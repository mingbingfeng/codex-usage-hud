from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.core import JsonlSessionParser
from codex_usage_hud.runtime_context import RuntimeContext


def test_runtime_context_construction_does_not_create_optional_workers(tmp_path: Path) -> None:
    context = RuntimeContext(
        platform=object(),
        sessions_root=tmp_path / "sessions",
        session_file=None,
        sqlite_log_path=None,
        state_db_path=tmp_path / "state.sqlite",
        session_index_path=tmp_path / "index.jsonl",
        poll_ms=500,
        daily_budget_usd=0.0,
        weekly_budget_usd=0.0,
        budget_thresholds=[],
        user_config=UserConfig.defaults(),
        settings_store=UserConfigStore(tmp_path / "settings.json"),
        settings_mtime=None,
        parser=JsonlSessionParser(),
        sse_tracker=None,
        active_session_tracker=None,
        session_resolver=object(),
        usage_cache=object(),
    )

    assert context.rest_reminder is None
    assert context.session_snapshot_cache is None
    assert context.usage_insights_worker is None
    assert context.session_cleanup_worker is None


def test_runtime_context_close_is_idempotent_and_closes_all_owned_resources() -> None:
    order: list[str] = []

    def resource(name: str) -> SimpleNamespace:
        return SimpleNamespace(close=MagicMock(side_effect=lambda: order.append(name)))

    context = object.__new__(RuntimeContext)
    context._closed = False
    context.active_session_tracker = resource("tracker")
    context.session_resolver = SimpleNamespace(active_session_tracker=context.active_session_tracker)
    context.session_cleanup_worker = resource("cleanup")
    context.usage_insights_worker = resource("insights")
    context.background_usage_runtime = resource("background")
    context.session_snapshot_cache = resource("snapshots")
    context.pre_send_estimator = resource("estimator")
    context.rest_reminder = resource("reminder")

    context.close()
    context.close()

    assert order == [
        "tracker",
        "cleanup",
        "insights",
        "background",
        "snapshots",
        "estimator",
        "reminder",
    ]
    assert context.session_snapshot_cache is None
    assert context.pre_send_estimator is None
    assert context.rest_reminder is None


def test_runtime_context_close_continues_after_one_resource_fails() -> None:
    order: list[str] = []

    def resource(name: str, *, fail: bool = False) -> SimpleNamespace:
        def close() -> None:
            order.append(name)
            if fail:
                raise RuntimeError(name)

        return SimpleNamespace(close=close)

    context = object.__new__(RuntimeContext)
    context._closed = False
    context.active_session_tracker = resource("tracker", fail=True)
    context.session_resolver = SimpleNamespace(active_session_tracker=context.active_session_tracker)
    context.session_cleanup_worker = resource("cleanup", fail=True)
    context.usage_insights_worker = resource("insights")
    context.background_usage_runtime = resource("background")
    context.session_snapshot_cache = resource("snapshots")
    context.pre_send_estimator = resource("estimator")
    context.rest_reminder = resource("reminder")

    context.close()

    assert order == [
        "tracker",
        "cleanup",
        "insights",
        "background",
        "snapshots",
        "estimator",
        "reminder",
    ]
    assert context.session_cleanup_worker is None
    assert context.rest_reminder is None
