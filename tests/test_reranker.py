"""Tests for reranker score extraction (no model download required)."""

from __future__ import annotations

import numpy as np

from memsearch.reranker import _extract_scores, _sigmoid


def test_sigmoid_does_not_overflow_on_extreme_logits() -> None:
    """math.exp(-x) overflows for x < ~-709; the safe sigmoid must not raise."""
    assert _sigmoid(-1000.0) == 0.0
    assert _sigmoid(1000.0) == 1.0
    assert abs(_sigmoid(0.0) - 0.5) < 1e-12


def test_extract_scores_single_logit_extreme_values() -> None:
    logits = np.array([[-1000.0], [0.0], [1000.0]])
    scores = _extract_scores(logits)
    assert scores[0] == 0.0
    assert abs(scores[1] - 0.5) < 1e-12
    assert scores[2] == 1.0


def test_extract_scores_flat_logits_extreme_values() -> None:
    logits = np.array([-1000.0, 1000.0])
    scores = _extract_scores(logits)
    assert scores == [0.0, 1.0]


def test_extract_scores_two_class_softmax_unchanged() -> None:
    logits = np.array([[0.0, 0.0], [-5.0, 5.0]])
    scores = _extract_scores(logits)
    assert abs(scores[0] - 0.5) < 1e-12
    assert scores[1] > 0.99


# ----------------------------------------------------------------------
# Length-bucketed _rerank_onnx: scores must land on their ORIGINAL result
# despite shortest-first sub-batching (the silent-misassignment risk).
# ----------------------------------------------------------------------


class _FakeEnc:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)
        self.type_ids = [0] * len(ids)


class _FakeTokenizer:
    """encode() returns ids whose FIRST token is the result's index (a marker),
    with a strictly-decreasing length so the length sort permutes the order."""

    def encode(self, query: str, content: str):
        idx = int(content.removeprefix("doc"))
        length = 50 - idx  # 50,49,... -> ascending-length sort reverses input order
        return _FakeEnc([idx] + [0] * (length - 1))


class _FakeSession:
    def run(self, _outputs, feed):
        # logit per row == its first token id (== the original result's marker)
        return [feed["input_ids"][:, :1].astype(float)]


def test_rerank_onnx_maps_scores_to_original_results(monkeypatch) -> None:
    from memsearch import reranker as RR

    fake = RR._OnnxCachedModel(session=_FakeSession(), tokenizer=_FakeTokenizer(), input_names=set())
    monkeypatch.setattr(RR, "_load_onnx_model", lambda name: fake)

    n = 20  # > _RERANK_BATCH so multiple buckets exercise the un-permutation
    results = [{"chunk_hash": f"h{i}", "content": f"doc{i}"} for i in range(n)]
    out = RR._rerank_onnx("q", results, "fake-model", top_k=0)

    # Each result must carry sigmoid(its own marker == its index); a wrong
    # batch->original mapping would shuffle scores onto the wrong chunk_hash.
    score_by_hash = {r["chunk_hash"]: r["score"] for r in out}
    for i in range(n):
        assert abs(score_by_hash[f"h{i}"] - RR._sigmoid(float(i))) < 1e-9

    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)  # returned sorted by score desc


def test_rerank_onnx_respects_top_k(monkeypatch) -> None:
    from memsearch import reranker as RR

    fake = RR._OnnxCachedModel(session=_FakeSession(), tokenizer=_FakeTokenizer(), input_names=set())
    monkeypatch.setattr(RR, "_load_onnx_model", lambda name: fake)

    results = [{"chunk_hash": f"h{i}", "content": f"doc{i}"} for i in range(10)]
    out = RR._rerank_onnx("q", results, "fake-model", top_k=3)
    assert len(out) == 3
    # Highest markers (indices 9,8,7) score highest via sigmoid.
    assert [r["chunk_hash"] for r in out] == ["h9", "h8", "h7"]
