from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.core.session_cleanup import SessionCleanupError
from codex_usage_hud.session_cleanup_runtime import SessionCleanupWorker


def _operation(request_id: str, action: str, state: str, **extra: object) -> dict[str, object]:
    return {
        "revision": "revision-1",
        "operation": {
            "requestId": request_id,
            "action": action,
            "state": state,
            **extra,
        },
    }


def test_session_cleanup_worker_refreshes_usage_after_verified_delete() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.execute.side_effect = lambda *_args, request_id: _operation(
        request_id, "sessionCleanupExecute", "complete", deletedCount=1
    )
    refreshed: list[str] = []
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda _context, request_id: refreshed.append(request_id),
    )
    try:
        result = worker.enqueue(
            {
                "action": "sessionCleanupExecute",
                "requestId": "request-1",
                "itemIds": ["session-1"],
                "inventoryRevision": "inventory-1",
                "confirmationToken": "confirm-1",
            }
        )
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while not refreshed:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert refreshed == ["request-1"]
        assert context.session_cleanup_payload["operation"]["state"] == "complete"
    finally:
        assert worker.close()


def test_session_cleanup_worker_publishes_matching_terminal_state_after_refresh() -> None:
    events: list[str] = []
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )

    def execute(*_args: object, request_id: str) -> dict[str, object]:
        events.append("execute")
        return _operation(
            request_id, "execute", "completed", deletedCount=1
        )

    manager.execute.side_effect = execute
    event_bus = RuntimeEventBus()
    event_bus.subscribe(
        lambda event: events.append(
            f"published:{event.context.get('requestId')}:{event.context.get('state')}"
        )
        if event.type == "session_cleanup_changed"
        else None
    )
    context = SimpleNamespace(session_cleanup_payload={}, runtime_events=event_bus)
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda _context, request_id: events.append(f"refresh:{request_id}"),
    )
    try:
        worker.enqueue(
            {
                "action": "sessionCleanupExecute",
                "requestId": "request-2",
                "itemIds": ["session-1"],
                "inventoryRevision": "inventory-1",
                "confirmationToken": "confirm-1",
            }
        )
        deadline = time.monotonic() + 2
        while "published:request-2:completed" not in events:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert events.index("execute") < events.index("refresh:request-2")
        assert events.index("refresh:request-2") < events.index(
            "published:request-2:completed"
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_rejects_unknown_action_and_closed_enqueue() -> None:
    manager = MagicMock()
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    with pytest.raises(SessionCleanupError, match="unsupported"):
        worker.enqueue({"action": "unknown"})
    assert worker.close()
    with pytest.raises(SessionCleanupError, match="closed"):
        worker.enqueue({"action": "sessionCleanupScan"})
