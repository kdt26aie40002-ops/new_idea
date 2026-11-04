"""承認キューと実行制御。

- ActionGateway: エージェント用。操作を「提案」してキューに入れることしかできない。
- ApprovalService: 教員用（API ルートからのみ使用）。編集・承認・却下・実行。

承認は payload の SHA-256 ハッシュに紐付く。教員は画面で見たハッシュを
添えて承認し、内容が変わっていれば承認は失敗する（再確認が必要）。
実行直前にも禁止事項を再チェックする（承認後にルールが追加された場合に備える）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..audit import AuditLog
from ..db import Database, dumps, now_iso
from ..settings import SettingsStore
from ..teams.base import TeamsAdapter
from .prohibitions import ProhibitionStore, find_violations


class PolicyError(Exception):
    """権限上の理由で操作できない（HTTP 409/403 に変換される）。"""


# --- 操作の定義 -------------------------------------------------------------
class SendMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class ActionSpec:
    payload_model: type[BaseModel]
    label: str


# ここに無い操作はすべて拒否する（許可リスト方式）。
# 外部に影響する操作は全て承認必須なので、自動実行の経路そのものを持たない。
ACTIONS: dict[str, ActionSpec] = {
    "teams.send_message": ActionSpec(SendMessagePayload, "Teams メッセージ送信"),
}


def payload_hash(action_type: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(dumps({"action": action_type, "payload": payload}).encode("utf-8")).hexdigest()


class _Base:
    def __init__(self, db: Database, teams: TeamsAdapter, prohibitions: ProhibitionStore,
                 settings: SettingsStore, audit: AuditLog):
        self.db, self.teams, self.prohibitions = db, teams, prohibitions
        self.settings, self.audit = settings, audit

    def _validate(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        spec = ACTIONS.get(action_type)
        if spec is None:
            raise PolicyError(f"許可されていない操作です: {action_type}")
        try:
            return spec.payload_model.model_validate(payload).model_dump()
        except ValidationError as e:
            raise PolicyError(f"操作内容が不正です: {e.errors()[0]['msg']}") from e

    def _recipients(self, payload: dict[str, Any]) -> list[str]:
        conv = self.teams.get_conversation(payload["conversation_id"])
        if conv is None:
            raise PolicyError("送信先の会話が存在しません")
        names = {u["id"]: u["name"] for u in self.teams.list_users()}
        teacher = self.settings.get().teacher_id
        others = [p for p in conv["participants"] if p != teacher]
        return [conv["id"], conv["title"], *others, *(names.get(p, p) for p in others)]

    def _violations(self, action_type: str, payload: dict[str, Any]) -> list[str]:
        return find_violations(self.prohibitions.list(enabled_only=True), action_type,
                               self._recipients(payload), [payload.get("text", "")])

    def _describe(self, payload: dict[str, Any]) -> tuple[str, str]:
        conv = self.teams.get_conversation(payload["conversation_id"])
        names = {u["id"]: u["name"] for u in self.teams.list_users()}
        teacher = self.settings.get().teacher_id
        to = "、".join(names.get(p, p) for p in conv["participants"] if p != teacher)  # type: ignore[index]
        summary = f"「{conv['title']}」({to}) へメッセージを送信"  # type: ignore[index]
        prediction = (f"{names.get(teacher, teacher)}の名前で {to} に表示されます。"
                      "送信後は取り消せません。（現在はモック Teams への送信）")
        return summary, prediction

    def get(self, approval_id: int) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row:
            row["payload"] = json.loads(row["payload"])
        return row


class ActionGateway(_Base):
    """エージェントが使える唯一の「外部に影響する操作」の窓口。提案のみ。"""

    def has_active(self, origin: str) -> bool:
        """同じ起点の提案が処理中/実行済みなら True（重複実行の防止）。"""
        return self.db.one("SELECT 1 FROM approvals WHERE origin = ? AND status IN "
                           "('pending', 'approved', 'executed')", (origin,)) is not None

    def propose(self, action_type: str, payload: dict[str, Any], origin: str) -> dict[str, Any]:
        clean = self._validate(action_type, payload)
        violations = self._violations(action_type, clean)
        summary, prediction = self._describe(clean)
        status = "blocked" if violations else "pending"
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO approvals (action_type, payload, payload_hash, summary, prediction, origin,"
                " status, original_text, result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (action_type, dumps(clean), payload_hash(action_type, clean), summary, prediction,
                 origin, status, clean.get("text"), dumps(violations) if violations else None, now_iso()))
        self.audit.record("agent", "action.proposed", approval_id=cur.lastrowid,
                          action=action_type, status=status, violations=len(violations))
        return self.get(cur.lastrowid)  # type: ignore[return-value]


class ApprovalService(_Base):
    """教員だけが使う承認操作。"""

    def __init__(self, *args: Any, on_edited: Callable[[dict[str, Any], str], None] | None = None,
                 **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.on_edited = on_edited

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT id FROM approvals", ()
        if status:
            sql, params = sql + " WHERE status = ?", (status,)
        return [self.get(r["id"]) for r in self.db.query(sql + " ORDER BY id DESC", params)]  # type: ignore[misc]

    def _require(self, approval_id: int, *statuses: str) -> dict[str, Any]:
        row = self.get(approval_id)
        if row is None:
            raise KeyError(approval_id)
        if row["status"] not in statuses:
            raise PolicyError(f"この操作は現在「{row['status']}」のため処理できません")
        return row

    def edit(self, approval_id: int, text: str) -> dict[str, Any]:
        """本文を修正する。ハッシュが変わるので、以前の画面からの承認は無効になる。"""
        row = self._require(approval_id, "pending")
        clean = self._validate(row["action_type"], {**row["payload"], "text": text})
        with self.db.tx() as c:
            c.execute("UPDATE approvals SET payload = ?, payload_hash = ? WHERE id = ?",
                      (dumps(clean), payload_hash(row["action_type"], clean), approval_id))
        self.audit.record("teacher", "action.edited", approval_id=approval_id)
        return self.get(approval_id)  # type: ignore[return-value]

    def reject(self, approval_id: int) -> dict[str, Any]:
        self._require(approval_id, "pending", "blocked")
        with self.db.tx() as c:
            c.execute("UPDATE approvals SET status = 'rejected', decided_at = ? WHERE id = ?",
                      (now_iso(), approval_id))
        self.audit.record("teacher", "action.rejected", approval_id=approval_id)
        return self.get(approval_id)  # type: ignore[return-value]

    def approve(self, approval_id: int, expected_hash: str) -> dict[str, Any]:
        row = self._require(approval_id, "pending")
        if row["payload_hash"] != expected_hash:
            self.audit.record("system", "action.hash_mismatch", approval_id=approval_id)
            raise PolicyError("承認しようとした内容と現在の内容が一致しません。最新の内容を確認して再度承認してください。")
        # 承認時点の禁止事項で再チェック
        violations = self._violations(row["action_type"], row["payload"])
        if violations:
            with self.db.tx() as c:
                c.execute("UPDATE approvals SET status = 'blocked', result = ?, decided_at = ? WHERE id = ?",
                          (dumps(violations), now_iso(), approval_id))
            self.audit.record("system", "action.blocked", approval_id=approval_id, violations=len(violations))
            raise PolicyError("禁止事項に該当するため実行できません: " + " / ".join(violations))

        with self.db.tx() as c:
            # status 条件付き UPDATE で二重承認・二重実行を防ぐ
            cur = c.execute("UPDATE approvals SET status = 'approved', decided_at = ? "
                            "WHERE id = ? AND status = 'pending' AND payload_hash = ?",
                            (now_iso(), approval_id, expected_hash))
            if cur.rowcount != 1:
                raise PolicyError("この操作は既に処理されています")
        self.audit.record("teacher", "action.approved", approval_id=approval_id, hash=expected_hash[:12])
        result = self._execute(row)
        if self.on_edited and row["original_text"] and row["payload"].get("text") != row["original_text"]:
            self.on_edited(row, row["payload"]["text"])
        return result

    def _execute(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            if row["action_type"] == "teams.send_message":
                out = self.teams.send_message(row["payload"]["conversation_id"],
                                              self.settings.get().teacher_id, row["payload"]["text"])
            else:  # ACTIONS にあるのに実行器が無い＝実装漏れ。安全側に倒す
                raise PolicyError(f"実行器が未実装の操作です: {row['action_type']}")
            status, result = "executed", dumps(out)
        except Exception as e:  # noqa: BLE001 - 失敗は記録して画面に返す
            status, result = "failed", dumps({"error": str(e)})
        with self.db.tx() as c:
            c.execute("UPDATE approvals SET status = ?, result = ?, executed_at = ? WHERE id = ?",
                      (status, result, now_iso(), row["id"]))
        self.audit.record("system", f"action.{status}", approval_id=row["id"], action=row["action_type"])
        return self.get(row["id"])  # type: ignore[return-value]
