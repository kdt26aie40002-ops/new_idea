from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Task = Literal["general", "summarize", "draft_reply", "minutes", "material", "improve", "briefing", "insights"]


@dataclass
class LLMRequest:
    """LLM への 1 回の依頼。

    system / messages が実際のプロンプト。task と hints は DemoLLM が
    テンプレート応答を組み立てるための構造化データで、Ollama は使わない。
    """

    system: str
    messages: list[dict[str, str]]
    task: Task
    hints: dict[str, Any] = field(default_factory=dict)


class LLMUnavailableError(RuntimeError):
    """LLM に接続できない・モデルがない等。画面にそのまま表示できる日本語メッセージを持つ。"""


class LLMClient(Protocol):
    name: str

    def complete(self, req: LLMRequest) -> str: ...

    def health(self) -> dict[str, Any]: ...
