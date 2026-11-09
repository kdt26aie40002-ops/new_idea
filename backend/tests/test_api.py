import httpx
from fastapi.testclient import TestClient

from app.llm.base import LLMRequest, LLMUnavailableError
from app.llm.ollama import OllamaClient
from app.main import create_app

import pytest


def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["provider"] == "demo"
    assert body["unanswered"] == 3


def test_rejects_non_local_host(services):
    with TestClient(create_app(services), base_url="http://evil.example.com") as c:
        assert c.get("/api/status").status_code == 403


def test_post_requires_custom_header(services):
    with TestClient(create_app(services), base_url="http://127.0.0.1:8765") as c:
        assert c.post("/api/chat", json={"text": "hi"}).status_code == 403
        assert c.post("/api/chat", json={"text": "hi"}, headers={"X-Teacher-Agent": "1"}).status_code == 200


def test_rejects_foreign_origin(client):
    r = client.post("/api/prohibitions", json={"kind": "keyword", "value": "x"},
                    headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_full_flow_over_api(client):
    r = client.post("/api/chat", json={"text": "学生からの未回答の質問を確認して"})
    assert r.status_code == 200
    ids = r.json()["meta"]["approval_ids"]
    a = client.get("/api/approvals", params={"status": "pending"}).json()[0]
    assert a["id"] in ids
    bad = client.post(f"/api/approvals/{a['id']}/approve", json={"payload_hash": "0" * 64})
    assert bad.status_code == 409
    ok = client.post(f"/api/approvals/{a['id']}/approve", json={"payload_hash": a["payload_hash"]})
    assert ok.json()["status"] == "executed"


def test_settings_persist_and_validate(client):
    assert client.put("/api/settings", json={"ollama_model": "gemma3:12b"}).json()["ollama_model"] == "gemma3:12b"
    assert client.get("/api/settings").json()["ollama_model"] == "gemma3:12b"
    assert client.put("/api/settings", json={"unknown": 1}).status_code == 400


def test_ollama_down_returns_503_without_fallback(client):
    client.put("/api/settings", json={"llm_provider": "ollama", "ollama_url": "http://127.0.0.1:9"})
    r = client.post("/api/chat", json={"text": "こんにちは"})
    assert r.status_code == 503
    assert "Ollama" in r.json()["detail"]
    assert client.get("/api/chat").json() == []  # デモ LLM で勝手に答えていない
    assert client.get("/api/status").json()["llm"]["ok"] is False


def test_ollama_client_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "<think>考え中</think>こんにちは"}})
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    c = OllamaClient("http://ollama", "qwen3:8b", transport=httpx.MockTransport(handler))
    assert c.complete(LLMRequest(system="s", messages=[], task="general")) == "こんにちは"
    assert c.health()["ok"] is True


def test_ollama_missing_model():
    c = OllamaClient("http://ollama", "nope", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(LLMUnavailableError, match="ollama pull nope"):
        c.complete(LLMRequest(system="s", messages=[], task="general"))


def test_rating_creates_feedback(client):
    mid = client.post("/api/chat", json={"text": "こんにちは"}).json()["id"]
    assert client.post(f"/api/chat/{mid}/rating", json={"rating": -1, "comment": "短く"}).status_code == 200
    assert client.get("/api/feedback").json()[0]["rating"] == -1


@pytest.mark.parametrize("body", [
    {"action": "briefing"},
    {"action": "insights"},
    {"action": "summarize", "conversation_id": "ch-2a"},
    {"action": "minutes", "conversation_id": "m-1102"},
    {"action": "material", "topic": "for文", "kind": "小テスト"},
])
def test_dashboard_ai_actions(client, body):
    r = client.post("/api/dashboard/ai", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["reply"]
    assert client.get("/api/chat").json()[0]["text"].startswith("［ダッシュボード］")


def test_dashboard_unanswered_only_proposes(client):
    r = client.post("/api/dashboard/ai", json={"action": "unanswered"})
    assert len(r.json()["meta"]["approval_ids"]) == 3
    assert all(a["status"] in ("pending", "blocked") for a in client.get("/api/approvals").json())


def test_dashboard_briefing_lists_pending(client):
    client.post("/api/dashboard/ai", json={"action": "unanswered"})
    reply = client.post("/api/dashboard/ai", json={"action": "briefing"}).json()["reply"]
    assert "承認待ち" in reply and "11/14" in reply


@pytest.mark.parametrize("body", [
    {"action": "summarize"},
    {"action": "summarize", "conversation_id": "nope"},
    {"action": "material", "topic": " "},
    {"action": "send_message"},
    {"action": "material", "topic": "x", "kind": "成績表"},
])
def test_dashboard_ai_rejects_bad_input(client, body):
    assert client.post("/api/dashboard/ai", json=body).status_code in (400, 422)


def test_user_name_defaults_to_teams_name(client):
    assert client.get("/api/me").json() == {"name": "佐藤先生", "teams_name": "佐藤先生", "custom": False}


def test_user_name_change_and_reset(client, services):
    r = client.put("/api/settings", json={"user_name": "  佐藤\n花子 先生 "})
    assert r.json()["user_name"] == "佐藤 花子 先生"  # 改行・余分な空白はまとめる
    assert client.get("/api/me").json()["name"] == "佐藤 花子 先生"
    assert services.agent().ctx.teacher_name() == "佐藤 花子 先生"  # AI の名乗りにも反映
    # Teams 上の名前は変わらない
    assert {u["id"]: u["name"] for u in client.get("/api/teams/users").json()}["t-sato"] == "佐藤先生"
    client.put("/api/settings", json={"user_name": ""})
    assert client.get("/api/me").json() == {"name": "佐藤先生", "teams_name": "佐藤先生", "custom": False}


@pytest.mark.parametrize("bad", ["x" * 41, "佐藤\u0000先生", "佐藤‮先生"])
def test_user_name_rejects_invalid(client, bad):
    assert client.put("/api/settings", json={"user_name": bad}).status_code == 400
    assert client.get("/api/me").json()["custom"] is False
