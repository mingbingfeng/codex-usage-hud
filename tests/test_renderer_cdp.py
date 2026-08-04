from __future__ import annotations

from codex_usage_hud.renderer_cdp import RendererTargetDiscovery, _RendererBinding
from codex_usage_hud.renderer_cdp import bindings, connection, target


def test_renderer_cdp_facade_exports_transport_owners() -> None:
    assert RendererTargetDiscovery is target.RendererTargetDiscovery
    assert _RendererBinding is bindings._RendererBinding


def test_target_discovery_caches_and_explicitly_stops_after_disconnect() -> None:
    calls = 0

    def list_targets(_port: int, _timeout: float) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"id": "target-1", "type": "page"}]

    discovery = RendererTargetDiscovery(
        port=9229,
        timeout_seconds=0.05,
        list_targets_fn=list_targets,
        pick_target_fn=lambda targets: targets[0],
    )

    assert discovery.target() == {"id": "target-1", "type": "page"}
    assert discovery.target() == {"id": "target-1", "type": "page"}
    assert calls == 1

    discovery.mark_disconnected("socket closed")
    try:
        discovery.target()
    except RuntimeError as exc:
        assert str(exc) == "CDP target discovery disconnected: socket closed"
    else:
        raise AssertionError("disconnected target discovery unexpectedly recovered")


def test_connection_helpers_remain_transport_only() -> None:
    assert connection.connect_websocket.__module__ == connection.__name__
    assert connection.receive_message.__module__ == connection.__name__
    assert connection.send_command.__module__ == connection.__name__
