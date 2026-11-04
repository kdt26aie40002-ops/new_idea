"""権限・承認・禁止事項（最重要）。"""

import pytest

from app.policy import PolicyError


def _propose(s, conv="c-tanaka", text="テスト返信です。", origin="t1"):
    return s.gateway.propose("teams.send_message", {"conversation_id": conv, "text": text}, origin=origin)


def _count(s, conv):
    return len(s.teams.get_messages(conv))


def test_propose_does_not_send(services):
    before = _count(services, "c-tanaka")
    a = _propose(services)
    assert a["status"] == "pending"
    assert _count(services, "c-tanaka") == before


def test_unknown_action_is_rejected(services):
    with pytest.raises(PolicyError):
        services.gateway.propose("teams.delete_chat", {"conversation_id": "c-tanaka"}, origin="x")


def test_extra_payload_fields_rejected(services):
    # 送信者を偽装するようなフィールドは受け付けない
    with pytest.raises(PolicyError):
        services.gateway.propose("teams.send_message",
                                 {"conversation_id": "c-tanaka", "text": "x", "sender_id": "s-tanaka"}, origin="x")


def test_approve_sends_once(services):
    a = _propose(services)
    before = _count(services, "c-tanaka")
    done = services.approvals.approve(a["id"], a["payload_hash"])
    assert done["status"] == "executed"
    assert _count(services, "c-tanaka") == before + 1
    last = services.teams.get_messages("c-tanaka")[-1]
    assert last["sender_id"] == "t-sato" and last["text"] == "テスト返信です。"
    with pytest.raises(PolicyError):  # 二重実行しない
        services.approvals.approve(a["id"], a["payload_hash"])
    assert _count(services, "c-tanaka") == before + 1


def test_edit_invalidates_previous_approval(services):
    a = _propose(services)
    old_hash = a["payload_hash"]
    edited = services.approvals.edit(a["id"], "修正した返信です。")
    assert edited["payload_hash"] != old_hash
    before = _count(services, "c-tanaka")
    with pytest.raises(PolicyError, match="一致しません"):
        services.approvals.approve(a["id"], old_hash)
    assert _count(services, "c-tanaka") == before
    services.approvals.approve(a["id"], edited["payload_hash"])
    assert services.teams.get_messages("c-tanaka")[-1]["text"] == "修正した返信です。"


def test_keyword_prohibition_blocks_at_proposal(services):
    a = _propose(services, text="クラス全員の成績一覧を送ります")  # 初期データで「成績」は禁止
    assert a["status"] == "blocked"
    with pytest.raises(PolicyError):
        services.approvals.approve(a["id"], a["payload_hash"])


def test_prohibition_normalization(services):
    # 全角・半角や大文字小文字の違いですり抜けない
    services.prohibitions.add("keyword", "password")
    a = _propose(services, text="ＰＡＳＳＷＯＲＤ は abc です")
    assert a["status"] == "blocked"


def test_recipient_prohibition(services):
    a = _propose(services, conv="ch-2a", text="連絡です")  # ch-2a は送信禁止
    assert a["status"] == "blocked"


def test_prohibition_added_after_proposal_blocks_execution(services):
    a = _propose(services, text="明日は休講です")
    services.prohibitions.add("keyword", "休講")
    before = _count(services, "c-tanaka")
    with pytest.raises(PolicyError, match="禁止事項"):
        services.approvals.approve(a["id"], a["payload_hash"])
    assert services.approvals.get(a["id"])["status"] == "blocked"
    assert _count(services, "c-tanaka") == before


def test_edit_into_prohibited_content_is_blocked(services):
    a = _propose(services)
    e = services.approvals.edit(a["id"], "成績を共有します")
    with pytest.raises(PolicyError):
        services.approvals.approve(a["id"], e["payload_hash"])


def test_action_prohibition(services):
    services.prohibitions.add("action", "teams.send_message", "送信全面禁止")
    assert _propose(services)["status"] == "blocked"


def test_disabled_prohibition_not_enforced(services):
    rule = services.prohibitions.add("keyword", "休講")
    services.prohibitions.update(rule["id"], enabled=False)
    assert _propose(services, text="明日は休講です")["status"] == "pending"


def test_reject(services):
    a = _propose(services)
    assert services.approvals.reject(a["id"])["status"] == "rejected"
    with pytest.raises(PolicyError):
        services.approvals.approve(a["id"], a["payload_hash"])


def test_agent_context_has_no_write_access_to_policy(services):
    ctx = services.agent().ctx
    assert not hasattr(ctx.prohibitions, "add")
    assert not hasattr(ctx.prohibitions, "delete")
    assert not hasattr(ctx.gateway, "approve")
    assert not hasattr(ctx, "approvals")


def test_audit_log_has_no_message_body(services):
    a = _propose(services, text="秘密の内容ABC")
    services.approvals.approve(a["id"], a["payload_hash"])
    for row in services.audit.list():
        assert "秘密の内容ABC" not in row["detail"]
