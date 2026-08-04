from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud.core import ParsedSession, UsageSummary
from codex_usage_hud.config import UserConfig
from codex_usage_hud.snapshot_builder import (
    RuntimeSnapshotBuilder,
    SnapshotBuilderPorts,
    build_snapshot,
)


def test_snapshot_keeps_selection_sequence_captured_before_parse() -> None:
    tracker = SimpleNamespace(
        selection_seq=10,
        selection_observed_at_ms=100,
        renderer_session_id="renderer-old",
        follow_state="following",
        follow_reason="",
        selection_received_at_ms=101,
        selection_resolved_at_ms=102,
        follow_stuck_since_ms=0,
        follow_stuck_elapsed_ms=0,
        title_for_session=lambda path, session_id: "old title",
    )

    def snapshot_for(path: Path, *, session_id: str) -> ParsedSession:
        del path, session_id
        tracker.selection_seq = 11
        tracker.selection_observed_at_ms = 200
        return ParsedSession(session_id="old-session", status="parsed")

    context = SimpleNamespace(
        reload_user_config=lambda: None,
        active_session_tracker=tracker,
        session_resolver=SimpleNamespace(
            resolve=lambda: (Path("old.jsonl"), "renderer:old"),
            session_id="old-session",
        ),
        session_snapshot_cache=SimpleNamespace(snapshot_for=snapshot_for),
        visible_app_error_cache=SimpleNamespace(resolve=lambda snapshot, error: ""),
        platform=SimpleNamespace(get_active_app_error=lambda: ""),
        user_config=UserConfig.defaults(),
        usage_cache=SimpleNamespace(
            summarize=lambda *args, **kwargs: (UsageSummary(), UsageSummary())
        ),
        sessions_root=Path("."),
        daily_budget_usd=0.0,
        weekly_budget_usd=0.0,
        budget_thresholds=[],
        pre_send_estimator=None,
        parser=SimpleNamespace(),
        session_management_current_session_id="",
        session_management_active_session_ids=set(),
    )
    ports = SnapshotBuilderPorts(
        record_active_session_error=lambda *args: None,
        provider_scope=lambda *args: None,
        refresh_usage_insights=lambda *args: None,
        active_work_items=lambda *args: [],
        apply_family_usage=lambda *args: None,
    )

    snapshot = build_snapshot(context, ports)

    assert tracker.selection_seq == 11
    assert snapshot.selection_seq == 10
    assert snapshot.selection_observed_at_ms == 100


def test_runtime_snapshot_builder_preserves_refresh_options() -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def builder(context: object, **kwargs: object) -> ParsedSession:
        calls.append((context, dict(kwargs)))
        return ParsedSession(status="ok")

    context = object()
    runtime_builder = RuntimeSnapshotBuilder(context=context, builder=builder)
    reused = ParsedSession(status="old")

    runtime_builder(
        refresh_budget_aggregate=False,
        refresh_budget_paths=(Path("changed.jsonl"),),
        refresh_active_work_items=False,
        refresh_current_session_usage=False,
        reuse_budget_from=reused,
        refresh_visible_app_error=False,
    )

    assert calls == [
        (
            context,
            {
                "refresh_budget_aggregate": False,
                "refresh_budget_paths": (Path("changed.jsonl"),),
                "reuse_budget_from": reused,
                "refresh_visible_app_error": False,
                "refresh_current_session_usage": False,
                "refresh_active_work_items": False,
            },
        )
    ]


def test_runtime_snapshot_builder_returns_error_snapshot_on_failure() -> None:
    def fail(_context: object) -> ParsedSession:
        raise RuntimeError("snapshot failed")

    snapshot = RuntimeSnapshotBuilder(context=object(), builder=fail)()

    assert snapshot.status == "error"
    assert snapshot.error == "snapshot failed"
