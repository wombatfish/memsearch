#!/usr/bin/env bash
# SessionStart hook (Gemini CLI) — CENTERPIECE.
#
# Injects branch-scoped recent memory so a Gemini session — especially a headless
# `-p` critic spawned by Syndic against a target repo — starts already aware of
# recent decisions/work on the current git branch.
#
# Design (deliberately minimal vs the sibling SessionStart hooks):
#  - GREP-PUSH, not semantic search. At SessionStart there is no user query to
#    search against, and a headless critic gets a single turn (it cannot invoke
#    the recall skill mid-conversation). So we PUSH actual recent content rather
#    than a "memory is available" pointer. Reads markdown directly: ~0.08s, no
#    ONNX cold-load (~3s), no Milvus dependency at all.
#  - READ-ONLY. No watch, no index, no podman, no single-writer lock, and no
#    session-heading write. Gemini is a consumer; the sibling plugins are the
#    producers. (A background watcher would orphan on Windows — bash reaping
#    no-ops against native memsearch.exe.)
#  - No network (no PyPI version check) — keeps the headless hook fast.
#
# Output: JSON with hookSpecificOutput.additionalContext. In non-interactive
# (`-p`) mode Gemini PREPENDS additionalContext to the user's prompt; in
# interactive mode it is injected as the first history turn.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# session-start.sh does not use stdin beyond the cwd; common.sh reads it.
source "$SCRIPT_DIR/common.sh"

_scope_label="${_REPO_BUCKET}${_BRANCH_SEG:+/$_BRANCH_SEG}"
status="[memsearch] gemini memory | ${_scope_label}"
json_status=$(_json_encode_str "$status")

# Fail closed: if no JSON encoder is available (no jq, no python3),
# _json_encode_str returns empty — emit a bare valid object rather than
# malformed JSON, and skip injection entirely.
if [ -z "$json_status" ]; then
  echo '{}'
  exit 0
fi

emit_status_only() { echo "{\"systemMessage\": $json_status}"; }

# --- Collect the 2 most recent daily logs: branch bucket first, repo bucket as
#     fallback (a brand-new branch with no memory yet still gets repo context). ---
# $1 = dir, $2 = maxdepth. Match only DATE-named daily logs (YYYY-MM-DD.md →
# starts with a digit) so curated files (PROJECT.md, USER.md, lessons-*.md) that
# accumulate in a bucket can't crowd the recent dailies out of the top-2 — those
# are the recall skill's domain, not the cold-start push. Sort by basename (the
# date), not the full path, so the newest DATE wins even across branch subdirs.
_recent_in() {
  find "$1" -maxdepth "$2" -type f -name '[0-9]*.md' -print 2>/dev/null \
    | awk -F/ '{ print $NF"\t"$0 }' | sort -r | cut -f2- | head -2 || true
}

scope="$_scope_label"
recent_files=""
if [ -d "$MEMORY_BUCKET_DIR" ]; then
  # Branch bucket is the leaf dir → maxdepth 1.
  recent_files="$(_recent_in "$MEMORY_BUCKET_DIR" 1)"
fi
# Fresh branch with no memory yet → fall back to recent logs from ANY branch of
# this repo. maxdepth 2 descends one level into the branch subdirs (the producers
# write <repo>/<branch>/<date>.md), which maxdepth 1 could never reach.
if [ -z "$recent_files" ] && [ "$_REPO_BUCKET" != "__no_repo__" ] && [ -d "$MEMORY_DIR/$_REPO_BUCKET" ]; then
  recent_files="$(_recent_in "$MEMORY_DIR/$_REPO_BUCKET" 2)"
  scope="${_REPO_BUCKET} (repo-wide; no branch-specific memory yet)"
fi

if [ -z "$recent_files" ]; then
  emit_status_only
  exit 0
fi

# --- Build the injected context. Extract headings + bullets (high signal
#     density, skipping anchors/blank lines), then keep the MOST RECENT via
#     tail — a headless `-p` critic gets ONE turn, so it must see the latest
#     sessions, not the oldest. Framed as background, not the user request. ---
context="# Recent memory for ${scope}
(Background context from prior sessions on this git branch, surfaced automatically by memsearch. Use it to ground your work; it is NOT part of the user's request. For deeper recall, use the memory-recall skill.)

"
while IFS= read -r f; do
  [ -z "$f" ] && continue
  basename_f=$(basename "$f")
  content=$(grep -E '^(#{2,4} |- )' "$f" 2>/dev/null | tail -300 || true)
  if [ -n "$content" ]; then
    context+="## ${basename_f}
${content}

"
  fi
done <<< "$recent_files"

json_context=$(_json_encode_str "$context")
# Defensive: if encoding the (multi-line) context failed, degrade to status-only
# rather than emitting `"additionalContext": ` with an empty value (invalid JSON).
if [ -z "$json_context" ]; then
  emit_status_only
  exit 0
fi
echo "{\"systemMessage\": $json_status, \"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $json_context}}"
