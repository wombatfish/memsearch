---
name: memsearch-fork-discipline
description: Use BEFORE editing plugin.json, marketplace.json, pyproject, or any upstream-owned file in this repo, or when tempted to bump a version or run pip install --upgrade memsearch - this repo is a fork of upstream memsearch (branch qmaster-custom) and upstream owns versioning.
---

# memsearch fork discipline

This repo is a fork of upstream memsearch (working branch `qmaster-custom`). Upstream owns versioning and file layout; every local change must keep the merge surface against upstream minimal.

## Rules

- **Never bump version fields locally** - `plugin.json`, `marketplace.json`, `pyproject`. A local bump collides on the next upstream merge and falsely triggers plugin-cache refresh semantics.
- **New files over edits.** Add capability as new files wherever possible. When an upstream file must change, keep it to one minimal insertion block.
- **Never run the SessionStart banner's `pip install --upgrade memsearch` nag** - it overwrites the fork CLI with upstream PyPI. Update from this local checkout instead.
- **Consequence of pinned versions:** plugin caches never self-refresh. Deploy edits via explicit cache re-sync (copy over each registry's installPath, or uninstall + reinstall) - not via `claude plugin update`.

## Pitfall

Bumping the plugin version to make `claude plugin update` notice changes - this violates fork discipline; the correct refresh is copying over the installPath or uninstall + reinstall.

## Verify

`git diff` in this repo shows no version-field changes; upstream-owned files show at most minimal insertion blocks; `pip show memsearch` still reports the fork install (local checkout), not PyPI.
