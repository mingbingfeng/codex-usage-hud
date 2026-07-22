"""Event-driven runtime for the local background-usage audit store."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import logging
from pathlib import Path
import threading
import time
from typing import Any

from .core.background_usage import BackgroundUsageScanner, BackgroundUsageStore
from .core.runtime_errors import RuntimeErrorRegistry
from .core.runtime_events import RuntimeEventBus
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec


_LOGGER = logging.getLogger("codex_usage_hud.background_usage")
_LOGGER.addHandler(logging.NullHandler())
BACKGROUND_USAGE_DATABASE_FILENAME = "background-usage.sqlite3"
BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS = 5.0


class BackgroundUsageRuntime:
    """Coalesce Codex SQLite events into one non-blocking audit worker."""

    def __init__(
        self,
        *,
        logs_path: str | Path,
        state_path: str | Path,
        database_path: str | Path,
        provider: str,
        price_table: Mapping[str, Mapping[str, Any]],
        app_process_ids: Iterable[int] = (),
        event_bus: RuntimeEventBus | None = None,
        runtime_errors: RuntimeErrorRegistry | None = None,
        watcher_factory: Callable[..., FileChangeWatcher] = FileChangeWatcher,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.logs_path = Path(logs_path)
        self.state_path = Path(state_path)
        self.store = BackgroundUsageStore(database_path)
        self.event_bus = event_bus
        self.runtime_errors = runtime_errors
        self._clock = clock or time.time
        self._scanner = BackgroundUsageScanner(
            logs_path=self.logs_path,
            state_path=self.state_path,
            store=self.store,
            provider=provider,
            price_table=price_table,
            app_process_ids=app_process_ids,
            now=self._clock,
        )
        self._scanner_lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._idle = threading.Event()
        self._watcher = watcher_factory(
            self._on_source_changed,
            fallback_poll_seconds=BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS,
        )
        self._worker = threading.Thread(
            target=self._run,
            name="codex-hud-background-usage",
            daemon=True,
        )
        self._started = False
        self._diagnostic_codes: set[str] = set()

    def start(self) -> "BackgroundUsageRuntime":
        if self._started:
            return self
        self._started = True
        self._worker.start()
        self._watcher.update(
            [
                FileWatchSpec.file(self.logs_path, "background-usage-log"),
                FileWatchSpec.file(self.state_path, "background-usage-state"),
            ]
        )
        self.request_scan()
        return self

    def request_scan(self) -> None:
        if self._closed.is_set():
            return
        self._idle.clear()
        self._wake.set()

    def reconfigure(
        self,
        *,
        provider: str,
        price_table: Mapping[str, Mapping[str, Any]],
        app_process_ids: Iterable[int] = (),
    ) -> None:
        with self._scanner_lock:
            self._scanner.reconfigure(
                provider=provider,
                price_table=price_table,
                app_process_ids=app_process_ids,
            )
        self.request_scan()

    def confirm(self, event_id: object) -> bool:
        changed = self.store.confirm(event_id)
        if changed:
            self._publish_changed(reason="confirmed", event_id=str(event_id or ""))
        return changed

    def query(self, **filters: object) -> dict[str, object]:
        allowed = {
            "range_key": str(filters.get("range_key") or "today"),
            "feature": str(filters.get("feature") or ""),
            "model": str(filters.get("model") or ""),
            "event_id": str(filters.get("event_id") or ""),
        }
        return self.store.query(**allowed)

    def detail(self, event_id: object) -> dict[str, object] | None:
        return self.store.detail(event_id)

    def pending_today(self) -> list[dict[str, object]]:
        return self.store.pending_today()

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        return self._idle.wait(max(0.0, float(timeout)))

    @property
    def watcher_event_driven(self) -> bool:
        return bool(getattr(self._watcher, "event_driven", False))

    @property
    def watcher_polling_cause(self) -> str:
        return str(getattr(self._watcher, "polling_cause", "") or "")

    @property
    def worker_alive(self) -> bool:
        return self._worker.is_alive()

    def close(self, timeout: float = 2.0) -> bool:
        if self._closed.is_set():
            return not self._worker.is_alive()
        self._closed.set()
        self._wake.set()
        try:
            self._watcher.close()
        finally:
            if self._worker.is_alive():
                self._worker.join(timeout=max(0.0, float(timeout)))
        return not self._worker.is_alive()

    def _on_source_changed(self, reasons: set[str], paths: set[Path]) -> None:
        del paths
        _LOGGER.debug("background_usage_source_changed reasons=%s", sorted(reasons))
        self.request_scan()

    def _run(self) -> None:
        pending_deadline: float | None = None
        while not self._closed.is_set():
            timeout: float | None = None
            if pending_deadline is not None:
                timeout = max(0.0, pending_deadline - float(self._clock()))
            awakened = self._wake.wait(timeout)
            if self._closed.is_set():
                break
            if awakened:
                self._wake.clear()
            elif pending_deadline is None:
                continue
            try:
                with self._scanner_lock:
                    result = self._scanner.scan()
                pending_deadline = result.pending_deadline
                self._sync_diagnostics(result.diagnostics)
                if result.content_changed:
                    self._publish_changed(reason="scan")
            except Exception as exc:
                pending_deadline = None
                self._record_runtime_error(
                    "worker_failed",
                    "Background usage scan failed without affecting the renderer HUD.",
                    context={"errorType": type(exc).__name__},
                )
                _LOGGER.exception("background_usage_scan_failed")
            finally:
                self._idle.set()

    def _publish_changed(self, *, reason: str, event_id: str = "") -> None:
        publish = getattr(self.event_bus, "publish", None)
        if not callable(publish):
            return
        publish(
            "background_usage_changed",
            source="background_usage",
            context={
                "reason": str(reason or ""),
                "eventId": str(event_id or ""),
                "revision": self.store.revision(),
            },
        )

    def _sync_diagnostics(self, diagnostics: tuple[str, ...]) -> None:
        next_codes = {str(value).split(":", 1)[0] for value in diagnostics if value}
        for code in sorted(next_codes):
            self._record_runtime_error(
                code,
                "Background usage source is temporarily unavailable.",
                severity="warning",
            )
        registry = self.runtime_errors
        if registry is not None:
            for code in sorted(self._diagnostic_codes - next_codes):
                registry.resolve(source="background_usage", code=code)
        self._diagnostic_codes = next_codes

    def _record_runtime_error(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
        context: Mapping[str, Any] | None = None,
    ) -> None:
        registry = self.runtime_errors
        if registry is None:
            return
        if registry.event_bus is None and self.event_bus is not None:
            registry.event_bus = self.event_bus
        registry.record(
            source="background_usage",
            code=code,
            message=message,
            severity=severity,
            context=dict(context or {}),
        )


__all__ = [
    "BACKGROUND_USAGE_DATABASE_FILENAME",
    "BACKGROUND_USAGE_WATCHER_FALLBACK_SECONDS",
    "BackgroundUsageRuntime",
]
