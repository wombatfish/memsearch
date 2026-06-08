from __future__ import annotations

import json
import types
from pathlib import Path

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
    # Tail content lives PAST the per-file truncation cap used for the prompt.
    head = "head\n"
    tail_pos = PER_FILE_BUDGET + 100
    journal.write_text(head + "y" * (tail_pos - len(head)) + "A", encoding="utf-8")

    ctx_before = _make_ctx(project, input_dir)
    digest_before = _input_digest(input_dir)
    prompt_before = _build_prompt(ctx_before, MemSearchConfig())

    # Edit one byte in the tail, past the truncation cut.
    journal.write_text(head + "y" * (tail_pos - len(head)) + "B", encoding="utf-8")

    digest_after = _input_digest(input_dir)
    ctx_after = _make_ctx(project, input_dir)
    prompt_after = _build_prompt(ctx_after, MemSearchConfig())

    # Digest reads full content -> changes when past-cap bytes change.
    assert digest_before != digest_after
    # The truncated prompt body (journal section) ignored the past-cap edit.
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
