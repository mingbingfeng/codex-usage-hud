from __future__ import annotations

from unittest.mock import MagicMock, patch

from codex_usage_hud.renderer_client import RendererHudClient
from codex_usage_hud.ui.work_overlay.qt_window import _state_has_persistent_sidecar


def test_unacknowledged_renderer_payload_reinstalls_immediately() -> None:
    client = RendererHudClient(port=9229, enabled=True)
    target = {
        "id": "target-1",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/page/1",
    }
    client._target_id = str(target["id"])
    client._websocket_url = str(target["webSocketDebuggerUrl"])
    client._script_identifier = "script-1"
    client._payload_domain_digests = {"settings": "digest-1"}
    client._payload_extras_digest = "extras-1"
    client._payload_digest_target_id = str(target["id"])
    client._page_target = MagicMock(return_value=target)  # type: ignore[method-assign]

    installs: list[tuple[str, str, bool]] = []

    def install(websocket_url: str, target_id: str, *, force: bool = False) -> None:
        installs.append((websocket_url, target_id, force))
        client._websocket_url = websocket_url
        client._target_id = target_id
        client._script_identifier = "script-2"

    client._install = install  # type: ignore[method-assign]
    client._send_update = MagicMock(side_effect=[False, True])  # type: ignore[method-assign]

    assert client.update_payload({"payloadDomains": {}})
    assert installs == [
        (str(target["webSocketDebuggerUrl"]), str(target["id"]), True)
    ]
    assert client._send_update.call_count == 2
    assert client.last_update_metrics["inPlaceRecovery"] is True


def test_verified_persistent_update_does_not_invalidate_renderer_cache() -> None:
    client = RendererHudClient(port=9229, enabled=True)
    target = {
        "id": "target-1",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/page/1",
    }
    client._target_id = str(target["id"])
    client._websocket_url = str(target["webSocketDebuggerUrl"])
    client._script_identifier = "script-1"
    binding = MagicMock()
    binding.send_command.return_value = {
        "result": {"result": {"value": {"ok": False, "applyMs": 0.0}}}
    }
    client._active_session_binding = binding
    verified = {
        "result": {"result": {"value": {"ok": True, "applyMs": 1.0}}}
    }

    with (
        patch.object(client, "_page_target", return_value=target),
        patch(
            "codex_usage_hud.renderer_client.send_cdp_command",
            return_value=verified,
        ),
        patch.object(client, "_clear_target_cache") as clear_target,
        patch.object(client, "_install") as install,
    ):
        assert client.update_payload(
            {"payloadDomains": {"settings": {"theme": "dark"}}}
        )

    clear_target.assert_not_called()
    install.assert_not_called()


def test_visible_rest_reminder_is_a_persistent_overlay_sidecar() -> None:
    assert _state_has_persistent_sidecar(None, None, {"phase": "prompt"})
    assert _state_has_persistent_sidecar({"persistent": True}, None, None)
    assert _state_has_persistent_sidecar(None, {"persistent": True}, None)
    assert not _state_has_persistent_sidecar(None, None, None)
