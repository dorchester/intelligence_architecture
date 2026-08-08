"""Abstract storage interface for run artifacts.

Supports local filesystem and S3 backends with identical semantics,
ensuring run isolation by construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import PurePosixPath


class StorageBackend(ABC):
    """Base class for run-scoped storage backends."""

    @abstractmethod
    def write(self, run_id: str, client_id: str, category: str, filename: str, data: bytes) -> str:
        """Write data to storage. Returns the storage path/key."""
        ...

    @abstractmethod
    def read(self, run_id: str, client_id: str, category: str, filename: str) -> bytes:
        """Read data from storage."""
        ...

    @abstractmethod
    def exists(self, run_id: str, client_id: str, category: str, filename: str) -> bool:
        """Check if a file exists in storage."""
        ...

    @abstractmethod
    def list_files(self, run_id: str, client_id: str, category: str) -> list[str]:
        """List files in a category for a run."""
        ...

    def _build_key(self, client_id: str, run_id: str, category: str, filename: str) -> str:
        """Build a storage key enforcing client/run/category structure."""
        return str(PurePosixPath("runs") / client_id / run_id / category / filename)
