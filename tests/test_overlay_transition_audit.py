import json
from pathlib import Path

from codex_usage_hud import overlay_ipc, overlay_transition_audit


def test_transition_audit_projects_v1_event_with_injected_timestamp(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "transitions.jsonl"
    old_items = [
        {
            "id": "thread-1",
            "sessionId": "thread-1",
            "title": "Thread One",
            "status": "working",
            "pendingAccounting": True,
            "updatedAt": "old",
        }
    ]
    new_items = [
        {
            "id": "thread-1",
            "sessionId": "thread-1",
            "title": "Thread One",
            "status": "recent",
            "pendingAccounting": False,
            "updatedAt": "new",
        }
    ]

    overlay_transition_audit.append_transition_audit(
        new_items,
        previous_items=old_items,
        close=False,
        state_revision=4,
        audit_path=audit_path,
        state_path=tmp_path / "state.json",
        producer_instance_id="producer-1",
        owner_pid=123,
        timestamp_factory=lambda: "2026-08-03T00:00:00+08:00",
    )

    event = overlay_ipc.parse_transition(
        json.loads(audit_path.read_text(encoding="utf-8"))
    )
    assert event["messageType"] == "overlay.transition"
    assert event["transition"] == "card_to_completed"
    assert event["time"] == "2026-08-03T00:00:00+08:00"
    assert event["stateRevision"] == 4
    assert event["producerInstanceId"] == "producer-1"
    assert event["ownerPid"] == 123
    assert event["oldStatus"] == "working"
    assert event["newStatus"] == "recent"
    assert event["oldPendingAccounting"] is True
    assert event["newPendingAccounting"] is False


def test_transition_audit_skips_initial_close_and_unchanged_payloads(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "transitions.jsonl"
    item = {"id": "thread-1", "status": "working"}

    for previous_items, close in ((None, False), ([item], True), ([item], False)):
        overlay_transition_audit.append_transition_audit(
            [item],
            previous_items=previous_items,
            close=close,
            state_revision=1,
            audit_path=audit_path,
            state_path=tmp_path / "state.json",
            producer_instance_id="producer-1",
            owner_pid=123,
            timestamp_factory=lambda: "unused",
        )

    assert not audit_path.exists()
