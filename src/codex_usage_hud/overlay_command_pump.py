"""Event-driven watcher for desktop-overlay command sidecars."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from threading import Event

from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec

WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS = 5.0
_LOGGER = logging.getLogger("codex_usage_hud.overlay_command_pump")


class WorkOverlayCommandPump:
    """Wake and delegate command draining without owning routing semantics."""

    def __init__(
        self,
        work_overlay: object,
        command_handler: Callable[[], int],
        *,
        command_event: Event | None = None,
        watcher_factory: Callable[..., FileChangeWatcher] = FileChangeWatcher,
    ) -> None:
        self._work_overlay = work_overlay
        self._command_handler = command_handler
        self._command_event = command_event
        self._watcher_factory = watcher_factory
        self._stop_event = Event()
        self._lock = threading.Lock()
        self._watcher: FileChangeWatcher | None = None

    def start(self) -> bool:
        with self._lock:
            if self._watcher is not None:
                return True
            self._stop_event.clear()
        try:
            self.drain_once()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_initial_drain_failed error=%s", exc)
        try:
            command_path = self._command_path()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_path_unavailable error=%s", exc)
            return True
        with self._lock:
            if self._watcher is not None:
                return True
            watcher = self._watcher_factory(
                self._on_command_file_changed,
                fallback_poll_seconds=WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS,
            )
            self._watcher = watcher
        try:
            watcher.update([FileWatchSpec.file(command_path, "work-overlay-command")])
            return True
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_watcher_start_failed error=%s", exc)
            with self._lock:
                if self._watcher is watcher:
                    self._watcher = None
            try:
                watcher.close()
            except Exception:
                pass
            return False

    def close(self, timeout_seconds: float = 0.5) -> None:
        del timeout_seconds
        self._stop_event.set()
        with self._lock:
            watcher = self._watcher
            self._watcher = None
        if watcher is not None:
            watcher.close()

    def drain_once(self) -> int:
        handled = int(self._command_handler())
        if handled and self._command_event is not None:
            self._command_event.set()
        return handled

    def _command_path(self) -> Path:
        command_path = getattr(self._work_overlay, "command_path", None)
        if command_path is not None:
            return Path(command_path)
        return Path(getattr(self._work_overlay, "_command_path"))

    def _on_command_file_changed(self, reasons: set[str], paths: set[Path]) -> None:
        del reasons, paths
        if self._stop_event.is_set():
            return
        try:
            self.drain_once()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_pump_failed error=%s", exc)


__all__ = ["WorkOverlayCommandPump"]
