"""Run context: the authoritative state object for a single engine run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Stage(Enum):
    INITIALIZED = "initialized"
    DATA_LOADED = "data_loaded"
    ANALYSIS_COMPLETE = "analysis_complete"
    NARRATIVE_COMPLETE = "narrative_complete"
    REPORT_GENERATED = "report_generated"


@dataclass
class RunContext:
    client_id: str
    client_name: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    stage: Stage = Stage.INITIALIZED
    methodology_version: str = "0.1.0"
    code_version: str = "0.1.0"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    base_path: Path = field(default_factory=lambda: Path("runs"))

    @property
    def run_path(self) -> Path:
        return self.base_path / self.run_id

    @property
    def input_path(self) -> Path:
        return self.run_path / "input"

    @property
    def working_path(self) -> Path:
        return self.run_path / "working"

    @property
    def output_path(self) -> Path:
        return self.run_path / "output"

    def ensure_directories(self) -> None:
        for path in (self.input_path, self.working_path, self.output_path):
            path.mkdir(parents=True, exist_ok=True)

    def advance_to(self, stage: Stage) -> None:
        self.stage = stage
