"""Tests for the cross-platform single-writer watch lock (watchlock.py).

These exercise the REAL OS primitives (advisory lock + terminate) on the host
platform — including a genuine second process — because the entire reason this
module exists is that the previous bash reap silently no-opped on Windows.
Mock-only coverage would reproduce that failure mode.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from memsearch.watchlock import WatchLock, _terminate

_SRC = str(Path(__file__).resolve().parent.parent / "src")


@pytest.fixture(autouse=True)
def _isolated_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMSEARCH_LOCK_DIR", str(tmp_path / "locks"))


def _spawn(code: str, env_extra: dict | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def test_terminate_kills_real_process():
    """_terminate actually ends a live process on this OS."""
    proc = _spawn("import time; time.sleep(60)")
    try:
        assert proc.poll() is None  # alive
        _terminate(proc.pid)
        proc.wait(timeout=10)
        assert proc.poll() is not None  # dead
    finally:
        if proc.poll() is None:
            proc.kill()


def test_acquire_then_refuse_then_reclaim_in_process():
    """Held lock refuses a second acquirer; after release it is reclaimable."""
    first = WatchLock("col_a")
    assert first.acquire() is True

    second = WatchLock("col_a")
    assert second.acquire(replace=False) is False  # live holder -> refuse

    first.release()

    third = WatchLock("col_a")
    assert third.acquire(replace=False) is True  # holder gone -> reclaim
    third.release()


def test_distinct_collections_do_not_contend():
    a = WatchLock("col_x")
    b = WatchLock("col_y")
    assert a.acquire() is True
    assert b.acquire() is True  # different collection, independent lock
    a.release()
    b.release()


_HOLDER = (
    "import os, time\n"
    "from memsearch.watchlock import WatchLock\n"
    "lock = WatchLock(os.environ['TESTCOL'], domain=os.environ.get('TESTDOMAIN', 'watch'))\n"
    "print('ACQUIRED' if lock.acquire(replace=False) else 'FAILED', flush=True)\n"
    "time.sleep(60)\n"
)


def test_watch_command_refuses_without_constructing_memsearch(tmp_path, monkeypatch):
    """The `watch` command acquires the lock BEFORE building MemSearch and, when
    refused, returns cleanly (exit 0) without opening Milvus / hitting the watch
    loop. Proves the command wiring, not just the lock primitive — no Milvus."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    def _boom(*_a, **_k):
        raise AssertionError("MemSearch must not be constructed while the lock is held")

    monkeypatch.setattr(core, "MemSearch", _boom)

    held = WatchLock("testcol_cli")
    assert held.acquire() is True
    try:
        watchdir = tmp_path / "mem"
        watchdir.mkdir()
        result = CliRunner().invoke(cli, ["watch", str(watchdir), "--collection", "testcol_cli"])
        assert result.exit_code == 0
        assert result.exception is None  # _boom never raised -> body never reached -> no hang
    finally:
        held.release()


def test_index_command_skips_when_collection_locked(tmp_path, monkeypatch):
    """`index` shares the per-collection lock: if a writer holds the collection,
    it skips (exit 0) without constructing MemSearch — refuse is immediate, so
    this is fast (no wait)."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    def _boom(*_a, **_k):
        raise AssertionError("MemSearch must not be constructed while the collection is locked")

    monkeypatch.setattr(core, "MemSearch", _boom)

    held = WatchLock("testcol_idx", domain="index")  # same domain the index command uses
    assert held.acquire() is True
    try:
        srcdir = tmp_path / "src"
        srcdir.mkdir()
        result = CliRunner().invoke(cli, ["index", str(srcdir), "--collection", "testcol_idx"])
        assert result.exit_code == 0
        assert result.exception is None
    finally:
        held.release()


def test_watch_lock_does_not_block_index(tmp_path, monkeypatch):
    """The fix: a live watcher (watch-domain lock held) does NOT block a manual
    `index` (index-domain). The index command proceeds past the guard and runs."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    constructed = {"n": 0}

    class _FakeMS:
        def __init__(self, *_a, **_k):
            constructed["n"] += 1

        async def index(self, *, force=False):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(core, "MemSearch", _FakeMS)

    watcher_lock = WatchLock("shared_col", domain="watch")  # simulate a live watcher
    assert watcher_lock.acquire() is True
    try:
        srcdir = tmp_path / "src"
        srcdir.mkdir()
        result = CliRunner().invoke(cli, ["index", str(srcdir), "--collection", "shared_col"])
        assert result.exit_code == 0, result.output
        assert constructed["n"] == 1  # index ran — the watch lock did not block it
    finally:
        watcher_lock.release()


def test_index_replace_takes_over_stuck_index(tmp_path, monkeypatch):
    """`memsearch index --replace` terminates a stuck index-domain holder
    (cross-platform) and takes over — the self-heal for the index residual."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    col = "col_idx_replace"
    proc = _spawn(
        _HOLDER,
        env_extra={"TESTCOL": col, "TESTDOMAIN": "index", "MEMSEARCH_LOCK_DIR": os.environ["MEMSEARCH_LOCK_DIR"]},
    )
    ran = {"n": 0}

    class _FakeMS:
        def __init__(self, *_a, **_k):
            ran["n"] += 1

        async def index(self, *, force=False):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(core, "MemSearch", _FakeMS)
    try:
        assert proc.stdout.readline().strip() == "ACQUIRED"
        srcdir = tmp_path / "src"
        srcdir.mkdir()
        result = CliRunner().invoke(cli, ["index", str(srcdir), "--collection", col, "--replace"])
        assert result.exit_code == 0, result.output
        assert ran["n"] == 1  # took over and ran
        proc.wait(timeout=10)
        assert proc.poll() is not None  # stuck holder terminated
    finally:
        if proc.poll() is None:
            proc.kill()


def test_replace_reprobes_before_terminating(monkeypatch):
    """If the holder exits between the failed probe and the takeover, --replace
    must re-acquire via a second probe WITHOUT terminating the recorded PID
    (which could by then belong to a recycled, unrelated process)."""
    import memsearch.watchlock as wl

    real_try_lock = wl._try_lock
    state = {"calls": 0}

    def flaky_try_lock(fd: int) -> bool:
        state["calls"] += 1
        if state["calls"] == 1:
            return False  # simulate a live holder at the first probe
        return real_try_lock(fd)  # holder gone by the re-probe

    killed: list[int] = []
    monkeypatch.setattr(wl, "_try_lock", flaky_try_lock)
    monkeypatch.setattr(wl, "_terminate", lambda pid: killed.append(pid))

    lock = wl.WatchLock("col_reprobe")
    # Leave a recorded PID behind, as a previous holder would.
    lock._pid_path.write_text("123456", encoding="utf-8")

    assert lock.acquire(replace=True) is True
    assert killed == []  # re-probe succeeded -> nothing terminated
    lock.release()


def test_watch_stop_terminates_running_watcher(monkeypatch):
    """`watch --stop` terminates the live watcher for the collection, leaves the
    lock free, and never starts a new watcher (MemSearch not constructed)."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    def _boom(*_a, **_k):
        raise AssertionError("MemSearch must not be constructed by watch --stop")

    monkeypatch.setattr(core, "MemSearch", _boom)

    col = "col_stop"
    proc = _spawn(_HOLDER, env_extra={"TESTCOL": col, "MEMSEARCH_LOCK_DIR": os.environ["MEMSEARCH_LOCK_DIR"]})
    try:
        assert proc.stdout.readline().strip() == "ACQUIRED"
        result = CliRunner().invoke(cli, ["watch", "--stop", "--collection", col])
        assert result.exit_code == 0, result.output
        assert result.exception is None
        proc.wait(timeout=10)
        assert proc.poll() is not None  # watcher terminated
        # The lock was released again — no new watcher left holding it.
        free = WatchLock(col)
        assert free.acquire(replace=False) is True
        free.release()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_watch_stop_exits_zero_when_no_watcher(monkeypatch):
    """`watch --stop` with no running watcher is a silent no-op (exit 0)."""
    from click.testing import CliRunner

    import memsearch.core as core
    from memsearch.cli import cli

    def _boom(*_a, **_k):
        raise AssertionError("MemSearch must not be constructed by watch --stop")

    monkeypatch.setattr(core, "MemSearch", _boom)

    result = CliRunner().invoke(cli, ["watch", "--stop", "--collection", "col_stop_idle"])
    assert result.exit_code == 0, result.output
    assert result.output == ""

    # The lock is free afterwards.
    free = WatchLock("col_stop_idle")
    assert free.acquire(replace=False) is True
    free.release()


def test_watch_without_paths_or_stop_is_usage_error():
    """Plain `watch` with no PATHS still errors (paths only optional for --stop)."""
    from click.testing import CliRunner

    from memsearch.cli import cli

    result = CliRunner().invoke(cli, ["watch", "--collection", "col_no_paths"])
    assert result.exit_code == 2
    assert "PATHS" in result.output


def test_cross_process_refuse_and_replace(tmp_path):
    """A real second process holds the lock; the parent refuses without
    --replace and, with --replace, terminates the holder and takes over."""
    col = "col_xproc"
    proc = _spawn(_HOLDER, env_extra={"TESTCOL": col, "MEMSEARCH_LOCK_DIR": os.environ["MEMSEARCH_LOCK_DIR"]})
    try:
        assert proc.stdout.readline().strip() == "ACQUIRED"

        # Without replace: parent must back off.
        assert WatchLock(col).acquire(replace=False) is False
        assert proc.poll() is None  # holder untouched

        # With replace: parent kills the holder and acquires.
        taker = WatchLock(col)
        assert taker.acquire(replace=True, wait_seconds=10.0) is True
        proc.wait(timeout=10)
        assert proc.poll() is not None  # holder was terminated
        taker.release()
    finally:
        if proc.poll() is None:
            proc.kill()
