#!/usr/bin/env bash
# SessionStart hook: start watch singleton + inject recent memory context.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Redirect stdin to /dev/null before sourcing common.sh.
# common.sh does INPUT="$(cat)" which blocks indefinitely if stdin never
# receives EOF (e.g. during `claude --resume` or any new session start on
# macOS where Claude Code keeps the pipe open). session-start.sh never uses
# INPUT, so it is safe to discard stdin entirely here.
exec < /dev/null
source "$SCRIPT_DIR/common.sh"

# Bootstrap: if memsearch not available, install uv and warm up uvx cache
if [ -z "$MEMSEARCH_CMD" ]; then
  if ! command -v uvx &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
    export PATH="$HOME/.local/bin:$PATH"
  fi
  # Warm up uvx cache with --upgrade to pull latest version
  # First run downloads packages (~2s); subsequent runs use cache (<0.3s)
  uvx --upgrade --from 'memsearch[onnx]' memsearch --version &>/dev/null || true
  _detect_memsearch
fi

# First-time setup: if no config file exists, default to onnx provider.
# This avoids requiring an OPENAI_API_KEY for new plugin users.
# Existing users (who already have a config file) are not affected.
if [ -n "$MEMSEARCH_CMD" ]; then
  if [ ! -f "$HOME/.memsearch/config.toml" ] && [ ! -f "${CLAUDE_PROJECT_DIR:-.}/.memsearch.toml" ]; then
    $MEMSEARCH_CMD config set embedding.provider onnx 2>/dev/null || true
  fi
fi

# Read resolved config and version for status display
PROVIDER="onnx"; MODEL=""; MILVUS_URI=""; VERSION=""
if [ -n "$MEMSEARCH_CMD" ]; then
  PROVIDER=$($MEMSEARCH_CMD config get embedding.provider 2>/dev/null || echo "onnx")
  MODEL=$($MEMSEARCH_CMD config get embedding.model 2>/dev/null || echo "")
  MILVUS_URI=$($MEMSEARCH_CMD config get milvus.uri 2>/dev/null || echo "")
  # "memsearch, version 0.1.10" → "0.1.10"
  VERSION=$($MEMSEARCH_CMD --version 2>/dev/null | sed 's/.*version //' || echo "")
fi

# Auto-start podman/Milvus stack if URI is HTTP and the port is unreachable.
# Silent no-op for Lite mode (file URI), missing podman, or missing compose file.
ensure_milvus_up || true

# Determine required API key for the configured provider
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
REQUIRED_KEY=$(_required_env_var "$PROVIDER")

KEY_MISSING=false
if [ -n "$REQUIRED_KEY" ] && [ -z "${!REQUIRED_KEY:-}" ]; then
  # Env var not set — check if API key is configured in memsearch config file
  CONFIG_API_KEY=""
  if [ -n "$MEMSEARCH_CMD" ]; then
    CONFIG_API_KEY=$($MEMSEARCH_CMD config get embedding.api_key 2>/dev/null || echo "")
  fi
  if [ -z "$CONFIG_API_KEY" ]; then
    KEY_MISSING=true
  fi
fi

# Check PyPI for newer version (2s timeout, non-blocking on failure)
UPDATE_HINT=""
if [ -n "$VERSION" ]; then
  _PYPI_JSON=$(curl -s --max-time 2 https://pypi.org/pypi/memsearch/json 2>/dev/null || true)
  LATEST=$(_json_val "$_PYPI_JSON" "info.version" "")
  if [ -n "$LATEST" ] && [ "$LATEST" != "$VERSION" ]; then
    # Detect install method to suggest the right upgrade command
    _MS_REAL=$(readlink -f "$(command -v memsearch 2>/dev/null)" 2>/dev/null || echo "")
    if [[ "$MEMSEARCH_CMD" == *"uvx"* ]] || [[ "$_MS_REAL" == *"uv/tools"* ]]; then
      UPGRADE_CMD="uv tool install -U 'memsearch[onnx]'"
    else
      UPGRADE_CMD="pip install --upgrade 'memsearch[onnx]'"
    fi
    UPDATE_HINT=" | UPDATE: v${LATEST} available — run: ${UPGRADE_CMD}"
  fi
fi

# Build status line: version | provider/model | milvus | optional update/error
VERSION_TAG="${VERSION:+ v${VERSION}}"
COLLECTION_HINT=""
if [ -n "$COLLECTION_NAME" ]; then
  COLLECTION_HINT=" | collection: ${COLLECTION_NAME}"
fi
AUTOSTART_TAG=""
case "${MILVUS_AUTOSTART_STATUS:-}" in
  started)  AUTOSTART_TAG=" (auto-started)" ;;
  starting) AUTOSTART_TAG=" (starting…)" ;;
  failed)   AUTOSTART_TAG=" (UNREACHABLE)" ;;
esac
status="[memsearch${VERSION_TAG}] embedding: ${PROVIDER}/${MODEL:-unknown} | milvus: ${MILVUS_URI:-unknown}${AUTOSTART_TAG}${COLLECTION_HINT}${UPDATE_HINT}"
if [ "$KEY_MISSING" = true ]; then
  status+=" | ERROR: ${REQUIRED_KEY} not set — memory search disabled"
  status+=" | Tip: switch to free local embedding: memsearch config set embedding.provider onnx && memsearch index --force"
fi

# Build collection description: "<project_basename> | <provider>/<model>"
PROJECT_BASENAME=$(basename "${CLAUDE_PROJECT_DIR:-.}")
COLLECTION_DESC="${PROJECT_BASENAME} | ${PROVIDER}/${MODEL:-default}"

# Ensure the bucket dir exists. The "## Session" heading is no longer written
# eagerly here — the Stop hook writes it lazily next to the first real summary,
# so sessions that never summarize leave no orphan empty "## Session" headings.
ensure_memory_dir

# --- Crash-safety recovery (persist-then-upgrade, recovery phase) ---
# The Stop hook persists each turn to a hidden `.pending/<token>` sidecar before
# the slow `claude -p`, then deletes it once the polished summary is durable. A
# sidecar still present and older than the 120s Stop timeout therefore belongs to
# a Stop hook that was killed mid-summarize — re-home its raw turn into today's
# file so it isn't lost to search. The >2-min floor guarantees we never grab a
# still-live sidecar from a concurrent session. Rare path: usually a no-op.
_PENDING_DIR="$MEMORY_BUCKET_DIR/.pending"
if [ -d "$_PENDING_DIR" ]; then
  _RECOVERY_FILE="$MEMORY_BUCKET_DIR/$(date +%Y-%m-%d).md"
  _RECOVERY_HEAD="## Session $(date +%H:%M) (recovered)"
  while IFS= read -r _sc; do
    [ -z "$_sc" ] && continue
    [ -f "$_sc" ] || continue
    # Same mkdir-mutex the Stop hook uses, so recovery and a live Stop write to
    # today's file never interleave. 10s budget; reclaim a >60s stale lock.
    _LOCKDIR="${_RECOVERY_FILE}.lock.d"
    _acq=0
    for _ in $(seq 1 100); do
      if mkdir "$_LOCKDIR" 2>/dev/null; then _acq=1; break; fi
      if [ -d "$_LOCKDIR" ] && [ -n "$(find "$_LOCKDIR" -maxdepth 0 -mmin +1 2>/dev/null)" ]; then
        rmdir "$_LOCKDIR" 2>/dev/null
      fi
      sleep 0.1
    done
    if [ "$_acq" = 1 ]; then
      {
        echo ""
        echo "$_RECOVERY_HEAD"
        echo ""
        cat "$_sc"
        echo ""
      } >> "$_RECOVERY_FILE"
      rm -f "$_sc" 2>/dev/null || true
      rmdir "$_LOCKDIR" 2>/dev/null || true
    fi
  done <<RECOVER_EOF
$(find "$_PENDING_DIR" -maxdepth 1 -type f -mmin +2 2>/dev/null || true)
RECOVER_EOF
fi

# If API key is missing, show status and exit early (watch/search would fail)
if [ "$KEY_MISSING" = true ]; then
  json_status=$(_json_encode_str "$status")
  echo "{\"systemMessage\": $json_status}"
  exit 0
fi

# Start memsearch watch (Server mode) or do one-time index (Lite mode).
# start_watch() skips watch for Lite — file lock prevents concurrent access.
start_watch

# Lite mode: one-time index since watch is not running.
# Runs in background subshell to avoid blocking the hook (ONNX model loading takes ~10s).
# The index-domain single-writer lock serializes indexers; --replace takes over
# a hung prior index cross-platform (retires the old .index.pid kill). The
# milvus_lite GC first frees any stale .db lock left by a force-killed run.
# If embedding dimension changed (e.g. user switched provider), auto-reset and re-index.
if [[ "$MILVUS_URI" != http* ]] && [[ "$MILVUS_URI" != tcp* ]]; then
  kill_orphaned_milvus_lite
  (
    _index_args=("$MEMORY_DIR" --replace)
    [ -n "$COLLECTION_NAME" ] && _index_args+=(--collection "$COLLECTION_NAME")
    [ -n "$COLLECTION_DESC" ] && _index_args+=(--description "$COLLECTION_DESC")
    INDEX_OUTPUT=$($MEMSEARCH_CMD index "${_index_args[@]}" 2>&1) || true
    if echo "$INDEX_OUTPUT" | grep -q "dimension mismatch"; then
      _reset_args=(--yes)
      [ -n "$COLLECTION_NAME" ] && _reset_args+=(--collection "$COLLECTION_NAME")
      $MEMSEARCH_CMD reset "${_reset_args[@]}" 2>/dev/null || true
      $MEMSEARCH_CMD index "${_index_args[@]}" 2>/dev/null || true
    fi
  ) >/dev/null 2>&1 &
fi

# Always include status in systemMessage
json_status=$(_json_encode_str "$status")

# Curated artifacts written off the hot path by maintenance (memsearch_dir-relative).
# Priority order for Option-1 cold-start injection: CORRECTIONS -> PROJECT -> USER.
CORRECTIONS_FILE="$MEMSEARCH_DIR/CORRECTIONS.md"
PROJECT_FILE="$MEMSEARCH_DIR/PROJECT.md"
USER_FILE="$MEMSEARCH_DIR/USER.md"

# Single predicate reused by both the guard and the injection branch selector.
_has_artifact=false
for _af in "$CORRECTIONS_FILE" "$PROJECT_FILE" "$USER_FILE"; do
  [ -s "$_af" ] && { _has_artifact=true; break; }
done

# Exit early only when NEITHER any curated artifact NOR any daily log exists.
# A freshly switched worktree may have curated artifacts but no recent daily
# logs — the old daily-log-only guard would silently exit 0 and never inject them.
if [ "$_has_artifact" = false ] && { [ ! -d "$MEMORY_BUCKET_DIR" ] || ! ls "$MEMORY_BUCKET_DIR"/*.md &>/dev/null; }; then
  echo "{\"systemMessage\": $json_status}"
  exit 0
fi

context=""

if [ "$_has_artifact" = true ]; then
  # Option 1: inject the already-collapsed curated artifacts (higher signal-per-token
  # than a raw daily-log tail), plus a short recent tail of today's file only.
  # Byte caps (no token counter in bash): per-item caps clamp each item, TOTAL_MAX
  # is the HARD ceiling. 2000*3 + 1500 = 7500 > 6000, so per-item caps alone are
  # unenforceable — the running_total below enforces TOTAL_MAX in priority order.
  ARTIFACT_MAX=2000
  RECENT_TAIL_MAX=1500
  TOTAL_MAX=6000

  context="# Recent Memory\n\n"
  running_total=0

  # _append_item <heading> <body> <per_item_cap>
  # Clamp body to its per-item cap; enforce TOTAL_MAX as the hard ceiling in
  # priority order. Returns 1 (stop) when the total budget is reached, else 0.
  _append_item() {
    local heading="$1" body="$2" cap="$3"
    local orig_bytes item item_bytes was_clamped=false remaining
    # `|| true` on every pipe-to-head/wc: a large body makes head -c exit before
    # printf finishes, killing printf with SIGPIPE — which `set -o pipefail` +
    # `set -e` would turn into a silent hook abort. head already wrote its bytes
    # to the capture buffer, so the guard is behavior-preserving. Matches the
    # `| head`/`| tail … || true` convention used elsewhere in this file.
    orig_bytes=$(printf '%s' "$body" | wc -c || true)
    # Clamp to per-item cap. $(...) strips trailing newlines, so measure what we hold.
    item=$(printf '%s' "$body" | head -c "$cap" || true)
    item_bytes=$(printf '%s' "$item" | wc -c || true)
    [ "$orig_bytes" -gt "$cap" ] && was_clamped=true
    remaining=$((TOTAL_MAX - running_total))
    if [ "$remaining" -le 0 ]; then
      return 1
    fi
    if [ "$item_bytes" -gt "$remaining" ]; then
      item=$(printf '%s' "$item" | head -c "$remaining" || true)
      context+="## $heading\n$item\n[truncated]\n\n"
      return 1
    fi
    if [ "$was_clamped" = true ]; then
      context+="## $heading\n$item\n[truncated]\n\n"
    else
      context+="## $heading\n$item\n\n"
    fi
    running_total=$((running_total + item_bytes))
    return 0
  }

  # Priority order: CORRECTIONS -> PROJECT -> USER -> recent-tail.
  # Stop appending lower-priority items once the total budget is reached.
  _done=false
  for _spec in "CORRECTIONS.md:$CORRECTIONS_FILE" "PROJECT.md:$PROJECT_FILE" "USER.md:$USER_FILE"; do
    _heading="${_spec%%:*}"
    _file="${_spec#*:}"
    [ -s "$_file" ] || continue
    _body=$(cat "$_file" 2>/dev/null || true)
    [ -z "$_body" ] && continue
    if ! _append_item "$_heading" "$_body" "$ARTIFACT_MAX"; then
      _done=true
      break
    fi
  done

  # Recent tail: today's file only, so "what just happened" survives the
  # 24h-gated curated cadence. Heading mirrors the existing "## <basename>.md" style.
  if [ "$_done" = false ]; then
    _today="$MEMORY_BUCKET_DIR/$(date +%Y-%m-%d).md"
    _tail=$(grep -E '^(#{2,4} |- )' "$_today" 2>/dev/null || true)
    if [ -n "$_tail" ]; then
      _append_item "$(date +%Y-%m-%d).md" "$_tail" "$RECENT_TAIL_MAX" || true
    fi
  fi
else
  # Fallback (maintenance off / zero-config): the existing behavior unchanged.
  # Find the 2 most recent daily log files within the current repo bucket.
  recent_files=$(find "$MEMORY_BUCKET_DIR" -maxdepth 1 -type f -name '*.md' -print 2>/dev/null | sort -r | head -2 || true)

  if [ -n "$recent_files" ]; then
    context="# Recent Memory\n\n"
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      basename_f=$(basename "$f")
      # Extract headings (## Session, ### turn timestamps) and bullet content —
      # higher signal density than a raw tail of the file (skips HTML anchors and
      # blank lines). tail -300 keeps the MOST RECENT turns — effectively the whole
      # day, since summaries are compact (~1-2K tokens for a full day). The cap is
      # only a runaway-day guard (the real scope bound is the 2-file `head -2`
      # above), not a relevance filter; head -N would freeze the injected context
      # on the morning's entries and hide everything newer (the bug that lost the
      # 16:45 plan reference).
      content=$(grep -E '^(#{2,4} |- )' "$f" 2>/dev/null | tail -300 || true)
      if [ -n "$content" ]; then
        context+="## $basename_f\n$content\n\n"
      fi
    done <<< "$recent_files"
  fi
fi

# Note: Detailed memory search is handled by the memory-recall skill (pull-based).
# The cold-start context above gives Claude enough awareness of recent sessions
# to decide when to invoke the skill for deeper recall.

if [ -n "$context" ]; then
  json_context=$(_json_encode_str "$context")
  echo "{\"systemMessage\": $json_status, \"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $json_context}}"
else
  echo "{\"systemMessage\": $json_status}"
fi
