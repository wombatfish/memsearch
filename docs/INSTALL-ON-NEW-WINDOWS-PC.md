# Installing the memsearch memory plugin on a new Windows PC

A complete, copy-paste, step-by-step guide. **No prior technical knowledge assumed.**
This installs the "automatic memory" plugin for Claude Code onto a Windows PC where
**Claude Code is already installed** and the Windows account is **not** "dave".

Every command below uses `%USERPROFILE%` (PowerShell: `$env:USERPROFILE`), which
automatically means *your* user folder — so nothing is hard-coded to "dave" and it
works for any username.

> **Time needed:** about 45–60 minutes, most of it waiting for downloads.
> **You will do almost everything inside one blue "PowerShell" window.**

---

## Before you start — prepare the thumb drive

This guide assumes you have a thumb drive prepared on the original "dave" PC, containing
the `memsearch` repo with the saved memories nested inside it at `_backfill\`.

➡️ **If the drive isn't prepared yet, do that first:** see
[PREP-THUMBDRIVE-FOR-INSTALL.md](PREP-THUMBDRIVE-FOR-INSTALL.md). It assembles the exact
layout this guide expects.

Quick sanity check — plug the drive in and confirm it looks like this (`E:` = the drive):

```
E:\
└── memsearch\                                    ← the ENTIRE repo
    ├── pyproject.toml
    ├── plugins\claude-code\podman\docker-compose.yml   ← Milvus recipe (in the repo)
    ├── src\memsearch\ ...
    └── _backfill\memsearch-global\              ← the saved memories you'll backfill
```

If `_backfill\memsearch-global\` is missing you'll still get a *working* plugin, but with
**no past memories** to backfill. *(Offline installs:* `E:\milvus-images.tar` *should also
be present — see Step 5.)*

---

## How to open the PowerShell window (you'll need it a lot)

1. Press the **Windows key**.
2. Type **PowerShell**.
3. Click **Windows PowerShell**.
4. A blue window opens. This is where you paste commands.

To paste: **right-click** inside the blue window, then press **Enter** to run.

---

# Part 1 — Install the supporting software

Claude is already installed. We still need four free tools. Install them in this order.

## Step 1 — Install Git for Windows (provides "Git Bash")

The plugin's behind-the-scenes scripts need this.

1. Go to **https://git-scm.com/download/win** in your browser.
2. Download and run the installer.
3. Click **Next** on every screen (the defaults are correct). Click **Install**, then **Finish**.

## Step 2 — Install Python (the real one, NOT the Store version)

> ⚠️ **Do not install Python from the Microsoft Store.** The Store version looks
> installed but secretly fails when scripts try to use it. Use python.org only.

1. Go to **https://www.python.org/downloads/windows/**.
2. Download the latest **"Windows installer (64-bit)"**.
3. Run it. On the very first screen, **tick the box that says "Add python.exe to PATH"** (bottom of the window). This step is critical.
4. Click **Install Now**, then **Close**.

## Step 3 — Install "uv" (the tool that installs memsearch)

1. In the **blue PowerShell window**, paste this and press Enter:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. When it finishes, **close the PowerShell window and open a fresh one** (so it
   notices the new tool). Reopen it the same way as before.

## Step 4 — Install Podman Desktop (runs the memory database)

The memory database (called **Milvus**) cannot run directly on Windows, so it runs
inside a small container managed by Podman.

1. Go to **https://podman.io/** and click **Download** → **Windows**.
2. Run the installer. Click **Next/Install** through the defaults, then **Finish**.
3. Launch **Podman Desktop** from the Start menu.
4. If it asks to **install/initialize a Podman machine** or **enable WSL**, click
   **Yes / Initialize**. Let it finish (this may take several minutes and may ask to restart).

### Give the database enough memory (important — it won't start otherwise)

1. In the blue PowerShell window, paste this to create a small settings file:

   ```powershell
   @"
   [wsl2]
   memory=8GB
   "@ | Set-Content -NoNewline "$env:USERPROFILE\.wslconfig"
   ```

2. Then paste this to apply it:

   ```powershell
   wsl --shutdown
   ```

3. Wait about 10 seconds. Done.

---

# Part 2 — Start the memory database

## Step 5 — Copy the repo to your PC, then start the database

1. First, copy the whole repo off the thumb drive onto your PC (everything else
   reads from this copy). **Replace `E:` with your thumb drive's letter** if different
   (check "This PC" in File Explorer):

   ```powershell
   $tdrive = "E:"   # <-- change E: to your thumb drive letter if needed
   Copy-Item "$tdrive\memsearch" "$env:USERPROFILE\memsearch" -Recurse -Force
   ```

2. Copy the database recipe (which lives **inside** the repo) into the place the
   plugin expects it. Paste:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.memsearch\milvus" | Out-Null
   Copy-Item "$env:USERPROFILE\memsearch\plugins\claude-code\podman\docker-compose.yml" `
             "$env:USERPROFILE\.memsearch\milvus\docker-compose.yml" -Force
   ```

3. **(Offline installs only — skip if this PC has internet.)** If your drive has
   `milvus-images.tar`, load it so Podman doesn't need to download:

   ```powershell
   podman load -i "$tdrive\milvus-images.tar"
   ```

4. Start the database. Paste this:

   ```powershell
   podman machine start
   cd "$env:USERPROFILE\.memsearch\milvus"
   podman compose up -d
   ```

   The first run downloads the database (1–2 GB) if you didn't load it offline —
   this can take several minutes. Wait until it finishes and you get the cursor back.

5. **Check it worked.** Paste:

   ```powershell
   podman ps
   ```

   You should see **three** lines listing `milvus-standalone`, `milvus-etcd`, and
   `milvus-minio`. The database needs about **90 seconds** after this to become fully
   ready. ✅ If you see three containers, the database is installed.

---

# Part 3 — Install the memsearch program and point it at the database

## Step 6 — Create the configuration file

This one small file tells memsearch to use the local database and the free,
no-API-key embedding engine.

Paste this (it creates the folder and the file in one go):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.memsearch" | Out-Null
@"
[milvus]
uri = "http://localhost:19530"

[embedding]
provider = "onnx"
"@ | Set-Content -NoNewline "$env:USERPROFILE\.memsearch\config.toml"
```

## Step 7 — Install the memsearch program

The repo is already on your PC (copied in Step 5), so this is just the install.

1. Install the program from the copied repo folder. Paste:

   ```powershell
   cd "$env:USERPROFILE\memsearch"
   uv tool install --force --no-cache ".[onnx]"
   ```

   > The `--no-cache` part is important — without it the installer can silently
   > install an old copy.

2. **Check it worked.** Paste:

   ```powershell
   memsearch --version
   ```

   You should see a version number like `memsearch, version 0.4.6`. ✅

   > If you instead see *"memsearch is not recognized"*, close PowerShell, open a
   > fresh window, and try `memsearch --version` again.

---

# Part 4 — Put your saved memories in place

## Step 8 — Tell Windows where memories live, and copy them

1. Set the memory location permanently (so Claude's plugin always finds it). Paste:

   ```powershell
   setx MEMSEARCH_DIR "$env:USERPROFILE\.claude\memsearch-global"
   $env:MEMSEARCH_DIR = "$env:USERPROFILE\.claude\memsearch-global"
   ```

2. Copy your actual saved memories. They came inside the repo at `_backfill\`
   (copied to your PC in Step 5). Paste:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude" | Out-Null
   Copy-Item "$env:USERPROFILE\memsearch\_backfill\memsearch-global" `
             "$env:USERPROFILE\.claude\memsearch-global" -Recurse -Force
   ```

   > Only relevant if this PC has **already** used the plugin before: this copy
   > **overwrites** same-named daily files, it does not merge them. On a fresh PC
   > there is nothing to overwrite, so ignore this.

3. **Check it worked.** Paste:

   ```powershell
   dir "$env:USERPROFILE\.claude\memsearch-global\memory"
   ```

   You should see a list of `.md` files (dated like `2026-06-08.md`). ✅

---

# Part 5 — Install the plugin into Claude

## Step 9 — Add and install the plugin

Do this **inside Claude Code**, not in PowerShell.

1. Open **Claude Code**.
2. Type this and press Enter (adjust the path if you copied the project somewhere else):

   ```
   /plugin marketplace add C:\Users\%USERNAME%\memsearch
   ```

   > If Claude doesn't accept `%USERNAME%`, type your real Windows username instead,
   > e.g. `C:\Users\bob\memsearch`.

3. Then type and press Enter:

   ```
   /plugin install memsearch@memsearch-plugins
   ```

4. Then type and press Enter:

   ```
   /reload-plugins
   ```

5. **Start a new Claude Code session** (close and reopen, or run `/clear`).
   At the top you should see a grey status line that begins with **`[memsearch ...]`**.
   It should say `milvus: http://localhost:19530` (not "UNREACHABLE") and
   `embedding: onnx`. ✅

---

# Part 6 — Run the backfill (load your old memories into the database)

This is the final step. It reads all your copied markdown memories and loads them into
the search database so Claude can recall them.

> **Why this is needed:** the plugin only *automatically* indexes new memories from now
> on. Your historical memories must be loaded once, by hand, with the backfill below.
>
> Backfill loads the **markdown memory files only**. It does not read or merge this PC's
> existing Claude conversation logs (the `.jsonl` files) — those are a separate thing and
> are left untouched.

## Step 10 — Find your collection name

The plugin gave your memories a unique database name ("collection"). You need it for the
backfill so it matches exactly.

Look at the grey **`[memsearch ...]`** status line at the top of your Claude session
(from Step 9). Near the end it says:

```
... | collection: ms_memsearch_global_xxxxxxxx
```

**Copy that whole `ms_memsearch_global_xxxxxxxx` name.** You'll paste it below.

> Can't find it? In Claude, just ask: *"What memsearch collection name is shown in the
> status line?"* and it will read it back to you.

## Step 11 — Run the backfill

1. Go to the **blue PowerShell window**.
2. Paste the command below, **but first replace** `ms_memsearch_global_xxxxxxxx`
   with the exact name you copied in Step 10:

   ```powershell
   $col = "ms_memsearch_global_xxxxxxxx"   # <-- paste YOUR collection name here

   # Stop the live watcher so it doesn't fight the backfill
   memsearch watch --stop --collection $col

   # Load all your historical memories into the database
   memsearch index "$env:USERPROFILE\.claude\memsearch-global\memory" --collection $col --replace
   ```

   This takes a minute or two the first time (it loads the embedding engine, then
   processes every memory file). Wait for the cursor to return.

3. **Check it worked.** Paste (replace the search words with something you know is in
   your notes, e.g. a project name):

   ```powershell
   memsearch search "milvus podman windows" --collection $col
   ```

   You should see a few matching results with snippets of your old notes. ✅

## Step 12 — Restart Claude and you're done

Close and reopen **Claude Code** one last time. From now on:

- Your **past memories are searchable** (Claude pulls them in automatically when relevant).
- **New memories are saved automatically** at the end of each session — no action needed.
- The database **auto-starts** with Claude on future days.

🎉 **Installation complete.**

---

# If something goes wrong

| Problem | Fix |
|---|---|
| `podman ps` shows fewer than 3 containers, or the status line says **UNREACHABLE** | Run `podman machine start` then `cd "$env:USERPROFILE\.memsearch\milvus"; podman compose up -d`. Wait 90 seconds, then start a new Claude session. |
| Status line says **`ERROR: memsearch not found`** | Re-do Step 7. Make sure you opened a fresh PowerShell window afterwards. |
| Database won't start / Podman errors about memory | Re-do the `.wslconfig` part of Step 4, then run `wsl --shutdown`, wait 10s, and try Step 5 again. |
| Backfill `search` returns nothing | Double-check the collection name matches the status line exactly (Step 10). Re-run Step 11. |
| `memsearch` says "not recognized" right after install | Close PowerShell, open a fresh window, try again. The tool was added to PATH only for new windows. |
| Python errors mentioning the Microsoft Store | You installed Store Python. Uninstall it, install from python.org (Step 2), tick "Add to PATH". |

---

## Quick reference — the whole thing in order

1. Git for Windows → 2. Python (python.org, "Add to PATH") → 3. uv → 4. Podman Desktop + `.wslconfig` 8GB
5. Copy repo to `~\memsearch` → copy bundled `plugins\claude-code\podman\docker-compose.yml` into `~\.memsearch\milvus\` → `podman compose up -d` → confirm 3 containers
6. Create `~\.memsearch\config.toml` (milvus uri + onnx)
7. `cd ~\memsearch` → `uv tool install --force --no-cache ".[onnx]"` → confirm `memsearch --version`
8. `setx MEMSEARCH_DIR ...` + copy `~\memsearch\_backfill\memsearch-global` → `~\.claude\memsearch-global`
9. `/plugin marketplace add` → `/plugin install` → `/reload-plugins` → confirm `[memsearch]` line
10. Read collection name from status line
11. `memsearch watch --stop` then `memsearch index ... --replace` → confirm `memsearch search`
12. Restart Claude — done.
