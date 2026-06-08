"""Tests for the cross-provider failure-category classifier.

The classifier (`classify_error` + `_ERR_PATTERNS`) is ported verbatim into all
four provider transcript parsers so the corrections maintenance task mines ONE
stable taxonomy across providers. These tests pin both that invariant (the
pattern table is byte-identical everywhere) and the per-category behaviour, plus
the OpenCode error/shell-exit gate that decides when classification fires.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

# The four parsers that must share the taxonomy. OpenCode keeps the canonical
# importable copy in opencode_turns.py; the other three embed it in a parser
# (bash-wrapped Python for codex/openclaw, here a plain module).
_PARSER_FILES = [
    Path("plugins/claude-code/hooks/parse-transcript.sh"),
    Path("plugins/codex/scripts/parse-rollout.sh"),
    Path("plugins/openclaw/scripts/parse-transcript.sh"),
    Path("plugins/opencode/scripts/opencode_turns.py"),
]


def _extract_pattern_block(path: Path) -> str:
    """Return the `_ERR_PATTERNS = [ ... ]` list literal text from a parser."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("_ERR_PATTERNS = ["))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "]")
    return "\n".join(lines[start : end + 1])


def _load_opencode_turns():
    # Import normally (not via a synthetic spec name) so the @dataclass
    # forward-ref annotations resolve — dataclasses looks up cls.__module__ in
    # sys.modules, which a spec_from_file_location alias name would not populate.
    script_dir = Path("plugins/opencode/scripts").resolve()
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import opencode_turns  # noqa: E402

    return opencode_turns


def test_taxonomy_is_byte_identical_across_all_parsers() -> None:
    """Cross-provider mining depends on one shared taxonomy — not per-provider
    drift. The _ERR_PATTERNS list literal must be identical in every parser."""
    blocks = {str(p): _extract_pattern_block(p) for p in _PARSER_FILES}
    reference = blocks[str(_PARSER_FILES[0])]
    for name, block in blocks.items():
        assert block == reference, f"taxonomy drifted in {name}"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("No such file or directory", "file_not_found"),
        ("File not found: C:/x/hello.md", "file_not_found"),  # OpenCode/Windows phrasing
        ("does not exist", "file_not_found"),
        ("ModuleNotFoundError: No module named foo", "module_not_found"),
        ("bash: frobnicate: command not found", "command_not_found"),
        ("Permission denied", "permission_denied"),
        ("rg: x: Access is denied.", "permission_denied"),  # Windows phrasing
        ("The user has specified a rule which prevents this tool call", "permission_denied"),
        ('Skill "error-ecosystem" not found. Available skills: ...', "no_matches"),
        ("No matches found", "no_matches"),
        ("the user declined to proceed", "user_rejected"),
        ("Process exited with code 1", "exit_code"),
        ("ECONNREFUSED 127.0.0.1:5432", "connection_error"),
        ("BUILD FAILED", "build_failure"),
        ("totally fine output", "unknown"),
        ("invalid ripgrep output", "unknown"),
    ],
)
def test_classify_error_categories(text: str, expected: str) -> None:
    module = _load_opencode_turns()
    assert module.classify_error(text) == expected


def _insert_user(conn: sqlite3.Connection, mid: str, sid: str, t: int, text: str) -> None:
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (mid, sid, t, t, json.dumps({"role": "user", "time": {"created": t}, "finish": "stop"})),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        (f"{mid}-p1", mid, sid, t, t, json.dumps({"type": "text", "text": text})),
    )


def _insert_assistant_tools(conn, mid: str, sid: str, t: int, parent: str, tool_parts: list[dict]) -> None:
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (mid, sid, t, t, json.dumps({"role": "assistant", "parentID": parent, "time": {"created": t}, "finish": "stop"})),
    )
    for i, part in enumerate(tool_parts):
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
            (f"{mid}-t{i}", mid, sid, t + i, t + i, json.dumps(part)),
        )


def test_opencode_error_gate_labels_failures_only(tmp_path: Path, monkeypatch) -> None:
    """OpenCode failures classify; successes whose output merely contains 'Error'
    do not. status=='error' and completed-with-nonzero-metadata.exit both fire."""
    module = _load_opencode_turns()
    pt_dir = Path("plugins/opencode/scripts").resolve()
    spec = importlib.util.spec_from_file_location("opencode_parse_transcript_ut", pt_dir / "parse-transcript.py")
    parse_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parse_mod)

    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    sid = "ses_gate"
    _insert_user(conn, "u1", sid, 100, "do it")
    _insert_assistant_tools(
        conn, "a1", sid, 110, "u1",
        [
            {"type": "tool", "tool": "read", "state": {"status": "error", "error": "File not found: x.md", "input": {}}},
            {"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "x"}, "output": "Permission denied", "metadata": {"exit": 1}}},
            {"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "y"}, "output": "Error: this is fine", "metadata": {"exit": 0}}},
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(parse_mod, "get_db_path", lambda: str(db_path))
    out = io.StringIO()
    with redirect_stdout(out):
        parse_mod.parse_session(sid, limit=5)
    text = out.getvalue()

    assert "[Tool error: file_not_found]" in text          # status==error
    assert "[Tool error: permission_denied]" in text       # completed, exit!=0
    assert "(exit 1)" in text
    assert text.count("[Tool error:") == 2                  # the exit-0 success is NOT an error
    assert "Error: this is fine" in text                    # success output still shown, just unlabeled
