"""Milvus-free unit tests for time-aware re-scoring (A2) and the per-source cap (A4).

None of these touch Milvus, so they run on any host (including Windows, where
milvus-lite has no wheels). The pipeline-order test constructs a MemSearch via
``__new__`` + fakes (the same pattern as test_graph.py) so ``__init__`` — which
would build a MilvusStore and raise on Windows — is never invoked.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from memsearch.core import (
    MemSearch,
    _apply_recency,
    _cap_per_source,
    _recency_factor,
    _source_date,
)

# ----------------------------------------------------------------------
# _source_date — parse the canonical date from a daily-log source path
# ----------------------------------------------------------------------


def test_source_date_posix_path():
    assert _source_date("/home/u/.memsearch/memory/2026-06-11.md") == date(2026, 6, 11)


def test_source_date_windows_backslash_path():
    assert _source_date(r"D:\Projects\notes\memory\2026-06-11.md") == date(2026, 6, 11)


def test_source_date_bare_basename():
    assert _source_date("2026-01-02.md") == date(2026, 1, 2)


def test_source_date_undated_returns_none():
    assert _source_date("/x/PROJECT.md") is None
    assert _source_date(r"C:\x\USER.md") is None
    assert _source_date("") is None


def test_source_date_embedded_date_not_a_daily_log():
    # A date embedded in a longer basename is NOT a canonical daily log → None
    # (fullmatch on the basename, not a loose search).
    assert _source_date("/x/project-2026-06-11.md") is None
    assert _source_date("/x/2026-06-11-notes.md") is None
    # A date-named PARENT dir but non-.md / non-date basename → None.
    assert _source_date("/logs/2026-06-11/index.md") is None


def test_source_date_impossible_date_returns_none():
    assert _source_date("/x/2026-13-99.md") is None
    assert _source_date("/x/2026-02-30.md") is None


# ----------------------------------------------------------------------
# _recency_factor — exponential decay in (0, 1]
# ----------------------------------------------------------------------


def test_recency_factor_today_is_one():
    t = date(2026, 6, 11)
    assert _recency_factor(t, 30.0, t) == pytest.approx(1.0)


def test_recency_factor_one_half_life_is_half():
    t = date(2026, 6, 11)
    d = t - timedelta(days=30)
    assert _recency_factor(d, 30.0, t) == pytest.approx(0.5)


def test_recency_factor_two_half_lives_is_quarter():
    t = date(2026, 6, 11)
    d = t - timedelta(days=60)
    assert _recency_factor(d, 30.0, t) == pytest.approx(0.25)


def test_recency_factor_undated_is_one():
    assert _recency_factor(None, 30.0, date(2026, 6, 11)) == 1.0


def test_recency_factor_future_date_clamped_to_one():
    # Clock skew / timezone: a future-dated file must NOT be boosted above 1.0.
    t = date(2026, 6, 11)
    assert _recency_factor(t + timedelta(days=10), 30.0, t) == pytest.approx(1.0)


def test_recency_factor_nonpositive_half_life_disables_decay():
    t = date(2026, 6, 11)
    d = t - timedelta(days=365)
    assert _recency_factor(d, 0.0, t) == 1.0
    assert _recency_factor(d, -5.0, t) == 1.0


# ----------------------------------------------------------------------
# _apply_recency — multiplicative damping + stable desc re-sort
# ----------------------------------------------------------------------


def _row(h, score, source=""):
    return {"chunk_hash": h, "score": score, "source": source}


def test_apply_recency_weight_zero_is_identity_for_sorted_input():
    # Already-desc input + weight 0 → scores unchanged, order preserved.
    rows = [_row("a", 0.9), _row("b", 0.7), _row("c", 0.5)]
    out = _apply_recency(rows, weight=0.0, half_life_days=30.0, today=date(2026, 6, 11))
    assert [r["chunk_hash"] for r in out] == ["a", "b", "c"]
    assert [r["score"] for r in out] == [0.9, 0.7, 0.5]


def test_apply_recency_keeps_scores_in_unit_interval():
    t = date(2026, 6, 11)
    rows = [
        _row("recent", 1.0, f"/m/{t.isoformat()}.md"),
        _row("stale", 1.0, f"/m/{(t - timedelta(days=365)).isoformat()}.md"),
        _row("evergreen", 1.0, "/m/PROJECT.md"),
    ]
    out = _apply_recency(rows, weight=0.3, half_life_days=30.0, today=t)
    for r in out:
        assert 0.0 <= r["score"] <= 1.0


def test_apply_recency_negative_input_score_clamped_no_inverted_penalty():
    # A torch cross-encoder can emit a negative logit; damping it must not boost it.
    rows = [_row("neg", -2.0, "/m/PROJECT.md")]
    out = _apply_recency(rows, weight=0.5, half_life_days=30.0, today=date(2026, 6, 11))
    assert out[0]["score"] == 0.0


def test_apply_recency_recent_breaks_tie_over_stale():
    t = date(2026, 6, 11)
    rows = [
        _row("stale", 0.80, f"/m/{(t - timedelta(days=60)).isoformat()}.md"),
        _row("recent", 0.80, f"/m/{t.isoformat()}.md"),
    ]
    out = _apply_recency(rows, weight=0.5, half_life_days=30.0, today=t)
    # Equal relevance → the recent chunk wins after damping.
    assert out[0]["chunk_hash"] == "recent"


def test_apply_recency_empty_is_passthrough():
    assert _apply_recency([], weight=0.3, half_life_days=30.0) == []


# ----------------------------------------------------------------------
# _cap_per_source — per-source diversity, overflow appended (never dropped)
# ----------------------------------------------------------------------


def test_cap_per_source_caps_and_appends_overflow():
    rows = [_row(f"h{i}", 1.0 - i / 10, "/m/day.md") for i in range(5)]
    out = _cap_per_source(rows, 2)
    # Order preserved overall; first two kept in place, the other three appended
    # AFTER (still present — a short pool must still fill top_k).
    assert [r["chunk_hash"] for r in out] == ["h0", "h1", "h2", "h3", "h4"]
    assert len(out) == 5


def test_cap_per_source_interleaves_distinct_sources():
    rows = [
        _row("a1", 0.9, "/m/A.md"),
        _row("a2", 0.8, "/m/A.md"),
        _row("a3", 0.7, "/m/A.md"),
        _row("b1", 0.6, "/m/B.md"),
    ]
    out = _cap_per_source(rows, 2)
    # A.md capped at 2 (a1,a2 kept; a3 to overflow); b1 kept. Overflow last.
    assert [r["chunk_hash"] for r in out] == ["a1", "a2", "b1", "a3"]


def test_cap_per_source_zero_is_identity_order():
    rows = [_row("a", 0.9, "/m/A.md"), _row("b", 0.8, "/m/B.md")]
    out = _cap_per_source(rows, 0)
    assert [r["chunk_hash"] for r in out] == ["a", "b"]


# ----------------------------------------------------------------------
# Pipeline order-of-operations (MemSearch.__new__ + fakes, no Milvus)
# ----------------------------------------------------------------------


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.0, 0.0, 0.0] for _ in texts]


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.received_top_k = None

    def search(self, embedding, *, query_text="", top_k=10, filter_expr=""):
        self.received_top_k = top_k
        return [dict(r) for r in self._rows[:top_k]]


def _make_search_mem(store, *, reranker="", recency_weight=0.0, max_per_source=0, fetch_multiplier=3):
    m = MemSearch.__new__(MemSearch)
    m._embedder = _FakeEmbedder()
    m._store = store
    m._reranker_model = reranker
    m._graph_enabled = False
    m._edges = None
    m._edges_checked = False
    m._recency_weight = recency_weight
    m._recency_half_life_days = 30.0
    m._max_per_source = max_per_source
    m._fetch_multiplier = fetch_multiplier
    return m


def test_pipeline_rerank_gets_all_fetch_k_then_recency_reorders(monkeypatch):
    """rerank must receive ALL fetch_k candidates (top_k=0 keep-all), and recency
    must re-score the rerank OUTPUT (a recent chunk promoted above a stale one the
    cross-encoder ranked first)."""
    t = date.today()
    stale_src = f"/m/{(t - timedelta(days=60)).isoformat()}.md"
    recent_src = f"/m/{t.isoformat()}.md"
    # 6 distinct sources so the per-source cap (disabled here anyway) is irrelevant.
    rows = [
        {"chunk_hash": "A", "score": 0.0, "source": stale_src, "content": "a", "heading": ""},
        {"chunk_hash": "B", "score": 0.0, "source": recent_src, "content": "b", "heading": ""},
        {"chunk_hash": "C", "score": 0.0, "source": "/m/C.md", "content": "c", "heading": ""},
        {"chunk_hash": "D", "score": 0.0, "source": "/m/D.md", "content": "d", "heading": ""},
        {"chunk_hash": "E", "score": 0.0, "source": "/m/E.md", "content": "e", "heading": ""},
        {"chunk_hash": "F", "score": 0.0, "source": "/m/F.md", "content": "f", "heading": ""},
    ]
    store = _FakeStore(rows)
    captured = {}

    def fake_rerank(query, results, *, model_name="", top_k=0):
        captured["n"] = len(results)
        captured["top_k"] = top_k
        # Cross-encoder ranks A first (highest), descending by incoming order.
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
        out = [{**r, "score": s} for r, s in zip(results, scores, strict=False)]
        out.sort(key=lambda r: r["score"], reverse=True)
        return out

    monkeypatch.setattr("memsearch.reranker.rerank", fake_rerank)

    m = _make_search_mem(store, reranker="x", recency_weight=0.5, max_per_source=0, fetch_multiplier=3)
    out = asyncio.run(m.search("q", top_k=2))

    assert store.received_top_k == 6  # top_k(2) * fetch_multiplier(3)
    assert captured["n"] == 6  # rerank saw every fetched candidate
    assert captured["top_k"] == 0  # keep-all, so recency can promote from below the cut
    assert len(out) == 2
    # 'A' was the cross-encoder's #1 but is 60 days stale; 'B' is today → B promoted.
    assert out[0]["chunk_hash"] == "B"


def test_pipeline_optout_is_passthrough_top_k():
    """reranker off + recency 0 + cap 0 → fetch_k == top_k, store rows returned as-is."""
    rows = [
        {"chunk_hash": "a", "score": 0.9, "source": "/m/x.md", "content": "a", "heading": ""},
        {"chunk_hash": "b", "score": 0.8, "source": "/m/x.md", "content": "b", "heading": ""},
        {"chunk_hash": "c", "score": 0.7, "source": "/m/x.md", "content": "c", "heading": ""},
    ]
    store = _FakeStore(rows)
    m = _make_search_mem(store, reranker="", recency_weight=0.0, max_per_source=0)
    out = asyncio.run(m.search("q", top_k=2))
    assert store.received_top_k == 2  # no over-fetch when every post-stage is disabled
    assert [r["chunk_hash"] for r in out] == ["a", "b"]
