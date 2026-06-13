from __future__ import annotations

from click.testing import CliRunner
from pymilvus.exceptions import MilvusException

from memsearch import cli as cli_module
from memsearch import store as store_module
from memsearch.cli import cli
from memsearch.config import ConfigEnvVarError, MemSearchConfig


def test_stats_shows_friendly_config_error(monkeypatch) -> None:
    def fake_resolve_config(_overrides=None):
        raise ConfigEnvVarError("Environment variable 'MISSING_KEY' referenced in config is not set")

    monkeypatch.setattr(cli_module, "resolve_config", fake_resolve_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["stats"])

    assert result.exit_code == 1
    assert "Configuration error:" in result.stderr
    assert "MISSING_KEY" in result.stderr


def test_stats_shows_friendly_milvus_error(monkeypatch) -> None:
    class BrokenStore:
        def __init__(self, **_kwargs):
            raise MilvusException(code=2, message="server unavailable")

    monkeypatch.setattr(cli_module, "resolve_config", lambda _overrides=None: MemSearchConfig())
    monkeypatch.setattr(store_module, "MilvusStore", BrokenStore)

    runner = CliRunner()
    result = runner.invoke(cli, ["stats"])

    assert result.exit_code == 1
    assert "Milvus error (code 2):" in result.stderr
    assert "server unavailable" in result.stderr


def test_unrelated_key_error_is_not_swallowed(monkeypatch) -> None:
    """A bare KeyError from config resolution (e.g. a programming bug) must
    surface as a traceback, not be misreported as a user config error."""

    def fake_resolve_config(_overrides=None):
        raise KeyError("internal_lookup_bug")

    monkeypatch.setattr(cli_module, "resolve_config", fake_resolve_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["stats"])

    assert result.exit_code != 0
    assert "Configuration error:" not in (result.stderr or "")
    assert isinstance(result.exception, KeyError)
    assert not isinstance(result.exception, ConfigEnvVarError)


def test_missing_env_var_in_real_resolve(monkeypatch) -> None:
    """End-to-end: a real env:VAR config with an unset var produces a
    friendly error, exercising ConfigEnvVarError through resolve_config."""
    monkeypatch.delenv("DEFINITELY_NOT_SET_MEMSEARCH_API_KEY", raising=False)

    def fake_load(_path):
        return {"embedding": {"api_key": "env:DEFINITELY_NOT_SET_MEMSEARCH_API_KEY"}}

    monkeypatch.setattr(cli_module, "resolve_config", cli_module.resolve_config)
    # Patch the loader inside the real resolve_config pipeline.
    from memsearch import config as config_module

    monkeypatch.setattr(config_module, "load_config_file", fake_load)

    runner = CliRunner()
    result = runner.invoke(cli, ["stats"])

    assert result.exit_code == 1
    assert "Configuration error:" in result.stderr
    assert "DEFINITELY_NOT_SET_MEMSEARCH_API_KEY" in result.stderr


def test_expand_no_section_emits_only_chunk_lines(monkeypatch, tmp_path) -> None:
    """`expand <hash> --no-section` (without --lines) must output ONLY the
    chunk's own lines, not the surrounding heading section. The default
    (section) output includes the neighboring 'neighbor body line'; --no-section
    must exclude it while still emitting the chunk's own body."""
    md = tmp_path / "doc.md"
    md.write_text(
        "## Section A\nneighbor body line\nchunk body line\n## Section B\nother section body\n",
        encoding="utf-8",
    )

    class FakeStore:
        def __init__(self, **_kwargs):
            pass

        def query(self, filter_expr=None):
            return [
                {
                    "chunk_hash": "deadbeef",
                    "source": str(md),
                    "start_line": 3,
                    "end_line": 3,
                    "heading": "Section A",
                    "heading_level": 2,
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "resolve_config", lambda _overrides=None: MemSearchConfig())
    monkeypatch.setattr(store_module, "MilvusStore", FakeStore)

    runner = CliRunner()
    default_result = runner.invoke(cli, ["expand", "deadbeef"])
    no_section_result = runner.invoke(cli, ["expand", "deadbeef", "--no-section"])

    assert default_result.exit_code == 0
    assert no_section_result.exit_code == 0
    # Default (section) output spans the whole heading section.
    assert "neighbor body line" in default_result.output
    assert "chunk body line" in default_result.output
    # --no-section drops the neighbor line but keeps the chunk's own line.
    assert "neighbor body line" not in no_section_result.output
    assert "chunk body line" in no_section_result.output


def test_expand_malformed_chunk_emits_friendly_error(monkeypatch) -> None:
    """I4: a chunk missing required fields (schema corruption / concurrent deletion) must
    produce a clear message and exit 1, not a bare KeyError traceback (KeyError is not
    caught by the outer MilvusException handler)."""

    class FakeStore:
        def __init__(self, **_kwargs):
            pass

        def query(self, filter_expr=None):
            return [{}]  # malformed: missing source/start_line/end_line

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "resolve_config", lambda _overrides=None: MemSearchConfig())
    monkeypatch.setattr(store_module, "MilvusStore", FakeStore)

    runner = CliRunner()
    result = runner.invoke(cli, ["expand", "deadbeef"])

    assert result.exit_code == 1
    assert "Malformed chunk" in result.stderr
    assert not isinstance(result.exception, KeyError)  # friendly exit, not a traceback


def test_search_rejects_non_positive_top_k() -> None:
    """--top-k 0 violates IntRange(min=1) → parse-time usage error (exit 2)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "q", "--top-k", "0"])

    assert result.exit_code != 0


def test_expand_rejects_negative_lines() -> None:
    """--lines below IntRange(min=0) → parse-time usage error (exit 2).

    Use the `--lines=-5` equals form so Click does not parse `-5` as a flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["expand", "deadbeef", "--lines=-5"])

    assert result.exit_code != 0


def test_expand_handles_non_utf8_source_file(monkeypatch, tmp_path) -> None:
    """N1: a cp1252/Latin-1 source file (invalid UTF-8 byte) must not crash expand with
    UnicodeDecodeError — read with errors='replace'."""
    md = tmp_path / "doc.md"
    md.write_bytes(b"## H\nsmart \x92quote here\nbody\n")  # 0x92 is invalid UTF-8

    class FakeStore:
        def __init__(self, **_kwargs):
            pass

        def query(self, filter_expr=None):
            return [
                {"chunk_hash": "deadbeef", "source": str(md), "start_line": 2,
                 "end_line": 3, "heading": "H", "heading_level": 2}
            ]

        def close(self):
            pass

    monkeypatch.setattr(cli_module, "resolve_config", lambda _overrides=None: MemSearchConfig())
    monkeypatch.setattr(store_module, "MilvusStore", FakeStore)

    runner = CliRunner()
    result = runner.invoke(cli, ["expand", "deadbeef"])

    assert result.exit_code == 0
    assert not isinstance(result.exception, UnicodeDecodeError)
