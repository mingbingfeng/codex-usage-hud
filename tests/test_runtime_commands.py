from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
