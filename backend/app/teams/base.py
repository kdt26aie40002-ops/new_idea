"""Teams 連携の抽象インターフェース。

エージェントはこのインターフェースだけを使う。現在はモック実装のみ。
本物の Teams 連携（Bot / Graph API）を作るときは、同じメソッドを持つ
アダプターを追加して差し替える。
"""

from __future__ import annotations

from typing import Any, Protocol


class TeamsAdapter(Protocol):
    name: str

    def list_users(self) -> list[dict[str, Any]]: ...

    def list_conversations(self) -> list[dict[str, Any]]: ...

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None: ...

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]: ...

    def send_message(self, conversation_id: str, sender_id: str, text: str) -> dict[str, Any]:
        """メッセージ送信。外部に影響する操作なので、呼び出せるのは承認済み実行器だけ。"""
        ...

    def unanswered_questions(self, teacher_id: str) -> list[dict[str, Any]]: ...
