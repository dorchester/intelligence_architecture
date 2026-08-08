"""Logging configuration for the Intelligence Engine.

Operator view: high-level run status.
Engineer view: tool calls, model calls, timing, errors.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """JSON-structured log format for engineer observability."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "run_id"):
            log_entry["run_id"] = record.run_id
        if hasattr(record, "tool"):
            log_entry["tool"] = record.tool
        if hasattr(record, "turn"):
            log_entry["turn"] = record.turn
        if hasattr(record, "stop_reason"):
            log_entry["stop_reason"] = record.stop_reason
        return json.dumps(log_entry)


class OperatorFormatter(logging.Formatter):
    """Human-readable format for operator console output."""

    def format(self, record: logging.LogRecord) -> str:
        prefix = ""
        if hasattr(record, "run_id"):
            prefix = f"[{record.run_id[:8]}] "
        return f"{prefix}{record.getMessage()}"


def configure_logging(
    level: str = "INFO",
    structured: bool = False,
    log_file: str | None = None,
) -> None:
    """Configure logging for the Intelligence Engine."""
    root = logging.getLogger("intelligence_engine")
    root.setLevel(getattr(logging, level.upper()))

    # Console handler (operator view)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(OperatorFormatter())
    root.addHandler(console)

    # File handler (engineer view) — structured JSON
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(StructuredFormatter())
        root.addHandler(file_handler)

    if structured:
        console.setFormatter(StructuredFormatter())
