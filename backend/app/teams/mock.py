"""モック Teams（SQLite 上の疑似チャット）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import Database, dumps, now_iso

SEED_PATH = Path(__file__).with_name("seed.json")


class MockTeamsAdapter:
    name = "mock"

    def __init__(self, db: Database):
        self.db = db

    # --- 初期データ -----------------------------------------------------
    def seed_if_empty(self, path: Path = SEED_PATH) -> bool:
        if self.db.one("SELECT 1 FROM teams_users LIMIT 1"):
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.db.tx() as c:
            for u in data["users"]:
                c.execute("INSERT INTO teams_users (id, name, role) VALUES (?, ?, ?)",
                          (u["id"], u["name"], u["role"]))
            for conv in data["conversations"]:
                c.execute("INSERT INTO teams_conversations (id, title, kind, participants) "
                          "VALUES (?, ?, ?, ?)",
                          (conv["id"], conv["title"], conv["kind"], dumps(conv["participants"])))
                for m in conv["messages"]:
                    c.execute("INSERT INTO teams_messages (conversation_id, sender_id, text, created_at) "
                              "VALUES (?, ?, ?, ?)", (conv["id"], m["sender"], m["text"], m["at"]))
        return True

    # --- 読み取り -------------------------------------------------------
    def list_users(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM teams_users ORDER BY role, id")

    def _user_names(self) -> dict[str, str]:
        return {u["id"]: u["name"] for u in self.list_users()}

    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT c.*, (SELECT MAX(created_at) FROM teams_messages m WHERE m.conversation_id = c.id)"
            " AS last_at FROM teams_conversations c ORDER BY last_at DESC")
        for r in rows:
            r["participants"] = json.loads(r["participants"])
        return rows

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM teams_conversations WHERE id = ?", (conversation_id,))
        if row:
            row["participants"] = json.loads(row["participants"])
        return row

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        names = self._user_names()
        rows = self.db.query(
            "SELECT m.*, u.role AS sender_role FROM teams_messages m "
            "JOIN teams_users u ON u.id = m.sender_id "
            "WHERE conversation_id = ? ORDER BY m.id", (conversation_id,))
        for r in rows:
            r["sender_name"] = names.get(r["sender_id"], r["sender_id"])
        return rows

    def unanswered_questions(self, teacher_id: str) -> list[dict[str, Any]]:
        """個人チャットで、学生の発言の後に教員がまだ返信していないもの。"""
        result = []
        for conv in self.list_conversations():
            if conv["kind"] != "chat" or teacher_id not in conv["participants"]:
                continue
            msgs = self.get_messages(conv["id"])
            pending = []
            for m in msgs:
                if m["sender_id"] == teacher_id:
                    pending = []
                elif m["sender_role"] == "student":
                    pending.append(m)
            if pending:
                result.append({"conversation": conv, "messages": pending})
        return result

    # --- 書き込み -------------------------------------------------------
    def send_message(self, conversation_id: str, sender_id: str, text: str) -> dict[str, Any]:
        conv = self.get_conversation(conversation_id)
        if conv is None:
            raise ValueError(f"会話が見つかりません: {conversation_id}")
        if sender_id not in conv["participants"]:
            raise ValueError("送信者がこの会話の参加者ではありません")
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO teams_messages (conversation_id, sender_id, text, created_at) "
                            "VALUES (?, ?, ?, ?)", (conversation_id, sender_id, text, now_iso()))
        return {"id": cur.lastrowid, "conversation_id": conversation_id}
