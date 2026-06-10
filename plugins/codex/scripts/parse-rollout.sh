#!/usr/bin/env bash
# Parse a Codex CLI rollout JSONL — extract and format the LAST TURN only.
#
# A "turn" starts at the last task_started event and includes all subsequent
# messages (user question, agent responses, tool calls, tool results) until EOF.
#
# Key rollout JSONL line types:
#   event_msg + user_message   → user's text input
#   event_msg + agent_message  → agent's text output
#   response_item + function_call        → tool invocation
#   response_item + function_call_output → tool result
#   response_item + message (role=user)  → user content blocks
#   response_item + message (role=assistant) → assistant content blocks
#   event_msg + task_started   → turn boundary
#   event_msg + task_complete  → turn end
#
# Tool results are truncated to MAX_RESULT_CHARS (default 1000).
#
# Usage: bash parse-rollout.sh <rollout_path>

set -euo pipefail

ROLLOUT_PATH="${1:-}"

if [ -z "$ROLLOUT_PATH" ] || [ ! -f "$ROLLOUT_PATH" ]; then
  echo "ERROR: rollout not found: $ROLLOUT_PATH" >&2
  exit 1
fi

# Check if rollout has any content
LINE_COUNT=$(wc -l < "$ROLLOUT_PATH" 2>/dev/null || echo "0")
if [ "$LINE_COUNT" -eq 0 ]; then
  echo "(empty rollout)"
  exit 0
fi

MAX_RESULT_CHARS="${MEMSEARCH_MAX_RESULT_CHARS:-1000}"

# Prefer the interpreter common.sh verified actually runs (python3 may be the
# Windows Store stub); fall back to python3 when invoked standalone.
"${MEMSEARCH_PY:-python3}" -c '
import json, re, sys

# Force UTF-8 on stdout — Python on Windows defaults to cp1252 and crashes
# print() the first time a non-cp1252 byte appears in formatted output. That
# crash routes the traceback to stderr (discarded by stop.sh 2>/dev/null),
# returns empty stdout, and the capture pipeline silently no-ops.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_RESULT_CHARS = int(sys.argv[2])

def truncate(text, max_chars):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"

# Failure-category taxonomy — deterministic regex classifier ported from
# headroom (chopratejas/headroom, Apache-2.0; headroom/learn/_shared.py). Gives
# the summarizer a stable category per failed tool result so the corrections
# task can mine recurring failure categories. First match wins; first 2KB only.
# Kept byte-identical to the other provider parsers so one taxonomy is mined
# cross-provider. Patterns are apostrophe-free on purpose — this whole block
# runs inside a single-quoted bash string, so "." stands in for any literal quote.
_ERR_PATTERNS = [
    (re.compile(r"No such file or directory|ENOENT|FileNotFoundError|File not found|does not exist", re.I), "file_not_found"),
    (re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I), "module_not_found"),
    (re.compile(r"command not found", re.I), "command_not_found"),
    (re.compile(r"Permission denied|Access is denied|EACCES|EPERM|auto-denied|rule which prevents", re.I), "permission_denied"),
    (re.compile(r"file is too large|too many lines|exceeds.*limit", re.I), "file_too_large"),
    (re.compile(r"EISDIR|Is a directory", re.I), "is_directory"),
    (re.compile(r"SyntaxError|IndentationError", re.I), "syntax_error"),
    (re.compile(r"Traceback \(most recent|Exception:|Error:", re.I), "runtime_error"),
    (re.compile(r"timed? ?out|TimeoutError|deadline exceeded", re.I), "timeout"),
    (re.compile(r"No (?:matches|files|results) found|0 matches|[Ss]kill .* not found", re.I), "no_matches"),
    (re.compile(r"user.*reject|user.*denied|declined|didn.t want to proceed", re.I), "user_rejected"),
    (re.compile(r"[Ss]ibling tool call errored", re.I), "sibling_error"),
    (re.compile(r"exit code|non-zero|exited with", re.I), "exit_code"),
    (re.compile(r"ConnectionError|ConnectionRefused|ECONNREFUSED|network", re.I), "connection_error"),
    (re.compile(r"BUILD FAILED|compilation error|compile error", re.I), "build_failure"),
]

def classify_error(content):
    head = content[:2000]
    for pat, cat in _ERR_PATTERNS:
        if pat.search(head):
            return cat
    return "unknown"

def find_last_turn_start(lines):
    """Find the index of the last task_started event."""
    for i in range(len(lines) - 1, -1, -1):
        try:
            obj = json.loads(lines[i])
            if obj.get("type") == "event_msg":
                payload = obj.get("payload", {})
                if payload.get("type") == "task_started":
                    return i
        except Exception:
            pass
    return None

def find_last_user_message(lines):
    """Fallback: find the last user_message event."""
    for i in range(len(lines) - 1, -1, -1):
        try:
            obj = json.loads(lines[i])
            if obj.get("type") == "event_msg":
                payload = obj.get("payload", {})
                if payload.get("type") == "user_message":
                    return i
        except Exception:
            pass
    return None

def format_turn(lines):
    """Format a turn into structured text for LLM summarization."""
    output = ["=== Transcript of a conversation between a human and Codex CLI ==="]

    for raw_line in lines:
        try:
            obj = json.loads(raw_line)
        except Exception:
            continue

        line_type = obj.get("type", "")
        payload = obj.get("payload", {})

        if line_type == "event_msg":
            msg_type = payload.get("type", "")

            if msg_type == "user_message":
                message = payload.get("message", "")
                if message.strip():
                    output.append(f"[Human]: {message.strip()}")

            elif msg_type == "agent_message":
                message = payload.get("message", "")
                if message.strip():
                    output.append(f"[Codex]: {message.strip()}")

            # Skip: task_started, task_complete, token_count, agent_reasoning

        elif line_type == "response_item":
            item_type = payload.get("type", "")

            if item_type == "function_call":
                name = payload.get("name", "unknown")
                args = payload.get("arguments", "")
                # Parse arguments JSON for concise display
                try:
                    args_obj = json.loads(args) if isinstance(args, str) else args
                    if isinstance(args_obj, dict):
                        parts = []
                        for k, v in args_obj.items():
                            v_str = str(v)
                            if len(v_str) > 120:
                                v_str = v_str[:120] + "..."
                            parts.append(f"{k}={v_str}")
                        args_summary = ", ".join(parts)
                    else:
                        args_summary = str(args_obj)
                except Exception:
                    args_summary = str(args)
                if len(args_summary) > 400:
                    args_summary = args_summary[:400] + "..."
                output.append(f"[Codex calls tool]: {name}({args_summary})")

            elif item_type == "function_call_output":
                result = str(payload.get("output", ""))
                # Codex wraps shell results with a "Process exited with code N"
                # header — the only failure signal in the rollout. Non-shell tool
                # outputs carry no exit marker, so they stay unlabeled (no false
                # positives). Classify the full output, then truncate for display.
                m = re.search(r"exited with code (\d+)", result)
                if m and m.group(1) != "0":
                    label = "[Tool error: " + classify_error(result) + "]"
                else:
                    label = "[Tool output]"
                result = truncate(result, MAX_RESULT_CHARS)
                output.append(f"{label}: {result}")

            # Skip response_item "message" — duplicates event_msg user_message/agent_message.
            # Skip: reasoning, session_meta, turn_context, web_search_call

    return "\n".join(output)

# --- Main ---
rollout_path = sys.argv[1]
# Explicit UTF-8 — see stdout note above; same trap on the read side.
with open(rollout_path, encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

if not lines:
    print("(empty rollout)")
    sys.exit(0)

# Find the start of the last turn
start_idx = find_last_turn_start(lines)

# Fallback: find last user_message if no task_started found
if start_idx is None:
    start_idx = find_last_user_message(lines)

if start_idx is None:
    print("(no user message found)")
    sys.exit(0)

last_turn = lines[start_idx:]
formatted = format_turn(last_turn)

if not formatted.strip():
    print("(empty turn)")
    sys.exit(0)

print(formatted)
' "$ROLLOUT_PATH" "$MAX_RESULT_CHARS"
