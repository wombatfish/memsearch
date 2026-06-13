from __future__ import annotations

import pytest

from memsearch import compact as compact_module


@pytest.mark.asyncio
async def test_compact_chunks_returns_empty_string_for_empty_input() -> None:
    assert await compact_module.compact_chunks([]) == ""


@pytest.mark.asyncio
async def test_compact_chunks_dispatches_to_openai(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    async def fake_openai(prompt: str, model: str, *, base_url: str | None = None, api_key: str | None = None) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return "openai-summary"

    monkeypatch.setattr(compact_module, "_compact_openai", fake_openai)

    result = await compact_module.compact_chunks(
        [{"content": "alpha"}, {"content": "beta"}],
        llm_provider="openai",
        model="gpt-test",
        base_url="https://example.invalid/v1",
        api_key="env:OPENAI_API_KEY",
    )

    assert result == "openai-summary"
    assert captured == {
        "prompt": compact_module.COMPACT_PROMPT.format(chunks="alpha\n\n---\n\nbeta"),
        "model": "gpt-test",
        "base_url": "https://example.invalid/v1",
        "api_key": "env:OPENAI_API_KEY",
    }


@pytest.mark.asyncio
async def test_compact_chunks_dispatches_to_anthropic(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_anthropic(prompt: str, model: str, *, api_key: str | None = None) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        return "anthropic-summary"

    monkeypatch.setattr(compact_module, "_compact_anthropic", fake_anthropic)

    result = await compact_module.compact_chunks(
        [{"content": "memory chunk"}],
        llm_provider="anthropic",
    )

    assert result == "anthropic-summary"
    assert captured["model"] == "claude-sonnet-4-6"
    assert "memory chunk" in captured["prompt"]


@pytest.mark.asyncio
async def test_compact_chunks_dispatches_to_gemini(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_gemini(prompt: str, model: str, *, api_key: str | None = None) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        return "gemini-summary"

    monkeypatch.setattr(compact_module, "_compact_gemini", fake_gemini)

    result = await compact_module.compact_chunks(
        [{"content": "memory chunk"}],
        llm_provider="gemini",
        prompt_template="Summarize:\n{chunks}",
    )

    assert result == "gemini-summary"
    assert captured == {
        "prompt": "Summarize:\nmemory chunk",
        "model": "gemini-3-flash-preview",
    }


@pytest.mark.asyncio
async def test_compact_chunks_rejects_prompt_without_chunks_placeholder() -> None:
    with pytest.raises(ValueError, match=r"prompt_template must include the \{chunks\} placeholder"):
        await compact_module.compact_chunks(
            [{"content": "x"}],
            prompt_template="Summarize the memory carefully.",
        )


@pytest.mark.asyncio
async def test_compact_chunks_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        await compact_module.compact_chunks([{"content": "x"}], llm_provider="unknown")


@pytest.mark.asyncio
async def test_compact_chunks_caps_oversized_combined_input(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_openai(prompt: str, model: str, *, base_url: str | None = None, api_key: str | None = None) -> str:
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(compact_module, "_compact_openai", fake_openai)

    big = "z" * (compact_module.MAX_COMBINED_CHARS + 5_000)
    await compact_module.compact_chunks([{"content": big}], llm_provider="openai")

    # Oversized chunk text is truncated before going into the prompt.
    assert "[truncated]" in captured["prompt"]
    assert len(captured["prompt"]) < len(big)


@pytest.mark.asyncio
async def test_compact_chunks_cap_is_noop_for_small_input(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_openai(prompt: str, model: str, *, base_url: str | None = None, api_key: str | None = None) -> str:
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(compact_module, "_compact_openai", fake_openai)

    await compact_module.compact_chunks([{"content": "alpha"}, {"content": "beta"}], llm_provider="openai")

    # Small inputs must be untouched: exact prompt match, no truncation marker.
    assert captured["prompt"] == compact_module.COMPACT_PROMPT.format(chunks="alpha\n\n---\n\nbeta")
    assert "[truncated]" not in captured["prompt"]


# ---------------------------------------------------------------------------
# I11: api_key threading into Anthropic and Gemini backends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_text_anthropic_resolves_env_ref_api_key(monkeypatch) -> None:
    anthropic = pytest.importorskip("anthropic")

    constructor_kwargs: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            class _Resp:
                content = [type("_T", (), {"text": "ant-summary", "type": "text"})()]
            return _Resp()

    class FakeClient:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)
            self.messages = FakeMessages()

    monkeypatch.setenv("MY_ANTH_KEY", "sk-ant-secret")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    result = await compact_module.summarize_text(
        "some prompt",
        llm_provider="anthropic",
        api_key="env:MY_ANTH_KEY",
    )

    assert result == "ant-summary"
    assert constructor_kwargs.get("api_key") == "sk-ant-secret"


@pytest.mark.asyncio
async def test_summarize_text_anthropic_no_api_key_uses_ambient(monkeypatch) -> None:
    anthropic = pytest.importorskip("anthropic")

    constructor_kwargs: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            class _Resp:
                content = [type("_T", (), {"text": "ambient-summary", "type": "text"})()]
            return _Resp()

    class FakeClient:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    result = await compact_module.summarize_text(
        "some prompt",
        llm_provider="anthropic",
        api_key=None,
    )

    assert result == "ambient-summary"
    assert "api_key" not in constructor_kwargs


@pytest.mark.asyncio
async def test_summarize_text_gemini_resolves_env_ref_api_key(monkeypatch) -> None:
    genai = pytest.importorskip("google.genai")

    constructor_kwargs: dict = {}

    class FakeModels:
        async def generate_content(self, **kwargs):
            class _Resp:
                text = "gem-summary"
            return _Resp()

    class FakeAio:
        models = FakeModels()

    class FakeClient:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)
            self.aio = FakeAio()

    monkeypatch.setenv("MY_GEM_KEY", "gem-secret-key")
    monkeypatch.setattr(genai, "Client", FakeClient)

    result = await compact_module.summarize_text(
        "some prompt",
        llm_provider="gemini",
        api_key="env:MY_GEM_KEY",
    )

    assert result == "gem-summary"
    assert constructor_kwargs.get("api_key") == "gem-secret-key"


@pytest.mark.asyncio
async def test_summarize_text_gemini_no_api_key_uses_ambient(monkeypatch) -> None:
    genai = pytest.importorskip("google.genai")

    constructor_kwargs: dict = {}

    class FakeModels:
        async def generate_content(self, **kwargs):
            class _Resp:
                text = "ambient-gem"
            return _Resp()

    class FakeAio:
        models = FakeModels()

    class FakeClient:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)
            self.aio = FakeAio()

    monkeypatch.setattr(genai, "Client", FakeClient)

    result = await compact_module.summarize_text(
        "some prompt",
        llm_provider="gemini",
        api_key=None,
    )

    assert result == "ambient-gem"
    assert "api_key" not in constructor_kwargs


@pytest.mark.asyncio
async def test_compact_anthropic_skips_non_text_leading_block(monkeypatch) -> None:
    """_compact_anthropic must not assume content[0] is a text block: a leading
    non-text block (e.g. a future tool_use block) must be skipped and the text
    block(s) returned, instead of raising AttributeError on `.text`."""
    anthropic = pytest.importorskip("anthropic")

    class FakeMessages:
        async def create(self, **kwargs):
            class _Resp:
                content = [
                    type("_Tool", (), {"type": "tool_use", "id": "t1", "input": {}})(),
                    type("_Text", (), {"type": "text", "text": "real-summary"})(),
                ]
            return _Resp()

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    result = await compact_module.summarize_text("p", llm_provider="anthropic", api_key=None)
    assert result == "real-summary"
