"""デモ用 LLM（Ollama が無い環境でコアロジックを動かすためのテンプレート応答）。

文章を「考えて」書くわけではなく、渡された構造化データ（hints）から
決まった型の文章を組み立てるだけ。画面上でも「デモ LLM」と明示する。
ただし嗜好記憶（「冒頭に〜」「最後に〜」）は実際に反映するので、
自己改善の流れはデモでも確認できる。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import LLMRequest

_QUOTE_RE = re.compile(r"「([^」]+)」")
_SENT_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.findall(text) if s.strip()]


def apply_preferences(body: str, preferences: list[str]) -> str:
    head: list[str] = []
    tail: list[str] = []
    for p in preferences:
        for phrase in _QUOTE_RE.findall(p):
            if any(k in p for k in ("冒頭", "最初", "始め")):
                if phrase not in body:
                    head.append(phrase)
            elif any(k in p for k in ("最後", "末尾", "締め")):
                if phrase not in body:
                    tail.append(phrase)
    return "\n".join([*head, body, *tail]).strip()


def _short(text: str, n: int = 40) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


class DemoLLM:
    name = "demo"

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "model": "demo-template",
                "message": "デモ LLM（テンプレート応答）で動作中"}

    def complete(self, req: LLMRequest) -> str:
        h = req.hints
        handler = getattr(self, f"_{req.task}")
        return handler(h)

    # --- 各タスク -------------------------------------------------------
    def _general(self, h: dict[str, Any]) -> str:
        mems = h.get("memories") or []
        lines = [f"「{_short(h.get('query', ''), 60)}」について、記憶から関連しそうな情報をまとめました。"]
        if mems:
            lines += [f"- {_short(m, 80)}" for m in mems]
        else:
            lines.append("- 関連する記憶は見つかりませんでした。記憶画面から知識を登録すると参照できます。")
        lines.append("（デモ LLM のため、内容の推論は行っていません）")
        return "\n".join(lines)

    def _summarize(self, h: dict[str, Any]) -> str:
        msgs = h.get("messages") or []
        senders = sorted({m["sender"] for m in msgs})
        questions = [m for m in msgs if re.search(r"[?？]|ですか|ますか", m["text"])]
        lines = [f"■ {h.get('title', '会話')} の要約",
                 f"- 参加者: {', '.join(senders) or 'なし'}（{len(msgs)} 件）"]
        lines += [f"- {m['sender']}: {_short(m['text'])}" for m in msgs[-5:]]
        if questions:
            lines.append(f"- 質問とみられる発言: {len(questions)} 件")
        return "\n".join(lines)

    def _draft_reply(self, h: dict[str, Any]) -> str:
        parts = [f"{h.get('student', '')}さん", "",
                 "ご質問ありがとうございます。"]
        knowledge = h.get("knowledge") or []
        if knowledge:
            parts.append(f"{_short(knowledge[0], 120)}")
            parts.append("上記を参考に、もう一度取り組んでみてください。")
        else:
            parts.append("確認して改めてお知らせします。")
        return apply_preferences("\n".join(parts), h.get("preferences") or [])

    def _minutes(self, h: dict[str, Any]) -> str:
        msgs = h.get("messages") or []
        decisions = [m for m in msgs if re.search(r"決定|決まり|にします|で行きましょう", m["text"])]
        todos = [m for m in msgs if re.search(r"お願いします|担当|までに", m["text"])]
        lines = [f"# 議事録：{h.get('title', '')}",
                 f"参加者：{', '.join(sorted({m['sender'] for m in msgs}))}", "", "## 発言要旨"]
        lines += [f"- {m['sender']}：{_short(m['text'], 60)}" for m in msgs]
        lines += ["", "## 決定事項"] + ([f"- {_short(m['text'], 60)}" for m in decisions] or ["- なし"])
        lines += ["", "## ToDo"] + ([f"- {m['sender']}：{_short(m['text'], 60)}" for m in todos] or ["- なし"])
        return "\n".join(lines)

    def _material(self, h: dict[str, Any]) -> str:
        topic, kind = h.get("topic", "テーマ"), h.get("kind", "授業資料")
        lines = [f"# {kind}：{topic}", "", "## ねらい", f"- {topic} の基本を理解する", "",
                 "## 構成案", "1. 導入（前回の振り返り・5分）", f"2. {topic} の解説（20分）",
                 "3. 演習（20分）", "4. まとめ・質疑（5分）"]
        if kind in ("課題", "テスト"):
            lines += ["", "## 問題案", f"1. {topic} とは何か、自分の言葉で説明しなさい。",
                      f"2. {topic} を使った簡単な例を作りなさい。"]
        if h.get("knowledge"):
            lines += ["", "## 参考にした記憶"] + [f"- {_short(k, 60)}" for k in h["knowledge"]]
        return apply_preferences("\n".join(lines), h.get("preferences") or [])

    def _briefing(self, h: dict[str, Any]) -> str:
        unanswered, pending = h.get("unanswered") or [], h.get("pending") or []
        lines = ["## 今日やること"]
        if pending:
            lines.append(f"- [ ] 承認待ちの返信 {len(pending)} 件を確認して送信する（学生が返事を待っています）")
        if unanswered:
            names = "、".join(f"{q['student']}さん" for q in unanswered)
            lines.append(f"- [ ] 未回答の質問に返信案を作る：{names}")
        lines.append("- [ ] 改善提案と記憶を見直す（返信の質を上げるため）")
        dated = [k for k in h.get("knowledge") or [] if re.search(r"\d+/\d+", k)]
        if dated:
            lines += ["", "## 近い締め切り・予定"] + [f"- {_short(k, 70)}" for k in dated]
        lines.append("\n（デモ LLM のため、優先順位の判断はしていません）")
        return "\n".join(lines)

    def _insights(self, h: dict[str, Any]) -> str:
        msgs = h.get("messages") or []
        questions = [m for m in msgs if re.search(r"[?？]|ですか|ますか", m["text"])]
        lines = ["## よく聞かれていること", f"- 学生の発言 {len(msgs)} 件のうち、質問とみられるもの {len(questions)} 件"]
        lines += [f"- {m['sender']}（{m['where']}）：{_short(m['text'], 50)}" for m in questions]
        lines += ["", "## 記憶に登録しておくとよい知識",
                  "- 質問に出てきた締め切り・範囲・ルールを「知識」として登録すると、返信案に使われます",
                  "", "## クラスへの連絡案", "- 質問の多い項目をまとめて、クラス連絡チャネルで先に案内しましょう",
                  "\n（デモ LLM のため、内容の分析は行っていません）"]
        return "\n".join(lines)

    def _improve(self, h: dict[str, Any]) -> str:
        """修正前後の差分から改善案を JSON 配列で返す（ヒューリスティック）。"""
        original, revised = h.get("original", ""), h.get("revised") or ""
        props: list[dict[str, str]] = []
        if revised:
            o, r = split_sentences(original), split_sentences(revised)
            added = [s for s in r if s not in o]
            removed = [s for s in o if s not in r]
            if added and r and r[0] == added[0]:
                props.append({"kind": "preference", "content": f"返信の冒頭に「{added[0]}」を入れる",
                              "rationale": "教員が修正で冒頭に追加した表現"})
            if added and r and r[-1] == added[-1] and (len(added) > 1 or r[0] != added[0]):
                props.append({"kind": "preference", "content": f"返信の最後に「{added[-1]}」を添える",
                              "rationale": "教員が修正で末尾に追加した表現"})
            for s in removed[:1]:
                props.append({"kind": "preference", "content": f"「{_short(s, 30)}」のような表現は避ける",
                              "rationale": "教員が修正で削除した表現"})
        if h.get("comment"):
            props.append({"kind": "judgment", "content": f"教員の指摘：{h['comment']}",
                          "rationale": "評価時のコメント" + ("（低評価）" if h.get("rating") == -1 else "")})
        return json.dumps(props[:3], ensure_ascii=False)
