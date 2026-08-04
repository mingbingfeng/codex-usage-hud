from codex_usage_hud.runtime_ports import RuntimeServices
from pathlib import Path

from runtime_fakes import (
    FakeBridge,
    FakeClock,
    FakeCodexApp,
    FakeEventSource,
    FakeOverlay,
    FakeRendererClient,
    FakeSnapshotBuilder,
    FakeStorage,
    FactoryRecorder,
    LifecycleRecorder,
)


def test_runtime_services_delay_every_resource_construction() -> None:
    clock = FakeClock()
    factories = FactoryRecorder(
        values={
            "context": "context",
            "renderer": "renderer",
            "overlay": "overlay",
            "updates": "updates",
            "bridge": "bridge",
            "snapshot": "snapshot",
            "command_pump": "command_pump",
            "file_events": "file_events",
            "active_work": "active_work",
        }
    )
    services = RuntimeServices(
        clock=clock,
        context_factory=factories.factory("context"),
        renderer_factory=factories.factory("renderer"),
        overlay_factory=factories.factory("overlay"),
        update_manager_factory=factories.factory("updates"),
        bridge_factory=factories.factory("bridge"),
        snapshot_builder=factories.factory("snapshot"),
        command_pump_factory=factories.factory("command_pump"),
        file_event_source_factory=factories.factory("file_events"),
        active_work_pump_factory=factories.factory("active_work"),
    )

    assert factories.calls == []
    assert services.context_factory("args") == "context"
    assert services.renderer_factory(9222, 0.5) == "renderer"
    assert services.overlay_factory("context") == "overlay"
    assert services.update_manager_factory() == "updates"
    assert services.bridge_factory("store", callback=True) == "bridge"
    assert services.snapshot_builder("context", refresh=True) == "snapshot"
    assert services.command_pump_factory("overlay", "controller") == "command_pump"
    assert services.file_event_source_factory("context", "wake") == "file_events"
    assert services.active_work_pump_factory("context", "wake") == "active_work"
    assert [call[0] for call in factories.calls] == [
        "context",
        "renderer",
        "overlay",
        "updates",
        "bridge",
        "snapshot",
        "command_pump",
        "file_events",
        "active_work",
    ]


def test_fake_clock_advances_only_when_explicitly_slept() -> None:
    clock = FakeClock(now=10.0)
    assert clock.monotonic() == 10.0
    clock.sleep(0.25)
    assert clock.monotonic() == 10.25


def test_runtime_fakes_are_deterministic_and_side_effect_free(tmp_path: Path) -> None:
    events = FakeEventSource()
    events.publish({"kind": "session"})
    assert events.next_event() == {"kind": "session"}
    assert events.next_event() is None
    events.close()
    assert events.closed

    renderer = FakeRendererClient()
    assert renderer.update_payload({"value": 1})
    renderer.close()
    assert renderer.payloads == [{"value": 1}]
    assert renderer.closed

    bridge = FakeBridge()
    bridge.start()
    bridge.close()
    assert bridge.started and bridge.closed

    overlay = FakeOverlay()
    overlay.update("snapshot", force=True)
    overlay.close()
    assert overlay.updates == [("snapshot", {"force": True})]
    assert overlay.closed

    storage = FakeStorage()
    path = tmp_path / "state.json"
    storage.write_json(path, {"value": 2})
    value = storage.read_json(path)
    assert value == {"value": 2}
    value["value"] = 3
    assert storage.read_json(path) == {"value": 2}

    snapshots = FakeSnapshotBuilder(result={"snapshot": True})
    assert snapshots.build({"refresh": "usage"}) == {"snapshot": True}
    assert snapshots.requests == [{"refresh": "usage"}]

    app = FakeCodexApp()
    assert app.activate(title="Codex")
    assert app.activations == ["Codex"]


def test_lifecycle_recorder_closes_each_resource_once() -> None:
    lifecycle = LifecycleRecorder()
    close_renderer = lifecycle.resource("renderer")
    close_bridge = lifecycle.resource("bridge")
    close_bridge()
    close_bridge()
    close_renderer()
    assert lifecycle.events == [
        "start:renderer",
        "start:bridge",
        "close:bridge",
        "close:renderer",
    ]
