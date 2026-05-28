"""Base interfaces for Codex platform integration."""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path


class BasePlatform(ABC):
    """Abstract interface for platform-specific Codex behavior."""

    @abstractmethod
    def get_codex_data_dir(self) -> Path:
        """Return the standard Codex data root for the current platform."""

    @abstractmethod
    def detect_active_session(self, sessions_root: Path) -> Path | None:
        """Return the newest JSONL session file under the given sessions root."""

    def build_active_title_command(self, poll_ms: int) -> list[str] | None:
        """Return a best-effort command that streams active Codex conversation titles."""
        del poll_ms
        return None

    def supports_active_title_polling(self) -> bool:
        """Return whether this platform can poll the Codex title in this process."""
        return False

    def get_active_conversation_title(self) -> str | None:
        """Return the currently selected Codex conversation title, if available."""
        return None

    @staticmethod
    def _detect_latest_jsonl_by_mtime(sessions_root: Path) -> Path | None:
        """Find the most recently modified JSONL file under ``sessions_root``."""
        if not sessions_root.exists():
            return None

        candidates: list[tuple[float, Path]] = []
        for path in sessions_root.rglob("*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]


def get_current_platform() -> BasePlatform:
    """Return the platform adapter for the current operating system."""
    if sys.platform.startswith("win"):
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform == "darwin":
        from .macos import MacOSPlatform

        return MacOSPlatform()
    if sys.platform.startswith("linux"):
        from .linux import LinuxPlatform

        return LinuxPlatform()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
