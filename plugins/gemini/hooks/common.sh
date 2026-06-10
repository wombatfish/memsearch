#!/usr/bin/env bash
# Shared setup for memsearch Gemini CLI hooks. Sourced — not executed directly.
#
# Gemini-specific notes vs the sibling plugins (claude-code / codex):
#  - Hook commands are run by Gemini via `powershell.exe -NoProfile -Command`
#    on Windows and `/bin/sh -c` on POSIX. This file only runs AFTER bash has
#    been launched, so it is ordinary bash — the shell quirk lives in the
#    generated hooks.json command (see hooks.json.template + install.sh).
#  - The hook receives the FULL parent environment (the doc's "extensions don't
#    inherit shell env" warning applies to MCP servers, NOT command hooks —
#    verified). So MEMSEARCH_DIR / API keys flow through normally.
#  - The authoritative cwd is the stdin `cwd` field (Syndic spawns gemini with
#    cwd = target repo). All git calls use `git -C "$HOOK_CWD"` so branch
#    detection is correct regardless of the bash process's own cwd.
#  - NO watch / index / podman / single-writer lock: Gemini sessions (especially
#    headless `-p` one-shots) are consumers of memory the sibling plugins write.
#    Spawning a background watcher here would orphan on Windows (bash reaping
#    no-ops against native memsearch.exe). Injection is strictly read-only.

set -euo pipefail

# --- Read stdin JSON into $INPUT (bounded; never block the hook) ---
if command -v timeout &>/dev/null; then
  INPUT="$(timeout 2 cat 2>/dev/null || echo '{}')"
else
  INPUT="$(perl -e 'alarm 2; local $/; $_ = <STDIN>; print if defined' 2>/dev/null || echo '{}')"
fi

# Ensure common user bin paths are on PATH (hooks may run in a minimal env)
for p in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/bin" "/usr/local/bin"; do
  [[ -d "$p" ]] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done

# --- JSON helpers (jq preferred, python3 fallback) ---

# _json_val <json_string> <dotted_key> [default]
_json_val() {
  local json="$1" key="$2" default="${3:-}"
  local result=""
  if command -v jq &>/dev/null; then
    result=$(printf '%s' "$json" | jq -r ".${key} // empty" 2>/dev/null) || true
  else
    # JSON goes via STDIN, not argv: large hook payloads (>32K) hit the
    # Windows CreateProcess argv limit and every extraction silently
    # returns its default.
    result=$(printf '%s' "$json" | python3 -c "
import json, sys
try:
    obj = json.loads(sys.stdin.read())
    val = obj
    for k in sys.argv[1].split('.'):
        val = val[k]
    if val is None:
        print('')
    elif isinstance(val, bool):
        print(str(val).lower())
    else:
        print(val)
except Exception:
    print('')
" "$key" 2>/dev/null) || true
  fi
  if [ -z "$result" ]; then printf '%s' "$default"; else printf '%s' "$result"; fi
  return 0
}

# _json_encode_str <string> — encode as a JSON string (with surrounding quotes).
_json_encode_str() {
  local str="$1"
  if command -v jq &>/dev/null; then
    printf '%s' "$str" | jq -Rs . 2>/dev/null && return 0
  fi
  printf '%s' "$str" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" 2>/dev/null && return 0
  # No JSON encoder available (neither jq nor a working python3 — e.g. a bare
  # macOS without Xcode CLT). Emit NOTHING and let the caller fail closed
  # (status-only / "{}"). The sibling plugins' naive `printf '"%s"'` fallback
  # emits raw newlines/quotes from the multi-line injected context — invalid
  # JSON that makes Gemini drop the ENTIRE hook payload, which is worse than no
  # injection. (A pure-bash escaper was tried and rejected: bash `${//}`
  # backslash-doubling is version-fragile and produced invalid \escapes.)
  # Returning with no output and exit 0 keeps `set -e` happy.
  return 0
}

# --- Project directory (authoritative: stdin cwd → GEMINI_* → pwd) ---
if [ -n "${MEMSEARCH_PROJECT_DIR:-}" ] && [ -d "${MEMSEARCH_PROJECT_DIR:-}" ]; then
  HOOK_CWD="$MEMSEARCH_PROJECT_DIR"
else
  HOOK_CWD="$(_json_val "$INPUT" "cwd" "")"
  [ -d "$HOOK_CWD" ] || HOOK_CWD="${GEMINI_PROJECT_DIR:-${GEMINI_CWD:-$(pwd)}}"
  [ -d "$HOOK_CWD" ] || HOOK_CWD="$(pwd)"
fi

# Prefer git root so the bucket converges with the sibling plugins even when
# gemini is launched from a subdirectory.
_GIT_ROOT="$(git -C "$HOOK_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")"
if [ -n "$_GIT_ROOT" ]; then
  PROJECT_DIR="$_GIT_ROOT"
else
  PROJECT_DIR="$HOOK_CWD"
fi

# MEMSEARCH_DIR explicit → global/shared dir; else per-project. Matches siblings.
MEMSEARCH_DIR="${MEMSEARCH_DIR:-$PROJECT_DIR/.memsearch}"
MEMORY_DIR="$MEMSEARCH_DIR/memory"

# --- Per-branch bucket under MEMORY_DIR (forked from claude-code/common.sh) ---
# Segregate by main-repo name + branch slug so parallel sessions never collide
# and cold-start context is branch-scoped. Worktrees collapse to their main repo
# via --git-common-dir. Outside any repo → __no_repo__.
_REPO_BUCKET="__no_repo__"
_GIT_COMMON_DIR="$(git -C "$HOOK_CWD" rev-parse --git-common-dir 2>/dev/null || echo "")"
if [ -n "$_GIT_COMMON_DIR" ]; then
  # --git-common-dir may be relative to HOOK_CWD; resolve against it.
  case "$_GIT_COMMON_DIR" in
    /*|[A-Za-z]:*) ;;                       # already absolute (POSIX or Windows)
    *) _GIT_COMMON_DIR="$HOOK_CWD/$_GIT_COMMON_DIR" ;;
  esac
  if [ -d "$_GIT_COMMON_DIR" ]; then
    _GIT_COMMON_DIR="$(cd "$_GIT_COMMON_DIR" && pwd)"
    _REPO_BUCKET="$(basename "$(dirname "$_GIT_COMMON_DIR")" | LC_ALL=C tr '[:upper:]' '[:lower:]' 2>/dev/null || true)"
  fi
fi
case "$_REPO_BUCKET" in ""|"/") _REPO_BUCKET="__no_repo__";; esac

_BRANCH_SEG=""
if [ "$_REPO_BUCKET" != "__no_repo__" ]; then
  _branch="$(git -C "$HOOK_CWD" symbolic-ref --short -q HEAD 2>/dev/null || echo "")"
  if [ -z "$_branch" ]; then
    _BRANCH_SEG="_detached"
  else
    # LC_ALL=C: BSD/macOS `tr -c` throws "Illegal byte sequence" on a non-ASCII
    # branch name under a UTF-8 locale; with `set -e` that aborts the hook before
    # any JSON is emitted. `|| true` + the empty-check below degrade gracefully.
    _BRANCH_SEG="$(printf '%s' "$_branch" \
      | LC_ALL=C tr '[:upper:]' '[:lower:]' \
      | LC_ALL=C tr -c 'a-z0-9._-' '-' \
      | LC_ALL=C sed 's/-\{2,\}/-/g; s/^-//; s/-$//' 2>/dev/null || true)"
    [ -z "$_BRANCH_SEG" ] && _BRANCH_SEG="_branch"
  fi
fi

if [ -n "$_BRANCH_SEG" ]; then
  MEMORY_BUCKET_DIR="$MEMORY_DIR/$_REPO_BUCKET/$_BRANCH_SEG"
else
  MEMORY_BUCKET_DIR="$MEMORY_DIR/$_REPO_BUCKET"
fi

# --- memsearch binary detection (for status display / future capture only;
#     the grep-push centerpiece reads markdown directly and needs neither
#     memsearch nor Milvus) ---
_detect_memsearch() {
  MEMSEARCH_CMD=""
  if command -v memsearch &>/dev/null; then
    MEMSEARCH_CMD="memsearch"
  elif command -v uvx &>/dev/null; then
    MEMSEARCH_CMD="uvx --from memsearch[onnx] memsearch"
  fi
}
_detect_memsearch
memsearch_available() { [ -n "$MEMSEARCH_CMD" ]; }

# NOTE: no COLLECTION_NAME here. The grep-push centerpiece reads markdown
# directly (no Milvus), and the status line uses the repo/branch scope label —
# nothing in these read-only hooks needs a collection. derive-collection.sh is
# still shipped for the recall skill / sibling parity. Re-add when capture lands.
