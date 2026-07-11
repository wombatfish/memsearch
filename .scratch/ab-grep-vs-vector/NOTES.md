# Grep recall vs vector pipeline A/B

Date: 2026-07-11
Status: Step 1 complete; vector pipeline remains the per-developer default.

This file is the audit trail required by Part 4, Step 1 of
`C:\Users\dave\.claude\plans\examine-https-github-com-googlecloudplat-transient-nova.md`.
The scored query set and its gold targets were selected before either arm ran.

## Environment

- `MEMSEARCH_DIR`: `C:\Users\dave\.claude\memsearch-global`
- Canonical data root: `C:/Users/dave/.claude/memsearch-global`
- Collection: `ms_memsearch_global_25623e13`
- CLI: `C:\Users\dave\.local\bin\memsearch.exe`, version 0.4.6
- Daemon at measurement time: live, PID 35464, ONNX provider

## Item 0: locally measured evidence

### Warm vector search

Representative real query: `uv stale cache watcher restart requirement`.
The same top result (`79d6541f5b49...`) was returned on all three runs.

| Run | Wall time | Exit | Results |
|---:|---:|---:|---:|
| 1 | 2156.8 ms | 0 | 5 |
| 2 | 2218.6 ms | 0 | 5 |
| 3 | 2207.0 ms | 0 | 5 |

Median: **2207.0 ms**.

Command shape:

```powershell
memsearch search 'uv stale cache watcher restart requirement' --collection ms_memsearch_global_25623e13 --json-output
```

This supersedes the plan narrative's unpersisted 10.8-second observation for this
machine and current daemon state. It is a point-in-time measurement, not a general
benchmark.

### Expansion

Expanded the stable search result `79d6541f5b492497`.

| Run | Wall time | Exit | JSON payload |
|---:|---:|---:|---:|
| 1 | 744.8 ms | 0 | 2452 characters |
| 2 | 742.2 ms | 0 | 2452 characters |

Command shape:

```powershell
memsearch expand 79d6541f5b492497 --collection ms_memsearch_global_25623e13 --json-output
```

### Full-corpus heading grep

Scanner-visible command shape:

```powershell
rg -n -g '*.md' -g '*.markdown' '^## ' $env:MEMSEARCH_DIR
```

Five runs: `35.5, 24.7, 22.7, 21.9, 22.2 ms`; 2,089 matches each.
Median: **22.7 ms**; observed maximum: **35.5 ms**.

Including hidden directories with `--hidden`: `31.9, 27.2, 26.4, 26.9,
29.5 ms`; 2,118 matches each. Median: **27.2 ms**.

Timings include process startup and PowerShell capture, but not display rendering.

### Corpus inventory

| Scope | Files | Bytes | MiB |
|---|---:|---:|---:|
| All markdown below `MEMSEARCH_DIR`, including hidden | 282 | 7,629,390 | 7.276 |
| Scanner-visible markdown below `MEMSEARCH_DIR` | 268 | 7,376,344 | 7.035 |
| Markdown below `MEMSEARCH_DIR\memory`, including hidden | 279 | 7,620,836 | 7.268 |

### Graph inventory

Read-only SQLite transaction against `C:\Users\dave\.memsearch\edges.db`:

| Relation | Count |
|---|---:|
| `same_section` | 1,649 |
| `sibling` | 7,643 |
| `similar` (dense-derived) | 31,708 |
| **Total** | **41,000** |

All rows named model `gpahal/bge-m3-onnx-int8`.

Caveats:

- `edges.db` is global and has no collection column, so these rows cannot be
  attributed exclusively to `ms_memsearch_global_25623e13`.
- The watcher was rebuilding the graph. Counts from separate autocommit reads
  changed, so they were discarded; the table is one internally consistent
  transaction snapshot.

### External claims

#### LlamaIndex correctness 8.4 vs 6.4

**Unverified.** Searches found the figures only in a secondary article, not an
identifiable LlamaIndex primary benchmark. This claim must not be presented as a
verified LlamaIndex result unless the original artifact is found.

Secondary lead (not accepted as verification):
https://sesamedisk.com/direct-corpus-interaction-ai-retrieval/

#### Conversational-memory grep vs vector comparison

**Verified against the primary paper abstract.** Sahil Sen et al.,
"Is Grep All You Need? How Agent Harnesses Reshape Agentic Search," arXiv
2605.15184: https://arxiv.org/abs/2605.15184

Fetched passage: "Across Chronos and the provider CLIs, grep generally yields
higher accuracy than vector retrieval."

The experiment used a 116-question LongMemEval sample and several agent
harnesses. The result is specific to that setup; it is not evidence that grep
universally dominates vector retrieval.

#### Onyx file-search scaling

**Verified against Onyx's published experiment.** Joachim Rahmfeld,
"Are File Systems All You Need? It Depends...":
https://onyx.app/blog/file-search-vs-hybrid-search

Fetched passage: "Hybrid search held steady, while file search recall dropped
substantially (24 percentage points averaged over all question categories)."

The comparison used a synthetic enterprise dataset at approximately 160,000
and 510,000 documents. Onyx explicitly calls the results directional and notes
limited tuning and 51 questions.

## Item 1: pre-registered scored query set

The 15 queries below were harvested from real prior recall invocations, not
generated from target chunks. Gold targets were fixed before either arm ran.
Paths are relative to `MEMSEARCH_DIR\memory` unless shown otherwise.

### Identifier-heavy

| ID | Query | Pre-registered gold |
|---|---|---|
| I1 | `parse-transcript.sh UnicodeDecodeError cp1252 silently killed the Stop hook` | `memsearch/qmaster-custom/lessons-windows-failures.md`, heading `B1. cp1252 UnicodeDecodeError silently killed the Stop hook`, chunk `d2803cc6e790b3df`; must surface the Windows cp1252 default and explicit UTF-8 fix. |
| I2 | `scoped index single file prunes other sources; deleted-file GC core.py` | `memsearch/qmaster-custom/lessons-windows-failures.md`, heading `G. Historical footgun: a scoped index used to prune the rest of the collection (fixed)`, current chunk `76b26e7b92e2754e`; must distinguish the destructive pre-fix behavior from the current scope-safe GC implementation. |
| I3 | `claude -p stdin vs argv Windows 32K CreateProcess limit PR #563` | `memsearch/qmaster-custom/2026-06-03.md`, heading `15:01`, chunk `ed167eb3e44e442a`; must surface argv failure beyond the CreateProcess limit and stdin transport. |
| I4 | `GEMINI_CLI_TRUST_WORKSPACE failure in the Syndic Gemini worker` | `qmaster/qm-55-remitwashingmachine/2026-07-05.md`, heading `17:11`, chunk `9cdfa1a8136ad484`; must surface workspace-trust validation and the required environment setting. |
| I5 | `PR 525 multi-scope blended search, PR 541 extra watch collection, PR 543 Milvus flush` | `memsearch/qmaster-custom/2026-06-03.md`, heading `13:47`, chunk `0d83fd5def9bda26`; must surface the distinct assessment of all three PRs. |

### Paraphrase / natural-language

| ID | Query | Pre-registered gold |
|---|---|---|
| P1 | `Show me the checks I need to do to find the white-screen cause.` | `qmaster/main/2026-06-12.md`, heading `10:20`, chunk `e5424c7ad0ac6d61`; must surface per-user RDP variance plus logs, reset per-user settings/layout, and graphics toggle. |
| P2 | `Find other approaches to agent memory retrieval that could improve this codebase.` | `qmaster/main/2026-05-27.md`, heading `21:54`, chunk `c0daa8bc528d3640`; must surface the three-layer SessionStart grep push, prompt hint, and semantic pull architecture. |
| P3 | `Use Memsearch to track the lessons from why the original version failed on Windows.` | `memsearch/qmaster-custom/lessons-windows-failures.md`, especially `Overarching lessons`; must identify this curated file and at least the bare-bash/WSL path trap and explicit-encoding rule. |
| P4 | `How do I configure LINQPad to work with Claude skills?` | `__no_repo__/2026-07-10.md`, heading `14:31`, chunk `dce72355158b488f`; must surface LINQPad's separate skill root and copy-vs-junction drift risk. |
| P5 | `What software and files are needed to install this plugin on another Windows workstation?` | `memsearch/qmaster-custom/2026-05-28.md`, heading `10:37`, chunk `1bc3b38f20efd932`; must surface the Windows/WSL-Podman Milvus stack and local endpoint; generic plugin installation alone is partial. |

### Temporal / cross-project

Time-relative gold is frozen to the original invocation time, not today's date.

| ID | Query | Pre-registered gold |
|---|---|---|
| T1 | `What did we do in the last session?` (invoked 2026-06-24 15:04Z) | `memsearch/qmaster-custom/2026-06-24.md`, heading `16:34`, chunk `f2e8f051a794791d`; must surface fuzzy-clover config trust-boundary and batching commits/verification. |
| T2 | `Show me the last recorded memory.` (invoked 2026-07-01 12:28Z) | `qmaster/qm-55-remitwashingmachine/2026-07-01.md`, heading `14:24`, chunk `c13f81117660fca7`; must surface the then-latest `tasks.md` progress update. |
| T3 | `In the most recent sessions, which plan had two things left to update?` | `robsavnew/main/2026-06-01.md`, heading `17:08`, chunk `12d6f97f883e6d96`; must surface empty session headings and the async Stop data-loss window. |
| T4 | `What were the recent changes to the memsearch plugin hooks and skills?` | `memsearch/qmaster-custom/2026-06-08.md`, heading `15:57`, chunk `61e7d93b5ee86f01`; must surface the unchanged-version cache/reinstall gotcha and deployed-cache verification. |
| T5 | `How do I deploy the local fork without getting caught by stale CLI, plugin-cache, or watcher state?` | `memsearch/qmaster-custom/2026-06-23.md`, heading `13:11`, chunk `8ff26450222b6455`; must surface forced no-cache rebuild, deployed cached-skill content verification, and deployed-binary E2E. |

### Query provenance

The harvested invocations came from these transcript/sub-agent trees:

- I1: `D--Projects-memsearch/0f997e30-447a-4377-a38a-edb0b4b47d24`
- I2: `D--Projects-memsearch/437ffeaa-fb6c-439e-8d3a-1b98ce63c23f`
- I3: `D--Projects-memsearch/053cfd15-c4c3-4302-a9a1-57173f7fc05b`
- I4: `D--Projects-syndic/4266f502-0369-4402-a498-cefbcbd63336`
- I5: `D--Projects-memsearch/a6199c5e-c86d-4736-99e8-af9577bc25e6`
- P1: `d--Projects-qmaster/a4e6f5eb-18c9-4ce7-bca0-9654a1146251`
- P2: `D--Projects-memsearch/f3b14e12-7566-42cc-80f5-df556a58e70f`
- P3: `C--Users-dave/fe4c6215-1aa8-447a-8574-ef8a84c2c602`
- P4: `C--Users-dave--agent-config/63b597cf-17ee-4b89-9a73-997564aea1d4`
- P5: `D--Projects-memsearch/f50622c9-1782-4d68-820e-b5c6ef0c2a1c`
- T1: `D--Projects-memsearch/98e85f90-4235-4a2c-bca8-9d8315bb6e28`
- T2: `d--Projects-qmaster/bb13a44a-5de2-42e0-a761-74ac7fff6684`
- T3: `D--Projects-RobsavNew/2e01b3a4-aa48-4c47-8aa6-cf935209144c`
- T4: `D--Projects-memsearch/e3b033cb-e68d-45a2-9dd7-69e2a699d6ba`
- T5: `D--Projects-memsearch/418bcca8-23fb-4d45-af14-3e6359a49c40`

## Item 2: held-out grep-prompt tuning pool

These queries are excluded from scoring.

| ID | Query | Gold |
|---|---|---|
| U1 | `Why can OpenCode see PowerShell's memsearch.exe but fail when it runs recall through bash?` | `memsearch/qmaster-custom/2026-05-28.md`, heading `12:41:06`, chunk `17de38118174178e`: bare bash resolves WSL with a separate PATH; spawn the executable directly. |
| U2 | `Which config.toml does memsearch load, and what does MEMSEARCH_DIR actually control?` | `qmaster/main/2026-05-27.md`, heading `13:19`, chunk `e504c88525060c58`: config-resolution path versus data-root behavior. |
| U3 | `How were the BugTracker MCP and file-conversion guidance fanned out to the other agent CLIs?` | `__no_repo__/2026-06-26.md`, heading `15:34`, chunk `83d7e379e2244f16`: no edit needed, dry-run proved existing scope, then fan-out verification. |
| U4 | `Why did memsearch stats undercount immediately after a large re-index, and what fixed the count?` | `__no_repo__/2026-05-28.md`, heading `12:40`, chunk `23f4fdb878e50cc2`: growing versus sealed segments and explicit flush correcting row count. |

### Tuning result

All four held-out gold targets were surfaced using raw Markdown only. No
`memsearch search`, `memsearch expand`, vector, or database call was used.

| Query | Result | Observed `rg` time | Prompt lesson |
|---|---|---:|---|
| U1 | Gold surfaced in the curated Windows-failures note | 16-24 ms per variant | Tokenize identifiers and prefer exact rare terms before broad alternatives. |
| U2 | Gold surfaced only after global fallback from inferred memsearch scope | 16-27 ms per variant | Project/date are ranking hints, never early hard exclusions. |
| U3 | Gold surfaced in `__no_repo__/2026-06-26.md` | 25-30 ms per variant | Separate configuration/tool fan-out from prose-guidance fan-out. |
| U4 | Gold surfaced in `__no_repo__/2026-05-28.md` | 23-24 ms per variant | Require mechanism and outcome terms to co-occur in a section. |

The tuning review also found that `rg` without `--hidden` omits 14 Markdown
files under a hidden project directory, timestamp/session headings are useful
mainly as section boundaries, inferred project paths can be wrong, and temporal
answers need to detect later superseding decisions.

No scored query was used to alter the prompt.

### Frozen grep-only prompt

Frozen on 2026-07-11 after U1-U4 tuning and before scored run 1.

```text
You retrieve prior memories only from Markdown under
${MEMSEARCH_DIR}/memory. Do not call memsearch search, expand, or any
vector/semantic search tool. Markdown is the source of truth.

1. Resolve the request
Rewrite pronouns and shorthand into a self-contained retrieval question.
Extract exact identifiers; concepts, actions, and outcomes; project and branch
hypotheses; and temporal language. Treat inferred project, branch, and date as
ranking hints, never as hard exclusions unless the user explicitly limits scope.

2. Build at most three lexical variant families
- Identifier: search exact identifiers literally, then split CamelCase,
  snake_case, kebab-case, paths, and concatenated issue names into meaningful
  tokens. Preserve issue numbers and distinctive suffixes.
- Concept: choose 2-6 independent nouns, verbs, outcome terms, and plausible
  terminology synonyms. Do not rely on one paraphrased sentence or require every
  term on the same line.
- Temporal/scope: convert relative dates to absolute dates using the supplied
  current date/time zone. Include project/branch aliases and core topic terms.
  Search both the inferred range and all dates so earlier and superseding
  decisions remain discoverable.

Do not invent identifiers or domain synonyms unsupported by the conversation.

3. Search safely and globally
Use `rg --hidden`, restricted to `*.md`. Search content globally before
narrowing by project/date. For exact strings use fixed-string search first,
case-sensitive and then case-insensitive if needed; never interpret identifier
punctuation as regex. For concepts, search escaped alternatives or independent
terms. Prefer candidates where several independent terms co-occur in one file
or Markdown section. A phrase miss is not evidence the concept is absent.

Search root files, `__no_repo__`, hidden project directories, and every
project/branch directory. Then rank inferred project/date matches higher while
retaining strong cross-project matches. Before reporting no result, perform one
mandatory global fallback with tokenized identifiers and independently searched
concept synonyms.

4. Select and inspect candidates
Rank: distinctive exact identifier; several concept terms in one section;
project/branch/date agreement; then recency. Do not rank a weak recent hit above
a strong exact older hit. Keep materially contradictory or superseding claims.

For a promising hit, find the nearest preceding Markdown heading and next
heading of equal or higher level, then read the complete containing section.
Do not return a line window crossing a section boundary. Timestamp headings are
labels, not semantic evidence. For temporal questions inspect earlier and later
matching sections; state changes explicitly and prefer the latest implemented
or verified state over an older proposal or warning.

5. Deep drill only when necessary
Follow only the transcript, rollout, or database anchor inside the selected
section, and only when the summary is ambiguous, lacks required detail, or exact
attribution is needed. Verify the source exists. Use session/turn when present;
if turn is empty, use a tightly bounded session lookup. Treat the anchor path as
authoritative and do not search every raw transcript globally.

6. Return evidence
For each result give the concise recalled fact or decision, source path/date,
containing heading, line number(s), short supporting passage, and whether it is
current, historical, superseded, or uncertain. Order by relevance. If the
mandatory broad fallback also finds nothing, return exactly:
No relevant memories found.
```

## Frozen protocol clarification

Approved and frozen by the user on 2026-07-11 before scored run 1. The source
plan is internally inconsistent: its prose says decisive quality wins are not
overridden by latency, while its outcome table sends a grep quality win that
fails latency to a preference tie. It also leaves "within noise" and the
expanded-set allocation undefined. The following clarification resolves those
gaps without inspecting scored results.

1. Judge: two fresh sub-agents with no forked context, given only opaque,
   randomized normalized attempts, gold targets, and the rubric. Exact agreement
   is accepted; disagreements go to a fresh adjudicator. This is best-effort
   procedural blinding because agents share a filesystem.
2. Rubric: 2 = target surfaced and usable; 1 = partial or adjacent; 0 = missed.
3. Normalized artifact: `(source file, quoted markdown)` pairs only. Strip hashes,
   scores, traces, timings, token counts, and arm labels before judging.
4. Latency thresholds: compare unrounded values; grep/pipeline ratio must be
   `<= 1.00` independently for median and nearest-rank p95.
5. Outcome precedence:
   - Decisive grep quality: grep leads by at least 2 total points and strictly
     leads at least two strata; grep default regardless of latency.
   - Decisive pipeline quality: pipeline leads by at least 2 total points and
     strictly leads at least two strata; vector default regardless of latency.
   - Quality-equivalent: absolute total gap at most 1 and grep meets both latency
     thresholds; grep default on measured equivalence and speed.
   - Everything else: preference tie, grep default, mandatory expanded rerun.
6. A direction-consistent 2-point pilot gap is the defined "decisive-looking
   margin still within noise" and also requires expanded confirmation.
7. Expanded run: exactly 30 queries, 10 per stratum, comprising the original 15
   plus five new pre-registered-gold queries per stratum. Re-run all 30 attempts
   per arm fresh; do not combine old pilot timings with new results.

### Frozen execution harness and metric definitions

Frozen on 2026-07-11 before scored run 1:

- Both arms run through Claude Code 2.1.207 using Claude Sonnet 5, medium effort,
  the default system prompt, the same working directory, and no persisted
  session. The local `plugins/claude-code` directory supplies the current arm's
  skill implementation.
- Current arm: explicitly invoke the `memory-recall` skill; allow only the Skill,
  Bash, Read, ToolSearch, and bundled memory MCP tools required by that skill.
- Grep arm: provide the frozen grep-only prompt above and the query; allow only
  read-only shell/file tools. The memory skill and bundled MCP tools are not
  available to this arm.
- Each attempt is independent. No conversation or prompt cache state is
  intentionally carried from an earlier attempt.
- Wall-clock is Claude Code JSON `duration_ms`, measured end-to-end. Median uses
  the ordinary midpoint definition; p95 uses nearest rank, rank
  `ceiling(0.95 * n)` on ascending unrounded durations.
- "Fork tokens" means the sum, across `modelUsage`, of `inputTokens`,
  `outputTokens`, `cacheReadInputTokens`, and `cacheCreationInputTokens`.
  Component totals and cost are also recorded so cache behavior is visible.
- Per-attempt budget ceiling: USD 1.00. A budget/permission/tool failure is an
  arm failure and is recorded, not silently rerun with different permissions.

Held-out harness validation U3 completed successfully through the current arm
before scored run 1: 75.441 seconds, USD 0.3924723, no permission denials, and
the expected fan-out evidence was surfaced. This validates JSON timing/token
capture only; U3 remains excluded from scoring.

## Scored pilot results

All 30 selected attempts completed successfully with terminal status
`completed`, no permission denials, and no budget failure. Runs were sequential
within each stratum; the three strata ran in parallel, so at most three attempts
overlapped.

The CLI's `--allowedTools` option controlled permission rather than physically
removing every other built-in tool. The current skill's fork frontmatter did
physically restrict its tool set. The grep prompt prohibited Skill/MCP/vector
use and its returned artifacts showed only raw-Markdown evidence—no vector
hashes, scores, Skill calls, or MCP results. This is recorded as a harness
limitation rather than silently overstating isolation.

### Per-query measurements and adjudicated quality

| Query | Current score | Current ms | Current fork tokens | Grep score | Grep ms | Grep fork tokens |
|---|---:|---:|---:|---:|---:|---:|
| I1 | 2 | 23,326 | 91,808 | 2 | 33,765 | 70,449 |
| I2 | 2 | 55,598 | 223,320 | 2 | 66,530 | 84,158 |
| I3 | 2 | 60,116 | 221,739 | 2 | 45,861 | 158,090 |
| I4 | 2 | 60,604 | 222,715 | 2 | 38,062 | 94,481 |
| I5 | 2 | 60,779 | 223,497 | 2 | 55,985 | 122,303 |
| P1 | 1 | 90,283 | 295,079 | 1 | 65,509 | 523,210 |
| P2 | 1 | 80,287 | 360,308 | 1 | 48,258 | 300,676 |
| P3 | 2 | 80,755 | 360,919 | 1 | 43,259 | 233,654 |
| P4 | 2 | 76,122 | 414,496 | 2 | 93,881 | 791,151 |
| P5 | 1 | 75,797 | 434,662 | 1 | 31,395 | 219,917 |
| T1 | 1 | 121,088 | 377,443 | 1 | 48,050 | 373,112 |
| T2 | 2 | 180,456 | 305,917 | 0 | 46,773 | 308,367 |
| T3 | 0 | 71,247 | 358,606 | 0 | 177,015 | 2,124,427 |
| T4 | 1 | 73,221 | 281,309 | 1 | 94,817 | 452,726 |
| T5 | 1 | 82,798 | 227,170 | 1 | 31,644 | 182,252 |

### Quality totals

| Stratum | Current | Grep | Direction |
|---|---:|---:|---|
| Identifier-heavy | 10/10 | 10/10 | tie |
| Paraphrase-only | 7/10 | 6/10 | current pipeline |
| Temporal/cross-project | 5/10 | 3/10 | current pipeline |
| **Total** | **22/30** | **19/30** | **current pipeline +3** |

### Overall performance and cost

| Metric | Current | Grep |
|---|---:|---:|
| Median wall-clock | 75,797 ms | 48,050 ms |
| Nearest-rank p95 | 180,456 ms | 177,015 ms |
| Input tokens | 174 | 7,293 |
| Output tokens | 38,796 | 39,354 |
| Cache-read tokens | 3,312,551 | 5,478,722 |
| Cache-creation tokens | 1,047,467 | 513,604 |
| **Fork-token total** | **4,398,988** | **6,038,973** |
| Total cost | USD 5.50422855 | USD 4.16679260 |

Grep was materially faster at the median and slightly faster at overall p95,
but used 37.3% more fork tokens because its worst temporal search was extremely
expensive. Cost was lower because its cache-creation mix differed.

### Blind judging

- Judge 1: fresh sub-agent `/root/ab_blind_judge_1`, no forked context.
- Judge 2: fresh sub-agent `/root/ab_blind_judge_2`, no forked context.
- Adjudicator: fresh sub-agent `/root/ab_blind_adjudicator`, no forked context.
- Packet seed: `20260711`; attempts were opaque and randomized.
- Judges received only the query, frozen gold, and normalized
  `(source file, supporting passage)` evidence. They did not receive arm labels,
  timings, tokens, costs, hashes, scores, or tool traces.
- The judges agreed on 29/30 attempts (96.7%). They disagreed only on P1 current
  (2 versus 1); the adjudicator assigned 1 because the evidence suggested layout
  corruption but did not explicitly instruct resetting/clearing the layout.
- Blinding was best-effort, not guaranteed: the agents share a filesystem, but
  they were explicitly prohibited from reading the mapping or experiment notes.

### Invalid operator trials excluded before scoring

- One P1 grep transport trial lost Markdown backticks and was invalidated without
  content inspection.
- Ten concurrently launched paraphrase attempts were invalidated because the
  concurrency would contaminate latency; six still-running processes were
  terminated by exact command signature/PID and none of their outputs was used.
- Two initial I1 current launches lost their PTY capture handles; their outputs
  were never retrieved.
- Redundant I1 replicates were excluded by earliest-launch order before content
  review. A redundant I2 current process was terminated before completion.

These are operator/harness failures, not arm failures, and no invalid result
contributed to quality, latency, token, or cost totals.

## Decision

The current pipeline wins quality by 3 points and is strictly higher in two of
three strata. Under the frozen quality-first precedence, this is a decisive
vector-default verdict; grep's latency advantage does not override it.

Therefore:

- Keep the vector pipeline as the per-developer default.
- Keep grep as the zero-infrastructure fallback.
- Skip conditional Step 2a; do not split vector dependencies into an optional
  default-off extra during standalone extraction.
- Do not run the expanded n=30 query set. The trigger applies only to a tie or a
  direction-consistent 2-point pilot margin; this margin is 3 points.
