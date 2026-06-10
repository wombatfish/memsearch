#!/usr/bin/env bash
# UserPromptSubmit hook: lightweight hint reminding Claude about the memory-recall skill.
# The actual search + expand is handled by the memory-recall skill (pull-based, context: fork).
#
# Deliberately does NOT source common.sh: this hook fires on every prompt, and
# common.sh costs ~10 process spawns (git rev-parse ×2, derive-collection,
# timeout cat, PATH probing) just to emit a static hint. Only the bits actually
# needed are inlined below — keep them in sync with common.sh if they change.

set -euo pipefail

# Read stdin JSON (same guarded drain as common.sh — stdin may never close).
if command -v timeout &>/dev/null; then
  INPUT="$(timeout 2 cat 2>/dev/null || echo '{}')"
else
  INPUT="$(perl -e 'alarm 2; local $/; $_ = <STDIN>; print if defined' 2>/dev/null || echo '{}')"
fi

# Skip short prompts (greetings, single words, etc.). Pure-bash extraction —
# good enough for a length check, no jq/python spawn. Escaped quotes inside the
# prompt only make the extracted prefix shorter, which is fine for >=10 chars.
PROMPT=""
case "$INPUT" in
  *'"prompt":"'*) PROMPT="${INPUT#*\"prompt\":\"}"; PROMPT="${PROMPT%%\"*}" ;;
  *'"prompt": "'*) PROMPT="${INPUT#*\"prompt\": \"}"; PROMPT="${PROMPT%%\"*}" ;;
esac
if [ -z "$PROMPT" ] || [ "${#PROMPT}" -lt 10 ]; then
  echo '{}'
  exit 0
fi

# Need memsearch available (PATH fix-up inlined from common.sh — pure bash).
for p in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/bin" "/usr/local/bin"; do
  [[ -d "$p" ]] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done
if ! command -v memsearch &>/dev/null && ! command -v uvx &>/dev/null; then
  echo '{}'
  exit 0
fi

echo '{"systemMessage": "[memsearch] Memory available"}'
