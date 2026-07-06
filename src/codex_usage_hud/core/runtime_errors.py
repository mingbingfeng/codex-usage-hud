"""Runtime diagnostics shared by renderer-mode subsystems."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import time
from typing import Any

from .runtime_events import RuntimeEventBus


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_source(value: object) -> str:
    return _clean_text(value).replace(" ", "_") or "runtime"


def _normalize_code(source: str, code: object) -> str:
    text = _clean_text(code).replace(" ", "_") or "unknown"
    return text if "." in text else f"{source}.{text}"


def _normalize_severity(value: object) -> str:
    text = _clean_text(value).lower()
    return text if text in {"debug", "info", "warning", "error", "critical"} else "error"


@dataclass
class RuntimeErrorEvent:
    """One aggregated runtime error for diagnostics and DEBUG HUD display."""

    source: str
    severity: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    first_seen_at: float = 0.0
    last_seen_at: float = 0.0
    count: int = 1

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "context": dict(self.context),
            "firstSeenAt": float(self.first_seen_at),
            "lastSeenAt": float(self.last_seen_at),
            "count": int(self.count),
        }


class RuntimeErrorRegistry:
    """Aggregate repeated runtime errors by source/code."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        event_bus: RuntimeEventBus | None = None,
        diagnostic_callback: Callable[[str, RuntimeErrorEvent], None] | None = None,
    ) -> None:
        self.clock = clock or time.time
        self.event_bus = event_bus
        self.diagnostic_callback = diagnostic_callback
        self._events: dict[tuple[str, str], RuntimeErrorEvent] = {}

    def record(
        self,
        *,
        source: str,
        code: str,
        message: str,
        severity: str = "error",
        context: Mapping[str, Any] | None = None,
    ) -> RuntimeErrorEvent:
        normalized_source = _normalize_source(source)
        normalized_code = _normalize_code(normalized_source, code)
        normalized_severity = _normalize_severity(severity)
        now = float(self.clock())
        key = (normalized_source, normalized_code)
        event = self._events.get(key)
        if event is None:
            event = RuntimeErrorEvent(
                source=normalized_source,
                severity=normalized_severity,
                code=normalized_code,
                message=_clean_text(message) or normalized_code,
                context=dict(context or {}),
                first_seen_at=now,
                last_seen_at=now,
                count=1,
            )
            self._events[key] = event
            self._publish_recorded(event, now)
            return event
        event.severity = normalized_severity
        event.message = _clean_text(message) or event.message
        event.context = dict(context or {})
        event.last_seen_at = now
        event.count += 1
        self._publish_recorded(event, now)
        return event

    def resolve(self, *, source: str, code: str) -> None:
        normalized_source = _normalize_source(source)
        normalized_code = _normalize_code(normalized_source, code)
        event = self._events.pop((normalized_source, normalized_code), None)
        if event is not None:
            self._publish_resolved(event)

    def clear(self) -> None:
        self._events.clear()

    def to_payload(self) -> list[dict[str, object]]:
        events = sorted(
            self._events.values(),
            key=lambda event: (event.severity != "critical", -event.last_seen_at, event.code),
        )
        return [event.to_payload() for event in events]

    def _publish_recorded(self, event: RuntimeErrorEvent, timestamp: float) -> None:
        self._append_diagnostic("recorded", event)
        if self.event_bus is None:
            return
        error_payload = event.to_payload()
        self.event_bus.publish(
            "runtime_error",
            source=event.source,
            timestamp=timestamp,
            session=_session_from_error_context(event.context),
            context={"action": "recorded", "error": error_payload},
            error=error_payload,
        )

    def _publish_resolved(self, event: RuntimeErrorEvent) -> None:
        self._append_diagnostic("resolved", event)
        if self.event_bus is None:
            return
        timestamp = float(self.clock())
        error_payload = event.to_payload()
        self.event_bus.publish(
            "runtime_error",
            source=event.source,
            timestamp=timestamp,
            session=_session_from_error_context(event.context),
            context={
                "action": "resolved",
                "code": event.code,
                "error": error_payload,
            },
            error=error_payload,
        )

    def _append_diagnostic(self, action: str, event: RuntimeErrorEvent) -> None:
        if self.diagnostic_callback is None:
            return
        try:
            self.diagnostic_callback(action, event)
        except Exception:
            return


def _session_from_error_context(context: Mapping[str, Any]) -> str | None:
    for key in ("sessionPath", "session", "sessionId", "threadId"):
        value = context.get(key)
        text = _clean_text(value)
        if text:
            return text
    return None


__all__ = ["RuntimeErrorEvent", "RuntimeErrorRegistry"]
