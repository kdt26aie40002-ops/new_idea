from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..audit import AuditLog
from ..llm import LLMClient
from ..memory import MemoryStore
from ..policy import ActionGateway, ProhibitionReader
from ..settings import AppSettings
from ..teams import TeamsAdapter


def resolve_teacher_name(settings: AppSettings, teams: TeamsAdapter) -> str:
    """設定の名前があればそれを、なければ Teams の表示名を使う。"""
    if settings.user_name:
        return settings.user_name
    for u in teams.list_users():
        if u["id"] == settings.teacher_id:
            return u["name"]
    return settings.teacher_id


@dataclass
class AgentContext:
    """エージェントに渡す道具一式。

    意図的に含めていないもの：ApprovalService（承認・実行）、ProhibitionStore（書き込み）、
    SettingsStore（書き込み）、ImprovementService.decide（改善案の承認）。
    エージェントからはこれらを呼ぶ手段がない。
    """

    llm: LLMClient
    teams: TeamsAdapter
    memory: MemoryStore
    gateway: ActionGateway
    prohibitions: ProhibitionReader
    settings: AppSettings
    audit: AuditLog

    def teacher_name(self) -> str:
        return resolve_teacher_name(self.settings, self.teams)

    def recall(self, query: str) -> dict[str, Any]:
        """プロンプト用に記憶を集める。basic/preference/judgment は常に、knowledge は検索で。"""
        k = self.settings.memory_top_k
        # episode（AI 自身の作業記録）は回答の根拠にすると雑音になるので含めない
        knowledge = self.memory.search(query, k=k, kinds=("knowledge",))
        return {
            "basic": [m["content"] for m in self.memory.list("basic")[:5]],
            "preference": [m["content"] for m in self.memory.list("preference")[:10]],
            "judgment": [m["content"] for m in self.memory.search(query, k=k, kinds=("judgment",))],
            "knowledge": [m["content"] for m in knowledge],
            "_ids": [m["id"] for m in knowledge],
        }
