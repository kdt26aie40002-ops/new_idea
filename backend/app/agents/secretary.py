"""秘書担当エージェント：Teams の会話の要約・議事録・返信案。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..llm import LLMRequest
from .context import AgentContext
from .prompts import persona_system, untrusted


def _local(iso: str) -> str:
    """発言時刻を PC のローカル時刻で表示（LLM が年月日を推測しないように）。"""
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%Y-%m-%d %H:%M}"
    except ValueError:
        return iso


class SecretaryAgent:
    role = "秘書担当"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def _system(self, query: str) -> tuple[str, dict[str, Any]]:
        mem = self.ctx.recall(query)
        return persona_system(self.role, self.ctx.teacher_name(), mem, self.ctx.prohibitions.list()), mem

    def _transcript(self, conv_id: str) -> tuple[dict[str, Any], list[dict[str, str]], str]:
        conv = self.ctx.teams.get_conversation(conv_id)
        if conv is None:
            raise ValueError(f"会話が見つかりません: {conv_id}")
        raw = self.ctx.teams.get_messages(conv_id)
        msgs = [{"sender": m["sender_name"], "text": m["text"]} for m in raw]
        body = "\n".join(f"[{_local(m['created_at'])}] {m['sender_name']}: {m['text']}" for m in raw)
        return conv, msgs, untrusted(f"teams:{conv_id}", body)

    def summarize(self, conv_id: str) -> str:
        conv, msgs, data = self._transcript(conv_id)
        system, _ = self._system(conv["title"])
        return self.ctx.llm.complete(LLMRequest(
            system=system, task="summarize", hints={"title": conv["title"], "messages": msgs},
            messages=[{"role": "user", "content": f"次の Teams の会話「{conv['title']}」を箇条書きで要約してください。\n\n{data}"}]))

    def minutes(self, conv_id: str) -> str:
        conv, msgs, data = self._transcript(conv_id)
        system, _ = self._system(conv["title"])
        return self.ctx.llm.complete(LLMRequest(
            system=system, task="minutes", hints={"title": conv["title"], "messages": msgs},
            messages=[{"role": "user", "content":
                       f"次の会議「{conv['title']}」の記録から、参加者・発言要旨・決定事項・ToDo を含む議事録を Markdown で作成してください。\n\n{data}"}]))

    def draft_reply(self, conv: dict[str, Any], student_msgs: list[dict[str, Any]]) -> tuple[str, list[int]]:
        question = "\n".join(m["text"] for m in student_msgs)
        student = student_msgs[-1]["sender_name"]
        system, mem = self._system(question)
        text = self.ctx.llm.complete(LLMRequest(
            system=system, task="draft_reply",
            hints={"student": student, "question": question,
                   "knowledge": mem["knowledge"], "preferences": mem["preference"]},
            messages=[{"role": "user", "content":
                       f"学生「{student}」からの次のメッセージに対する、{self.ctx.teacher_name()}としての返信文の下書きだけを書いてください。"
                       f"前置きや説明は不要です。Teams では送信者名が表示されるので、署名は書かないでください。"
                       f"記憶にない事実（日付・範囲・ルールなど）は作らず、わからなければ確認すると書いてください。\n\n{untrusted('teams:' + conv['id'], question)}"}]))
        return text, mem["_ids"]

    def briefing(self) -> str:
        """今日やることを、未回答の質問・承認待ち・記憶の予定から優先順にまとめる。"""
        teacher = self.ctx.settings.teacher_id
        unanswered = [{"student": i["messages"][-1]["sender_name"], "text": i["messages"][-1]["text"]}
                      for i in self.ctx.teams.unanswered_questions(teacher)]
        pending = self.ctx.gateway.pending_summaries()
        knowledge = [m["content"] for m in self.ctx.memory.list("knowledge")]
        system, _ = self._system("今日の予定 締め切り 期限")
        questions = "\n".join(f"- {q['student']}: {q['text']}" for q in unanswered) or "なし"
        data = "\n".join([
            "## 学生からの未回答の質問", untrusted("teams:unanswered", questions),
            "## 承認待ちの送信", "\n".join(f"- {p}" for p in pending) or "なし",
            "## 予定・締め切りに関する記憶", "\n".join(f"- {k}" for k in knowledge) or "なし",
        ])
        return self.ctx.llm.complete(LLMRequest(
            system=system, task="briefing",
            hints={"unanswered": unanswered, "pending": pending, "knowledge": knowledge},
            messages=[{"role": "user", "content":
                       "次の情報から、今日やるべきことを優先度の高い順に 3〜6 項目の Markdown のチェックリストで書いてください。"
                       "各項目に理由を一言添え、最後に近い締め切りを日付順に挙げてください。"
                       f"情報にない予定は作らないでください。\n\n{data}"}]))

    def insights(self) -> str:
        """学生の発言全体から、よくある質問・つまずき・先回りの連絡案をまとめる。"""
        msgs = []
        for c in self.ctx.teams.list_conversations():
            if c["kind"] == "meeting":
                continue
            msgs += [{"sender": m["sender_name"], "text": m["text"], "where": c["title"]}
                     for m in self.ctx.teams.get_messages(c["id"]) if m["sender_role"] == "student"]
        system, _ = self._system(" ".join(m["text"] for m in msgs)[:400])
        body = "\n".join(f"[{m['where']}] {m['sender']}: {m['text']}" for m in msgs)
        return self.ctx.llm.complete(LLMRequest(
            system=system, task="insights", hints={"messages": msgs},
            messages=[{"role": "user", "content":
                       "次の学生の発言全体を分析し、Markdown で次の 3 つを書いてください。\n"
                       "1. よく聞かれていること・つまずいている点（件数の目安つき）\n"
                       "2. 記憶に登録しておくと返信が楽になりそうな知識\n"
                       "3. クラス全体に先回りして伝えるとよい連絡の文案\n\n"
                       f"{untrusted('teams:students', body)}"}]))

    def check_unanswered(self) -> dict[str, Any]:
        """未回答の質問ごとに返信案を作り、送信を承認キューに入れる（送信はしない）。"""
        proposed, skipped = [], []
        for item in self.ctx.teams.unanswered_questions(self.ctx.settings.teacher_id):
            conv, msgs = item["conversation"], item["messages"]
            origin = f"unanswered:{conv['id']}:{msgs[-1]['id']}"
            if self.ctx.gateway.has_active(origin):
                skipped.append(conv["title"])
                continue
            text, _ = self.draft_reply(conv, msgs)
            # 送信先 conversation_id はデータから決める。LLM の出力では決めない。
            proposed.append(self.ctx.gateway.propose(
                "teams.send_message", {"conversation_id": conv["id"], "text": text}, origin=origin))
        return {"proposed": proposed, "skipped": skipped}
