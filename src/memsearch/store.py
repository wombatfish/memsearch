"""Milvus vector storage layer using MilvusClient API."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

_RRF_K = 60  # standard RRF k from Cormack et al.; tune here, used by core graph fusion too


def _escape_filter_value(value: str) -> str:
    """Escape backslashes and double quotes for Milvus filter expressions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MilvusStore:
    """Thin wrapper around ``pymilvus.MilvusClient`` for chunk storage.

    Collections use both dense vector and BM25 sparse vector fields,
    with hybrid search (semantic + keyword, RRF reranking) by default.
    """

    DEFAULT_COLLECTION = "memsearch_chunks"

    def __init__(
        self,
        uri: str = "~/.memsearch/milvus.db",
        *,
        token: str | None = None,
        collection: str = DEFAULT_COLLECTION,
        dimension: int | None = 1536,
        description: str = "",
        consistency_level: str = "",
    ) -> None:
        from pymilvus import MilvusClient

        is_local = not uri.startswith(("http", "tcp"))
        if is_local and sys.platform == "win32":
            raise RuntimeError(
                "milvus-lite does not support Windows (no wheels on PyPI).\n"
                "Use a remote Milvus server instead:\n"
                "  docker run -d -p 19530:19530 milvusdb/milvus:latest standalone\n"
                "  MemSearch(milvus_uri='http://localhost:19530')\n"
                "Or run memsearch inside WSL2: "
                "https://learn.microsoft.com/en-us/windows/wsl/install"
            )
        resolved = str(Path(uri).expanduser()) if is_local else uri
        if is_local:
            Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        connect_kwargs: dict[str, Any] = {"uri": resolved}
        if token:
            connect_kwargs["token"] = token
        try:
            self._client = MilvusClient(**connect_kwargs)
        except Exception as exc:
            if is_local:
                raise RuntimeError(
                    "Failed to open the local Milvus Lite database. If this database was created "
                    "with an older Milvus Lite release, it may not be compatible with Milvus Lite "
                    "3.x. Move the existing .db file aside, then rebuild the index from your "
                    "source markdown files with 'memsearch index'. Alternatively, use Milvus "
                    "Server via Docker or Zilliz Cloud."
                ) from exc
            raise
        self._is_lite = is_local
        self._resolved_uri = resolved
        self._collection = collection
        self._dimension = dimension
        self._description = description
        # Read-path consistency override. Milvus Lite is locally strong-consistent,
        # so the level only matters for remote: default "Bounded" lags freshly-upserted
        # data by ~seconds, which makes cross-process read-after-write (watcher writes,
        # recall searches) miss new chunks. Setting "Strong" closes that window. Empty =
        # inherit the collection default (current behaviour). Never sent for Lite.
        self._consistency_level = consistency_level
        self._ensure_collection()

    def _consistency_kwargs(self) -> dict[str, str]:
        """consistency_level kwarg for read ops — only on remote when explicitly set.

        Normalised to the capitalised enum name pymilvus requires: resolution is
        ``ConsistencyLevel.Value(name)`` (protobuf, case-sensitive), so "strong"
        raises InvalidConsistencyLevel while "Strong" is valid. Every valid level
        (Strong/Bounded/Session/Eventually/Customized) equals its ``.capitalize()``
        form, so this accepts any casing from CLI/config/skill callers.
        """
        level = self._consistency_level.strip()
        if self._is_lite or not level:
            return {}
        return {"consistency_level": level.capitalize()}

    def _nonempty(self) -> bool:
        """Whether the collection holds any rows.

        ``get_collection_stats`` counts only *sealed* segments, so on remote a fresh
        collection whose first writes aren't auto-flushed yet reports row_count=0 even
        though the data exists in growing segments. When a consistency override is in
        effect, confirm true emptiness with a consistency-honouring point query before
        short-circuiting — otherwise the BM25 empty-guard would drop just-written data.
        """
        row_count = int(self._client.get_collection_stats(self._collection).get("row_count", 0))
        if row_count > 0:
            return True
        cons = self._consistency_kwargs()
        if not cons:
            return False
        probe = self._client.query(
            collection_name=self._collection,
            filter='chunk_hash != ""',
            output_fields=["chunk_hash"],
            limit=1,
            **cons,
        )
        return bool(probe)

    def _ensure_collection(self) -> None:
        if self._client.has_collection(self._collection):
            self._check_dimension()
            self._load_collection()
            return

        if self._dimension is None:
            return  # read-only mode: don't create a new collection

        from pymilvus import DataType, Function, FunctionType

        schema = self._client.create_schema(
            enable_dynamic_field=True,
            description=self._description,
        )
        schema.add_field(field_name="chunk_hash", datatype=DataType.VARCHAR, max_length=64, is_primary=True)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self._dimension)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535, enable_analyzer=True)
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="heading", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="heading_level", datatype=DataType.INT64)
        schema.add_field(field_name="start_line", datatype=DataType.INT64)
        schema.add_field(field_name="end_line", datatype=DataType.INT64)
        schema.add_function(
            Function(
                name="bm25_fn",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
            )
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="FLAT", metric_type="COSINE")
        index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")

        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
        )
        self._load_collection()

    def _load_collection(self) -> None:
        """Load the collection before query/search operations."""
        try:
            self._client.load_collection(collection_name=self._collection)
        except TypeError:
            self._client.load_collection(self._collection)

    def _check_dimension(self) -> None:
        """Verify that the existing collection's embedding dimension matches."""
        if self._dimension is None:
            return  # no dimension specified — skip check (read-only mode)
        try:
            info = self._client.describe_collection(self._collection)
        except Exception:
            return  # best-effort; skip if describe is not supported
        for field in info.get("fields", []):
            if field.get("name") == "embedding":
                existing_dim = field.get("params", {}).get("dim")
                if existing_dim is not None and int(existing_dim) != self._dimension:
                    raise ValueError(
                        f"Embedding dimension mismatch: collection '{self._collection}' "
                        f"has dim={existing_dim} but the current embedding provider "
                        f"outputs dim={self._dimension}. "
                        f"Run 'memsearch reset --yes' to drop the collection and re-index, "
                        f"or use a different --milvus-uri / --collection."
                    )
                break

    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        """Insert or update chunks (keyed by ``chunk_hash`` primary key).

        ``sparse_vector`` is auto-generated by the BM25 Function from
        ``content`` — do NOT include it in chunk dicts.
        """
        if not chunks:
            return 0
        result = self._client.upsert(
            collection_name=self._collection,
            data=chunks,
        )
        return result.get("upsert_count", len(chunks)) if isinstance(result, dict) else len(chunks)

    def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        top_k: int = 10,
        filter_expr: str = "",
    ) -> list[dict[str, Any]]:
        """Hybrid search: dense vector + BM25 full-text with RRF reranking."""
        from pymilvus import AnnSearchRequest, RRFRanker

        # BM25 crashes on empty collections (avgdl=0 → NaN). See #306.
        if not self._nonempty():
            return []

        req_kwargs: dict[str, Any] = {}
        if filter_expr:
            req_kwargs["expr"] = filter_expr

        dense_req = AnnSearchRequest(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            **req_kwargs,
        )

        bm25_req = AnnSearchRequest(
            data=[query_text] if query_text else [""],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=top_k,
            **req_kwargs,
        )

        reqs = [dense_req, bm25_req]
        rrf_k = _RRF_K
        results = self._client.hybrid_search(
            collection_name=self._collection,
            reqs=reqs,
            ranker=RRFRanker(k=rrf_k),
            limit=top_k,
            output_fields=self._QUERY_FIELDS,
            **self._consistency_kwargs(),
        )

        if not results or not results[0]:
            return []
        # Normalize RRF scores to [0, 1].
        # Theoretical max = num_retrievers / (k + 1), when a result ranks #1 in every retriever.
        max_rrf = len(reqs) / (rrf_k + 1)
        return [{**hit["entity"], "score": hit["distance"] / max_rrf} for hit in results[0]]

    _QUERY_FIELDS: ClassVar[list[str]] = [
        "content",
        "source",
        "heading",
        "chunk_hash",
        "heading_level",
        "start_line",
        "end_line",
    ]

    def dense_search(
        self, embeddings: list[list[float]], *, top_k: int = 6, filter_expr: str = ""
    ) -> list[list[dict[str, Any]]]:
        """Per-vector dense COSINE search. One hit-list per input vector;
        each hit = {"chunk_hash": str, "score": float}  (score = cosine similarity in [-1,1])."""
        if not self._nonempty():
            return [[] for _ in embeddings]

        search_kwargs: dict[str, Any] = {
            "collection_name": self._collection,
            "data": embeddings,
            "anns_field": "embedding",
            "search_params": {"metric_type": "COSINE", "params": {}},
            "limit": top_k,
            "output_fields": ["chunk_hash"],
            **self._consistency_kwargs(),
        }
        if filter_expr:
            search_kwargs["filter"] = filter_expr

        res = self._client.search(**search_kwargs)
        return [[{"chunk_hash": h["entity"]["chunk_hash"], "score": h["distance"]} for h in hits] for hits in res]

    def iter_chunks(self, *, with_embeddings: bool = False, filter_expr: str = "") -> Iterator[dict[str, Any]]:
        """Yield every stored chunk via query_iterator (paginated, avoids loading the whole
        collection at once). Includes 'embedding' in output_fields when with_embeddings=True."""
        output_fields = list(self._QUERY_FIELDS)
        if with_embeddings:
            output_fields.append("embedding")

        it = self._client.query_iterator(
            collection_name=self._collection,
            batch_size=1000,
            filter=filter_expr if filter_expr else 'chunk_hash != ""',
            output_fields=output_fields,
            **self._consistency_kwargs(),
        )
        while True:
            batch = it.next()
            if not batch:
                it.close()
                break
            yield from batch

    def query(self, *, filter_expr: str = "") -> list[dict[str, Any]]:
        """Retrieve chunks by scalar filter (no vector needed)."""
        kwargs: dict[str, Any] = {
            "collection_name": self._collection,
            "output_fields": self._QUERY_FIELDS,
            "filter": filter_expr if filter_expr else 'chunk_hash != ""',
            **self._consistency_kwargs(),
        }
        return self._client.query(**kwargs)

    def hashes_by_source(self, source: str) -> set[str]:
        """Return all chunk_hash values for a given source file."""
        escaped = _escape_filter_value(source)
        results = self._client.query(
            collection_name=self._collection,
            filter=f'source == "{escaped}"',
            output_fields=["chunk_hash"],
            **self._consistency_kwargs(),
        )
        return {r["chunk_hash"] for r in results}

    def indexed_sources(self) -> set[str]:
        """Return all distinct source values in the collection.

        Streams via query_iterator: a plain query() without limit is capped at
        16384 rows by remote Milvus, silently truncating the source set (and
        making the deleted-file GC skip sources).
        """
        it = self._client.query_iterator(
            collection_name=self._collection,
            batch_size=1000,
            filter='chunk_hash != ""',
            output_fields=["source"],
            **self._consistency_kwargs(),
        )
        sources: set[str] = set()
        while True:
            batch = it.next()
            if not batch:
                it.close()
                break
            sources.update(r["source"] for r in batch)
        return sources

    def delete_by_source(self, source: str) -> None:
        """Delete all chunks from a given source file."""
        escaped = _escape_filter_value(source)
        self._client.delete(
            collection_name=self._collection,
            filter=f'source == "{escaped}"',
        )

    def delete_by_hashes(self, hashes: list[str]) -> None:
        """Delete chunks by their content hashes (primary keys)."""
        if not hashes:
            return
        self._client.delete(
            collection_name=self._collection,
            ids=hashes,
        )

    def count(self) -> int:
        """Return total number of stored chunks."""
        stats = self._client.get_collection_stats(self._collection)
        return int(stats.get("row_count", 0))  # pymilvus may return row_count as str

    def drop(self) -> None:
        """Drop the entire collection."""
        if self._client.has_collection(self._collection):
            self._client.drop_collection(self._collection)

    def close(self) -> None:
        self._client.close()
        # Milvus Lite: release the server process to free the db file lock.
        # Without this, the milvus_lite subprocess outlives the parent and
        # blocks subsequent CLI invocations from opening the same .db file.
        if self._is_lite:
            try:
                from milvus_lite.server_manager import server_manager_instance

                server_manager_instance.release_server(self._resolved_uri)
            except Exception:
                pass

    def __enter__(self) -> MilvusStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
