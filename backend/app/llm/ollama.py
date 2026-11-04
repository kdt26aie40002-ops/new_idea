"""Ollama クライアント（ローカル LLM）。

失敗時は LLMUnavailableError を投げるだけで、外部 AI API などへの
フォールバックは一切行わない。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .base import LLMRequest, LLMUnavailableError

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n```$", re.DOTALL)


class OllamaClient:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout_sec: float = 120.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_sec,
                                    transport=transport)

    def complete(self, req: LLMRequest) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": req.system}, *req.messages],
        }
        try:
            r = self._client.post("/api/chat", json=body)
        except httpx.TimeoutException as e:
            raise LLMUnavailableError("Ollama の応答がタイムアウトしました。") from e
        except httpx.HTTPError as e:
            raise LLMUnavailableError(
                f"Ollama（{self.base_url}）に接続できません。Ollama が起動しているか確認してください。"
            ) from e
        if r.status_code == 404:
            raise LLMUnavailableError(
                f"モデル「{self.model}」が見つかりません。`ollama pull {self.model}` を実行してください。"
            )
        if r.status_code >= 400:
            raise LLMUnavailableError(f"Ollama がエラーを返しました（HTTP {r.status_code}）。")
        try:
            content = r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise LLMUnavailableError("Ollama の応答形式が想定と異なります。") from e
        text = _THINK_RE.sub("", content).strip()
        # 指示しても出力全体を ```markdown で囲むことがあるので外す
        m = _FENCE_RE.match(text)
        return m.group(1).strip() if m else text

    def health(self) -> dict[str, Any]:
        try:
            r = self._client.get("/api/tags", timeout=3.0)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return {"provider": self.name, "ok": False, "model": self.model,
                    "message": f"Ollama（{self.base_url}）に接続できません。"}
        available = any(m == self.model or m.split(":")[0] == self.model for m in models)
        return {
            "provider": self.name,
            "ok": available,
            "model": self.model,
            "models": models,
            "message": "接続中" if available
            else f"Ollama は起動していますが、モデル「{self.model}」がありません。",
        }
