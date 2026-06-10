"""Tests for the embedding provider factory (memsearch.embeddings.get_provider)."""

from __future__ import annotations

import sys
import types

import pytest

from memsearch import embeddings as emb


class _FakeProvider:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


@pytest.fixture
def _fake_provider_module(monkeypatch):
    mod = types.ModuleType("memsearch_test_fake_emb")
    mod.FakeProvider = _FakeProvider
    monkeypatch.setitem(sys.modules, "memsearch_test_fake_emb", mod)
    return mod


def _register(monkeypatch, name: str) -> None:
    monkeypatch.setitem(emb._PROVIDERS, name, ("memsearch_test_fake_emb", "FakeProvider"))


def test_dropped_api_key_and_base_url_warn(_fake_provider_module, monkeypatch, caplog) -> None:
    """Configured api_key/base_url silently ignored by a provider must log a warning."""
    _register(monkeypatch, "google")

    with caplog.at_level("WARNING", logger="memsearch.embeddings"):
        provider = emb.get_provider("google", api_key="sk-x", base_url="https://x.example.com")

    assert "api_key" not in provider.kwargs
    assert "base_url" not in provider.kwargs
    messages = [r.getMessage() for r in caplog.records]
    assert any("embedding.api_key is configured but ignored" in m for m in messages)
    assert any("embedding.base_url is configured but ignored" in m for m in messages)


def test_no_warning_when_provider_accepts_credentials(_fake_provider_module, monkeypatch, caplog) -> None:
    _register(monkeypatch, "openai")

    with caplog.at_level("WARNING", logger="memsearch.embeddings"):
        provider = emb.get_provider("openai", api_key="sk-x", base_url="https://x.example.com")

    assert provider.kwargs == {"api_key": "sk-x", "base_url": "https://x.example.com"}
    assert not caplog.records


def test_jina_mistral_accept_api_key_but_warn_on_base_url(_fake_provider_module, monkeypatch, caplog) -> None:
    _register(monkeypatch, "mistral")

    with caplog.at_level("WARNING", logger="memsearch.embeddings"):
        provider = emb.get_provider("mistral", api_key="sk-x", base_url="https://x.example.com")

    assert provider.kwargs == {"api_key": "sk-x"}
    messages = [r.getMessage() for r in caplog.records]
    assert any("embedding.base_url is configured but ignored" in m for m in messages)
    assert not any("embedding.api_key" in m for m in messages)
