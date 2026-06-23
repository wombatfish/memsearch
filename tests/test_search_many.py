"""Unit tests for MemSearch.search_many (multi-query union + grouped rerank).

These bypass the heavy MemSearch constructor (which loads the embedder + connects
Milvus) via __new__ and inject fakes, so they run on every platform without a
Milvus server, API key, or model download.
"""

from __future__ import annotations

from typing import Any

from memsearch.core import MemSearch


class _FakeEmbedder:
    model_name = "fake"
    dimension = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 0.0, 0.0] for t in texts]


class _FakeStore:
    """Returns a canned hit list keyed by the query_text passed to search()."""

    def __init__(self, by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._by_query = by_query

    def search(self, emb, *, query_text="", top_k=10, filter_expr=""):
        return [dict(r) for r in self._by_query.get(query_text, [])][:top_k]

    def count(self) -> int:
        return 100


def _make_mem(
    store: _FakeStore,
    *,
    reranker_model: str = "",
    recency_weight: float = 0.0,
    max_per_source: int = 0,
    fetch_multiplier: int = 3,
) -> MemSearch:
    m = MemSearch.__new__(MemSearch)
    m._embedder = _FakeEmbedder()
    m._store = store
    m._edges = None
    m._graph_enabled = False
    m._edges_checked = False
    m._reranker_model = reranker_model
    m._recency_weight = recency_weight
    m._recency_half_life_days = 30.0
    m._max_per_source = max_per_source
    m._fetch_multiplier = fetch_multiplier
    return m


async def test_search_many_unions_and_keeps_max_score() -> None:
    store = _FakeStore(
        {
            "qa": [
                {"chunk_hash": "h1", "source": "a", "content": "x", "score": 0.5},
                {"chunk_hash": "h2", "source": "b", "content": "y", "score": 0.4},
            ],
            "qb": [
                {"chunk_hash": "h1", "source": "a", "content": "x", "score": 0.9},  # higher for h1
                {"chunk_hash": "h3", "source": "c", "content": "z", "score": 0.3},
            ],
        }
    )
    mem = _make_mem(store)
    out = await mem.search_many(["qa", "qb"], top_k=10)

    assert {r["chunk_hash"]: r["score"] for r in out} == {"h1": 0.9, "h2": 0.4, "h3": 0.3}
    assert [r["chunk_hash"] for r in out] == ["h1", "h2", "h3"]  # sorted desc


async def test_search_many_applies_per_source_cap() -> None:
    store = _FakeStore(
        {
            "qa": [
                {"chunk_hash": "h1", "source": "a", "content": "", "score": 0.9},
                {"chunk_hash": "h2", "source": "a", "content": "", "score": 0.8},
                {"chunk_hash": "h3", "source": "a", "content": "", "score": 0.7},
            ],
            "qb": [{"chunk_hash": "h4", "source": "b", "content": "", "score": 0.6}],
        }
    )
    mem = _make_mem(store, max_per_source=2)
    out = await mem.search_many(["qa", "qb"], top_k=10)

    hashes = [r["chunk_hash"] for r in out]
    # First 2 of source "a" kept; the 3rd (h3) demoted below h4; never dropped.
    assert hashes == ["h1", "h2", "h4", "h3"]


async def test_search_many_reranks_against_best_query(monkeypatch) -> None:
    from memsearch import reranker as RR

    calls: list[tuple[str, list[str]]] = []

    def fake_rerank(query, results, *, model_name, top_k=0):
        calls.append((query, [r["chunk_hash"] for r in results]))
        return [{**r, "score": 1.0 / (i + 1)} for i, r in enumerate(results)]

    monkeypatch.setattr(RR, "rerank", fake_rerank)

    store = _FakeStore(
        {
            "qa": [{"chunk_hash": "h1", "source": "a", "content": "", "score": 0.5}],
            "qb": [
                {"chunk_hash": "h1", "source": "a", "content": "", "score": 0.9},  # h1 best from qb
                {"chunk_hash": "h2", "source": "b", "content": "", "score": 0.8},
            ],
        }
    )
    mem = _make_mem(store, reranker_model="m")
    out = await mem.search_many(["qa", "qb"], top_k=10)

    qmap = dict(calls)
    assert set(qmap["qb"]) == {"h1", "h2"}  # h1 grouped under its best variant (qb), with h2
    assert "qa" not in qmap  # qa surfaced nothing it owned the best score for

    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)  # merged groups globally re-sorted


async def test_search_many_single_query_delegates_to_search() -> None:
    store = _FakeStore({"only": [{"chunk_hash": "h1", "source": "a", "content": "", "score": 0.5}]})
    mem = _make_mem(store)
    out = await mem.search_many(["only"], top_k=5)
    assert [r["chunk_hash"] for r in out] == ["h1"]


async def test_search_many_empty_queries_returns_empty() -> None:
    mem = _make_mem(_FakeStore({}))
    assert await mem.search_many(["", "   "], top_k=5) == []
