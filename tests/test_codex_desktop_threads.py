from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codex_usage_hud.platforms.codex_desktop_threads import (
    CodexDesktopThreadLifecycle,
    CodexDesktopThreadLifecycleError,
    DesktopThreadLifecycleReport,
    _desktop_thread_lifecycle_script,
    _desktop_thread_preflight_script,
)


SOURCE_ID = "10000000-0000-4000-8000-000000000001"


def _cdp_value(value: object) -> dict[str, object]:
    return {"result": {"result": {"value": value}}}


def _lifecycle(command_sender: object) -> CodexDesktopThreadLifecycle:
    return CodexDesktopThreadLifecycle(
        port=55545,
        enabled=True,
        target_lister=lambda _port, _timeout: [
            {"webSocketDebuggerUrl": "ws://127.0.0.1:55545/devtools/page/main"}
        ],
        target_picker=lambda targets: targets[0],
        command_sender=command_sender,  # type: ignore[arg-type]
    )


def test_lifecycle_script_uses_desktop_app_server_and_requires_notifications() -> None:
    script = _desktop_thread_lifecycle_script(
        SOURCE_ID,
        "E:/project",
        timeout_ms=2500,
    )

    assert 'type: "mcp-request"' in script
    assert 'type: "archive-thread"' in script
    assert '"thread/archive"' in script
    assert '"thread/delete"' in script
    assert '"thread/archived"' in script
    assert '"thread/deleted"' in script
    assert 'window.addEventListener("message", onMessage)' in script
    assert 'window.removeEventListener("message", onMessage)' in script
    assert "persisted-atom-update" not in script
    assert "state_5.sqlite" not in script
    assert SOURCE_ID in script


def test_preflight_script_reads_all_source_rollouts_without_mutation() -> None:
    script = _desktop_thread_preflight_script(
        [SOURCE_ID],
        "E:/project",
        timeout_ms=2500,
    )

    assert '"thread/read"' in script
    assert 'includeTurns: false' in script
    assert 'source: "hud-session-migration-preflight"' in script
    assert '"thread/archive"' not in script
    assert '"thread/delete"' not in script
    assert SOURCE_ID in script


def test_preflight_reports_a_verified_source_family() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "verified": True,
                "threadIds": [SOURCE_ID],
                "error": "",
            }
        )
    )

    result = _lifecycle(sender).preflight([SOURCE_ID], cwd="E:/project")

    assert result["verified"] is True
    assert result["threadIds"] == [SOURCE_ID]
    params = sender.call_args.args[2]
    assert params["awaitPromise"] is True
    assert '"thread/read"' in params["expression"]


def test_lifecycle_reports_a_fully_verified_desktop_delete() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "threadId": SOURCE_ID,
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
                "error": "",
            }
        )
    )

    report = _lifecycle(sender).archive_then_delete(SOURCE_ID, cwd="E:/project")

    assert report == DesktopThreadLifecycleReport(
        thread_id=SOURCE_ID,
        archived=True,
        deleted=True,
        archive_notification=True,
        delete_notification=True,
    )
    assert report.verified
    params = sender.call_args.args[2]
    assert params["awaitPromise"] is True
    assert 'type: "mcp-request"' in params["expression"]


def test_lifecycle_preserves_an_archived_source_when_delete_is_not_confirmed() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": False,
                "threadId": SOURCE_ID,
                "archived": True,
                "deleted": False,
                "archiveNotification": True,
                "deleteNotification": False,
                "error": "desktop-delete-failed",
            }
        )
    )

    report = _lifecycle(sender).archive_then_delete(SOURCE_ID)

    assert report.archived
    assert not report.deleted
    assert not report.verified
    assert report.error == "desktop-delete-failed"


def test_lifecycle_rejects_a_mismatched_desktop_result() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "threadId": "10000000-0000-4000-8000-000000000002",
                "archived": True,
                "deleted": True,
                "archiveNotification": True,
                "deleteNotification": True,
            }
        )
    )

    with pytest.raises(CodexDesktopThreadLifecycleError, match="不匹配"):
        _lifecycle(sender).archive_then_delete(SOURCE_ID)
