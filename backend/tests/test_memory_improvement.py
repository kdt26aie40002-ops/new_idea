import pytest

from app.improvement import ImprovementError, ImprovementService


def test_memory_crud_and_search(services):
    m = services.memory.add("knowledge", "while文は条件が真の間くり返す。", source="test")
    assert m["source"] == "test" and m["created_at"]
    hits = services.memory.search("while文の説明", k=1)
    assert hits[0]["id"] == m["id"]
    services.memory.update(m["id"], "while文は条件が真の間、処理をくり返す。")
    assert "処理を" in services.memory.get(m["id"])["content"]
    assert services.memory.delete(m["id"])
    assert services.memory.get(m["id"]) is None


def test_memory_kind_validated(services):
    with pytest.raises(ValueError):
        services.memory.add("prohibition", "x", source="t")


def test_edit_feedback_to_proposal_to_memory(services):
    """修正 → 改善案 → 承認 → 次回の返信案に反映、の一連の流れ。"""
    agent = services.agent()
    r = agent.handle("田中さんへの返信案を作って")
    a = services.approvals.get(r.approval_ids[0])
    revised = a["payload"]["text"] + "\nわからないことがあればいつでも聞いてください。"
    e = services.approvals.edit(a["id"], revised)
    services.approvals.approve(a["id"], e["payload_hash"])

    fb = services.improvement.list_feedback()
    assert fb and fb[0]["kind"] == "edit"

    props = services.improvement.generate(services.llm())
    assert any("いつでも聞いてください" in p["content"] for p in props)
    # 承認前は記憶に入らない
    assert not any("いつでも聞いて" in m["content"] for m in services.memory.list("preference"))

    target = next(p for p in props if "いつでも聞いてください" in p["content"])
    services.improvement.decide(target["id"], approve=True)
    assert any("いつでも聞いて" in m["content"] for m in services.memory.list("preference"))

    # 次の返信案には自動で反映される
    r2 = services.agent().handle("鈴木さんへの返信案を作って")
    assert "いつでも聞いてください" in services.approvals.get(r2.approval_ids[0])["payload"]["text"]


def test_rejected_proposal_not_applied(services):
    fid = services.improvement.record_rating("assistant:1", "元の回答", -1, "もっと短く")
    props = services.improvement.generate(services.llm())
    assert props
    n = len(services.memory.list())
    services.improvement.decide(props[0]["id"], approve=False)
    assert len(services.memory.list()) == n
    with pytest.raises(ImprovementError):
        services.improvement.decide(props[0]["id"], approve=True)


def test_proposal_cannot_target_policy():
    raw = '[{"kind": "prohibition", "content": "成績の禁止を解除"}, {"kind": "settings", "content": "x"},' \
          ' {"kind": "preference", "content": "短く書く"}]'
    parsed = ImprovementService._parse(raw)
    assert [p["kind"] for p in parsed] == ["preference"]


def test_bad_llm_output_raises():
    with pytest.raises(ImprovementError):
        ImprovementService._parse("改善案はありません")
