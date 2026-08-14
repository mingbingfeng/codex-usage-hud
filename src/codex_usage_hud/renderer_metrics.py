"""Low-overhead rolling metrics for the Renderer/CDP lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time


_COUNTER_DEFAULTS = {
    "cdp_commands": 0.0,
    "payload_updates": 0.0,
    "script_installs": 0.0,
    "payload_bytes": 0.0,
    "merged_refreshes": 0.0,
    "cooldown_seconds": 0.0,
    "binding_rebuilds": 0.0,
}


@dataclass(slots=True)
class RendererMetricsWindow:
    """Accumulate Renderer work in one-minute windows without a timer thread."""

    window_seconds: float = 60.0
    monotonic: Callable[[], float] = time.monotonic
    _started_at: float | None = None
    _cooldown_until: float = 0.0
    _cooldown_last_at: float | None = None
    _counters: dict[str, float] = field(default_factory=lambda: dict(_COUNTER_DEFAULTS))
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, name: str, amount: float = 1.0, *, now: float | None = None) -> dict[str, object] | None:
        with self._lock:
            current = self.monotonic() if now is None else float(now)
            closed = self._roll_locked(current)
            self._accrue_cooldown_locked(current)
            if name in self._counters:
                self._counters[name] += float(amount)
            return closed

    def start_cooldown(self, duration: float, *, now: float | None = None) -> dict[str, object] | None:
        with self._lock:
            current = self.monotonic() if now is None else float(now)
            closed = self._roll_locked(current)
            self._accrue_cooldown_locked(current)
            seconds = max(0.0, float(duration))
            if seconds:
                if self._cooldown_until <= current:
                    self._cooldown_last_at = current
                self._cooldown_until = max(self._cooldown_until, current + seconds)
            return closed

    def clear_cooldown(self, *, now: float | None = None) -> None:
        with self._lock:
            current = self.monotonic() if now is None else float(now)
            self._accrue_cooldown_locked(current)
            self._cooldown_until = 0.0
            self._cooldown_last_at = None

    def flush(self, *, now: float | None = None) -> dict[str, object] | None:
        with self._lock:
            current = self.monotonic() if now is None else float(now)
            if self._started_at is None or not any(self._counters.values()):
                return None
            self._accrue_cooldown_locked(current)
            summary = self._snapshot_locked(current)
            self._started_at = current
            self._counters = dict(_COUNTER_DEFAULTS)
            return summary

    def _roll_locked(self, now: float) -> dict[str, object] | None:
        if self._started_at is None:
            self._started_at = now
            return None
        if now - self._started_at < max(0.1, float(self.window_seconds)):
            return None
        self._accrue_cooldown_locked(now)
        summary = self._snapshot_locked(now)
        self._started_at = now
        self._counters = dict(_COUNTER_DEFAULTS)
        return summary

    def _accrue_cooldown_locked(self, now: float) -> None:
        if self._cooldown_until <= 0.0:
            return
        last = self._cooldown_last_at if self._cooldown_last_at is not None else now
        end = min(now, self._cooldown_until)
        if end > last:
            self._counters["cooldown_seconds"] += end - last
        self._cooldown_last_at = end
        if now >= self._cooldown_until:
            self._cooldown_until = 0.0
            self._cooldown_last_at = None

    def _snapshot_locked(self, now: float) -> dict[str, object]:
        return {
            "windowStartedAt": self._started_at,
            "windowEndedAt": now,
            "cdpCommands": int(self._counters["cdp_commands"]),
            "payloadUpdates": int(self._counters["payload_updates"]),
            "scriptInstalls": int(self._counters["script_installs"]),
            "payloadBytes": int(self._counters["payload_bytes"]),
            "mergedRefreshes": int(self._counters["merged_refreshes"]),
            "cooldownSeconds": round(self._counters["cooldown_seconds"], 3),
            "bindingRebuilds": int(self._counters["binding_rebuilds"]),
        }


__all__ = ["RendererMetricsWindow"]
