from __future__ import annotations

import json

from memsearch.core import _build_expand_result


def test_build_expand_result_matches_expand_json_golden(tmp_path) -> None:
    md = tmp_path / "memory.md"
    md.write_text(
        "# Root\n"
        "intro\n"
        "## Session\n"
        "<!-- session:s1 turn:t1 transcript:C:/logs/session.jsonl -->\n"
        "chunk body\n"
        "neighbor body\n"
        "## Next\n"
        "other\n",
        encoding="utf-8",
    )

    class FakeStore:
        def query(self, *, filter_expr: str = ""):
            assert "deadbeef" in filter_expr
            return [
                {
                    "chunk_hash": "deadbeef",
                    "source": str(md),
                    "heading": "Session",
                    "heading_level": 2,
                    "start_line": 5,
                    "end_line": 5,
                }
            ]

    expected = {
        "chunk_hash": "deadbeef",
        "source": str(md),
        "heading": "Session",
        "start_line": 3,
        "end_line": 6,
        "content": "## Session\n<!-- session:s1 turn:t1 transcript:C:/logs/session.jsonl -->\nchunk body\nneighbor body",
        "anchor": {
            "session": "s1",
            "turn": "t1",
            "transcript": "C:/logs/session.jsonl",
        },
    }

    result = _build_expand_result(FakeStore(), "deadbeef", section=True, lines=None, cfg=None)

    assert json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)
