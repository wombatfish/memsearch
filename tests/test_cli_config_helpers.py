from __future__ import annotations

from memsearch import cli as cli_module
from memsearch.config import MemSearchConfig


def test_build_cli_overrides_maps_only_non_none_values() -> None:
    overrides = cli_module._build_cli_overrides(
        provider="google",
        model="gemini-embedding-001",
        batch_size=64,
        base_url=None,
        api_key="env:EMBED_KEY",
        collection="custom_chunks",
        milvus_uri="http://localhost:19530",
        milvus_token=None,
        llm_provider="gemini",
        llm_model="gemini-3-flash-preview",
        prompt_file="prompts/compact.txt",
        llm_base_url="https://llm.example.com",
        llm_api_key="env:LLM_KEY",
        max_chunk_size=2048,
        overlap_lines=3,
        debounce_ms=250,
        reranker_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
    )

    assert overrides == {
        "embedding": {
            "provider": "google",
            "model": "gemini-embedding-001",
            "batch_size": 64,
            "api_key": "env:EMBED_KEY",
        },
        "milvus": {
            "collection": "custom_chunks",
            "uri": "http://localhost:19530",
        },
        # LLM flags map to [llm]/[prompts] (not deprecated [compact]) so explicit
        # CLI flags win over a [llm] config section.
        "llm": {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "base_url": "https://llm.example.com",
            "api_key": "env:LLM_KEY",
        },
        "prompts": {
            "compact": "prompts/compact.txt",
        },
        "chunking": {
            "max_chunk_size": 2048,
            "overlap_lines": 3,
        },
        "watch": {
            "debounce_ms": 250,
        },
        "reranker": {
            "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        },
    }


def test_cfg_to_memsearch_kwargs_translates_resolved_config() -> None:
    cfg = MemSearchConfig()
    cfg.embedding.provider = "local"
    cfg.embedding.model = "all-MiniLM-L6-v2"
    cfg.embedding.batch_size = 32
    cfg.embedding.base_url = "http://embeddings.local"
    cfg.embedding.api_key = "env:LOCAL_KEY"
    cfg.milvus.uri = "http://milvus.local:19530"
    cfg.milvus.token = "milvus-token"
    cfg.milvus.collection = "team_notes"
    cfg.chunking.max_chunk_size = 1800
    cfg.chunking.overlap_lines = 4
    cfg.reranker.model = ""

    kwargs = cli_module._cfg_to_memsearch_kwargs(cfg)

    assert kwargs == {
        "embedding_provider": "local",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_batch_size": 32,
        "embedding_base_url": "http://embeddings.local",
        "embedding_api_key": "env:LOCAL_KEY",
        "milvus_uri": "http://milvus.local:19530",
        "milvus_token": "milvus-token",
        "collection": "team_notes",
        "consistency_level": "",
        "max_chunk_size": 1800,
        "overlap_lines": 4,
        "reranker_model": "",
        "graph_enabled": True,
        "graph_edges_uri": "~/.memsearch/edges.db",
        "graph_weight": 0.5,
        "graph_seed_k": 10,
        "graph_fanout": 5,
        "graph_similar_top_n": 5,
        "graph_similar_threshold": 0.7,
        "graph_structural": True,
        "recency_weight": 0.3,
        "recency_half_life_days": 30.0,
        "max_per_source": 2,
        "fetch_multiplier": 3,
    }


def test_cfg_to_memsearch_kwargs_includes_search_defaults() -> None:
    cfg = MemSearchConfig()
    kwargs = cli_module._cfg_to_memsearch_kwargs(cfg)

    assert kwargs["recency_weight"] == 0.3
    assert kwargs["recency_half_life_days"] == 30.0
    assert kwargs["max_per_source"] == 2
    assert kwargs["fetch_multiplier"] == 3


def test_build_cli_overrides_search_flags() -> None:
    # recency_weight / max_per_source map into the [search] section.
    assert cli_module._build_cli_overrides(recency_weight=0.0) == {"search": {"recency_weight": 0.0}}
    assert cli_module._build_cli_overrides(max_per_source=0) == {"search": {"max_per_source": 0}}
    # None means "not set by the user" → no search key.
    assert "search" not in cli_module._build_cli_overrides(recency_weight=None, max_per_source=None)


def test_cfg_to_memsearch_kwargs_includes_graph_defaults() -> None:
    cfg = MemSearchConfig()
    kwargs = cli_module._cfg_to_memsearch_kwargs(cfg)

    assert kwargs["graph_enabled"] is True
    assert kwargs["graph_edges_uri"] == "~/.memsearch/edges.db"
    assert kwargs["graph_weight"] == 0.5
    assert kwargs["graph_seed_k"] == 10
    assert kwargs["graph_fanout"] == 5
    assert kwargs["graph_similar_top_n"] == 5
    assert kwargs["graph_similar_threshold"] == 0.7
    assert kwargs["graph_structural"] is True


def test_build_cli_overrides_consistency_maps_to_milvus() -> None:
    assert cli_module._build_cli_overrides(consistency="Strong") == {"milvus": {"consistency_level": "Strong"}}
    # None means "not set by the user" → no milvus key
    assert "milvus" not in cli_module._build_cli_overrides(consistency=None)


def test_cfg_to_memsearch_kwargs_carries_consistency_level() -> None:
    cfg = MemSearchConfig()
    cfg.milvus.consistency_level = "Strong"
    assert cli_module._cfg_to_memsearch_kwargs(cfg)["consistency_level"] == "Strong"


def test_build_cli_overrides_graph_flag() -> None:
    # graph=False → {"graph": {"enabled": False}}
    overrides = cli_module._build_cli_overrides(graph=False)
    assert overrides == {"graph": {"enabled": False}}

    # graph=True → {"graph": {"enabled": True}}
    overrides = cli_module._build_cli_overrides(graph=True)
    assert overrides == {"graph": {"enabled": True}}

    # graph=None → no "graph" key (not set by user)
    overrides = cli_module._build_cli_overrides(graph=None)
    assert "graph" not in overrides


def test_derive_collection_name_golden_canonical_paths() -> None:
    """GOLDEN backstop against the orphan-index regression: canonical
    forward-slashed inputs must hash to these EXACT names. These literals were
    computed independently from the pre-normpath function, so they fail loudly
    if any future change (e.g. ntpath.normpath rewriting `/`->`\\`) shifts the
    derived collection name and silently orphans every existing Milvus index."""
    assert cli_module._derive_collection_name("D:/Projects/x") == "ms_x_7d70789c"
    assert cli_module._derive_collection_name("/home/u/proj") == "ms_proj_9c09f805"


def test_derive_collection_name_normalizes_dot_segments() -> None:
    """`.`/`..` segments must collapse so equivalent paths derive the SAME
    collection name (posixpath.normpath parity with `realpath -m`)."""
    assert cli_module._derive_collection_name("/a/b/../c") == cli_module._derive_collection_name("/a/c")
    assert cli_module._derive_collection_name("/a/./c") == cli_module._derive_collection_name("/a/c")
