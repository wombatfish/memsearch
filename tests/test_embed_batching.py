"""Tests for embedding batch-size handling.

Uses a fake embedding provider to verify that large chunk lists are
split into batches that respect the provider's batch_size limit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from memsearch.chunker import Chunk
from memsearch.core import MemSearch
from memsearch.embeddings.utils import batched_embed
from memsearch.store import MilvusStore

# Tests that instantiate MilvusStore against a local URI require milvus-lite,
# which has no Windows wheels. The batched_embed utility tests above need no skip.
_requires_milvus_lite = pytest.mark.skipif(
    sys.platform == "win32", reason="milvus-lite is unsupported on Windows"
)

# -- batched_embed utility tests --


class _Recorder:
    """Records call sizes for embed assertions."""

    def __init__(self, dim: int = 4) -> None:
        self.call_sizes: list[int] = []
        self._dim = dim

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.call_sizes.append(len(texts))
        return [[0.0] * self._dim for _ in texts]


@pytest.mark.asyncio
async def test_batched_embed_splits():
    rec = _Recorder()
    result = await batched_embed(list("abcdefghij"), rec, batch_size=4)
    assert len(result) == 10
    assert rec.call_sizes == [4, 4, 2]


@pytest.mark.asyncio
async def test_batched_embed_single_batch():
    rec = _Recorder()
    result = await batched_embed(list("abc"), rec, batch_size=4)
    assert len(result) == 3
    # Under the limit — should be a single call, not split
    assert rec.call_sizes == [3]


@pytest.mark.asyncio
async def test_batched_embed_exact():
    rec = _Recorder()
    result = await batched_embed(list("abcd"), rec, batch_size=4)
    assert len(result) == 4
    assert rec.call_sizes == [4]


@pytest.mark.asyncio
async def test_batched_embed_empty():
    rec = _Recorder()
    result = await batched_embed([], rec, batch_size=4)
    assert result == []
    assert rec.call_sizes == []


@pytest.mark.asyncio
async def test_batched_embed_invalid_batch_size():
    rec = _Recorder()
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        await batched_embed(["a"], rec, batch_size=0)


# -- Integration test: MemSearch._embed_and_store with fake embedder --


class FakeEmbedder:
    """Fake embedding provider with configurable batch_size."""

    def __init__(self, *, batch_size: int = 4, dim: int = 4) -> None:
        self._batch_size = batch_size
        self._dim = dim
        self.call_sizes: list[int] = []
        self._next_embedding_index = 0

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from memsearch.embeddings.utils import batched_embed

        return await batched_embed(texts, self._embed_batch, self._batch_size)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.call_sizes.append(len(texts))
        start = self._next_embedding_index
        self._next_embedding_index += len(texts)
        return [[float(i)] + [0.0] * (self._dim - 1) for i in range(start, start + len(texts))]


@pytest.fixture
def mem_with_fake(tmp_path: Path):
    """MemSearch instance wired to a FakeEmbedder with batch_size=4."""
    fake = FakeEmbedder(batch_size=4, dim=4)
    ms = MemSearch.__new__(MemSearch)
    ms._paths = []
    ms._max_chunk_size = 1500
    ms._overlap_lines = 2
    ms._embedder = fake
    ms._store = MilvusStore(uri=str(tmp_path / "test.db"), dimension=fake.dimension)
    ms._edges = None
    ms._graph_similar_top_n = 0
    ms._graph_similar_threshold = 0.0
    yield ms, fake
    ms.close()


class RecordingStore:
    """Records upsert batch sizes without opening a real Milvus connection."""

    def __init__(self) -> None:
        self.call_sizes: list[int] = []
        self.records: list[dict[str, Any]] = []

    def upsert(self, records: list[dict[str, Any]]) -> int:
        self.call_sizes.append(len(records))
        self.records.extend(records)
        return len(records)

    def dense_search(self, vectors: list[list[float]], top_k: int = 10) -> list[list[dict[str, Any]]]:
        hits: list[list[dict[str, Any]]] = []
        for vector in vectors:
            own_index = int(vector[0])
            hit_list = [{"chunk_hash": self.records[own_index]["chunk_hash"], "score": 1.0}]
            if own_index == 2 and len(self.records) > 3:
                hit_list.append({"chunk_hash": self.records[3]["chunk_hash"], "score": 0.9})
            elif own_index == 3 and len(self.records) > 2:
                hit_list.append({"chunk_hash": self.records[2]["chunk_hash"], "score": 0.9})
            hits.append(hit_list[:top_k])
        return hits


class RecordingEdgeStore:
    """Records similarity edges without opening a real edge database."""

    def __init__(self) -> None:
        self.edges: list[tuple[str, str, str, float, str]] = []

    def add_edges(self, edges: list[tuple[str, str, str, float, str]]) -> None:
        self.edges.extend(edges)


@pytest.fixture
def mem_with_recording_store():
    """MemSearch instance wired to fake embedding and fake storage."""
    fake = FakeEmbedder(batch_size=4, dim=4)
    store = RecordingStore()
    ms = MemSearch.__new__(MemSearch)
    ms._paths = []
    ms._max_chunk_size = 1500
    ms._overlap_lines = 2
    ms._embedder = fake
    ms._store = store
    ms._edges = None
    ms._graph_similar_top_n = 0
    ms._graph_similar_threshold = 0.0
    return ms, fake, store


def _make_chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            content=f"chunk {i}",
            source="test.md",
            heading="",
            heading_level=0,
            start_line=i,
            end_line=i,
        )
        for i in range(n)
    ]


@_requires_milvus_lite
@pytest.mark.asyncio
async def test_embed_and_store_batching(mem_with_fake):
    ms, fake = mem_with_fake
    chunks = _make_chunks(10)  # 10 chunks, batch size 4
    n = await ms._embed_and_store(chunks)
    assert n == 10
    # Should have been split into 3 batches: 4 + 4 + 2
    assert fake.call_sizes == [4, 4, 2]


@pytest.mark.asyncio
async def test_embed_and_store_upserts_by_embedding_batch(mem_with_recording_store):
    ms, fake, store = mem_with_recording_store
    chunks = _make_chunks(10)  # 10 chunks, batch size 4
    n = await ms._embed_and_store(chunks)
    assert n == 10
    assert fake.call_sizes == [4, 4, 2]
    assert store.call_sizes == [4, 4, 2]
    assert len(store.records) == 10


@pytest.mark.asyncio
async def test_embed_and_store_preserves_cross_batch_edges(mem_with_recording_store):
    ms, fake, store = mem_with_recording_store
    fake._batch_size = 3
    edge_store = RecordingEdgeStore()
    ms._edges = edge_store
    ms._graph_similar_top_n = 1
    ms._graph_similar_threshold = 0.5

    chunks = _make_chunks(6)
    n = await ms._embed_and_store(chunks)

    assert n == 6
    assert store.call_sizes == [3, 3]
    assert {edge[:3] for edge in edge_store.edges} == {
        (store.records[2]["chunk_hash"], store.records[3]["chunk_hash"], "similar"),
        (store.records[3]["chunk_hash"], store.records[2]["chunk_hash"], "similar"),
    }


@_requires_milvus_lite
@pytest.mark.asyncio
async def test_embed_and_store_under_limit(mem_with_fake):
    ms, fake = mem_with_fake
    chunks = _make_chunks(3)
    n = await ms._embed_and_store(chunks)
    assert n == 3
    assert fake.call_sizes == [3]


@_requires_milvus_lite
@pytest.mark.asyncio
async def test_embed_and_store_empty(mem_with_fake):
    ms, fake = mem_with_fake
    n = await ms._embed_and_store([])
    assert n == 0
    assert fake.call_sizes == []


# -- Error isolation tests --


@_requires_milvus_lite
@pytest.mark.asyncio
async def test_index_continues_after_file_failure(tmp_path: Path):
    """A file that fails to index should not prevent other files from indexing."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "aaa_good.md").write_text("# Good\n\nThis file is fine.\n")
    (docs / "bbb_bad.md").write_text("# Bad\n\nThis file will fail.\n")
    (docs / "ccc_good.md").write_text("# Also Good\n\nThis file is fine too.\n")

    fake = FakeEmbedder(batch_size=100, dim=4)
    ms = MemSearch.__new__(MemSearch)
    ms._paths = [str(docs)]
    ms._max_chunk_size = 1500
    ms._overlap_lines = 2
    ms._embedder = fake
    ms._store = MilvusStore(uri=str(tmp_path / "test.db"), dimension=fake.dimension)

    # Patch _index_file to fail on the bad file
    original_index_file = ms._index_file

    async def _patched_index_file(f, *, force=False):
        if "bbb_bad" in str(f.path):
            raise RuntimeError("Simulated embedding API failure")
        return await original_index_file(f, force=force)

    ms._index_file = _patched_index_file

    n = await ms.index()
    ms.close()

    # Both good files should have been indexed despite the middle file failing
    assert n > 0
    assert len(fake.call_sizes) >= 2
