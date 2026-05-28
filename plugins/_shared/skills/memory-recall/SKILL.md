---
name: memory-recall
description: "Search and recall relevant memories from past sessions via memsearch. Use when the user's question could benefit from historical context, past decisions, debugging notes, previous conversations, or project knowledge -- especially questions like 'what did I decide about X', 'why did we do Y', or 'have I seen this before'. Also use when you see `[memsearch] Memory available` hints injected via SessionStart or UserPromptSubmit. Typical flow: search for 3-5 chunks, expand the most relevant, optionally deep-drill into the source transcript. Skip when the question is purely about current code state (use Read/Grep), ephemeral (today's task only), or the user has explicitly asked to ignore memory."
---

You are a memory retrieval agent for memsearch. Search past memories and return the most relevant context to the main conversation.

## Pick a method

Look at your available tools. Choose the first matching path:

- **If you have `memsearch_search` registered as a tool** → use **Method 1 (native)**. The plugin handles collection + path resolution; do not shell out.
- **Otherwise** → use **Method 2 (CLI shell-out)**. You will need a shell (Bash on POSIX/git-bash, or PowerShell on Windows with `bash` available).

Both methods return the same shape of result; pick the one your runtime supports.

## Method 1 — native tools (OpenCode)

| Tool | Purpose |
|------|---------|
| `memsearch_search` | Hybrid (BM25 + dense + RRF) search across past memories. Returns ranked chunks. |
| `memsearch_get` | Expand a `chunk_hash` from a search result to its full markdown section. |
| `memsearch_transcript` | Pull the original conversation for a session/turn anchor. |

Steps:

1. **Search**: `memsearch_search(query, top_k: 5)` — query captures the core intent of the user's question.
2. **Evaluate**: drop chunks that are clearly irrelevant or too generic.
3. **Expand**: `memsearch_get(chunk_hash)` for each promising result.
4. **Deep drill (optional)**: if an expanded chunk has a session anchor (`session:`/`turn:`), call `memsearch_transcript(session_id, turn_id, context: 3)`. If only `session_id` is present, use `limit: 10`.

Do **not** fall back to shelling out to the `memsearch` CLI from native mode — that bypasses the plugin's collection resolution.

## Method 2 — CLI shell-out (Codex, generic shell agents)

All commands invoke `memsearch` directly — shell-agnostic, works the same in PowerShell, bash, zsh, fish. **Do not call `bash`** for collection derivation; on Windows `bash` may dispatch to WSL (no Windows path access) instead of git-bash.

Resolve the collection once at the start, then reuse:

```
# PowerShell
$env:MEMSEARCH_DIR  # confirm it's set; if not, use the repo root
$COLL = memsearch collection-name "$env:MEMSEARCH_DIR"

# bash / zsh / fish
COLL=$(memsearch collection-name "${MEMSEARCH_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}")
```

If `MEMSEARCH_DIR` is unset and you're not in a git repo, pass the project root explicitly. `memsearch collection-name` is pure: same input → same output, no side effects.

Steps:

1. **Search**: `memsearch search "<query>" --top-k 5 --json-output --collection "$COLL"`.
   - If `memsearch` is not on PATH, fall back to `uvx --from memsearch[onnx] memsearch ...`.
2. **Evaluate**: skip chunks that are clearly irrelevant or too generic.
3. **Expand**: `memsearch expand <chunk_hash> --collection "$COLL"` for each relevant result.
   - If `expand` fails with a Milvus lock/permission error (sandboxed environments), fall back to reading the source file directly. Every result includes `source` and `start_line`/`end_line`.
4. **Deep drill (optional)**: if an expanded chunk has a transcript anchor (HTML comment with `transcript:` / `rollout:` / `db:` + `session:` / `turn:`), read the referenced file directly — `cat` (POSIX) or `Get-Content` (PowerShell) for `.jsonl`/`.md`, or for Codex rollouts open the file at the path given in the anchor and locate the matching `session_id`/`turn_id` by string match. Anchor formats vary by source agent; the file path in the anchor is the source of truth.

### Fallback if `memsearch collection-name` is unavailable

If `memsearch collection-name` fails with `Error: No such command 'collection-name'`, the installed `memsearch` predates this subcommand. Omit `--collection` entirely:

```
memsearch search "<query>" --top-k 5 --json-output
memsearch expand <chunk_hash>
```

This relies on `~/.memsearch/config.toml` having `[milvus] collection = ...` pinned to the active collection. If it's not pinned and search returns `[]`, ask the user to either upgrade `memsearch` or set the collection in their config.

## When unsure what to search

If the user's question is vague, explore raw markdown first — it's the source of truth:

```bash
MDIR="${MEMSEARCH_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.memsearch}"
ls -t "$MDIR/memory/" | head -10                            # recent daily logs
grep -h "^## " "$MDIR/memory/"*.md | sort -u | tail -40     # session headings
cat "$MDIR/memory/<YYYY-MM-DD>.md"                          # read a specific day
```

Once a concrete topic surfaces, rerun `memsearch_search` (Method 1) or `memsearch search` (Method 2).

## Output format

Organize by relevance. For each memory include:
- The key information (decisions, patterns, solutions, context)
- Source reference (file name, date) — the `source` field from the search result

If nothing relevant is found, say `No relevant memories found.` and stop.
