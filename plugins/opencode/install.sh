#!/usr/bin/env bash
# Install the memsearch OpenCode plugin.
#
# This script:
# 1. Detects if memsearch is installed
# 2. Symlinks the plugin to OpenCode's plugins directory
# 3. Symlinks the memory-recall skill to ~/.agents/skills/
# 4. Prints setup instructions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_PLUGINS_DIR="${HOME}/.config/opencode/plugins"
AGENTS_SKILLS_DIR="${HOME}/.agents/skills"

echo "=== memsearch OpenCode Plugin Installer ==="
echo ""

if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
  cat <<'EOF'
[ERROR] This installer is running under WSL bash.
        For Windows source installs, run the Git Bash launcher instead:
          plugins\opencode\install.cmd

        Plain "bash" often resolves to WSL on Windows, which installs into the
        WSL home/config instead of the Windows OpenCode environment.
EOF
  exit 1
fi

is_windows_git_bash() {
  command -v cygpath >/dev/null 2>&1 && [[ "$(uname -s 2>/dev/null)" =~ ^(MINGW|MSYS|CYGWIN) ]]
}

create_dir_symlink() {
  local src="$1"
  local link="$2"

  if [ -L "${link}" ]; then
    rm -f "${link}"
  elif [ -e "${link}" ]; then
    echo "[INFO] Replacing existing directory with symlink: ${link}"
    rm -rf "${link}"
  fi

  if is_windows_git_bash; then
    if ! MSYS=winsymlinks:nativestrict ln -s "${src}" "${link}"; then
      local src_win
      local link_win
      src_win="$(cygpath -w "${src}")"
      link_win="$(cygpath -w "${link}")"
      cat <<EOF
[ERROR] Windows refused to create a directory symlink:
        ${link_win} -> ${src_win}

        Enable Windows Developer Mode or run this installer from an elevated
        terminal, then rerun plugins\\opencode\\install.cmd.
EOF
      exit 1
    fi

    if [ ! -L "${link}" ]; then
      echo "[ERROR] Symlink creation reported success but ${link} is not a symlink."
      exit 1
    fi
  else
    ln -sfn "${src}" "${link}"
  fi
}

# 1. Check memsearch
if command -v memsearch &>/dev/null; then
  echo "[OK] memsearch found: $(which memsearch)"
elif command -v uvx &>/dev/null; then
  echo "[OK] uvx found — will use: uvx --from 'memsearch[onnx]' memsearch"
  echo "     Tip: install memsearch for faster startup: uv tool install 'memsearch[onnx]'"
else
  echo "[WARN] Neither memsearch nor uvx found."
  echo "       Install with: uv tool install 'memsearch[onnx]'"
  echo "       Or: pip install 'memsearch[onnx]'"
fi
echo ""

# 2. Symlink plugin to OpenCode plugins directory
mkdir -p "${OPENCODE_PLUGINS_DIR}"
PLUGIN_LINK="${OPENCODE_PLUGINS_DIR}/memsearch.ts"
if [ -L "${PLUGIN_LINK}" ] || [ -f "${PLUGIN_LINK}" ]; then
  echo "[SKIP] Plugin already exists at ${PLUGIN_LINK}"
  echo "       Remove it first if you want to reinstall: rm ${PLUGIN_LINK}"
else
  ln -sf "${SCRIPT_DIR}/index.ts" "${PLUGIN_LINK}"
  echo "[OK] Plugin symlinked: ${PLUGIN_LINK} -> ${SCRIPT_DIR}/index.ts"
fi
echo ""

# 3. Symlink skills to ~/.agents/skills/ (OpenCode-compatible)
# memory-recall is the unified cross-plugin skill from _shared/ — same source as
# the Codex installer uses, so install order no longer matters. memory-config is
# OpenCode-specific and stays in this plugin's tree.
mkdir -p "${AGENTS_SKILLS_DIR}"

SHARED_SKILLS_DIR="$(cd "${SCRIPT_DIR}/../_shared/skills" && pwd)"

declare -A SKILL_SOURCES=(
  [memory-recall]="${SHARED_SKILLS_DIR}/memory-recall"
  [memory-config]="${SCRIPT_DIR}/skills/memory-config"
)

for skill_name in "${!SKILL_SOURCES[@]}"; do
  SKILL_LINK="${AGENTS_SKILLS_DIR}/${skill_name}"
  SKILL_SRC="${SKILL_SOURCES[$skill_name]}"
  create_dir_symlink "${SKILL_SRC}" "${SKILL_LINK}"
  echo "[OK] Skill symlinked: ${SKILL_LINK} -> ${SKILL_SRC}"
done
echo ""

# 4. Install plugin dependencies
#
# OpenCode resolves the plugin's imports from the plugin file's own directory,
# NOT from OpenCode's runtime. Without @opencode-ai/plugin installed beside
# index.ts, the plugin fails to load with "Cannot find module" and registers
# no tools at all — so we surface the failure loudly rather than swallowing it.
echo "[INFO] Installing plugin dependencies (@opencode-ai/plugin)..."
if command -v bun &>/dev/null; then
  (cd "${SCRIPT_DIR}" && bun install) \
    && echo "[OK] Dependencies installed via bun" \
    || { echo "[ERROR] bun install failed — plugin will not load"; exit 1; }
elif command -v npm &>/dev/null; then
  (cd "${SCRIPT_DIR}" && npm install --save-dev @opencode-ai/plugin) \
    && echo "[OK] Dependencies installed via npm" \
    || { echo "[ERROR] npm install failed — plugin will not load"; exit 1; }
else
  echo "[ERROR] Neither bun nor npm found — cannot install @opencode-ai/plugin."
  echo "        OpenCode will fail to load the plugin until dependencies are installed."
  exit 1
fi
echo ""

# 5. Show next steps
echo "=== Installation Complete ==="
echo ""
echo "The plugin will be auto-loaded next time you start OpenCode."
echo ""
echo "To verify, start OpenCode and check if memsearch_search tool appears:"
echo "  opencode"
echo ""
echo "Optional: Add to opencode.json for npm-based install:"
echo '  "plugin": ["memsearch-opencode"]'
echo ""
echo "Memory files will be stored under <root>/memory/<repo>/<branch>/, where <root>"
echo "is MEMSEARCH_DIR if set, otherwise <project>/.memsearch. Collection name is"
echo "derived from the same root (shared across projects in global mode)."
