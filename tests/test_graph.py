"""Milvus-free unit tests for graph retrieval logic.

These cover the corrected, content-based section-grouping algorithm in
``_structural_edges`` / ``_is_section_head`` and the RRF fusion math in
``MemSearch._graph_expand``.  Neither path touches Milvus, so they run on
any host (including Windows, where milvus-lite has no wheels).
"""

from __future__ import annotations

import logging
from typing import Any

from memsearch.core import MemSearch, _is_section_head, _structural_edges


def _same_section(edges: list[tuple[str, str, str, float, str]]) -> list[tuple[str, str]]:
    return [(s, d) for s, d, rel, _w, _m in edges if rel == "same_section"]


def _sibling(edges: list[tuple[str, str, str, float, str]]) -> list[tuple[str, str]]:
    return [(s, d) for s, d, rel, _w, _m in edges if rel == "sibling"]


# ----------------------------------------------------------------------
# A) Pure _structural_edges / _is_section_head invariants
# ----------------------------------------------------------------------


def test_adjacent_equal_level_sections_are_not_same_section():
    """Two adjacent equal-level single-chunk sections (## A then ## B):
    sibling edge yes, same_section edge no — they are distinct cliques."""
    items = [("a", "## A\nbody a"), ("b", "## B\nbody b")]
    edges = _structural_edges(items, "model")
    assert ("a", "b") in _sibling(edges)
    assert ("a", "b") not in _same_section(edges)
    assert ("b", "a") not in _same_section(edges)


def test_split_large_section_is_one_run():
    """A split large section — only the first sub-chunk carries the heading;
    continuation chunks do not — stays in one windowed run."""
    items = [("c1", "## Big\npart one"), ("c2", "part two"), ("c3", "part three")]
    ss = set(_same_section(_structural_edges(items, "model")))
    assert ("c1", "c2") in ss
    assert ("c1", "c3") in ss
    assert ("c2", "c3") in ss


def test_repeated_identical_heading_starts_a_new_run():
    """Repeated identical heading text yields distinct runs, so the two
    chunks are NOT linked by same_section.  This assertion FAILS under the
    buggy heading-level-delta sketch and PASSES under the content-based fix."""
    items = [("f1", "### Fixed\nx"), ("f2", "### Fixed\ny")]
    ss = _same_section(_structural_edges(items, "model"))
    assert ("f1", "f2") not in ss
    assert ("f2", "f1") not in ss


def test_window_cap_limits_same_section_edges():
    """A single run of 6 continuation chunks with window=2: each chunk only
    links forward to the next 2 — no O(n^2) blowup.  Expected count = 9."""
    items = [("p0", "## Sec\np0"), ("p1", "p1"), ("p2", "p2"), ("p3", "p3"), ("p4", "p4"), ("p5", "p5")]
    ss = _same_section(_structural_edges(items, "model", window=2))
    assert ss == [
        ("p0", "p1"),
        ("p0", "p2"),
        ("p1", "p2"),
        ("p1", "p3"),
        ("p2", "p3"),
        ("p2", "p4"),
        ("p3", "p4"),
        ("p3", "p5"),
        ("p4", "p5"),
    ]
    assert len(ss) == 9


def test_is_section_head():
    assert _is_section_head("## Heading\nbody") is True
    assert _is_section_head("###### h") is True
    assert _is_section_head("\n\n## H") is True  # leading blank lines
    assert _is_section_head("plain body") is False
    assert _is_section_head("no heading here") is False


# ----------------------------------------------------------------------
# B) _graph_expand fusion math (no Milvus — bypass __init__)
# ----------------------------------------------------------------------


class _FakeEdges:
    """Minimal EdgeStore stub: returns canned neighbor tuples."""

    def __init__(self, neighbors: list[tuple[str, float, str]]) -> None:
        self._neighbors = neighbors

    def neighbors(self, seeds: list[str], *, limit_per_node: int = 5) -> list[tuple[str, float, str]]:
        # Only return neighbors whose seed was actually queried.
        return [(n, w, s) for (n, w, s) in self._neighbors if s in seeds]


class _FakeStore:
    """Minimal MilvusStore stub: query() returns canned rows regardless of expr."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def query(self, *, filter_expr: str = "") -> list[dict[str, Any]]:
        return list(self._rows)


def _make_mem(edges: _FakeEdges, store: _FakeStore, *, weight: float = 0.5) -> MemSearch:
    """Construct a MemSearch without invoking __init__ (which would build a
    MilvusStore and raise on Windows).  Only the attrs _graph_expand reads."""
    m = MemSearch.__new__(MemSearch)
    m._graph_weight = weight
    m._graph_seed_k = 10
    m._graph_fanout = 5
    m._edges = edges
    m._store = store
    return m


def test_graph_expand_empty_neighbors_returns_base_unchanged():
    base = [
        {"chunk_hash": "a", "score": 0.9, "content": "A"},
        {"chunk_hash": "b", "score": 0.8, "content": "B"},
    ]
    m = _make_mem(_FakeEdges([]), _FakeStore([]))
    out = m._graph_expand(base, top_k=10, filter_expr="")
    assert out is base  # untouched identity


def test_graph_expand_pulls_in_non_base_neighbor():
    base = [
        {"chunk_hash": "a", "score": 0.9, "content": "A"},
        {"chunk_hash": "b", "score": 0.8, "content": "B"},
    ]
    # "x" is NOT in base; it is a strong neighbor of seed "a".
    edges = _FakeEdges([("x", 0.95, "a")])
    store = _FakeStore([{"chunk_hash": "x", "content": "X", "source": "s", "heading": "h"}])
    m = _make_mem(edges, store)
    out = m._graph_expand(base, top_k=10, filter_expr="")
    out_hashes = {r["chunk_hash"] for r in out}
    assert "x" in out_hashes
    # the pulled-in row carries through its fetched fields
    x_row = next(r for r in out if r["chunk_hash"] == "x")
    assert x_row["content"] == "X"


def test_graph_expand_doc_strong_in_both_outranks_base_only():
    # docB is base rank 1 (base-only); docA is base rank 2 but ALSO a strong
    # graph neighbor (via seed docB).  base_rank is positional, so without the
    # graph term docB would win.  The graph contribution must flip docA above
    # docB — this catches a regression in the graph-rank branch of the fusion.
    base = [
        {"chunk_hash": "docB", "score": 0.8, "content": "B"},  # base rank 1, base-only
        {"chunk_hash": "docA", "score": 0.7, "content": "A"},  # base rank 2, strong in graph too
    ]
    edges = _FakeEdges([("docA", 0.95, "docB")])  # graph_score[docA] = 0.8 * 0.95
    m = _make_mem(edges, _FakeStore([]))
    out = m._graph_expand(base, top_k=10, filter_expr="")
    assert out[0]["chunk_hash"] == "docA"  # graph term flips it above base rank 1
    assert out[1]["chunk_hash"] == "docB"


def test_graph_expand_scores_normalized_to_unit_range():
    base = [
        {"chunk_hash": "a", "score": 0.9, "content": "A"},
        {"chunk_hash": "b", "score": 0.8, "content": "B"},
    ]
    edges = _FakeEdges([("a", 0.95, "b"), ("x", 0.7, "a")])
    store = _FakeStore([{"chunk_hash": "x", "content": "X"}])
    m = _make_mem(edges, store)
    out = m._graph_expand(base, top_k=10, filter_expr="")
    for r in out:
        assert r["score"] >= 0.0
        assert r["score"] <= 1.0 + 1e-9


# ----------------------------------------------------------------------
# C) Integration of rebuild_edges() and search()'s graph hook — still
#    Milvus-free (store + embedder are stubbed), so they run on Windows.
# ----------------------------------------------------------------------


class _FakeEmbedder:
    """Embedder stub that records every embed() call (rebuild must make none)."""

    model_name = "fake-model"

    def __init__(self) -> None:
        self.embed_calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        return [[0.0, 0.0] for _ in texts]


class _RecordingEdges:
    """EdgeStore stub capturing clear()/add_edges() for rebuild assertions."""

    def __init__(self) -> None:
        self.cleared = 0
        self.added: list[tuple[str, str, str, float, str]] = []

    def clear(self) -> None:
        self.cleared += 1

    def add_edges(self, edges: list[tuple[str, str, str, float, str]]) -> None:
        self.added.extend(edges)

    def is_empty(self) -> bool:
        return not self.added


class _RebuildStore:
    """Store stub: iter_chunks() yields canned rows; dense_search() links each
    vector to the next chunk (by object identity, matching rebuild's vec order)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._vec_to_idx = {id(r["embedding"]): i for i, r in enumerate(rows)}

    def iter_chunks(self, *, with_embeddings: bool = False):
        return iter(self._rows)

    def dense_search(self, vecs: list[list[float]], *, top_k: int = 6) -> list[list[dict[str, Any]]]:
        n = len(self._rows)
        out: list[list[dict[str, Any]]] = []
        for v in vecs:
            i = self._vec_to_idx[id(v)]
            own = self._rows[i]["chunk_hash"]
            nxt = self._rows[(i + 1) % n]["chunk_hash"]
            # self-hit (score 1.0) is filtered by `nbr != own_id`; nxt at 0.8 survives.
            out.append([{"chunk_hash": own, "score": 1.0}, {"chunk_hash": nxt, "score": 0.8}])
        return out


def _make_rebuild_mem(store: _RebuildStore, edges: _RecordingEdges, embedder: _FakeEmbedder) -> MemSearch:
    m = MemSearch.__new__(MemSearch)
    m._edges = edges
    m._store = store
    m._embedder = embedder
    m._graph_structural = True
    m._graph_similar_top_n = 2
    m._graph_similar_threshold = 0.5
    return m


async def test_rebuild_edges_makes_no_embedding_calls():
    """rebuild_edges must reconstruct edges from STORED vectors only — never
    call the embedder (the whole point of the backfill command: no re-embed)."""
    rows = [
        {"chunk_hash": "c1", "source": "a.md", "content": "## A\nbody", "start_line": 1, "embedding": [0.0]},
        {"chunk_hash": "c2", "source": "a.md", "content": "continuation", "start_line": 5, "embedding": [1.0]},
        {"chunk_hash": "c3", "source": "b.md", "content": "## B\nbody", "start_line": 1, "embedding": [2.0]},
    ]
    store = _RebuildStore(rows)
    edges = _RecordingEdges()
    embedder = _FakeEmbedder()
    m = _make_rebuild_mem(store, edges, embedder)

    count = await m.rebuild_edges()

    assert embedder.embed_calls == 0  # the key guarantee: no re-embedding
    assert edges.cleared == 1  # full truncate before rebuild
    rels = {rel for _s, _d, rel, _w, _mdl in edges.added}
    assert "sibling" in rels  # c1-c2 (same source, file order)
    assert "same_section" in rels  # c1-c2 (c1 head + c2 continuation, one run)
    assert "similar" in rels  # kNN from stored vectors
    assert count == len(edges.added)


def test_rebuild_edges_noop_when_graph_disabled():
    m = MemSearch.__new__(MemSearch)
    m._edges = None
    # rebuild_edges short-circuits on _edges is None without touching anything else.

    import asyncio

    assert asyncio.run(m.rebuild_edges()) == 0


class _SearchStore:
    def __init__(self, base: list[dict[str, Any]], *, count: int = 5) -> None:
        self._base = base
        self._count = count
        self.search_calls = 0

    def search(self, emb, *, query_text: str = "", top_k: int = 10, filter_expr: str = "") -> list[dict[str, Any]]:
        self.search_calls += 1
        return list(self._base)

    def count(self) -> int:
        return self._count

    def query(self, *, filter_expr: str = "") -> list[dict[str, Any]]:
        return []


class _EmptyEdges:
    def is_empty(self) -> bool:
        return True

    def neighbors(self, seeds: list[str], *, limit_per_node: int = 5) -> list[tuple[str, float, str]]:
        return []


def _make_search_mem(store, edges, *, graph_enabled: bool) -> MemSearch:
    m = MemSearch.__new__(MemSearch)
    m._embedder = _FakeEmbedder()
    m._reranker_model = ""
    m._store = store
    m._graph_enabled = graph_enabled
    m._edges = edges
    m._empty_edges_warned = False
    m._graph_weight = 0.5
    m._graph_seed_k = 10
    m._graph_fanout = 5
    return m


async def test_search_graph_disabled_returns_base_unchanged():
    base = [{"chunk_hash": "a", "score": 0.9, "content": "A"}, {"chunk_hash": "b", "score": 0.8, "content": "B"}]
    m = _make_search_mem(_SearchStore(base), _EmptyEdges(), graph_enabled=False)
    out = await m.search("q", top_k=10)
    assert [r["chunk_hash"] for r in out] == ["a", "b"]


async def test_search_warns_once_when_graph_on_but_edges_empty(caplog):
    base = [{"chunk_hash": "a", "score": 0.9, "content": "A"}]
    m = _make_search_mem(_SearchStore(base, count=5), _EmptyEdges(), graph_enabled=True)
    with caplog.at_level(logging.WARNING, logger="memsearch.core"):
        await m.search("q", top_k=10)
        first = caplog.text.count("graph rebuild")
        await m.search("q", top_k=10)
        second = caplog.text.count("graph rebuild")
    assert first == 1  # warned on the first empty-edges search
    assert second == 1  # one-time: not repeated on the second search
    assert m._empty_edges_warned is True
