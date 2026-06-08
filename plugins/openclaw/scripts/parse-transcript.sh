#!/usr/bin/env bash
# Parse OpenClaw JSONL transcript and extract the last conversation turn.
#
# OpenClaw transcript format (one JSON object per line):
#   {"type":"message","id":"...","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
#   {"type":"message","id":"...","message":{"role":"assistant","content":[{"type":"text","text":"..."},{"type":"toolCall",...}]}}
#
# Usage: parse-transcript.sh <transcript.jsonl>
# Output: formatted last turn with [Human] / [Assistant] / [Tool Call] labels

set -euo pipefail

TRANSCRIPT_FILE="${1:-}"

if [ -z "$TRANSCRIPT_FILE" ] || [ ! -f "$TRANSCRIPT_FILE" ]; then
  echo "(no transcript file)"
  exit 0
fi

# Use Python3 for reliable JSON parsing
python3 - "$TRANSCRIPT_FILE" << 'PYEOF'
import json
import re
import sys

# Force UTF-8 on stdout — Python on Windows defaults to cp1252 and crashes
# print() the first time a non-cp1252 byte appears in formatted output. The
# crash sends a traceback to stderr (discarded by the caller's 2>/dev/null),
# returns empty stdout, and the capture pipeline silently no-ops.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_RESULT_CHARS = 1000

# Failure-category taxonomy — deterministic regex classifier ported from
# headroom (chopratejas/headroom, Apache-2.0; headroom/learn/_shared.py). Gives
# the summarizer a stable category per failed tool result so the corrections
# task can mine recurring failure categories. First match wins; first 2KB only.
# Kept byte-identical to the other provider parsers so one taxonomy is mined
# cross-provider. Patterns are apostrophe-free to match the Codex copy (which
# lives in a single-quoted bash string); harmless here inside a quoted heredoc.
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

def extract_text(content):
    """Extract text from content (string or array of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "toolCall":
                    name = block.get("name", block.get("toolName", "unknown"))
                    inp = block.get("input", block.get("parameters", {}))
                    # Compact tool call representation
                    if isinstance(inp, dict):
                        preview = json.dumps(inp, ensure_ascii=False)[:200]
                    else:
                        preview = str(inp)[:200]
                    parts.append(f"[Tool Call: {name}({preview})]")
                elif block.get("type") == "toolResult":
                    # OpenClaw marks a failed tool call with a boolean is_error on
                    # the toolResult block (snake_case per OpenClaw docs — NOT
                    # isError). Gate on it so successful results whose text merely
                    # contains "Error" are never mislabeled. Classify the full text
                    # before truncating for display.
                    is_error = bool(block.get("is_error"))
                    result_text = block.get("text", block.get("content", ""))
                    if isinstance(result_text, list):
                        result_text = " ".join(
                            r.get("text", "") for r in result_text if isinstance(r, dict)
                        )
                    result_text = str(result_text)
                    label = "[Tool error: " + classify_error(result_text) + "]" if is_error else "[Tool Result]"
                    if len(result_text) > MAX_RESULT_CHARS:
                        result_text = result_text[:MAX_RESULT_CHARS] + "..."
                    parts.append(f"{label}: {result_text}")
        return "\n".join(parts)
    return str(content)


def main():
    transcript_file = sys.argv[1]

    messages = []
    # Explicit UTF-8 — see stdout note above; same trap on the read side.
    with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            if msg_type != "message":
                continue

            msg = obj.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role in ("user", "assistant"):
                messages.append({"role": role, "content": content, "id": obj.get("id", "")})

    if not messages:
        print("(empty transcript)")
        return

    # Find the last real user message (text content, not tool_result)
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, str):
                last_user_idx = i
                break
            if isinstance(content, list) and any(
                b.get("type") == "text" for b in content if isinstance(b, dict)
            ):
                last_user_idx = i
                break

    if last_user_idx == -1:
        print("(no user message found)")
        return

    # Format the last turn
    print("=== Transcript of a conversation between a human and an AI assistant ===")
    for msg in messages[last_user_idx:]:
        role = msg["role"]
        text = extract_text(msg["content"])
        if not text.strip():
            continue

        if role == "user":
            print(f"[Human]: {text}")
        elif role == "assistant":
            print(f"[Assistant]: {text}")


if __name__ == "__main__":
    main()
PYEOF
