"""自己改善：修正・評価の記録 → 改善案の生成 → 教員の承認後にのみ記憶へ反映。

改善案が書き換えられるのは記憶（嗜好・判断・知識）だけ。禁止事項・権限・設定を
変更する経路はこのモジュールには存在しない。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .audit import AuditLog
from .db import Database, now_iso
from .llm import LLMClient, LLMRequest, LLMUnavailableError
from .memory import MemoryStore

ALLOWED_PROPOSAL_KINDS = ("preference", "judgment", "knowledge")
MAX_PROPOSAL_LEN = 300

IMPROVE_SYSTEM = """あなたは教員の業務を支援するAIの改善担当です。
教員がAIの出力を修正・評価した記録から、次回以降AIが教員本人に近づくための「記憶」の案を作ります。
- 出力は JSON 配列のみ。各要素は {"kind": "preference|judgment|knowledge", "content": "...", "rationale": "..."}。
- preference=文章表現や話し方、judgment=判断基準、knowledge=事実。
- 最大3件。根拠が弱ければ空配列 [] を返す。
- 禁止事項・権限・承認ルール・設定の変更は提案してはいけません。"""


class ImprovementError(RuntimeError):
    pass


class ImprovementService:
    def __init__(self, db: Database, memory: MemoryStore, audit: AuditLog):
        self.db, self.memory, self.audit = db, memory, audit

    # --- 記録 ---------------------------------------------------------
    def record_edit(self, target: str, original: str, revised: str) -> int:
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO feedback (kind, target, original, revised, created_at) "
                            "VALUES ('edit', ?, ?, ?, ?)", (target, original, revised, now_iso()))
        self.audit.record("teacher", "feedback.edit", feedback_id=cur.lastrowid, target=target)
        return cur.lastrowid  # type: ignore[return-value]

    def record_rating(self, target: str, original: str, rating: int, comment: str = "") -> int:
        if rating not in (1, -1):
            raise ValueError("rating は 1 か -1")
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO feedback (kind, target, original, rating, comment, created_at) "
                            "VALUES ('rating', ?, ?, ?, ?, ?)", (target, original, rating, comment, now_iso()))
        self.audit.record("teacher", "feedback.rating", feedback_id=cur.lastrowid, target=target, rating=rating)
        return cur.lastrowid  # type: ignore[return-value]

    def list_feedback(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM feedback ORDER BY id DESC")

    # --- 改善案の生成 ---------------------------------------------------
    def generate(self, llm: LLMClient) -> list[dict[str, Any]]:
        created = []
        pending = self.db.query("SELECT * FROM feedback WHERE processed = 0 ORDER BY id")
        for fb in pending:
            # 高評価でコメントも無いものは学ぶ材料が無い
            if fb["kind"] == "rating" and fb["rating"] == 1 and not fb["comment"]:
                self._mark_processed(fb["id"])
                continue
            user = (f"AIの元の出力:\n{fb['original']}\n\n"
                    + (f"教員が修正した版:\n{fb['revised']}\n" if fb["revised"] else "")
                    + (f"評価: {'良い' if fb['rating'] == 1 else '悪い'}\n" if fb["rating"] else "")
                    + (f"コメント: {fb['comment']}\n" if fb["comment"] else ""))
            raw = llm.complete(LLMRequest(
                system=IMPROVE_SYSTEM, messages=[{"role": "user", "content": user}], task="improve",
                hints={"original": fb["original"], "revised": fb["revised"],
                       "comment": fb["comment"], "rating": fb["rating"]}))
            for p in self._parse(raw):
                created.append(self._insert(p, fb["id"]))
            self._mark_processed(fb["id"])
        self.audit.record("agent", "proposals.generated", count=len(created))
        return created

    @staticmethod
    def _parse(raw: str) -> list[dict[str, str]]:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            raise ImprovementError("改善案の形式を読み取れませんでした（LLM の出力が JSON 配列ではありません）")
        try:
            items = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ImprovementError("改善案の JSON を解析できませんでした") from e
        out = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            kind, content = it.get("kind"), str(it.get("content", "")).strip()
            # 許可された記憶種別以外（禁止事項・設定など）への提案は捨てる
            if kind in ALLOWED_PROPOSAL_KINDS and 0 < len(content) <= MAX_PROPOSAL_LEN:
                out.append({"kind": kind, "content": content,
                            "rationale": str(it.get("rationale", ""))[:MAX_PROPOSAL_LEN]})
        return out[:3]

    def _insert(self, p: dict[str, str], feedback_id: int) -> dict[str, Any]:
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO proposals (memory_kind, content, rationale, feedback_id, status,"
                            " created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                            (p["kind"], p["content"], p["rationale"], feedback_id, now_iso()))
        return self.db.one("SELECT * FROM proposals WHERE id = ?", (cur.lastrowid,))  # type: ignore[return-value]

    def _mark_processed(self, feedback_id: int) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE feedback SET processed = 1 WHERE id = ?", (feedback_id,))

    # --- 教員による承認・却下 -------------------------------------------
    def list_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self.db.query("SELECT * FROM proposals WHERE status = ? ORDER BY id DESC", (status,))
        return self.db.query("SELECT * FROM proposals ORDER BY id DESC")

    def decide(self, proposal_id: int, approve: bool, content: str | None = None) -> dict[str, Any]:
        p = self.db.one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if p is None:
            raise KeyError(proposal_id)
        if p["status"] != "pending":
            raise ImprovementError("この改善案は既に処理済みです")
        memory_id = None
        if approve:
            final = (content or p["content"]).strip()
            mem = self.memory.add(p["memory_kind"], final, source=f"改善案#{proposal_id}（教員承認）")
            self.memory.add("improvement", f"改善案#{proposal_id}を承認: {final}",
                            source=f"feedback#{p['feedback_id']}")
            memory_id = mem["id"]
        with self.db.tx() as c:
            c.execute("UPDATE proposals SET status = ?, memory_id = ?, decided_at = ? WHERE id = ?",
                      ("approved" if approve else "rejected", memory_id, now_iso(), proposal_id))
        self.audit.record("teacher", "proposal.approved" if approve else "proposal.rejected",
                          proposal_id=proposal_id, memory_id=memory_id)
        return self.db.one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))  # type: ignore[return-value]


__all__ = ["ImprovementService", "ImprovementError", "LLMUnavailableError", "ALLOWED_PROPOSAL_KINDS"]
