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
