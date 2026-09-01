from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.core.session_cleanup import SessionCleanupError, SessionCleanupManager
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


def test_session_cleanup_worker_publishes_matching_terminal_state_before_refresh() -> None:
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
        while "refresh:request-2" not in events:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert events.index("execute") < events.index("refresh:request-2")
        assert events.index("published:request-2:completed") < events.index(
            "refresh:request-2"
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_deletes_provider_history_only_for_background_cleanup() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.delete_provider_history.return_value = _operation(
        "provider-request",
        "providerHistoryDelete",
        "completed",
        deletedCount=2,
        actualBytes=1234,
    )
    # 未配置 config 删除回调：config.toml 删除已由 dispatch 同步阶段完成，
    # worker 后台任务只负责历史清理，不应再调用 config 删除。
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    try:
        worker.enqueue(
            {
                "action": "providerDelete",
                "requestId": "provider-request",
                "provider": "muyuan",
                "deleteSessionHistory": True,
            }
        )
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        manager.delete_provider_history.assert_called_once_with(
            "muyuan", request_id="provider-request"
        )
        final_operation = manager.mark_operation.call_args_list[-1].kwargs
        assert final_operation["actualBytes"] == 1234
        assert final_operation["deletedCount"] == 2
    finally:
        assert worker.close()


def test_session_cleanup_worker_skips_provider_history_when_not_requested() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    try:
        worker.enqueue(
            {
                "action": "providerDelete",
                "requestId": "provider-request",
                "provider": "muyuan",
                "deleteSessionHistory": False,
            }
        )
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        manager.delete_provider_history.assert_not_called()
    finally:
        assert worker.close()


def test_session_cleanup_worker_accepts_renderer_provider_delete_alias() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    context = SimpleNamespace(
        app_provider="codex",
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )
    worker = SessionCleanupWorker(
        context,
        manager,
        on_deleted=lambda *_args: None,
    )
    command = {
        "action": "deleteProvider",
        "requestId": "provider-alias-1",
        "provider": "muyuan",
        "deleteSessionHistory": False,
    }
    try:
        result = worker.enqueue(command)
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert context.session_cleanup_payload["operation"]["action"] == "providerDelete"
    finally:
        assert worker.close()


def test_session_cleanup_worker_runs_indexed_search_without_delete_path() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: _operation(
        str(values["request_id"]), str(values["action"]), str(values["state"])
    )
    manager.search.side_effect = lambda *_args, **kwargs: _operation(
        str(kwargs["request_id"]), "search", "completed", matches=["session-1"]
    )
    context = SimpleNamespace(
        session_cleanup_payload={}, runtime_events=RuntimeEventBus()
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    try:
        result = worker.enqueue(
            {
                "action": "sessionCleanupSearch",
                "requestId": "search-request",
                "query": "active_session.py",
                "workdirId": "opaque-workdir",
            }
        )
        assert result["status"] == "accepted"
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        manager.search.assert_called_once_with(
            "active_session.py",
            workdir_id="opaque-workdir",
            request_id="search-request",
            include_sessions=False,
        )
    finally:
        assert worker.close()


def test_session_cleanup_worker_indexes_after_scan_and_searches_resident_index() -> None:
    session_id = "10000000-0000-4000-8000-000000000091"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sessions_root = root / "sessions"
        sessions_root.mkdir()
        rollout = sessions_root / "session.jsonl"
        rollout.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "cwd": str(root),
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "resident-worker-marker",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_db = root / "state.sqlite"
        connection = sqlite3.connect(state_db)
        try:
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, title TEXT, "
                "cwd TEXT, archived INTEGER, updated_at_ms INTEGER)"
            )
            connection.execute(
                "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, str(rollout), "Resident worker", str(root), 0, 1_700_000_000_000),
            )
            connection.commit()
        finally:
            connection.close()
        session_index = root / "session_index.jsonl"
        session_index.write_text("", encoding="utf-8")
        manager = SessionCleanupManager(
            state_db_path=state_db,
            sessions_root=sessions_root,
            session_index_path=session_index,
            search_index_path=root / "search.sqlite",
        )
        context = SimpleNamespace(
            state_db_path=state_db,
            session_cleanup_payload={},
            runtime_events=RuntimeEventBus(),
        )
        worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
        try:
            accepted = worker.enqueue(
                {"action": "sessionCleanupScan", "requestId": "resident-scan"}
            )
            assert accepted["status"] == "accepted"
            deadline = time.monotonic() + 3
            while context.session_cleanup_payload.get("operation", {}).get("state") != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            while context.session_cleanup_payload.get("search", {}).get("indexState") != "ready":
                assert time.monotonic() < deadline
                time.sleep(0.01)

            result = manager.search("resident-worker-marker", request_id="resident-search")
            assert result["search"]["matches"]
            assert manager._search_index.memory_loaded is True

            row_id = result["search"]["matches"][0]
            rollout.write_text(
                rollout.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "resident-event-marker",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 3
            while (
                context.session_cleanup_payload.get("search", {}).get("indexState") != "ready"
                or row_id
                not in context.session_cleanup_payload.get("search", {}).get("matches", [])
            ):
                assert time.monotonic() < deadline
                time.sleep(0.01)

            worker.enqueue(
                {
                    "action": "sessionCleanupSearch",
                    "requestId": "resident-search-command",
                    "query": "resident-event-marker",
                }
            )
            while context.session_cleanup_payload.get("operation", {}).get("requestId") != "resident-search-command":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert "sessions" in context.session_cleanup_payload
            assert "sessions" not in context.session_cleanup_delta
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
