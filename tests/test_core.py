"""Integration tests for the MemSearch core class.

Requires OPENAI_API_KEY to be set.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)


@pytest.fixture
def mem(tmp_path: Path):
    from memsearch.core import MemSearch

    m = MemSearch(
        milvus_uri=str(tmp_path / "test.db"),
    )
    yield m
    m.close()


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "notes.md").write_text(
        "# Python Tips\n\n"
        "Use list comprehensions for cleaner code.\n\n"
        "## Virtual Environments\n\n"
        "Always use virtual environments for project isolation.\n"
    )
    (d / "recipes.md").write_text(
        "# Cooking\n\n"
        "## Pasta\n\n"
        "Boil water, add salt, cook pasta for 10 minutes.\n\n"
        "## Salad\n\n"
        "Mix greens, tomatoes, and dressing.\n"
    )
    return d


@pytest.mark.asyncio
async def test_index_and_search(mem, sample_dir: Path):
    mem._paths = [str(sample_dir)]
    n = await mem.index()
    assert n > 0

    results = await mem.search("virtual environment python")
    assert len(results) > 0
    # The top result should be about Python / virtual environments
    top = results[0]
    assert "content" in top
    assert "score" in top


@pytest.mark.asyncio
async def test_index_single_file(mem, sample_dir: Path):
    n = await mem.index_file(sample_dir / "notes.md")
    assert n > 0

    results = await mem.search("list comprehension")
    assert len(results) > 0


@pytest.mark.asyncio
async def test_scoped_index_preserves_other_sources(mem, sample_dir: Path):
    """A narrow `index <file>` must not prune sources outside its scope."""
    mem._paths = [str(sample_dir)]
    await mem.index()
    # Derive the stored source key from the store itself, not by reconstructing
    # the path — avoids coupling the test to scanner path-normalization (case /
    # 8.3 short-name / junction drift) that can't be reproduced off-box.
    recipes = next(s for s in mem.store.indexed_sources() if s.endswith("recipes.md"))

    # Re-index scoped to a single OTHER file. The GC must leave recipes.md alone.
    mem._paths = [str(sample_dir / "notes.md")]
    await mem.index()
    assert recipes in mem.store.indexed_sources(), "scoped index must not prune out-of-scope sources"


@pytest.mark.asyncio
async def test_whole_tree_index_prunes_deleted_file(mem, sample_dir: Path):
    """The scope-aware GC must still prune a deleted file on a whole-tree re-index.

    Primary regression guard: fails if the scope guard silently turns the GC into
    a no-op (e.g. path-form drift making is_relative_to always False).
    """
    mem._paths = [str(sample_dir)]
    await mem.index()
    recipes = next(s for s in mem.store.indexed_sources() if s.endswith("recipes.md"))

    (sample_dir / "recipes.md").unlink()
    await mem.index()
    assert recipes not in mem.store.indexed_sources(), "deleted file must be pruned within the scanned tree"
