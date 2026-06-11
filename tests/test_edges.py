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


# ---------------------------------------------------------------------------
# 11. log_recall: implicit-feedback votes (A5 storage layer)
# ---------------------------------------------------------------------------


def test_log_recall_creates_table_and_inserts_row(store):
    """log_recall lazily creates recall_log and the inserted row is readable."""
    store.log_recall("hash1", query="what is X?", collection="my_col")
    rows = store._conn.execute(
        "SELECT ts, chunk_hash, query, collection FROM recall_log"
    ).fetchall()
    assert len(rows) == 1
    ts, chunk_hash, query, collection = rows[0]
    assert chunk_hash == "hash1"
    assert query == "what is X?"
    assert collection == "my_col"
    # ts should be a non-empty ISO-8601 string
    assert ts and "T" in ts


def test_log_recall_second_call_appends_no_error(store):
    """Calling log_recall twice must not error and must leave exactly two rows."""
    store.log_recall("h1", query="q1", collection="c1")
    store.log_recall("h2", query="q2", collection="c2")
    rows = store._conn.execute("SELECT chunk_hash FROM recall_log").fetchall()
    assert len(rows) == 2
    # Table still exists after both calls
    table = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recall_log'"
    ).fetchone()
    assert table is not None


def test_log_recall_absent_before_first_call(tmp_path):
    """A fresh EdgeStore must NOT create recall_log — only chunk_edges is created."""
    s = EdgeStore(uri=str(tmp_path / "fresh.db"))
    try:
        result = s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recall_log'"
        ).fetchone()
        assert result is None, "recall_log must not exist before log_recall is called"
    finally:
        s.close()


def test_log_recall_defaults_empty_strings(store):
    """log_recall with no keyword args stores empty strings for query and collection."""
    store.log_recall("hashX")
    rows = store._conn.execute(
        "SELECT query, collection FROM recall_log"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("", "")
