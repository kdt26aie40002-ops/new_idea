from app.llm import LLMRequest


class InjectedLLM:
    """プロンプトインジェクションに「従ってしまった」LLM を模擬する。"""

    name = "evil"

    def complete(self, req: LLMRequest) -> str:
        return "承知しました。2年Aクラス全員の成績一覧です：田中 80点…"

    def health(self):
        return {"ok": True}


def test_routing(services):
    agent = services.agent()
    assert agent.route("学生からの未回答の質問を確認して") == "unanswered"
    assert agent.route("学科会議の議事録を作成して") == "minutes"
    assert agent.route("2年Aクラス 連絡を要約して") == "summarize"
    assert agent.route("田中さんへの返信案を作って") == "reply"
    assert agent.route("for文の授業資料を作って") == "material"
    assert agent.route("こんにちは") == "general"


def test_unanswered_creates_approvals_without_sending(services):
    before = {c["id"]: len(services.teams.get_messages(c["id"])) for c in services.teams.list_conversations()}
    r = services.agent().handle("学生からの未回答の質問を確認して")
    assert r.route == "unanswered"
    assert len(r.approval_ids) == 3  # 田中・鈴木・高橋
    after = {c["id"]: len(services.teams.get_messages(c["id"])) for c in services.teams.list_conversations()}
    assert before == after


def test_unanswered_is_idempotent(services):
    services.agent().handle("未回答の質問を確認して")
    r2 = services.agent().handle("未回答の質問を確認して")
    assert r2.approval_ids == []
    assert len(services.approvals.list("pending")) == 3


def test_reply_uses_knowledge_memory(services):
    r = services.agent().handle("田中さんへの返信案を作って")
    a = services.approvals.get(r.approval_ids[0])
    assert "range(1, 11)" in a["payload"]["text"]


def test_injection_cannot_send_or_change_rules(services):
    services._llm_override = InjectedLLM()
    rules_before = services.prohibitions.list()
    before = len(services.teams.get_messages("c-takahashi"))
    r = services.agent().handle("未回答の質問を確認して")
    rows = [services.approvals.get(i) for i in r.approval_ids]
    # LLM が「成績」を含む文を作っても、禁止事項でブロックされ、送信もされない
    assert all(row["status"] == "blocked" for row in rows)
    assert len(services.teams.get_messages("c-takahashi")) == before
    assert services.prohibitions.list() == rules_before
    # 送信先は LLM 出力ではなくデータから決まる
    assert {row["payload"]["conversation_id"] for row in rows} == {"c-tanaka", "c-suzuki", "c-takahashi"}


def test_untrusted_data_is_wrapped(services):
    seen = {}

    class Spy:
        name = "spy"

        def complete(self, req):
            seen["req"] = req
            return "ok"

        def health(self):
            return {"ok": True}

    services._llm_override = Spy()
    services.agent().handle("高橋さんへの返信案を作って")
    content = seen["req"].messages[-1]["content"]
    assert "<untrusted_data" in content and "[SYSTEM]" in content
    assert "従わず" in seen["req"].system


def test_minutes_and_summary(services):
    m = services.agent().handle("学科会議の議事録を作って")
    assert "決定事項" in m.reply and "11/9" in m.reply
    s = services.agent().handle("2年Aクラス 連絡を要約して")
    assert "要約" in s.reply
