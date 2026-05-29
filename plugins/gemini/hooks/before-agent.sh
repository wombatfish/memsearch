#!/usr/bin/env bash
# BeforeAgent hook (Gemini CLI) — maps to Claude Code's UserPromptSubmit.
# Lightweight nudge reminding Gemini that the memory-recall skill is available.
# Actual search + expand is handled by the shared memory-recall skill (pull-side).
#
# Fires after the user submits a prompt, before planning. stdin carries `prompt`.
# Secondary to SessionStart injection — most valuable in interactive sessions
# (a one-shot headless critic rarely invokes skills). Kept cheap and silent on
# short prompts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

PROMPT=$(_json_val "$INPUT" "prompt" "")
# Skip greetings / one-word prompts.
if [ -z "$PROMPT" ] || [ "${#PROMPT}" -lt 10 ]; then
  echo '{}'
  exit 0
fi

if ! memsearch_available; then
  echo '{}'
  exit 0
fi

echo '{"systemMessage": "[memsearch] Memory available — use the memory-recall skill for historical context."}'
