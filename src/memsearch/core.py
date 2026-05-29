"""MemSearch — main orchestrator class."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .watcher import FileWatcher

from .chunker import _HEADING_RE, Chunk, chunk_markdown, clean_content_for_embedding, compute_chunk_id
from .compact import compact_chunks
from .edges import EdgeStore
from .embeddings import EmbeddingProvider, get_provider
from .scanner import ScannedFile, scan_paths
from .store import MilvusStore

logger = logging.getLogger(__name__)


def _is_section_head(content: str) -> bool:
    """True if the chunk's first non-empty line is a markdown heading.

    A split large-section's continuation chunks do NOT start with a heading
    (the chunker only emits the heading line in the first sub-chunk), so this
    reliably starts a new section run at each real heading and keeps a single
    heading-section's sub-chunks together.
    """
    for line in content.lstrip().splitlines():
        if line.strip():
            return bool(_HEADING_RE.match(line))
    return False


def _structural_edges(
    items: list[tuple[str, str]], model: str, *, window: int = 4
) -> list[tuple[str, str, str, float, str]]:
    """items = (chunk_id, content) in file order. Returns sibling + same_section edges.

    - sibling: every consecutive pair (weight 1.0)
    - same_section: windowed (+/-window) pairs within a section run (weight 0.6),
      where a section run starts at a section-head chunk (or the first chunk).
    """
    edges: list[tuple[str, str, str, float, str]] = []
    for (a_id, _a), (b_id, _b) in pairwise(items):
        edges.append((a_id, b_id, "sibling", 1.0, model))
    runs: list[list[str]] = []
    for cid, content in items:
        if not runs or _is_section_head(content):
            runs.append([])
        runs[-1].append(cid)
    for run in runs:
        for i, h in enumerate(run):
            edges.extend(
                (h, run[j], "same_section", 0.6, model) for j in range(i + 1, min(i + 1 + window, len(run)))
            )
    return edges


class MemSearch:
    """High-level API for semantic memory search.

    Parameters
    ----------
    paths:
        Directories / files to index.
    embedding_provider:
        Name of the embedding backend (``"openai"``, ``"google"``, etc.).
    embedding_model:
        Override the default model for the chosen provider.
    milvus_uri:
        Milvus connection URI.  A local ``*.db`` path uses Milvus Lite,
        ``http://host:port`` connects to a Milvus server, and a
        ``https://*.zillizcloud.com`` URL connects to Zilliz Cloud.
    milvus_token:
        Authentication token for Milvus server or Zilliz Cloud.
        Not needed for Milvus Lite (local).
    collection:
        Milvus collection name.  Use different names to isolate
        agents sharing the same Milvus server.
    """

    def __init__(
        self,
        paths: list[str | Path] | None = None,
        *,
        embedding_provider: str = "openai",
        embedding_model: str | None = None,
        embedding_batch_size: int = 0,
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
        milvus_uri: str = "~/.memsearch/milvus.db",
        milvus_token: str | None = None,
        collection: str = "memsearch_chunks",
        description: str = "",
        max_chunk_size: int = 1500,
        overlap_lines: int = 2,
        reranker_model: str = "",
        graph_enabled: bool = False,
        graph_edges_uri: str = "~/.memsearch/edges.db",
        graph_weight: float = 0.5,
        graph_seed_k: int = 10,
        graph_fanout: int = 5,
        graph_similar_top_n: int = 5,
        graph_similar_threshold: float = 0.7,
        graph_structural: bool = True,
    ) -> None:
        self._paths = [str(p) for p in (paths or [])]
        self._max_chunk_size = max_chunk_size
        self._overlap_lines = overlap_lines
        self._embedder: EmbeddingProvider = get_provider(
            embedding_provider,
            model=embedding_model,
            batch_size=embedding_batch_size,
            base_url=embedding_base_url,
            api_key=embedding_api_key,
        )
        self._store = MilvusStore(
            uri=milvus_uri,
            token=milvus_token,
            collection=collection,
            dimension=self._embedder.dimension,
            description=description,
        )
        self._reranker_model = reranker_model
        self._graph_enabled = graph_enabled
        self._graph_weight = graph_weight
        self._graph_seed_k = graph_seed_k
        self._graph_fanout = graph_fanout
        self._graph_similar_top_n = graph_similar_top_n
        self._graph_similar_threshold = graph_similar_threshold
        self._graph_structural = graph_structural
        self._edges = EdgeStore(graph_edges_uri) if graph_enabled else None
        self._empty_edges_warned = False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index(self, *, force: bool = False) -> int:
        """Scan paths and index all markdown files.

        Returns the number of chunks indexed.  Also removes chunks for
        files that no longer exist on disk (deleted-file cleanup).
        """
        files = scan_paths(self._paths)
        total = 0
        failed = 0
        active_sources: set[str] = set()
        for f in files:
            active_sources.add(str(f.path))
            try:
                n = await self._index_file(f, force=force)
                total += n
            except Exception:
                failed += 1
                logger.exception("Failed to index %s, skipping", f.path)

        # Clean up chunks for files that no longer exist
        indexed_sources = self._store.indexed_sources()
        for source in indexed_sources:
            if source not in active_sources:
                if self._edges is not None:
                    self._edges.delete_by_hashes(list(self._store.hashes_by_source(source)))
                self._store.delete_by_source(source)
                logger.info("Removed stale chunks for deleted file: %s", source)

        if failed:
            logger.warning("Indexed %d chunks from %d files (%d files failed)", total, len(files) - failed, failed)
        else:
            logger.info("Indexed %d chunks from %d files", total, len(files))
        return total

    async def index_file(self, path: str | Path) -> int:
        """Index a single file.  Returns number of chunks."""
        p = Path(path).expanduser().resolve()
        _st = p.stat()
        sf = ScannedFile(path=p, mtime=_st.st_mtime, size=_st.st_size)
        return await self._index_file(sf)

    async def _index_file(self, f: ScannedFile, *, force: bool = False) -> int:
        source = str(f.path)
        text = f.path.read_text(encoding="utf-8")
        chunks = chunk_markdown(
            text,
            source=source,
            max_chunk_size=self._max_chunk_size,
            overlap_lines=self._overlap_lines,
        )
        model = self._embedder.model_name

        # Compute composite chunk IDs (matching OpenClaw format)
        chunk_ids = {compute_chunk_id(c.source, c.start_line, c.end_line, c.content_hash, model) for c in chunks}
        old_ids = self._store.hashes_by_source(source)

        # Delete stale chunks that are no longer in the file
        stale = old_ids - chunk_ids
        if stale:
            self._store.delete_by_hashes(list(stale))
            if self._edges is not None:
                self._edges.delete_by_hashes(list(stale))

        if not chunks:
            return 0

        # Build structural edges from the FULL chunks list, before the `force`
        # filter reduces `chunks` to the edited-onward subset.
        if self._edges is not None and self._graph_structural and chunks:
            items = [
                (compute_chunk_id(c.source, c.start_line, c.end_line, c.content_hash, model), c.content) for c in chunks
            ]
            self._edges.add_edges(_structural_edges(items, model))

        if not force:
            # Only embed chunks whose ID doesn't already exist
            chunks = [
                c
                for c in chunks
                if compute_chunk_id(c.source, c.start_line, c.end_line, c.content_hash, model) not in old_ids
            ]
            if not chunks:
                return 0

        return await self._embed_and_store(chunks)

    async def _embed_and_store(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0

        model = self._embedder.model_name
        # Clean content for embedding: strip HTML comments and metadata noise
        # so the embedding vector captures semantics, not UUIDs/paths.
        # The original content is preserved in the Milvus record below.
        contents = [clean_content_for_embedding(c.content) for c in chunks]
        embeddings = await self._embedder.embed(contents)

        records: list[dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            chunk_id = compute_chunk_id(
                chunk.source,
                chunk.start_line,
                chunk.end_line,
                chunk.content_hash,
                model,
            )
            records.append(
                {
                    "chunk_hash": chunk_id,
                    "embedding": embeddings[i],
                    "content": chunk.content,
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "heading_level": chunk.heading_level,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                }
            )

        n = self._store.upsert(records)
        if self._edges is not None and self._graph_similar_top_n > 0:
            ids = [r["chunk_hash"] for r in records]
            sim_edges: list[tuple[str, str, str, float, str]] = []
            hits: list[list[dict[str, Any]]] = []
            for i in range(0, len(embeddings), 1024):
                hits.extend(self._store.dense_search(embeddings[i : i + 1024], top_k=self._graph_similar_top_n + 1))
            for own_id, hit_list in zip(ids, hits, strict=True):
                for h in hit_list:
                    nbr, sim = h["chunk_hash"], max(0.0, h["score"])
                    if nbr != own_id and sim >= self._graph_similar_threshold:
                        sim_edges.append((own_id, nbr, "similar", sim, model))
            self._edges.add_edges(sim_edges)
        return n

    async def rebuild_edges(self) -> int:
        """Rebuild chunk_edges from stored chunks/embeddings. No embedding API calls."""
        if self._edges is None:
            return 0
        self._edges.clear()
        edges: list[tuple[str, str, str, float, str]] = []
        rows = list(self._store.iter_chunks(with_embeddings=(self._graph_similar_top_n > 0)))
        model = self._embedder.model_name
        if self._graph_structural:
            by_src: dict[str, list[dict]] = {}
            for r in rows:
                by_src.setdefault(r["source"], []).append(r)
            for src_rows in by_src.values():
                src_rows.sort(key=lambda r: r["start_line"])
                items = [(r["chunk_hash"], r["content"]) for r in src_rows]
                edges += _structural_edges(items, model)
        if self._graph_similar_top_n > 0:
            ids = [r["chunk_hash"] for r in rows]
            vecs = [r["embedding"] for r in rows]
            for i in range(0, len(vecs), 1024):
                hits = self._store.dense_search(vecs[i : i + 1024], top_k=self._graph_similar_top_n + 1)
                for own_id, hit_list in zip(ids[i : i + 1024], hits, strict=True):
                    for h in hit_list:
                        nbr, sim = h["chunk_hash"], max(0.0, h["score"])
                        if nbr != own_id and sim >= self._graph_similar_threshold:
                            edges.append((own_id, nbr, "similar", sim, model))
        self._edges.add_edges(edges)
        return len(edges)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        source_prefix: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across indexed chunks.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum results to return.
        source_prefix:
            Optional path prefix to scope results. Only chunks whose
            ``source`` starts with this prefix are returned.

        Returns
        -------
        list[dict]
            Each dict contains ``content``, ``source``, ``heading``,
            ``score``, and other metadata.
        """
        filter_expr = ""
        if source_prefix is not None:
            prefix = str(Path(source_prefix).expanduser().resolve())
            escaped = prefix.replace("\\", "\\\\").replace('"', '\\"')
            filter_expr = f'source like "{escaped}%"'

        embeddings = await self._embedder.embed([query])
        fetch_k = top_k * 3 if self._reranker_model else top_k
        results = self._store.search(embeddings[0], query_text=query, top_k=fetch_k, filter_expr=filter_expr)
        if self._graph_enabled and self._edges is not None and results:
            if not self._empty_edges_warned and self._edges.is_empty() and self._store.count() > 0:
                logger.warning("graph mode on but no edges — run `memsearch graph rebuild`")
                self._empty_edges_warned = True
            results = self._graph_expand(results, top_k=fetch_k, filter_expr=filter_expr)
        if self._reranker_model and results:
            from .reranker import rerank

            results = rerank(query, results, model_name=self._reranker_model, top_k=top_k)
        return results

    def _graph_expand(
        self, base: list[dict[str, Any]], *, top_k: int, filter_expr: str
    ) -> list[dict[str, Any]]:
        rrf_k, w_g = 60, self._graph_weight
        base_by_hash = {r["chunk_hash"]: r for r in base}
        base_rank = {h: i + 1 for i, h in enumerate(base_by_hash)}
        seeds = list(base_by_hash)[: self._graph_seed_k]
        edges = self._edges.neighbors(seeds, limit_per_node=self._graph_fanout)
        graph_score: dict[str, float] = {}
        for nbr, weight, seed in edges:
            s = base_by_hash[seed]["score"] * weight
            if s > graph_score.get(nbr, 0.0):
                graph_score[nbr] = s
        if not graph_score:
            return base
        missing = [h for h in graph_score if h not in base_by_hash]
        records = dict(base_by_hash)
        records.update(self._fetch_chunks(missing, filter_expr))
        graph_ranked = sorted(graph_score, key=graph_score.get, reverse=True)
        graph_rank = {h: i + 1 for i, h in enumerate(graph_ranked)}
        fused = {
            h: (1.0 / (rrf_k + base_rank[h]) if h in base_rank else 0.0)
            + (w_g / (rrf_k + graph_rank[h]) if h in graph_rank else 0.0)
            for h in records
        }
        max_fused = (1.0 + w_g) / (rrf_k + 1)
        ordered = sorted(records.values(), key=lambda r: fused[r["chunk_hash"]], reverse=True)
        return [{**r, "score": fused[r["chunk_hash"]] / max_fused} for r in ordered[:top_k]]

    def _fetch_chunks(self, hashes: list[str], filter_expr: str) -> dict[str, dict[str, Any]]:
        from .store import _escape_filter_value

        if not hashes:
            return {}
        in_list = ", ".join(f'"{_escape_filter_value(h)}"' for h in hashes)
        expr = f"chunk_hash in [{in_list}]"
        if filter_expr:
            expr = f"({expr}) and ({filter_expr})"
        rows = self._store.query(filter_expr=expr)
        return {r["chunk_hash"]: {**r, "score": 0.0} for r in rows}

    # ------------------------------------------------------------------
    # Compact (compress memories)
    # ------------------------------------------------------------------

    async def compact(
        self,
        *,
        source: str | None = None,
        llm_provider: str = "openai",
        llm_model: str | None = None,
        prompt_template: str | None = None,
        output_dir: str | Path | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
    ) -> str:
        """Compress indexed chunks into a summary and append to a daily log.

        The summary is appended to ``memory/YYYY-MM-DD.md`` inside the
        output directory (defaults to the first configured path).  The
        next ``index()`` or ``watch`` cycle will pick it up as a normal
        markdown file — keeping markdown as the single source of truth.

        Parameters
        ----------
        source:
            If given, only compact chunks from this source file.
        llm_provider:
            LLM backend for summarization.
        llm_model:
            Override the default model.
        prompt_template:
            Custom prompt template for the LLM.  Must contain a
            ``{chunks}`` placeholder.  Defaults to the built-in prompt.
        output_dir:
            Directory to write the compact file into.  Defaults to the
            first entry in *paths*.
        llm_base_url:
            Custom base URL for OpenAI-compatible API endpoints.  Only
            used when *llm_provider* is ``"openai"``.
        llm_api_key:
            API key for the LLM provider.  Only used when *llm_provider*
            is ``"openai"``.

        Returns
        -------
        str
            The generated summary markdown.
        """
        from .store import _escape_filter_value

        filter_expr = f'source == "{_escape_filter_value(source)}"' if source else ""
        all_chunks = self._store.query(filter_expr=filter_expr)
        if not all_chunks:
            return ""

        summary = await compact_chunks(
            all_chunks,
            llm_provider=llm_provider,
            model=llm_model,
            prompt_template=prompt_template,
            base_url=llm_base_url,
            api_key=llm_api_key,
        )

        # Write summary to memory/YYYY-MM-DD.md (append)
        base = Path(output_dir) if output_dir else Path(self._paths[0]) if self._paths else Path.cwd()
        memory_dir = base / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        compact_file = memory_dir / f"{date.today()}.md"
        compact_heading = "\n\n## Memory Compact\n\n"
        with open(compact_file, "a", encoding="utf-8") as f:
            if compact_file.stat().st_size == 0:
                f.write(f"# {date.today()}\n")
            f.write(compact_heading)
            f.write(summary)
            f.write("\n")

        # Index the updated file immediately
        n = await self.index_file(compact_file)
        logger.info("Compacted %d chunks into %s (%d new chunks indexed)", len(all_chunks), compact_file, n)
        return summary

    # ------------------------------------------------------------------
    # Watch
    # ------------------------------------------------------------------

    def watch(
        self,
        *,
        on_event: Callable[[str, str, Path], None] | None = None,
        debounce_ms: int | None = None,
    ) -> FileWatcher:
        """Watch configured paths for markdown changes and auto-index.

        Starts a background thread that monitors the filesystem.  When a
        markdown file is created or modified it is re-indexed automatically;
        when deleted its chunks are removed from the store.

        Parameters
        ----------
        on_event:
            Optional callback invoked *after* each event is processed.
            Signature: ``(event_type, action_summary, file_path)``.
            ``event_type`` is ``"created"``, ``"modified"``, or ``"deleted"``.

        Returns
        -------
        FileWatcher
            The running watcher.  Call ``watcher.stop()`` when done, or
            use it as a context manager.

        Example
        -------
        ::

            mem = MemSearch(paths=["./docs/"])
            watcher = mem.watch()
            # ... watcher auto-indexes in background ...
            watcher.stop()
        """
        from .watcher import FileWatcher

        # Persistent event loop for watcher callbacks.
        #
        # asyncio.run() creates and closes a new loop on every call. Async
        # HTTP clients (httpx — used by ollama, openai, voyage) cache
        # connections tied to that loop, so a second asyncio.run() hits the
        # closed loop and raises RuntimeError: Event loop is closed.
        # This is a known httpx limitation:
        #   https://github.com/encode/httpx/discussions/2489
        #   https://github.com/encode/httpx/discussions/2959
        loop = asyncio.new_event_loop()

        def _on_change(event_type: str, file_path: Path) -> None:
            try:
                if event_type == "deleted":
                    if self._edges is not None:
                        self._edges.delete_by_hashes(list(self._store.hashes_by_source(str(file_path))))
                    self._store.delete_by_source(str(file_path))
                    summary = f"Removed chunks for {file_path}"
                else:
                    n = loop.run_until_complete(self.index_file(file_path))
                    summary = f"Indexed {n} chunks from {file_path}"
                logger.info(summary)
                if on_event is not None:
                    on_event(event_type, summary, file_path)
            except Exception:
                # Watch is a long-running daemon callback — swallow any failure
                # (network blips, provider 500s, malformed embeddings, disk
                # errors, etc.) so a single bad file cannot crash the watcher.
                logger.exception("Failed to process %s event for %s", event_type, file_path)

        fw_kwargs: dict[str, Any] = {}
        if debounce_ms is not None:
            fw_kwargs["debounce_ms"] = debounce_ms
        watcher = FileWatcher(self._paths, _on_change, **fw_kwargs)
        watcher.start()
        return watcher

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @property
    def store(self) -> MilvusStore:
        return self._store

    def close(self) -> None:
        """Release resources."""
        self._store.close()
        if self._edges is not None:
            self._edges.close()

    def __enter__(self) -> MemSearch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
