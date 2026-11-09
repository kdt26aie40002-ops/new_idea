"""FastAPI アプリ。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.context import resolve_teacher_name
from .db import dumps, now_iso
from .improvement import ImprovementError
from .llm import LLMUnavailableError
from .memory import KIND_LABELS, MEMORY_KINDS
from .policy import ACTIONS, PolicyError
from .security import LocalOnlyMiddleware
from .services import Services

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# --- リクエストモデル ----------------------------------------------------------
class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DashboardAiIn(BaseModel):
    action: Literal["briefing", "insights", "unanswered", "summarize", "minutes", "material"]
    conversation_id: str | None = Field(default=None, max_length=100)
    topic: str = Field(default="", max_length=200)
    kind: Literal["授業資料", "課題", "小テスト"] = "授業資料"


DASHBOARD_LABELS = {
    "briefing": "今日のブリーフィング", "insights": "学生の質問の傾向分析",
    "unanswered": "学生からの未回答の質問を確認して",
}


class RatingIn(BaseModel):
    rating: Literal[1, -1]
    comment: str = Field(default="", max_length=1000)


class TeamsPostIn(BaseModel):
    sender_id: str
    text: str = Field(min_length=1, max_length=4000)


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ApproveIn(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)


class MemoryIn(BaseModel):
    kind: str
    content: str = Field(min_length=1, max_length=4000)


class MemoryUpdateIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ProhibitionIn(BaseModel):
    kind: Literal["keyword", "recipient", "action"]
    value: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=500)


class ProhibitionUpdateIn(BaseModel):
    value: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class ProposalDecisionIn(BaseModel):
    content: str | None = Field(default=None, max_length=300)


def create_app(services: Services | None = None, data_dir: Path | None = None) -> FastAPI:
    if services is None:
        data_dir = data_dir or Path(os.environ.get("TEACHER_AGENT_DATA_DIR", DEFAULT_DATA_DIR))
        services = Services(Path(data_dir) / "teacher_agent.db")
    s = services

    app = FastAPI(title="Teacher Agent (Teams demo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware)
    app.state.services = s

    # --- エラーの変換 ---------------------------------------------------
    @app.exception_handler(LLMUnavailableError)
    async def _llm_err(_: Request, e: LLMUnavailableError):
        s.audit.record("system", "llm.unavailable")
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(PolicyError)
    async def _policy_err(_: Request, e: PolicyError):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ImprovementError)
    async def _impr_err(_: Request, e: ImprovementError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, e: KeyError):
        return JSONResponse({"detail": "見つかりません"}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # --- 状態・設定 -----------------------------------------------------
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "llm": s.llm().health(),
            "teams": {"adapter": s.teams.name, "message": "モック Teams（本物の Teams には未接続）"},
            "pending_approvals": len(s.approvals.list("pending")),
            "pending_proposals": len(s.improvement.list_proposals("pending")),
            "unanswered": len(s.teams.unanswered_questions(s.settings.get().teacher_id)),
        }

    @app.get("/api/me")
    def me() -> dict[str, Any]:
        settings = s.settings.get()
        teams_name = resolve_teacher_name(settings.model_copy(update={"user_name": ""}), s.teams)
        return {"name": settings.user_name or teams_name, "teams_name": teams_name, "custom": bool(settings.user_name)}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return s.settings.get().model_dump()

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        new = s.settings.update(body)
        s.audit.record("teacher", "settings.updated", keys=sorted(body))
        return new.model_dump()

    # --- AI チャット ----------------------------------------------------
    def _chat_rows() -> list[dict[str, Any]]:
        rows = s.db.query("SELECT * FROM assistant_messages ORDER BY id")
        for r in rows:
            r["meta"] = json.loads(r["meta"])
        return rows

    @app.get("/api/chat")
    def chat_history() -> list[dict[str, Any]]:
        return _chat_rows()

    def _save_exchange(text: str, result: Any, llm_name: str) -> dict[str, Any]:
        meta = {"route": result.route, "agent": result.agent, "approval_ids": result.approval_ids,
                "memory_ids": result.memory_ids, "llm": llm_name}
        with s.db.tx() as c:
            c.execute("INSERT INTO assistant_messages (role, text, created_at) VALUES ('user', ?, ?)",
                      (text, now_iso()))
            cur = c.execute("INSERT INTO assistant_messages (role, text, meta, created_at) "
                            "VALUES ('assistant', ?, ?, ?)", (result.reply, dumps(meta), now_iso()))
        return {"id": cur.lastrowid, "reply": result.reply, "meta": meta}

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        history = [{"role": r["role"], "content": r["text"]} for r in _chat_rows()[-6:]]
        agent = s.agent()
        result = agent.handle(body.text, history)  # LLM 失敗時は 503 になり、何も保存しない
        return _save_exchange(body.text, result, agent.ctx.llm.name)

    @app.post("/api/dashboard/ai")
    def dashboard_ai(body: DashboardAiIn) -> dict[str, Any]:
        """ダッシュボードのワンクリック操作。結果は評価できるように AI チャットの履歴にも残す。"""
        label = DASHBOARD_LABELS.get(body.action)
        if label is None:
            conv = s.teams.get_conversation(body.conversation_id or "")
            if body.action == "material":
                label = f"{body.topic}の{body.kind}を作って"
            elif conv is None:
                raise ValueError("会話を選んでください")
            else:
                label = f"「{conv['title']}」の{'議事録を作って' if body.action == 'minutes' else '要約'}"
        agent = s.agent()
        result = agent.run_action(body.action, label, conversation_id=body.conversation_id,
                                  topic=body.topic, kind=body.kind)
        return _save_exchange(f"［ダッシュボード］{label}", result, agent.ctx.llm.name)

    @app.post("/api/chat/{msg_id}/rating")
    def rate(msg_id: int, body: RatingIn) -> dict[str, Any]:
        row = s.db.one("SELECT * FROM assistant_messages WHERE id = ? AND role = 'assistant'", (msg_id,))
        if row is None:
            raise KeyError(msg_id)
        with s.db.tx() as c:
            c.execute("UPDATE assistant_messages SET rating = ? WHERE id = ?", (body.rating, msg_id))
        fid = s.improvement.record_rating(f"assistant:{msg_id}", row["text"], body.rating, body.comment)
        return {"feedback_id": fid}

    @app.delete("/api/chat")
    def clear_chat() -> dict[str, Any]:
        with s.db.tx() as c:
            c.execute("DELETE FROM assistant_messages")
        s.audit.record("teacher", "chat.cleared")
        return {"ok": True}

    # --- モック Teams ---------------------------------------------------
    @app.get("/api/teams/users")
    def teams_users():
        return s.teams.list_users()

    @app.get("/api/teams/conversations")
    def teams_convs():
        return s.teams.list_conversations()

    @app.get("/api/teams/conversations/{conv_id}/messages")
    def teams_msgs(conv_id: str):
        if s.teams.get_conversation(conv_id) is None:
            raise KeyError(conv_id)
        return s.teams.get_messages(conv_id)

    @app.post("/api/teams/conversations/{conv_id}/messages")
    def teams_post(conv_id: str, body: TeamsPostIn):
        """デモ用：学生や教員になりきってモック Teams に投稿する（人間の操作）。"""
        out = s.teams.send_message(conv_id, body.sender_id, body.text)
        s.audit.record("teacher", "mock_teams.posted", conversation_id=conv_id, sender_id=body.sender_id)
        return out

    # --- 承認キュー -----------------------------------------------------
    @app.get("/api/approvals")
    def approvals(status: str | None = None):
        return s.approvals.list(status)

    @app.get("/api/actions")
    def actions():
        return [{"type": k, "label": v.label, "requires_approval": True} for k, v in ACTIONS.items()]

    @app.post("/api/approvals/{aid}/edit")
    def approval_edit(aid: int, body: EditIn):
        return s.approvals.edit(aid, body.text)

    @app.post("/api/approvals/{aid}/approve")
    def approval_approve(aid: int, body: ApproveIn):
        return s.approvals.approve(aid, body.payload_hash)

    @app.post("/api/approvals/{aid}/reject")
    def approval_reject(aid: int):
        return s.approvals.reject(aid)

    # --- 記憶 -----------------------------------------------------------
    @app.get("/api/memories")
    def memories(kind: str | None = None, q: str | None = None):
        if q:
            return s.memory.search(q, k=20, kinds=(kind,) if kind else None, min_score=0.0)
        return s.memory.list(kind)

    @app.get("/api/memory-kinds")
    def memory_kinds():
        return [{"kind": k, "label": KIND_LABELS[k]} for k in MEMORY_KINDS]

    @app.post("/api/memories")
    def memory_add(body: MemoryIn):
        m = s.memory.add(body.kind, body.content, source="教員が手動登録")
        s.audit.record("teacher", "memory.added", memory_id=m["id"], kind=body.kind)
        return m

    @app.put("/api/memories/{mid}")
    def memory_update(mid: int, body: MemoryUpdateIn):
        m = s.memory.update(mid, body.content)
        if m is None:
            raise KeyError(mid)
        s.audit.record("teacher", "memory.updated", memory_id=mid)
        return m

    @app.delete("/api/memories/{mid}")
    def memory_delete(mid: int):
        if not s.memory.delete(mid):
            raise KeyError(mid)
        s.audit.record("teacher", "memory.deleted", memory_id=mid)
        return {"ok": True}

    # --- 禁止事項（教員のみ） -------------------------------------------
    @app.get("/api/prohibitions")
    def prohibitions():
        return s.prohibitions.list()

    @app.post("/api/prohibitions")
    def prohibition_add(body: ProhibitionIn):
        p = s.prohibitions.add(body.kind, body.value, body.note)
        s.audit.record("teacher", "prohibition.added", prohibition_id=p["id"], kind=body.kind)
        return p

    @app.put("/api/prohibitions/{pid}")
    def prohibition_update(pid: int, body: ProhibitionUpdateIn):
        p = s.prohibitions.update(pid, value=body.value, note=body.note, enabled=body.enabled)
        if p is None:
            raise KeyError(pid)
        s.audit.record("teacher", "prohibition.updated", prohibition_id=pid)
        return p

    @app.delete("/api/prohibitions/{pid}")
    def prohibition_delete(pid: int):
        if not s.prohibitions.delete(pid):
            raise KeyError(pid)
        s.audit.record("teacher", "prohibition.deleted", prohibition_id=pid)
        return {"ok": True}

    # --- 自己改善 -------------------------------------------------------
    @app.get("/api/feedback")
    def feedback():
        return s.improvement.list_feedback()

    @app.get("/api/proposals")
    def proposals(status: str | None = None):
        return s.improvement.list_proposals(status)

    @app.post("/api/proposals/generate")
    def proposals_generate():
        return s.improvement.generate(s.llm())

    @app.post("/api/proposals/{pid}/approve")
    def proposal_approve(pid: int, body: ProposalDecisionIn):
        return s.improvement.decide(pid, True, body.content)

    @app.post("/api/proposals/{pid}/reject")
    def proposal_reject(pid: int):
        return s.improvement.decide(pid, False)

    # --- 操作履歴 -------------------------------------------------------
    @app.get("/api/audit")
    def audit(limit: int = 200):
        rows = s.audit.list(min(max(limit, 1), 1000))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    # --- 画面 -----------------------------------------------------------
    @app.get("/api/{rest:path}")
    def api_404(rest: str):
        raise HTTPException(404, "見つかりません")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
