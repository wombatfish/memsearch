---
name: memory-recall
description: "Search and recall relevant memories from past sessions via memsearch. Use when the user's question could benefit from historical context, past decisions, debugging notes, previous conversations, or project knowledge -- especially questions like 'what did I decide about X', 'why did we do Y', or 'have I seen this before'. Also use when you see `[memsearch] Memory available` hints injected via SessionStart or UserPromptSubmit. Typical flow: search for 3-5 chunks, expand the most relevant, optionally deep-drill into original transcripts via the anchor format. Skip when the question is purely about current code state (use Read/Grep), ephemeral (today's task only), or the user has explicitly asked to ignore memory."
metadata:
  openclaw:
    emoji: "🧠"
---

You have three memory tools for progressive recall. Start with search, go deeper only when needed.

## Tools (use progressively)

### 1. memory_search — Start here
Semantic search across all past conversation memories.
- Returns: chunk summaries with dates, topics, chunk_hash identifiers
- Use for: "What did we discuss about X?", "Have I asked about Y before?"

### 2. memory_get — When search results aren't detailed enough
Expands a specific chunk_hash to show the full markdown section with surrounding context.
- Input: chunk_hash from memory_search results
- Returns: full section text, may include transcript anchors (<!-- session:UUID transcript:PATH -->)
- Use for: "Show me the details", "I need more context on that result"

### 3. memory_transcript — When you need the exact original conversation
Parses the original session transcript to retrieve the raw dialogue.
- Input: transcript_path from the anchor comment in memory_get results
- Returns: formatted conversation with [Human]/[Assistant] labels and tool calls
- Use for: "What exactly did I say?", "Show me the original conversation"
- If the anchor format is unfamiliar (e.g. `rollout:`, `turn:`, `db:` instead of `transcript:`), try reading the referenced file directly to explore its structure and locate the relevant conversation by the session or turn identifiers in the anchor.

## Recall procedure

1. **Rewrite**: Resolve any pronouns or shorthand ("this bug", "that file") using the surrounding conversation, producing a self-contained query string.

2. **Generate variants** (up to 3 total):
   - *Semantic*: a paraphrase of the core intent.
   - *Keyword-heavy*: exact identifiers, file names, error strings (feeds the BM25 leg).
   - *Temporal* (only when the question implies time — "recently", "last week", "what did I decide about X"): include date cues or session references.

3. **Search**: call `memory_search(query, top_k: 15)` for each variant (max 3 calls). The results are compact summaries — use `heading`, `date`, and `preview` fields to judge relevance without expanding.

4. **Union + dedup**: merge results across variants by `chunk_hash`, keeping the highest score per hash. Chunks hit by multiple variants are preferred candidates for the next step.

5. **Filter-before-expand**: from the compact summaries, pick the 3–5 most promising hashes and call `memory_get(chunk_hash)` for each. Do **not** use HyDE. Expand only the chosen few — do not expand every search result.

6. **Deep drill (optional)**: if an expanded chunk reveals a `<!-- session:UUID transcript:PATH -->` anchor and the original dialogue seems critical, call `memory_transcript(transcript_path)`.

## Decision guide

| User intent | Tools to use |
|---|---|
| Quick recall ("did we discuss X?") | memory_search only |
| Need details ("what was the solution?") | memory_search → memory_get |
| Need original dialogue ("show me the exact conversation") | memory_search → memory_get → memory_transcript |

## Tips
- memory_search returns chunk_hash — pass it to memory_get for expansion
- memory_get may reveal `<!-- session:UUID transcript:PATH -->` anchors — pass the path to memory_transcript
- If memory_search returns no results, try rephrasing with different keywords
- Results are sorted by relevance (hybrid BM25 + vector search)

## When unsure what to search

The SessionStart injection already shows you a heading-level preview of recent memory files — skim it first to spot concrete topics (dates, session numbers, task names). If that preview doesn't surface an obvious query, try broad keywords like `overview`, `recent work`, or a topic guess — hybrid BM25 + vector retrieval will surface chunks that share any of the terms, and you can iterate from there.
