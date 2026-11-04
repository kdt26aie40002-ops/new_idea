"""サービスの組み立て（依存関係の配線を 1 か所にまとめる）。"""

from __future__ import annotations

import json
from pathlib import Path

from .agents import AgentContext, MainAgent
from .audit import AuditLog
from .db import Database
from .improvement import ImprovementService
from .llm import LLMClient, build_llm
from .memory import MemoryStore
from .policy import ActionGateway, ApprovalService, ProhibitionReader, ProhibitionStore
from .settings import SettingsStore
from .teams import MockTeamsAdapter
from .teams.mock import SEED_PATH


class Services:
    def __init__(self, db_path: Path | str, seed: bool = True, llm_override: LLMClient | None = None):
        self.db = Database(db_path)
        self.audit = AuditLog(self.db)
        self.settings = SettingsStore(self.db)
        self.memory = MemoryStore(self.db)
        self.teams = MockTeamsAdapter(self.db)
        self.prohibitions = ProhibitionStore(self.db)
        self.improvement = ImprovementService(self.db, self.memory, self.audit)
        deps = (self.db, self.teams, self.prohibitions, self.settings, self.audit)
        self.gateway = ActionGateway(*deps)
        self.approvals = ApprovalService(
            *deps, on_edited=lambda row, revised: self.improvement.record_edit(
                f"approval:{row['id']}", row["original_text"], revised))
        self._llm_override = llm_override  # テスト用
        if seed:
            self.seed()

    def seed(self) -> None:
        if not self.teams.seed_if_empty():
            return
        data = json.loads(Path(SEED_PATH).read_text(encoding="utf-8"))
        for m in data.get("memories", []):
            self.memory.add(m["kind"], m["content"], source="初期データ（デモ）")
        for p in data.get("prohibitions", []):
            self.prohibitions.add(p["kind"], p["value"], p.get("note", ""))
        self.audit.record("system", "seeded")

    def llm(self) -> LLMClient:
        return self._llm_override or build_llm(self.settings.get())

    def agent(self) -> MainAgent:
        ctx = AgentContext(llm=self.llm(), teams=self.teams, memory=self.memory, gateway=self.gateway,
                           prohibitions=ProhibitionReader(self.prohibitions),
                           settings=self.settings.get(), audit=self.audit)
        return MainAgent(ctx)
