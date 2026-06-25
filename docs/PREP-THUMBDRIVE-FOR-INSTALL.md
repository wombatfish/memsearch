# Preparing the thumb drive (do this on the original "dave" PC)

Run this **before** the install. It assembles a single thumb drive that the
[INSTALL-ON-NEW-WINDOWS-PC.md](INSTALL-ON-NEW-WINDOWS-PC.md) guide expects. When you're
done, hand the drive to the new PC and follow that guide.

> Everything below is run on the **source** PC (the one that already has the working
> plugin and the memories). `E:` = your thumb drive — change it if File Explorer shows a
> different letter.

---

## What the finished drive must contain

You copy **one folder** to the drive: the whole `memsearch` repo. The only thing you add
by hand is the saved-memories folder, nested inside the repo at `_backfill\`. The
database recipe is already inside the repo — no separate file needed.

The drive must look exactly like this (`E:` = the drive):

```
E:\
└── memsearch\                                    ← the ENTIRE repo, copied as-is
    ├── pyproject.toml
    ├── plugins\claude-code\podman\
    │   └── docker-compose.yml                    ← Milvus recipe (already in the repo)
    ├── plugins\claude-code\ ...                  (hooks, skills, plugin.json)
    ├── src\memsearch\ ...
    ├── uv.lock
    └── _backfill\                                ← YOU create this folder
        ├── memsearch-global\                     ← copy of C:\Users\dave\.claude\memsearch-global
        │   ├── memory\
        │   │   ├── 2026-06-08.md                 (legacy top-level logs)
        │   │   └── <repo>\<branch>\*.md          (per-project / per-branch logs)
        │   ├── PROJECT.md
        │   ├── USER.md
        │   └── CORRECTIONS.md
        └── scripts\                              ← the conversation-backfill program
            ├── memsearch-backfill-claude.py      (conversation-backfill program)
            ├── _backfill_*.py                    (its helper modules)
            └── memsearch-bucket-rules.json       (optional repo→bucket rules, if present)
```

- `memsearch-global\` holds **the actual saved memories** — this is the data the new PC
  backfills. If you leave `_backfill\` out, the new PC still gets a *working* plugin, but
  with **no past memories** — it will only remember things from then on.
- `scripts\` holds the **conversation-backfill program** the new PC uses in Part 7 to load
  *its own* past Claude conversations. Leave it out and the new PC still gets a working
  plugin with your carried-over memories, but Part 7 won't be available there.
- *(Optional, offline installs only)* also drop `milvus-images.tar` at the drive root
  (`E:\milvus-images.tar`) if the target PC will have **no internet** — see below.

---

## Step A — Copy the repo to the drive

From the repo root on this PC (`D:\Projects\memsearch`):

```powershell
$repo  = "D:\Projects\memsearch"
$drive = "E:"   # <-- your thumb drive letter

# Copy the repo, skipping the heavy throwaway folders (smaller/faster copy).
robocopy $repo "$drive\memsearch" /E `
  /XD ".git" ".venv" ".pytest_cache" ".ruff_cache" "site" "__pycache__" `
  /XF "*.pyc"
```

> `robocopy` is built into Windows. The `/XD` list excludes folders the new PC rebuilds
> anyway; copying them just wastes space. (Copying the *whole* repo including `.git` also
> works — it's only a size optimization.)

## Step B — Add the memories into `_backfill\`

```powershell
$drive = "E:"
New-Item -ItemType Directory -Force "$drive\memsearch\_backfill" | Out-Null
Copy-Item "$env:USERPROFILE\.claude\memsearch-global" `
          "$drive\memsearch\_backfill\memsearch-global" -Recurse -Force
```

**Check it worked:**

```powershell
dir -Recurse -Filter *.md "$drive\memsearch\_backfill\memsearch-global\memory" | select -First 10
```

You should see dated `.md` files (e.g. `2026-06-08.md`) — some at the top level, most under
`<repo>\<branch>\` subfolders. ✅

## Step B2 — Add the conversation-backfill program into `_backfill\scripts\`

This is the program the new PC runs in Part 7 to load *its own* past Claude conversations.
Skip it only if you don't want that capability on the new PC.

```powershell
$drive = "E:"
$dst = "$drive\memsearch\_backfill\scripts"
New-Item -ItemType Directory -Force $dst | Out-Null

# The backfill program + its helper modules only (not the rest of ~\.claude\scripts).
Copy-Item "$env:USERPROFILE\.claude\scripts\memsearch-backfill-claude.py" $dst -Force
Copy-Item "$env:USERPROFILE\.claude\scripts\_backfill_*.py"                $dst -Force

# Bucket rules — keeps repo→bucket mapping consistent across machines (copy if you have it).
if (Test-Path "$env:USERPROFILE\.claude\memsearch-bucket-rules.json") {
  Copy-Item "$env:USERPROFILE\.claude\memsearch-bucket-rules.json" $dst -Force
}
```

**Check it worked:**

```powershell
dir "$drive\memsearch\_backfill\scripts"
```

You should see `memsearch-backfill-claude.py`, the `_backfill_*.py` helpers, and (if you had
one) `memsearch-bucket-rules.json`. ✅ (If `~\.claude\scripts` doesn't exist on this PC, the
program isn't installed here — there's nothing to carry, and the new PC simply won't have
Part 7.)

## Step C *(optional)* — Bundle the database images for an offline install

Skip this if the new PC has internet — it will download the images itself on first run.
Do it only if the new PC is **offline**. Requires Podman running on this PC:

```powershell
$drive = "E:"
podman save -o "$drive\milvus-images.tar" `
  milvusdb/milvus:v2.6.17 `
  quay.io/coreos/etcd:v3.5.18 `
  minio/minio:RELEASE.2023-03-20T20-16-18Z
```

This produces a ~1–2 GB file. The install guide's Step 5 loads it with `podman load`.

---

## Done

Eject the drive and take it to the new PC. Continue with
[INSTALL-ON-NEW-WINDOWS-PC.md](INSTALL-ON-NEW-WINDOWS-PC.md).
