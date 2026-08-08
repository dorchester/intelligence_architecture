"""Run context: the authoritative state object for a single engine run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from storage.base import StorageBackend


class Stage(Enum):
    INITIALIZED = "initialized"
    DATA_LOADED = "data_loaded"
    ANALYSIS_COMPLETE = "analysis_complete"
    NARRATIVE_COMPLETE = "narrative_complete"
    REPORT_GENERATED = "report_generated"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"


@dataclass
class RunContext:
    client_id: str
    client_name: str
    storage: StorageBackend
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    stage: Stage = Stage.INITIALIZED
    methodology_version: str = "0.1.0"
    code_version: str = "0.1.0"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_id: str = ""

    def write_artifact(self, category: str, filename: str, data: bytes) -> str:
        return self.storage.write(self.run_id, self.client_id, category, filename, data)

    def read_artifact(self, category: str, filename: str) -> bytes:
        return self.storage.read(self.run_id, self.client_id, category, filename)

    def artifact_exists(self, category: str, filename: str) -> bool:
        return self.storage.exists(self.run_id, self.client_id, category, filename)

    def list_artifacts(self, category: str) -> list[str]:
        return self.storage.list_files(self.run_id, self.client_id, category)

    def advance_to(self, stage: Stage) -> None:
        self.stage = stage
