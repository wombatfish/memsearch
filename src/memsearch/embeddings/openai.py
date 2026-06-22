"""OpenAI embedding provider.

Requires: ``pip install memsearch`` (openai is included by default)
Environment variables:
    OPENAI_API_KEY   — required
    OPENAI_BASE_URL  — optional, override API base URL
"""

from __future__ import annotations

import os


class OpenAIEmbedding:
    """OpenAI text-embedding provider."""

    # OpenAI limits total tokens per embedding request to 300K.
    # A lower batch size avoids hitting that limit with large chunks.
    _DEFAULT_BATCH_SIZE = 256

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        batch_size: int = 0,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        import openai

        kwargs: dict = {}
        # Explicit params take priority over environment variables.
        # Env vars (OPENAI_BASE_URL, OPENAI_API_KEY) are still read
        # automatically by the openai SDK when not overridden here.
        effective_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        if effective_base_url:
            kwargs["base_url"] = effective_base_url
        if api_key:
            kwargs["api_key"] = api_key

        self._client = openai.AsyncOpenAI(**kwargs)  # reads OPENAI_API_KEY if not provided
        self._model = model
        self._client_kwargs = kwargs
        # Known models resolve from the table; unknown models defer the trial
        # embed until the dimension is first needed (no network at construction).
        self._dimension: int | None = _KNOWN_DIMENSIONS.get(model)
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = _detect_dimension(self._model, self._client_kwargs)
        return self._dimension

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        return await batched_embed(texts, self._embed_batch, self._batch_size)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(input=texts, model=self._model, encoding_format="float")
        return [item.embedding for item in resp.data]


_KNOWN_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _detect_dimension(model: str, client_kwargs: dict) -> int:
    """Return the embedding dimension for *model* via a trial embed.

    Only called lazily for models not in ``_KNOWN_DIMENSIONS``
    (e.g. custom models via OPENAI_BASE_URL).
    """
    import openai

    sync_client = openai.OpenAI(**client_kwargs)
    trial = sync_client.embeddings.create(input=["dim"], model=model, encoding_format="float")
    return len(trial.data[0].embedding)
