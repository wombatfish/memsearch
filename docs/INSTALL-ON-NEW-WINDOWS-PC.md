# Installing the memsearch memory plugin on a new Windows PC

A complete, copy-paste, step-by-step guide. **No prior technical knowledge assumed.**
This installs the "automatic memory" plugin for Claude Code onto a Windows PC where
**Claude Code is already installed** and the Windows account is **not** "dave".

Every command below uses `%USERPROFILE%` (PowerShell: `$env:USERPROFILE`), which
automatically means *your* user folder — so nothing is hard-coded to "dave" and it
works for any username.

> **Time needed:** about 45–60 minutes, most of it waiting for downloads — longer if you
> have to enable virtualization in BIOS or reboot for WSL (see Step 0).
> **You will do almost everything inside one blue "PowerShell" window.**

---

## Before you start — what this needs

The memsearch **program, the plugin, and the Milvus recipe are pulled straight from the
public fork on GitHub** (`wombatfish/memsearch`, branch `qmaster-custom`) during install —
you do **not** copy the repo onto this PC. So the thumb drive only needs to carry your
**personal data** (not on GitHub): your saved memories and the conversation-backfill program.
**This PC needs internet** for the install (to pull the program/plugin from GitHub and the
database image from the registry).

➡️ **If the drive isn't prepared yet, do that first:** see
[PREP-THUMBDRIVE-FOR-INSTALL.md](https://github.com/wombatfish/memsearch/blob/qmaster-custom/docs/PREP-THUMBDRIVE-FOR-INSTALL.md).

Quick sanity check — plug the drive in and confirm `_backfill\` is present (`E:` = the drive):

```
E:\
├── docker-compose.yml                          ← Milvus recipe (offline installs only)
├── milvus-images.tar                           ← Milvus container images (offline installs only)
└── memsearch\
    └── _backfill\
        ├── memsearch-global\                    ← the saved memories you'll backfill
        └── scripts\                             ← the conversation-backfill program (Part 7)
```

> A drive prepared the **old** way also has the full repo tree alongside `_backfill\` —
> that's fine, the repo code is simply ignored now (only `_backfill\` is read).

If `_backfill\memsearch-global\` is missing you'll still get a *working* plugin, but with
**no past memories** to backfill. If `_backfill\scripts\` is missing you'll still get a
working plugin and your carried-over memories, but Part 7 (loading *this* PC's own past
conversations) won't be available. *(Truly offline? Keep the full repo on the drive and use
the offline notes in Steps 5 and 7;* `E:\milvus-images.tar` *should also be present.)*

---

## How to open the PowerShell window (you'll need it a lot)

1. Press the **Windows key**.
2. Type **PowerShell**.
3. Click **Windows PowerShell**.
4. A blue window opens. This is where you paste commands.

To paste: **right-click** inside the blue window, then press **Enter** to run.

> **If a command ever says "Access is denied":** close PowerShell, then reopen it
> **as Administrator** — press the Windows key, type **PowerShell**, **right-click**
> *Windows PowerShell*, choose **Run as administrator**, and click **Yes** on the
> prompt. Re-run the command there.

---

# Step 0 — Before you begin (a 5-minute readiness check)

The memory database runs inside Podman, which needs a Windows feature called **WSL2**.
Two things must be true first, or the install **will fail in Step 4** with confusing
errors. Check them now.

**A. Hardware virtualization must be ON.**

1. Press **Ctrl + Shift + Esc** to open **Task Manager**.
2. Click the **Performance** tab, then **CPU**.
3. Look for **Virtualization:** on the right. It must say **Enabled**.

If it says **Disabled**, you must turn it on in your PC's BIOS/UEFI (this is a firmware
setting, not Windows): restart the PC, press the firmware key during boot (often **F2**,
**Del**, or **F10** — the boot screen usually says which), find a setting named
**Intel VT-x** / **Intel Virtualization Technology** or **SVM Mode** (AMD), set it to
**Enabled**, save, and exit. If you're not comfortable doing this, **stop here and get
help** — Podman cannot run without it.

**B. Expect interruptions.** During Part 1 you will:
- see **UAC pop-ups** ("Do you want to allow this app to make changes?") — click **Yes**;
- likely be asked to **restart the PC once** (when WSL is enabled) — that's normal, just
  resume at the next step afterward.

> **Tip — find your details now.** Paste these into the blue PowerShell window; you'll
> need them later:
> ```powershell
> echo "Your username: $env:USERNAME"
> Get-Volume | Where-Object DriveType -eq 'Removable' | Select-Object DriveLetter, FileSystemLabel, @{n='FreeGB';e={[math]::Round($_.SizeRemaining/1GB,1)}}
> ```
> The second line lists removable drives — the letter shown is your **thumb drive letter**
> (used as `E:` below).

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
   **If it asks to restart, do it**, then re-launch Podman Desktop and let it finish.

   > **If Podman does *not* offer to set up WSL** (or setup fails), open PowerShell
   > **as Administrator** (see the box above Step 0) and run:
   > ```powershell
   > wsl --install --no-distribution
   > ```
   > **Restart the PC when it finishes**, then re-open Podman Desktop and let it
   > initialize the machine. (You don't need a Linux distro — Podman supplies its own.)

5. **Confirm the engine is running.** In a normal PowerShell window, paste:

   ```powershell
   podman machine list
   ```

   You should see a machine (usually `podman-machine-default`) with **Running** under
   *LAST UP* / *Running*. If it says *Stopped*, paste `podman machine start` and wait. ✅

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

## Step 5 — Copy your saved data off the drive, get the recipe, start the database

1. Copy your **personal data** (`_backfill\`) off the thumb drive. The repo code is *not*
   copied — the program comes from GitHub in Step 7. **Replace `E:` with your thumb drive's
   letter** if different (check "This PC" in File Explorer):

   ```powershell
   $tdrive = "E:"   # <-- change E: to your thumb drive letter if needed
   Copy-Item "$tdrive\memsearch\_backfill" "$env:USERPROFILE\memsearch\_backfill" -Recurse -Force
   ```

2. Download the database recipe (the Milvus `docker-compose.yml`) from the cloud fork into
   the place the plugin expects it. Paste:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.memsearch\milvus" | Out-Null
   Invoke-WebRequest -UseBasicParsing `
     -Uri "https://raw.githubusercontent.com/wombatfish/memsearch/qmaster-custom/plugins/claude-code/podman/docker-compose.yml" `
     -OutFile "$env:USERPROFILE\.memsearch\milvus\docker-compose.yml"
   ```

   > **No internet?** The prepared drive carries the recipe at its root — copy it instead:
   > ```powershell
   > Copy-Item "$tdrive\docker-compose.yml" "$env:USERPROFILE\.memsearch\milvus\docker-compose.yml" -Force
   > ```

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

## Step 7 — Install the memsearch program (from the cloud fork)

This pulls the fork's `qmaster-custom` branch straight from GitHub and builds it — no
repo copy needed. (Git, from Step 1, does the fetch.)

1. Install the program. Paste:

   ```powershell
   uv tool install --force --no-cache "memsearch[onnx] @ git+https://github.com/wombatfish/memsearch.git@qmaster-custom"
   ```

   > `--no-cache` is important — without it the installer can silently reuse an old build.
   > This installs the **fork** pinned to `qmaster-custom` — *not* the stock PyPI
   > `memsearch` (which is a different, higher-versioned project).

   > **No internet?** This step fetches and builds the fork from GitHub, and the prepared
   > drive does **not** carry the memsearch source (only your `_backfill\` data) — so there is
   > **no offline install** for the program itself; this step needs internet. *(A drive prepped
   > the old way with the full repo could instead run
   > `cd "$tdrive\memsearch"; uv tool install --force --no-cache ".[onnx]"`.)*

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

1. Set the memory location permanently (so Claude's plugin always finds it), and turn
   off the upstream update check. This is a **fork** build: the public PyPI version is
   *higher* than yours, so the check would nag you to "upgrade" with a command that
   replaces your fork with the stock version. Paste all four lines:

   ```powershell
   setx MEMSEARCH_DIR "$env:USERPROFILE\.claude\memsearch-global"
   setx MEMSEARCH_NO_UPDATE_CHECK 1
   $env:MEMSEARCH_DIR = "$env:USERPROFILE\.claude\memsearch-global"
   $env:MEMSEARCH_NO_UPDATE_CHECK = "1"
   ```

   > ⚠️ **`setx` only affects programs started *after* it runs.** If Claude Code is
   > already open, **fully close and reopen it** (not just `/clear`) before Step 9, so it
   > inherits `MEMSEARCH_DIR`. Without that variable the plugin falls back to *per-project*
   > memory and the backfill (Step 11) lands in a collection the session never reads.

2. Copy your actual saved memories. They were copied off the drive to
   `~\memsearch\_backfill\` in Step 5. Paste:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude" | Out-Null
   Copy-Item "$env:USERPROFILE\memsearch\_backfill\memsearch-global" `
             "$env:USERPROFILE\.claude\memsearch-global" -Recurse -Force
   ```

   > Only relevant if this PC has **already** used the plugin before: this copy
   > **overwrites** same-named daily files, it does not merge them. On a fresh PC
   > there is nothing to overwrite, so ignore this.

3. **Check it worked.** Memories are now filed in per-project / per-branch subfolders,
   so list them recursively:

   ```powershell
   dir -Recurse -Filter *.md "$env:USERPROFILE\.claude\memsearch-global\memory" | select -First 10
   ```

   You should see dated files like `2026-06-08.md` — some at the top level, most under
   subfolders such as `memsearch\<branch>\`. ✅ The three curated files `CORRECTIONS.md`,
   `PROJECT.md`, and `USER.md` sit one level up in `memsearch-global\` itself; the copy in
   step 2 brought those across too (the plugin injects them at session start).

---

# Part 5 — Install the plugin into Claude

## Step 9 — Add and install the plugin

> ### ⚠️ First, if you restored your `~\.claude` profile from a dotfiles/profiles repo
>
> A profile restore carries your **dev machine's** plugin registration with it: a
> `memsearch-local` marketplace whose source is `D:\Projects\memsearch` — a path that does
> **not** exist on this PC. Claude Code then can't resolve the plugin, so every memsearch
> hook fires with an empty `CLAUDE_PLUGIN_ROOT` and **crashes**
> (`bash "/hooks/session-end.sh": No such file or directory`). Strip the stale entry before
> installing the real one. In **PowerShell**, check for it:
>
> ```powershell
> Select-String -Path "$env:USERPROFILE\.claude\settings.json" -Pattern "memsearch-local"
> ```
>
> If nothing prints, skip ahead. If it matches, open
> `"$env:USERPROFILE\.claude\settings.json"` in a text editor and delete **both**:
>
> 1. the `"memsearch@memsearch-local": true` line inside `"enabledPlugins"`, and
> 2. the entire `"memsearch-local": { ... }` block inside `"extraKnownMarketplaces"`.
>
> Save. (The correct `memsearch-plugins` marketplace is added in the steps below.) Unsure
> about the JSON edit? Ask Claude Code to do it for you.

Do this **inside Claude Code**, not in PowerShell.

1. Open **Claude Code**.
2. Add the plugin marketplace **from the cloud fork, pinned to the `qmaster-custom`
   branch**. Type this and press Enter:

   ```
   /plugin marketplace add https://github.com/wombatfish/memsearch.git#qmaster-custom
   ```

   > The `#qmaster-custom` suffix pins the branch. Without it you'd get the fork's default
   > `main` branch, which is not what runs here.

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

> ### 🟢 Easy mode — let Claude do this for you (recommended for non-technical users)
>
> Claude Code is installed and working now, so you can skip the manual Steps 10–11.
> In your Claude Code session, **paste this request**:
>
> > *Read the memsearch collection name from your status line, then in PowerShell run:
> > stop the watcher for that collection, run `memsearch index` on
> > `%USERPROFILE%\.claude\memsearch-global\memory` with `--replace` for that collection,
> > and finally verify with a `memsearch search --no-daemon`. Show me the results.*
>
> Claude will read its own status line, run the commands, and confirm it worked. If you'd
> rather do it by hand, follow Steps 10–11 below.

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
   your notes, e.g. a project name). `--no-daemon` makes the search read the database
   directly instead of routing through the background watcher:

   ```powershell
   memsearch search "milvus podman windows" --collection $col --no-daemon
   ```

   You should see a few matching results with snippets of your old notes. ✅

## Step 12 — Restart Claude and you're done

Close and reopen **Claude Code** one last time. From now on:

- Your **past memories are searchable** (Claude pulls them in automatically when relevant).
- **New memories are saved automatically** at the end of each session — no action needed.
- The database **auto-starts** with Claude on future days.

🎉 **Installation complete.**

---

# Part 7 — (Optional) Load *this* PC's own past conversations

Everything above loaded the memories **carried over from the original PC**. But this PC
has its *own* past Claude Code conversations (the raw chat logs) that aren't in memory yet.
This step reads those, writes a short summary of each, and adds them so Claude can recall
this PC's history too. It's optional, and it's **separate** from the Step 11 backfill
(Step 11 loaded already-summarized notes; this reads raw conversation logs).

> ⚠️ **CLAUDE CODE — READ THIS. You must do the following before you tell the user the
> install is finished.**
>
> The install is otherwise complete. Now present this offer to the user **in plain
> language** and wait for their answer:
>
> > "I can also load **this** computer's own past Claude conversations into memory (separate
> > from the memories I just restored from the other PC). It's optional and may take a few
> > minutes up to about half an hour, because I summarize each past conversation one at a
> > time. Want me to do that now?"
>
> - **If the user says no:** tell them they can ask anytime by saying *"load this PC's past
>   conversations into memory"*, then finish. Do **not** run the commands.
> - **If the user says yes:** run the steps in **Step 13** below using your PowerShell tool,
>   then report the verification result. Let it run to completion — it spawns short
>   `claude` summarizer subprocesses per conversation turn, so it is slow but unattended.

## Step 13 — Run the conversation backfill (only after the user agrees)

1. Put the backfill program in place. This prefers the on-disk repo copy (made in Step 5,
   so it still works if the thumb drive has been removed) and **automatically falls back to
   the thumb drive** if this PC was installed from an older drive that didn't carry the
   program. No drive letter to type — it scans the plugged-in removable drives:

   ```powershell
   # Locate the backfill program: on-disk copy first, else any plugged-in removable drive.
   $src = "$env:USERPROFILE\memsearch\_backfill\scripts"
   if (-not (Test-Path "$src\memsearch-backfill-claude.py")) {
       $src = Get-Volume | Where-Object DriveType -eq 'Removable' |
           ForEach-Object { "$($_.DriveLetter):\memsearch\_backfill\scripts" } |
           Where-Object { Test-Path "$_\memsearch-backfill-claude.py" } |
           Select-Object -First 1
   }
   if (-not $src) {
       throw "Backfill program not found on disk or any plugged-in drive. Plug in the prepared thumb drive (with _backfill\scripts\) and re-run."
   }
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude" | Out-Null
   Copy-Item $src "$env:USERPROFILE\.claude\" -Recurse -Force
   # Bucket rules (bundled alongside the program) — keeps repo→bucket mapping consistent
   # across your machines. Lands in the default location the program reads.
   $rules = "$env:USERPROFILE\.claude\scripts\memsearch-bucket-rules.json"
   if (Test-Path $rules) { Copy-Item $rules "$env:USERPROFILE\.claude\memsearch-bucket-rules.json" -Force }
   "Copied backfill program from: $src"
   ```

2. Run the backfill. Replace `ms_memsearch_global_xxxxxxxx` with the collection name from
   the status line (same name as Step 10):

   ```powershell
   $col = "ms_memsearch_global_xxxxxxxx"   # <-- your collection name (status line)

   # Memory location (already set in Step 8; re-assert in case this is a fresh window)
   $env:MEMSEARCH_DIR = "$env:USERPROFILE\.claude\memsearch-global"

   # Without this, every conversation is skipped as "already handled live" — the built-in
   # cutoff predates this PC's history. Push it to tomorrow so all past chats are included.
   $env:MEMSEARCH_BACKFILL_DATE_GUARD = (Get-Date).AddDays(1).ToString('yyyy-MM-dd')

   # Pause the live updater so it doesn't fight the backfill, then run it.
   memsearch watch --stop --collection $col
   python "$env:USERPROFILE\.claude\scripts\memsearch-backfill-claude.py" --yes
   ```

   The program reads every past conversation, summarizes each turn, files the notes, and
   indexes them into the database at the end. It is **safe to re-run** — already-loaded
   conversations are skipped automatically.

3. **Check it worked.** Search for something you remember discussing on this PC
   (`--no-daemon` reads the database directly):

   ```powershell
   memsearch search "something from a past chat on this PC" --collection $col --no-daemon
   ```

   You should see matching snippets from your old conversations. ✅ The live updater
   restarts on its own next time you open Claude Code.

🎉 **All done — past conversations from this PC are now searchable.**

---

# Environment variables (reference)

You don't set anything by hand *before* starting — the steps above create what's needed.
This is a map of what's used and when, and the few conditional keys to be aware of.

**Managed by this install (the steps set these for you):**

| Variable | Set in | Purpose |
|---|---|---|
| `MEMSEARCH_DIR` | Step 8 (`setx`, persisted) | Points the plugin **and** the backfill at the one global memory collection. ⚠️ `setx` only affects programs started *afterward* — **fully close and reopen Claude Code after Step 8** (not `/clear`) so it inherits this. If it doesn't, the plugin silently falls back to a *per-project* collection and the backfill lands where nothing reads. This is the single most common failure. |
| `MEMSEARCH_NO_UPDATE_CHECK` | Step 8 (`setx`, persisted) | Silences the status-line "update available" nag, which would otherwise push the stock PyPI build over this fork. |
| `MEMSEARCH_BACKFILL_DATE_GUARD` | Part 7 / Step 13 (temporary) | Forces the conversation backfill to include this PC's existing history (the built-in cutoff would otherwise skip it all). Lives only for that one PowerShell window. |

**Automatic — already present, nothing to do:** `USERPROFILE` (Windows sets it; the backfill
locates your history at `%USERPROFILE%\.claude\projects\` through it) and `PATH` (the Step 1–4
installers add `git`, `python`, `uv`, `memsearch`, and `claude`).

**Conditional keys — normally absent, and the default install needs none of them.** Memory
summarization (the Stop hook and the Part 7 backfill) uses your **Claude Code subscription**
(Anthropic Haiku) with **no API key**. Set one of these *only* if a specific path below
applies and it isn't already set:

- `ANTHROPIC_API_KEY` — **not needed** by default. Set it **only if** you switch the backfill
  to the API summarizer (`--llm anthropic-api`, e.g. to avoid subscription rate limits on a
  very large history). ⚠️ **If you intend to use that mode and this key is not already set,
  set it before running Part 7** — the backfill aborts at preflight without it.
- `OPENAI_API_KEY` — **not needed** by this install. Only the separate `memsearch compact`
  command (not used here) would require it, and only because its LLM provider defaults to
  OpenAI when left unconfigured.
- `CLAUDE_CONFIG_DIR` — **do not set this.** Its absence is correct and means Claude Code uses
  the default `~/.claude`. It matters only if it is *already* set to a non-default location on
  this PC — then the backfill won't auto-find your history and you pass
  `--claude-home "<that dir>"` to the program in Step 13 instead.

---

# If something goes wrong

| Problem | Fix |
|---|---|
| `podman ps` shows fewer than 3 containers, or the status line says **UNREACHABLE** | Run `podman machine start` then `cd "$env:USERPROFILE\.memsearch\milvus"; podman compose up -d`. Wait 90 seconds, then start a new Claude session. |
| Status line says **`ERROR: memsearch not found`** | Re-do Step 7. Make sure you opened a fresh PowerShell window afterwards. |
| **memsearch hooks crash** with `bash "/hooks/session-end.sh": No such file or directory` (or any `/hooks/*.sh` not found) | Your restored profile carries the dev-box `memsearch-local` marketplace (source `D:\Projects\memsearch`), which is absent here — so the plugin can't load and `CLAUDE_PLUGIN_ROOT` expands to empty. Do the **Step 9 ⚠️ prelude**: remove the `memsearch@memsearch-local` and `memsearch-local` entries from `~\.claude\settings.json`, keep only `memsearch@memsearch-plugins`, then `/reload-plugins`. |
| Database won't start / Podman errors about memory | Re-do the `.wslconfig` part of Step 4, then run `wsl --shutdown`, wait 10s, and try Step 5 again. |
| **Podman machine won't initialize/start**, or errors mention **WSL** or **virtualization / hypervisor / VT-x** | Virtualization is almost certainly off — redo **Step 0 part A** (enable it in BIOS/UEFI). Then, in an **Administrator** PowerShell, run `wsl --install --no-distribution`, **restart the PC**, reopen Podman Desktop, and let it initialize. Confirm with `podman machine list` (Step 4.5). |
| Backfill `search` returns nothing | Double-check the collection name matches the status line exactly (Step 10). Re-run Step 11. |
| `memsearch` says "not recognized" right after install | Close PowerShell, open a fresh window, try again. The tool was added to PATH only for new windows. |
| Python errors mentioning the Microsoft Store | You installed Store Python. Uninstall it, install from python.org (Step 2), tick "Add to PATH". |
| Status line shows **`UPDATE: vX available — run: uv tool install -U 'memsearch[onnx]'`** | Ignore it — this is a **fork**, and that command would install the *stock* PyPI build over it. The `setx MEMSEARCH_NO_UPDATE_CHECK 1` from Step 8 hides the nag; restart Claude to apply it. To actually update a fork, reinstall from the repo (Step 7). |
| Status line shows `collection: ms_<projectname>_...` instead of `ms_memsearch_global_...` | Claude didn't inherit `MEMSEARCH_DIR`. Confirm Step 8 ran, then **fully close and reopen** Claude Code (not `/clear`). |
| **Backfill (Step 11/13):** `memsearch collection-name` prints `ms_memsearch_<hash>` but the plugin status line shows `ms_memsearch_global_<hash>` | These are **different collections** — the CLI command derives the name differently from the plugin. **Always use the name from the status line** (Step 10), never `collection-name`. Indexing against the CLI name lands the data where your sessions never read it (and you re-run the whole backfill). If you already indexed the wrong one, re-run with the status-line name. |
| **Part 7:** `python` says *"can't open file ... memsearch-backfill-claude.py"* | The program wasn't copied. Plug in the prepared thumb drive (the one with `_backfill\scripts\`) and re-run Step 13.1 — it auto-detects the drive. If it still can't find it (`throw` message), the drive was never prepped with the program — re-prep it (PREP guide, Step B2). |
| **Part 7:** backfill summary shows everything under `skipped: post-install` | The date-guard line didn't run. Re-run the `$env:MEMSEARCH_BACKFILL_DATE_GUARD = ...` line, then the `python ...` line, in the **same** PowerShell window. |
| **Part 7:** `preflight: LLM probe failed` | The summarizer (`claude`) couldn't be reached. Make sure Claude Code is installed and signed in, then re-run Step 13.2. |

---

## Quick reference — the whole thing in order

0. Readiness: virtualization **Enabled** (Task Manager → Performance → CPU); expect UAC + one reboot
1. Git for Windows → 2. Python (python.org, "Add to PATH") → 3. uv → 4. Podman Desktop (+ `wsl --install --no-distribution` if needed) + `.wslconfig` 8GB → confirm `podman machine list` Running
5. Copy `_backfill\` off drive to `~\memsearch\_backfill\` → download `docker-compose.yml` from the cloud fork into `~\.memsearch\milvus\` → `podman compose up -d` → confirm 3 containers
6. Create `~\.memsearch\config.toml` (milvus uri + onnx)
7. `uv tool install --force --no-cache "memsearch[onnx] @ git+https://github.com/wombatfish/memsearch.git@qmaster-custom"` → confirm `memsearch --version`
8. `setx MEMSEARCH_DIR ...` + `setx MEMSEARCH_NO_UPDATE_CHECK 1` + copy `~\memsearch\_backfill\memsearch-global` → `~\.claude\memsearch-global` → fully restart Claude
9. `/plugin marketplace add https://github.com/wombatfish/memsearch.git#qmaster-custom` → `/plugin install memsearch@memsearch-plugins` → `/reload-plugins` → confirm `[memsearch]` line
10–11. Backfill: easiest is to **ask Claude** to read its collection name and run it; or by hand: read collection name → `memsearch watch --stop` then `memsearch index ... --replace` → confirm `memsearch search --no-daemon`
12. Restart Claude — done.
13. *(Optional, Claude offers this automatically at the end)* Load **this PC's own** past conversations: copy `_backfill\scripts` → `~\.claude\scripts`, set `MEMSEARCH_BACKFILL_DATE_GUARD` to tomorrow, `memsearch watch --stop`, `python ~\.claude\scripts\memsearch-backfill-claude.py --yes`, verify with `memsearch search --no-daemon`.
