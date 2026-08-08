"""Local filesystem storage backend."""

from __future__ import annotations

from pathlib import Path

from storage.base import StorageBackend


class LocalStorage(StorageBackend):
    """File-system storage with run isolation via directory structure."""

    def __init__(self, base_dir: Path | str = "runs"):
        self.base_dir = Path(base_dir)

    def _resolve(self, run_id: str, client_id: str, category: str, filename: str) -> Path:
        return self.base_dir / client_id / run_id / category / filename

    def write(self, run_id: str, client_id: str, category: str, filename: str, data: bytes) -> str:
        path = self._resolve(run_id, client_id, category, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def read(self, run_id: str, client_id: str, category: str, filename: str) -> bytes:
        path = self._resolve(run_id, client_id, category, filename)
        return path.read_bytes()

    def exists(self, run_id: str, client_id: str, category: str, filename: str) -> bool:
        return self._resolve(run_id, client_id, category, filename).exists()

    def list_files(self, run_id: str, client_id: str, category: str) -> list[str]:
        dir_path = self.base_dir / client_id / run_id / category
        if not dir_path.exists():
            return []
        return [f.name for f in dir_path.iterdir() if f.is_file()]
