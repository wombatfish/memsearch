#!/usr/bin/env python3
"""Minimal MCP stdio server exposing memsearch to harnesses without a Bash tool.

Some Claude Code embeddings (e.g. LINQPad's) ship no Bash tool, so the
memory-recall skill cannot shell out to the memsearch CLI. This server wraps
the CLI as MCP tools instead: the harness spawns this process directly and
the model calls memory_search / memory_expand / memory_collection_name like
any other MCP tool. stdlib-only; the CLI keeps owning config resolution,
daemon routing, and output encoding.

Transport: newline-delimited JSON-RPC 2.0 over stdio (standard MCP stdio).
Registered via .mcp.json at the plugin root (${CLAUDE_PLUGIN_ROOT} substituted
by the harness).
"""

import json
import os
import re
import shutil
import subprocess
import sys

PROTOCOL_VERSION = "2024-11-05"
# Version of this MCP wrapper only - intentionally NOT the memsearch package
# version. This repo is a fork of upstream memsearch; upstream owns the
# package/plugin version numbers, so local additions must never claim one.
SERVER_VERSION = "0.1.0"

SEARCH_TIMEOUT = 300  # first search may load the embedder; daemon path is fast
OTHER_TIMEOUT = 60

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_cli_cmd: list[str] | None = None
_default_collection: str | None = None


def _resolve_cli() -> list[str]:
    """Locate the memsearch CLI once: env override, PATH, ~/.local/bin, uvx."""
    global _cli_cmd
    if _cli_cmd is not None:
        return _cli_cmd
    override = os.environ.get("MEMSEARCH_CLI")
    if override:
        _cli_cmd = [override]
        return _cli_cmd
    found = shutil.which("memsearch")
    if found:
        _cli_cmd = [found]
        return _cli_cmd
    for name in ("memsearch.exe", "memsearch"):
        cand = os.path.join(os.path.expanduser("~"), ".local", "bin", name)
        if os.path.isfile(cand):
            _cli_cmd = [cand]
            return _cli_cmd
    _cli_cmd = ["uvx", "memsearch"]  # last resort; uv ships with memsearch installs
    return _cli_cmd


def _run_cli(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        _resolve_cli() + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )


def _project_dir() -> str:
    """Mirror the memory-recall skill's chain: MEMSEARCH_DIR > git root > cwd."""
    env_dir = os.environ.get("MEMSEARCH_DIR")
    if env_dir:
        return env_dir
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.getcwd()


def _derive_collection() -> str:
    """Default collection via `memsearch collection-name` (hash-parity with hooks)."""
    global _default_collection
    if _default_collection is None:
        proc = _run_cli(["collection-name", _project_dir()], OTHER_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError(
                f"collection-name failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        _default_collection = proc.stdout.strip()
    return _default_collection


# ---- tools -----------------------------------------------------------------

TOOLS = [
    {
        "name": "memory_search",
        "description": (
            "Semantic search over memsearch memory (Milvus vector + BM25 + rerank). "
            "Pass 1-3 query variants (semantic / keyword-heavy / temporal) of ONE "
            "intent; the CLI unions, dedups, and reranks them into a single result "
            "set. Returns compact JSON per result: chunk_hash, score, date, source, "
            "heading, preview. Follow up with memory_expand on the best 3-5 hashes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                    "description": "1-3 variants of one search intent.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 15,
                    "description": "Number of results (default 15).",
                },
                "collection": {
                    "type": "string",
                    "description": "Milvus collection; omit to derive from the project (MEMSEARCH_DIR > git root > cwd).",
                },
                "source_prefix": {
                    "type": "string",
                    "description": "Only search chunks whose source path starts with this prefix.",
                },
            },
            "required": ["queries"],
        },
    },
    {
        "name": "memory_expand",
        "description": (
            "Expand one memory chunk (by chunk_hash from memory_search) to its full "
            "heading section, including any transcript anchors for deep-drill."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_hash": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{1,64}$",
                    "description": "Hash from a memory_search result.",
                },
                "collection": {
                    "type": "string",
                    "description": "Milvus collection; omit to derive from the project.",
                },
            },
            "required": ["chunk_hash"],
        },
    },
    {
        "name": "memory_collection_name",
        "description": (
            "Derive the Milvus collection name for a project directory (or the "
            "current project when omitted). Rarely needed: memory_search and "
            "memory_expand already derive it when collection is omitted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Project directory; omit for MEMSEARCH_DIR > git root > cwd.",
                }
            },
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tool_memory_search(args: dict) -> dict:
    queries = args.get("queries")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        return _text_result("queries must be a non-empty array of non-empty strings", True)
    top_k = args.get("top_k") or 15
    collection = args.get("collection") or _derive_collection()

    cli_args = [
        "search", *queries,
        "--top-k", str(top_k),
        "--compact-output", "--json-output",
        "--consistency", "Strong",
        "--collection", collection,
    ]
    if args.get("source_prefix"):
        cli_args += ["--source-prefix", args["source_prefix"]]
    proc = _run_cli(cli_args, SEARCH_TIMEOUT)

    # Older binaries: no multi-query / --compact-output. Retry minimal, single query.
    if proc.returncode != 0 and (
        "Got unexpected extra argument" in proc.stderr or "No such option: --compact-output" in proc.stderr
    ):
        proc = _run_cli(
            ["search", queries[0], "--top-k", "5", "--json-output",
             "--consistency", "Strong", "--collection", collection],
            SEARCH_TIMEOUT,
        )

    if proc.returncode != 0:
        return _text_result(f"memsearch search failed:\n{proc.stderr.strip() or proc.stdout.strip()}", True)
    return _text_result(proc.stdout.strip() or "[]")


def _tool_memory_expand(args: dict) -> dict:
    chunk_hash = args.get("chunk_hash") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{1,64}", chunk_hash):
        return _text_result(f"Invalid chunk hash: {chunk_hash!r}", True)
    collection = args.get("collection") or _derive_collection()
    proc = _run_cli(
        ["expand", chunk_hash, "--json-output", "--collection", collection],
        SEARCH_TIMEOUT,
    )
    if proc.returncode != 0:
        return _text_result(f"memsearch expand failed:\n{proc.stderr.strip() or proc.stdout.strip()}", True)
    return _text_result(proc.stdout.strip())


def _tool_memory_collection_name(args: dict) -> dict:
    path = args.get("path") or _project_dir()
    proc = _run_cli(["collection-name", path], OTHER_TIMEOUT)
    if proc.returncode != 0:
        return _text_result(f"collection-name failed:\n{proc.stderr.strip() or proc.stdout.strip()}", True)
    return _text_result(proc.stdout.strip())


_TOOL_HANDLERS = {
    "memory_search": _tool_memory_search,
    "memory_expand": _tool_memory_expand,
    "memory_collection_name": _tool_memory_collection_name,
}


# ---- JSON-RPC plumbing -----------------------------------------------------

def _handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        client_version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memsearch", "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        handler = _TOOL_HANDLERS.get(params.get("name"))
        if handler is None:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {params.get('name')!r}"},
            }
        try:
            result = handler(params.get("arguments") or {})
        except subprocess.TimeoutExpired:
            result = _text_result("memsearch CLI timed out", True)
        except Exception as e:  # tool failures are results, not protocol errors
            result = _text_result(f"{type(e).__name__}: {e}", True)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method and method.startswith("notifications/"):
        return None
    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def main() -> None:
    # Windows defaults std streams to cp1252 and \r\n newlines; MCP stdio wants
    # UTF-8, one JSON message per \n-terminated line.
    for stream, kwargs in ((sys.stdin, {}), (sys.stdout, {"newline": "\n"})):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", **kwargs)
        except (AttributeError, OSError):
            pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = _handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
