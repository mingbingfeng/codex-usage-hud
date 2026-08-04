from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

from codex_usage_hud.active_work import RendererActiveWorkPump


def test_active_work_pump_publishes_sequence_bound_result_and_wakes() -> None:
    wake = Event()
    expected = [SimpleNamespace(id="work-1")]
    pump = RendererActiveWorkPump(
        "context",
        wake,
        build_items=lambda context, snapshot, path: expected,
    )
    try:
        assert pump.request(SimpleNamespace(selection_seq=12), Path("session.jsonl"))
        assert wake.wait(1.0)
        assert pump.take_latest() == (12, expected)
        assert pump.take_latest() is None
    finally:
        pump.close()


def test_active_work_pump_coalesces_to_latest_pending_request() -> None:
    first_started = Event()
    release_first = Event()
    calls: list[int] = []

    def build_items(context: object, snapshot: object, path: Path | None) -> list[int]:
        sequence = int(getattr(snapshot, "selection_seq"))
        calls.append(sequence)
        if sequence == 1:
            first_started.set()
            assert release_first.wait(1.0)
        return [sequence]

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    try:
        assert pump.request(SimpleNamespace(selection_seq=1), None)
        assert first_started.wait(1.0)
        assert pump.request(SimpleNamespace(selection_seq=2), None)
        assert pump.request(SimpleNamespace(selection_seq=3), None)
        release_first.set()
        assert wake.wait(1.0)
        assert pump.take_latest() == (3, [3])
        assert calls == [1, 3]
    finally:
        release_first.set()
        pump.close()


def test_active_work_pump_uses_a_shallow_snapshot_copy() -> None:
    release = Event()
    observed: list[str] = []

    def build_items(context: object, snapshot: object, path: Path | None) -> list[object]:
        assert release.wait(1.0)
        observed.append(str(getattr(snapshot, "title")))
        return []

    wake = Event()
    snapshot = SimpleNamespace(selection_seq=4, title="before")
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    try:
        assert pump.request(snapshot, None)
        snapshot.title = "after"
        release.set()
        assert wake.wait(1.0)
        assert observed == ["before"]
    finally:
        release.set()
        pump.close()


def test_active_work_pump_publishes_empty_result_after_builder_error() -> None:
    def fail(context: object, snapshot: object, path: Path | None) -> list[object]:
        raise RuntimeError("boom")

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=fail)
    try:
        assert pump.request(SimpleNamespace(selection_seq=7), None)
        assert wake.wait(1.0)
        assert pump.take_latest() == (7, [])
    finally:
        pump.close()


def test_active_work_pump_close_during_work_suppresses_publish() -> None:
    started = Event()
    release = Event()

    def build_items(context: object, snapshot: object, path: Path | None) -> list[int]:
        started.set()
        assert release.wait(1.0)
        return [1]

    wake = Event()
    pump = RendererActiveWorkPump("context", wake, build_items=build_items)
    assert pump.request(SimpleNamespace(selection_seq=9), None)
    assert started.wait(1.0)
    pump.close()
    release.set()
    pump.close()

    assert not wake.wait(0.05)
    assert pump.take_latest() is None
    assert not pump.request(SimpleNamespace(selection_seq=10), None)
