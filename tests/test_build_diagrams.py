"""Regression tests for docs/diagrams/build_diagrams.py.

The script is not a package, so it is loaded by file path. These cover:
  * I9 — lint_full enforces invariants with raises (survives `python -O`, which
         strips assert statements).
  * I10 — per-diagram seed reset (deterministic, cross-diagram independent ids).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_BD_PATH = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "build_diagrams.py"


def _load_bd():
    spec = importlib.util.spec_from_file_location("build_diagrams_under_test", _BD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bd = _load_bd()


# --- I9: lint_full raises (not assert) --------------------------------------


def test_lint_full_raises_diagram_lint_error_on_label() -> None:
    scene = {"elements": [{"type": "text", "label": "x"}]}
    with pytest.raises(bd.DiagramLintError):
        bd.lint_full(scene, [], "t")


def test_lint_full_enforced_under_O_optimization() -> None:
    """Under `python -O` asserts are stripped; the invariant must still raise."""
    bd_path = _BD_PATH.as_posix()
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('bd', '{bd_path}')\n"
        "bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)\n"
        "scene = {'elements': [{'type': 'text', 'label': 'x'}]}\n"
        "try:\n"
        "    bd.lint_full(scene, [], 't'); print('NO_RAISE')\n"
        "except bd.DiagramLintError: print('RAISED')\n"
    )
    result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "RAISED" in result.stdout


# --- I10: per-diagram seed reset --------------------------------------------


def test_seed_reset_makes_build_deterministic() -> None:
    bd._reset_seed()
    seeds_a = [e["seed"] for e in bd.to_full(bd.d1_architecture())["elements"] if "seed" in e]
    bd._reset_seed()
    seeds_b = [e["seed"] for e in bd.to_full(bd.d1_architecture())["elements"] if "seed" in e]
    assert seeds_a  # the diagram actually emits seeded elements
    assert seeds_a == seeds_b


def test_seed_reset_isolates_diagrams() -> None:
    """An edit to an earlier diagram's element count must not shift a later diagram's
    seeds, because each diagram resets the counter."""
    bd._reset_seed()
    d2_alone = [e["seed"] for e in bd.to_full(bd.d2_lifecycle())["elements"] if "seed" in e]

    bd._reset_seed()
    bd.to_full(bd.d1_architecture())  # build d1 first (consumes the shared counter)
    bd._reset_seed()
    d2_after_d1 = [e["seed"] for e in bd.to_full(bd.d2_lifecycle())["elements"] if "seed" in e]

    assert d2_alone == d2_after_d1


def test_all_diagrams_build_and_lint() -> None:
    """Smoke test of the real pipeline (no file writes): every diagram builds and lints."""
    for name, fn in bd.DIAGRAMS:
        bd._reset_seed()
        spec = fn()
        scene = bd.to_full(spec)
        assert bd.lint_full(scene, spec, name) > 0


def test_lint_full_rejects_blank_text_value() -> None:
    """N5: a present-but-blank text element (text='   ') must be rejected, not only missing keys."""
    el = {
        "type": "text", "text": "   ", "fontFamily": 2, "textAlign": "center",
        "verticalAlign": "top", "width": 10, "height": 10, "fontSize": 16, "strokeColor": "#1e1e1e",
    }
    with pytest.raises(bd.DiagramLintError):
        bd.lint_full({"elements": [el]}, [{"k": "text"}], "t")


def test_lint_full_allows_cameraupdate_in_text_content() -> None:
    """N11: a text element whose CONTENT mentions cameraUpdate must NOT false-positive —
    the check is structural (element type/keys), not a raw-string search."""
    el = {
        "type": "text", "text": "see cameraUpdate docs", "fontFamily": 2, "textAlign": "center",
        "verticalAlign": "top", "width": 100, "height": 20, "fontSize": 16, "strokeColor": "#1e1e1e",
    }
    assert bd.lint_full({"elements": [el]}, [{"k": "text"}], "t") == 1
