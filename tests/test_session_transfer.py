from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.core.session_transfer import (
    CodexAppServerClient,
    SessionTransferError,
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


def test_app_server_fork_rejects_provider_mismatch() -> None:
    client = CodexAppServerClient(executable="codex")
    client.request = MagicMock(
        return_value={"thread": {"id": TARGET_ID, "modelProvider": "other"}}
    )

    with pytest.raises(SessionTransferError, match="目标 Provider 不匹配"):
        client.fork(SOURCE_ID, "routin")


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
    manager.transfer.return_value = {
        "revision": "revision-2",
        "operation": {
            "requestId": "transfer-1",
            "action": "sessionTransfer",
            "state": "completed",
            "copiedCount": 1,
            "migratedCount": 0,
        },
    }
    app_server = MagicMock()
    app_server.fork.return_value = TARGET_ID
    client_factory = MagicMock()
    client_factory.return_value.__enter__.return_value = app_server
    context = SimpleNamespace(
        app_provider="codex",
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
        fork = call.kwargs["fork"]
        assert fork(SOURCE_ID, "routin", "E:/project") == TARGET_ID
        app_server.fork.assert_called_once_with(SOURCE_ID, "routin", cwd="E:/project")
    finally:
        assert worker.close()
