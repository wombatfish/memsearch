#!/usr/bin/env bash
# One-click installer for memsearch Codex CLI plugin.
# Copies the skill, installs or updates memsearch hook entries, enables feature flag.
#
# Usage: bash plugins/codex/scripts/install.sh

set -euo pipefail

# Determine install directory (parent of scripts/)
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ensure_hooks_enabled() {
  local config_file="$1"

  python3 - "$config_file" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
if not path.exists():
    path.write_text("[features]\nhooks = true\n")
    raise SystemExit

text = path.read_text()

features_match = re.search(r"(?m)^\[features\]\s*$", text)
if features_match:
    next_section = re.search(r"(?m)^\[[^]]+\]\s*$", text[features_match.end():])
    block_end = len(text) if next_section is None else features_match.end() + next_section.start()
    block = text[features_match.end():block_end]
    block = re.sub(r"(?m)^codex_hooks\s*=.*\n?", "", block)
    if re.search(r"(?m)^hooks\s*=", block):
        block = re.sub(r"(?m)^hooks\s*=.*$", "hooks = true", block)
    else:
        if block and not block.startswith("\n"):
            block = "\n" + block
        block = "\nhooks = true" + block
    text = text[:features_match.end()] + block + text[block_end:]
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n[features]\nhooks = true\n"

path.write_text(text)
PY
}

install_or_update_hooks_file() {
  local hooks_file="$1"
  local install_dir="$2"
  local bash_exe="$3"
  local windows_cmd="$4"

  python3 - "$hooks_file" "$install_dir" "$bash_exe" "$windows_cmd" <<'PY'
from pathlib import Path
import json
import math
import os
import shlex
import sys

hooks_file = Path(sys.argv[1])
install_dir = sys.argv[2]
bash_exe = sys.argv[3]
windows_cmd = sys.argv[4] == "1"

spec = {
    "SessionStart": {"script": "session-start.sh", "timeout": 30},
    "UserPromptSubmit": {"script": "user-prompt-submit.sh", "timeout": 10},
    "Stop": {"script": "stop.sh", "timeout": 30},
}


def convert_legacy_array(items):
    data = {"hooks": {}}
    for item in items:
        if not isinstance(item, dict):
            continue
        event = item.get("event")
        command = item.get("command")
        if not event or not command:
            continue
        hook = {"type": "command", "command": command}
        timeout_ms = item.get("timeout_ms")
        if isinstance(timeout_ms, (int, float)):
            hook["timeout"] = max(1, math.ceil(timeout_ms / 1000))
        if item.get("async") is True:
            hook["async"] = True
        data["hooks"].setdefault(event, []).append(
            {"matcher": item.get("matcher", ""), "hooks": [hook]}
        )
    return data


def load_existing():
    if not hooks_file.exists():
        return {"hooks": {}}

    parsed = json.loads(hooks_file.read_text())
    if isinstance(parsed, list):
        return convert_legacy_array(parsed)
    if isinstance(parsed, dict) and isinstance(parsed.get("hooks"), dict):
        return parsed
    return {"hooks": {}}


def strip_old_memsearch(entries, script_name):
    marker = f"plugins/codex/hooks/{script_name}"
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = []
        for hook in entry.get("hooks", []):
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            if marker in command:
                continue
            hooks.append(hook)
        if hooks:
            copied = dict(entry)
            copied["hooks"] = hooks
            cleaned.append(copied)
    return cleaned


data = load_existing()
hooks = data.setdefault("hooks", {})

for event, details in spec.items():
    script = details["script"]
    script_path = f"{install_dir}/hooks/{script}"
    if windows_cmd:
        command = f'cmd /c {bash_exe} "{script_path}"'
    else:
        command = f"{shlex.quote(bash_exe)} {shlex.quote(script_path)}"
    cleaned = strip_old_memsearch(hooks.get(event, []), script)
    cleaned.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": details["timeout"],
                }
            ],
        }
    )
    hooks[event] = cleaned

# Write via sibling temp + os.replace so an interrupted write never leaves hooks_file truncated.
tmp = hooks_file.with_name(hooks_file.name + ".tmp")
tmp.write_text(json.dumps(data, indent=2) + "\n")
os.replace(tmp, hooks_file)
PY
}

echo "=== memsearch Codex CLI Plugin Installer ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# --- 1. Check memsearch availability ---
echo "[1/6] Checking memsearch..."
if command -v memsearch &>/dev/null; then
  MS_VERSION=$(memsearch --version 2>/dev/null || echo "unknown")
  echo "  ✓ memsearch found: $(command -v memsearch) ($MS_VERSION)"
elif command -v uvx &>/dev/null; then
  echo "  ✓ uvx found — will use: uvx --from memsearch[onnx] memsearch"
  echo "  Warming up cache (first run may take ~30s)..."
  uvx --from 'memsearch[onnx]' memsearch --version 2>/dev/null || true
else
  echo "  ✗ memsearch not found. Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  echo "  Warming up uvx cache (first run may take ~30s)..."
  uvx --from 'memsearch[onnx]' memsearch --version 2>/dev/null || true
fi

# --- 2. Install skills ---
# memory-recall is the unified cross-plugin skill from _shared/ — bundles its own
# derive-collection.sh so it needs no install-time path substitution. memory-config
# stays Codex-specific. Both are copied (not symlinked) so an editor opening the
# installed file doesn't accidentally edit the repo copy.
echo "[2/6] Installing memsearch skills..."
mkdir -p "$HOME/.agents/skills"

SHARED_SKILLS_DIR="$(cd "$INSTALL_DIR/../_shared/skills" && pwd)"

declare -A SKILL_SOURCES=(
  [memory-recall]="$SHARED_SKILLS_DIR/memory-recall"
  [memory-config]="$INSTALL_DIR/skills/memory-config"
)

for skill_name in "${!SKILL_SOURCES[@]}"; do
  SKILL_SRC="${SKILL_SOURCES[$skill_name]}"
  SKILL_DST="$HOME/.agents/skills/$skill_name"
  if [ -d "$SKILL_DST" ] || [ -L "$SKILL_DST" ]; then
    echo "  ⚠ Existing $skill_name skill found — replacing"
    rm -rf "$SKILL_DST"
  fi
  cp -r "$SKILL_SRC" "$SKILL_DST"
  echo "  ✓ Copied $skill_name skill to $SKILL_DST"
done

# --- 3. (Reserved for future per-skill configuration steps) ---
echo "[3/6] Skill configuration..."
echo "  ✓ No substitution needed — unified memory-recall resolves paths at runtime"

# --- 4. Install or update hooks.json ---
echo "[4/6] Configuring hooks..."
CODEX_DIR="$HOME/.codex"
mkdir -p "$CODEX_DIR"
HOOKS_FILE="$CODEX_DIR/hooks.json"
HOOK_INSTALL_DIR="$INSTALL_DIR"
HOOK_BASH_EXE="bash"
HOOK_WINDOWS_CMD=0

if command -v cygpath >/dev/null 2>&1; then
  _win_install_dir="$(cygpath -m "$INSTALL_DIR" 2>/dev/null || true)"
  _git_root="$(cygpath -m / 2>/dev/null || true)"
  _git_bash_exe="${_git_root%/}/bin/bash.exe"
  _win_bash_exe="$(cygpath -m -s "$_git_bash_exe" 2>/dev/null || cygpath -m "$_git_bash_exe" 2>/dev/null || true)"
  if [[ "$_win_install_dir" =~ ^[A-Za-z]:/ ]] && [[ "$_win_bash_exe" =~ ^[A-Za-z]:/ ]]; then
    HOOK_INSTALL_DIR="$_win_install_dir"
    HOOK_BASH_EXE="$_win_bash_exe"
    HOOK_WINDOWS_CMD=1
  fi
fi

if [ -f "$HOOKS_FILE" ]; then
  echo "  ⚠ Existing hooks.json found — backing up to hooks.json.bak"
  cp "$HOOKS_FILE" "${HOOKS_FILE}.bak"
fi
install_or_update_hooks_file "$HOOKS_FILE" "$HOOK_INSTALL_DIR" "$HOOK_BASH_EXE" "$HOOK_WINDOWS_CMD"
echo "  ✓ Installed memsearch hooks in $HOOKS_FILE"

# --- 5. Enable hooks feature flag ---
echo "[5/6] Enabling hooks feature flag..."
CONFIG_FILE="$CODEX_DIR/config.toml"
ensure_hooks_enabled "$CONFIG_FILE"
echo "  ✓ Ensured hooks = true in $CONFIG_FILE"

# --- 6. Make scripts executable ---
echo "[6/6] Setting permissions..."
chmod +x "$INSTALL_DIR/hooks/"*.sh
chmod +x "$INSTALL_DIR/scripts/"*.sh
echo "  ✓ All scripts marked executable"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "The memsearch plugin is now configured for Codex CLI."
echo ""
echo "What happens automatically:"
echo "  • SessionStart: indexes project memory, injects recent context"
echo "  • Stop: summarizes each turn and saves to memory"
echo "  • UserPromptSubmit: reminds Codex about memory-recall skill"
echo "  • memory-recall skill: search past memories when relevant"
echo "  • memory-config skill: diagnose and configure memsearch"
echo ""
echo "Memory files:   <project>/.memsearch/memory/*.md"
echo "Hooks config:   $HOOKS_FILE"
echo "Skill location: $HOME/.agents/skills/{memory-recall,memory-config}"
echo "Feature flag:   hooks = true in $CONFIG_FILE"
echo ""
echo "To verify: start a new codex session and check for [memsearch] status line."
