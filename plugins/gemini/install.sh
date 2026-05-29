#!/usr/bin/env bash
# One-click installer for the memsearch Gemini CLI extension.
#
# What it does:
#   1. Checks memsearch availability (memsearch / uvx / installs uv).
#   2. Ensures the shared memory-recall skill is in ~/.agents/skills/
#      (Gemini auto-discovers it there — does NOT dual-install).
#   3. Generates hooks/hooks.json from hooks.json.template with the correct
#      per-platform launch command (PowerShell `&` + Git Bash on Windows,
#      `bash` on POSIX). hooks.json is gitignored — it is machine-specific.
#   4. Links the extension with `gemini extensions link --consent`.
#
# Usage: bash plugins/gemini/install.sh

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== memsearch Gemini CLI Extension Installer ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# --- 1. memsearch availability ---
echo "[1/5] Checking memsearch..."
if command -v memsearch &>/dev/null; then
  echo "  ok memsearch: $(command -v memsearch) ($(memsearch --version 2>/dev/null || echo unknown))"
elif command -v uvx &>/dev/null; then
  echo "  ok uvx found — hooks will use: uvx --from memsearch[onnx] memsearch"
  uvx --from 'memsearch[onnx]' memsearch --version &>/dev/null || true
else
  echo "  memsearch not found — installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uvx --from 'memsearch[onnx]' memsearch --version &>/dev/null || true
fi

# --- 2. Shared memory-recall skill (single source: ~/.agents/skills) ---
# Gemini auto-discovers skills from ~/.agents/skills AND ~/.gemini/skills.
# Installing in more than one place triggers a conflict warning, so we keep
# exactly one copy here and do NOT bundle a skills/ dir in the extension.
echo "[2/5] Ensuring shared memory-recall skill..."
SHARED_SKILL="$(cd "$INSTALL_DIR/../_shared/skills/memory-recall" 2>/dev/null && pwd || echo "")"
SKILL_DST="$HOME/.agents/skills/memory-recall"
if [ -z "$SHARED_SKILL" ]; then
  echo "  ! _shared/skills/memory-recall not found — skipping (recall skill unavailable)"
elif [ -e "$SKILL_DST" ]; then
  echo "  ok already present: $SKILL_DST (left as-is)"
else
  mkdir -p "$HOME/.agents/skills"
  cp -r "$SHARED_SKILL" "$SKILL_DST"
  echo "  ok copied memory-recall -> $SKILL_DST"
fi

# --- 3. Determine per-platform launch prefix ---
# Gemini runs Windows hook commands via `powershell.exe -Command`; a quoted exe
# path needs the `&` call operator, and its quotes are JSON-escaped (\") because
# the prefix is substituted into a JSON string value. NEVER fall back to bare
# `bash` on Windows — PowerShell resolves that to the WSL launcher, which cannot
# run a C:/ path, so the SessionStart hook would fail silently (no memory).
echo "[3/5] Resolving hook launch command..."
if [ "${OS:-}" = "Windows_NT" ]; then
  _bash_win=""
  if command -v cygpath &>/dev/null; then
    _cand="$(cygpath -m "$(command -v bash)" 2>/dev/null || echo "")"
    [[ "$_cand" == [A-Za-z]:/*bash.exe ]] && _bash_win="$_cand"
  fi
  if [ -z "$_bash_win" ]; then
    # cygpath absent, or a path the drive-letter glob rejects (e.g. a UNC
    # //server/share install). Probe standard Git for Windows locations.
    for _p in "C:/Program Files/Git/usr/bin/bash.exe" \
              "C:/Program Files/Git/bin/bash.exe" \
              "C:/Program Files (x86)/Git/usr/bin/bash.exe"; do
      [ -f "$_p" ] && { _bash_win="$_p"; break; }
    done
  fi
  if [ -z "$_bash_win" ]; then
    echo "  x Could not locate Git Bash. Gemini runs Windows hooks via PowerShell," >&2
    echo "    which cannot use bare 'bash' (it routes to the WSL launcher)." >&2
    echo "    Install Git for Windows and re-run, or set the bash path in hooks.json by hand." >&2
    exit 1
  fi
  LAUNCH="& \\\"$_bash_win\\\""
  echo "  ok Windows (PowerShell): $LAUNCH"
else
  LAUNCH="bash"
  echo "  ok POSIX: bash"
fi

# --- 4. Generate hooks/hooks.json from the template ---
# Substitute @@LAUNCH@@ with $LAUNCH. Use python str.replace (NOT bash ${//},
# sed, or awk): in bash 5.1+ and sed/awk, an `&` in the replacement expands to
# the matched text, which mangles the PowerShell call operator. Feed the
# template via STDIN and pass $LAUNCH via ARGV — no file-path arguments, so the
# MSYS-path-to-Windows-python trap is avoided too. ${extensionPath} is left
# intact (Gemini substitutes it at runtime). timeouts are MILLISECONDS.
echo "[4/5] Generating hooks/hooks.json..."
cat "$INSTALL_DIR/hooks/hooks.json.template" \
  | python3 -c "import sys; sys.stdout.write(sys.stdin.read().replace('@@LAUNCH@@', sys.argv[1]))" "$LAUNCH" \
  > "$INSTALL_DIR/hooks/hooks.json.tmp"
mv -f "$INSTALL_DIR/hooks/hooks.json.tmp" "$INSTALL_DIR/hooks/hooks.json"
echo "  ok wrote $INSTALL_DIR/hooks/hooks.json"

# --- 5. Make scripts executable + link the extension ---
echo "[5/5] Permissions + linking extension..."
chmod +x "$INSTALL_DIR/hooks/"*.sh 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true

if command -v gemini &>/dev/null; then
  LINK_PATH="$INSTALL_DIR"
  command -v cygpath &>/dev/null && LINK_PATH="$(cygpath -m "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"
  # Re-link cleanly so updates to the manifest/hooks are picked up.
  gemini extensions uninstall memsearch >/dev/null 2>&1 || true
  if gemini extensions link "$LINK_PATH" --consent; then
    echo "  ok linked: $LINK_PATH"
  else
    echo "  ! 'gemini extensions link' failed — link manually:"
    echo "      gemini extensions link \"$LINK_PATH\" --consent"
  fi
else
  echo "  ! gemini not on PATH — after installing Gemini CLI, run:"
  echo "      gemini extensions link \"$INSTALL_DIR\" --consent"
fi

echo ""
echo "=== Done ==="
echo "SessionStart now injects branch-scoped recent memory into Gemini sessions"
echo "(including headless 'gemini -p' critics spawned by Syndic)."
echo "Memory is read-only here; the sibling plugins (Claude Code / Codex / OpenCode) write it."
echo "Verify: cd into a repo with memsearch memory and run:"
echo "    gemini -p \"what was recently decided on this branch?\""
