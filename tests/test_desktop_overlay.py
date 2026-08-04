import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_usage_hud import overlay_ipc
from codex_usage_hud.core import WorkStatusItem
from codex_usage_hud.desktop_overlay import DesktopWorkOverlay


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


@pytest.mark.qt_ui
def test_real_helper_round_trip_artifacts(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    state_path = tmp_path / f"work-overlay-{os.getpid()}-round-trip.json"
    overlay = DesktopWorkOverlay(
        item_limit=2,
        runtime_dir=lambda: tmp_path,
        runtime_available=lambda: True,
        state_path=state_path,
    )
    overlay._transition_audit_path = tmp_path / "transitions.jsonl"
    started = datetime.now(timezone.utc)
    running = WorkStatusItem(
        id="session-1",
        session_id="session-1",
        title="Real helper round trip",
        target_title="Real helper round trip",
        status="running",
        status_label="running",
        detail="working",
        started_at=started,
        updated_at=started,
    )

    try:
        overlay.update([running])
        overlay.update([running])
        assert _wait_until(lambda: state_path.exists())
        assert _wait_until(
            lambda: overlay._process is not None and overlay._process.poll() is None
        )
        state = overlay_ipc.parse_state(
            json.loads(state_path.read_text(encoding="utf-8"))
        )
        assert state["schemaVersion"] == 1
        assert state["revision"] == 1
        assert state["items"][0]["id"] == "session-1"

        command = overlay_ipc.command_message(
            action="activateSession",
            sessionId="session-1",
            targetTitle="Real helper round trip",
            producerInstanceId="test-helper",
        )
        overlay.command_path.write_text(
            json.dumps(command, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        assert overlay.take_commands()[0]["requestId"] == command["requestId"]
        assert overlay.acknowledge_command(command, status="completed")
        ack = overlay_ipc.parse_ack(
            json.loads(
                overlay_ipc.ack_path(state_path).read_text(encoding="utf-8").strip()
            )
        )
        assert ack["requestId"] == command["requestId"]
        assert overlay.mark_switch_completed(command)

        completed = replace(
            running,
            status="recent",
            status_label="completed",
            pending_accounting=True,
            updated_at=datetime.now(timezone.utc),
        )
        overlay.update([completed])
        held = overlay_ipc.parse_state(
            json.loads(state_path.read_text(encoding="utf-8"))
        )
        assert held["items"][0]["current"] is True
        transitions = [
            overlay_ipc.parse_transition(json.loads(line))
            for line in overlay._transition_audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assert any(event["transition"] == "card_to_completed" for event in transitions)
    finally:
        process = overlay._process
        overlay.close()
        if process is not None:
            assert _wait_until(lambda: process.poll() is not None)
        assert not state_path.exists()
        assert not overlay.command_path.exists()
        assert not overlay_ipc.ack_path(state_path).exists()


def test_command_reader_keeps_partial_tail_and_deduplicates_request_id(
    tmp_path: Path,
) -> None:
    overlay = DesktopWorkOverlay(
        item_limit=2,
        runtime_dir=lambda: tmp_path,
        state_path=tmp_path / "work-overlay-1-reader.json",
    )
    command = overlay_ipc.command_message(
        action="activateSession",
        requestId="request-1",
        producerInstanceId="helper-1",
    )
    encoded = json.dumps(command, ensure_ascii=False)
    split = len(encoded) // 2
    overlay.command_path.write_text(encoded[:split], encoding="utf-8")

    assert overlay.take_commands() == []
    assert overlay._command_offset == 0

    with overlay.command_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded[split:] + "\n" + encoded + "\n")

    assert overlay.take_commands() == [command]
    assert overlay.take_commands() == []
