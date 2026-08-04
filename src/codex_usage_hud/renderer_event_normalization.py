"""Pure normalization of renderer runtime wake signals into typed events."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .core.runtime_events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class NormalizedEventBatch:
    """Ordered runtime events plus the activity wake reason they imply."""

    events: tuple[object, ...]
    activity_wake_reason: str = ""


def event_bus_timestamp(
    event_bus: object | None,
    *,
    fallback_clock: Callable[[], float],
) -> float:
    """Read the event-bus clock, falling back to the sampled wall clock."""
    clock = getattr(event_bus, "clock", None)
    try:
        return float(clock() if callable(clock) else fallback_clock())
    except Exception:
        return float(fallback_clock())


def normalize_runtime_events(
    events: Iterable[object],
    *,
    file_change_reasons: set[str],
    file_change_paths: set[Path],
    session_map_changed: bool,
    active_session_wake: bool,
    current_session: str | None,
    timestamp: float,
    path_key: Callable[[Path | None], str],
    existing_activity_wake_reason: str = "",
) -> NormalizedEventBatch:
    """Turn sampled wake signals into an ordered, deduplicated event batch."""
    normalized = list(events)
    event_types = {
        str(getattr(event, "type", "") or "") for event in normalized
    }
    paths_payload = sorted(path_key(path) for path in file_change_paths)
    activity_reason = str(existing_activity_wake_reason or "")

    def append_event(
        event_type: str,
        *,
        source: str,
        session: str | None,
        context: Mapping[str, object],
    ) -> None:
        normalized.append(
            RuntimeEvent(
                type=event_type,
                source=source,
                timestamp=float(timestamp),
                session=session,
                context=dict(context),
            )
        )

    if session_map_changed:
        append_event(
            "active_session_changed",
            source="session_map",
            session=current_session,
            context={
                "reason": "exact_renderer_mapping_available",
                "paths": paths_payload,
            },
        )
        activity_reason = "session-map"
    if (
        file_change_reasons.intersection({"session", "sessions-root"})
        and "session_file_changed" not in event_types
    ):
        session = (
            path_key(sorted(file_change_paths, key=path_key)[0])
            if file_change_paths
            else current_session
        )
        append_event(
            "session_file_changed",
            source="file_watcher",
            session=session,
            context={
                "reasons": sorted(file_change_reasons),
                "paths": paths_payload,
            },
        )
        if not activity_reason:
            activity_reason = "session-file"
    if (
        "settings" in file_change_reasons
        and "settings_changed" not in event_types
    ):
        append_event(
            "settings_changed",
            source="file_watcher",
            session=current_session,
            context={
                "reasons": sorted(file_change_reasons),
                "paths": paths_payload,
            },
        )
    if active_session_wake and "active_session_changed" not in event_types:
        append_event(
            "active_session_changed",
            source="renderer_loop",
            session=current_session,
            context={"reason": "active_session_wakeup"},
        )
    return NormalizedEventBatch(
        events=tuple(normalized),
        activity_wake_reason=activity_reason,
    )


__all__ = [
    "NormalizedEventBatch",
    "event_bus_timestamp",
    "normalize_runtime_events",
]
