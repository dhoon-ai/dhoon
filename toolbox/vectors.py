"""A vector index in one SQLite file: passages with their embeddings, searched by meaning.

Used by search_all_documents (workspace/.index/vectors.sqlite). Why not a vector database server: at a
person's scale (thousands of passages) numpy compares a question with every stored vector in a few
milliseconds, and a file needs no service to run or back up. Past about a million vectors, sqlite-vec
could take over the search; nothing else changes.

  vectors   (key -> vector)
  items     (source, key -> text, meta): what can be searched, e.g. source "docs" (passages of the
            workspace's documents) or "files" (each document's size and date, to see what changed)
"""

import json
import sqlite3
from pathlib import Path

import numpy as np


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vec BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS items (source TEXT, key TEXT, text TEXT, meta TEXT,
                                              PRIMARY KEY (source, key));
        """)
        self._matrix: dict[str, tuple] = {}  # source -> (keys, texts, metas, matrix), until it changes

    # -- the embedding cache
    def vector(self, key: str) -> list[float] | None:
        row = self.db.execute("SELECT vec FROM vectors WHERE key = ?", (key,)).fetchone()
        return np.frombuffer(row[0], dtype=np.float32).tolist() if row else None

    def put_vector(self, key: str, vec: list[float]) -> None:
        self.db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?)", (key, np.asarray(vec, np.float32).tobytes()))

    def commit(self) -> None:
        self.db.commit()

    # -- searchable items
    def has(self, source: str, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM items WHERE source = ? AND key = ?", (source, key)).fetchone() is not None

    def add(self, source: str, key: str, text: str, meta: dict | None = None) -> None:
        """An item to search; its vector must be in the cache under the same key."""
        self.db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?)", (source, key, text, json.dumps(meta or {})))
        self._matrix.pop(source, None)

    def remove(self, source: str, key: str) -> None:
        self.db.execute("DELETE FROM items WHERE source = ? AND key = ?", (source, key))
        self._matrix.pop(source, None)

    def remove_prefix(self, source: str, prefix: str) -> None:
        """Every item (and vector) whose key starts with prefix: a document's passages when it changed."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        self.db.execute("DELETE FROM vectors WHERE key IN (SELECT key FROM items WHERE source = ? AND key LIKE ? "
                        "ESCAPE '\\')", (source, like))  # fmt: skip
        self.db.execute("DELETE FROM items WHERE source = ? AND key LIKE ? ESCAPE '\\'", (source, like))
        self._matrix.pop(source, None)

    def keys(self, source: str) -> dict[str, str]:
        return dict(self.db.execute("SELECT key, text FROM items WHERE source = ?", (source,)).fetchall())

    def count(self, source: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items WHERE source = ?", (source,)).fetchone()[0]

    def _load(self, source: str) -> tuple:
        if source not in self._matrix:
            rows = self.db.execute("SELECT i.key, i.text, i.meta, v.vec FROM items i JOIN vectors v ON v.key = i.key "
                                   "WHERE i.source = ?", (source,)).fetchall()  # fmt: skip
            matrix = (np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows]) if rows
                      else np.zeros((0, 1), np.float32))  # fmt: skip
            self._matrix[source] = ([r[0] for r in rows], [r[1] for r in rows], [json.loads(r[2]) for r in rows], matrix)
        return self._matrix[source]

    def search(self, vec: list[float], source: str, k: int = 5, least: float = 0.0) -> list[dict]:
        """The k items of a source closest in meaning: [{score, text, meta}], best first (vectors are
        unit length, so the dot product is the cosine similarity)."""
        keys, texts, metas, matrix = self._load(source)
        if not keys:
            return []
        scores = matrix @ np.asarray(vec, np.float32)
        best = np.argsort(-scores)[:k]
        return [{"score": float(scores[i]), "text": texts[i], "meta": metas[i], "key": keys[i]}
                for i in best if scores[i] >= least]  # fmt: skip
