import json
from pathlib import Path
from unittest.mock import patch

from codex_usage_hud import overlay_ipc, overlay_state
from codex_usage_hud.desktop_overlay import DesktopWorkOverlay


def test_state_signature_is_canonical_and_ignores_close_only_fields() -> None:
    values = {
        "item_limit": 2,
        "command_path": Path("commands.jsonl"),
        "items": [{"id": "thread-1"}],
        "system_action": {"action": "restartCodex"},
        "rest_reminder": {"phase": "prompt"},
        "theme": {"variant": "dark"},
    }

    open_signature = json.loads(
        overlay_state.state_signature(**values, close=False)
    )
    close_signature = json.loads(
        overlay_state.state_signature(**values, close=True)
    )

    assert open_signature["systemAction"] == {"action": "restartCodex"}
    assert open_signature["restReminder"] == {"phase": "prompt"}
    assert close_signature["systemAction"] == {}
    assert close_signature["restReminder"] == {}
    assert close_signature["theme"] == {"variant": "dark"}


def test_build_state_message_preserves_v1_sidecar_and_flat_fields(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "work-overlay.json"
    command_path = tmp_path / "work-overlay-commands.jsonl"
    payload = overlay_state.build_state_message(
        owner_pid=123,
        item_limit=2,
        command_path=command_path,
        state_path=state_path,
        revision=4,
        producer_instance_id="producer-1",
        items=[{"id": "thread-1"}],
        system_action={"action": "restartCodex"},
        rest_reminder={"phase": "prompt"},
        theme={"variant": "dark"},
        updated_at=100.5,
        close=False,
    )

    state = overlay_ipc.parse_state(payload)
    assert state["messageType"] == "overlay.state"
    assert state["ownerPid"] == 123
    assert state["revision"] == 4
    assert state["commandPath"] == str(command_path)
    assert state["ackPath"] == str(overlay_ipc.ack_path(state_path))
    assert state["systemAction"] == {"action": "restartCodex"}
    assert state["restReminder"] == {"phase": "prompt"}
    assert state["theme"] == {"variant": "dark"}
    assert state["updatedAt"] == 100.5


def test_desktop_overlay_state_adapters_delegate_to_owner_and_writer() -> None:
    overlay = DesktopWorkOverlay(item_limit=2)
    signature = "stable-signature"
    state_payload = {"schemaVersion": 1, "items": []}

    with (
        patch(
            "codex_usage_hud.desktop_overlay.overlay_state.state_signature",
            return_value=signature,
        ) as state_signature,
        patch(
            "codex_usage_hud.desktop_overlay.overlay_state.build_state_message",
            return_value=state_payload,
        ) as build_state,
        patch("codex_usage_hud.desktop_overlay.write_json_object") as writer,
        patch.object(overlay, "_append_transition_audit"),
    ):
        assert overlay._state_signature([{"id": "thread-1"}], close=False) == signature
        overlay._write_state([{"id": "thread-1"}], close=False)

    assert state_signature.call_count == 2
    build_state.assert_called_once()
    writer.assert_called_once_with(overlay._state_path, state_payload)
