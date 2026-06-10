# CLAUDE.md

<!-- This file is for AI agents (Claude Code, Cursor, Copilot, etc.) working in this repository.
     It also serves as a shared project memory — recording conventions, architecture decisions,
     and common patterns that all contributors (human or AI) should follow.
     Symlinked as AGENT.md and MEMORY.md for compatibility with other tools. -->

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install in development mode
uv sync --all-extras

# Run all tests (use python -m pytest to avoid system pytest conflicts)
uv run python -m pytest

# Run a single test file
uv run python -m pytest tests/test_chunker.py

# Run a specific test
uv run python -m pytest tests/test_store.py::test_upsert_and_search -v

# Serve docs locally
uv run mkdocs serve

# Run the CLI
uv run memsearch --help
```

## Architecture

**memsearch** is a semantic memory search engine for markdown knowledge bases, built on Milvus.

### Data Flow

```
Markdown files → Scanner → Chunker → Embedder → MilvusStore
                                                      ↓
                               User query → Embedder → Hybrid Search (dense + BM25 + RRF) → Results
```

### Core Library (`src/memsearch/`)

- **`core.py`** — `MemSearch` class: the public Python API that orchestrates everything. Entry point for `index()`, `search()`, `compact()`, `watch()`.
- **`store.py`** — `MilvusStore`: Milvus wrapper handling collection creation, upsert, hybrid search (dense cosine + BM25 sparse + RRF reranking), and cleanup. The `chunk_hash` (composite ID of source+lines+content+model) is the VARCHAR primary key.
- **`chunker.py`** — Splits markdown by headings into `Chunk` dataclasses. SHA-256 content hash enables dedup. `compute_chunk_id()` generates composite IDs matching OpenClaw's format.
- **`embeddings/__init__.py`** — `EmbeddingProvider` protocol + lazy-loading factory (`get_provider()`). Providers: openai (default), google, voyage, jina, mistral, ollama, local, onnx.
- **`scanner.py`** — Walks directories to find `.md`/`.markdown` files, returns `ScannedFile` list.
- **`config.py`** — Layered TOML config: dataclass defaults → `~/.memsearch/config.toml` → `.memsearch.toml` → CLI flags.
- **`cli.py`** — Click CLI wrapping the Python API. All commands resolve config via `resolve_config()` then instantiate `MemSearch`.
- **`watcher.py`** — `watchdog`-based file watcher with debounce, used by `memsearch watch` and the Claude Code plugin.
- **`compact.py`** — LLM-powered chunk summarization (OpenAI/Anthropic/Gemini).
- **`reranker.py`** — Optional cross-encoder reranking (ONNX or PyTorch backend). Disabled by default; enable via `reranker.model` config.

### Claude Code Plugin (`plugins/claude-code/`)

The plugin is a first-class component of memsearch — it's the primary real-world application that demonstrates the library in action. It gives Claude Code automatic persistent memory across sessions with zero user intervention.

**Architecture: 4 shell hooks + 1 skill + 1 background watcher**

```
plugins/claude-code/
├── hooks/
│   ├── common.sh                # Shared setup: PATH, memsearch detection, collection name, watch PID
│   ├── session-start.sh         # SessionStart: start watch, write session heading, inject recent memories
│   ├── user-prompt-submit.sh    # UserPromptSubmit: lightweight hint reminding Claude about memory skill
│   ├── stop.sh                  # Stop: extract last turn → haiku summarize (third-person) → append to daily .md (async)
│   ├── session-end.sh           # SessionEnd: stop watch process
│   └── parse-transcript.sh      # Last-turn extractor: finds last user question → EOF, formats with role labels for LLM (Python 3, no jq)
├── scripts/
│   └── derive-collection.sh     # Derive per-project collection name from project path
├── transcript.py                # JSONL transcript parser for Claude Code conversation files (L3 deep drill)
└── skills/
    └── memory-recall/
        └── SKILL.md             # Skill (context: fork): search → expand → transcript in subagent
```

**Key design: skill-based memory recall.** Memory retrieval is handled by a `memory-recall` skill that runs in a forked subagent context (`context: fork`). Claude automatically invokes the skill when it judges the user's question could benefit from historical context. The subagent autonomously performs search, evaluates relevance, expands promising results, and returns a curated summary — all without polluting the main conversation context.

**Three-layer progressive disclosure (all in subagent):**
1. **L1 (search):** Subagent runs `memsearch search` to find relevant chunks
2. **L2 (expand):** Subagent runs `memsearch expand <chunk_hash>` to get full markdown sections
3. **L3 (transcript):** Subagent runs `python3 ${CLAUDE_PLUGIN_ROOT}/transcript.py <jsonl>` to drill into original conversations

**Supporting hooks:**
- `SessionStart` injects cold-start context (recent daily logs) so Claude knows history exists
- `UserPromptSubmit` returns a lightweight `systemMessage` hint ("[memsearch] Memory available") to increase skill trigger awareness
- `Stop` hook is async and non-blocking — extracts last turn only, calls `claude -p --model haiku` (with `CLAUDECODE=` to bypass nested session detection) to summarize as third-person notes, appends to daily `.md`

When modifying hooks/skills, keep in mind:
- All hooks output JSON to stdout (`additionalContext` for context injection, `systemMessage` for visible hints, or empty `{}`)
- `common.sh` is sourced by every hook — changes there affect all hooks. It derives a per-project `COLLECTION_NAME` via `derive-collection.sh` and passes `--collection` automatically through `run_memsearch()` and `start_watch()`
- The watch process uses a PID file (`.memsearch/.watch.pid`) for singleton behavior. Milvus Lite falls back to one-time `index()` at session start
- `stop.sh` has a recursion guard (`stop_hook_active`) since it calls `claude -p` internally, and sets `MEMSEARCH_NO_WATCH=1` to prevent the child process from interfering with the main session's watch
- The `memory-recall` skill uses `context: fork` — the subagent has its own context window and does not see main conversation history
- `transcript.py` lives in the plugin directory (not in core library) since it is entirely Claude Code JSONL-specific

## Key Design Decisions

- **Markdown is the source of truth.** Milvus is a derived index, rebuildable anytime from `.md` files.
- **Composite chunk ID as PK.** `hash(source:startLine:endLine:contentHash:model)` — enables natural dedup without a separate cache.
- **ONNX bge-m3 as plugin default.** The Claude Code plugin hooks default to `onnx` provider (bge-m3, CPU, no API key). The Python API still defaults to `openai`.
- **Hybrid search by default.** Every collection has both dense vector and BM25 sparse fields. Search uses RRF to combine them. RRF scores are normalized to `[0, 1]` (theoretical max = `num_retrievers / (k + 1)`).
- **`[llm]` + `[prompts]` config.** New config sections for LLM provider selection and custom prompt templates. `[compact]` is deprecated but still works (fallback: `[llm]` > `[compact]` > defaults). Plugins read `prompts.summarize` for custom session summarization prompts. **Migration plan:** `[compact]` will be removed in the next major version (1.0). During the transition, `resolve_config()` emits a visible `UserWarning` (formerly `DeprecationWarning`, which Python hides by default) when user config files contain `[compact]`. The compact CLI command resolves LLM settings as `cfg.llm.* or cfg.compact.*`.
- **Shared prompt template.** All four plugins share a single `summarize.txt` template (maintained in `plugins/_shared/prompts/`, synced via `scripts/sync-prompts.sh`). Template uses `{{AGENT_NAME}}` placeholder.
- **Remote Milvus `query()` requires a filter.** Use `chunk_hash != ""` as a "match all" filter when no filter is provided (Milvus Lite doesn't enforce this, but Milvus Server does).

## Versioning & Release

**Five independent version numbers** — bump only the ones that changed:

| Component | Version file | Publish channel |
|-----------|-------------|-----------------|
| **memsearch** (PyPI) | `pyproject.toml` | PyPI (automated via GitHub Actions on tag push) |
| **Claude Code plugin** | `plugins/claude-code/.claude-plugin/plugin.json` | Marketplace (`.claude-plugin/marketplace.json`) |
| **OpenClaw plugin** | `plugins/openclaw/package.json` | ClawHub (`clawhub package publish`) |
| **OpenCode plugin** | `plugins/opencode/package.json` | npm (`@zilliz/memsearch-opencode`) |
| **Codex CLI plugin** | *(none)* | `install.sh` (no version management) |

See `CLAUDE.local.md` for detailed release procedures, current versions, and operational details.

**Version numbers track upstream *release lines* via cherry-pick; the version line is fork-owned, not merge-owned.** This fork follows `upstream/main` by **cherry-picking the compatible subset** of each upstream release — it has diverged, so not every upstream change is wanted or even applies. The version set is a single coordinated marker recording **which upstream release line the fork has been brought level with**; on an upstream sync all version files move *together* to one upstream release's values (this overrides the "bump only what changed" rule above, which governs purely-local changes — a per-component version gap would create unpredictable merge behavior).

- **A bump is the trailing half of a deliberate port pass — never a standalone act.** Assess upstream release `X`, cherry-pick the appropriate changes (verbatim where they apply, adapted where the fork diverged), skip the rest — *then, in the same commit*, set **all** version files to exactly `X`'s upstream values (`pyproject.toml`, the `memsearch` entry in `uv.lock`, every `plugins/*/…/{plugin.json,package.json}`, `marketplace.json`). Taking *some* of `X` is sufficient to move the number to `X`; **full-file parity is NOT required**. The version means "level with upstream `X`, having taken what's compatible," not "our tree equals upstream's."
- **Until that port pass happens, hold the fork BEHIND upstream.** `fork version < latest upstream version` is the deliberate signal that an un-ported upstream release exists and there is work to bring back. Never close the gap by bumping the number alone; never bump to a version upstream doesn't have (arbitrary local bumps cause resync conflicts).

**⚠️ The gap does not defend itself — a merge silently erases it.** `gsync` is merge-based. Once the fork sits at `X` (which becomes the merge-base) and upstream ships `X+1`, the version line three-way-merges as `base=X, ours=X, theirs=X+1` → **no conflict, theirs wins**, and the fork auto-advances to `X+1` with zero content ported — the precise failure this policy prevents. Therefore:
- **Detection is an explicit step, not a merge artifact.** As part of every sync, run `git show upstream/main:pyproject.toml | grep -m1 '^version'` (and the plugin manifests) and compare to the working tree. The version delta is a checklist item you read — the toolchain will not raise it for you.
- **The version line is fork-owned: re-assert it after any wholesale merge.** If a `gsync` merge auto-advanced the version but the port pass isn't actually done, revert those version lines back down until you've cherry-picked the new release's content.

**Self-documenting history.** The port+sync commit must name the upstream release and exactly what was taken, e.g. `chore(sync): integrate upstream 0.4.6 (#556) — cherry-pick compact/maintenance temperature removals; align all version files to 0.4.6`. Do the port and the version bump in **one** commit (or tightly cross-reference them) so a later reader sees which upstream release each version corresponds to and what was cherry-picked.

**Worked example — the 0.4.6 line.** Upstream 0.4.6 is one feature commit, #556 (`d24ebb1`, 41 files). The fork cherry-picked #556's `temperature` removals into `src/memsearch/compact.py` and `src/memsearch/maintenance.py` **byte-for-byte** (matching blob hashes `27852ae..be21dd5`, `f928943..3f067fd`) and took nothing else from #556 — the rest conflicts with fork-local divergence. That partial take is sufficient: **`0.4.6` is the correct fork version.** Two caveats the history must not blur: (a) the `stop.sh` changes in `9479950` are *separate* local/#563 work, **not** a #556 cherry-pick — don't conflate them; (b) `openclaw`/`opencode` moved to their 0.4.6-line values (`0.3.6` / `0.3.3`) as part of the coordinated marker even though no openclaw/opencode content was ported — the version tracks the whole-repo release line, not per-component ports. (Side note: bumping `plugin.json` also forces a fresh plugin cache dir on the next `/plugin install` — per the stale-cache traps below.)

## Updating a deployed build (stale-cache traps)

There are **two independent deploy layers** — updating one does NOT update the other:
1. the `memsearch` **CLI** (installed as a `uv tool`, on `PATH` at `~/.local/bin/memsearch`), and
2. the **plugin hooks** (cached under `~/.claude/plugins/cache/<marketplace>/memsearch/<plugin-version>/hooks/`).

**CLI — local / fork build.** Deploy with `uv tool install --force ".[onnx]"` from the repo root.
- ⚠️ **uv keys its build cache by the `pyproject.toml` version.** If the version is unchanged — which it *always* is for an unpublished local/fork build — uv **reuses a cached wheel and silently installs stale code**. The install still reports `Installed …` and rewrites the shim, but the package content is old (new files like `watchlock.py` are simply absent). Cache hit = instant; a real rebuild prints `Built memsearch @ file://…`.
- **Fix:** bump `pyproject.toml` version, **or** add `--no-cache` to force a rebuild: `uv tool install --force --no-cache ".[onnx]"`.
- **Never trust the "Installed" message — verify by content:** e.g. `memsearch watch --help | grep -- --replace`, or inspect `~/AppData/Roaming/uv/tools/memsearch/Lib/site-packages/memsearch/`.
- **Fork ≠ PyPI.** `uv tool install -U "memsearch[onnx]"` pulls **upstream** from PyPI (the `memsearch` name belongs to zilliztech) — it does NOT deploy a fork. Deploy a fork from the local path (`".[onnx]"`) or `git+<fork-url>@<branch>`.

**Plugin hooks.** Refresh with `/plugin marketplace update <mp>` → `/plugin install memsearch@<mp>` → `/reload-plugins`.
- ⚠️ **Same version-keying trap on the plugin side.** If `plugins/claude-code/.claude-plugin/plugin.json` version is unchanged, `/plugin install` reports `already installed globally` and may not refresh the cache. **Bump the plugin.json version** to force a new cache dir, then verify by grepping the *cached* hook (not the repo copy) for your change.

**Editable venv vs deployed binary.** `uv sync` / `.venv` is an editable install (reflects edits instantly); the uv-tool `memsearch` on `PATH` is a *separate copy* that lags until reinstalled. Testing `.venv/Scripts/memsearch` is NOT testing the live binary. Several copies can coexist (`~/.local/bin` shim, `~/AppData/Roaming/uv/tools/memsearch`, `.venv`).

## Operational footguns (data integrity)

- **A scoped `index <path>` is scope-safe — its deleted-file GC only prunes within the scanned paths.** `MemSearch.index()` ends with a deleted-file GC that deletes a stored source only if it is both gone from disk **and** lexically under one of the scanned PATHS (`core.py`, `Path.is_relative_to`). So `memsearch index <single-file>` refreshes that file without touching any other file's chunks, and an empty-paths `MemSearch.index()` (Python API — the CLI requires a path) is a no-op GC rather than a whole-collection wipe. A whole-tree `index` still prunes files deleted anywhere under the tree root (no regression). Markdown is the source of truth, so a full-tree re-index restores anything pruned. (Historical: pre-fix, a scoped `index` pruned *everything* outside `<path>` — caused real loss on 2026-05-29.)
- **`memsearch stats` (Milvus `row_count`) misleads in both directions.** It counts uncompacted tombstones (inflates after write churn) and can also *mask* real loss (stays high on tombstones while live chunks are gone). For the true count, run a distinct-key Milvus query. Reclaim tombstones with Milvus `client.compact(collection)` — **NOT** `memsearch compact`, which is unrelated (LLM summarization, destructive to raw chunks).
- **One writer per collection.** Concurrent writers churn the index (and dup rows when embedding-model strings diverge, since `model` is in the PK). The single-writer guard is `watchlock.py` — an OS advisory lock (`fcntl.flock` POSIX / `msvcrt.locking` Windows) keyed by `(domain, collection)`; `watch` and `index` use separate domains and `--replace` for cross-platform takeover. This supersedes the old `.watch.pid` singleton and the retired `kill_orphaned_index` bash sweep.

## Windows specifics

- **Milvus Lite is unavailable on Windows** (`milvus-lite … ; sys_platform != 'win32'`). Windows runs server mode only; Lite-mode code paths and the Lite-dependent tests fail/skip on Windows — that is environmental, not a regression.
- **Bash process reaping silently no-ops on Windows.** `pgrep`, `kill -0`, `kill -- -pid`, and `$!` PID files (Git Bash) do not map to native `memsearch.exe` PIDs. Cross-platform process control must live in the Python CLI (the lock), not in the shell hooks.

## Project Conventions

- Uses `uv` + `pyproject.toml` for dependency management (not pip).
- Optional deps via extras: `[google]`, `[voyage]`, `[ollama]`, `[local]`, `[onnx]`, `[all]`. The Claude Code plugin uses `memsearch[onnx]` for zero-config ONNX embedding.
- Docs at `docs/` use mkdocs-material. The `site/` directory is build output — do not commit.
- Always use `uv run python -m pytest` instead of `uv run pytest` to avoid system Python pytest conflicts.
