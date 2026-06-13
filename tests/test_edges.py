"""Tests for EdgeStore (SQLite edge sidecar)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from memsearch.edges import EdgeStore


@pytest.fixture
def store(tmp_path):
    s = EdgeStore(uri=str(tmp_path / "edges.db"))
    yield s
    s.close()


# ---------------------------------------------------------------------------
# 1. Forward direction
# ---------------------------------------------------------------------------


def test_neighbors_forward(store):
    store.add_edges([("src1", "dst1", "related", 0.9, "gpt4")])
    result = store.neighbors(["src1"])
    assert result == [("dst1", 0.9, "src1")]


# ---------------------------------------------------------------------------
# 2. Undirected: reverse direction
# ---------------------------------------------------------------------------


def test_neighbors_reverse(store):
    store.add_edges([("src1", "dst1", "related", 0.9, "gpt4")])
    result = store.neighbors(["dst1"])
    assert result == [("src1", 0.9, "dst1")]


# ---------------------------------------------------------------------------
# 3. Fanout cap: exactly limit_per_node results returned
# ---------------------------------------------------------------------------


def test_fanout_cap_count(store):
    edges = [("seed", f"n{i}", "rel", float(i) / 10, "m") for i in range(10)]
    store.add_edges(edges)
    result = store.neighbors(["seed"], limit_per_node=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# 4. Weight ordering: top-N results are the highest-weight neighbours
# ---------------------------------------------------------------------------


def test_weight_ordering(store):
    edges = [("seed", f"n{i}", "rel", float(i) / 10, "m") for i in range(10)]
    store.add_edges(edges)
    result = store.neighbors(["seed"], limit_per_node=3)
    weights = [w for _, w, _ in result]
    # Results must be sorted desc within the seed group.
    assert weights == sorted(weights, reverse=True)
    # The three highest weights are 0.9, 0.8, 0.7 (n9, n8, n7).
    assert pytest.approx(sorted(weights, reverse=True)) == [0.9, 0.8, 0.7]


# ---------------------------------------------------------------------------
# 5. delete_by_hashes removes edges in BOTH directions
# ---------------------------------------------------------------------------


def test_delete_by_hashes_both_directions(store):
    # "a" is src in first edge, dst in second edge.
    store.add_edges([("a", "b", "rel", 1.0, "m"), ("c", "a", "rel", 0.5, "m")])
    store.delete_by_hashes(["a"])
    assert store.neighbors(["a"]) == []
    assert store.neighbors(["b"]) == []
    assert store.neighbors(["c"]) == []


# ---------------------------------------------------------------------------
# 5b. delete_by_hashes spanning more than one batch (mirrors replace_all's
#     500-slice batching): deletes exactly the targeted rows, leaves the rest.
# ---------------------------------------------------------------------------


def test_delete_by_hashes_spans_multiple_batches(store):
    # 501 targeted src hashes > one 500-hash batch → forces a second batch.
    targeted = [f"t{i:03d}" for i in range(501)]
    store.add_edges([(h, "common_dst", "rel", 1.0, "m") for h in targeted])
    # One non-targeted edge whose endpoints are absent from `targeted`.
    store.add_edges([("keep_src", "keep_dst", "rel", 0.5, "m")])

    store.delete_by_hashes(targeted)

    rows = store._conn.execute("SELECT src_hash, dst_hash, relation FROM chunk_edges").fetchall()
    assert rows == [("keep_src", "keep_dst", "rel")]  # only the untargeted edge remains


# ---------------------------------------------------------------------------
# 6. clear() empties the table; is_empty() reflects the state
# ---------------------------------------------------------------------------


def test_clear_and_is_empty(store):
    assert store.is_empty()
    store.add_edges([("x", "y", "r", 1.0, "m")])
    assert not store.is_empty()
    store.clear()
    assert store.is_empty()


# ---------------------------------------------------------------------------
# 7. INSERT OR REPLACE idempotency: duplicate (src,dst,relation) keeps latest weight
# ---------------------------------------------------------------------------


def test_insert_or_replace_idempotency(store):
    store.add_edges([("a", "b", "rel", 0.5, "m1")])
    store.add_edges([("a", "b", "rel", 0.8, "m2")])
    result = store.neighbors(["a"])
    assert len(result) == 1
    assert pytest.approx(result[0][1]) == 0.8


# ---------------------------------------------------------------------------
# 8. Empty-input no-ops: no errors, sensible empty returns
# ---------------------------------------------------------------------------


def test_empty_inputs_no_error(store):
    store.add_edges([])  # should not raise
    assert store.neighbors([]) == []
    store.delete_by_hashes([])  # should not raise


# ---------------------------------------------------------------------------
# 9. __init__ must not stall (or crash) behind a concurrent writer.
#    Regression: schema DDL ran on every open and took a write lock, so opening
#    the store on the search hot path hung up to busy_timeout (30s) behind the
#    session-start indexer's write transaction.
# ---------------------------------------------------------------------------


def test_init_does_not_block_behind_a_writer(tmp_path):
    db = str(tmp_path / "edges.db")
    EdgeStore(db).close()  # create the file + schema once

    # Hold an open write transaction, as the watcher/indexer does mid-write.
    blocker = sqlite3.connect(db, timeout=60)
    blocker.execute("PRAGMA busy_timeout=0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT OR REPLACE INTO chunk_edges VALUES ('w', 'w', 'r', 1.0, 'm')")
    try:
        t0 = time.monotonic()
        s = EdgeStore(db)  # must skip DDL (table exists) → no write lock, no stall
        elapsed = time.monotonic() - t0
        s.close()
    finally:
        blocker.rollback()
        blocker.close()
    assert elapsed < 1.0, f"EdgeStore.__init__ stalled {elapsed:.2f}s behind a writer (DDL took a write lock)"


# ---------------------------------------------------------------------------
# 10. replace_structural_edges: relation-scoped + owned-scoped swap.
#     Incremental reindex must drop stale sibling/same_section rows for this
#     source's chunks while preserving cross-file `similar` edges.
# ---------------------------------------------------------------------------


def test_replace_structural_edges_scopes_relation_and_owned(store):
    store.add_edges(
        [
            ("a", "b", "sibling", 1.0, "m"),
            ("a", "c", "same_section", 0.6, "m"),
            ("a", "z", "similar", 0.8, "m"),
        ]
    )
    store.replace_structural_edges(["a", "b", "c"], [("a", "b", "sibling", 1.0, "m")])
    rows = set(store._conn.execute("SELECT src_hash, dst_hash, relation FROM chunk_edges").fetchall())
    assert ("a", "c", "same_section") not in rows  # stale structural edge dropped
    assert ("a", "b", "sibling") in rows  # current structural edge present
    assert ("a", "z", "similar") in rows  # cross-file similar edge preserved


def test_replace_structural_edges_rolls_back_on_failure(store):
    """A failed swap (malformed row) must roll back to the prior structural edge,
    not leave it deleted."""
    store.add_edges([("a", "b", "sibling", 1.0, "m")])
    bad = [("a", "b", "sibling", 1.0, "m"), ("bad", "tuple")]  # 2nd tuple has wrong arity
    try:
        store.replace_structural_edges(["a"], bad)
    except Exception:
        pass
    rows = store._conn.execute("SELECT src_hash, dst_hash, relation FROM chunk_edges").fetchall()
    assert rows == [("a", "b", "sibling")]  # prior edge survived the rolled-back swap


def test_neighbors_dedup_reciprocal_and_multirelation(store):
    """A reciprocal pair (a->b and b->a) must not surface the same neighbor twice and
    consume a fanout slot — dedup by (seed, neighbor) keeping the max weight BEFORE the
    per-node cap, so a distinct lower-weight neighbor is not suppressed."""
    store.add_edges(
        [
            ("a", "b", "similar", 0.9, "m"),
            ("b", "a", "similar", 0.8, "m"),  # reciprocal: also surfaces b for seed a
            ("a", "c", "similar", 0.7, "m"),
        ]
    )
    result = store.neighbors(["a"], limit_per_node=2)
    assert sorted(n for n, _, _ in result) == ["b", "c"]  # b once; c not dropped
    weights = {n: w for n, w, _ in result}
    assert weights["b"] == 0.9  # max weight across the collapsed reciprocal rows


def test_replace_all_rollback_logs_warning(store, caplog):
    """A rolled-back swap must emit an observable warning — edges.py was the site of a
    data-loss incident and silent rollbacks are undiagnosable in production."""
    import logging

    store.add_edges([("a", "b", "rel", 1.0, "m")])
    bad = [("a", "b", "rel", 1.0, "m"), ("bad", "tuple")]  # wrong arity -> failure mid-swap
    with caplog.at_level(logging.WARNING, logger="memsearch.edges"):
        try:
            store.replace_all(["a"], bad)
        except Exception:
            pass
    assert any("rolled back" in r.message for r in caplog.records)
