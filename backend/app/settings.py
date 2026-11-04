"""ユーザー設定（SQLite に保存し、再起動後も保持）。

モデル名などはコードに固定せず、ここから読む。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import Database


class AppSettings(BaseModel):
    # 'demo' はテンプレートで応答するデモ用 LLM。'ollama' は本物のローカル LLM。
    # Ollama が落ちていても demo に自動で切り替えることはしない。
    llm_provider: Literal["demo", "ollama"] = "demo"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_sec: float = Field(default=120.0, ge=5, le=600)
    teacher_id: str = "t-sato"
    memory_top_k: int = Field(default=4, ge=1, le=20)


class SettingsStore:
    KEY = "app"

    def __init__(self, db: Database):
        self.db = db

    def get(self) -> AppSettings:
        row = self.db.one("SELECT value FROM settings WHERE key = ?", (self.KEY,))
        if row is None:
            return AppSettings()
        return AppSettings.model_validate(json.loads(row["value"]))

    def update(self, changes: dict[str, Any]) -> AppSettings:
        unknown = set(changes) - set(AppSettings.model_fields)
        if unknown:
            raise ValueError(f"未知の設定項目: {sorted(unknown)}")
        validated = AppSettings.model_validate({**self.get().model_dump(), **changes})
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self.KEY, validated.model_dump_json()),
            )
        return validated
