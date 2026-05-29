"""Tests for EdgeStore (SQLite edge sidecar)."""

from __future__ import annotations

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
