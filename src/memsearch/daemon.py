"""Local IPC search daemon hosted by the persistent watch process."""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import multiprocessing
import os
import queue
import stat
import subprocess
import sys
import threading
from contextlib import suppress
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

from .core import _build_expand_result
from .watchlock import _safe_name

logger = logging.getLogger(__name__)

SUPPORTED_DAEMON_PROVIDERS = frozenset({"onnx", "local"})
MAX_MESSAGE_BYTES = 1_048_576


def _daemon_dir() -> Path:
    d = Path("~/.memsearch").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def daemon_address(collection: str) -> tuple[str, str]:
    safe = _safe_name(collection)
    if sys.platform == "win32":
        return rf"\\.\pipe\memsearch-{safe}", "AF_PIPE"
    return str((_daemon_dir() / f"daemon-{safe}.sock").expanduser()), "AF_UNIX"


class DaemonKey:
    """Collection-scoped authkey lifecycle for the local IPC daemon."""

    def __init__(self, collection: str) -> None:
        self.collection = collection
        self.path = _daemon_dir() / f"daemon-{_safe_name(collection)}.key"

    def read(self) -> bytes | None:
        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        return data if len(data) == 32 else None

    def ensure(self) -> bytes:
        try:
            return self._create_atomic()
        except FileExistsError as exc:
            if self._validate_existing():
                data = self.read()
                if data is not None:
                    return data
            self._replace()
            data = self.read()
            if data is None:
                raise OSError(f"Invalid daemon authkey at {self.path}") from exc
            return data

    def _create_atomic(self) -> bytes:
        data = os.urandom(32)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(self.path, flags, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        self._harden_permissions()
        return data

    def _replace(self) -> None:
        tmp = self.path.with_suffix(".key.tmp")
        data = os.urandom(32)
        flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(tmp, flags, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        self._harden_permissions()

    def _validate_existing(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            return False
        if st.st_size != 32:
            return False
        if os.name != "nt":
            if st.st_uid != os.getuid():
                return False
            return stat.S_IMODE(st.st_mode) == 0o600
        return True

    def _harden_permissions(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            logger.warning("could not chmod daemon key %s: %s", self.path, exc)
        if os.name == "nt":
            self._harden_windows_acl()

    def _harden_windows_acl(self) -> None:
        user = _current_windows_principal()
        if not user:
            logger.warning("could not resolve current user for daemon key ACL")
            return
        cmd = [
            "icacls",
            str(self.path),
            "/inheritance:r",
            "/grant:r",
            f"{user}:F",
            "*S-1-5-18:F",
            "*S-1-5-32-544:F",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags, check=False)
        except OSError as exc:
            logger.warning("could not apply daemon key ACL: %s", exc)
            return
        if result.returncode != 0:
            logger.warning("icacls failed for daemon key %s: %s", self.path, result.stderr.strip())


def _current_windows_principal() -> str:
    domain = os.environ.get("USERDOMAIN")
    username = os.environ.get("USERNAME")
    if domain and username:
        return f"{domain}\\{username}"
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


class SearchDaemon:
    """Search/expand IPC server sharing the watch process's warm ``MemSearch``."""

    def __init__(self, ms: Any, cfg: Any) -> None:
        self.ms = ms
        self.cfg = cfg
        self.address, self.family = daemon_address(cfg.milvus.collection)
        self.key_helper = DaemonKey(cfg.milvus.collection)
        self._key: bytes | None = None
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = threading.Event()

    def start(self) -> SearchDaemon:
        if self.cfg.embedding.provider not in SUPPORTED_DAEMON_PROVIDERS:
            logger.info("search daemon disabled for provider %s", self.cfg.embedding.provider)
            return self
        try:
            self._key = self.key_helper.ensure()
            if self.family == "AF_UNIX":
                self._unlink_stale_socket()
            self._listener = Listener(self.address, family=self.family, authkey=self._key)
            self._loop = asyncio.new_event_loop()
            if self.cfg.daemon.warmup and self.cfg.reranker.model:
                self._warm_reranker()
            self._thread = threading.Thread(target=self._accept_loop, name="memsearch-search-daemon", daemon=True)
            self._thread.start()
            logger.info("search daemon started for collection %s", self.cfg.milvus.collection)
        except Exception:
            logger.exception("search daemon failed to start; continuing without daemon acceleration")
            self.stop()
        return self

    def _unlink_stale_socket(self) -> None:
        path = Path(self.address)
        if not path.exists():
            return
        key = self.key_helper.read()
        if key is not None and _ping_daemon(self.address, key, timeout_s=0.5) is not None:
            raise OSError(f"daemon socket already in use: {self.address}")
        path.unlink(missing_ok=True)

    def _warm_reranker(self) -> None:
        from .reranker import rerank

        rerank("warmup", [{"content": "x"}], model_name=self.cfg.reranker.model)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            while not self._stopping.is_set():
                try:
                    conn = self._listener.accept()
                except multiprocessing.AuthenticationError as e:
                    logger.warning("daemon authentication failed: %s", e)
                    continue
                except Exception as e:
                    if self._stopping.is_set():
                        break
                    logger.warning("daemon accept failed: %s", e)
                    continue

                if self._stopping.is_set():
                    conn.close()
                    break

                try:
                    if not conn.poll(1.0):
                        logger.debug("daemon client connected but sent no request")
                        conn.close()
                        continue
                    raw = conn.recv_bytes(maxlength=MAX_MESSAGE_BYTES)
                    req = json.loads(raw)
                    resp = self._dispatch(req)
                except (EOFError, OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as e:
                    logger.warning("daemon handler error: %s", e)
                    resp = {"ok": False, "error": str(e)}
                except Exception as e:
                    logger.exception("daemon unexpected handler error")
                    resp = {"ok": False, "error": str(e)}

                try:
                    conn.send_bytes(json.dumps(resp).encode("utf-8"))
                except Exception as e:
                    logger.warning("daemon send failed: %s", e)
                finally:
                    conn.close()
        finally:
            asyncio.set_event_loop(None)
            self._loop.close()

    def _dispatch(self, req: Any) -> dict[str, Any]:
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
        op = req.get("op")
        if op == "ping":
            return {
                "ok": True,
                "pid": os.getpid(),
                "collection": self.cfg.milvus.collection,
                "provider": self.cfg.embedding.provider,
            }
        if op == "search":
            queries = req.get("queries")
            if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
                raise ValueError("search requires a list of string queries")
            top_k = int(req.get("top_k", 5))
            source_prefix = req.get("source_prefix")
            if source_prefix is not None and not isinstance(source_prefix, str):
                raise ValueError("source_prefix must be a string or null")
            with self.ms.write_lock:
                assert self._loop is not None
                results = self._loop.run_until_complete(
                    self.ms.search_many(queries, top_k=top_k, source_prefix=source_prefix)
                )
            return {"ok": True, "results": results}
        if op == "expand":
            chunk_hash = req.get("chunk_hash")
            if not isinstance(chunk_hash, str):
                raise ValueError("expand requires chunk_hash")
            with self.ms.write_lock:
                result = _build_expand_result(
                    self.ms.store,
                    chunk_hash,
                    section=bool(req.get("section", True)),
                    lines=req.get("lines"),
                    cfg=self.cfg,
                )
            return {"ok": True, **result}
        raise ValueError(f"unknown daemon op: {op!r}")

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            if self._key is not None:
                with suppress(Exception):
                    conn = Client(self.address, family=self.family, authkey=self._key)
                    conn.close()
            with suppress(Exception):
                self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.family == "AF_UNIX":
            with suppress(OSError):
                Path(self.address).unlink(missing_ok=True)


def daemon_request(collection: str, payload: dict[str, Any], cfg: Any) -> dict[str, Any] | None:
    if cfg.embedding.provider not in SUPPORTED_DAEMON_PROVIDERS:
        return None
    key = DaemonKey(collection).read()
    if key is None:
        logger.debug("daemon key missing for collection %s", collection)
        return None
    address, family = daemon_address(collection)
    timeout_s = max(0.01, float(cfg.daemon.connect_timeout_s) + float(cfg.daemon.request_timeout_s))
    resp = _request_with_deadline(
        address,
        family,
        key,
        payload,
        timeout_s=timeout_s,
        response_timeout_s=float(cfg.daemon.request_timeout_s),
    )
    if resp is None or not resp.get("ok"):
        return None
    return resp


def _ping_daemon(address: str, key: bytes, *, timeout_s: float = 0.5) -> dict[str, Any] | None:
    family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
    resp = _request_with_deadline(
        address,
        family,
        key,
        {"op": "ping"},
        timeout_s=timeout_s,
        response_timeout_s=timeout_s,
    )
    if resp is None or not resp.get("ok"):
        return None
    return resp


def ping_daemon(collection: str, *, timeout_s: float = 0.5) -> dict[str, Any] | None:
    key = DaemonKey(collection).read()
    if key is None:
        return None
    address, _family = daemon_address(collection)
    return _ping_daemon(address, key, timeout_s=timeout_s)


def _request_with_deadline(
    address: str,
    family: str,
    key: bytes,
    payload: dict[str, Any],
    *,
    timeout_s: float,
    response_timeout_s: float,
) -> dict[str, Any] | None:
    q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _worker() -> None:
        conn = None
        try:
            conn = Client(address, family=family, authkey=key)
            conn.send_bytes(json.dumps(payload).encode("utf-8"))
            if not conn.poll(response_timeout_s):
                _put_result(q, None)
                return
            raw = conn.recv_bytes(maxlength=MAX_MESSAGE_BYTES)
            result = json.loads(raw)
            _put_result(q, result if isinstance(result, dict) else None)
        except Exception as e:
            logger.debug("daemon request failed: %s", e)
            _put_result(q, None)
        finally:
            if conn is not None:
                with suppress(Exception):
                    conn.close()

    t = threading.Thread(target=_worker, name="memsearch-daemon-client", daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        logger.debug("daemon request timed out after %.2fs", timeout_s)
        return None
    try:
        return q.get_nowait()
    except queue.Empty:
        return None


def _put_result(q: queue.Queue[dict[str, Any] | None], result: dict[str, Any] | None) -> None:
    with suppress(queue.Full):
        q.put_nowait(result)
