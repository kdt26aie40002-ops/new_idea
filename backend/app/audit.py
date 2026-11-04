"""操作履歴。

detail には ID・種別・ハッシュなどの識別情報だけを入れ、メッセージ本文や
学生の個人情報は入れない（ログ経由の情報漏えい防止）。
"""

from __future__ import annotations

from typing import Any, Literal

from .db import Database, dumps, now_iso

Actor = Literal["agent", "teacher", "system"]


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, actor: Actor, event: str, **detail: Any) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO audit_log (ts, actor, event, detail) VALUES (?, ?, ?, ?)",
                (now_iso(), actor, event, dumps(detail)),
            )

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
