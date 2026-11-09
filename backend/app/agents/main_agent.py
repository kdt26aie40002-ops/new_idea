"""メインエージェント：教員の依頼を解釈し、秘書担当に振り分けるか自分で処理する。

振り分けはキーワードによる決定的なルールで行う（デモ LLM でも動き、挙動を説明しやすいため）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..llm import LLMRequest
from .context import AgentContext
from .prompts import persona_system
from .secretary import SecretaryAgent

_MATERIAL_RE = re.compile(r"(.+?)(?:の|について|に関する)\s*(授業資料|教材|課題|小テスト|テスト|資料)")
_MATERIAL_KINDS = ("授業資料", "教材", "課題", "小テスト", "テスト", "資料")


@dataclass
class AgentResult:
    reply: str
    route: str
    agent: str
    approval_ids: list[int] = field(default_factory=list)
    memory_ids: list[int] = field(default_factory=list)


class MainAgent:
    role = "メイン"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.secretary = SecretaryAgent(ctx)

    # --- 振り分け -------------------------------------------------------
    def route(self, text: str) -> str:
        if re.search(r"未回答|未返信|返事して(い)?ない|質問を確認", text):
            return "unanswered"
        if re.search(r"ブリーフィング|今日(やる|の予定|のタスク)|やることリスト", text):
            return "briefing"
        if re.search(r"傾向|よくある質問|つまず", text):
            return "insights"
        if "議事録" in text:
            return "minutes"
        if re.search(r"要約|まとめて|まとめる", text):
            return "summarize"
        if "返信" in text and self._find_conversation(text, kinds=("chat",)):
            return "reply"
        if any(k in text for k in _MATERIAL_KINDS):
            return "material"
        return "general"

    def _find_conversation(self, text: str, kinds: tuple[str, ...]) -> dict[str, Any] | None:
        convs = [c for c in self.ctx.teams.list_conversations() if c["kind"] in kinds]
        names = {u["id"]: u["name"] for u in self.ctx.teams.list_users()}
        for c in convs:
            keys = {c["title"], *(names.get(p, "") for p in c["participants"]
                                   if p != self.ctx.settings.teacher_id)}
            if any(k and k in text for k in keys):
                return c
        return None

    # --- 実行 -----------------------------------------------------------
    def handle(self, text: str, history: list[dict[str, str]] | None = None) -> AgentResult:
        route = self.route(text)
        self.ctx.audit.record("agent", "task.routed", route=route)
        return self._finish(text, getattr(self, f"_do_{route}")(text, history or []))

    def run_action(self, action: str, label: str, conversation_id: str | None = None,
                   topic: str = "", kind: str = "授業資料") -> AgentResult:
        """ダッシュボードのボタンから直接タスクを実行する（文章からの振り分けを経由しない）。"""
        self.ctx.audit.record("agent", "task.dashboard", action=action)
        if action in ("summarize", "minutes"):
            if not conversation_id:
                raise ValueError("会話を選んでください")
            out = getattr(self.secretary, action)(conversation_id)  # 存在しない会話は ValueError
            result = AgentResult(out, action, SecretaryAgent.role)
        elif action == "material":
            if not topic.strip():
                raise ValueError("テーマを入力してください")
            result = self._do_material(f"{topic}の{kind}を作って", [])
        elif action in ("briefing", "insights", "unanswered"):
            result = getattr(self, f"_do_{action}")(label, [])
        else:
            raise ValueError(f"未知の操作: {action}")
        return self._finish(label, result)

    def _finish(self, text: str, result: AgentResult) -> AgentResult:
        if result.route != "general":
            self.ctx.memory.add("episode", f"依頼「{text[:60]}」を {result.agent} が処理（{result.route}）",
                                source="agent:main")
        return result

    def _do_briefing(self, text: str, history: list) -> AgentResult:
        return AgentResult(self.secretary.briefing(), "briefing", SecretaryAgent.role)

    def _do_insights(self, text: str, history: list) -> AgentResult:
        return AgentResult(self.secretary.insights(), "insights", SecretaryAgent.role)

    def _do_unanswered(self, text: str, history: list) -> AgentResult:
        out = self.secretary.check_unanswered()
        lines = []
        for a in out["proposed"]:
            mark = "承認待ち" if a["status"] == "pending" else "⚠ 禁止事項によりブロック"
            lines.append(f"- {a['summary']}（#{a['id']} {mark}）")
        if out["skipped"]:
            lines.append(f"- 既に返信案があるためスキップ: {'、'.join(out['skipped'])}")
        if not lines:
            return AgentResult("未回答の質問はありません。", "unanswered", SecretaryAgent.role)
        head = f"未回答の質問に対して {len(out['proposed'])} 件の返信案を作成しました。送信は承認後に行います。"
        return AgentResult("\n".join([head, *lines]), "unanswered", SecretaryAgent.role,
                           approval_ids=[a["id"] for a in out["proposed"]])

    def _do_minutes(self, text: str, history: list) -> AgentResult:
        conv = self._find_conversation(text, ("meeting",)) or next(
            (c for c in self.ctx.teams.list_conversations() if c["kind"] == "meeting"), None)
        if conv is None:
            return AgentResult("議事録を作れる会議の記録が見つかりません。", "minutes", SecretaryAgent.role)
        return AgentResult(self.secretary.minutes(conv["id"]), "minutes", SecretaryAgent.role)

    def _do_summarize(self, text: str, history: list) -> AgentResult:
        conv = self._find_conversation(text, ("chat", "channel", "meeting"))
        if conv is None:
            titles = "、".join(c["title"] for c in self.ctx.teams.list_conversations())
            return AgentResult(f"どの会話を要約しますか？（{titles}）", "summarize", SecretaryAgent.role)
        return AgentResult(self.secretary.summarize(conv["id"]), "summarize", SecretaryAgent.role)

    def _do_reply(self, text: str, history: list) -> AgentResult:
        conv = self._find_conversation(text, ("chat",))
        msgs = [m for m in self.ctx.teams.get_messages(conv["id"]) if m["sender_role"] == "student"]  # type: ignore[index]
        if not msgs:
            return AgentResult(f"「{conv['title']}」に学生からのメッセージがありません。", "reply",  # type: ignore[index]
                               SecretaryAgent.role)
        draft, mem_ids = self.secretary.draft_reply(conv, msgs[-1:])  # type: ignore[arg-type]
        a = self.ctx.gateway.propose("teams.send_message", {"conversation_id": conv["id"], "text": draft},  # type: ignore[index]
                                     origin=f"manual-reply:{conv['id']}:{msgs[-1]['id']}")  # type: ignore[index]
        status = "承認待ち一覧に入れました" if a["status"] == "pending" else "禁止事項に該当したためブロックしました"
        return AgentResult(f"返信案を作成し、{status}（#{a['id']}）。\n\n{draft}", "reply",
                           SecretaryAgent.role, approval_ids=[a["id"]], memory_ids=mem_ids)

    def _do_material(self, text: str, history: list) -> AgentResult:
        m = _MATERIAL_RE.search(text)
        topic, kind = (m.group(1).strip(), m.group(2)) if m else (text, "授業資料")
        mem = self.ctx.recall(topic)
        system = persona_system(self.role, self.ctx.teacher_name(), mem, self.ctx.prohibitions.list())
        out = self.ctx.llm.complete(LLMRequest(
            system=system, task="material",
            hints={"topic": topic, "kind": kind, "knowledge": mem["knowledge"], "preferences": mem["preference"]},
            messages=[{"role": "user", "content": f"「{topic}」の{kind}を Markdown で作成してください。依頼文: {text}"}]))
        return AgentResult(out, "material", self.role, memory_ids=mem["_ids"])

    def _do_general(self, text: str, history: list) -> AgentResult:
        mem = self.ctx.recall(text)
        system = persona_system(self.role, self.ctx.teacher_name(), mem, self.ctx.prohibitions.list())
        out = self.ctx.llm.complete(LLMRequest(
            system=system, task="general", hints={"query": text, "memories": mem["knowledge"]},
            messages=[*history[-6:], {"role": "user", "content": text}]))
        return AgentResult(out, "general", self.role, memory_ids=mem["_ids"])
