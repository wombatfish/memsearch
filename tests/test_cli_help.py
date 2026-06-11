"""Tests for CLI help and version commands."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from memsearch.cli import _compact_preview, cli


@pytest.mark.parametrize(
    ("args", "expected_text"),
    [
        pytest.param(["--help"], "Usage:", id="main-help"),
        pytest.param(["config", "--help"], "Usage:", id="config-help"),
        pytest.param(["config", "init", "--help"], "Usage:", id="config-init-help"),
        pytest.param(["config", "set", "--help"], "Usage:", id="config-set-help"),
        pytest.param(["config", "get", "--help"], "Usage:", id="config-get-help"),
        pytest.param(["config", "list", "--help"], "Usage:", id="config-list-help"),
        pytest.param(["index", "--help"], "Usage:", id="index-help"),
        pytest.param(["search", "--help"], "Usage:", id="search-help"),
        pytest.param(["expand", "--help"], "Usage:", id="expand-help"),
        pytest.param(["stats", "--help"], "Usage:", id="stats-help"),
        pytest.param(["reset", "--help"], "Usage:", id="reset-help"),
        pytest.param(["watch", "--help"], "Usage:", id="watch-help"),
        pytest.param(["compact", "--help"], "Usage:", id="compact-help"),
        pytest.param(["--version"], "version", id="version"),
    ],
)
def test_cli_help_and_version_commands(args: list[str], expected_text: str) -> None:
    """CLI entrypoints should expose stable help/version output."""
    runner = CliRunner()
    result = runner.invoke(cli, args)

    assert result.exit_code == 0
    assert expected_text in result.output


@pytest.mark.parametrize("args", [["index", "--help"], ["watch", "--help"]])
def test_chunk_size_flag_appears_in_help(args: list[str]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, args)

    assert result.exit_code == 0
    assert "--max-chunk-size" in result.output


def test_search_help_mentions_consistency() -> None:
    result = CliRunner().invoke(cli, ["search", "--help"])
    assert result.exit_code == 0
    assert "--consistency" in result.output


def test_cli_group_reconfigures_all_std_streams_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """The group callback must reconfigure stdin as well as stdout/stderr —
    `summarize` reads UTF-8 transcripts from stdin, which decodes as cp1252
    on Windows without this (silent Stop-hook summary loss)."""
    import sys

    class FakeStream:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def reconfigure(self, **kwargs) -> None:
            self.calls.append(kwargs)

    fake_in, fake_out, fake_err = FakeStream(), FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "stdin", fake_in)
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)

    cli.callback()

    for fake in (fake_in, fake_out, fake_err):
        assert fake.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_search_consistency_flag_reaches_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--consistency strong` must arrive as the MemSearch consistency_level kwarg."""
    captured: dict = {}

    class FakeMS:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def search(self, *args, **kwargs):
            return []

        def close(self):
            pass

    monkeypatch.setattr("memsearch.core.MemSearch", FakeMS)
    result = CliRunner().invoke(cli, ["search", "foo", "--consistency", "Strong"])
    assert result.exit_code == 0, result.output
    assert captured.get("consistency_level") == "Strong"


# ----------------------------------------------------------------------
# A2/A3/A4/A5 — new search/expand flags surface in help
# ----------------------------------------------------------------------


def test_search_help_mentions_new_search_flags() -> None:
    result = CliRunner().invoke(cli, ["search", "--help"])
    assert result.exit_code == 0
    for flag in ("--compact-output", "--recency-weight", "--max-per-source"):
        assert flag in result.output


def test_expand_help_mentions_query_flag() -> None:
    result = CliRunner().invoke(cli, ["expand", "--help"])
    assert result.exit_code == 0
    assert "--query" in result.output


# ----------------------------------------------------------------------
# A3 — _compact_preview formatter
# ----------------------------------------------------------------------


def test_compact_preview_skips_heading_and_comment_lines() -> None:
    content = "## Session 14:30\n<!-- session:abc turn:def transcript:/p -->\n\nDecided to use RRF k=60."
    assert _compact_preview(content) == "Decided to use RRF k=60."


def test_compact_preview_truncates_to_100() -> None:
    assert len(_compact_preview("y" * 250)) == 100


def test_compact_preview_strips_inline_comment() -> None:
    assert _compact_preview("hello <!-- c --> world") == "hello  world"


def test_compact_preview_empty_when_no_body() -> None:
    assert _compact_preview("# Title\n## Sub\n<!-- only comments -->") == ""


# ----------------------------------------------------------------------
# A3 — --compact-output JSON shape
# ----------------------------------------------------------------------


def test_search_compact_output_json_shape(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from any real ~/.memsearch config so output is pure JSON (no warnings).
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", tmp_path / "g.toml")
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "p.toml")

    class FakeMS:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, *args, **kwargs):
            return [
                {
                    "chunk_hash": "ab12",
                    "score": 0.81237,
                    "source": "/m/2026-06-05.md",
                    "heading": "Session 14:30",
                    "content": "<!-- anchor -->\nDecided to use RRF.",
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("memsearch.core.MemSearch", FakeMS)
    result = CliRunner().invoke(cli, ["search", "q", "--compact-output", "--json-output"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == [
        {
            "chunk_hash": "ab12",
            "score": 0.8124,  # round(0.81237, 4)
            "date": "2026-06-05",  # parsed from the source path
            "source": "/m/2026-06-05.md",  # full path in JSON mode
            "heading": "Session 14:30",
            "preview": "Decided to use RRF.",  # anchor comment line skipped
        }
    ]


# ----------------------------------------------------------------------
# A5 — expand recall logging is best-effort (never fails the expand)
# ----------------------------------------------------------------------


def _fake_expand_store(md_path):
    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, *, filter_expr=""):
            return [
                {
                    "source": str(md_path),
                    "start_line": 1,
                    "end_line": 2,
                    "heading": "H",
                    "heading_level": 1,
                    "chunk_hash": "abcd",
                }
            ]

        def close(self):
            pass

    return FakeStore


def test_expand_recall_log_failure_is_isolated(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", tmp_path / "g.toml")
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "p.toml")
    md = tmp_path / "2026-06-05.md"
    md.write_text("# H\nbody line\n", encoding="utf-8")

    class FakeEdges:
        def __init__(self, *args, **kwargs):
            pass

        def log_recall(self, *args, **kwargs):
            raise OSError("read-only edges.db")

        def close(self):
            pass

    monkeypatch.setattr("memsearch.store.MilvusStore", _fake_expand_store(md))
    monkeypatch.setattr("memsearch.edges.EdgeStore", FakeEdges)

    result = CliRunner().invoke(cli, ["expand", "abcd", "--query", "what did I decide"])
    assert result.exit_code == 0, result.output
    assert "body line" in result.output


def test_expand_logs_recall_with_query(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", tmp_path / "g.toml")
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "p.toml")
    md = tmp_path / "2026-06-05.md"
    md.write_text("# H\nbody line\n", encoding="utf-8")
    captured: dict = {}

    class FakeEdges:
        def __init__(self, *args, **kwargs):
            pass

        def log_recall(self, chunk_hash, *, query="", collection=""):
            captured.update(chunk_hash=chunk_hash, query=query, collection=collection)

        def close(self):
            pass

    monkeypatch.setattr("memsearch.store.MilvusStore", _fake_expand_store(md))
    monkeypatch.setattr("memsearch.edges.EdgeStore", FakeEdges)

    result = CliRunner().invoke(cli, ["expand", "abcd", "--query", "what did I decide"])
    assert result.exit_code == 0, result.output
    assert captured["chunk_hash"] == "abcd"
    assert captured["query"] == "what did I decide"
