"""Milvus-free unit tests for graph retrieval logic.

These cover the corrected, content-based section-grouping algorithm in
``_structural_edges`` / ``_is_section_head`` and the RRF fusion math in
``MemSearch._graph_expand``.  Neither path touches Milvus, so they run on
any host (including Windows, where milvus-lite has no wheels).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from memsearch.core import MemSearch, _is_section_head, _structural_edges
from memsearch.edges import EdgeStore
from memsearch.scanner import ScannedFile


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
    """EdgeStore stub capturing replace_all()/clear()/add_edges() for rebuild assertions."""

    def __init__(self) -> None:
        self.cleared = 0
        self.added: list[tuple[str, str, str, float, str]] = []

    def replace_all(self, owned: list[str], edges: list[tuple[str, str, str, float, str]]) -> None:
        # rebuild_edges now does an atomic, collection-scoped swap; mirror it.
        self.cleared += 1
        self.owned = owned
        self.added.extend(edges)

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

    def is_empty_for(self, hashes: list[str]) -> bool:
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
    m._edges_checked = False
    m._graph_weight = 0.5
    m._graph_seed_k = 10
    m._graph_fanout = 5
    # Post-search stages (A2 recency / A4 cap) disabled: these fixtures exercise
    # the graph/search plumbing, not re-scoring (covered in test_recency.py).
    m._recency_weight = 0.0
    m._recency_half_life_days = 30.0
    m._max_per_source = 0
    m._fetch_multiplier = 3
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
    assert second == 1  # one-time: latched after the first check, not re-run
    assert m._edges_checked is True


# --- EdgeStore concurrency hardening (shared edges.db across sessions) ---------


def test_edge_store_sets_concurrency_pragmas(tmp_path):
    """WAL keeps search-time reads from blocking a writer. busy_timeout is kept
    SHORT (not 30s) so an open never hangs the session behind the indexer."""
    store = EdgeStore(str(tmp_path / "edges.db"))
    try:
        journal = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        store.close()
    assert journal.lower() == "wal"
    assert timeout == 3000


def test_replace_all_swaps_owned_and_preserves_other_collections(tmp_path):
    """replace_all must delete only edges sourced from `owned` (this collection's
    chunks) and re-insert the new set. Edges sourced elsewhere — i.e. another
    collection sharing the global edges.db — MUST survive (the 2026-05-29
    global-delete regression guard)."""
    store = EdgeStore(str(tmp_path / "edges.db"))
    try:
        # "a" belongs to this collection; "c" belongs to a different collection.
        store.add_edges([("a", "b", "similar", 0.9, "m"), ("c", "d", "sibling", 1.0, "m")])
        store.replace_all(["a"], [("a", "b", "similar", 0.5, "m")])
        rows = store._conn.execute(
            "SELECT src_hash, dst_hash, weight FROM chunk_edges ORDER BY src_hash"
        ).fetchall()
    finally:
        store.close()
    assert rows == [("a", "b", 0.5), ("c", "d", 1.0)]  # a-b reweighted, c-d (other collection) untouched


def test_replace_all_rolls_back_on_failure_keeping_prior_edges(tmp_path):
    """A failed swap (e.g. malformed row) must roll back to the prior edges, not
    leave the graph truncated."""
    store = EdgeStore(str(tmp_path / "edges.db"))
    try:
        store.add_edges([("a", "b", "similar", 0.9, "m")])
        bad = [("a", "z", "similar", 0.5, "m"), ("too", "few")]  # 2nd tuple has wrong arity
        try:
            store.replace_all(["a"], bad)
        except Exception:
            pass
        rows = store._conn.execute("SELECT src_hash, dst_hash FROM chunk_edges").fetchall()
    finally:
        store.close()
    assert rows == [("a", "b")]  # prior edge survived the rolled-back swap


def test_is_empty_for_scopes_to_given_hashes(tmp_path):
    """is_empty_for must reflect ONLY the given hashes — a populated foreign
    collection sharing the global edges.db must not mask a fresh collection's
    emptiness (the warning-suppression bug global is_empty() has)."""
    store = EdgeStore(str(tmp_path / "edges.db"))
    try:
        store.add_edges([("foreign_src", "foreign_dst", "similar", 0.9, "m")])
        assert store.is_empty() is False  # global view: foreign edge masks emptiness
        assert store.is_empty_for(["mine_a", "mine_b"]) is True  # scoped view: my collection is empty
        store.add_edges([("x", "mine_a", "similar", 0.5, "m")])  # edge touches mine_a as dst
        assert store.is_empty_for(["mine_a", "mine_b"]) is False  # matches src OR dst
        assert store.is_empty_for([]) is True  # no hashes → vacuously empty
    finally:
        store.close()


# ----------------------------------------------------------------------
# D) watch()'s _dispatch_event concurrency + search() source_prefix escaping
# ----------------------------------------------------------------------


def test_dispatch_event_serializes_concurrent_callbacks(caplog):
    """Two debounce Timer threads firing _dispatch_event at once must BOTH be
    processed.  Without the lock the second thread drives run_until_complete on
    the already-running loop and raises "event loop is already running", silently
    dropping its file."""
    processed: list[str] = []

    async def slow_index(path: Path) -> int:
        await asyncio.sleep(0.05)
        processed.append(str(path))
        return 1

    m = MemSearch.__new__(MemSearch)
    m._watch_loop = asyncio.new_event_loop()
    m._watch_lock = threading.Lock()
    m._watch_on_event = None
    m._edges = None

    class _StubStore:
        def delete_by_source(self, source: str) -> None:
            pass

        def hashes_by_source(self, source: str) -> set[str]:
            return set()

    m._store = _StubStore()
    m.index_file = slow_index  # type: ignore[method-assign]

    t1 = threading.Thread(target=m._dispatch_event, args=("modified", Path("a.md")))
    t2 = threading.Thread(target=m._dispatch_event, args=("modified", Path("b.md")))
    with caplog.at_level(logging.ERROR, logger="memsearch.core"):
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    m._watch_loop.close()

    assert set(processed) == {"a.md", "b.md"}  # both files indexed, none dropped
    assert "already running" not in caplog.text


async def test_search_source_prefix_escapes_wildcards_and_bounds_path():
    """source_prefix must escape LIKE wildcards (% and _) and produce both an
    equality clause (the prefix itself) and a path-bounded LIKE clause, so
    `/repo/docs_v1` does not match `/repo/docs_v2/...`."""

    class _FilterStore:
        def search(self, emb, *, query_text: str = "", top_k: int = 10, filter_expr: str = "") -> list[dict[str, Any]]:
            self.last_filter = filter_expr
            return []

    stub = _FilterStore()
    m = MemSearch.__new__(MemSearch)
    m._embedder = _FakeEmbedder()
    m._reranker_model = ""
    m._store = stub
    m._graph_enabled = False
    m._edges = None
    m._recency_weight = 0.0
    m._recency_half_life_days = 30.0
    m._max_per_source = 0
    m._fetch_multiplier = 3

    await m.search("q", source_prefix="/repo/docs_v1")

    assert "\\_" in stub.last_filter  # underscore escaped (no longer a LIKE wildcard)
    assert "source == " in stub.last_filter  # equality clause for the prefix itself
    assert "source like " in stub.last_filter  # path-bounded LIKE clause


async def test_search_source_prefix_escapes_pattern_before_string_literal(tmp_path):
    """LIKE escaping order: pattern-level first (\\ -> \\\\, % -> \\%, _ -> \\_),
    THEN string-literal escaping of the finished pattern.  The old inverted
    order let a trailing Windows path separator escape the appended % wildcard
    (directory-scoped search silently returned nothing) and emitted % and _
    with only a single backslash, which the string-literal decode consumed."""

    class _FilterStore:
        def search(self, emb, *, query_text: str = "", top_k: int = 10, filter_expr: str = "") -> list[dict[str, Any]]:
            self.last_filter = filter_expr
            return []

    stub = _FilterStore()
    m = MemSearch.__new__(MemSearch)
    m._embedder = _FakeEmbedder()
    m._reranker_model = ""
    m._store = stub
    m._graph_enabled = False
    m._edges = None
    m._recency_weight = 0.0
    m._recency_half_life_days = 30.0
    m._max_per_source = 0
    m._fetch_multiplier = 3

    await m.search("q", source_prefix=tmp_path / "pct%un_der")

    like_clause = stub.last_filter.split(" or ", 1)[1]
    # Path's % and _: pattern-escaped (\%, \_) then literal-escaped -> two
    # backslashes in the final expression (the buggy order produced one).
    assert ("\\" * 2) + "%un" in like_clause
    assert ("\\" * 2) + "_der" in like_clause
    # The appended wildcard must survive: after the separator (4 backslashes on
    # Windows: pattern-escape then literal-escape) comes a bare, unescaped %.
    sep_encoded = "\\" * 4 if os.sep == "\\" else os.sep
    assert stub.last_filter.endswith(sep_encoded + '%"')


# ----------------------------------------------------------------------
# E) _index_file embed-first reorder: new chunks are stored BEFORE stale
#    ones are deleted, so an embed failure never opens a transient hole.
# ----------------------------------------------------------------------


class _StaleSpyStore:
    """Store stub: canned hashes_by_source + a recording delete_by_hashes spy."""

    def __init__(self, old_ids: set[str]) -> None:
        self._old_ids = old_ids
        self.delete_calls: list[list[str]] = []

    def hashes_by_source(self, source: str) -> set[str]:
        return set(self._old_ids)

    def delete_by_hashes(self, hashes: list[str]) -> None:
        self.delete_calls.append(list(hashes))


def _make_index_mem(store: _StaleSpyStore) -> MemSearch:
    """Minimal mem for _index_file. _edges=None short-circuits the structural-edge
    block, so _graph_structural / replace_structural_edges are never evaluated."""
    m = MemSearch.__new__(MemSearch)
    m._edges = None
    m._store = store
    m._embedder = _FakeEmbedder()
    m._max_chunk_size = 1500
    m._overlap_lines = 2
    return m


def test_index_file_keeps_stale_chunks_when_embed_fails(tmp_path):
    """Embed failure on new chunks must propagate AND leave the existing/stale
    chunks intact — delete_by_hashes is never reached when embedding raises."""
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nfresh body that yields a new chunk id\n", encoding="utf-8")
    # Sentinel old id can't equal the real computed chunk id, so it is stale AND
    # to_embed is non-empty (the new chunk's id is not in old_ids).
    store = _StaleSpyStore({"stale_sentinel"})
    m = _make_index_mem(store)

    async def _boom(chunks):
        raise RuntimeError("embed API down")

    m._embed_and_store = _boom  # type: ignore[method-assign]

    sf = ScannedFile(path=f, mtime=0.0, size=f.stat().st_size)
    with pytest.raises(RuntimeError, match="embed API down"):
        asyncio.run(m._index_file(sf))

    assert store.delete_calls == []  # stale chunk NOT deleted — no transient hole


def test_index_file_emptied_file_still_deletes_stale(tmp_path):
    """An emptied file (no chunks → empty to_embed) must still delete the old
    chunks — the emptied-file stale-delete the reorder must preserve."""
    f = tmp_path / "doc.md"
    f.write_text("", encoding="utf-8")
    old = {"old1", "old2"}
    store = _StaleSpyStore(old)
    m = _make_index_mem(store)
    # _embed_and_store must not run for an empty file; assert by sabotaging it.
    m._embed_and_store = None  # type: ignore[assignment]

    sf = ScannedFile(path=f, mtime=0.0, size=0)
    n = asyncio.run(m._index_file(sf))

    assert n == 0
    assert len(store.delete_calls) == 1
    assert set(store.delete_calls[0]) == old  # exactly the old set (order nondeterministic)


def test_index_file_strips_bom_and_tolerates_bad_bytes(tmp_path):
    """utf-8-sig: a BOM must not land in the first chunk's content/hash, and a
    stray invalid byte must not make the whole file unindexable."""
    f = tmp_path / "doc.md"
    f.write_bytes(b"\xef\xbb\xbf# Title\n\nbody with a bad \xff byte\n")
    store = _StaleSpyStore(set())
    m = _make_index_mem(store)
    captured: list[Any] = []

    async def _capture(chunks):
        captured.extend(chunks)
        return len(chunks)

    m._embed_and_store = _capture  # type: ignore[method-assign]

    sf = ScannedFile(path=f, mtime=0.0, size=f.stat().st_size)
    asyncio.run(m._index_file(sf))

    assert captured, "file with a bad byte must still produce chunks"
    assert captured[0].content.startswith("# Title")  # BOM stripped
    assert "\ufeff" not in captured[0].content


# ----------------------------------------------------------------------
# F) Path normalization: watcher delete resolution + GC case drift on NTFS
# ----------------------------------------------------------------------


def test_dispatch_event_deleted_resolves_path_like_indexing(tmp_path):
    """The watcher's deleted branch must delete by the SAME resolved path form
    indexing stores (str(Path(...).expanduser().resolve())) — a raw watchdog
    path that differs in form would leave the file's chunks behind forever."""
    deleted: list[str] = []

    class _SpyStore:
        def hashes_by_source(self, source: str) -> set[str]:
            return set()

        def delete_by_source(self, source: str) -> None:
            deleted.append(source)

    m = MemSearch.__new__(MemSearch)
    m._watch_loop = asyncio.new_event_loop()
    m._watch_lock = threading.Lock()
    m._watch_on_event = None
    m._edges = None
    m._store = _SpyStore()

    m._dispatch_event("deleted", Path("some.md"))  # relative, unresolved
    m._watch_loop.close()

    assert deleted == [str(Path("some.md").expanduser().resolve())]


def test_index_gc_prunes_case_drifted_stale_source(tmp_path):
    """Deleted-file GC scope check must be case-insensitive on Windows: a stale
    stored source whose case drifted from the scope root must still be pruned
    (pre-3.12 Path.is_relative_to compares case-sensitively). On POSIX the
    drifted form is the identity, so this also guards plain in-scope pruning."""
    stale = str((tmp_path / "sub" / "gone.md").resolve())
    drifted = stale.upper() if sys.platform == "win32" else stale

    class _GCStore:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def indexed_sources(self) -> set[str]:
            return {drifted}

        def hashes_by_source(self, source: str) -> set[str]:
            return set()

        def delete_by_source(self, source: str) -> None:
            self.deleted.append(source)

    m = MemSearch.__new__(MemSearch)
    m._paths = [str(tmp_path)]
    m._edges = None
    m._store = _GCStore()

    asyncio.run(m.index())

    assert m._store.deleted == [drifted]
