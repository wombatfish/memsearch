# TeamMem standalone cutover audit

Date: 2026-07-11

## Published code repository

- Private repository: `https://github.com/RobsonSavage/teammem`
- Initial reviewed extraction: `a8e149da3e7f208f2e7a8055d1433ca9adabc8de`
- Legacy-upgrade fixes: `34acb292b473cb445640f30fdcb6e02163046355`
- Soak head: `d60e100cc53ebc7d19a8dfc510ea80fc7162e17d`
- Full-history gitleaks scan after publication: clean.

## Deployment surfaces

| Surface | Cutover evidence |
|---|---|
| uv tool | Clean private-Git install succeeded, then a forced no-cache upgrade moved the receipt from `a8e149d` to `34acb29`. The installed module is `teammem`; the old `memsearch` tool remains available only as rollback. |
| Claude Code | `teammem@teammem-plugins` is enabled from the deployed `0.4.6` cache at commit `34acb29`. The cached hook, skill, and MCP manifest contain the renamed command, private source, and `mcp__plugin_teammem_teammem__*` tools. The old plugin is uninstalled; its marketplace registration is retained for rollback during soak. Marketplace refresh plus uninstall/reinstall passed. |
| Codex | All three live hooks point to `D:/Projects/teammem/plugins/codex`. A timestamped pre-cutover hooks backup exists under `~/.codex/`. Clean-profile simulation and two live idempotent installer runs passed. |
| Gemini | The legacy `memsearch` extension is removed. The enabled `teammem` link resolves to `D:/Projects/teammem/plugins/gemini`. The Windows libuv post-success assertion was traced to the Node 23/24 shutdown race; the installer now accepts only that exact signature after independently verifying persisted link metadata, effective enablement, and a successful readback. A final live upgrade completed normally. |
| OpenCode | The legacy `memsearch.ts` link is removed. `teammem.ts`, `memory-recall`, and `memory-config` resolve into `D:/Projects/teammem`; an idempotent live reinstall passed. Runtime configuration loaded the new plugin, and its capture daemon uses the new repository plus the expected collection. |
| OpenClaw | Not installed on this seat; Step 2.3 marks this surface as conditional, so it is N/A rather than provisioned as new scope. |

## Personal-data migration

The explicit data root and collection remained unchanged:

- data root: `%USERPROFILE%/.claude/memsearch-global`
- collection: `ms_memsearch_global_25623e13`
- Milvus: local server at `http://localhost:19530`
- embedding identity: `onnx` / `gpahal/bge-m3-onnx-int8`

The old OpenCode capture daemon and old MemSearch watcher were stopped before
the baseline. Two consecutive Strong-consistency snapshots matched.

### Backup and source integrity

- Backup: `%USERPROFILE%/.claude/memsearch-backups/teammem-cutover-20260711-204327/MEMSEARCH_DIR`
- Full tree: 1,820 files, 7,940,754 bytes.
- Full-tree aggregate: `46f6886242ee068d2a284dbf5b2908570b9dd05786decfbc931e65b633b6c92b`.
- Markdown: 282 files, 7,638,666 bytes.
- Markdown aggregate before and after: `2119aec5bd2524dbe751234a2d4955a8be466b2ef6419e6ff0b862370ec7eb73`.
- Source and backup aggregates matched before re-indexing. The post-index
  Markdown aggregate remained identical.

Each aggregate hashes sorted records of relative path, byte length, and file
SHA-256; it is not merely a hash of concatenated file bodies.

### Collection continuity

| Measure | Before | After |
|---|---:|---:|
| Rows | 7,869 | 7,869 |
| Distinct `chunk_hash` values | 7,869 | 7,869 |
| Duplicate primary keys | 0 | 0 |
| Indexed sources | 277 | 277 |
| Sorted-key-set aggregate | `e5d6e4ecf9649cff5ad46f9cb08b17c5c0725304b6b013186d18720a4180b176` | same |

The ordinary full-tree scanner initially produced 7,683 chunks from 264
non-empty visible sources. Diagnosis showed that the live watcher had also
indexed 13 Markdown files under the hidden `.agent-config` directory via file
events; those 13 sources accounted for exactly 186 chunks. Explicitly
re-indexing that evidence-derived compatibility set restored the exact
pre-migration key-set aggregate. The one older hidden file that had never been
in the live collection was deliberately not added.

### Recall continuity

Five fixed queries were captured before and after. All five retained the same
top result. Four queries have pre-registered benchmark gold hashes; every gold
hash remained present at the same rank (1, 4, 1, and 3 respectively). The fifth
query had no gold hit in the pre-migration top five. Lower non-gold top-five
ordering changed after embeddings and graph edges were recomputed, so the audit
does not claim byte-identical ranking below the top result.

The deployed `teammem` surface returned the historical cp1252/Stop-hook gold
chunk `d2803cc6e790b3df` first through the live collection. A new TeamMem watcher
is active on the original data root and collection; no old `memsearch watch`
process remains.

## Verification at soak head

- 402 repository tests passed; 17 platform-dependent tests skipped.
- The live OpenAI exact-float determinism test remains excluded from the final
  deterministic gate because repeated provider calls returned tiny numerical
  differences. This is not coupled to the rename or plugin cutover.
- Strict MkDocs and Python sdist/wheel builds passed.
- The four-commit fresh history passed gitleaks.
- The final code repository and all deployed/code-linked surfaces are on
  `d60e100`.

## Soak window

- Start: 2026-07-11 21:27 Africa/Johannesburg.
- Earliest completion: 2026-07-25 21:27 Africa/Johannesburg.
- The old `wombatfish/memsearch` fork remains unarchived and its old uv tool and
  Claude marketplace registration remain available as rollback sources.
- Do not tag/archive the old fork or begin Step 2.6 before the earliest
  completion time and a clean verification across every installed surface.

## Operational security follow-up

An OpenCode diagnostic command printed a GitHub PAT and a ref.tools API key into
the agent tool transcript while verifying plugin load. Neither value was written
to either repository, and all repository secret scans remain clean, but both
credentials must be rotated because transcript exposure is still exposure.
