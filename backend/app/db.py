"""SQLite 永続化レイヤー。

単一ファイルの SQLite を使い、スキーマはここで一元管理する。
FastAPI の同期エンドポイントはスレッドプールで動くため、接続は 1 本を
RLock で保護して共有する（ローカル単一ユーザー前提の規模）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- モック Teams（本物の Teams 連携ができるまでの代替データ）
CREATE TABLE IF NOT EXISTS teams_users (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('teacher', 'student', 'staff'))
);
CREATE TABLE IF NOT EXISTS teams_conversations (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('chat', 'channel', 'meeting')),
    participants TEXT NOT NULL            -- JSON 配列（teams_users.id）
);
CREATE TABLE IF NOT EXISTS teams_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES teams_conversations(id),
    sender_id       TEXT NOT NULL REFERENCES teams_users(id),
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- 教員 ⇄ AI の会話
CREATE TABLE IF NOT EXISTS assistant_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    rating     INTEGER,                    -- 1=良い, -1=悪い, NULL=未評価
    created_at TEXT NOT NULL
);

-- 記憶
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL,
    embedding  TEXT NOT NULL,              -- JSON 配列
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 承認キュー
CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type  TEXT NOT NULL,
    payload      TEXT NOT NULL,            -- JSON
    payload_hash TEXT NOT NULL,
    summary      TEXT NOT NULL,
    prediction   TEXT NOT NULL,
    origin       TEXT NOT NULL,            -- どの処理から生まれたか
    status       TEXT NOT NULL CHECK (status IN
                   ('pending', 'approved', 'rejected', 'executed', 'blocked', 'failed')),
    original_text TEXT,                    -- AI が最初に作った本文（修正学習用）
    result       TEXT,
    created_at   TEXT NOT NULL,
    decided_at   TEXT,
    executed_at  TEXT
);

-- 禁止事項（教員のみ編集可。エージェントからの変更経路は存在しない）
CREATE TABLE IF NOT EXISTS prohibitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('keyword', 'recipient', 'action')),
    value      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- 自己改善
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('edit', 'rating')),
    target      TEXT NOT NULL,             -- 'approval:12' / 'assistant:34'
    original    TEXT NOT NULL,
    revised     TEXT,
    rating      INTEGER,
    comment     TEXT NOT NULL DEFAULT '',
    processed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_kind TEXT NOT NULL,
    content     TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    feedback_id INTEGER REFERENCES feedback(id),
    status      TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    memory_id   INTEGER,
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);

-- 操作履歴（本文などの機密情報は記録しない）
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    actor  TEXT NOT NULL CHECK (actor IN ('agent', 'teacher', 'system')),
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """トランザクション。例外時はロールバックする。"""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
