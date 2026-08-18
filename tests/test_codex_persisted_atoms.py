from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codex_usage_hud.platforms.codex_persisted_atoms import (
    CodexDesktopBindingCleanupError,
    CodexDesktopBindingCleaner,
    DesktopBindingCleanupReport,
    PERSISTED_THREAD_BINDINGS_KEY,
    _persisted_atom_script,
)


SOURCE_ID = "10000000-0000-4000-8000-000000000001"


def _cdp_value(value: object) -> dict[str, object]:
    return {"result": {"result": {"value": value}}}


def _cleaner(command_sender: object) -> CodexDesktopBindingCleaner:
    return CodexDesktopBindingCleaner(
        port=55545,
        enabled=True,
        target_lister=lambda _port, _timeout: [
            {"webSocketDebuggerUrl": "ws://127.0.0.1:55545/devtools/page/main"}
        ],
        target_picker=lambda targets: targets[0],
        command_sender=command_sender,  # type: ignore[arg-type]
    )


def test_persisted_atom_script_updates_only_exact_source_binding_values() -> None:
    script = _persisted_atom_script([SOURCE_ID.upper()], commit=True, timeout_ms=2500)

    assert PERSISTED_THREAD_BINDINGS_KEY in script
    assert "persisted-atom-sync-request" in script
    assert "persisted-atom-update" in script
    assert "sourceIds.has(value.trim().toLowerCase())" in script
    assert "prompt-history" not in script
    assert SOURCE_ID in script


def test_prepare_returns_a_commit_plan_after_live_desktop_preflight() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "canWrite": True,
                "matchingBindingKeys": ["client-source"],
                "remainingBindingKeys": ["client-source"],
            }
        )
    )
    cleaner = _cleaner(sender)

    plan = cleaner.prepare_source_binding_cleanup([SOURCE_ID])

    assert plan.source_ids == (SOURCE_ID,)
    params = sender.call_args.args[2]
    assert params["awaitPromise"] is True
    assert 'phase: "prepare"' in params["expression"]
    assert "persisted-atom-update" in params["expression"]


def test_commit_reports_only_verified_exact_binding_removal() -> None:
    sender = MagicMock(
        side_effect=[
            _cdp_value(
                {
                    "ok": True,
                    "canWrite": True,
                    "matchingBindingKeys": ["client-source"],
                    "remainingBindingKeys": ["client-source"],
                }
            ),
            _cdp_value(
                {
                    "ok": True,
                    "canWrite": True,
                    "matchingBindingKeys": ["client-source"],
                    "removedBindingKeys": ["client-source"],
                    "remainingBindingKeys": [],
                }
            ),
        ]
    )
    cleaner = _cleaner(sender)

    report = cleaner.prepare_source_binding_cleanup([SOURCE_ID]).commit()

    assert report == DesktopBindingCleanupReport(("client-source",), ())
    assert report.verified
    commit_expression = sender.call_args.args[2]["expression"]
    assert 'phase: "commit"' in commit_expression
    assert "persisted-atom-update" in commit_expression


def test_prepare_allows_a_non_writable_desktop_only_when_no_source_binding_exists() -> (
    None
):
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": True,
                "canWrite": False,
                "matchingBindingKeys": [],
                "remainingBindingKeys": [],
            }
        )
    )

    plan = _cleaner(sender).prepare_source_binding_cleanup([SOURCE_ID])

    assert plan.source_ids == (SOURCE_ID,)


def test_prepare_marks_a_live_unwritable_source_binding_for_protection() -> None:
    sender = MagicMock(
        return_value=_cdp_value(
            {
                "ok": False,
                "reason": "desktop-persistence-not-writable",
                "matchingBindingKeys": ["client-source"],
                "remainingBindingKeys": ["client-source"],
            }
        )
    )

    with pytest.raises(CodexDesktopBindingCleanupError) as caught:
        _cleaner(sender).prepare_source_binding_cleanup([SOURCE_ID])

    assert caught.value.source_binding_detected
    assert "不允许写入" in str(caught.value)


def test_commit_rejects_unverified_remaining_source_binding() -> None:
    sender = MagicMock(
        side_effect=[
            _cdp_value(
                {
                    "ok": True,
                    "canWrite": True,
                    "matchingBindingKeys": ["client-source"],
                    "remainingBindingKeys": ["client-source"],
                }
            ),
            _cdp_value(
                {
                    "ok": False,
                    "reason": "desktop-persistence-not-writable",
                    "remainingBindingKeys": ["client-source"],
                }
            ),
        ]
    )

    plan = _cleaner(sender).prepare_source_binding_cleanup([SOURCE_ID])

    with pytest.raises(CodexDesktopBindingCleanupError, match="不允许写入"):
        plan.commit()
