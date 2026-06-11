# CLI Reference

memsearch provides a command-line interface for indexing, searching, and managing semantic memory over markdown knowledge bases.

```bash
$ memsearch --version
memsearch, version 0.4.6

$ memsearch --help
Usage: memsearch [OPTIONS] COMMAND [ARGS]...

  memsearch — semantic memory search for markdown knowledge bases.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  collection-name  Print the Milvus collection name derived from a project dir.
  compact          Compress stored memories into a summary.
  config           Manage memsearch configuration.
  expand           Expand a memory chunk to show full context.
  graph            Manage the chunk-relationship graph (edges sidecar).
  index            Index markdown files from PATHS.
  reset            Drop all indexed data.
  search           Search indexed memory for QUERY.
  stats            Show statistics about the index.
  summarize        Summarize stdin using a configured LLM provider.
  watch            Watch PATHS for markdown changes and auto-index.
```

## Command Summary

| Command | Description |
|---------|-------------|
| `memsearch config` | Initialize, view, and modify configuration |
| `memsearch index` | Scan directories and index markdown files into the vector store |
| `memsearch search` | Semantic search across indexed chunks using natural language |
| `memsearch watch` | Monitor directories and auto-index on file changes |
| `memsearch compact` | Compress indexed chunks into an LLM-generated summary |
| `memsearch expand` | Progressive disclosure L2: show full section around a chunk 🔌 |
| `memsearch graph` | Manage the chunk-relationship graph sidecar (`graph rebuild`) |
| `memsearch collection-name` | Print the per-project Milvus collection name derived from a path 🔌 |
| `memsearch summarize` | Summarize stdin via a configured LLM provider (plugin session notes) 🔌 |
| `memsearch stats` | Display index statistics (total chunk count) |
| `memsearch reset` | Drop all indexed data from the Milvus collection |

> 🔌 Commands marked with 🔌 are used by the [platform plugins](platforms/index.md). `expand` also works as a standalone CLI tool; `collection-name`/`summarize` are plugin helpers. Progressive-disclosure L3 (transcript drill-down) is handled inside the plugins by `transcript.py`, **not** a `memsearch` subcommand.

---

## `memsearch config`

Manage memsearch configuration. Configuration is stored in TOML files and follows a layered priority chain:

```
dataclass defaults -> ~/.memsearch/config.toml -> .memsearch.toml -> CLI flags
```

Higher-priority sources override lower-priority ones.

### Subcommands

#### `memsearch config init`

Launch an interactive wizard that walks through all configuration sections and writes a TOML config file.

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | `false` | Write to `.memsearch.toml` (project-level) instead of the global config |

```bash
$ memsearch config init
memsearch configuration wizard
Writing to: /home/user/.memsearch/config.toml

-- Milvus --
  Milvus URI [~/.memsearch/milvus.db]:
  Milvus token (empty for none) []:
  Collection name [memsearch_chunks]:

-- Embedding --
  Provider (openai/google/voyage/jina/mistral/ollama/local/onnx) [openai]:
  Model (empty for provider default) []:

-- Chunking --
  Max chunk size (chars) [1500]:
  Overlap lines [2]:

-- Watch --
  Debounce (ms) [1500]:

-- LLM (for memsearch compact) --
  Provider (empty/openai/anthropic/gemini) []:
  Model []:

-- Plugin summarize routing --
  Leave provider empty/native to keep each plugin's current native summarizer.
  Codex automatic summaries enabled [Y/n]:
  Codex summarize provider []:
  Codex summarize model []:

-- Advanced maintenance --
  Disabled by default. Configure provider/model if you enable these tasks.
  Codex project review enabled [y/N]:
  Codex user profile enabled [y/N]:
  Codex corrections enabled [y/N]:

-- Prompts --
  Leave empty to use built-in defaults.
  Summarize prompt file (for plugin session notes) []:
  Project review prompt file []:
  User profile prompt file []:
  Corrections prompt file []:

Config saved to /home/user/.memsearch/config.toml
```

Create a project-level config:

```bash
$ memsearch config init --project
memsearch configuration wizard
Writing to: .memsearch.toml
...
```

#### `memsearch config set`

Set a single configuration value by dotted key. Keys follow the `section.field` format.

| Flag | Default | Description |
|------|---------|-------------|
| `KEY` | *(required)* | Dotted config key (e.g., `milvus.uri`) |
| `VALUE` | *(required)* | Value to set |
| `--project` | `false` | Write to `.memsearch.toml` instead of global config |

```bash
$ memsearch config set milvus.uri http://localhost:19530
Set milvus.uri = http://localhost:19530 in /home/user/.memsearch/config.toml

$ memsearch config set embedding.provider google --project
Set embedding.provider = google in .memsearch.toml

$ memsearch config set chunking.max_chunk_size 2000
Set chunking.max_chunk_size = 2000 in /home/user/.memsearch/config.toml
```

Plugin config keys use `plugins.<platform>.<task>.<field>`:

```bash
$ memsearch config set plugins.codex.summarize.enabled false --project
Set plugins.codex.summarize.enabled = false in .memsearch.toml

$ memsearch config set plugins.codex.project_review.enabled true --project
Set plugins.codex.project_review.enabled = true in .memsearch.toml

$ memsearch config set plugins.codex.project_review.output_file .memsearch/PROJECT.md --project
Set plugins.codex.project_review.output_file = .memsearch/PROJECT.md in .memsearch.toml
```

Supported plugin platforms are `claude-code`, `codex`, `opencode`, and
`openclaw`. Supported plugin tasks are `summarize`, `project_review`,
`user_profile`, and `corrections`.

#### `memsearch config get`

Read a single resolved configuration value (merged from all sources).

```bash
$ memsearch config get milvus.uri
http://localhost:19530

$ memsearch config get embedding.provider
openai

$ memsearch config get chunking.max_chunk_size
1500
```

#### `memsearch config list`

Display configuration in TOML format.

| Flag | Default | Description |
|------|---------|-------------|
| `--resolved` | *(default)* | Show the fully merged configuration from all sources |
| `--global` | | Show only the global config file (`~/.memsearch/config.toml`) |
| `--project` | | Show only the project config file (`.memsearch.toml`) |

```bash
$ memsearch config list --resolved
# Resolved (all sources merged)

[milvus]
uri = "~/.memsearch/milvus.db"
token = ""
collection = "memsearch_chunks"

[embedding]
provider = "openai"
model = ""

[chunking]
max_chunk_size = 1500
overlap_lines = 2

[watch]
debounce_ms = 1500

[compact]
llm_provider = "openai"
llm_model = ""
prompt_file = ""

[llm]
provider = ""
model = ""

[llm.providers.openai]
type = "openai"
model = "gpt-5-mini"
base_url = ""
api_key = "env:OPENAI_API_KEY"

[plugins.claude-code.summarize]
provider = ""
model = ""

[plugins.codex.summarize]
provider = ""
model = ""

[plugins.opencode.summarize]
provider = ""
model = ""

[plugins.openclaw.summarize]
provider = ""
model = ""

[prompts]
compact = ""
summarize = ""
```

```bash
$ memsearch config list --global
# Global (/home/user/.memsearch/config.toml)

[milvus]
uri = "http://localhost:19530"

[embedding]
provider = "openai"
```

### Available Config Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `milvus.uri` | string | `~/.memsearch/milvus.db` | Milvus connection URI |
| `milvus.token` | string | `""` | Auth token for Milvus Server / Zilliz Cloud |
| `milvus.collection` | string | `memsearch_chunks` | Collection name |
| `embedding.provider` | string | `openai` | Embedding provider name |
| `embedding.model` | string | `""` | Override embedding model (empty = provider default) |
| `embedding.batch_size` | int | `0` | Embedding batch size (0 = provider default) |
| `embedding.base_url` | string | `""` | OpenAI-compatible API base URL (empty = SDK default) |
| `embedding.api_key` | string | `""` | API key for embedding provider (supports `env:VAR_NAME` syntax) |
| `chunking.max_chunk_size` | int | `1500` | Maximum chunk size in characters |
| `chunking.overlap_lines` | int | `2` | Number of overlapping lines between adjacent chunks |
| `watch.debounce_ms` | int | `1500` | File watcher debounce delay in milliseconds |
| `compact.llm_provider` | string | `openai` | *(deprecated)* LLM provider for compact — use `llm.provider` instead |
| `compact.llm_model` | string | `""` | *(deprecated)* LLM model — use `llm.model` instead |
| `compact.prompt_file` | string | `""` | *(deprecated)* Prompt file — use `prompts.compact` instead |
| `llm.provider` | string | `""` | LLM provider for `memsearch compact` (empty = compact defaults to openai) |
| `llm.model` | string | `""` | LLM model override for `memsearch compact` |
| `llm.base_url` | string | `""` | OpenAI-compatible API base URL |
| `llm.api_key` | string | `""` | API key (supports `env:VAR_NAME` syntax) |
| `llm.providers.<name>.type` | string | `""` | Named provider type for plugin summarization (`openai`, `openai-compatible`, `anthropic`, `gemini`) |
| `llm.providers.<name>.model` | string | `""` | Default model for a named plugin summarization provider |
| `llm.providers.<name>.base_url` | string | `""` | OpenAI-compatible API base URL for a named provider |
| `llm.providers.<name>.api_key` | string | `""` | API key for a named provider (supports `env:VAR_NAME` syntax) |
| `plugins.claude-code.summarize.provider` | string | `""` | Claude Code summarize provider route (empty/`native` = native summarizer) |
| `plugins.claude-code.summarize.model` | string | `""` | Claude Code native model override, or named provider model override |
| `plugins.codex.summarize.provider` | string | `""` | Codex summarize provider route (empty/`native` = native summarizer) |
| `plugins.codex.summarize.model` | string | `""` | Codex native model override, or named provider model override |
| `plugins.opencode.summarize.provider` | string | `""` | OpenCode summarize provider route (empty/`native` = native summarizer) |
| `plugins.opencode.summarize.model` | string | `""` | OpenCode native model override, or named provider model override |
| `plugins.openclaw.summarize.provider` | string | `""` | OpenClaw summarize provider route (empty/`native` = native summarizer) |
| `plugins.openclaw.summarize.model` | string | `""` | OpenClaw native model override, or named provider model override |
| `prompts.compact` | string | `""` | Custom prompt file for `memsearch compact` |
| `prompts.summarize` | string | `""` | Custom prompt file for plugin session summarization |
| `prompts.project_review` | string | `""` | Custom prompt file for plugin project maintenance |
| `prompts.user_profile` | string | `""` | Custom prompt file for plugin user-profile maintenance |
| `prompts.corrections` | string | `""` | Custom prompt file for plugin corrections maintenance |
| `search.recency_weight` | float | `0.3` | Time-aware re-scoring weight in `[0, 1]`; `0` disables (exact pre-change ranking) |
| `search.recency_half_life_days` | float | `30.0` | Days for a dated chunk's recency multiplier to halve |
| `search.max_per_source` | int | `2` | Max results kept per source file (diversity); `0` disables |
| `search.fetch_multiplier` | int | `3` | Over-fetch factor feeding the rerank/recency/cap stages |
| `search.log_recalls` | bool | `true` | Log every `expand` as implicit-feedback into `recall_log` (in `edges.db`) |

The `[search]` knobs are **on by default** and intentionally change default ranking:
recent daily logs outrank equally-similar older ones, and at most `max_per_source`
results come from any one file. Undated files (`PROJECT.md`, `USER.md`) are never
penalized. Restore exact pre-change output with `--recency-weight 0 --max-per-source 0`.

---

## `memsearch index`

Scan one or more directories (or files) and index all markdown files (`.md`, `.markdown`) into the Milvus vector store. Only new or changed chunks are embedded by default -- unchanged chunks are skipped. Chunks belonging to deleted files are automatically removed from the index.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `PATHS` | | *(required)* | One or more directories or files to index |
| `--provider` | `-p` | `openai` | Embedding provider (`openai`, `google`, `voyage`, `jina`, `mistral`, `ollama`, `local`, `onnx`) |
| `--model` | `-m` | provider default | Override the embedding model name |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token (for server or Zilliz Cloud) |
| `--max-chunk-size` | | config value | Override `chunking.max_chunk_size` for this run |
| `--force` | | `false` | Re-embed and re-index all chunks, even if unchanged |

### Examples

Index a single directory:

```bash
$ memsearch index ./memory/
Indexed 42 chunks.
```

Index multiple directories with a specific embedding provider:

```bash
$ memsearch index ./memory/ ./notes/ --provider google
Indexed 87 chunks.
```

Force re-index everything (ignores the content-hash dedup check):

```bash
$ memsearch index ./memory/ --force
Indexed 42 chunks.
```

Connect to a remote Milvus server instead of the default local file:

```bash
$ memsearch index ./memory/ --milvus-uri http://localhost:19530
Indexed 42 chunks.
```

Use a custom embedding model:

```bash
$ memsearch index ./memory/ --provider openai --model text-embedding-3-large
Indexed 42 chunks.
```

### Notes

- **Incremental by default.** Each chunk is identified by a composite hash of its source file, line range, content hash, and embedding model. Only chunks with new IDs are embedded and stored.
- **Stale cleanup.** If a file that was previously indexed no longer exists on disk, its chunks are automatically deleted from the index during the next `index` run.
- **`--force` re-embeds everything.** Use this when you switch embedding providers or models, since the same content will produce different vectors with a different model.

---

## `memsearch search`

Run a semantic search query against indexed chunks. Uses [hybrid search](https://milvus.io/docs/multi-vector-search.md) (dense vector cosine similarity + [BM25](https://en.wikipedia.org/wiki/Okapi_BM25) full-text) with [RRF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) reranking for best results.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `QUERY` | | *(required)* | Natural-language search query |
| `--top-k` | `-k` | `5` | Maximum number of results to return |
| `--source-prefix` | | *(none)* | Only return chunks whose source path starts with this prefix (directory-scoped search) |
| `--provider` | `-p` | `openai` | Embedding provider (must match the provider used at index time) |
| `--model` | `-m` | provider default | Override the embedding model |
| `--batch-size` | | `0` | Embedding batch size (`0` = provider default) |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |
| `--reranker-model` | | config (`""` = off) | Cross-encoder model for a second-stage rerank; empty string disables |
| `--graph` / `--no-graph` | | config (on) | Enable/disable graph-aware retrieval expansion |
| `--consistency` | | collection default (Bounded) | Remote Milvus read consistency; `Strong` avoids staleness (ignored on Milvus Lite) |
| `--recency-weight` | | config (`0.3`) | Time-aware re-scoring weight in `[0, 1]`; `0` disables |
| `--max-per-source` | | config (`2`) | Max results per source file (diversity); `0` disables |
| `--compact-output` | | `false` | Slim index-first view for filter-before-expand (see below) |
| `--json-output` | `-j` | `false` | Output results as JSON |

### Examples

Basic search:

```bash
$ memsearch search "how to configure Redis caching"

--- Result 1 (score: 0.9919) ---
Source: /home/user/docs/2026-01-15.md
Heading: Redis Configuration
Set REDIS_URL in .env to point to your Redis instance.
Use `cache.set(key, value, ttl=300)` for 5-minute expiry...

--- Result 2 (score: 0.4919) ---
Source: /home/user/docs/architecture.md
Heading: Caching Layer
We use Redis as the primary caching backend...
```

Return more results:

```bash
$ memsearch search "authentication flow" --top-k 10
```

Output as JSON (useful for piping to `jq` or other tools):

```bash
$ memsearch search "error handling" --json-output
[
  {
    "content": "All API endpoints should return structured error...",
    "source": "/home/user/docs/api-design.md",
    "heading": "Error Handling",
    "chunk_hash": "a1b2c3d4e5f6...",
    "heading_level": 2,
    "start_line": 45,
    "end_line": 62,
    "score": 0.9919
  }
]
```

Use with a different provider (must match the one used for indexing):

```bash
$ memsearch search "database migrations" --provider google
```

Compact index-first view — scan many candidates cheaply, then `expand` only the
chosen few (the *filter-before-fetch* pattern, ~10x fewer tokens than full-content
search):

```bash
$ memsearch search "RRF fusion" --top-k 15 --compact-output --json-output
[
  {
    "chunk_hash": "ab12cd34ef567890",
    "score": 0.8123,
    "date": "2026-06-05",
    "source": "/home/user/.memsearch/memory/2026-06-05.md",
    "heading": "Session 14:30",
    "preview": "Decided to use RRF k=60 for fusion because..."
  }
]
```

In `--compact-output` text mode each result is one line
(`#  chunk_hash  score  date  heading — preview`); `source` is the basename in text
mode and the full path in `--json-output`.

### Notes

- **Provider must match.** The search embedding provider and model must match whatever was used during indexing. Mixing providers will return poor results because the vector spaces are incompatible.
- **Hybrid search.** Results are ranked using Reciprocal Rank Fusion (RRF) across both dense (cosine) and sparse (BM25) retrieval, giving you the best of semantic and keyword matching. Scores are normalized to `[0, 1]` where 1.0 means ranked #1 in all retrievers.
- **Retrieval pipeline.** Hybrid candidates are optionally expanded via the chunk-relationship graph (`--graph`/`--no-graph`, on by default — run `memsearch graph rebuild` once to populate the edges sidecar) and optionally re-scored by a cross-encoder (`--reranker-model`, off by default; requires `memsearch[onnx]` or `memsearch[local]`). Time-aware re-scoring and the per-source cap run last.
- **Directory-scoped search.** `--source-prefix /path` restricts results to chunks whose `source` is under that prefix — useful for searching one project or subtree of a shared collection.
- **Time-aware ranking (on by default).** After hybrid search (and reranking, if enabled), scores are multiplicatively damped by source recency (`search.recency_weight`) and capped per source file (`search.max_per_source`). Relevance still dominates — recency only breaks ties / applies a bounded staleness penalty. Disable with `--recency-weight 0 --max-per-source 0`. See the `[search]` config keys above.
- **Content is truncated.** In the default text output, each result's content is truncated to 500 characters. Use `--json-output` to get the full content.

---

## `memsearch watch`

Start a long-running file watcher that monitors directories for markdown file changes. On startup, all existing markdown files are indexed first (dedup ensures no wasted API calls for unchanged content). Then the watcher monitors for changes: when a `.md` or `.markdown` file is created or modified, it is automatically re-indexed. When a file is deleted, its chunks are removed from the store.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `PATHS` | | *(required)* | One or more directories to watch |
| `--debounce-ms` | | `1500` | Debounce delay in milliseconds; multiple rapid changes to the same file within this window are collapsed into a single re-index |
| `--provider` | `-p` | `openai` | Embedding provider |
| `--model` | `-m` | provider default | Override the embedding model |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |
| `--max-chunk-size` | | config value | Override `chunking.max_chunk_size` for this run |

### Examples

Watch a single directory:

```bash
$ memsearch watch ./memory/
Indexed 8 chunks.
Watching 1 path(s) for changes... (Ctrl+C to stop)
Indexed 3 chunks from /home/user/docs/2026-02-11.md
Removed chunks for /home/user/docs/old-draft.md
^C
Stopping watcher.
```

Watch multiple directories with a longer debounce:

```bash
$ memsearch watch ./memory/ ./notes/ --debounce-ms 3000
Watching 2 path(s) for changes... (Ctrl+C to stop)
```

### Notes

- **Initial index on startup.** The watcher indexes all existing files before it starts monitoring. Content-hash dedup means unchanged files are skipped with zero API calls — only genuinely new or modified content is embedded.
- **Debounce.** Editors that write files in multiple steps (e.g., write temp file, then rename) can trigger several events in quick succession. The debounce window collapses these into one re-index operation.
- **Recursive.** The watcher monitors all subdirectories recursively.
- **Singleton behavior.** Only one watcher process should run per directory set. Running multiple watchers on the same paths will cause duplicate indexing work (though dedup by content hash means the index stays consistent).
- **Stop with Ctrl+C.** The watcher runs until you interrupt it.

---

## `memsearch compact`

Use an LLM to compress all indexed chunks (or a subset) into a condensed markdown summary. The summary is appended to a daily log file at `memory/YYYY-MM-DD.md` inside the first configured path, keeping markdown as the single source of truth.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--source` | `-s` | *(all chunks)* | Only compact chunks from this specific source file |
| `--output-dir` | `-o` | first configured path | Directory to write the compact summary into |
| `--llm-provider` | | `openai` | LLM backend for summarization (`openai`, `anthropic`, `gemini`) |
| `--llm-model` | | provider default | Override the LLM model |
| `--prompt` | | built-in template | Custom prompt template string (must contain `{chunks}` placeholder) |
| `--prompt-file` | | *(none)* | Read the prompt template from a file instead |
| `--provider` | `-p` | `openai` | Embedding provider (used to access the index) |
| `--model` | `-m` | provider default | Override the embedding model |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |

### Default LLM Models

| Provider | Default Model |
|----------|--------------|
| `openai` | `gpt-5-mini` |
| `anthropic` | `claude-sonnet-4-6` |
| `gemini` | `gemini-3-flash-preview` |

### Examples

Compact all chunks using the default LLM (OpenAI):

```bash
$ memsearch compact
Compact complete. Summary:

## Key Decisions
- Use Redis for session caching with 5-minute TTL
- All API errors return structured JSON responses
...
```

Compact only chunks from a specific source file:

```bash
$ memsearch compact --source ./memory/old-notes.md
Compact complete. Summary:

## Old Notes Summary
- Initial architecture decisions from January meeting...
```

Relative and `~` paths are automatically resolved to the absolute form used at index time. If no chunks match, memsearch prints the resolved source path to help debug the filter.

Use Anthropic Claude for summarization:

```bash
$ memsearch compact --llm-provider anthropic
```

Use a custom prompt template:

```bash
$ memsearch compact --prompt "Summarize these notes into action items:\n{chunks}"
```

Use a prompt file for complex templates:

```bash
$ memsearch compact --prompt-file ./prompts/compress.txt
```

### Notes

- **Output location.** The summary is appended to `<first-path>/memory/YYYY-MM-DD.md`. This file is then automatically eligible for future indexing.
- **The `{chunks}` placeholder is required.** Whether using `--prompt` or `--prompt-file`, the template must contain `{chunks}` which will be replaced with the concatenated chunk contents.
- **API key required.** The chosen LLM provider requires its corresponding API key in the environment (see [Environment Variables](#environment-variables)).

---

## `memsearch expand`

> 🔌 **Plugin command.** `expand` is the L2 step of the [platform plugins](platforms/index.md)' three-level progressive disclosure workflow (`search` → `expand` → plugin `transcript.py`), but works as a standalone CLI tool for any memsearch index.

Look up a chunk by its hash in the index and return the surrounding context from the original source markdown file. This is "progressive disclosure level 2" -- when a search result snippet is not enough, expand it to see the full heading section.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `CHUNK_HASH` | | *(required)* | The chunk hash (primary key) to look up |
| `--section/--no-section` | | `--section` | Show the full heading section (default behavior) |
| `--lines` | `-n` | *(full section)* | Instead of the full section, show N lines before and after the chunk |
| `--query` | | *(none)* | Original search query that surfaced this chunk; logged as implicit feedback (`recall_log`, controlled by `search.log_recalls`) |
| `--json-output` | `-j` | `false` | Output as JSON |
| `--provider` | `-p` | `openai` | Embedding provider |
| `--model` | `-m` | provider default | Override the embedding model |
| `--batch-size` | | `0` | Embedding batch size (`0` = provider default) |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |

### Examples

Expand a chunk to see its full heading section:

```bash
$ memsearch expand a1b2c3d4e5f6
Source: /home/user/docs/architecture.md (lines 10-35)
Heading: Caching Layer

## Caching Layer

We use Redis as the primary caching backend. All cache keys
follow the pattern `service:entity:id`.

### Configuration
Set REDIS_URL in .env to point to your Redis instance.
Use `cache.set(key, value, ttl=300)` for 5-minute expiry.
...
```

Show only 5 lines of context around the chunk:

```bash
$ memsearch expand a1b2c3d4e5f6 --lines 5
Source: /home/user/docs/architecture.md (lines 18-28)
Heading: Caching Layer

Set REDIS_URL in .env to point to your Redis instance.
Use `cache.set(key, value, ttl=300)` for 5-minute expiry.
...
```

Get JSON output (includes anchor metadata if present):

```bash
$ memsearch expand a1b2c3d4e5f6 --json-output
{
  "chunk_hash": "a1b2c3d4e5f6",
  "source": "/home/user/docs/architecture.md",
  "heading": "Caching Layer",
  "start_line": 10,
  "end_line": 35,
  "content": "## Caching Layer\n\nWe use Redis as the primary..."
}
```

### Notes

- **Source file must exist.** The `expand` command reads the original markdown file from disk. If the source file has been moved or deleted, the command will fail with an error.
- **Anchor parsing.** If the expanded text contains an HTML anchor comment in the format `<!-- session:ID turn:ID transcript:PATH -->`, the command parses it and displays the session, turn, and transcript file information. This connects memory chunks to their original conversation transcripts.
- **Progressive disclosure L3.** There is no `memsearch transcript` subcommand. To drill from the anchor into the original conversation, the platform plugins run their bundled `transcript.py` (Claude Code: `python3 "${CLAUDE_PLUGIN_ROOT}/transcript.py" <jsonl> --turn <uuid> --context 3`).
- **Workflow: search then expand.** A typical workflow is to `search` first, note the `chunk_hash` from a result, then `expand` it to see more context.

---

## `memsearch graph`

Manage the chunk-relationship graph — an undirected edge sidecar (`edges.db`, SQLite) that links related chunks so search can expand beyond the literal hybrid-search hits. Graph-aware retrieval is **on by default** (`graph.enabled`); see the [`[graph]` config keys](#available-config-keys).

Edges are built automatically during `index` in three relations:

- **`sibling`** — consecutive chunks in the same file.
- **`same_section`** — chunks under the same heading (windowed).
- **`similar`** — cross-file nearest neighbours by embedding cosine similarity (≥ `graph.similar_threshold`).

At search time the top seeds pull in their 1-hop neighbours, which are re-fused into the ranking via RRF (weighted by `graph.weight`). Disable per query with `search --no-graph`, or globally with `graph.enabled = false`.

### Subcommands

#### `memsearch graph rebuild`

Rebuild **all** edges for a collection from already-stored chunks and embeddings — **no re-embedding**, so no embedding-provider API calls or cost. Use it to backfill edges for a collection indexed before graph mode was enabled, or after `search` prints the `graph mode on but no edges` warning.

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--provider` | `-p` | `openai` | Embedding provider (only to open the collection; no embeddings are computed) |
| `--model` | `-m` | provider default | Embedding model |
| `--batch-size` | | `0` | Embedding batch size (`0` = provider default) |
| `--base-url` | | *(none)* | OpenAI-compatible API base URL |
| `--api-key` | | *(none)* | API key for the embedding provider |
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |
| `--replace` / `--no-replace` | | `--no-replace` | Take over the index writer lock from another `index`/`rebuild` already holding it |

### Examples

Backfill edges for the default collection:

```bash
$ memsearch graph rebuild
Rebuilt 4821 edges.
```

Rebuild a specific collection, taking over a stuck writer lock:

```bash
$ memsearch graph rebuild --collection team_notes --replace
```

### Notes

- **No re-embedding.** `rebuild` reads stored vectors only; it never calls the embedding provider. `--provider`/`--model` exist solely to open the collection and should still match what was used at index time.
- **Single-writer lock.** `rebuild` is a full-table swap of the edges sidecar and shares the `index` writer lock, so it is serialised against `memsearch index` (but not against `watch`). It refuses to run if another index/rebuild holds the lock — use `--replace` to take over.
- **Atomic + collection-scoped.** The swap is one transaction scoped to the collection's own chunk hashes: concurrent readers never observe an empty graph, and other collections sharing the single `edges.db` are untouched.

---

## `memsearch collection-name`

> 🔌 **Plugin helper.** Used by the platform plugins' skills to resolve a project's collection name without shelling out to `bash` (which on Windows may dispatch to WSL and lose access to Windows paths/PATH).

Print the per-project Milvus collection name derived from a directory path (defaults to the current directory). The plugins isolate each project's memory in its own collection; this command is the single source of truth for that name and mirrors `plugins/*/scripts/derive-collection.sh`, so the bash hooks and the Python skills always target the same collection. The derived name is `ms_<sanitized-basename>_<8-hex-hash-of-the-canonical-path>`.

### Options

Takes a single optional `PATH` argument (the project directory); with none, uses the current working directory. No other options.

### Examples

```bash
$ memsearch collection-name /home/user/projects/notes
ms_notes_c486aeb4

$ memsearch collection-name          # uses the current directory
ms_memsearch_4245a118
```

Resolve once and reuse, as the memory-recall skill does:

```bash
$ COLL=$(memsearch collection-name "$MEMSEARCH_DIR")
$ memsearch search "auth flow" --collection "$COLL"
```

### Notes

- **Pure function.** Same input path → same output, no side effects. The path is canonicalised (`.`/`..` collapsed, forward-slashed) before hashing, so equivalent spellings of one directory derive the same name.
- **Hash-stable by contract.** The output must stay byte-identical to `derive-collection.sh`; changing the derivation would orphan every existing index, so it is guarded by golden tests.

---

## `memsearch summarize`

> 🔌 **Plugin helper.** Invoked by the platform plugins' `Stop` hooks to turn a session transcript (piped on **stdin**) into third-person notes using an LLM provider you configure — an alternative to each plugin's native summarizer.

Read text from stdin and summarise it with the LLM provider routed for a given plugin platform. The summary is written to stdout.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--plugin` | | *(required)* | Plugin platform: `claude-code`, `codex`, `opencode`, or `openclaw` |
| `--agent-name` | | `""` | Agent display name substituted into the prompt's `{{AGENT_NAME}}` placeholder |

### Examples

```bash
$ cat transcript.txt | memsearch summarize --plugin claude-code --agent-name "Claude"
- User asked about N+1 queries; the agent applied selectinload and added an index.
- Decided to keep the index sidecar single-file across collections.
```

### Notes

- **Opt-in per platform.** Routing comes from `plugins.<platform>.summarize.provider`. If it is empty or `native`, the command exits with status **2** and prints a notice — the plugin keeps its own native summarizer. Set a named provider to enable memsearch-managed summarization.
- **Provider must be defined.** The selected provider name must exist under `[llm.providers.<name>]` (`type`/`model`/`base_url`/`api_key`). An unknown provider exits **1**.
- **Prompt template.** Uses `prompts.summarize` when set (with `{{AGENT_NAME}}` substituted), otherwise a built-in third-person note-taker prompt.
- **Empty stdin** produces no output and exits 0.

---

## `memsearch stats`

Show statistics about the current index, including the total number of stored chunks.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |

### Examples

```bash
$ memsearch stats
Total indexed chunks: 142
```

Check stats for a specific collection on a remote server:

```bash
$ memsearch stats --milvus-uri http://localhost:19530 --collection my_project
Total indexed chunks: 87
```

### Notes

- **Stats may lag on remote Milvus Server.** The `get_collection_stats()` API on a remote Milvus Server may return stale counts immediately after an upsert. Stats are updated after segment flush and compaction. Search results are always up to date.

---

## `memsearch reset`

Drop the entire Milvus collection, permanently deleting all indexed chunks. A confirmation prompt is shown before proceeding.

### Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--collection` | `-c` | `memsearch_chunks` | Milvus collection name |
| `--milvus-uri` | | `~/.memsearch/milvus.db` | Milvus connection URI |
| `--milvus-token` | | *(none)* | Milvus auth token |
| `--yes` | `-y` | | Skip the confirmation prompt |

### Examples

```bash
$ memsearch reset
This will delete all indexed data. Continue? [y/N]: y
Dropped collection.
```

Skip the confirmation prompt (useful in scripts):

```bash
$ memsearch reset --yes
Dropped collection.
```

Reset a specific collection on a remote server:

```bash
$ memsearch reset --milvus-uri http://localhost:19530 --collection old_project --yes
Dropped collection.
```

### Notes

- **This is destructive and irreversible.** All indexed data will be lost. Your original markdown files are not affected -- you can always re-index them with `memsearch index`.
- **Only drops the collection, not the database.** If you are using Milvus Lite (a local `.db` file), the file itself remains; only the collection inside it is removed.

---

## Environment Variables

memsearch reads API keys from environment variables by default. You can also configure them in TOML config files using the `env:VAR_NAME` reference syntax or the `embedding.api_key` / `embedding.base_url` fields. See [Configuration](getting-started.md#configuration) for details.

### API Keys

| Variable | Required By | Description |
|----------|-------------|-------------|
| `OPENAI_API_KEY` | `openai` embedding provider, `openai` LLM compact provider | OpenAI API key |
| `OPENAI_BASE_URL` | *(optional)* | Override the OpenAI API base URL (for proxies or compatible APIs) |
| `GOOGLE_API_KEY` | `google` embedding provider, `gemini` LLM compact provider | Google AI API key |
| `VOYAGE_API_KEY` | `voyage` embedding provider | Voyage AI API key |
| `JINA_API_KEY` | `jina` embedding provider | Jina AI API key |
| `MISTRAL_API_KEY` | `mistral` embedding provider | Mistral AI API key |
| `ANTHROPIC_API_KEY` | `anthropic` LLM compact provider | Anthropic API key |
| `OLLAMA_HOST` | `ollama` embedding provider *(optional)* | Ollama server URL (default: `http://localhost:11434`) |

All memsearch settings (Milvus URI, embedding provider, chunking parameters, etc.) are configured via TOML config files or CLI flags -- see [Configuration](getting-started.md#configuration) for details.

### Examples

```bash
# Set API key and run a search
$ export OPENAI_API_KEY=sk-...
$ memsearch search "database schema"

# Use Google for embedding, Anthropic for compact
$ export GOOGLE_API_KEY=AIza...
$ memsearch index ./memory/ --provider google
$ memsearch compact --llm-provider anthropic
```

### Embedding Provider Reference

| Provider | Install | Default Model | Dimension | API Key Variable |
|----------|---------|---------------|-----------|-----------------|
| `openai` | included by default | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` |
| `google` | `pip install "memsearch[google]"` | `gemini-embedding-001` | 768 | `GOOGLE_API_KEY` |
| `voyage` | `pip install "memsearch[voyage]"` | `voyage-3-lite` | 512 | `VOYAGE_API_KEY` |
| `jina` | `pip install "memsearch[jina]"` | `jina-embeddings-v4` | 2048 | `JINA_API_KEY` |
| `mistral` | `pip install "memsearch[mistral]"` | `mistral-embed` | 1024 | `MISTRAL_API_KEY` |
| `ollama` | `pip install "memsearch[ollama]"` | `nomic-embed-text` | 768 | *(none, local)* |
| `local` | `pip install "memsearch[local]"` | `all-MiniLM-L6-v2` | 384 | *(none, local)* |
| `onnx` | `pip install "memsearch[onnx]"` | `gpahal/bge-m3-onnx-int8` | 1024 | *(none, local)* |

Install all optional providers at once:

```bash
$ pip install "memsearch[all]"
```
