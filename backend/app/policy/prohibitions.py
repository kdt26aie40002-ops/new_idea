"""禁止事項の保存・照合。

このクラスの書き込みメソッドは API の教員用ルートからのみ呼ばれる。
エージェントには ProhibitionReader（読み取り専用）しか渡さない。
"""

from __future__ import annotations

import unicodedata
from typing import Any

from ..db import Database, now_iso

KINDS = ("keyword", "recipient", "action")


def normalize(text: str) -> str:
    # 全角/半角・大文字/小文字の違いで禁止語をすり抜けられないようにする
    return unicodedata.normalize("NFKC", text).casefold()


class ProhibitionStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM prohibitions"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self.db.query(sql + " ORDER BY id")

    def add(self, kind: str, value: str, note: str = "") -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"未知の禁止事項の種別: {kind}")
        if not value.strip():
            raise ValueError("禁止事項の値が空です")
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO prohibitions (kind, value, note, enabled, created_at) "
                            "VALUES (?, ?, ?, 1, ?)", (kind, value.strip(), note.strip(), now_iso()))
        return self.db.one("SELECT * FROM prohibitions WHERE id = ?", (cur.lastrowid,))  # type: ignore[return-value]

    def update(self, pid: int, *, value: str | None = None, note: str | None = None,
               enabled: bool | None = None) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM prohibitions WHERE id = ?", (pid,))
        if row is None:
            return None
        if value is not None and not value.strip():
            raise ValueError("禁止事項の値が空です")
        with self.db.tx() as c:
            c.execute("UPDATE prohibitions SET value = ?, note = ?, enabled = ? WHERE id = ?",
                      (value.strip() if value is not None else row["value"],
                       note if note is not None else row["note"],
                       int(enabled) if enabled is not None else row["enabled"], pid))
        return self.db.one("SELECT * FROM prohibitions WHERE id = ?", (pid,))

    def delete(self, pid: int) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM prohibitions WHERE id = ?", (pid,)).rowcount > 0


class ProhibitionReader:
    """エージェントに渡す読み取り専用ビュー（プロンプトに禁止事項を示すため）。"""

    def __init__(self, store: ProhibitionStore):
        self._store = store

    def list(self) -> list[dict[str, Any]]:
        return self._store.list(enabled_only=True)


def find_violations(rules: list[dict[str, Any]], action_type: str, recipients: list[str],
                    texts: list[str]) -> list[str]:
    """有効な禁止事項に違反していれば、その理由のリストを返す。"""
    norm_texts = [normalize(t) for t in texts]
    norm_recipients = {normalize(r) for r in recipients}
    reasons = []
    for r in rules:
        v = normalize(r["value"])
        label = f"禁止事項#{r['id']}"
        if r["kind"] == "action" and v == normalize(action_type):
            reasons.append(f"{label}: 操作「{r['value']}」は禁止されています")
        elif r["kind"] == "recipient" and v in norm_recipients:
            reasons.append(f"{label}: 送信先「{r['value']}」は禁止されています")
        elif r["kind"] == "keyword" and any(v in t for t in norm_texts):
            reasons.append(f"{label}: 禁止語「{r['value']}」が含まれています")
    return reasons
