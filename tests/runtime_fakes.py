"""Deterministic fakes shared by owner-based runtime tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class FakeClock:
    now: float = 0.0
    waits: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        delay = max(0.0, float(seconds))
        self.waits.append(delay)
        self.now += delay

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


@dataclass
class FakeEventSource:
    events: deque[object] = field(default_factory=deque)
    closed: bool = False

    def publish(self, event: object) -> None:
        self.events.append(event)

    def next_event(self, timeout: float | None = None) -> object | None:
        del timeout
        return self.events.popleft() if self.events else None

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeRendererClient:
    payloads: list[dict[str, object]] = field(default_factory=list)
    closed: bool = False

    def update_payload(self, payload: dict[str, object]) -> bool:
        self.payloads.append(dict(payload))
        return True

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeBridge:
    started: bool = False
    closed: bool = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeOverlay:
    updates: list[tuple[object, dict[str, object]]] = field(default_factory=list)
    closed: bool = False

    def update(self, snapshot: object, **kwargs: object) -> None:
        self.updates.append((snapshot, dict(kwargs)))

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeStorage:
    values: dict[Path, dict[str, object]] = field(default_factory=dict)

    def read_json(self, path: Path) -> dict[str, object] | None:
        value = self.values.get(path)
        return dict(value) if value is not None else None

    def write_json(self, path: Path, value: dict[str, object]) -> None:
        self.values[path] = dict(value)


@dataclass
class FakeSnapshotBuilder:
    result: object = None
    requests: list[object] = field(default_factory=list)

    def build(self, request: object) -> object:
        self.requests.append(request)
        return self.result


@dataclass
class FakeCodexApp:
    activations: list[str] = field(default_factory=list)

    def activate(self, *, title: str = "") -> bool:
        self.activations.append(title)
        return True


@dataclass
class LifecycleRecorder:
    events: list[str] = field(default_factory=list)

    def record(self, event: str) -> None:
        self.events.append(event)

    def resource(self, name: str) -> Callable[[], None]:
        self.record(f"start:{name}")
        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                self.record(f"close:{name}")
                closed = True

        return close


@dataclass
class FactoryRecorder:
    values: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = field(
        default_factory=list
    )

    def factory(self, name: str) -> Callable[..., object]:
        def create(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args, dict(kwargs)))
            return self.values.get(name)

        return create
