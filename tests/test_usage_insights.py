from __future__ import annotations

import time
from types import SimpleNamespace

from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.usage_insights import UsageInsightsWorker


def test_usage_insights_worker_publishes_ready_payload_and_closes() -> None:
    events = RuntimeEventBus()
    context = SimpleNamespace(usage_insights_payload={}, runtime_events=events)

    worker = UsageInsightsWorker(
        context,
        refresh=lambda _context, request_id: {
            "state": "ready",
            "ready": True,
            "revision": 7,
            "requestId": request_id,
        },
    )
    try:
        assert worker.request_refresh(request_id="request-1")
        deadline = time.monotonic() + 2
        while context.usage_insights_payload.get("state") != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        published = events.drain()
        assert [event.type for event in published] == [
            "usage_insights_changed",
            "usage_insights_changed",
            "usage_cache_hydrated",
        ]
        assert published[-1].context["revision"] == 7
    finally:
        worker.close()

    assert not worker.request_refresh(request_id="late")


def test_usage_insights_worker_projects_refresh_failure() -> None:
    context = SimpleNamespace(
        usage_insights_payload={},
        runtime_events=RuntimeEventBus(),
    )

    def fail(_context: object, _request_id: str) -> dict[str, object]:
        raise RuntimeError("refresh failed")

    worker = UsageInsightsWorker(context, refresh=fail)
    try:
        worker.request_refresh(request_id="request-2")
        deadline = time.monotonic() + 2
        while context.usage_insights_payload.get("state") != "failed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert context.usage_insights_payload["requestId"] == "request-2"
        assert context.usage_insights_payload["error"] == "refresh failed"
    finally:
        worker.close()
