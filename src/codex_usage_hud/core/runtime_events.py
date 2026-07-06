"""Internal runtime event bus for renderer-mode coordination."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import threading
import time
from typing import Any


RuntimeEventCallback = Callable[["RuntimeEvent"], None]


@dataclass(frozen=True)
class RuntimeEvent:
    """One normalized runtime event emitted by HUD subsystems."""

    type: str
    source: str
    timestamp: float
    session: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "source": self.source,
            "timestamp": float(self.timestamp),
            "session": self.session,
            "context": dict(self.context),
            "error": dict(self.error) if self.error is not None else None,
        }


class RuntimeEventBus:
    """Small in-process pub/sub bus for renderer runtime events."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._subscribers: list[RuntimeEventCallback] = []
        self._events: list[RuntimeEvent] = []

    def subscribe(self, callback: RuntimeEventCallback) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    return

        return unsubscribe

    def publish(
        self,
        event_type: str,
        *,
        source: str,
        timestamp: float | None = None,
        session: str | None = None,
        context: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            type=str(event_type or "runtime"),
            source=str(source or "runtime"),
            timestamp=float(self.clock() if timestamp is None else timestamp),
            session=str(session) if session else None,
            context=dict(context or {}),
            error=dict(error) if error is not None else None,
        )
        with self._lock:
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(event)
        return event

    def drain(self) -> list[RuntimeEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events


__all__ = ["RuntimeEvent", "RuntimeEventBus"]
