from __future__ import annotations

import pytest
from click.testing import CliRunner

from memsearch.cli import cli


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    proj = tmp_path / ".memsearch.toml"
    glob = tmp_path / "global.toml"
    from memsearch import cli as cli_mod, config as cfg_mod

    for mod in (cli_mod, cfg_mod):
        monkeypatch.setattr(mod, "GLOBAL_CONFIG_PATH", glob, raising=False)
        monkeypatch.setattr(mod, "PROJECT_CONFIG_PATH", proj, raising=False)
    return {"proj": proj, "glob": glob}


def test_config_init_preserves_env_ref(isolated_config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-SHOULD-NOT-APPEAR")
    isolated_config["proj"].write_text('[embedding]\napi_key = "env:OPENAI_API_KEY"\n')

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "init", "--project"], input="\n" * 150)

    text = isolated_config["proj"].read_text()
    assert "env:OPENAI_API_KEY" in text
    assert "sk-test-SHOULD-NOT-APPEAR" not in text


def test_expand_rejects_malformed_hash():
    runner = CliRunner()
    result = runner.invoke(cli, ["expand", 'x" or "1"=="1'])

    assert result.exit_code == 1
    assert "Invalid chunk hash" in result.stderr


def test_config_set_masks_secret_value(isolated_config):
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "embedding.api_key", "sk-supersecret", "--project"])

    assert result.exit_code == 0
    assert "sk-supersecret" not in result.output


def test_config_set_shows_nonsecret_value(isolated_config):
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "milvus.uri", "http://host:19530", "--project"])

    assert result.exit_code == 0
    assert "http://host:19530" in result.output


def test_config_list_masks_literal_secret(isolated_config):
    isolated_config["proj"].write_text('[embedding]\napi_key = "sk-literal-secret"\n')

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "list"])
    assert "sk-literal-secret" not in result.output

    result_shown = runner.invoke(cli, ["config", "list", "--show-secrets"])
    assert "sk-literal-secret" in result_shown.output


def test_config_list_friendly_error_on_unset_env(isolated_config, monkeypatch):
    monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
    isolated_config["proj"].write_text('[milvus]\ntoken = "env:UNSET_VAR_XYZ"\n')

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "list"])

    assert result.exit_code == 1
    assert "Configuration error" in result.stderr


def test_config_get_friendly_error_on_unset_env(isolated_config, monkeypatch):
    monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
    isolated_config["proj"].write_text('[milvus]\ntoken = "env:UNSET_VAR_XYZ"\n')

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "get", "milvus.token"])

    assert result.exit_code == 1
    assert "Configuration error" in result.stderr
