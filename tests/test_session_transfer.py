from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from codex_usage_hud.core.session_transfer import (
    CodexAppServerClient,
    SessionTransferError,
    _codex_environment,
)
from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.session_cleanup_runtime import (
    SessionCleanupWorker,
    _load_transfer_inherited_session_ids,
)


SOURCE_ID = "10000000-0000-4000-8000-000000000001"
TARGET_ID = "10000000-0000-4000-8000-000000000002"
SECOND_TARGET_ID = "10000000-0000-4000-8000-000000000003"


def test_app_server_fork_requests_interactive_target_provider_and_validates_response() -> None:
    client = CodexAppServerClient(executable="codex")
    client.request = MagicMock(
        return_value={
            "modelProvider": "routin",
            "thread": {"id": TARGET_ID, "modelProvider": "routin"},
        }
    )

    assert client.fork(SOURCE_ID, "ROUTIN", cwd="E:/project") == TARGET_ID
    client.request.assert_called_once_with(
        "thread/fork",
        {
            "threadId": SOURCE_ID,
            "modelProvider": "routin",
            "threadSource": "user",
            "ephemeral": False,
            "cwd": "E:/project",
        },
    )


def test_app_server_client_overrides_codex_home_for_desktop_store(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "E:/wrong-home")
    environment = _codex_environment("E:/AppData/Codex")
    assert environment["CODEX_HOME"] == "E:/AppData/Codex"


def test_load_transfer_inherited_session_ids_uses_state_db_fork_marker(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "state_5.sqlite"
    with sqlite3.connect(state_db) as connection:
        connection.execute(
            "CREATE TABLE threads ("
            "id TEXT, has_user_event INTEGER, thread_source TEXT"
            ")"
        )
        connection.executemany(
            "INSERT INTO threads(id, has_user_event, thread_source) VALUES (?, ?, ?)",
            [
                (TARGET_ID, 0, "user"),
                (SOURCE_ID, 1, "user"),
                ("10000000-0000-4000-8000-000000000003", 0, "subagent"),
            ],
        )

    assert _load_transfer_inherited_session_ids(state_db) == {TARGET_ID}


def test_app_server_fork_rejects_provider_mismatch() -> None:
    client = CodexAppServerClient(executable="codex")
    client.request = MagicMock(
        return_value={"thread": {"id": TARGET_ID, "modelProvider": "other"}}
    )

    with pytest.raises(SessionTransferError, match="目标 Provider 不匹配"):
        client.fork(SOURCE_ID, "routin")


def test_app_server_verifies_persistent_thread_before_source_deletion(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "target.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")
    client = CodexAppServerClient(executable="codex")
    client.request = MagicMock(
        side_effect=[
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                    "path": str(rollout),
                }
            },
            {
                "data": [
                    {
                        "id": TARGET_ID,
                        "modelProvider": "routin",
                        "ephemeral": False,
                    }
                ],
                "nextCursor": None,
            },
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                }
            },
        ]
    )

    assert client.verify_persistent_thread(TARGET_ID, "ROUTIN") is True
    assert client.request.call_args_list == [
        call(
            "thread/read",
            {"threadId": TARGET_ID, "includeTurns": True},
        ),
        call(
            "thread/list",
            {
                "modelProviders": ["routin"],
                "sourceKinds": [
                    "cli",
                    "vscode",
                    "exec",
                    "appServer",
                    "subAgent",
                    "subAgentReview",
                    "subAgentCompact",
                    "subAgentThreadSpawn",
                    "subAgentOther",
                    "unknown",
                ],
                "limit": 100,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "useStateDbOnly": False,
            },
        ),
        call(
            "thread/resume",
            {"threadId": TARGET_ID, "modelProvider": "routin"},
        ),
    ]


def test_app_server_verifies_batch_targets_with_one_provider_list_traversal(
    tmp_path: Path,
) -> None:
    first_rollout = tmp_path / "first-target.jsonl"
    second_rollout = tmp_path / "second-target.jsonl"
    first_rollout.write_text("{}\n", encoding="utf-8")
    second_rollout.write_text("{}\n", encoding="utf-8")
    client = CodexAppServerClient(
        executable="codex",
        target_visibility_retry_delays=(0.0,),
    )
    responses = iter(
        [
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                    "path": str(first_rollout),
                }
            },
            {
                "thread": {
                    "id": SECOND_TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                    "path": str(second_rollout),
                }
            },
            {
                "data": [
                    {"id": TARGET_ID, "modelProvider": "routin"},
                    {"id": SECOND_TARGET_ID, "modelProvider": "routin"},
                ],
                "nextCursor": None,
            },
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                }
            },
            {
                "thread": {
                    "id": SECOND_TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                }
            },
        ]
    )
    progress: list[tuple[str, str, bool, str]] = []

    def request(method: str, params: dict[str, object]) -> object:
        if method == "thread/resume" and params.get("threadId") == SECOND_TARGET_ID:
            assert progress == [(TARGET_ID, "verify", True, "")]
        return next(responses)

    client.request = request  # type: ignore[method-assign]

    assert client.verify_persistent_threads(
        ((TARGET_ID, "ROUTIN"), (SECOND_TARGET_ID, "ROUTIN")),
        progress_callback=lambda *values: progress.append(values),
    ) == {TARGET_ID: True, SECOND_TARGET_ID: True}
    assert progress == [
        (TARGET_ID, "verify", True, ""),
        (SECOND_TARGET_ID, "verify", True, ""),
    ]


def test_app_server_retries_until_target_is_listed_and_supports_pagination(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "target.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")
    calls: list[tuple[str, dict[str, object]]] = []
    responses = iter(
        [
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                    "path": str(rollout),
                }
            },
            {"data": []},
            {
                "thread": {
                    "id": TARGET_ID,
                    "modelProvider": "routin",
                    "ephemeral": False,
                    "path": str(rollout),
                }
            },
            {"data": [], "nextCursor": "page-2"},
            {"data": [{"id": TARGET_ID, "modelProvider": "routin"}]},
            {"thread": {"id": TARGET_ID, "modelProvider": "routin"}},
        ]
    )

    client = CodexAppServerClient(
        executable="codex",
        target_visibility_retry_delays=(0.0, 0.0),
    )

    def request(method: str, params: dict[str, object]) -> object:
        calls.append((method, dict(params)))
        return next(responses)

    client.request = request  # type: ignore[method-assign]

    assert client.verify_persistent_thread(TARGET_ID, "ROUTIN") is True
    assert [method for method, _params in calls] == [
        "thread/read",
        "thread/list",
        "thread/read",
        "thread/list",
        "thread/list",
        "thread/resume",
    ]
    assert calls[-2][1]["cursor"] == "page-2"


def test_worker_routes_session_transfer_to_app_server_and_configured_target() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: {
        "revision": "revision-1",
        "operation": {
            "requestId": str(values["request_id"]),
            "action": str(values["action"]),
            "state": str(values["state"]),
            **{
                key: value
                for key, value in values.items()
                if key not in {"request_id", "action", "state"}
            },
        },
    }
    transfer_result = {
        "revision": "revision-2",
        "operation": {
            "requestId": "transfer-1",
            "action": "sessionTransfer",
            "state": "completed",
            "copiedCount": 1,
            "migratedCount": 0,
            "results": [
                {
                    "targetSessionId": TARGET_ID,
                    "targetCreated": True,
                    "forked": True,
                }
            ],
        },
    }
    first_app_server = MagicMock()
    first_app_server.fork.return_value = TARGET_ID
    second_app_server = MagicMock()
    second_app_server.verify_persistent_threads.return_value = {TARGET_ID: True}
    first_client = MagicMock()
    first_client.__enter__.return_value = first_app_server
    second_client = MagicMock()
    second_client.__enter__.return_value = second_app_server
    client_factory = MagicMock(side_effect=[first_client, second_client])
    manager.materialize_target_rollouts.return_value = {TARGET_ID: True}

    def transfer(*_args, **kwargs):
        assert kwargs["fork"](SOURCE_ID, "routin", "E:/project") == TARGET_ID
        assert kwargs["materialize_batch"]([(TARGET_ID, SOURCE_ID)]) == {
            TARGET_ID: True
        }
        assert kwargs["verify_batch"]([(TARGET_ID, "routin")]) == {
            TARGET_ID: True
        }
        assert callable(kwargs["desktop_source_lifecycle"])
        return transfer_result

    manager.transfer.side_effect = transfer
    context = SimpleNamespace(
        app_provider="codex",
        sessions_root=Path("E:/AppData/Codex/sessions"),
        provider_registry=SimpleNamespace(
            entries={"routin": SimpleNamespace(from_provider_definition=True)},
        ),
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )

    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "codex_usage_hud.session_cleanup_runtime.CodexAppServerClient",
                client_factory,
            )
            result = worker.enqueue(
                {
                    "action": "sessionTransfer",
                    "requestId": "transfer-1",
                    "sourceProvider": "codex",
                    "targetProvider": "routin",
                    "mode": "copy",
                    "itemIds": ["opaque-id"],
                    "inventoryRevision": "revision-1",
                }
            )
            assert result["status"] == "accepted"
            deadline = time.monotonic() + 2
            while context.session_cleanup_payload["operation"]["state"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)

        call = manager.transfer.call_args
        assert call.args[:5] == (
            ["opaque-id"],
            "revision-1",
            "codex",
            "routin",
            "copy",
        )
        accepted_operation = manager.mark_operation.call_args_list[0].kwargs
        assert accepted_operation["sourceProvider"] == "codex"
        assert accepted_operation["targetProvider"] == "routin"
        assert accepted_operation["startedAt"] > 0
        assert accepted_operation["selectedIds"] == ["opaque-id"]
        assert accepted_operation["sessionCount"] == 1
        first_app_server.fork.assert_called_once_with(
            SOURCE_ID,
            "routin",
            cwd="E:/project",
        )
        manager.materialize_target_rollouts.assert_called_once_with(
            [(TARGET_ID, SOURCE_ID)],
            progress_callback=None,
        )
        second_app_server.verify_persistent_threads.assert_called_once_with(
            [(TARGET_ID, "routin")],
            progress_callback=None,
        )
        manager.materialize_target_rollout.assert_not_called()
        second_app_server.verify_persistent_thread.assert_not_called()
        first_client.__exit__.assert_called_once_with(None, None, None)
        second_client.__exit__.assert_called_once_with(None, None, None)
        assert client_factory.call_args_list == [
            call(codex_home=Path("E:/AppData/Codex")),
            call(codex_home=Path("E:/AppData/Codex")),
        ]
        assert TARGET_ID in context._work_overlay_transfer_inherited_session_ids
    finally:
        assert worker.close()


def test_worker_keeps_provider_pair_on_failed_session_transfer() -> None:
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: {
        "revision": "revision-1",
        "operation": {
            "requestId": str(values["request_id"]),
            "action": str(values["action"]),
            "state": str(values["state"]),
            **{
                key: value
                for key, value in values.items()
                if key not in {"request_id", "action", "state"}
            },
        },
    }
    context = SimpleNamespace(
        app_provider="codex",
        sessions_root=Path("E:/AppData/Codex/sessions"),
        provider_registry=SimpleNamespace(entries={}),
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    try:
        accepted = worker.enqueue(
            {
                "action": "sessionTransfer",
                "requestId": "transfer-failed-1",
                "sourceProvider": "codex",
                "targetProvider": "codex",
                "mode": "copy",
                "itemIds": ["opaque-id"],
                "inventoryRevision": "revision-1",
            }
        )
        assert accepted["status"] == "accepted"
        deadline = time.monotonic() + 2
        while context.session_cleanup_payload["operation"]["state"] != "failed":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        operation = context.session_cleanup_payload["operation"]
        assert operation["sourceProvider"] == "codex"
        assert operation["targetProvider"] == "codex"
        assert "不能相同" in operation["error"]
    finally:
        assert worker.close()


def test_worker_releases_fork_connection_before_materialization_and_verification() -> None:
    events: list[str] = []
    manager = MagicMock()
    manager.mark_operation.side_effect = lambda **values: {
        "revision": "revision-1",
        "operation": {
            "requestId": str(values["request_id"]),
            "action": str(values["action"]),
            "state": str(values["state"]),
            **{
                key: value
                for key, value in values.items()
                if key not in {"request_id", "action", "state"}
            },
        },
    }
    transfer_result = {
        "revision": "revision-2",
        "operation": {
            "requestId": "transfer-migrate-1",
            "action": "sessionTransfer",
            "state": "completed",
            "copiedCount": 1,
            "migratedCount": 1,
        },
    }
    first_app_server = MagicMock()
    first_app_server.fork.side_effect = lambda *_args, **_kwargs: (
        events.append("first-fork") or TARGET_ID
    )
    second_app_server = MagicMock()
    second_app_server.verify_persistent_threads.side_effect = lambda *_args, **_kwargs: (
        events.append("second-verify") or True
    )
    first_client = MagicMock()
    first_client.__enter__.side_effect = lambda: (
        events.append("first-enter") or first_app_server
    )
    first_client.__exit__.side_effect = lambda *_args: events.append("first-close")
    second_client = MagicMock()
    second_client.__enter__.side_effect = lambda: (
        events.append("second-enter") or second_app_server
    )
    second_client.__exit__.side_effect = lambda *_args: events.append("second-close")
    client_factory = MagicMock(side_effect=[first_client, second_client])
    manager.materialize_target_rollouts.side_effect = lambda *_args, **_kwargs: (
        events.append("materialize") or {TARGET_ID: True}
    )
    desktop_lifecycle = MagicMock()
    desktop_report = MagicMock()
    desktop_report.to_payload.return_value = {
        "threadId": SOURCE_ID,
        "archived": True,
        "deleted": True,
        "archiveNotification": True,
        "deleteNotification": True,
        "verified": True,
        "error": "",
    }
    desktop_lifecycle.archive_then_delete.side_effect = lambda *_args, **_kwargs: (
        events.append("desktop-lifecycle") or desktop_report
    )
    desktop_lifecycle.preflight.side_effect = lambda *_args, **_kwargs: (
        events.append("desktop-preflight")
        or {"verified": True, "threadIds": [SOURCE_ID], "error": ""}
    )

    def transfer(*_args, **kwargs):
        assert kwargs["fork"](SOURCE_ID, "routin", "E:/project") == TARGET_ID
        assert kwargs["materialize_batch"]([(TARGET_ID, SOURCE_ID)]) == {
            TARGET_ID: True
        }
        assert kwargs["verify_batch"]([(TARGET_ID, "routin")]) is True
        assert kwargs["desktop_source_preflight"]([SOURCE_ID], "E:/project") == {
            "verified": True,
            "threadIds": [SOURCE_ID],
            "error": "",
        }
        assert kwargs["desktop_source_lifecycle"](SOURCE_ID, "E:/project") == {
            "threadId": SOURCE_ID,
            "archived": True,
            "deleted": True,
            "archiveNotification": True,
            "deleteNotification": True,
            "verified": True,
            "error": "",
        }
        return transfer_result

    manager.transfer.side_effect = transfer
    context = SimpleNamespace(
        app_provider="codex",
        sessions_root=Path("E:/AppData/Codex/sessions"),
        provider_registry=SimpleNamespace(
            entries={"routin": SimpleNamespace(from_provider_definition=True)},
        ),
        session_cleanup_payload={},
        runtime_events=RuntimeEventBus(),
    )
    worker = SessionCleanupWorker(context, manager, on_deleted=lambda *_args: None)
    worker._desktop_thread_lifecycle = desktop_lifecycle
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "codex_usage_hud.session_cleanup_runtime.CodexAppServerClient",
                client_factory,
            )
            accepted = worker.enqueue(
                {
                    "action": "sessionTransfer",
                    "requestId": "transfer-migrate-1",
                    "sourceProvider": "codex",
                    "targetProvider": "routin",
                    "mode": "migrate",
                    "itemIds": ["opaque-id"],
                    "inventoryRevision": "revision-1",
                }
            )
            assert accepted["status"] == "accepted"
            deadline = time.monotonic() + 2
            while context.session_cleanup_payload["operation"]["state"] not in {
                "completed",
                "failed",
            }:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert context.session_cleanup_payload["operation"]["state"] == "completed", (
                context.session_cleanup_payload
            )

        assert events == [
            "first-enter",
            "first-fork",
            "first-close",
            "materialize",
            "second-enter",
            "second-verify",
            "second-close",
            "desktop-preflight",
            "desktop-lifecycle",
        ]
        assert client_factory.call_args_list == [
            call(codex_home=Path("E:/AppData/Codex")),
            call(codex_home=Path("E:/AppData/Codex")),
        ]
        manager.materialize_target_rollouts.assert_called_once_with(
            [(TARGET_ID, SOURCE_ID)],
            progress_callback=None,
        )
        desktop_lifecycle.archive_then_delete.assert_called_once_with(
            SOURCE_ID,
            cwd="E:/project",
        )
        desktop_lifecycle.preflight.assert_called_once_with(
            [SOURCE_ID],
            cwd="E:/project",
        )
    finally:
        assert worker.close()
