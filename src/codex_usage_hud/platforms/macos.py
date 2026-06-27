"""macOS-specific Codex platform helpers."""

from __future__ import annotations

from pathlib import Path

from .base import BasePlatform
from .cdp_probe import CodexCdpProbe


class MacOSPlatform(BasePlatform):
    """Codex platform implementation for macOS."""

    def __init__(self) -> None:
        self._last_observed_title = ""
        self._last_observed_session_id = ""
        self._cdp_probe: CodexCdpProbe | None = None
        try:
            self._cdp_probe = CodexCdpProbe()
        except Exception:
            self._cdp_probe = None

    def refresh_cdp_probe(self) -> None:
        try:
            self._cdp_probe = CodexCdpProbe()
        except Exception:
            self._cdp_probe = None

    def get_codex_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "Codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        return self._detect_latest_jsonl_by_mtime(sessions_root)

    def supports_active_title_polling(self) -> bool:
        return self._cdp_probe is not None

    def get_active_conversation_ref(self) -> tuple[str, str] | None:
        if self._cdp_probe is None:
            return None
        snapshot = self._cdp_probe.snapshot()
        if snapshot is None:
            return None
        session_id = snapshot.session_id.strip()
        title = snapshot.title.strip()
        if not session_id and not title:
            return None
        if title:
            self._last_observed_title = title
        if session_id:
            self._last_observed_session_id = session_id
        return session_id, title

    def get_active_app_error(self) -> str:
        if self._cdp_probe is None:
            return ""
        snapshot = self._cdp_probe.snapshot()
        if snapshot is None:
            return ""
        return str(getattr(snapshot, "app_error", "") or "").strip()

    def get_active_conversation_title(self) -> str | None:
        ref = self.get_active_conversation_ref()
        if ref is not None and ref[1]:
            return ref[1]
        return None
