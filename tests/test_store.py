"""Tests for the Milvus store."""

from pathlib import Path
from typing import Any

import pytest

from memsearch.store import MilvusStore


class _FakeClient:
    """Records read-call kwargs without touching a real Milvus (runs on Windows)."""

    def __init__(self, *, row_count: int = 5, query_result: list[dict] | None = None) -> None:
        self._row_count = row_count
        self._query_result = query_result if query_result is not None else [{"chunk_hash": "h1"}]
        self.search_kwargs: dict[str, Any] | None = None
        self.hybrid_kwargs: dict[str, Any] | None = None
        self.query_calls: list[dict[str, Any]] = []

    def get_collection_stats(self, collection_name: str) -> dict[str, int]:
        return {"row_count": self._row_count}

    def query(self, **kwargs: Any) -> list[dict]:
        self.query_calls.append(kwargs)
        return self._query_result

    def search(self, **kwargs: Any) -> list:
        self.search_kwargs = kwargs
        return []

    def hybrid_search(self, **kwargs: Any) -> list:
        self.hybrid_kwargs = kwargs
        return [[]]


def _bare_store(*, is_lite: bool, consistency: str, client: _FakeClient | None = None) -> MilvusStore:
    s = object.__new__(MilvusStore)
    s._is_lite = is_lite
    s._consistency_level = consistency
    s._collection = "c"
    s._client = client or _FakeClient()
    return s


# --- consistency_level: remote read ops carry it; Lite never does ---


def test_consistency_kwargs_remote_strong():
    assert _bare_store(is_lite=False, consistency="Strong")._consistency_kwargs() == {"consistency_level": "Strong"}


def test_consistency_kwargs_lite_omits():
    # Milvus Lite is locally strong-consistent — never send the kwarg.
    assert _bare_store(is_lite=True, consistency="Strong")._consistency_kwargs() == {}


def test_consistency_kwargs_remote_unset_omits():
    assert _bare_store(is_lite=False, consistency="")._consistency_kwargs() == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("strong", "Strong"), ("STRONG", "Strong"), (" Bounded ", "Bounded"), ("eventually", "Eventually")],
)
def test_consistency_kwargs_normalizes_case(raw: str, expected: str):
    # pymilvus ConsistencyLevel.Value() is case-sensitive — any caller casing must
    # resolve to the capitalised enum name or the real call raises InvalidConsistencyLevel.
    assert _bare_store(is_lite=False, consistency=raw)._consistency_kwargs() == {"consistency_level": expected}


def test_consistency_kwargs_whitespace_only_omits():
    assert _bare_store(is_lite=False, consistency="   ")._consistency_kwargs() == {}


def test_search_passes_consistency_on_remote():
    s = _bare_store(is_lite=False, consistency="Strong")
    s.search([0.0, 0.0, 0.0, 0.0], query_text="x")
    assert s._client.hybrid_kwargs["consistency_level"] == "Strong"


def test_dense_search_passes_consistency_on_remote():
    s = _bare_store(is_lite=False, consistency="Strong")
    s.dense_search([[0.0, 0.0, 0.0, 0.0]])
    assert s._client.search_kwargs["consistency_level"] == "Strong"


def test_query_passes_consistency_on_remote():
    s = _bare_store(is_lite=False, consistency="Strong")
    s.query()
    assert s._client.query_calls[0]["consistency_level"] == "Strong"


def test_dense_search_lite_omits_consistency():
    s = _bare_store(is_lite=True, consistency="Strong")
    s.dense_search([[0.0, 0.0, 0.0, 0.0]])
    assert "consistency_level" not in s._client.search_kwargs


# --- cold-start guard: sealed-segment row_count=0 must not drop unsealed data ---


def test_nonempty_coldstart_probes_when_strong_remote():
    # row_count=0 (no sealed segments) but a consistency-honouring point query finds a row.
    client = _FakeClient(row_count=0, query_result=[{"chunk_hash": "h1"}])
    s = _bare_store(is_lite=False, consistency="Strong", client=client)
    assert s._nonempty() is True
    assert client.query_calls and client.query_calls[0]["consistency_level"] == "Strong"


def test_nonempty_coldstart_probe_empty_returns_false():
    client = _FakeClient(row_count=0, query_result=[])
    s = _bare_store(is_lite=False, consistency="Strong", client=client)
    assert s._nonempty() is False


def test_nonempty_no_override_uses_stats_only():
    # Without a consistency override, behaviour is unchanged: trust the stats count,
    # never issue a probe (preserves the cheap path for Lite / default-consistency).
    client = _FakeClient(row_count=0)
    s = _bare_store(is_lite=False, consistency="", client=client)
    assert s._nonempty() is False
    assert client.query_calls == []


def test_nonempty_lite_uses_stats_only():
    client = _FakeClient(row_count=0)
    s = _bare_store(is_lite=True, consistency="Strong", client=client)
    assert s._nonempty() is False
    assert client.query_calls == []


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "test_milvus.db"
    s = MilvusStore(uri=str(db), dimension=4)
    yield s
    s.close()


def test_upsert_and_search(store: MilvusStore):
    chunks = [
        {
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "content": "Hello world",
            "source": "test.md",
            "heading": "Intro",
            "chunk_hash": "h1",
            "heading_level": 1,
            "start_line": 1,
            "end_line": 5,
        },
        {
            "embedding": [0.0, 1.0, 0.0, 0.0],
            "content": "Goodbye world",
            "source": "test.md",
            "heading": "Outro",
            "chunk_hash": "h2",
            "heading_level": 1,
            "start_line": 6,
            "end_line": 10,
        },
    ]
    n = store.upsert(chunks)
    assert n == 2

    results = store.search([1.0, 0.0, 0.0, 0.0], top_k=1)
    assert len(results) >= 1
    assert results[0]["content"] == "Hello world"


def test_delete_by_source(store: MilvusStore):
    chunks = [
        {
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "content": "A",
            "source": "a.md",
            "heading": "",
            "chunk_hash": "ha",
            "heading_level": 0,
            "start_line": 1,
            "end_line": 1,
        },
        {
            "embedding": [0.0, 1.0, 0.0, 0.0],
            "content": "B",
            "source": "b.md",
            "heading": "",
            "chunk_hash": "hb",
            "heading_level": 0,
            "start_line": 1,
            "end_line": 1,
        },
    ]
    store.upsert(chunks)
    store.delete_by_source("a.md")
    results = store.search([1.0, 0.0, 0.0, 0.0], top_k=10)
    sources = {r["source"] for r in results}
    assert "a.md" not in sources


def test_upsert_is_idempotent(store: MilvusStore):
    chunk = {
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "content": "Same content",
        "source": "test.md",
        "heading": "",
        "chunk_hash": "same_hash",
        "heading_level": 0,
        "start_line": 1,
        "end_line": 1,
        "doc_type": "markdown",
    }
    store.upsert([chunk])
    store.upsert([chunk])
    results = store.search([1.0, 0.0, 0.0, 0.0], top_k=10)
    hashes = [r["chunk_hash"] for r in results]
    assert hashes.count("same_hash") == 1


def test_hybrid_search(store: MilvusStore):
    chunks = [
        {
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "content": "Redis caching with TTL and LRU eviction policy",
            "source": "test.md",
            "heading": "Caching",
            "chunk_hash": "h_redis",
            "heading_level": 1,
            "start_line": 1,
            "end_line": 5,
        },
        {
            "embedding": [0.0, 1.0, 0.0, 0.0],
            "content": "PostgreSQL database migration and schema changes",
            "source": "test.md",
            "heading": "Database",
            "chunk_hash": "h_pg",
            "heading_level": 1,
            "start_line": 6,
            "end_line": 10,
        },
    ]
    store.upsert(chunks)

    # Hybrid search: BM25 should boost the Redis result for keyword "Redis"
    results = store.search(
        [0.5, 0.5, 0.0, 0.0],  # ambiguous dense vector
        query_text="Redis caching",
        top_k=2,
    )
    assert len(results) >= 1
    assert results[0]["content"].startswith("Redis")


def test_dimension_mismatch(tmp_path: Path):
    db = str(tmp_path / "dim_test.db")
    # Create collection with dim=4
    s1 = MilvusStore(uri=db, dimension=4)
    s1.close()
    # Re-open with dim=8 — should raise ValueError
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        MilvusStore(uri=db, dimension=8)


def test_reopened_collection_is_loaded_for_query(tmp_path: Path):
    db = str(tmp_path / "reopen_test.db")
    chunk = {
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "content": "Reopened collection",
        "source": "test.md",
        "heading": "",
        "chunk_hash": "reopen_hash",
        "heading_level": 0,
        "start_line": 1,
        "end_line": 1,
    }

    s1 = MilvusStore(uri=db, dimension=4)
    s1.upsert([chunk])
    s1.close()

    s2 = MilvusStore(uri=db, dimension=4)
    try:
        results = s2.query()
    finally:
        s2.close()

    assert [r["chunk_hash"] for r in results] == ["reopen_hash"]


def test_drop(store: MilvusStore):
    chunk = {
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "content": "Will be dropped",
        "source": "test.md",
        "heading": "",
        "chunk_hash": "hd",
        "heading_level": 0,
        "start_line": 1,
        "end_line": 1,
        "doc_type": "markdown",
    }
    store.upsert([chunk])
    store.drop()
    # After drop, collection is gone — re-ensure should work
    store._ensure_collection()
    results = store.search([1.0, 0.0, 0.0, 0.0], top_k=10)
    assert len(results) == 0


def test_collection_description(tmp_path: Path):
    """Collection should store the description when provided."""
    db = str(tmp_path / "desc_test.db")
    desc = "myproject | openai/text-embedding-3-small"
    s = MilvusStore(uri=db, dimension=4, description=desc)
    info = s._client.describe_collection(s._collection)
    assert info.get("description") == desc
    s.close()


def test_collection_description_empty_by_default(tmp_path: Path):
    """Collection should have empty description when not provided."""
    db = str(tmp_path / "desc_default_test.db")
    s = MilvusStore(uri=db, dimension=4)
    info = s._client.describe_collection(s._collection)
    assert info.get("description") == ""
    s.close()
