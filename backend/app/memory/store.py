"""記憶ストア（検索・追加・更新・削除。出典と日時を管理）。"""

from __future__ import annotations

import json
from typing import Any, Literal, get_args

from ..db import Database, now_iso
from .embed import cosine, embed

MemoryKind = Literal["basic", "knowledge", "episode", "judgment", "preference", "improvement"]
MEMORY_KINDS: tuple[str, ...] = get_args(MemoryKind)

KIND_LABELS = {
    "basic": "基本記憶",
    "knowledge": "知識記憶",
    "episode": "エピソード記憶",
    "judgment": "判断記憶",
    "preference": "嗜好記憶",
    "improvement": "改善記憶",
}


class MemoryStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, kind: str, content: str, source: str) -> dict[str, Any]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知の記憶種別: {kind}")
        if not content.strip():
            raise ValueError("記憶の内容が空です")
        ts = now_iso()
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO memories (kind, content, source, embedding, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, content.strip(), source, json.dumps(embed(content)), ts, ts),
            )
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def get(self, memory_id: int) -> dict[str, Any] | None:
        row = self.db.one("SELECT id, kind, content, source, created_at, updated_at "
                          "FROM memories WHERE id = ?", (memory_id,))
        return row

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, content, source, created_at, updated_at FROM memories"
        if kind:
            return self.db.query(sql + " WHERE kind = ? ORDER BY id DESC", (kind,))
        return self.db.query(sql + " ORDER BY id DESC")

    def update(self, memory_id: int, content: str) -> dict[str, Any] | None:
        if not content.strip():
            raise ValueError("記憶の内容が空です")
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE memories SET content = ?, embedding = ?, updated_at = ? WHERE id = ?",
                (content.strip(), json.dumps(embed(content)), now_iso(), memory_id),
            )
        return self.get(memory_id) if cur.rowcount else None

    def delete(self, memory_id: int) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def search(self, query: str, k: int = 4, kinds: tuple[str, ...] | None = None,
               min_score: float = 0.05) -> list[dict[str, Any]]:
        q = embed(query)
        sql = "SELECT id, kind, content, source, embedding, created_at, updated_at FROM memories"
        params: tuple = ()
        if kinds:
            sql += f" WHERE kind IN ({','.join('?' * len(kinds))})"
            params = tuple(kinds)
        scored = []
        for row in self.db.query(sql, params):
            score = cosine(q, json.loads(row.pop("embedding")))
            if score >= min_score:
                scored.append({**row, "score": round(score, 3)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]
