from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unittest.mock import patch

from codex_usage_hud.runtime_commands import (
    GeneralCommandPorts,
    RuntimeCommandPorts,
    _handle_renderer_settings_command,
    handle_background_command,
    handle_active_session_command,
    handle_cleanup_command,
    handle_insights_command,
    dispatch_command,
)
from codex_usage_hud.config import UserConfig


TARGET_ID = "10000000-0000-4000-8000-000000000012"


def test_renderer_resume_transfer_target_builds_verified_resume_launch(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    (tmp_path / "sessions").mkdir()
    manager = MagicMock()
    manager.workdir_for_transfer_target.return_value = workdir
    context = SimpleNamespace(
        app_provider="custom",
        sessions_root=tmp_path / "sessions",
        state_db_path=tmp_path / "state_5.sqlite",
        session_cleanup_manager=manager,
    )

    with (
        patch(
            "codex_usage_hud.runtime_commands.discover_codex_cli_options",
            return_value={
                "profile": "routin",
                "defaultProvider": "custom",
                "defaultTerminal": "windows-terminal",
                "terminals": [
                    {
                        "id": "windows-terminal",
                        "shell": "cmd",
                    }
                ],
                "proxy": {"enabled": False, "port": 7897},
            },
        ) as discover,
        patch(
            "codex_usage_hud.runtime_commands.build_codex_cli_command",
            return_value="codex --profile routin resume " + TARGET_ID,
        ) as build,
        patch(
            "codex_usage_hud.runtime_commands.launch_codex_cli",
            return_value={"pid": 42, "terminal": "Windows Terminal"},
        ) as launch,
    ):
        status = _handle_renderer_settings_command(
            {
                "action": "codexCliLaunch",
                "requestId": "resume-1",
                "provider": "routin",
                "sessionTransferResumeId": TARGET_ID,
            },
            context,
            MagicMock(),
            MagicMock(),
        )

    manager.workdir_for_transfer_target.assert_called_once_with(TARGET_ID, "routin")
    discover.assert_called_once()
    build.assert_called_once()
    build_kwargs = build.call_args.kwargs
    assert build_kwargs["resume"] is True
    assert build_kwargs["resume_session_id"] == TARGET_ID
    assert build_kwargs["provider"] == "routin"
    assert build_kwargs["shell"] == "cmd"
    launch.assert_called_once()
    assert launch.call_args.kwargs["codex_home"] == tmp_path
    assert status["kind"] == ""
    assert status["codexCliLaunch"]["sessionTransferResumeId"] == TARGET_ID


def test_renderer_no_project_launch_uses_neutral_home_workdir() -> None:
    context = SimpleNamespace(app_provider="custom")

    with patch(
        "codex_usage_hud.runtime_commands.launch_codex_cli",
        return_value={"pid": 43, "terminal": "PowerShell"},
    ) as launch:
        status = _handle_renderer_settings_command(
            {
                "action": "codexCliLaunch",
                "requestId": "no-project-1",
                "provider": "custom",
                "terminalId": "powershell7",
                "command": "codex --help",
                "noProject": True,
            },
            context,
            MagicMock(),
            MagicMock(),
        )

    assert status["kind"] == ""
    assert launch.call_args.kwargs["workdir"] == str(Path.home())


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


def test_active_session_candidate_command_binds_exact_id() -> None:
    received: list[dict[str, object]] = []

    status = handle_active_session_command(
        {
            "action": "resolveActiveSession",
            "sessionId": "thread-2",
            "selectionSeq": 9,
            "requestId": "candidate-1",
        },
        RuntimeCommandPorts(
            resolve_active_session=lambda command: received.append(dict(command))
            or True
        ),
    )

    assert received[0]["sessionId"] == "thread-2"
    assert received[0]["selectionSeq"] == 9
    assert status["message"] == "已按你的选择匹配当前未归档会话。"


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


def test_background_policy_set_uses_only_normal_policy_path() -> None:
    runtime = SimpleNamespace(
        policy_set=MagicMock(
            return_value={
                "featureKey": "memory_consolidation",
                "verificationState": "verified",
                "effectiveState": "disabled",
            }
        ),
    )

    status = handle_background_command(
        {
            "action": "backgroundUsagePolicySet",
            "requestId": "policy-1",
            "featureKey": "memory_consolidation",
            "desiredState": "disabled",
            "expectedPolicyRevision": 4,
        },
        RuntimeCommandPorts(background_usage=runtime),
    )

    runtime.policy_set.assert_called_once_with(
        "memory_consolidation", "disabled", 4, "", "usage_detail"
    )
    response = status["backgroundUsageResponse"]
    assert response["requestId"] == "policy-1"
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


def test_general_command_can_request_codex_restart() -> None:
    config = UserConfig.defaults()
    restart_codex = MagicMock()
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
        request_restart_codex=restart_codex,
    )

    status = dispatch_command(
        {"action": "restartCodex", "requestId": "codex-restart-1"},
        RuntimeCommandPorts(),
        ports,
    )

    restart_codex.assert_called_once_with()
    assert status["requestId"] == "codex-restart-1"
    assert status["action"] == "restartCodex"
    assert "Codex Desktop" in status["message"]


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


def test_general_command_dispatches_fetch_provider_models() -> None:
    calls: list[tuple[str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        fetch_provider_models=lambda base, key: calls.append((base, key)) or ["gpt-5", "gpt-5.6"],
    )

    status = dispatch_command(
        {
            "action": "fetchProviderModels",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-secret",
            "requestId": "models-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [("https://api.example.com/v1", "sk-secret")]
    assert status["providerConnected"] is True
    assert status["models"] == ["gpt-5", "gpt-5.6"]
    assert status["requestId"] == "models-1"


def test_general_command_fetch_provider_models_surfaces_errors() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        fetch_provider_models=lambda base, key: (_ for _ in ()).throw(
            ValueError("API key 不能为空。")
        ),
    )

    status = dispatch_command(
        {"action": "fetchProviderModels", "baseUrl": "https://x.example/v1", "apiKey": ""},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"


def test_general_command_dispatches_codex_cli_fetch_models() -> None:
    received: list[str] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        fetch_cli_provider_models=lambda provider: received.append(provider) or {
            "provider": provider,
            "baseUrl": "https://api.example.com/v1",
            "envKey": "MY_API_KEY",
            "models": ["gpt-5", "gpt-5.6"],
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliFetchModels",
            "provider": "muyuan",
            "requestId": "cli-models-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert received == ["muyuan"]
    assert status["codexCliModels"]["models"] == ["gpt-5", "gpt-5.6"]
    assert status["codexCliModels"]["envKey"] == "MY_API_KEY"
    assert status["requestId"] == "cli-models-1"


def test_general_command_codex_cli_fetch_models_surfaces_errors() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        fetch_cli_provider_models=lambda provider: (_ for _ in ()).throw(
            ValueError("用户环境变量中没有可用的 API key。")
        ),
    )

    status = dispatch_command(
        {"action": "codexCliFetchModels", "provider": "muyuan"},
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert status["message"] == "用户环境变量中没有可用的 API key。"


def test_general_command_dispatches_provider_chat_test() -> None:
    calls: list[tuple[str, str, str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        send_provider_chat_probe=lambda base, key, model, message: calls.append(
            (base, key, model, message)
        ) or {"ok": True, "reply": "Hello!", "model": model},
    )

    status = dispatch_command(
        {
            "action": "providerChatTest",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-secret",
            "model": "Deepseek-v4-flash",
            "message": "hi",
            "requestId": "chat-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [
        ("https://api.example.com/v1", "sk-secret", "Deepseek-v4-flash", "hi")
    ]
    assert status["providerChatTest"]["ok"] is True
    assert status["providerChatTest"]["reply"] == "Hello!"
    assert status["requestId"] == "chat-1"


def test_general_command_provider_chat_test_surfaces_failure() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        send_provider_chat_probe=lambda base, key, model, message: {
            "ok": False,
            "error": "聊天测试失败（HTTP 400）：invalid model",
        },
    )

    status = dispatch_command(
        {
            "action": "providerChatTest",
            "baseUrl": "https://x.example/v1",
            "apiKey": "key",
            "model": "bad-model",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert "invalid model" in str(status["message"])


def test_general_command_dispatches_codex_cli_chat_test() -> None:
    calls: list[tuple[str, str, str]] = []
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        send_cli_chat_probe=lambda provider, model, message: calls.append(
            (provider, model, message)
        ) or {
            "ok": True,
            "reply": "Hello!",
            "model": model,
            "provider": provider,
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliChatTest",
            "provider": "qq",
            "model": "Deepseek-v4-flash",
            "requestId": "cli-chat-1",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert calls == [("qq", "Deepseek-v4-flash", "hi")]
    assert status["codexCliChatTest"]["ok"] is True
    assert status["codexCliChatTest"]["provider"] == "qq"
    assert status["requestId"] == "cli-chat-1"


def test_general_command_codex_cli_chat_test_surfaces_failure() -> None:
    ports = GeneralCommandPorts(
        load_config=lambda: UserConfig.defaults(),
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
        send_cli_chat_probe=lambda provider, model, message: {
            "ok": False,
            "error": "用户环境变量 QQ_API_KEY 中没有可用的 API key。",
        },
    )

    status = dispatch_command(
        {
            "action": "codexCliChatTest",
            "provider": "qq",
            "model": "Deepseek-v4-flash",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert status["kind"] == "error"
    assert "QQ_API_KEY" in str(status["message"])
