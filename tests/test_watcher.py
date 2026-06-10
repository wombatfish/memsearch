"""Unit tests for the watchdog event handler (no real filesystem watching)."""

from __future__ import annotations

import time
from pathlib import Path

from memsearch.watcher import _MarkdownHandler


class _MoveEvent:
    """Minimal stand-in for watchdog's FileMovedEvent."""

    def __init__(self, src_path: str, dest_path: str, *, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory


def _collect_events(handler_events: list[tuple[str, Path]], timeout_s: float = 2.0, expected: int = 1) -> None:
    deadline = time.monotonic() + timeout_s
    while len(handler_events) < expected and time.monotonic() < deadline:
        time.sleep(0.01)


def test_on_moved_markdown_rename_schedules_delete_and_modify():
    """A rename a.md -> b.md must drop the old source and reindex the new one.
    On Windows, ReadDirectoryChangesW reports os.replace-style atomic saves as
    move events, so missing this handler means editor saves never reindex."""
    events: list[tuple[str, Path]] = []
    h = _MarkdownHandler(lambda et, p: events.append((et, p)), debounce_ms=10)

    h.on_moved(_MoveEvent("a.md", "b.md"))
    _collect_events(events, expected=2)

    assert ("deleted", Path("a.md")) in events
    assert ("modified", Path("b.md")) in events


def test_on_moved_atomic_save_from_temp_only_modifies_dest():
    """os.replace(tmp, target.md): non-markdown src is ignored, dest reindexed."""
    events: list[tuple[str, Path]] = []
    h = _MarkdownHandler(lambda et, p: events.append((et, p)), debounce_ms=10)

    h.on_moved(_MoveEvent("target.md.tmp123", "target.md"))
    _collect_events(events, expected=1)

    assert events == [("modified", Path("target.md"))]


def test_on_moved_directory_event_is_ignored():
    events: list[tuple[str, Path]] = []
    h = _MarkdownHandler(lambda et, p: events.append((et, p)), debounce_ms=10)

    h.on_moved(_MoveEvent("docs", "docs2", is_directory=True))
    time.sleep(0.05)

    assert events == []


def test_on_moved_markdown_to_non_markdown_only_deletes_src():
    events: list[tuple[str, Path]] = []
    h = _MarkdownHandler(lambda et, p: events.append((et, p)), debounce_ms=10)

    h.on_moved(_MoveEvent("note.md", "note.bak"))
    _collect_events(events, expected=1)

    assert events == [("deleted", Path("note.md"))]
