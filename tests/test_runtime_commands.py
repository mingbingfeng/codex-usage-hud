from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unittest.mock import patch

from codex_usage_hud.runtime_commands import (
    GeneralCommandPorts,
    RuntimeCommandPorts,
    handle_background_command,
    handle_cleanup_command,
    handle_insights_command,
    dispatch_command,
)
from codex_usage_hud.config import UserConfig


@pytest.mark.parametrize(
    ("handler", "command", "ports", "field"),
    [
        (
            handle_cleanup_command,
            {"action": "sessionCleanupScan", "requestId": "cleanup-1"},
            RuntimeCommandPorts(cleanup_worker=None),
            "sessionCleanupRequestId",
        ),
        (
            handle_insights_command,
            {"action": "usageInsightsRefresh", "requestId": "insights-1"},
            RuntimeCommandPorts(insights_worker=None),
            "usageInsightsRequestId",
        ),
    ],
)
def test_command_errors_keep_request_correlation(
    handler, command, ports, field: str
) -> None:
    status = handler(command, ports)

    assert status[field] == command["requestId"]
    assert status["kind"] == "error"


def test_background_exception_keeps_request_and_response_kind() -> None:
    runtime = SimpleNamespace(query=MagicMock(side_effect=RuntimeError("boom")))

    status = handle_background_command(
        {"action": "backgroundUsageQuery", "requestId": "background-1"},
        RuntimeCommandPorts(background_usage=runtime),
    )

    response = status["backgroundUsageResponse"]
    assert response["requestId"] == "background-1"
    assert response["kind"] == "query"
    assert "boom" in response["error"]


def test_background_policy_restart_uses_explicit_restart_path() -> None:
    runtime = SimpleNamespace(
        policy_set=MagicMock(),
        policy_restart=MagicMock(
            return_value={
                "featureKey": "memory_consolidation",
                "verificationState": "verified",
                "effectiveState": "disabled",
                "restartAttempted": True,
            }
        ),
    )

    status = handle_background_command(
        {
            "action": "backgroundUsagePolicySet",
            "requestId": "policy-restart-1",
            "featureKey": "memory_consolidation",
            "desiredState": "disabled",
            "expectedPolicyRevision": 4,
            "restartNow": True,
        },
        RuntimeCommandPorts(background_usage=runtime),
    )

    runtime.policy_restart.assert_called_once_with(
        "memory_consolidation", 4, "", "usage_detail"
    )
    runtime.policy_set.assert_not_called()
    response = status["backgroundUsageResponse"]
    assert response["requestId"] == "policy-restart-1"
    assert response["kind"] == "policyApply"
    assert response["payload"]["verificationState"] == "verified"


def test_usage_insights_workdir_opens_only_payload_directory(tmp_path: Path) -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {
        "week": {
            "topSessionsByUsage": [
                {"sessionId": session_id, "workdir": str(tmp_path)}
            ]
        }
    }

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"


def test_usage_insights_workdir_rejects_missing_or_unlisted_directory(tmp_path: Path) -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {"week": {"topSessionsByUsage": [{"sessionId": session_id, "workdir": str(tmp_path / "missing")} ]}}

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"


def test_usage_insights_workdir_rejects_empty_or_relative_directory() -> None:
    session_id = "10000000-0000-4000-8000-000000000001"
    payload = {"week": {"topSessionsByUsage": [{"sessionId": session_id, "workdir": ""}]}}

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_insights_command(
            {"action": "openUsageInsightsWorkdir", "sessionId": session_id},
            RuntimeCommandPorts(insights_payload=payload),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"


def test_session_cleanup_workdir_opens_manager_resolved_directory(tmp_path: Path) -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(return_value=tmp_path))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
                "cwd": str(tmp_path / "untrusted"),
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    manager.workdir_for_item.assert_called_once_with(
        "session-opaque-id", "1-opaque-revision"
    )
    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"
    assert "backgroundUsageResponse" not in status


@pytest.mark.parametrize("workdir", [None, Path("relative-workdir")])
def test_session_cleanup_workdir_rejects_invalid_manager_directory(workdir: object) -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(return_value=workdir))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "该会话没有可打开的工作目录。"


def test_session_cleanup_workdir_reports_manager_read_failure() -> None:
    manager = SimpleNamespace(workdir_for_item=MagicMock(side_effect=RuntimeError("offline")))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_cleanup_command(
            {
                "action": "openSessionCleanupWorkdir",
                "itemId": "session-opaque-id",
                "inventoryRevision": "1-opaque-revision",
            },
            RuntimeCommandPorts(cleanup_manager=manager),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "无法读取会话工作目录：offline"


def test_background_usage_workdir_opens_current_event_directory(tmp_path: Path) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(
        detail=MagicMock(return_value={"eventId": event_id, "cwd": str(tmp_path)}),
        confirm=MagicMock(),
    )

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {
                "action": "openBackgroundUsageWorkdir",
                "eventId": event_id,
                "cwd": str(tmp_path / "untrusted"),
            },
            RuntimeCommandPorts(background_usage=runtime),
        )

    runtime.detail.assert_called_once_with(event_id)
    runtime.confirm.assert_not_called()
    opener.assert_called_once_with(tmp_path)
    assert status["kind"] == ""
    assert status["message"] == "已打开工作目录。"
    assert "backgroundUsageResponse" not in status


@pytest.mark.parametrize(
    "detail_value",
    [
        None,
        {"eventId": "10000000-0000-4000-8000-000000000002", "cwd": ""},
        {
            "eventId": "10000000-0000-4000-8000-000000000002",
            "cwd": "relative-directory",
        },
    ],
)
def test_background_usage_workdir_rejects_unavailable_or_invalid_directory(
    detail_value: object,
) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(detail=detail_value)

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
            RuntimeCommandPorts(background_usage=runtime),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "该后台任务没有可打开的工作目录。"
    assert "backgroundUsageResponse" not in status


def test_background_usage_workdir_rejects_nonexistent_or_mismatched_event_directory(
    tmp_path: Path,
) -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    other_event_id = "10000000-0000-4000-8000-000000000003"

    for detail_payload in (
        {"eventId": event_id, "cwd": str(tmp_path / "missing")},
        {"eventId": other_event_id, "cwd": str(tmp_path)},
    ):
        runtime = SimpleNamespace(detail=MagicMock(return_value=detail_payload))
        with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
            status = handle_background_command(
                {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
                RuntimeCommandPorts(background_usage=runtime),
            )

        opener.assert_not_called()
        assert status["kind"] == "error"
        assert status["message"] == "该后台任务没有可打开的工作目录。"
        assert "backgroundUsageResponse" not in status


def test_background_usage_workdir_reports_detail_read_failure() -> None:
    event_id = "10000000-0000-4000-8000-000000000002"
    runtime = SimpleNamespace(detail=MagicMock(side_effect=RuntimeError("offline")))

    with patch("codex_usage_hud.runtime_commands._open_system_path") as opener:
        status = handle_background_command(
            {"action": "openBackgroundUsageWorkdir", "eventId": event_id},
            RuntimeCommandPorts(background_usage=runtime),
        )

    opener.assert_not_called()
    assert status["kind"] == "error"
    assert status["message"] == "无法读取后台任务工作目录：offline"
    assert "backgroundUsageResponse" not in status


def test_general_command_status_keeps_request_and_action() -> None:
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
    )

    status = dispatch_command(
        {"action": "dismissWarningsToday", "requestId": "settings-1"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["requestId"] == "settings-1"
    assert status["action"] == "dismissWarningsToday"


def test_general_command_dispatches_provider_delete() -> None:
    deleted: list[dict[str, object]] = []
    config = UserConfig.defaults()
    ports = GeneralCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: None,
        fetch_prices=lambda url: {},
        rest_reminder=None,
        update_manager=None,
        work_overlay=None,
        request_restart=lambda: None,
        request_exit=lambda: None,
        check_update=lambda: SimpleNamespace(error="", available=False, current_version="1"),
        install_update=lambda info: None,
        overlay_status=lambda: {},
        start_overlay_install=lambda: False,
        clear_forced_missing=lambda: None,
        forced_missing_with_real_install=lambda: False,
        pyside_version=lambda: "",
        default_overlay_limit=lambda: 1,
        dismiss_warnings_today=lambda: True,
        delete_provider=lambda command: deleted.append(dict(command)) or {
            "status": "ok",
            "providerId": "muyuan",
        },
    )

    status = dispatch_command(
        {"action": "deleteProvider", "provider": "muyuan", "requestId": "delete-1"},
        RuntimeCommandPorts(),
        ports,
    )

    assert deleted == [
        {"action": "deleteProvider", "provider": "muyuan", "requestId": "delete-1"}
    ]
    assert status["status"] == "ok"
    assert status["providerId"] == "muyuan"
    assert status["requestId"] == "delete-1"
