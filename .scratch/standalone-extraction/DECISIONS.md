# Standalone extraction decisions

Approved by the user on 2026-07-11. These decisions resolve Part 4 prerequisites
D1-D4 in
`C:\Users\dave\.claude\plans\examine-https-github-com-googlecloudplat-transient-nova.md`.

## D1: repository topology and identity

- Code repository: `RobsonSavage/teammem`
- Visibility: private
- History: fresh start
- Ownership: the `RobsonSavage` organization
- Topology: code and curated facts live in separate repositories

## D2: package, CLI, environment, and tool names

- Python distribution and CLI: `teammem`
- Python import namespace: `teammem`
- Retain the `MEMSEARCH_*` environment-variable prefix for compatibility
- Retain the `memory_search` and `memory_expand` MCP tool names
- Rename product/plugin/package identities that falsely imply Zilliz ownership;
  exact registry scopes must be verified before publication and are not guessed
- License attribution: retain the Zilliz MIT notice and add
  `Copyright (c) 2026 Robson Savage`
- Remove the optional Zilliz support-email field; do not publish a replacement
  address

## D3: facts repository and security boundary

- Facts repository: `RobsonSavage/teammem-facts`
- Visibility: private
- Initial contents: empty; promotion-only, with no historical digest backfill
- Default branch: `main`
- Required checks: recursive case-folded slug collision, secret/scrub, and
  privacy/policy checks; strict up-to-date-branch enforcement
- Review gate: one blocking human approval. The organization owner confirmed on
  2026-07-11 that Secret Protection is disabled and will remain disabled.
- Do not enable zero-approval auto-merge without the independent Secret
  Protection layer.
- CODEOWNERS is required for facts and security-critical CI paths.

## D4: old fork disposition

- Keep `wombatfish/memsearch` live throughout cutover and the minimum two-week
  soak.
- After a clean soak, tag the exact pre-cutover commit as the immutable fallback
  and archive/freeze the old fork; never delete it.
- The new repository does not carry gsync/upstream version-line discipline.

## Gates still outstanding

- Step 1 A/B must report before Step 2 starts.
- Secret scan of the extracted current tree is mandatory. A fresh-start private
  repository avoids importing old history but does not waive scanning the tree.
- The old fork cannot be archived before the minimum two-week soak completes.
