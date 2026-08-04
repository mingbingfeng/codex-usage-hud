"""Tests for the pure request/task-round Renderer payload owner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from codex_usage_hud import renderer_payload_builder as builder
from codex_usage_hud import renderer_request_projection as projection
from codex_usage_hud.core.parser import ParsedSession, RequestRound, RequestTokens


def _rows(count: int = 31) -> list[RequestRound]:
    moment = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    return [
        RequestRound(
            index=index,
            status="confirmed",
            model="gpt-5.5",
            input_tokens=100 + index,
            cached_tokens=20,
            output_tokens=10,
            reasoning_tokens=2,
            total_tokens=112 + index,
            estimated=False,
            cost_usd=0.01 * index,
            started_at=moment,
            completed_at=moment,
        )
        for index in range(1, count + 1)
    ]


def _snapshot() -> ParsedSession:
    snapshot = ParsedSession(
        session_id="request-projection-session",
        status="parsed",
        request=RequestTokens(status="confirmed", model="gpt-5.5"),
    )
    snapshot.request_history = _rows()
    return snapshot


def test_request_owner_preserves_order_limit_totals_and_builder_wrappers() -> None:
    snapshot = _snapshot()
    context = builder._request_projection_context()

    display_rows, _widths = projection.display_request_rows(snapshot, context=context)
    assert [item.index for item in display_rows] == list(range(31, 1, -1))
    assert projection.request_rows(snapshot, context=context) == builder._request_rows(snapshot)
    assert projection.request_row_details(snapshot, context=context) == builder._request_row_details(snapshot)

    input_tokens, cached_tokens, output_tokens, reasoning_tokens, total_tokens, cost, estimated = projection.task_total(
        snapshot,
        context=context,
    )
    assert input_tokens == sum(100 + index for index in range(1, 32))
    assert cached_tokens == 20 * 31
    assert output_tokens == 10 * 31
    assert reasoning_tokens == 2 * 31
    assert total_tokens == sum(112 + index for index in range(1, 32))
    assert abs(cost - sum(0.01 * index for index in range(1, 32))) < 1e-9
    assert estimated is False


def test_request_owner_projects_single_snapshot_round_and_has_no_runtime_dependency() -> None:
    snapshot = ParsedSession(
        status="parsed",
        request=RequestTokens(
            status="running",
            model="gpt-5.5",
            input_tokens=100,
            cached_tokens=20,
            output_tokens=10,
            reasoning_tokens=2,
            total_tokens=112,
            estimated=False,
            cost_usd=0.02,
        ),
    )
    context = builder._request_projection_context()
    row = projection.round_from_snapshot(snapshot, context=context)

    assert row.index == 1
    assert row.model == "gpt-5.5"
    assert row.input_tokens == 100
    assert row.cached_tokens == 20
    assert row.total_tokens == 112
    assert row.cost_usd == 0.02
    assert row.estimated is False

    source = Path(projection.__file__).read_text(encoding="utf-8")
    assert "renderer_payload_builder" not in source
    assert "renderer_runtime" not in source
    assert "desktop_overlay" not in source
    assert "renderer_client" not in source
