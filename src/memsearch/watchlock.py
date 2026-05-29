"""Cross-platform single-writer lock for ``memsearch`` indexers.

Concurrent writers on the same collection thrash the index: each computes the
other's chunks as stale (especially under divergent embedding models, which
change the ``model`` segment of every chunk's primary key) and re-inserts them,
producing duplicate rows / tombstone churn. The historical fix relied on a bash
``pgrep``/``kill`` sweep in the SessionStart hook — which silently no-ops on
Windows (no ``pgrep``; MSYS ``kill`` and the bash ``$!`` pidfile do not map to
the native ``memsearch.exe`` PID), so orphaned writers accumulated there.

This moves the guarantee into the cross-platform CLI. It uses an OS advisory
lock (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows) held on an open
fd for the writer's whole lifetime. The kernel drops the lock when the holder
dies — so "lock is held" reliably means "a live writer exists", with no PID
liveness probing, no stale-file cleanup, and no PID-reuse hazard. The PID is
recorded in a sidecar file ONLY so a ``--replace`` caller can terminate the
incumbent; it is read for killing exclusively when the lock is confirmed held,
which guarantees the recorded PID is the live holder (never a recycled PID).

The lock key is ``(domain, collection)``. ``watch`` and ``index`` use distinct
domains so each serializes only within its own kind and they never cross-block
(a manual ``index`` runs fine while a watcher is live).
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import time
from pathlib import Path


def _locks_dir() -> Path:
    # MEMSEARCH_LOCK_DIR lets tests isolate lock state (and cross-process tests
    # share a dir) without touching the user's real ~/.memsearch/locks.
    override = os.environ.get("MEMSEARCH_LOCK_DIR")
    d = Path(override) if override else Path.home() / ".memsearch" / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(collection: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", collection or "default")[:80]


def _try_lock(fd: int) -> bool:
    """Non-blocking exclusive advisory lock on ``fd``. True iff acquired."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


def _terminate(pid: int) -> None:
    """Best-effort terminate by PID. On Windows ``SIGTERM`` maps to
    ``TerminateProcess``; on POSIX we signal the single process (NOT the group:
    the nohup-launched watcher is not a group leader, so ``killpg`` could hit
    the launching shell)."""
    with contextlib.suppress(OSError):  # ProcessLookupError is an OSError subclass
        os.kill(pid, signal.SIGTERM)


class WatchLock:
    """Single-writer lock keyed by Milvus collection name."""

    def __init__(self, collection: str, *, domain: str = "watch") -> None:
        # The lock key is (domain, collection). `watch` and `index` use separate
        # domains so they serialize within their own kind (watch-vs-watch,
        # index-vs-index) without cross-blocking — a manual `index` can run while
        # a watcher is live. Same-domain, same-collection callers contend.
        base = _locks_dir() / f"{domain}-{_safe_name(collection)}"
        self.collection = collection
        self.domain = domain
        self._lock_path = base.with_suffix(".lock")
        self._pid_path = base.with_suffix(".pid")
        self._fd: int | None = None

    def acquire(self, *, replace: bool = False, wait_seconds: float = 5.0) -> bool:
        """Acquire the lock. Returns True on success, False if a live watcher
        holds it and ``replace`` is False (or the incumbent could not be
        displaced within ``wait_seconds``)."""
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        if _try_lock(fd):
            self._fd = fd
            self._write_pid()
            return True

        if not replace:
            os.close(fd)
            return False

        # Lock held by a live process -> the recorded PID is that live holder.
        incumbent = self._read_pid()
        if incumbent and incumbent != os.getpid():
            _terminate(incumbent)

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if _try_lock(fd):
                self._fd = fd
                self._write_pid()
                return True
            time.sleep(0.1)

        os.close(fd)
        return False

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None
        # Best-effort sidecar cleanup; lingering files are harmless (the OS lock,
        # not the files, is the source of truth).
        for p in (self._pid_path, self._lock_path):
            with contextlib.suppress(OSError):
                p.unlink()

    def __enter__(self) -> WatchLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    # -- internals --

    def _write_pid(self) -> None:
        # Written immediately after acquiring, before any slow work, so the
        # acquire->write window a --replace caller could observe is sub-ms.
        self._pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _read_pid(self) -> int | None:
        try:
            return int(self._pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
