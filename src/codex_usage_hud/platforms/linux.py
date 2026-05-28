"""Linux-specific Codex platform helpers."""

from __future__ import annotations

from pathlib import Path

from .base import BasePlatform


class LinuxPlatform(BasePlatform):
    """Codex platform implementation for Linux."""

    def get_codex_data_dir(self) -> Path:
        return Path.home() / ".config" / "Codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        return self._detect_latest_jsonl_by_mtime(sessions_root)
