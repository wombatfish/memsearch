from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest

from memsearch.config import LLMProviderConfig, MemSearchConfig
from memsearch.maintenance import (
    MAX_PROMPT_CHARS,
    MAX_TOOL_CALLS,
    PER_FILE_BUDGET,
    TaskContext,
    _build_prompt,
    _file_lock,
    _input_digest,
    _read_recent_journals,
    _run_gemini_with_tools,
    _run_openai_with_tools,
    _scrub_secrets,
    _validate_paths_in_args,
    run_due_tasks,
    run_memory_command,
    run_task_llm,
)


def _make_ctx(project: Path, input_dir: Path) -> TaskContext:
    return TaskContext(
        platform="codex",
        task="project_review",
        task_config=MemSearchConfig().plugins.codex.project_review,
        project_dir=project,
        memsearch_dir=project / ".memsearch",
        input_dir=input_dir,
        output_file=project / ".memsearch" / "PROJECT.md",
        input_digest=_input_digest(input_dir),
    )


def test_maintenance_routes_gemini_provider_to_tool_runner(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("- User discussed Gemini maintenance.\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.llm.providers["gemini"] = LLMProviderConfig(type="gemini", model="gemini-test")
    cfg.plugins.codex.project_review.enabled = True
    cfg.plugins.codex.project_review.provider = "gemini"

    captured = {}

    def fake_gemini(ctx, prompt: str, model: str | None, provider_cfg) -> str:
        captured["model"] = model
        captured["provider_type"] = provider_cfg.type
        return json.dumps({"action": "none", "reason": "ok"})

    monkeypatch.setattr("memsearch.maintenance._run_gemini_with_tools", fake_gemini)

    results = run_due_tasks(platform="codex", project_dir=project, cfg=cfg)

    assert results[0].action == "none"
    assert captured == {"model": "gemini-test", "provider_type": "gemini"}


def test_maintenance_replace_writes_output_and_state(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("### 10:00\n- User discussed maintenance runner.\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True
    cfg.plugins.codex.project_review.provider = "openai"

    def fake_runner(ctx, prompt: str) -> str:
        assert "Recent memory journal entries" in prompt
        return json.dumps(
            {
                "action": "replace",
                "reason": "new project state",
                "content": "# Project Memory\n\n## Active Threads\n- Maintenance runner.",
            }
        )

    results = run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)

    assert [r.action for r in results] == ["replace", "disabled", "disabled"]
    assert (project / ".memsearch" / "PROJECT.md").read_text(encoding="utf-8").startswith("# Project Memory")
    state = json.loads((project / ".memsearch" / ".maintenance-state.json").read_text(encoding="utf-8"))
    assert state["codex.project_review"]["last_action"] == "replace"
    assert state["codex.project_review"]["last_input_digest"].startswith("sha256:")


def test_corrections_task_recognized_and_runs(tmp_path: Path) -> None:
    """corrections must be in TASKS and recognized by config — not permanently disabled."""
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text(
        "### 10:00\n- CORRECTION: user fixed a path bug after a wrong assumption.\n", encoding="utf-8"
    )

    cfg = MemSearchConfig()
    cfg.plugins.codex.corrections.enabled = True
    cfg.plugins.codex.corrections.provider = "openai"

    def fake_runner(ctx, prompt: str) -> str:
        assert ctx.task == "corrections"
        return json.dumps(
            {"action": "replace", "reason": "new durable rule", "content": "# Corrections\n\n- Do X, not Y."}
        )

    results = run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)

    by_task = {r.task: r.action for r in results}
    assert by_task.get("corrections") == "replace"
    assert (project / ".memsearch" / "CORRECTIONS.md").read_text(encoding="utf-8").startswith("# Corrections")


def test_resolve_task_path_memsearch_prefix_is_memsearch_relative(tmp_path: Path) -> None:
    """In shared mode (memsearch_dir != project/.memsearch), `.memsearch/`-prefixed outputs
    resolve memsearch_dir-relative so SessionStart reads what maintenance writes."""
    from memsearch.maintenance import _resolve_task_path

    project = tmp_path / "repo"
    mem = tmp_path / "shared" / ".memsearch"
    project.mkdir(parents=True)
    mem.mkdir(parents=True)

    assert _resolve_task_path(".memsearch/CORRECTIONS.md", project, mem) == (mem / "CORRECTIONS.md").resolve()
    # existing input special-case still holds
    assert _resolve_task_path(".memsearch/memory", project, mem) == (mem / "memory").resolve()
    # absolute and non-.memsearch relative paths stay project-relative
    assert _resolve_task_path("notes/OUT.md", project, mem) == (project / "notes" / "OUT.md").resolve()


def test_atomic_write_text_writes_and_leaves_no_temp(tmp_path: Path) -> None:
    from memsearch.maintenance import _atomic_write_text

    target = tmp_path / "sub" / "CORRECTIONS.md"
    _atomic_write_text(target, "# Corrections\n- rule\n")

    assert target.read_text(encoding="utf-8") == "# Corrections\n- rule\n"
    assert [p.name for p in target.parent.iterdir()] == ["CORRECTIONS.md"]


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    from memsearch.maintenance import _atomic_write_text

    target = tmp_path / "f.md"
    target.write_text("old contents", encoding="utf-8")
    _atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in target.parent.iterdir()] == ["f.md"]


def test_missing_prompt_template_reports_error_not_crash(tmp_path: Path, monkeypatch) -> None:
    """A missing prompt template yields a per-task 'error' result instead of crashing the run."""
    import memsearch.maintenance as maint

    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("- something happened\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.plugins.codex.corrections.enabled = True
    cfg.plugins.codex.corrections.provider = "openai"

    def boom(task: str, _cfg) -> str:
        raise FileNotFoundError(f"{task}.txt")

    monkeypatch.setattr(maint, "_load_prompt_template", boom)

    results = run_due_tasks(
        platform="codex",
        project_dir=project,
        cfg=cfg,
        llm_runner=lambda ctx, prompt: json.dumps({"action": "none", "reason": "unreached"}),
    )

    by_task = {r.task: r.action for r in results}
    assert by_task.get("corrections") == "error"
    # Run completed and returned a result for every task — it did not abort.
    assert set(by_task) == {"project_review", "user_profile", "corrections"}
    assert not (project / ".memsearch" / "CORRECTIONS.md").exists()


def test_maintenance_skips_unchanged_input(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("### 10:00\n- Stable note.\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True

    calls = 0

    def fake_runner(ctx, prompt: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps({"action": "none", "reason": "no durable change"})

    first = run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)
    second = run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)

    assert first[0].action == "none"
    assert second[0].action == "skip"
    assert calls == 1


def test_run_memory_command_rejects_shell_metacharacters(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)
    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True

    captured = {}

    def fake_runner(ctx, prompt: str) -> str:
        captured["ctx"] = ctx
        return json.dumps({"action": "none", "reason": "test"})

    run_due_tasks(platform="codex", project_dir=project, cfg=cfg, force=True, llm_runner=fake_runner)
    output = run_memory_command("cat /etc/passwd", captured["ctx"])

    assert "not allowed" in output


def _ctx_via_runner(project: Path, monkeypatch) -> object:
    monkeypatch.delenv("MEMSEARCH_DIR", raising=False)
    (project / ".memsearch" / "memory").mkdir(parents=True, exist_ok=True)
    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True
    captured: dict[str, object] = {}

    def fake_runner(ctx, prompt: str) -> str:
        captured["ctx"] = ctx
        return json.dumps({"action": "none", "reason": "test"})

    run_due_tasks(platform="codex", project_dir=project, cfg=cfg, force=True, llm_runner=fake_runner)
    return captured["ctx"]


def test_run_memory_command_rejects_find_delete(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx_via_runner(tmp_path / "repo", monkeypatch)
    output = run_memory_command("find . -delete", ctx)
    assert output.startswith("Error:")
    assert "not allowed" in output


def test_run_memory_command_rejects_find_fprintf_and_exec(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx_via_runner(tmp_path / "repo", monkeypatch)
    assert "destructive find primary not allowed" in run_memory_command("find . -fprintf out.txt %p", ctx)
    # -exec ... + has no shell metacharacters, so it must be caught by the new denylist.
    assert "destructive find primary not allowed" in run_memory_command("find . -exec echo +", ctx)


def test_run_memory_command_allows_readonly_find(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx_via_runner(tmp_path / "repo", monkeypatch)
    output = run_memory_command("find . -name *.md", ctx)
    # The denylist must NOT reject a read-only -name search (it may still error from subprocess).
    assert "destructive find primary not allowed" not in output


def test_validate_paths_rejects_windows_absolute_outside_roots(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    memsearch_dir = project / ".memsearch"
    # Direct call: run_memory_command's shlex.split() strips backslashes, so the bypass
    # in fix C1(a) can only be exercised by validating the unmangled token directly.
    err = _validate_paths_in_args(
        [r"C:\Windows\System32\drivers\etc\hosts"],
        [project, input_dir, memsearch_dir],
        cwd=project,
        allow_hash=False,
    )
    assert "outside allowed memory roots" in err


def test_validate_paths_checks_value_of_opt_equals_args(tmp_path: Path) -> None:
    """`--opt=value` args must not bypass root validation when the value is a path."""
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    memsearch_dir = project / ".memsearch"
    err = _validate_paths_in_args(
        ["--file=C:/Users/x/.aws/credentials"],
        [input_dir, memsearch_dir],
        cwd=project,
        allow_hash=False,
    )
    assert "outside allowed memory roots" in err
    # Plain flags without a path value still pass.
    assert (
        _validate_paths_in_args(["-name", "--type=f"], [input_dir, memsearch_dir], cwd=project, allow_hash=False)
        == ""
    )


def test_run_memory_command_rejects_project_files_outside_memory_roots(tmp_path: Path, monkeypatch) -> None:
    """project_dir is no longer an allowed root: injected instructions cannot grep
    arbitrary project files (e.g. .env) for secrets."""
    project = tmp_path / "repo"
    ctx = _ctx_via_runner(project, monkeypatch)
    (project / ".env").write_text("SECRET=x\n", encoding="utf-8")

    # Forward-slash paths: shlex.split() inside run_memory_command strips backslashes.
    output = run_memory_command(f"grep SECRET {(project / '.env').as_posix()}", ctx)
    assert "outside allowed memory roots" in output

    # Paths under the memory roots are still allowed (no path-validation error).
    output = run_memory_command(f"grep note {ctx.input_dir.as_posix()}", ctx)
    assert "outside allowed memory roots" not in output


def test_any_task_exception_is_isolated_per_task(tmp_path: Path) -> None:
    """Any per-task failure (not just FileNotFoundError) yields an 'error' result
    and does not abort sibling tasks."""
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("- something happened\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True
    cfg.plugins.codex.project_review.provider = "openai"
    cfg.plugins.codex.corrections.enabled = True
    cfg.plugins.codex.corrections.provider = "openai"

    def fake_runner(ctx, prompt: str) -> str:
        if ctx.task == "project_review":
            raise ValueError("LLM exploded")
        return json.dumps({"action": "none", "reason": "ok"})

    results = run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)

    by_task = {r.task: r for r in results}
    assert by_task["project_review"].action == "error"
    assert "LLM exploded" in by_task["project_review"].reason
    # Sibling task still ran to completion.
    assert by_task["corrections"].action == "none"


def test_anthropic_tool_loop_enforces_max_tool_calls(monkeypatch) -> None:
    """The Anthropic loop caps per-response tool calls like the OpenAI loop."""
    from memsearch.maintenance import _run_anthropic_with_tools

    counter = {"n": 0}

    def fake_run_memory_command(command: str, ctx) -> str:
        counter["n"] += 1
        return "ok"

    monkeypatch.setattr("memsearch.maintenance.run_memory_command", fake_run_memory_command)

    def tool_use_block(i: int):
        return types.SimpleNamespace(type="tool_use", id=f"toolu_{i}", input={"command": "x"}, text="")

    responses = [
        # One response containing more tool_use blocks than the cap allows.
        types.SimpleNamespace(content=[tool_use_block(i) for i in range(MAX_TOOL_CALLS + 2)]),
        types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="done")]),
    ]
    tool_result_batches: list[list[dict]] = []

    class FakeMessages:
        def create(self, **kwargs):
            tool_result_batches.extend(
                m["content"]
                for m in kwargs.get("messages", [])
                if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), list)
            )
            return responses.pop(0)

    class FakeAnthropic:
        def __init__(self, *args, **kwargs) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)

    ctx = types.SimpleNamespace()
    provider_cfg = LLMProviderConfig(type="anthropic")
    result = _run_anthropic_with_tools(ctx, "prompt", None, provider_cfg)

    assert result == "done"
    assert counter["n"] == MAX_TOOL_CALLS
    over_cap = [r["content"] for r in tool_result_batches[0][MAX_TOOL_CALLS:]]
    assert over_cap == ["Error: memory tool call limit reached"] * 2


def test_scrub_secrets_redacts_hyphenated_openai_key_shapes() -> None:
    """sk-proj-… and sk-ant-api03-… must be redacted (hyphen must not end the match)."""
    proj_key = "sk-proj-" + "A" * 40
    ant_key = "sk-ant-api03-" + "B" * 40
    plain_key = "sk-" + "C" * 24
    text = f"a={proj_key}\nb={ant_key}\nc={plain_key}\n"

    scrubbed = _scrub_secrets(text)

    assert proj_key not in scrubbed
    assert ant_key not in scrubbed
    assert plain_key not in scrubbed
    assert scrubbed.count("[REDACTED]") == 3


def test_file_lock_reclaims_stale_dead_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "stale.lock"
    lock_path.write_text("99999", encoding="utf-8")
    with _file_lock(lock_path) as locked:
        assert locked is True
    # The stale lock must be cleaned up after the reclaimed context exits.
    assert not lock_path.exists()


def test_gemini_tool_loop_enforces_max_tool_calls(tmp_path: Path, monkeypatch) -> None:
    counter = {"n": 0}

    def fake_run_memory_command(command: str, ctx) -> str:
        counter["n"] += 1
        return "ok"

    monkeypatch.setattr("memsearch.maintenance.run_memory_command", fake_run_memory_command)

    tool_outputs: list[str] = []

    class FakeConfig:
        def __init__(self, **kwargs) -> None:
            self.tools = kwargs.get("tools", [])

    class FakeModels:
        def generate_content(self, model, contents, config):
            # Model decides to call the tool more times than the cap allows.
            for _ in range(MAX_TOOL_CALLS + 2):
                tool_outputs.append(config.tools[0]("x"))
            return types.SimpleNamespace(text="done")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr("google.genai.types.GenerateContentConfig", FakeConfig)

    ctx = types.SimpleNamespace()
    provider_cfg = LLMProviderConfig(type="gemini")
    result = _run_gemini_with_tools(ctx, "prompt", None, provider_cfg)

    assert result == "done"
    assert counter["n"] == MAX_TOOL_CALLS
    # Calls beyond the cap must return the limit message instead of invoking the tool.
    assert tool_outputs[MAX_TOOL_CALLS:] == ["Error: memory tool call limit reached"] * 2


def test_openai_tool_loop_handles_malformed_arguments(tmp_path: Path, monkeypatch) -> None:
    called = {"n": 0}

    def fake_run_memory_command(command: str, ctx) -> str:
        called["n"] += 1
        return "ok"

    monkeypatch.setattr("memsearch.maintenance.run_memory_command", fake_run_memory_command)

    bad_call = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(arguments="{not json"),
    )

    class FirstMessage:
        tool_calls = [bad_call]
        content = None

        def model_dump(self, exclude_none: bool = False) -> dict:
            return {"role": "assistant", "tool_calls": []}

    class SecondMessage:
        tool_calls = []
        content = "final"

    responses = [
        types.SimpleNamespace(choices=[types.SimpleNamespace(message=FirstMessage())]),
        types.SimpleNamespace(choices=[types.SimpleNamespace(message=SecondMessage())]),
    ]
    tool_messages: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            for m in kwargs.get("messages", []):
                if isinstance(m, dict) and m.get("role") == "tool":
                    tool_messages.append(m)
            return responses.pop(0)

    class FakeChat:
        def __init__(self) -> None:
            self.completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    ctx = types.SimpleNamespace()
    provider_cfg = LLMProviderConfig(type="openai")
    result = _run_openai_with_tools(ctx, "prompt", "openai", None, provider_cfg)

    assert result == "final"
    assert called["n"] == 0  # malformed parse must not invoke the tool
    assert any(m.get("content") == "Error: invalid tool arguments" for m in tool_messages)


def test_native_provider_requires_plugin_runner(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)
    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True
    cfg.plugins.codex.project_review.provider = "native"

    captured = {}

    def fake_runner(ctx, prompt: str) -> str:
        captured["ctx"] = ctx
        return json.dumps({"action": "none", "reason": "test"})

    run_due_tasks(platform="codex", project_dir=project, cfg=cfg, force=True, llm_runner=fake_runner)

    try:
        run_task_llm(captured["ctx"], "{}", cfg)
    except RuntimeError as e:
        assert "plugin runner" in str(e)
    else:
        raise AssertionError("native maintenance provider should require plugin runner")


# ---------------------------------------------------------------------------
# #9: scrub secrets from journal content before the LLM prompt
# ---------------------------------------------------------------------------


def test_build_prompt_redacts_secrets_in_journals(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)

    aws_key = "AKIA" + "A" * 16
    openai_key = "sk-" + "A" * 24
    google_key = "AIza" + "B" * 35
    (input_dir / "2026-05-27.md").write_text(
        f"aws={aws_key}\nopenai={openai_key}\ngoogle={google_key}\n", encoding="utf-8"
    )

    prompt = _build_prompt(_make_ctx(project, input_dir), MemSearchConfig())

    assert "[REDACTED]" in prompt
    # The raw secret tokens must not survive into the prompt.
    assert aws_key not in prompt
    assert openai_key not in prompt
    assert google_key not in prompt


# ---------------------------------------------------------------------------
# #11: enforce prompt-size cap during construction (not post-hoc)
# ---------------------------------------------------------------------------


def test_build_prompt_caps_oversized_journals_without_posthoc_truncation(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)

    # Many oversized journals: far more raw content than the cap allows.
    for i in range(12):
        (input_dir / f"2026-05-{i:02d}.md").write_text("x" * 50_000, encoding="utf-8")

    prompt = _build_prompt(_make_ctx(project, input_dir), MemSearchConfig())

    assert len(prompt) <= MAX_PROMPT_CHARS
    # During-construction budgeting must carry the load on its own; the post-hoc
    # backstop must NOT fire. If budgeting were broken, the marker would appear.
    assert "[truncated]" not in prompt


def test_input_digest_hashes_full_content_past_truncation_cap(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)

    journal = input_dir / "2026-05-27.md"
    # The prompt keeps only the TAIL (last PER_FILE_BUDGET chars), so a byte near the
    # START lives past the truncation cut and is dropped from the prompt body, while
    # the digest still hashes the full file. total_len > PER_FILE_BUDGET guarantees
    # index 0 falls outside the kept tail window.
    total_len = PER_FILE_BUDGET + 100
    journal.write_text("A" + "y" * (total_len - 1), encoding="utf-8")

    ctx_before = _make_ctx(project, input_dir)
    digest_before = _input_digest(input_dir)
    prompt_before = _build_prompt(ctx_before, MemSearchConfig())

    # Edit one byte at the head, past (before) the truncation cut.
    journal.write_text("B" + "y" * (total_len - 1), encoding="utf-8")

    digest_after = _input_digest(input_dir)
    ctx_after = _make_ctx(project, input_dir)
    prompt_after = _build_prompt(ctx_after, MemSearchConfig())

    # Digest reads full content -> changes when past-cut bytes change.
    assert digest_before != digest_after
    # The truncated prompt body (journal section) ignored the past-cut edit.
    # Strip the digest line, which legitimately differs, before comparing.
    assert _without_digest(prompt_before, digest_before) == _without_digest(prompt_after, digest_after)


def _without_digest(prompt: str, digest: str) -> str:
    return prompt.replace(digest, "<DIGEST>")


def test_read_recent_journals_scrub_then_truncate_cannot_leak_secret_fragment() -> None:
    # A secret straddling the cut: scrub-first redacts it whole; truncate-first
    # would keep its leading fragment (e.g. "sk-ZZ") in the kept prefix.
    secret = "sk-" + "Z" * 24
    raw = "p" * (PER_FILE_BUDGET - 5) + secret
    fragment = secret[:5]  # the part before the truncation boundary

    scrub_then_cut = _scrub_secrets(raw)[:PER_FILE_BUDGET]
    truncate_then_scrub = _scrub_secrets(raw[:PER_FILE_BUDGET])

    assert fragment not in scrub_then_cut  # scrub-first: nothing leaks
    assert fragment in truncate_then_scrub  # truncate-first: fragment survives (the bug we avoid)


def test_read_recent_journals_caps_per_file_and_total(tmp_path: Path) -> None:
    input_dir = tmp_path / "memory"
    input_dir.mkdir()
    for i in range(12):
        (input_dir / f"2026-05-{i:02d}.md").write_text("x" * 50_000, encoding="utf-8")

    out = _read_recent_journals(input_dir, max_total_chars=50_000)

    # No single file contributes more than its per-file budget of raw content,
    # and the function stops accumulating once the total budget is reached.
    assert "x" * (PER_FILE_BUDGET + 1) not in out
    assert len(out) <= 50_000 + PER_FILE_BUDGET  # last chunk may overshoot by at most one file


def test_read_recent_journals_keeps_newest_tail_not_oldest_head(tmp_path: Path) -> None:
    # A single journal larger than PER_FILE_BUDGET: the oldest content (head) must be
    # dropped and the newest content (tail) kept, since journals are chronological
    # append-logs and recency is what matters at recall time.
    input_dir = tmp_path / "memory"
    input_dir.mkdir()
    head_sentinel = "OLDEST_MORNING_TURN"
    tail_sentinel = "NEWEST_EVENING_TURN"
    body = head_sentinel + ("x" * (PER_FILE_BUDGET + 5_000)) + tail_sentinel
    (input_dir / "2026-06-13.md").write_text(body, encoding="utf-8")

    out = _read_recent_journals(input_dir)

    assert tail_sentinel in out  # newest turns survive
    assert head_sentinel not in out  # oldest turns are the ones dropped


def test_gemini_tool_failure_does_not_consume_budget_slot(tmp_path: Path, monkeypatch) -> None:
    """A tool call that RAISES must not consume a MAX_TOOL_CALLS slot: the counter
    only advances on success, so every attempt still reaches the tool instead of
    short-circuiting to the limit message (regression for the pre-increment bug)."""

    def raising_run_memory_command(command: str, ctx) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("memsearch.maintenance.run_memory_command", raising_run_memory_command)

    tool_outputs: list[str] = []

    class FakeConfig:
        def __init__(self, **kwargs) -> None:
            self.tools = kwargs.get("tools", [])

    class FakeModels:
        def generate_content(self, model, contents, config):
            for _ in range(MAX_TOOL_CALLS + 1):
                tool_outputs.append(config.tools[0]("x"))
            return types.SimpleNamespace(text="done")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr("google.genai.types.GenerateContentConfig", FakeConfig)

    ctx = types.SimpleNamespace()
    provider_cfg = LLMProviderConfig(type="gemini")
    result = _run_gemini_with_tools(ctx, "prompt", None, provider_cfg)

    assert result == "done"
    # Failed calls never advanced the counter, so all MAX+1 attempts reached the
    # tool (returning its error) — none hit the "limit reached" short-circuit.
    assert tool_outputs == ["Error: boom"] * (MAX_TOOL_CALLS + 1)


def test_run_memory_command_bare_filename_cannot_read_project_file(tmp_path: Path, monkeypatch) -> None:
    """C2: a bare relative filename resolves inside the memory sandbox (input_dir), not
    the project root, so `grep PATTERN secret.txt` cannot read project/secret.txt."""
    project = tmp_path / "repo"
    ctx = _ctx_via_runner(project, monkeypatch)
    (project / "secret.txt").write_text("TOPSECRET=hunter2\n", encoding="utf-8")
    output = run_memory_command("grep TOPSECRET secret.txt", ctx)
    assert "hunter2" not in output  # the project-root secret is unreachable


def test_run_memory_command_find_without_path_operand_stays_in_sandbox(tmp_path: Path, monkeypatch) -> None:
    """C3: find with no path operand defaults to the sandbox cwd (input_dir), not the
    project root, so it cannot enumerate the project tree."""
    project = tmp_path / "repo"
    ctx = _ctx_via_runner(project, monkeypatch)
    (project / "leak.py").write_text("x = 1\n", encoding="utf-8")
    output = run_memory_command("find -name *.py", ctx)
    assert "leak.py" not in output  # project-tree file not enumerated


def test_run_memory_command_rejects_python3_execution(tmp_path: Path, monkeypatch) -> None:
    """C5: a memory-root parse-transcript.py must NOT be executable as Python — the
    data/code boundary stays closed. python3 is no longer an allowed executable."""
    project = tmp_path / "repo"
    ctx = _ctx_via_runner(project, monkeypatch)
    malicious = ctx.input_dir / "parse-transcript.py"
    malicious.write_text("print('PWNED')\n", encoding="utf-8")
    output = run_memory_command(f"python3 {malicious.as_posix()}", ctx)
    assert "PWNED" not in output
    assert "not allowed" in output


def test_run_restricted_uses_resolved_absolute_binary_not_path(tmp_path: Path, monkeypatch) -> None:
    """C4: the executable is resolved to a trusted absolute path at module load and the
    subprocess gets a sanitized PATH, so a poisoned PATH cannot redirect grep/find."""
    import memsearch.maintenance as maint

    fake_grep = (tmp_path / "realgrep").resolve()
    monkeypatch.setitem(maint._RESOLVED_BINARIES, "grep", str(fake_grep))

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["path"] = kwargs.get("env", {}).get("PATH", "")
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(maint.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", str(tmp_path / "evil"))  # poison PATH AFTER module resolution

    maint._run_restricted(["grep", "x", str(tmp_path)], tmp_path)

    assert captured["argv"][0] == str(fake_grep)  # absolute, module-resolved — not "grep"
    assert str(tmp_path / "evil") not in captured["path"]  # poisoned PATH not inherited


def test_run_restricted_scrubs_secrets_in_output(tmp_path: Path, monkeypatch) -> None:
    """I2: tool output fed back to the LLM must be scrubbed (scrub-then-truncate)."""
    import memsearch.maintenance as maint

    monkeypatch.setitem(maint._RESOLVED_BINARIES, "grep", "/usr/bin/grep")
    secret = "sk-" + "A" * 24

    def fake_run(argv, **kwargs):
        return types.SimpleNamespace(stdout=f"match: {secret}\n", stderr="", returncode=0)

    monkeypatch.setattr(maint.subprocess, "run", fake_run)
    out = maint._run_restricted(["grep", "x", str(tmp_path)], tmp_path)
    assert secret not in out
    assert "[REDACTED]" in out


@pytest.mark.skipif(os.name != "nt", reason="backslash path survival is Windows-specific")
def test_run_memory_command_rejects_windows_backslash_path(tmp_path: Path, monkeypatch) -> None:
    """I1: on Windows, shlex.split(posix=False) preserves backslash paths so the validator
    sees the real absolute path and rejects it (not a mangled, accidentally-rejected token)."""
    project = tmp_path / "repo"
    ctx = _ctx_via_runner(project, monkeypatch)
    output = run_memory_command(r"grep x C:\Windows\System32\drivers\etc\hosts", ctx)
    assert "outside allowed memory roots" in output


def test_build_prompt_scrubs_existing_output_file(tmp_path: Path) -> None:
    """I2: the existing output file content fed into the prompt must be scrubbed too,
    not just the journals."""
    project = tmp_path / "repo"
    input_dir = project / ".memsearch" / "memory"
    input_dir.mkdir(parents=True)
    ctx = _make_ctx(project, input_dir)
    ctx.output_file.parent.mkdir(parents=True, exist_ok=True)
    secret = "AKIA" + "B" * 16
    ctx.output_file.write_text(f"prior state\nkey={secret}\n", encoding="utf-8")
    prompt = _build_prompt(ctx, MemSearchConfig())
    assert secret not in prompt
    assert "[REDACTED]" in prompt


def test_input_digest_streaming_matches_bulk(tmp_path: Path) -> None:
    """I14: chunk-streaming the hash must produce the same digest as a single bulk read."""
    import hashlib

    input_dir = tmp_path / "memory"
    input_dir.mkdir()
    content = b"line\n" * 50_000
    (input_dir / "a.md").write_bytes(content)
    h = hashlib.sha256()
    h.update(b"a.md")
    h.update(b"\0")
    h.update(content)
    h.update(b"\0")
    assert _input_digest(input_dir) == f"sha256:{h.hexdigest()}"


def test_is_due_handles_naive_timestamp_without_raising() -> None:
    """I5: a legacy/naive last_success_at must not raise TypeError on the aware-now
    subtraction (called before the per-task try, so it would abort the whole run)."""
    from memsearch.maintenance import _is_due

    cfg = MemSearchConfig().plugins.codex.project_review
    state = {"last_input_digest": "old", "last_success_at": "2026-06-13T10:00:00"}  # naive, no Z
    due, _reason = _is_due(cfg, state, "new-digest", False, Path("nonexistent"))
    assert isinstance(due, bool)  # reached the datetime math without raising


def test_maintenance_regenerates_deleted_output_on_unchanged_input(tmp_path: Path) -> None:
    """I6: a deleted output file is regenerated even when the journal input digest is
    unchanged — a digest-skip must not strand a missing output."""
    project = tmp_path / "repo"
    memory = project / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-05-27.md").write_text("### 10:00\n- Stable note.\n", encoding="utf-8")

    cfg = MemSearchConfig()
    cfg.plugins.codex.project_review.enabled = True
    calls = {"n": 0}

    def fake_runner(ctx, prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"action": "replace", "reason": "x", "content": "# Project\n- note"})

    run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)
    out = project / ".memsearch" / "PROJECT.md"
    assert calls["n"] == 1 and out.is_file()

    out.unlink()  # output deleted; journals unchanged
    run_due_tasks(platform="codex", project_dir=project, cfg=cfg, llm_runner=fake_runner)
    assert calls["n"] == 2  # regenerated despite unchanged input
    assert out.is_file()


def test_file_lock_does_not_reclaim_fresh_empty_lock(tmp_path: Path) -> None:
    """I7: an empty lock file (creator hasn't written its PID yet) must NOT be reclaimed
    as a dead owner — doing so would delete a live lock and admit a second writer."""
    from memsearch.maintenance import _reclaim_stale_lock

    lock = tmp_path / "x.lock"
    lock.write_text("", encoding="utf-8")  # mid-creation window
    assert _reclaim_stale_lock(lock) is False
    assert lock.exists()


def test_save_state_entry_merges_not_clobbers_sibling(tmp_path: Path) -> None:
    """I8: persisting one task's state must merge with on-disk state, not overwrite a
    sibling task's entry written concurrently by another process."""
    from memsearch.maintenance import _load_state, _save_state_entry

    state_path = tmp_path / ".maintenance-state.json"
    state_path.write_text(json.dumps({"codex.user_profile": {"last_action": "replace"}}), encoding="utf-8")
    _save_state_entry(state_path, "codex.project_review", {"last_action": "none"})
    disk = _load_state(state_path)
    assert set(disk) == {"codex.user_profile", "codex.project_review"}
    assert disk["codex.user_profile"]["last_action"] == "replace"


def test_atomic_write_text_writes_lf_not_crlf(tmp_path: Path) -> None:
    """N9: writes are LF even on Windows — no CRLF churn in committed markdown/state."""
    from memsearch.maintenance import _atomic_write_text

    target = tmp_path / "f.md"
    _atomic_write_text(target, "a\nb\n")
    assert target.read_bytes() == b"a\nb\n"


def test_parse_task_response_handles_nested_braces_in_fenced_json() -> None:
    """N4: a fenced JSON whose string value contains braces must not be truncated by a
    non-greedy capture."""
    from memsearch.maintenance import _parse_task_response

    raw = '```json\n{"action": "replace", "reason": "x", "content": "a {b} c"}\n```'
    data = _parse_task_response(raw)
    assert data["action"] == "replace"
    assert data["content"] == "a {b} c"
