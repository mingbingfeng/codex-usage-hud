from __future__ import annotations

from codex_usage_hud.renderer_metrics import RendererMetricsWindow


def test_renderer_metrics_rolls_up_required_counters() -> None:
    now = [100.0]
    metrics = RendererMetricsWindow(monotonic=lambda: now[0])
    metrics.record("cdp_commands")
    metrics.record("payload_updates", 2)
    metrics.record("script_installs")
    metrics.record("payload_bytes", 1234)
    metrics.record("merged_refreshes", 3)
    metrics.record("binding_rebuilds", 2)
    metrics.start_cooldown(10.0)
    now[0] = 105.0
    metrics.record("cdp_commands")
    now[0] = 161.0
    summary = metrics.record("payload_updates")
    assert summary is not None
    assert summary["cdpCommands"] == 2
    assert summary["payloadUpdates"] == 2
    assert summary["scriptInstalls"] == 1
    assert summary["payloadBytes"] == 1234
    assert summary["mergedRefreshes"] == 3
    assert summary["bindingRebuilds"] == 2
    assert summary["cooldownSeconds"] == 10.0


def test_renderer_metrics_has_no_timer_thread() -> None:
    metrics = RendererMetricsWindow(monotonic=lambda: 1.0)
    assert metrics.flush() is None
