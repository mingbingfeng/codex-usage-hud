"""macOS-specific Codex platform helpers."""

from __future__ import annotations

from pathlib import Path

from .base import BasePlatform


class MacOSPlatform(BasePlatform):
    """Codex platform implementation for macOS."""

    def get_codex_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "Codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        return self._detect_latest_jsonl_by_mtime(sessions_root)
