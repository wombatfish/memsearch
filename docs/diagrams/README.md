# memsearch architecture diagrams

Five layered Excalidraw diagrams documenting how memsearch works end-to-end.
Open any `.excalidraw` file in [excalidraw.com](https://excalidraw.com) or the
VS Code *Excalidraw* extension.

The canonical runtime depicted is **Windows + remote Milvus (podman) + ONNX
embedding** — the setup that actually runs here. POSIX/Milvus-Lite divergences
(one-time SessionStart index instead of a watcher, `milvus_lite` orphan GC) are
shown as dashed callouts where they differ.

| # | File | What it shows |
|---|------|---------------|
| 1 | [`01-architecture.excalidraw`](01-architecture.excalidraw) | Component map: Claude Code hooks + skill → memsearch package → data stores. The "what talks to what". |
| 2 | [`02-session-lifecycle.excalidraw`](02-session-lifecycle.excalidraw) | Chronological hook choreography: SessionStart → per-turn (UserPromptSubmit + Stop) → SessionEnd, with files created/read + DB writes per step. |
| 3 | [`03-index-pipeline.excalidraw`](03-index-pipeline.excalidraw) | `markdown → scan → chunk → compute_chunk_id → diff → embed → Milvus upsert + edge writes → stale-delete → deleted-file GC`. |
| 4 | [`04-search-recall.excalidraw`](04-search-recall.excalidraw) | The 3-layer `memory-recall` skill (search → expand → transcript) + hybrid-search internals (dense + BM25 + RRF + graph-expand + optional rerank). |
| 5 | [`05-background-curation.excalidraw`](05-background-curation.excalidraw) | Self-maintaining memory: Stop summarization (haiku), `compact`, and opt-in SessionEnd maintenance (CORRECTIONS/PROJECT/USER). |

## Two databases

- **Milvus collection** `ms_<sanitized-project>_<8-char-sha256-of-abspath>` —
  per project. Fields: `chunk_hash` (VARCHAR64 PK), `embedding`
  (FLOAT_VECTOR, COSINE/FLAT), `content` (analyzer on), `sparse_vector`
  (BM25, auto-derived from `content`), `source`, `heading`, `heading_level`,
  `start_line`, `end_line`. Written by `index`/`watch`/Stop.
- **`~/.memsearch/edges.db`** — a *single global* SQLite file shared across all
  collections (`graph.enabled` defaults to `True` on the CLI/plugin path).
  Table `chunk_edges(src_hash, dst_hash, relation ∈ {sibling, same_section,
  similar}, weight, model)`. Written during indexing; read during graph-expand.

Markdown remains the source of truth; both databases are derived indexes,
rebuildable from the `.md` files at any time.

## Regenerating

The diagrams are generated from a single compact spec so the live-render dialect
and the committed full-format dialect can never drift:

```bash
python docs/diagrams/build_diagrams.py
```

This rewrites the five `.excalidraw` files here and the shorthand previews under
`.scratch/diagrams/`. The script lints every committed file: valid JSON, no
`create_view`-only `label`/`cameraUpdate` shortcuts, and every text element
carries full font metadata with a readable stroke color (the failure mode where
text silently vanishes on excalidraw.com).
