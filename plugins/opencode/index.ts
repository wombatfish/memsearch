/**
 * memsearch OpenCode plugin — semantic memory search across sessions.
 *
 * Registers:
 * - memsearch_search tool: semantic search over past memories
 * - memsearch_get tool: expand a chunk to full context
 * - memsearch_transcript tool: parse original conversation from OpenCode SQLite
 * - experimental.chat.system.transform hook: inject recent memories as context
 *
 * Auto-capture is handled by a background Python daemon (capture-daemon.py)
 * that polls the OpenCode SQLite database for completed turns.
 */

import type { Plugin } from "@opencode-ai/plugin";
import { tool } from "@opencode-ai/plugin";
import { exec, spawn, spawnSync } from "node:child_process";
import {
  readFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  realpathSync,
} from "node:fs";
import { join, dirname, basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const PLUGIN_DIR = dirname(realpathSync(fileURLToPath(import.meta.url)));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Detect the memsearch CLI command.
 * Checks: PATH -> ~/.local/bin/uvx -> uvx in PATH.
 */
interface MemsearchCmd {
  argv0: string;          // executable name or absolute path
  prefixArgs: string[];   // args required before the subcommand (e.g. uvx --from ...)
}

/**
 * Detect how to invoke memsearch. Critical Windows note:
 *
 *   spawnSync("bash", ...) on Windows resolves to C:\Windows\System32\bash.exe,
 *   which is the WSL launcher. WSL has its OWN Linux PATH that does NOT include
 *   C:\Users\<u>\.local\bin\ where uv installs memsearch.exe — even though that
 *   path is on the Windows PATH the Node host inherits. So any tool call that
 *   shells through `bash -c` will fail with "memsearch: command not found"
 *   regardless of how well-resolved the Windows PATH is.
 *
 * Fix: detect via `where` (Windows) / `command -v` (POSIX), and execute the
 * binary directly with array args. No shell, no bash, no WSL detour.
 */
function detectMemsearchCmd(): MemsearchCmd {
  const isWin = process.platform === "win32";

  const probe = (name: string): boolean => {
    try {
      const r = isWin
        ? spawnSync("where", [name], { stdio: ["ignore", "pipe", "ignore"], encoding: "utf-8" })
        : spawnSync("sh", ["-c", `command -v ${name}`], { stdio: ["ignore", "pipe", "ignore"], encoding: "utf-8" });
      return r.status === 0 && !!(r.stdout || "").trim();
    } catch {
      return false;
    }
  };

  if (probe("memsearch")) return { argv0: "memsearch", prefixArgs: [] };
  if (probe("uvx"))       return { argv0: "uvx", prefixArgs: ["--from", "memsearch[onnx]", "memsearch"] };
  return { argv0: "memsearch", prefixArgs: [] };
}

/** Serialize a MemsearchCmd as a shell-safe string for fire-and-forget callers
 *  (capture daemon, fallback index call) that need a single command line. */
function serializeMemsearchCmd(cmd: MemsearchCmd): string {
  const quote = (s: string) => /[\s'"]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s;
  return [cmd.argv0, ...cmd.prefixArgs].map(quote).join(" ");
}

/**
 * Derive a per-project Milvus collection name.
 * Mirrors scripts/derive-collection.sh — kept in-process so the plugin works
 * on Windows where invoking WSL bash with a Windows path (e.g. C:\Users\…)
 * fails with "No such file or directory" because backslashes are eaten as
 * shell escapes and WSL needs /mnt/c/… form anyway.
 */
function deriveCollectionName(projectDir: string): string {
  try {
    // Normalize to forward-slash form to match plugins/claude-code/scripts/
    // derive-collection.sh, which uses git-bash's `realpath -m` and outputs
    // forward slashes on Windows. Node's path.resolve() emits backslashes
    // on Windows — hashing those would produce a different collection name
    // than Claude Code, and OpenCode would search an empty collection.
    const absPath = resolve(projectDir).replace(/\\/g, "/");
    const sanitized = basename(absPath)
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "_")
      .replace(/_+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 40);
    const hash = createHash("sha256").update(absPath).digest("hex").slice(0, 8);
    return `ms_${sanitized || "opencode_default"}_${hash}`;
  } catch {
    return "ms_opencode_default";
  }
}

/**
 * Slugify a git branch name for use as a memory subdirectory.
 * Mirrors the sed pipeline in plugins/claude-code/hooks/common.sh.
 */
function slugifyBranch(branch: string): string {
  return branch
    .toLowerCase()
    .replace(/[^a-z0-9._-]/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
}

interface MemorySetup {
  memsearchDir: string;       // root memsearch dir (global or project-local)
  memoryDir: string;          // memsearchDir/memory — root of all buckets
  memoryBucketDir: string;    // memoryDir/<repo>/<branch> — where daily files write
  collectionName: string;
  isGlobalScope: boolean;
}

/**
 * Resolve memory paths and Milvus collection for the given project.
 *
 * Honors the MEMSEARCH_DIR env var for global-scope sharing across plugins
 * (the Claude Code plugin sets this convention in hooks/common.sh). When
 * MEMSEARCH_DIR is set, all projects write daily files into per-repo,
 * per-branch buckets under one shared dir and share a single collection.
 *
 * Without MEMSEARCH_DIR, falls back to per-project isolation under
 * <project>/.memsearch with a project-specific collection.
 */
function resolveMemorySetup(projectDir: string): MemorySetup {
  const isGlobalScope = !!process.env.MEMSEARCH_DIR;
  const memsearchDir = process.env.MEMSEARCH_DIR ?? join(projectDir, ".memsearch");
  const memoryDir = join(memsearchDir, "memory");

  let repoBucket = "__no_repo__";
  let branchSeg = "";

  try {
    const r = spawnSync("git", ["rev-parse", "--git-common-dir"], {
      cwd: projectDir,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    if (r.status === 0) {
      const commonDir = resolve(projectDir, (r.stdout || "").trim());
      if (existsSync(commonDir)) {
        const candidate = basename(dirname(commonDir)).toLowerCase();
        if (candidate && candidate !== "/") repoBucket = candidate;
      }
    }
  } catch { /* not a git repo */ }

  if (repoBucket !== "__no_repo__") {
    try {
      const r = spawnSync("git", ["symbolic-ref", "--short", "-q", "HEAD"], {
        cwd: projectDir,
        encoding: "utf-8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      const branch = (r.stdout || "").trim();
      branchSeg = branch ? (slugifyBranch(branch) || "_branch") : "_detached";
    } catch {
      branchSeg = "_detached";
    }
  }

  const memoryBucketDir = branchSeg
    ? join(memoryDir, repoBucket, branchSeg)
    : join(memoryDir, repoBucket);

  // Collection: global scope → derived from shared dir so all projects share it.
  // Project scope → derived from project path for isolation.
  const collectionName = deriveCollectionName(isGlobalScope ? memsearchDir : projectDir);

  return { memsearchDir, memoryDir, memoryBucketDir, collectionName, isGlobalScope };
}

/**
 * Summarize the N most recent daily .md files for cold-start context.
 * Extracts headings (## Session, ### turns) and bullet content from each
 * file so the agent sees the structure of past days (what sessions existed,
 * what topics came up), not just the tail of whichever file is newest.
 */
function getRecentMemories(
  memDir: string,
  count = 2,
  maxLinesPerFile = 30
): string {
  if (!existsSync(memDir)) return "";

  const files = readdirSync(memDir)
    .filter((f) => f.endsWith(".md"))
    .sort()
    .slice(-count);

  if (files.length === 0) return "";

  const summary: string[] = [];
  for (const file of files) {
    try {
      const content = readFileSync(join(memDir, file), "utf-8");
      const lines = content.split("\n")
        .filter((l) => /^#{2,4}\s/.test(l) || l.startsWith("- ") || l.startsWith("[Human]") || l.startsWith("[Assistant]"))
        .slice(0, maxLinesPerFile);
      if (lines.length > 0) {
        summary.push(`[${file}]`, ...lines);
      }
    } catch { /* skip */ }
  }

  if (summary.length === 0) {
    return `You have ${files.length} past memory file(s). Use the memsearch_search tool when the user's question could benefit from historical context.`;
  }

  return `Recent memories (use memsearch_search for full search):\n${summary.join("\n")}`;
}

/** Shell-escape a string for safe use inside single quotes. */
function shellEscape(s: string): string {
  return s.replace(/'/g, "'\\''");
}

/**
 * Start the capture daemon as a background process.
 * The daemon polls OpenCode's SQLite for completed turns and writes to daily .md files.
 */
function startCaptureDaemon(
  projectDir: string,
  setup: MemorySetup,
  memsearchCmd: MemsearchCmd
): void {
  // PID file stays project-local: one daemon per project session even when
  // multiple projects share a global memsearchDir.
  const pidFile = join(projectDir, ".memsearch", ".capture.pid");
  const daemonScript = join(PLUGIN_DIR, "scripts", "capture-daemon.py");

  if (existsSync(pidFile)) {
    try {
      const pid = parseInt(readFileSync(pidFile, "utf-8").trim(), 10);
      if (pid > 0) {
        try {
          process.kill(pid, 0);
          return; // already running
        } catch { /* dead pid, fall through to restart */ }
      }
    } catch { /* ignore */ }
  }

  const cmdStr = serializeMemsearchCmd(memsearchCmd);
  exec(
    `python3 "${daemonScript}" "${projectDir}" "${setup.collectionName}" ` +
      `--memsearch-cmd "${shellEscape(cmdStr)}" ` +
      `--memsearch-dir "${shellEscape(setup.memsearchDir)}" ` +
      `--memory-dir "${shellEscape(setup.memoryBucketDir)}" ` +
      `--memory-root "${shellEscape(setup.memoryDir)}" ` +
      `--poll-interval 10 &`,
    {
      timeout: 5000,
      env: { ...process.env, MEMSEARCH_NO_WATCH: "1" },
      windowsHide: true,
    },
    () => { /* ignore */ }
  );
}

/**
 * Stop the capture daemon.
 */
function stopCaptureDaemon(projectDir: string): void {
  const pidFile = join(projectDir, ".memsearch", ".capture.pid");
  if (existsSync(pidFile)) {
    try {
      const pid = parseInt(readFileSync(pidFile, "utf-8").trim(), 10);
      if (pid > 0) {
        try { process.kill(pid, "SIGTERM"); } catch { /* ignore */ }
      }
    } catch { /* ignore */ }
  }
}

function wakeMaintenance(projectDir: string, memsearchDir: string): void {
  const runner = join(PLUGIN_DIR, "scripts", "maintenance-runner.py");
  exec(
    `python3 '${shellEscape(runner)}' --platform opencode ` +
      `--project-dir '${shellEscape(projectDir)}' --memsearch-dir '${shellEscape(memsearchDir)}' &`,
    {
      timeout: 5000,
      env: { ...process.env, MEMSEARCH_NO_WATCH: "1" },
      windowsHide: true,
    },
    () => { /* ignore */ }
  );
}

// ---------------------------------------------------------------------------
// Plugin entry
// ---------------------------------------------------------------------------

const MemsearchPlugin: Plugin = async ({ project, directory, worktree }) => {
  // worktree can be "/" for global projects — use directory instead
  const projectDir = (worktree && worktree !== "/") ? worktree : (directory || process.cwd());
  const memsearchCmd = detectMemsearchCmd();
  const setup = resolveMemorySetup(projectDir);
  const { memsearchDir, memoryDir, memoryBucketDir, collectionName } = setup;
  const home = process.env.HOME || "~";

  // Skip capture/recall in child processes to prevent recursion
  const isChildProcess = !!process.env.MEMSEARCH_NO_WATCH;
  const autoCapture = !isChildProcess;
  const autoRecall = !isChildProcess;

  // Ensure default config (onnx provider) at startup
  try {
    const configFile = join(home, ".memsearch", "config.toml");
    const localConfig = join(projectDir, ".memsearch.toml");
    if (!existsSync(configFile) && !existsSync(localConfig)) {
      try {
        spawnSync(
          memsearchCmd.argv0,
          [...memsearchCmd.prefixArgs, "config", "set", "embedding.provider", "onnx"],
          { timeout: 5000, stdio: "ignore", windowsHide: true }
        );
      } catch { /* ignore */ }
    }
  } catch { /* ignore */ }

  // Run initial index in background — index against the memory ROOT so all
  // repo/branch buckets are searchable from any session.
  if (existsSync(memoryDir)) {
    const child = spawn(
      memsearchCmd.argv0,
      [...memsearchCmd.prefixArgs, "index", memoryDir, "--collection", collectionName],
      // windowsHide suppresses the new console window Windows creates for any
      // detached child .exe — without it OpenCode startup pops a terminal.
      { stdio: "ignore", detached: true, windowsHide: true }
    );
    child.unref();
  }

  // Start capture daemon for auto-capture
  if (autoCapture) {
    startCaptureDaemon(projectDir, setup, memsearchCmd);
    wakeMaintenance(projectDir, memsearchDir);
  }

  // Resolve setup for a tool call. When the OpenCode session's working
  // directory differs from the init-time projectDir (multi-project session),
  // recompute the per-project bucket so writes/searches land in the right place.
  const setupFor = (dir: string): MemorySetup =>
    dir === projectDir ? setup : resolveMemorySetup(dir);

  return {
    // ----- Tools -----
    //
    // Tool names are prefixed `memsearch_*` to avoid collisions with other
    // MCP servers that expose similarly named memory tools (e.g. Roslyn's
    // roslyn_memory_search). Models route on tool name + description, so an
    // unprefixed `memory_search` regularly loses to closer-matching siblings.
    tool: {
      memsearch_search: tool({
        description:
          "Memsearch: semantic search over CONVERSATION HISTORY and past " +
          "session notes stored as daily markdown files. Use when the user " +
          "says 'memsearch', 'recall', 'memory search', or asks 'what did " +
          "we decide about X', 'what was the bug yesterday', 'have I seen " +
          "this before', 'what did I do last session', or otherwise refers " +
          "to prior conversations / daily logs / historical decisions. " +
          "This is plugin-managed SESSION MEMORY — it is NOT a code-symbol " +
          "search, NOT a Roslyn analysis tool, and NOT a current-file " +
          "lookup; prefer this over any other 'memory_search' tool when " +
          "the user mentions memsearch by name. Returns ranked chunks " +
          "from Milvus (BM25 + dense + RRF) with chunk_hash anchors for " +
          "follow-up expansion via memsearch_get.",
        args: {
          query: tool.schema.string().describe("Search query — describe what you want to find"),
          top_k: tool.schema.number().optional().describe("Number of results to return (default: 5)"),
        },
        async execute(args, context) {
          const dir = context?.directory || projectDir;
          const s = setupFor(dir);
          if (autoCapture) startCaptureDaemon(dir, s, memsearchCmd);
          const topK = args.top_k || 5;
          try {
            const result = spawnSync(
              memsearchCmd.argv0,
              [
                ...memsearchCmd.prefixArgs,
                "search", args.query,
                "--top-k", String(topK),
                "--json-output",
                "--collection", s.collectionName,
              ],
              { encoding: "utf-8", timeout: 30000, windowsHide: true }
            );
            return result.stdout || result.stderr || "No results found.";
          } catch (e: any) {
            return `Search failed: ${e.message}`;
          }
        },
      }),

      memsearch_get: tool({
        description:
          "Memsearch: expand a session-memory chunk to its full markdown " +
          "section. Use ONLY after memsearch_search returns a chunk_hash " +
          "you want to read in full. Operates on memsearch's daily " +
          "conversation logs — not on Roslyn or code symbols.",
        args: {
          chunk_hash: tool.schema.string().describe("The chunk_hash from a search result to expand"),
        },
        async execute(args, context) {
          const dir = context?.directory || projectDir;
          const s = setupFor(dir);
          if (autoCapture) startCaptureDaemon(dir, s, memsearchCmd);
          try {
            const result = spawnSync(
              memsearchCmd.argv0,
              [
                ...memsearchCmd.prefixArgs,
                "expand", args.chunk_hash,
                "--collection", s.collectionName,
              ],
              { encoding: "utf-8", timeout: 15000, windowsHide: true }
            );
            return result.stdout || result.stderr || "No content found.";
          } catch (e: any) {
            return `Expand failed: ${e.message}`;
          }
        },
      }),

      memsearch_transcript: tool({
        description:
          "Memsearch: pull the original OpenCode conversation transcript " +
          "for a session/turn anchor surfaced by memsearch_get. Use ONLY " +
          "when an expanded memsearch chunk contains <!-- session:ID " +
          "turn:ID db:PATH --> and you need the raw dialogue around that " +
          "turn. Reads OpenCode's SQLite store directly. Not a generic " +
          "conversation tool — only works for memsearch-captured sessions.",
        args: {
          session_id: tool.schema.string().describe("The session ID from the anchor comment"),
          turn_id: tool.schema.string().optional().describe("Optional turn ID from the anchor comment"),
          context: tool.schema.number().optional().describe("Turns before/after the target turn (default: 3)"),
          limit: tool.schema.number().optional().describe("Max number of turns to return when no turn_id is provided (default: 20)"),
        },
        async execute(args, context) {
          const dir = context?.directory || projectDir;
          const s = setupFor(dir);
          if (autoCapture) startCaptureDaemon(dir, s, memsearchCmd);
          try {
            const scriptPath = join(PLUGIN_DIR, "scripts", "parse-transcript.py");
            const scriptArgs = [
              scriptPath,
              args.session_id,
              "--project-dir",
              dir,
            ];
            if (args.turn_id) {
              scriptArgs.push("--turn", args.turn_id);
            }
            if (typeof args.context === "number") {
              scriptArgs.push("--context", String(args.context));
            }
            if (typeof args.limit === "number") {
              scriptArgs.push("--limit", String(args.limit));
            }
            const result = spawnSync("python3", scriptArgs, {
              encoding: "utf-8",
              timeout: 15000,
              windowsHide: true,
            });
            return result.stdout?.trim() || result.stderr || "No transcript content found.";
          } catch (e: any) {
            return `Transcript parse failed: ${e.message}`;
          }
        },
      }),
    },

    // ----- Hook: system prompt transform — inject recent memories -----
    //
    // Cold-start summary is sourced from the current repo+branch bucket,
    // not the whole memory root, so multi-project global setups don't bleed
    // unrelated context into every session.
    ...(autoRecall
      ? {
          "experimental.chat.system.transform": async (_input: any, output: any) => {
            try {
              const context = getRecentMemories(memoryBucketDir);
              if (context) {
                output.system.push(
                  `[memsearch] Memory available. You have access to memsearch_search, memsearch_get, and memsearch_transcript tools for recalling past sessions.\n\n${context}`
                );
              }
            } catch { /* ignore */ }
          },
        }
      : {}),
  };
};

export default MemsearchPlugin;
