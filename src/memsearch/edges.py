"""SQLite sidecar for undirected graph edges between markdown chunks."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS chunk_edges (
    src_hash  TEXT NOT NULL,
    dst_hash  TEXT NOT NULL,
    relation  TEXT NOT NULL,
    weight    REAL NOT NULL DEFAULT 1.0,
    model     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (src_hash, dst_hash, relation)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_edges_src ON chunk_edges(src_hash);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON chunk_edges(dst_hash);
"""


class EdgeStore:
    """Thin SQLite wrapper for storing and querying undirected chunk edges."""

    def __init__(self, uri: str = "~/.memsearch/edges.db") -> None:
        path = Path(uri).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_DDL)

    def add_edges(self, edges: list[tuple[str, str, str, float, str]]) -> None:
        """Insert or replace edges.  Tuple order: (src, dst, relation, weight, model)."""
        if not edges:
            return
        with self._lock:
            self._conn.executemany("INSERT OR REPLACE INTO chunk_edges VALUES (?,?,?,?,?)", edges)
            self._conn.commit()

    def neighbors(self, hashes: list[str], *, limit_per_node: int = 5) -> list[tuple[str, float, str]]:
        """Return undirected 1-hop neighbors for *hashes*, capped at *limit_per_node* per seed.

        Returns ``(neighbor_hash, weight, seed_hash)`` tuples, sorted desc by weight within
        each seed group.
        """
        if not hashes:
            return []
        ph = ",".join("?" * len(hashes))
        sql = (
            f"SELECT dst_hash, weight, src_hash FROM chunk_edges WHERE src_hash IN ({ph})"
            f" UNION ALL"
            f" SELECT src_hash, weight, dst_hash FROM chunk_edges WHERE dst_hash IN ({ph})"
        )
        with self._lock:
            rows = self._conn.execute(sql, hashes + hashes).fetchall()
        grouped: dict[str, list[tuple[str, float]]] = {}
        for neighbor, weight, seed in rows:
            grouped.setdefault(seed, []).append((neighbor, weight))
        return [
            (n, w, seed)
            for seed, nbrs in grouped.items()
            for n, w in sorted(nbrs, key=lambda x: x[1], reverse=True)[:limit_per_node]
        ]

    def delete_by_hashes(self, hashes: list[str]) -> None:
        """Delete all edges where *hashes* appear as src OR dst."""
        if not hashes:
            return
        ph = ",".join("?" * len(hashes))
        with self._lock:
            self._conn.execute(
                f"DELETE FROM chunk_edges WHERE src_hash IN ({ph}) OR dst_hash IN ({ph})",
                hashes + hashes,
            )
            self._conn.commit()

    def clear(self) -> None:
        """Delete every row from chunk_edges."""
        with self._lock:
            self._conn.execute("DELETE FROM chunk_edges")
            self._conn.commit()

    def is_empty(self) -> bool:
        """Return True if the table has zero rows."""
        with self._lock:
            return self._conn.execute("SELECT 1 FROM chunk_edges LIMIT 1").fetchone() is None

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
