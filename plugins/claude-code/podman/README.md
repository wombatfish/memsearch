# Podman / Milvus Standalone (optional)

The plugin can auto-start a containerised Milvus standalone (etcd + minio + milvus) at every `SessionStart` so memsearch is always ready. This is **optional** — the plugin still works fine with Milvus Lite (default) or any remote Milvus you point it at.

## Prerequisites

- [Podman](https://podman.io/) on PATH (`podman --version` works)
- `podman-compose` (`pip install podman-compose` or via `uv tool install podman-compose`)
- Podman machine initialised: `podman machine init && podman machine start`
- WSL2 memory ≥ 8 GiB (Milvus standalone's documented minimum). Edit `C:\Users\<you>\.wslconfig`:
  ```ini
  [wsl2]
  memory=8GB
  ```
  then `wsl --shutdown`.

## Setup (one-time)

Copy this template to `~/.memsearch/milvus/` and bring the stack up:

```bash
# Linux / macOS / WSL2:
mkdir -p ~/.memsearch/milvus
cp "$CLAUDE_PLUGIN_ROOT/podman/docker-compose.yml" ~/.memsearch/milvus/
( cd ~/.memsearch/milvus && podman compose up -d )

# Windows PowerShell:
New-Item -ItemType Directory -Force "$HOME\.memsearch\milvus" | Out-Null
Copy-Item "$env:CLAUDE_PLUGIN_ROOT\podman\docker-compose.yml" "$HOME\.memsearch\milvus\"
podman compose -f "$HOME\.memsearch\milvus\docker-compose.yml" up -d
```

Point memsearch at the local server (once):
```bash
memsearch config set milvus.uri http://localhost:19530
```

## Auto-install on first SessionStart

Set `MEMSEARCH_PODMAN_AUTO_INSTALL=1` to have the hook copy the template into `~/.memsearch/milvus/` automatically the first time it runs:

```bash
export MEMSEARCH_PODMAN_AUTO_INSTALL=1
```

Only the file copy is automated — the stack is still started by the hook's normal auto-start path.

## How auto-start works

On each `SessionStart`, `ensure_milvus_up` (in `hooks/common.sh`):

1. TCP-probes the configured `milvus.uri` host:port.
2. If reachable → no-op (`(auto-started)` tag absent).
3. If unreachable and `~/.memsearch/milvus/docker-compose.yml` exists and `podman` is on PATH:
   - Tries `podman compose start` synchronously (≤ 8 s budget — covers warm restarts).
   - If still unreachable, kicks `podman machine start` + `podman compose up -d` in the background and returns. Status line shows `(starting)`.

## Tuning

| Env var | Default | Effect |
|---|---|---|
| `MEMSEARCH_PODMAN_COMPOSE` | `$HOME/.memsearch/milvus/docker-compose.yml` | Compose file location |
| `MEMSEARCH_PODMAN_SYNC_TIMEOUT` | `8` | Seconds the hook waits synchronously |
| `MEMSEARCH_PODMAN_AUTO_INSTALL` | unset | If `1`, copy template to home on first run |

## Customising the stack

Edit `~/.memsearch/milvus/docker-compose.yml` to change versions, ports, volume paths, etc. The plugin's bundled template is treated as a starting point only — it's never re-read after you've copied it.
