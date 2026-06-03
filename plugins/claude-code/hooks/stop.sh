#!/usr/bin/env bash
# Stop hook: parse transcript, summarize with claude -p, and save to memory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Prevent infinite loop: if this Stop was triggered by a previous Stop hook, bail out
STOP_HOOK_ACTIVE=$(_json_val "$INPUT" "stop_hook_active" "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  echo '{}'
  exit 0
fi

# Skip summarization when the required API key is missing — embedding/search
# would fail, and the session likely only contains the "key not set" warning.
_required_env_var() {
  case "$1" in
    openai) echo "OPENAI_API_KEY" ;;
    google) echo "GOOGLE_API_KEY" ;;
    voyage) echo "VOYAGE_API_KEY" ;;
    jina) echo "JINA_API_KEY" ;;
    mistral) echo "MISTRAL_API_KEY" ;;
    *) echo "" ;;  # onnx, ollama, local — no API key needed
  esac
}
_PROVIDER=$($MEMSEARCH_CMD config get embedding.provider 2>/dev/null || echo "onnx")
_REQ_KEY=$(_required_env_var "$_PROVIDER")
if [ -n "$_REQ_KEY" ] && [ -z "${!_REQ_KEY:-}" ]; then
  # Env var not set — check if API key is configured in memsearch config file
  _CONFIG_API_KEY=""
  if [ -n "$MEMSEARCH_CMD" ]; then
    _CONFIG_API_KEY=$($MEMSEARCH_CMD config get embedding.api_key 2>/dev/null || echo "")
  fi
  if [ -z "$_CONFIG_API_KEY" ]; then
    echo '{}'
    exit 0
  fi
fi

# Extract transcript path from hook input
TRANSCRIPT_PATH=$(_json_val "$INPUT" "transcript_path" "")

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  echo '{}'
  exit 0
fi

# Check if transcript is empty (< 3 lines = no real content)
LINE_COUNT=$(wc -l < "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
if [ "$LINE_COUNT" -lt 3 ]; then
  echo '{}'
  exit 0
fi

ensure_memory_dir

SUMMARIZE_ENABLED=$($MEMSEARCH_CMD config get plugins.claude-code.summarize.enabled 2>/dev/null || echo "true")
if [ "$SUMMARIZE_ENABLED" = "false" ]; then
  echo '{}'
  exit 0
fi

# Parse transcript — extract the last turn only (one user question + all responses)
PARSED=$("$SCRIPT_DIR/parse-transcript.sh" "$TRANSCRIPT_PATH" 2>/dev/null || true)

if [ -z "$PARSED" ] || [ "$PARSED" = "(empty transcript)" ] || [ "$PARSED" = "(no user message found)" ] || [ "$PARSED" = "(empty turn)" ]; then
  echo '{}'
  exit 0
fi

# Determine today's date and current time
TODAY=$(date +%Y-%m-%d)
NOW=$(date +%H:%M)
MEMORY_FILE="$MEMORY_BUCKET_DIR/$TODAY.md"

# Extract session ID and last user turn UUID for progressive disclosure anchors
SESSION_ID=$(basename "$TRANSCRIPT_PATH" .jsonl)
LAST_USER_TURN_UUID=$(python3 -c "
import json, sys
uuid = ''
with open(sys.argv[1], encoding='utf-8', errors='replace') as f:
    for line in f:
        try:
            obj = json.loads(line)
            if obj.get('type') != 'user' or obj.get('isMeta'):
                continue
            content = obj.get('message', {}).get('content')
            if isinstance(content, str) and content.strip():
                uuid = obj.get('uuid', '')
                continue
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text' and block.get('text', '').strip():
                        uuid = obj.get('uuid', '')
                        break
        except: pass
print(uuid)
" "$TRANSCRIPT_PATH" 2>/dev/null || true)

# Load summarization prompt: user custom (via config) > plugin built-in template
AGENT_NAME="Claude Code"
PROMPT_FILE=""
if [ -n "$MEMSEARCH_CMD" ]; then
  PROMPT_FILE=$($MEMSEARCH_CMD config get prompts.summarize 2>/dev/null || true)
fi
if [ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ]; then
  SYSTEM_PROMPT=$(sed "s/{{AGENT_NAME}}/$AGENT_NAME/g" "$PROMPT_FILE")
elif [ -f "${CLAUDE_PLUGIN_ROOT}/prompts/summarize.txt" ]; then
  SYSTEM_PROMPT=$(sed "s/{{AGENT_NAME}}/$AGENT_NAME/g" "${CLAUDE_PLUGIN_ROOT}/prompts/summarize.txt")
else
  SYSTEM_PROMPT="You are a third-person note-taker. Summarize the transcript as 2-6 bullet points. Write in third person. Output ONLY bullet points."
fi

# --- Crash-safety sidecar (persist-then-upgrade, phase 1) ---
# This Stop hook is async and writes the polished summary only AFTER the slow,
# killable `claude -p` below. If teardown kills the hook mid-summarize (e.g. the
# user quits right after a turn), that turn would be lost. So first persist the
# raw turn to a uniquely-named sidecar in a hidden dir. The happy path appends
# the polished summary further down and deletes this sidecar, so it never reaches
# the main file; only a killed hook leaves it for SessionStart to recover.
# Sidecars are extension-less and live in a dotted dir, so the scanner's
# hidden-dir + extension filters never index them as raw (scanner.py:22,41,58).
ENTRY_ANCHOR="<!-- session:${SESSION_ID} turn:${LAST_USER_TURN_UUID} transcript:${TRANSCRIPT_PATH} -->"
PENDING_DIR="$MEMORY_BUCKET_DIR/.pending"
mkdir -p "$PENDING_DIR" 2>/dev/null || true
WRITE_TOKEN="${SESSION_ID:-nosid}-$$-$(date +%s)"
SIDECAR="$PENDING_DIR/$WRITE_TOKEN"
{
  echo "### $NOW"
  echo "$ENTRY_ANCHOR"
  echo "$PARSED"
} > "$SIDECAR" 2>/dev/null || true

# Summarize the last turn into structured bullet points.
# Default: use claude -p with the plugin default model. A plugin-specific
# summarize model override can replace the model without changing provider
# routing.
SUMMARY=""
SUMMARIZE_PROVIDER=""
if [ -n "$MEMSEARCH_CMD" ]; then
  SUMMARIZE_PROVIDER=$($MEMSEARCH_CMD config get plugins.claude-code.summarize.provider 2>/dev/null || true)
fi

if [ -n "$SUMMARIZE_PROVIDER" ] && [ "$SUMMARIZE_PROVIDER" != "native" ] && [ -n "$MEMSEARCH_CMD" ]; then
  SUMMARY=$(printf '%s' "$PARSED" | MEMSEARCH_NO_WATCH=1 $MEMSEARCH_CMD summarize \
    --plugin claude-code \
    --agent-name "$AGENT_NAME" \
    2>/dev/null || true)
elif command -v claude &>/dev/null; then
  SUMMARIZE_MODEL="haiku"
  if [ -n "$MEMSEARCH_CMD" ]; then
    CONFIG_MODEL=$($MEMSEARCH_CMD config get plugins.claude-code.summarize.model 2>/dev/null || true)
    if [ -n "$CONFIG_MODEL" ]; then
      SUMMARIZE_MODEL="$CONFIG_MODEL"
    fi
  fi
  # Fold the observer prompt into the primary prompt to avoid the broken
  # --system-prompt + stdin path (#563), but keep delivery on stdin: claude is a
  # native Windows .exe, and a long last turn passed as an argv can exceed the
  # ~32K CreateProcess command-line limit and silently fail (windows-bash-portability).
  LLM_PROMPT="${SYSTEM_PROMPT}

Transcript:
${PARSED}"
  SUMMARY=$(printf '%s' "$LLM_PROMPT" | MEMSEARCH_NO_WATCH=1 CLAUDECODE= claude -p \
    --strict-mcp-config \
    --model "$SUMMARIZE_MODEL" \
    --no-session-persistence \
    --no-chrome \
    2>/dev/null || true)
fi

# If claude is not available or returned empty, fall back to raw parsed output
if [ -z "$SUMMARY" ]; then
  SUMMARY="$PARSED"
fi

# Append as a sub-heading under the session heading written by SessionStart
# Include HTML comment anchor for progressive disclosure (L3 transcript lookup)
#
# Cross-process lock: parallel Claude sessions (worktrees) may write the same
# date file concurrently. mkdir is atomic on POSIX and NTFS; use it as a mutex.
# 10s budget with stale-lock recovery (older than 60s = abandoned).
LOCKDIR="${MEMORY_FILE}.lock.d"
acquired=0
for _ in $(seq 1 100); do
  if mkdir "$LOCKDIR" 2>/dev/null; then
    acquired=1
    break
  fi
  # Reclaim stale lock from a crashed writer
  if [ -d "$LOCKDIR" ] && [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +1 2>/dev/null)" ]; then
    rmdir "$LOCKDIR" 2>/dev/null
  fi
  sleep 0.1
done
trap '[ "$acquired" = 1 ] && rmdir "$LOCKDIR" 2>/dev/null' EXIT
# Guard the write: on lock-acquire failure, SKIP rather than corrupt the file
# with an unsynchronised append.
if [ "$acquired" = 1 ]; then
  {
    # Lazy session heading (Issue 1): write the "## Session" grouping heading
    # once per session, and only when a real summary follows — so sessions that
    # never summarize leave no orphan empty headings. Detected via the
    # per-session anchor already in the file. (Eager heading was removed from
    # session-start.sh.)
    if [ -z "$SESSION_ID" ] || ! grep -qF "session:${SESSION_ID}" "$MEMORY_FILE" 2>/dev/null; then
      echo ""
      echo "## Session $NOW"
      echo ""
    fi
    echo "### $NOW"
    echo "$ENTRY_ANCHOR"
    echo "$SUMMARY"
    echo ""
  } >> "$MEMORY_FILE"
  # Polished summary is durable — drop the crash-safety sidecar (phase 2) so the
  # SessionStart recovery won't re-append this same turn.
  rm -f "$SIDECAR" 2>/dev/null || true
else
  echo "[memsearch] WARNING: lock not acquired after 10s, skipping memory write for $MEMORY_FILE" >&2
fi
# Release before the slow, unguarded index step; trap stays armed only for a crash mid-write.
[ "$acquired" = 1 ] && rmdir "$LOCKDIR" 2>/dev/null
trap - EXIT

# Clear any stale milvus_lite (Lite mode); the index-domain lock + --replace
# below now handle index-process serialization/takeover cross-platform.
kill_orphaned_milvus_lite

# Index immediately — don't rely on watch (which may be killed by SessionEnd before
# debounce fires). --replace takes over a hung prior indexer so per-turn capture
# can't be blocked indefinitely (server mode has no SessionStart re-index to recover it).
run_memsearch index "$MEMORY_DIR" --replace

echo '{}'
