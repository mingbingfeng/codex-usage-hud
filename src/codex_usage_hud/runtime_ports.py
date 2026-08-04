"""Delayed dependency factories for the renderer runtime composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    """Factories invoked only when the matching runtime resource is needed."""

    clock: ClockPort
    context_factory: Callable[[Any], Any]
    renderer_factory: Callable[[int, float], Any]
    overlay_factory: Callable[[Any], Any]
    update_manager_factory: Callable[[], Any]
    bridge_factory: Callable[..., Any]
    snapshot_builder: Callable[..., Any]
    command_pump_factory: Callable[..., Any] | None = None
    file_event_source_factory: Callable[..., Any] | None = None
    active_work_pump_factory: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class CommandServices:
    """Restricted service view exposed to later command-owner extraction."""

    clock: ClockPort
    snapshot_builder: Callable[..., Any]


__all__ = ["ClockPort", "CommandServices", "RuntimeServices"]
