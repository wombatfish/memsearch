"""SQLite sidecar for undirected graph edges between markdown chunks."""

from __future__ import annotations

import contextlib
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
        # edges.db is a single global file shared across every session/process, and
        # an interactive `memsearch search` opens it on the hot path. Keep the
        # busy_timeout SHORT: it bounds writer-vs-writer contention (watcher/index),
        # but must never let an open stall the session. A 30s timeout here turned a
        # fast-fail into a multi-second hang behind the session-start indexer.
        self._conn.execute("PRAGMA busy_timeout=3000")
        # Best-effort WAL so reads never block a writer even during its commit
        # window. The DELETE->WAL conversion needs a brief exclusive lock and RAISES
        # 'database is locked' (it does NOT honor busy_timeout) when a writer is
        # active — swallow it; the file converts on the next uncontended open and
        # stays WAL. (WAL is unsupported on network filesystems — edges.db is local.)
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("PRAGMA journal_mode=WAL")
        # Run schema DDL ONLY on first creation. `CREATE ... IF NOT EXISTS` still
        # acquires a write lock even when the objects already exist, so running it on
        # every open stalls behind the session-start indexer's write transaction (up
        # to busy_timeout) — the actual cause of the session hang. A `sqlite_master`
        # read never blocks on a writer's RESERVED lock, so it is safe on the hot path.
        table_exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_edges'"
        ).fetchone()
        if table_exists is None:
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
        # Batched in 500-hash slices, mirroring replace_all's batching for symmetry
        # (each batch binds 2x the slice — src + dst IN-clauses). One trailing commit
        # keeps every slice in a single transaction, as the un-batched form was.
        with self._lock:
            for i in range(0, len(hashes), 500):
                batch = hashes[i : i + 500]
                ph = ",".join("?" * len(batch))
                self._conn.execute(
                    f"DELETE FROM chunk_edges WHERE src_hash IN ({ph}) OR dst_hash IN ({ph})",
                    batch + batch,
                )
            self._conn.commit()

    def replace_all(self, owned: list[str], edges: list[tuple[str, str, str, float, str]]) -> None:
        """Atomically replace one collection's edges — delete every edge sourced
        from *owned* (the collection's current chunk hashes), then bulk-insert
        *edges*, in ONE transaction.

        Scoped by ``src_hash IN owned`` rather than a global truncate because
        ``edges.db`` is a single file shared across every project/collection: an
        unscoped ``DELETE`` would wipe other collections' edges (the same
        scoped-operation/global-delete footgun that caused real loss on
        2026-05-29).  Every edge this store inserts has its src endpoint in the
        owning collection, so ``src_hash IN owned`` deletes exactly this
        collection's prior edges and nothing else.  The ``owned`` list is batched
        to stay under SQLite's host-parameter ceiling regardless of corpus size.

        One transaction (the implicit txn the first DELETE opens holds the write
        lock through the INSERT until commit) so concurrent readers never observe
        a partial graph and a crash or BUSY mid-swap rolls back to the prior
        edges rather than truncating them."""
        with self._lock:
            try:
                for i in range(0, len(owned), 500):
                    batch = owned[i : i + 500]
                    ph = ",".join("?" * len(batch))
                    self._conn.execute(f"DELETE FROM chunk_edges WHERE src_hash IN ({ph})", batch)
                if edges:
                    self._conn.executemany("INSERT OR REPLACE INTO chunk_edges VALUES (?,?,?,?,?)", edges)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def replace_structural_edges(self, owned: list[str], edges: list[tuple[str, str, str, float, str]]) -> None:
        """Atomically replace this source's structural (sibling/same_section) edges:
        delete prior sibling/same_section rows whose src is in *owned* (the source's
        current chunk hashes), then bulk-insert *edges*, in ONE transaction. Relation-
        scoped to ('sibling','same_section') so cross-file 'similar' edges survive.
        Batched under SQLite's host-parameter ceiling; rolls back on failure."""
        with self._lock:
            try:
                for i in range(0, len(owned), 500):
                    batch = owned[i : i + 500]
                    ph = ",".join("?" * len(batch))
                    self._conn.execute(
                        f"DELETE FROM chunk_edges WHERE relation IN ('sibling','same_section') AND src_hash IN ({ph})",
                        batch,
                    )
                if edges:
                    self._conn.executemany("INSERT OR REPLACE INTO chunk_edges VALUES (?,?,?,?,?)", edges)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def clear(self) -> None:
        """Delete every row from chunk_edges."""
        with self._lock:
            self._conn.execute("DELETE FROM chunk_edges")
            self._conn.commit()

    def is_empty(self) -> bool:
        """Return True if the table has zero rows."""
        with self._lock:
            return self._conn.execute("SELECT 1 FROM chunk_edges LIMIT 1").fetchone() is None

    def is_empty_for(self, hashes: list[str]) -> bool:
        """Return True if none of *hashes* appear as the src or dst of any edge.

        Collection-scoped counterpart to ``is_empty()`` for the shared global
        ``edges.db``: ``is_empty()`` scans the whole table (every collection), so
        it falsely reports "not empty" for a fresh collection co-mingled with a
        populated one — suppressing the "run graph rebuild" hint exactly when it
        is needed.  Checking the caller's own chunk hashes restores per-collection
        accuracy.  Batched and short-circuited, so for a query's top results
        (tens of hashes) it is a single indexed lookup."""
        if not hashes:
            return True
        with self._lock:
            for i in range(0, len(hashes), 500):
                batch = hashes[i : i + 500]
                ph = ",".join("?" * len(batch))
                row = self._conn.execute(
                    f"SELECT 1 FROM chunk_edges WHERE src_hash IN ({ph}) OR dst_hash IN ({ph}) LIMIT 1",
                    batch + batch,
                ).fetchone()
                if row is not None:
                    return False
        return True

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
