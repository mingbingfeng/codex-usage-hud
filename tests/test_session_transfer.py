from __future__ import annotations

from pathlib import Path
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
from codex_usage_hud.session_cleanup_runtime import SessionCleanupWorker


SOURCE_ID = "10000000-0000-4000-8000-000000000001"
TARGET_ID = "10000000-0000-4000-8000-000000000002"


def test_app_server_fork_requests_target_provider_and_validates_response() -> None:
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
            "threadSource": "custom",
            "ephemeral": False,
            "cwd": "E:/project",
        },
    )


def test_app_server_client_overrides_codex_home_for_desktop_store(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "E:/wrong-home")
    environment = _codex_environment("E:/AppData/Codex")
    assert environment["CODEX_HOME"] == "E:/AppData/Codex"


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
                "useStateDbOnly": True,
            },
        ),
        call(
            "thread/resume",
            {"threadId": TARGET_ID, "modelProvider": "routin"},
        ),
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
        },
    }
    first_app_server = MagicMock()
    first_app_server.fork.return_value = TARGET_ID
    first_app_server.verify_persistent_thread.return_value = True
    first_client = MagicMock()
    first_client.__enter__.return_value = first_app_server
    client_factory = MagicMock(return_value=first_client)
    manager.materialize_target_rollout.return_value = None

    def transfer(*_args, **kwargs):
        assert kwargs["fork"](SOURCE_ID, "routin", "E:/project") == TARGET_ID
        assert kwargs["verify"](TARGET_ID, "routin") is True
        assert callable(kwargs["prepare_desktop_binding_cleanup"])
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
        first_app_server.fork.assert_called_once_with(
            SOURCE_ID,
            "routin",
            cwd="E:/project",
        )
        manager.materialize_target_rollout.assert_not_called()
        first_app_server.verify_persistent_thread.assert_called_once_with(
            TARGET_ID,
            "routin",
        )
        first_client.__exit__.assert_called_once_with(None, None, None)
        assert client_factory.call_args_list == [call(codex_home=Path("E:/AppData/Codex"))]
    finally:
        assert worker.close()


def test_worker_releases_fork_connection_before_migrate_materialization() -> None:
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
    first_app_server.verify_persistent_thread.side_effect = lambda *_args: (
        events.append("first-verify") or True
    )
    second_app_server = MagicMock()
    second_app_server.verify_persistent_thread.side_effect = lambda *_args: (
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
    manager.materialize_target_rollout.side_effect = lambda *_args: events.append(
        "materialize"
    )

    def transfer(*_args, **kwargs):
        assert kwargs["fork"](SOURCE_ID, "routin", "E:/project") == TARGET_ID
        assert kwargs["verify"](TARGET_ID, "routin") is True
        assert kwargs["materialize"](TARGET_ID, SOURCE_ID) is None
        assert kwargs["verify"](TARGET_ID, "routin") is True
        assert callable(kwargs["prepare_desktop_binding_cleanup"])
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
            while context.session_cleanup_payload["operation"]["state"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)

        assert events == [
            "first-enter",
            "first-fork",
            "first-verify",
            "first-close",
            "materialize",
            "second-enter",
            "second-verify",
            "second-close",
        ]
        assert client_factory.call_args_list == [
            call(codex_home=Path("E:/AppData/Codex")),
            call(codex_home=Path("E:/AppData/Codex")),
        ]
        manager.materialize_target_rollout.assert_called_once_with(TARGET_ID, SOURCE_ID)
    finally:
        assert worker.close()
