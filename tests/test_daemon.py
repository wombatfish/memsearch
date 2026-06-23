from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import suppress

import pytest

from memsearch import cli as cli_module
from memsearch.config import MemSearchConfig
from memsearch.daemon import (
    DaemonKey,
    SearchDaemon,
    _ping_daemon,
    _request_with_deadline,
    daemon_address,
    daemon_request,
)


@pytest.fixture
def daemon_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _cfg(collection: str, *, provider: str = "local") -> MemSearchConfig:
    cfg = MemSearchConfig()
    cfg.embedding.provider = provider
    cfg.embedding.model = "fake-model"
    cfg.milvus.collection = collection
    cfg.daemon.warmup = False
    cfg.daemon.connect_timeout_s = 0.1
    cfg.daemon.request_timeout_s = 0.5
    return cfg


class FakeMemSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, str | None]] = []
        self._lock = threading.Lock()
        self.store = object()

    @property
    def write_lock(self):
        return self._lock

    async def search_many(self, queries: list[str], *, top_k: int = 10, source_prefix: str | None = None):
        self.calls.append((queries, top_k, source_prefix))
        await asyncio.sleep(0)
        return [
            {
                "chunk_hash": "abc123",
                "score": 0.9,
                "source": source_prefix or "",
                "heading": "H",
                "content": "body",
            }
        ][:top_k]


def _unique_collection(name: str) -> str:
    return f"daemon_{os.getpid()}_{time.time_ns()}_{name}"


def test_daemon_request_falls_back_when_no_daemon_key(daemon_home) -> None:
    cfg = _cfg(_unique_collection("missing"))

    assert daemon_request(cfg.milvus.collection, {"op": "ping"}, cfg) is None


def test_daemon_provider_gate_returns_none_without_key_read(monkeypatch, daemon_home) -> None:
    cfg = _cfg(_unique_collection("provider"), provider="openai")

    def fail_read(self):
        raise AssertionError("provider gate should run before DaemonKey.read")

    monkeypatch.setattr(DaemonKey, "read", fail_read)

    assert daemon_request(cfg.milvus.collection, {"op": "ping"}, cfg) is None


def test_search_daemon_roundtrip_uses_keyword_only_search_many(daemon_home) -> None:
    cfg = _cfg(_unique_collection("search"))
    ms = FakeMemSearch()
    daemon = SearchDaemon(ms, cfg).start()
    try:
        result = daemon_request(
            cfg.milvus.collection,
            {"op": "search", "queries": ["q1", "q2"], "top_k": 1, "source_prefix": "C:/abs/docs"},
            cfg,
        )
    finally:
        daemon.stop()

    assert result is not None
    assert result["results"][0]["chunk_hash"] == "abc123"
    assert ms.calls == [(["q1", "q2"], 1, "C:/abs/docs")]


def test_bad_request_returns_none_and_accept_loop_survives(daemon_home) -> None:
    cfg = _cfg(_unique_collection("bad"))
    ms = FakeMemSearch()
    daemon = SearchDaemon(ms, cfg).start()
    try:
        bad = daemon_request(cfg.milvus.collection, {"op": "bogus"}, cfg)
        good = daemon_request(cfg.milvus.collection, {"op": "search", "queries": ["q"], "top_k": 1}, cfg)
    finally:
        daemon.stop()

    assert bad is None
    assert good is not None
    assert good["results"][0]["chunk_hash"] == "abc123"


def test_wrong_auth_does_not_kill_accept_loop(daemon_home) -> None:
    cfg = _cfg(_unique_collection("auth"))
    ms = FakeMemSearch()
    daemon = SearchDaemon(ms, cfg).start()
    address, family = daemon_address(cfg.milvus.collection)
    try:
        wrong = _request_with_deadline(
            address,
            family,
            b"x" * 32,
            {"op": "ping"},
            timeout_s=0.5,
            response_timeout_s=0.1,
        )
        good = daemon_request(cfg.milvus.collection, {"op": "ping"}, cfg)
    finally:
        daemon.stop()

    assert wrong is None
    assert good is not None
    assert good["collection"] == cfg.milvus.collection


def test_daemon_request_timeout_returns_none(monkeypatch, daemon_home) -> None:
    cfg = _cfg(_unique_collection("timeout"))
    key = DaemonKey(cfg.milvus.collection)
    key.ensure()

    def never_finishes(*_args, **_kwargs):
        time.sleep(10)

    monkeypatch.setattr("memsearch.daemon.Client", never_finishes)

    assert daemon_request(cfg.milvus.collection, {"op": "ping"}, cfg) is None


def test_ping_daemon_uses_explicit_short_deadline(monkeypatch) -> None:
    seen = {}

    def fake_request(address, family, key, payload, *, timeout_s, response_timeout_s):
        seen["timeout_s"] = timeout_s
        seen["response_timeout_s"] = response_timeout_s
        return {"ok": True}

    monkeypatch.setattr("memsearch.daemon._request_with_deadline", fake_request)

    assert _ping_daemon("addr", b"x" * 32, timeout_s=0.5) == {"ok": True}
    assert seen == {"timeout_s": 0.5, "response_timeout_s": 0.5}


def test_no_daemon_flag_is_not_a_config_override() -> None:
    assert "no_daemon" not in cli_module._PARAM_MAP
    assert cli_module._build_cli_overrides(no_daemon=False) == {}


def test_daemon_stop_is_bounded(daemon_home) -> None:
    cfg = _cfg(_unique_collection("stop"))
    daemon = SearchDaemon(FakeMemSearch(), cfg).start()

    start = time.monotonic()
    with suppress(Exception):
        daemon.stop()

    assert time.monotonic() - start < 2.5
