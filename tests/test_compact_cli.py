from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from click.testing import CliRunner

from memsearch import cli as cli_module
from memsearch.cli import cli
from memsearch.config import save_config


class DummyMemSearch:
    last_source = None
    last_prompt_template = None
    last_kwargs: ClassVar[dict] = {}

    async def compact(self, **kwargs):
        DummyMemSearch.last_source = kwargs["source"]
        DummyMemSearch.last_prompt_template = kwargs["prompt_template"]
        DummyMemSearch.last_kwargs = kwargs
        return ""

    def close(self) -> None:
        pass


def test_normalize_compact_source_resolves_existing_relative_path(tmp_path: Path):
    note = tmp_path / "memory" / "old-notes.md"
    note.parent.mkdir()
    note.write_text("# note\n")

    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        normalized = cli_module._normalize_compact_source("./memory/old-notes.md")
    finally:
        os.chdir(cwd)

    assert normalized == str(note.resolve())


def test_normalize_compact_source_expands_user_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    note = home / "memory" / "old-notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("# note\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows expanduser reads USERPROFILE, not HOME

    normalized = cli_module._normalize_compact_source("~/memory/old-notes.md")

    assert normalized == str(note.resolve())


def test_normalize_compact_source_leaves_non_path_filters_unchanged() -> None:
    source = "session:abc123"

    assert cli_module._normalize_compact_source(source) == source


def test_compact_shows_matched_source_when_no_chunks(monkeypatch, tmp_path: Path):
    note = tmp_path / "memory" / "old-notes.md"
    note.parent.mkdir()
    note.write_text("# note\n")

    monkeypatch.setattr("memsearch.core.MemSearch", lambda **kwargs: DummyMemSearch())

    runner = CliRunner()
    result = runner.invoke(cli, ["compact", "--source", str(note)])

    assert result.exit_code == 0
    assert DummyMemSearch.last_source == str(note.resolve())
    assert f"No chunks matched source: {note.resolve()}" in result.output


def test_compact_reads_prompt_file_and_passes_template(monkeypatch, tmp_path: Path):
    note = tmp_path / "memory" / "old-notes.md"
    note.parent.mkdir()
    note.write_text("# note\n")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Summarize carefully:\n{chunks}\n", encoding="utf-8")

    monkeypatch.setattr("memsearch.core.MemSearch", lambda **kwargs: DummyMemSearch())

    runner = CliRunner()
    result = runner.invoke(cli, ["compact", "--source", str(note), "--prompt-file", str(prompt_file)])

    assert result.exit_code == 0
    assert DummyMemSearch.last_prompt_template == "Summarize carefully:\n{chunks}\n"


def test_compact_cli_llm_flags_beat_llm_config_section(monkeypatch, tmp_path: Path):
    """Explicit --llm-* CLI flags must win over a [llm] config section
    (previously they mapped into [compact] and lost to [llm])."""
    cfg_path = tmp_path / "config.toml"
    save_config(
        {"llm": {"provider": "anthropic", "model": "config-model", "base_url": "https://config.example.com"}},
        cfg_path,
    )
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "nope.toml")
    monkeypatch.setattr("memsearch.core.MemSearch", lambda **kwargs: DummyMemSearch())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["compact", "--llm-provider", "gemini", "--llm-model", "cli-model", "--llm-base-url", "https://cli.example.com"],
    )

    assert result.exit_code == 0, result.output
    assert DummyMemSearch.last_kwargs["llm_provider"] == "gemini"
    assert DummyMemSearch.last_kwargs["llm_model"] == "cli-model"
    assert DummyMemSearch.last_kwargs["llm_base_url"] == "https://cli.example.com"


def test_compact_cli_prompt_file_beats_prompts_config(monkeypatch, tmp_path: Path):
    """--prompt-file must win over a prompts.compact config entry."""
    config_prompt = tmp_path / "config-prompt.txt"
    config_prompt.write_text("From config:\n{chunks}\n", encoding="utf-8")
    cli_prompt = tmp_path / "cli-prompt.txt"
    cli_prompt.write_text("From CLI:\n{chunks}\n", encoding="utf-8")

    cfg_path = tmp_path / "config.toml"
    save_config({"prompts": {"compact": str(config_prompt)}}, cfg_path)
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "nope.toml")
    monkeypatch.setattr("memsearch.core.MemSearch", lambda **kwargs: DummyMemSearch())

    runner = CliRunner()
    result = runner.invoke(cli, ["compact", "--prompt-file", str(cli_prompt)])

    assert result.exit_code == 0, result.output
    assert DummyMemSearch.last_prompt_template == "From CLI:\n{chunks}\n"


def test_summarize_uses_named_provider(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    save_config(
        {
            "llm": {
                "providers": {
                    "openai": {
                        "type": "openai",
                        "model": "provider-model",
                        "api_key": "env:OPENAI_API_KEY",
                    }
                }
            },
            "plugins": {
                "codex": {
                    "summarize": {
                        "provider": "openai",
                        "model": "plugin-model",
                    }
                }
            },
        },
        cfg_path,
    )
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "nope.toml")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    captured = {}

    async def fake_summarize_text(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "- summarized"

    monkeypatch.setattr("memsearch.compact.summarize_text", fake_summarize_text)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["summarize", "--plugin", "codex", "--agent-name", "Codex"],
        input="[Human]: hello\n[Codex]: hi",
    )

    assert result.exit_code == 0
    assert result.output.strip() == "- summarized"
    assert "Transcript:\n[Human]: hello" in captured["prompt"]
    assert captured["llm_provider"] == "openai"
    assert captured["model"] == "plugin-model"
    assert captured["api_key"] == "env:OPENAI_API_KEY"


def test_summarize_rejects_native_provider(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    save_config(
        {"plugins": {"codex": {"summarize": {"provider": "native"}}}},
        cfg_path,
    )
    monkeypatch.setattr("memsearch.config.GLOBAL_CONFIG_PATH", cfg_path)
    monkeypatch.setattr("memsearch.config.PROJECT_CONFIG_PATH", tmp_path / "nope.toml")

    runner = CliRunner()
    result = runner.invoke(cli, ["summarize", "--plugin", "codex"], input="hello")

    assert result.exit_code == 2
    assert "native summarization" in result.output
