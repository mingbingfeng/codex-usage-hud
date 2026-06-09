"""Unit tests for HUD geometry and budget helpers."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import subprocess
import tkinter as tk
from tkinter import ttk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.cli import (
    AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT,
    DAEMON_RESTART_REQUESTED,
    DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS,
    HudAlreadyRunningError,
    HudInstanceLock,
    RENDERER_HUD_UNAVAILABLE,
    RENDERER_UPDATE_FAILURE_LIMIT,
    UsageSummaryCache,
    _renderer_refresh_delay_seconds,
    _wait_for_visible_codex_window,
    _renderer_update_failure_limit,
    _handle_renderer_settings_command,
    budget_warnings,
    main,
    parse_thresholds,
    run_renderer_hud_session,
    snapshot_to_text,
    run_daemon,
    run_hud_session,
    run_tk_hud_session,
    stop_running_hud,
    usage_before_today_in_week,
)
from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.core.parser import (
    GapTiming,
    ParsedSession,
    RequestRound,
    SlowSummary,
    ToolCallTiming,
    UsageSummary,
)
from codex_usage_hud.ui.tk_hud import (
    REQUEST_DOCK_BOTTOM,
    REQUEST_DOCK_EXPANDED_HEIGHT,
    REQUEST_DOCK_HEIGHT,
    REQUEST_DOCK_LEFT,
    REQUEST_DOCK_RIGHT,
    REQUEST_DOCK_WIDTH,
    NATIVE_ANCHOR_STABLE_FRAMES,
    HUD_CDP_DOM_ENV,
    SETTINGS_DIALOG_HEIGHT,
    SETTINGS_DIALOG_WIDTH,
    TOP_DOCK_EXPANDED_HEIGHT,
    TOP_DOCK_HEIGHT,
    TOP_DOCK_LEFT,
    TOP_DOCK_RIGHT,
    TOP_DOCK_TOP,
    AttachedHudGeometry,
    DockGeometry,
    HudSettings,
    HudSettingsStore,
    HudAnchor,
    AutoScrollLabel,
    TOKEN_LEGEND_TEXT,
    TokenHudWindow,
    WindowPlacement,
    WindowRect,
    _can_animate_numeric_text,
    _copyable_gap_detail,
    _copyable_tool_command,
    _fixed_token_total,
    _interpolate_numeric_text,
    _request_total_line,
    _round_entry,
    _round_entry_widths,
    _visual_anchor_geometry,
)


class _FakeWindow:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def winfo_x(self) -> int:
        return self._x

    def winfo_y(self) -> int:
        return self._y

    def winfo_width(self) -> int:
        return self._width

    def winfo_height(self) -> int:
        return self._height


class _RecordingGeometryWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def geometry(self, value: str) -> None:
        self.calls.append(value)


class _FakeAnchorLocator:
    def __init__(self, anchors: dict[str, HudAnchor], *, active: bool = True) -> None:
        self.anchors = anchors
        self.active = active

    def set_dpi_aware(self) -> None:
        return None

    def find(self) -> WindowRect | None:
        return None

    def is_active(self, rect: WindowRect, allowed_hwnds: set[int]) -> bool:
        del rect, allowed_hwnds
        return self.active

    def dock_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> tuple[int, int, int] | None:
        del rect, hud_height
        anchor = self.anchors.get(target)
        if anchor is None:
            return None
        return anchor.default_x, anchor.default_y, anchor.default_width

    def anchor_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        del rect, hud_height
        return self.anchors.get(target)


def _attached_geometry_after_stable(
    window: TokenHudWindow,
    target: str,
    rect: WindowRect,
    expanded: bool = False,
) -> tuple[int, int, int, int]:
    result = window._attached_geometry(target, rect, expanded)
    for _ in range(NATIVE_ANCHOR_STABLE_FRAMES - 1):
        result = window._attached_geometry(target, rect, expanded)
    return result


class _FakeUsageParser:
    def __init__(self) -> None:
        self.loads = 0

    def load_records_lenient(self, path: Path) -> list[dict[str, str]]:
        del path
        self.loads += 1
        return []

    def usage_events(self, records: list[dict[str, str]]) -> list[dict[str, str]]:
        del records
        return []

    def summarize_usage_events(
        self,
        events: list[dict[str, str]],
        start: datetime,
    ) -> UsageSummary:
        del events
        return UsageSummary(tokens=start.day)


def _walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
    widgets = [widget]
    for child in widget.winfo_children():
        widgets.extend(_walk_widgets(child))
    return widgets


def _parse_tk_geometry(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", value)
    if match is None:
        raise AssertionError(f"unexpected Tk geometry: {value!r}")
    width, height, x, y = match.groups()
    return int(width), int(height), int(x), int(y)


class AttachedHudGeometryTests(unittest.TestCase):
    def test_calculate_places_hud_inside_top_right_corner(self) -> None:
        rect = WindowRect(left=100, top=50, right=1100, bottom=850)

        x, y, width, height = AttachedHudGeometry.calculate(
            rect,
            width=360,
            height=34,
            offset=10,
        )

        self.assertEqual((x, y, width, height), (730, 60, 360, 34))
        self.assertLessEqual(x + width, rect.right - 10)
        self.assertGreaterEqual(y, rect.top + 10)

    def test_calculate_clamps_width_to_codex_window(self) -> None:
        rect = WindowRect(left=0, top=0, right=300, bottom=220)

        x, y, width, height = AttachedHudGeometry.calculate(
            rect,
            width=520,
            height=284,
            offset=10,
        )

        self.assertEqual((x, y), (10, 10))
        self.assertEqual(width, 280)
        self.assertEqual(height, 200)


class OriginalDockGeometryTests(unittest.TestCase):
    def test_top_dock_uses_original_offsets(self) -> None:
        rect = WindowRect(left=100, top=50, right=1300, bottom=850)
        geometry = DockGeometry(
            top=TOP_DOCK_TOP,
            left=TOP_DOCK_LEFT,
            right=TOP_DOCK_RIGHT,
            height=TOP_DOCK_HEIGHT,
            expanded_height=TOP_DOCK_EXPANDED_HEIGHT,
            min_width=300,
        )

        collapsed = geometry.calculate(rect, expanded=False)
        expanded = geometry.calculate(rect, expanded=True)

        self.assertEqual(collapsed, (556, 92, 520, 36))
        self.assertEqual(expanded, (556, 92, 520, TOP_DOCK_EXPANDED_HEIGHT))

    def test_bottom_request_dock_uses_original_offsets_and_fixed_width(self) -> None:
        rect = WindowRect(left=100, top=50, right=1300, bottom=850)
        geometry = DockGeometry(
            top=0,
            left=REQUEST_DOCK_LEFT,
            right=REQUEST_DOCK_RIGHT,
            height=REQUEST_DOCK_HEIGHT,
            expanded_height=REQUEST_DOCK_EXPANDED_HEIGHT,
            min_width=300,
            bottom=REQUEST_DOCK_BOTTOM,
            fixed_width=REQUEST_DOCK_WIDTH,
        )

        collapsed = geometry.calculate(rect, expanded=False)
        expanded = geometry.calculate(rect, expanded=True)

        self.assertEqual(collapsed, (620, 790, 358, 32))
        self.assertEqual(expanded, (620, 642, 358, 180))


class VisualAnchorGeometryTests(unittest.TestCase):
    def test_top_anchor_tracks_title_bar_content_region(self) -> None:
        rect = WindowRect(left=240, top=0, right=1230, bottom=740)

        x, y, width, height = _visual_anchor_geometry("top", rect, expanded=False)

        self.assertEqual((x, y, width, height), (394, 38, 664, 36))

    def test_request_anchor_tracks_input_composer_region(self) -> None:
        rect = WindowRect(left=240, top=0, right=1230, bottom=740)

        x, y, width, height = _visual_anchor_geometry("request", rect, expanded=False)

        self.assertEqual((x, y, width, height), (538, 672, 347, 32))


class BudgetHelperTests(unittest.TestCase):
    def test_parse_thresholds_accepts_percent_or_fraction(self) -> None:
        self.assertEqual(parse_thresholds("50,0.8,90"), [0.5, 0.8, 0.9])

    def test_budget_warnings_report_latest_crossed_threshold(self) -> None:
        warnings = budget_warnings(
            day_cost=85.0,
            week_cost=210.0,
            daily_limit_usd=100.0,
            weekly_limit_usd=400.0,
            thresholds=[0.5, 0.8, 0.9],
        )

        self.assertEqual(len(warnings), 2)
        self.assertIn("日额度已用 85.00/100 USD", warnings[0])
        self.assertIn("超过 80% 阈值", warnings[0])
        self.assertIn("周额度已用 210.00/400 USD", warnings[1])
        self.assertIn("超过 50% 阈值", warnings[1])

    def test_usage_summary_cache_invalidates_when_budget_window_changes(self) -> None:
        parser = _FakeUsageParser()
        cache = UsageSummaryCache(parser)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text("{}", encoding="utf-8")
            first_day = datetime(2026, 5, 28, 10, 0)
            second_day = datetime(2026, 5, 29, 10, 0)
            week_start = datetime(2026, 5, 28, 10, 0)

            first, _ = cache.summarize(Path(temp_dir), first_day, week_start)
            second, _ = cache.summarize(Path(temp_dir), second_day, week_start)

        self.assertEqual(first.tokens, 28)
        self.assertEqual(second.tokens, 29)
        self.assertEqual(parser.loads, 2)

    def test_usage_summary_cache_throttles_repeated_directory_rescans(self) -> None:
        parser = _FakeUsageParser()
        cache = UsageSummaryCache(  # type: ignore[arg-type]
            parser,
            min_rescan_seconds=60.0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text("{}", encoding="utf-8")
            day_start = datetime(2026, 5, 28, 10, 0)
            week_start = datetime(2026, 5, 25, 10, 0)

            first, _ = cache.summarize(Path(temp_dir), day_start, week_start)
            path.write_text('{"later": true}', encoding="utf-8")
            second, _ = cache.summarize(Path(temp_dir), day_start, week_start)

        self.assertEqual(first.tokens, 28)
        self.assertEqual(second.tokens, 28)
        self.assertEqual(parser.loads, 1)

    def test_week_before_today_breakdown_subtracts_current_daily_window(self) -> None:
        week = UsageSummary(tokens=1190, input_tokens=800, cost_usd=119.142012)
        today = UsageSummary(tokens=202, input_tokens=150, cost_usd=20.226427)
        day_start = datetime(2026, 5, 29, 10, 0)
        week_start = datetime(2026, 5, 28, 10, 0)

        prior = usage_before_today_in_week(week, today, day_start, week_start)

        self.assertEqual(prior.tokens, 988)
        self.assertEqual(prior.input_tokens, 650)
        self.assertEqual(prior.cost_usd, 98.915585)

    def test_week_before_today_breakdown_is_empty_at_week_reset(self) -> None:
        week = UsageSummary(tokens=200, cost_usd=20.0)
        today = UsageSummary(tokens=200, cost_usd=20.0)
        start = datetime(2026, 5, 28, 10, 0)

        prior = usage_before_today_in_week(week, today, start, start)

        self.assertEqual(prior.tokens, 0)
        self.assertEqual(prior.cost_usd, 0.0)

    def test_snapshot_text_includes_week_breakdown(self) -> None:
        snapshot = ParsedSession(
            today_tokens=202,
            today_cost_usd=20.226427,
            week_tokens=1190,
            week_cost_usd=119.142012,
            week_before_today_tokens=988,
            week_before_today_cost_usd=98.915585,
            week_adjustment_usd=12.5,
        )

        text = snapshot_to_text(snapshot)

        self.assertIn("This Week Breakdown:", text)
        self.assertIn("before today reset $98.915585", text)
        self.assertIn("today $20.226427", text)
        self.assertIn("This Week Manual Adjustment: $12.500000", text)

    def test_hud_instance_lock_prevents_duplicate_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud.pid"
            mutex_name = f"Local\\codex_usage_hud_test_{os.getpid()}_{id(path)}"
            lock = HudInstanceLock(path, mutex_name=mutex_name)
            lock.acquire()
            try:
                self.assertEqual(path.read_text(encoding="utf-8"), str(os.getpid()))
                with self.assertRaises(HudAlreadyRunningError):
                    HudInstanceLock(path, mutex_name=mutex_name).acquire()
            finally:
                lock.release()

            self.assertFalse(path.exists())

    def test_stop_running_hud_clears_invalid_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud.pid"
            path.write_text("not-a-pid", encoding="utf-8")

            message = stop_running_hud(path)

            self.assertIn("No running", message)
            self.assertFalse(path.exists())

    def test_stop_running_hud_clears_stale_process_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud.pid"
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(0.1)"]
            )
            proc.wait(timeout=5)
            path.write_text(str(proc.pid), encoding="utf-8")

            message = stop_running_hud(path)

            self.assertIn("Removed stale", message)
            self.assertFalse(path.exists())


class AutoScrollHelpersTests(unittest.TestCase):
    def test_numeric_text_can_animate_when_template_matches(self) -> None:
        self.assertTrue(
            _can_animate_numeric_text(
                "↑10 ↻2 ↓3 ◇1 ∑13 $0.5000",
                "↑15 ↻4 ↓6 ◇2 ∑21 $0.7500",
            )
        )

    def test_numeric_text_interpolates_midpoint_values(self) -> None:
        text = _interpolate_numeric_text(
            "↑10 ↻2 ↓3 ◇1 ∑13 $0.5000",
            "↑20 ↻4 ↓9 ◇3 ∑29 $0.9000",
            0.5,
        )

        self.assertEqual(text, "↑15 ↻3 ↓6 ◇2 ∑21 $0.7000")

    def test_fixed_token_total_uses_stable_width_units(self) -> None:
        self.assertEqual(_fixed_token_total(516), "516")
        self.assertEqual(_fixed_token_total(295_000), "295k")
        self.assertEqual(_fixed_token_total(2_500_000), "2.5M")

    def test_copyable_tool_command_extracts_shell_command_field(self) -> None:
        snapshot = ParsedSession(
            slow=SlowSummary(
                slowest_tool_call=ToolCallTiming(
                    call_id="call_1",
                    name="shell_command",
                    args='{"command":"git status","timeout_ms":1000}',
                    start=datetime.now().astimezone(),
                    start_line=1,
                )
            )
        )

        self.assertEqual(_copyable_tool_command(snapshot), "git status")

    def test_copyable_gap_detail_contains_traceable_context(self) -> None:
        snapshot = ParsedSession(
            slow=SlowSummary(
                longest_gap_detail=GapTiming(
                    start=datetime.now().astimezone(),
                    end=datetime.now().astimezone(),
                    duration_seconds=24.8,
                    category="model_or_idle",
                    from_event="user:请总结这段代码",
                    to_event="reasoning:先看入口和数据流",
                    start_line=10,
                    end_line=11,
                )
            )
        )

        detail = _copyable_gap_detail(snapshot)

        self.assertIsNotNone(detail)
        self.assertIn("类型: 模型思考", detail)
        self.assertIn("结束事件: reasoning:先看入口和数据流", detail)

    def test_request_total_line_starts_with_aligned_money_and_total(self) -> None:
        snapshot = ParsedSession()
        snapshot.request.input_tokens = 194_000
        snapshot.request.cached_tokens = 93_000
        snapshot.request.output_tokens = 852
        snapshot.request.reasoning_tokens = 516
        snapshot.request.total_tokens = 295_000
        snapshot.request.cost_usd = 0.094
        snapshot.request.estimated = False

        self.assertTrue(_request_total_line(snapshot).startswith("$0.094 ∑295k"))

    def test_request_total_line_includes_session_cache_hit_rate(self) -> None:
        snapshot = ParsedSession()
        snapshot.request.input_tokens = 194_000
        snapshot.request.cached_tokens = 93_000
        snapshot.request.output_tokens = 852
        snapshot.request.reasoning_tokens = 516
        snapshot.request.total_tokens = 295_000
        snapshot.request.cost_usd = 0.094
        snapshot.request.estimated = False

        self.assertIn("◎48%", _request_total_line(snapshot))

    def test_round_entry_puts_natural_sequence_money_and_total_first(self) -> None:
        item = RequestRound(
            index=33,
            status="confirmed",
            model="gpt-5.4",
            input_tokens=194_000,
            cached_tokens=93_000,
            output_tokens=852,
            reasoning_tokens=516,
            total_tokens=295_000,
            estimated=False,
            cost_usd=0.094,
            started_at=datetime(2026, 5, 28, 20, 36, 26).astimezone(),
        )

        self.assertTrue(_round_entry(item, "gpt-5.4").startswith("#33 $0.094 ∑295k"))

    def test_round_entry_includes_round_cache_hit_rate(self) -> None:
        item = RequestRound(
            index=33,
            status="confirmed",
            model="gpt-5.4",
            input_tokens=194_000,
            cached_tokens=93_000,
            output_tokens=852,
            reasoning_tokens=516,
            total_tokens=295_000,
            estimated=False,
            cost_usd=0.094,
        )

        self.assertIn("◎48%", _round_entry(item, "gpt-5.4"))

    def test_round_entry_prefers_completed_time_for_confirmed_rounds(self) -> None:
        item = RequestRound(
            index=1,
            status="confirmed",
            model="gpt-5.4",
            input_tokens=1_000,
            cached_tokens=0,
            output_tokens=10,
            reasoning_tokens=0,
            total_tokens=1_010,
            estimated=False,
            cost_usd=0.1,
            started_at=datetime(2026, 5, 28, 20, 0, 0).astimezone(),
            completed_at=datetime(2026, 5, 28, 20, 1, 30).astimezone(),
        )

        entry = _round_entry(item, "gpt-5.4")

        self.assertIn("20:01:30", entry)
        self.assertNotIn("20:00:00", entry)

    def test_round_entry_uses_elapsed_seconds_for_running_rounds(self) -> None:
        started_at = datetime(2026, 5, 28, 20, 0, 0).astimezone()
        item = RequestRound(
            index=1,
            status="running",
            model="gpt-5.4",
            input_tokens=1_000,
            cached_tokens=0,
            output_tokens=10,
            reasoning_tokens=0,
            total_tokens=1_010,
            estimated=True,
            cost_usd=0.1,
            started_at=started_at,
        )

        entry = _round_entry(
            item,
            "gpt-5.4",
            now=started_at + timedelta(seconds=42),
        )

        self.assertIn("42s", entry)
        self.assertNotIn("20:00:00", entry)

    def test_round_entry_uses_dynamic_widths_without_leading_zeroes(self) -> None:
        rows = [
            RequestRound(
                index=9,
                status="confirmed",
                model="gpt-5.4",
                input_tokens=1_000,
                cached_tokens=0,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=2_000,
                estimated=False,
                cost_usd=0.1,
            ),
            RequestRound(
                index=128,
                status="confirmed",
                model="gpt-5.4",
                input_tokens=1_000_000,
                cached_tokens=0,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=1_200_000,
                estimated=False,
                cost_usd=12.34,
            ),
        ]
        widths = _round_entry_widths(rows, "gpt-5.4")

        first = _round_entry(rows[0], "gpt-5.4", index_width=widths[0], money_width=widths[1], total_width=widths[2])
        second = _round_entry(rows[1], "gpt-5.4", index_width=widths[0], money_width=widths[1], total_width=widths[2])

        self.assertTrue(first.startswith("#  9 $0.100 ∑2,000"))
        self.assertTrue(second.startswith("#128 $12.34 ∑ 1.2M"))


class HudSettingsStoreTests(unittest.TestCase):
    def test_settings_round_trip_persists_position_and_width(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            store = HudSettingsStore(path)
            settings = HudSettings(
                top=WindowPlacement(
                    relative_x=460,
                    relative_y=44,
                    absolute_x=900,
                    absolute_y=50,
                    width=640,
                    height=390,
                    width_ratio=0.75,
                    anchor_x_ratio=0.25,
                    anchor_y_ratio=0.5,
                    anchor_source="geometry",
                ),
                request=WindowPlacement(
                    relative_x=520,
                    relative_bottom=28,
                    absolute_x=920,
                    absolute_y=790,
                    width=420,
                    height=210,
                    width_ratio=1.1,
                    anchor_x_ratio=0.4,
                    anchor_y_ratio=0.0,
                    anchor_source="geometry",
                ),
            )

            store.save(settings)
            loaded = store.load()

        self.assertEqual(loaded.top.relative_x, 460)
        self.assertEqual(loaded.top.relative_y, 44)
        self.assertEqual(loaded.top.width, 640)
        self.assertEqual(loaded.top.height, 390)
        self.assertEqual(loaded.top.width_ratio, 0.75)
        self.assertEqual(loaded.top.anchor_x_ratio, 0.25)
        self.assertEqual(loaded.top.anchor_y_ratio, 0.5)
        self.assertEqual(loaded.top.anchor_source, "geometry")
        self.assertEqual(loaded.request.relative_bottom, 28)
        self.assertEqual(loaded.request.width, 420)
        self.assertEqual(loaded.request.height, 210)
        self.assertEqual(loaded.request.width_ratio, 1.1)
        self.assertEqual(loaded.request.anchor_x_ratio, 0.4)
        self.assertEqual(loaded.request.anchor_y_ratio, 0.0)
        self.assertEqual(loaded.request.anchor_source, "geometry")

    def test_geometry_save_preserves_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                '{"user":{"weekly_adjustment_usd":7.5},"future":{"keep":true}}',
                encoding="utf-8",
            )
            store = HudSettingsStore(path)

            store.save(HudSettings(top=WindowPlacement(width=320), request=WindowPlacement()))

            raw = path.read_text(encoding="utf-8")

        self.assertIn('"weekly_adjustment_usd": 7.5', raw)
        self.assertIn('"future"', raw)


class TokenHudWindowLifecycleTests(unittest.TestCase):
    def test_top_rebuild_does_not_destroy_bottom_request_window(self) -> None:
        window = TokenHudWindow()
        try:
            self.assertEqual(window.request_root.winfo_exists(), 1)

            window.toggle_top_expanded()
            self.assertEqual(window.request_root.winfo_exists(), 1)

            window.toggle_top_expanded()
            self.assertEqual(window.request_root.winfo_exists(), 1)
        finally:
            window._close()

    def test_settings_button_opens_settings_dialog_directly(self) -> None:
        window = TokenHudWindow()
        try:
            window._open_settings_dialog = MagicMock()
            button = window._settings_button(window.root)

            button.invoke()

            window._open_settings_dialog.assert_called_once_with()
        finally:
            window._close()

    def test_settings_dialog_matches_renderer_modal_structure(self) -> None:
        window = TokenHudWindow()
        try:
            window._open_settings_dialog()
            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            widgets = _walk_widgets(dialog)
            button_texts = {
                str(widget.cget("text"))
                for widget in widgets
                if isinstance(widget, tk.Button)
            }

            self.assertFalse(any(isinstance(widget, ttk.Notebook) for widget in widgets))
            self.assertTrue(dialog.overrideredirect())
            width, height, x, y = _parse_tk_geometry(dialog.geometry())
            self.assertEqual(width, SETTINGS_DIALOG_WIDTH)
            self.assertEqual(height, SETTINGS_DIALOG_HEIGHT)
            self.assertAlmostEqual(x, (dialog.winfo_screenwidth() - width) // 2, delta=2)
            self.assertAlmostEqual(y, (dialog.winfo_screenheight() - height) // 2, delta=2)
            self.assertIn("设置", button_texts)
            self.assertIn("请作者喝咖啡", button_texts)
            self.assertIn("拉取价格", button_texts)
            self.assertIn("导出 JSON", button_texts)
            self.assertIn("保存", button_texts)
            self.assertIn("display_mode", window._settings_entries)
            self.assertTrue(window._settings_price_rows)

            window._select_settings_tab("support")
            support_buttons = {
                str(widget.cget("text"))
                for widget in _walk_widgets(dialog)
                if isinstance(widget, tk.Button)
            }
            support_images = [
                widget
                for widget in _walk_widgets(dialog)
                if isinstance(widget, tk.Label) and str(widget.cget("image"))
            ]
            self.assertNotIn("打开图片", support_buttons)
            self.assertIn("关闭", support_buttons)
            self.assertGreaterEqual(len(support_images), 2)
            self.assertEqual(len(window._settings_support_images), 2)
            canvas = window._settings_canvas
            self.assertIsNotNone(canvas)
            assert canvas is not None
            with patch.object(canvas, "yview_scroll") as scroll:
                result = window._scroll_settings_body(SimpleNamespace(delta=-120, num=None))
            self.assertEqual(result, "break")
            scroll.assert_called_once_with(1, "units")
        finally:
            window._close()

    def test_tk_dom_anchors_are_opt_in_for_smooth_fallback(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(HUD_CDP_DOM_ENV, None)
            window = TokenHudWindow()
            try:
                self.assertFalse(window._use_dom_anchors)
                self.assertTrue(window._use_top_dom_anchors)
            finally:
                window._close()

        with patch.dict(os.environ, {HUD_CDP_DOM_ENV: "1"}, clear=False):
            window = TokenHudWindow()
            try:
                self.assertTrue(window._use_dom_anchors)
            finally:
                window._close()

    def test_top_uses_default_dom_anchor_without_enabling_request_anchor(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "top": HudAnchor(
                        left=320,
                        top=80,
                        right=1180,
                        bottom=126,
                        default_x=320,
                        default_y=86,
                        default_width=860,
                        source="cdp:title",
                    ),
                    "request": HudAnchor(
                        left=600,
                        top=720,
                        right=1200,
                        bottom=776,
                        default_x=600,
                        default_y=688,
                        default_width=600,
                        source="cdp:composer",
                    ),
                }
            )
            window._use_dom_anchors = False
            window._use_native_anchors = False
            window._use_top_dom_anchors = True
            window.settings.top = WindowPlacement()
            window.settings.request = WindowPlacement()

            top = _attached_geometry_after_stable(window, "top", rect)
            request = _attached_geometry_after_stable(window, "request", rect)

            self.assertEqual(top, (320, 86, 860, TOP_DOCK_HEIGHT))
            self.assertNotEqual(request[:3], (600, 688, 600))
        finally:
            window._close()

    def test_top_dom_anchor_updates_when_title_region_changes(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            first_anchor = HudAnchor(
                left=300,
                top=80,
                right=1180,
                bottom=126,
                default_x=300,
                default_y=86,
                default_width=880,
                source="cdp:title",
            )
            second_anchor = HudAnchor(
                left=460,
                top=80,
                right=1180,
                bottom=126,
                default_x=460,
                default_y=86,
                default_width=720,
                source="cdp:title",
            )
            locator = _FakeAnchorLocator({"top": first_anchor})
            window.locator = locator
            window._use_dom_anchors = False
            window._use_native_anchors = False
            window._use_top_dom_anchors = True
            window.settings.top = WindowPlacement()

            first = _attached_geometry_after_stable(window, "top", rect)
            locator.anchors["top"] = second_anchor
            second = _attached_geometry_after_stable(window, "top", rect)

            self.assertEqual(first, (300, 86, 880, TOP_DOCK_HEIGHT))
            self.assertEqual(second, (460, 86, 720, TOP_DOCK_HEIGHT))
        finally:
            window._close()

    def test_collapsed_top_bar_shows_session_cache_hit_rate(self) -> None:
        window = TokenHudWindow()
        try:
            snapshot = ParsedSession()
            snapshot.confirmed.cumulative_total = 451_844
            snapshot.confirmed.cumulative_input = 429_843
            snapshot.confirmed.cumulative_cached = 374_400
            snapshot.today_tokens = 41_100_000
            snapshot.today_cost_usd = 39.31
            snapshot.week_tokens = 159_500_000
            snapshot.week_cost_usd = 138.23

            window.update_display(snapshot)

            self.assertIn("命中 ◎87%", window.top_labels["bar"].cget("text"))
        finally:
            window._close()

    def test_request_expanded_rows_show_round_cache_hit_rate(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_request_expanded()
            snapshot = ParsedSession()
            snapshot.request.model = "gpt-5.4"
            snapshot.request_history = [
                RequestRound(
                    index=1,
                    status="confirmed",
                    model="gpt-5.4",
                    input_tokens=1_000,
                    cached_tokens=500,
                    output_tokens=20,
                    reasoning_tokens=0,
                    total_tokens=1_020,
                    estimated=False,
                    cost_usd=0.1,
                ),
                RequestRound(
                    index=2,
                    status="confirmed",
                    model="gpt-5.4",
                    input_tokens=2_000,
                    cached_tokens=500,
                    output_tokens=50,
                    reasoning_tokens=10,
                    total_tokens=2_050,
                    estimated=False,
                    cost_usd=0.2,
                ),
            ]

            window.update_display(snapshot)
            window.root.update_idletasks()
            request_rows = window.request_text.get("1.0", "end-1c")

            self.assertIn("◎50%", request_rows)
            self.assertIn("◎25%", request_rows)
        finally:
            window._close()

    def test_attached_geometry_scales_with_saved_width_ratio(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.settings.top.relative_x = None
            window.settings.top.relative_y = None
            window.settings.top.relative_x_ratio = None
            window.settings.top.relative_y_ratio = None
            window.settings.top.anchor_x_ratio = None
            window.settings.top.anchor_y_ratio = None
            window.settings.top.anchor_source = None
            window.settings.top.width_ratio = 0.5
            x, y, width, height = window._attached_geometry("top", rect, False)

            self.assertEqual((x, y, height), (286, 88, 36))
            self.assertEqual(width, 421)
        finally:
            window._close()

    def test_move_saves_anchor_position_without_changing_width_ratio(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=520,
                        top=700,
                        right=1020,
                        bottom=756,
                        default_x=520,
                        default_y=668,
                        default_width=500,
                        source="test-input",
                    )
                }
            )
            window._use_native_anchors = True
            window._attached = True
            window._last_rect = rect
            window.settings.request.width_ratio = 0.75
            _attached_geometry_after_stable(window, "request", rect)

            window._remember_window_position(
                "request",
                _FakeWindow(x=650, y=660, width=320, height=32),
                reason="test-move",
            )

            self.assertEqual(window.settings.request.width_ratio, 0.75)
            self.assertAlmostEqual(window.settings.request.anchor_x_ratio or 0.0, 0.26)
            self.assertAlmostEqual(window.settings.request.anchor_y_ratio or 0.0, -0.142857, places=5)
        finally:
            window._close()

    def test_resize_saves_width_ratio_against_current_anchor_width(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "top": HudAnchor(
                        left=260,
                        top=80,
                        right=1060,
                        bottom=126,
                        default_x=260,
                        default_y=85,
                        default_width=800,
                        source="test-title",
                    )
                }
            )
            window._use_native_anchors = True
            window._attached = True
            window._last_rect = rect
            _attached_geometry_after_stable(window, "top", rect)

            fake = _FakeWindow(x=420, y=92, width=400, height=36)
            window._remember_window_position("top", fake, reason="test-resize")
            window._remember_window_width("top", fake, reason="test-resize")

            self.assertAlmostEqual(window.settings.top.width_ratio or 0.0, 0.5)
            self.assertAlmostEqual(window.settings.top.anchor_x_ratio or 0.0, 0.2)
            self.assertAlmostEqual(window.settings.top.anchor_y_ratio or 0.0, 12 / 46)
        finally:
            window._close()

    def test_request_anchor_tracks_input_box_changes(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.settings.request.anchor_x_ratio = 0.2
            window.settings.request.anchor_y_ratio = 0.0
            window.settings.request.anchor_source = "test-input"
            window.settings.request.width_ratio = 0.5
            window.settings.request.width = None
            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=600,
                        top=720,
                        right=1200,
                        bottom=776,
                        default_x=600,
                        default_y=688,
                        default_width=600,
                        source="test-input",
                    )
                }
            )
            window._use_native_anchors = True

            x, y, width, height = _attached_geometry_after_stable(
                window,
                "request",
                rect,
            )

            self.assertEqual(height, 32)
            self.assertEqual((x, y, width), (720, 688, 300))
        finally:
            window._close()

    def test_native_anchor_is_disabled_by_default_for_scroll_stability(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=900,
                        top=300,
                        right=1200,
                        bottom=340,
                        default_x=900,
                        default_y=268,
                        default_width=300,
                        source="test-moving-uia",
                    )
                }
            )
            window.settings.request.anchor_x_ratio = 0.0
            window.settings.request.anchor_y_ratio = 0.0
            window.settings.request.anchor_source = "test-moving-uia"
            window.settings.request.width = None
            window.settings.request.width_ratio = None
            window.settings.request.relative_x_ratio = None
            window.settings.request.relative_bottom_ratio = None
            window._use_dom_anchors = False

            x, y, width, height = window._attached_geometry("request", rect, False)

            self.assertEqual((x, y, width, height), (460, 782, 495, 32))
        finally:
            window._close()

    def test_native_anchor_waits_for_stable_frames(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=600,
                        top=720,
                        right=1200,
                        bottom=776,
                        default_x=600,
                        default_y=688,
                        default_width=600,
                        source="test-input",
                    )
                }
            )
            window._use_native_anchors = True
            window.settings.request = WindowPlacement()

            first = window._attached_geometry("request", rect, False)
            stable = _attached_geometry_after_stable(window, "request", rect)
            moved_rect = WindowRect(left=140, top=90, right=1340, bottom=890)
            translated = window._attached_geometry("request", moved_rect, False)

            self.assertNotEqual(first, (600, 688, 600, 32))
            self.assertEqual(stable, (600, 688, 600, 32))
            self.assertEqual(translated, (640, 728, 600, 32))
        finally:
            window._close()

    def test_stable_anchor_is_projected_during_resize_gate(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=600,
                        top=720,
                        right=1200,
                        bottom=776,
                        default_x=600,
                        default_y=688,
                        default_width=600,
                        source="test-input",
                    )
                }
            )
            window._use_native_anchors = True
            window.settings.request = WindowPlacement()
            stable = _attached_geometry_after_stable(window, "request", rect)
            self.assertEqual(stable, (600, 688, 600, 32))

            window.locator = _FakeAnchorLocator(
                {
                    "request": HudAnchor(
                        left=680,
                        top=790,
                        right=1380,
                        bottom=853,
                        default_x=680,
                        default_y=758,
                        default_width=700,
                        source="test-input",
                    )
                }
            )
            resized_rect = WindowRect(left=100, top=50, right=1500, bottom=950)

            projected = window._attached_geometry("request", resized_rect, False)

            self.assertNotEqual(projected, (520, 900, 588, 32))
            self.assertEqual(projected, (683, 768, 700, 32))
        finally:
            window._close()

    def test_window_geometry_is_not_reapplied_when_unchanged(self) -> None:
        window = TokenHudWindow()
        try:
            fake = _RecordingGeometryWindow()

            window._apply_window_geometry("top", fake, (10, 20, 300, 32))
            window._apply_window_geometry("top", fake, (10, 20, 300, 32))
            window._apply_window_geometry("top", fake, (11, 20, 300, 32))

            self.assertEqual(fake.calls, ["300x32+10+20", "300x32+11+20"])
        finally:
            window._close()

    def test_attached_geometry_clamp_does_not_rewrite_saved_width_ratio(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=700, bottom=650)
            window.locator = _FakeAnchorLocator(
                {
                    "top": HudAnchor(
                        left=140,
                        top=70,
                        right=640,
                        bottom=116,
                        default_x=140,
                        default_y=75,
                        default_width=500,
                        source="test-title",
                    )
                }
            )
            window._use_native_anchors = True
            window.settings.top.anchor_x_ratio = 0.9
            window.settings.top.anchor_y_ratio = 0.0
            window.settings.top.anchor_source = "test-title"
            window.settings.top.width_ratio = 2.0

            x, _, width, _ = _attached_geometry_after_stable(window, "top", rect)

            self.assertLessEqual(x + width, rect.right - 12)
            self.assertEqual(window.settings.top.width_ratio, 2.0)
        finally:
            window._close()

    def test_interactive_min_width_keeps_handles_visible(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {
                    "top": HudAnchor(
                        left=260,
                        top=80,
                        right=1060,
                        bottom=126,
                        default_x=260,
                        default_y=85,
                        default_width=800,
                        source="test-title",
                    )
                }
            )
            window._use_native_anchors = True
            window.settings.top.anchor_x_ratio = 0.0
            window.settings.top.anchor_y_ratio = 0.0
            window.settings.top.anchor_source = "test-title"
            window.settings.top.width = 1
            window.settings.top.width_ratio = None

            _, _, width, _ = _attached_geometry_after_stable(window, "top", rect)

            self.assertGreaterEqual(width, 120)
        finally:
            window._close()

    def test_expanded_height_can_be_saved_and_reused(self) -> None:
        window = TokenHudWindow()
        try:
            window.top_expanded = True
            fake = _FakeWindow(x=20, y=20, width=420, height=455)

            window._remember_window_height("top", fake, reason="test-resize")

            self.assertEqual(window.settings.top.height, 455)
            self.assertEqual(window._top_size()[1], 455)
        finally:
            window._close()

    def test_attached_geometry_uses_saved_expanded_height(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.settings.top.height = 430

            _, _, _, height = window._attached_geometry("top", rect, True)

            self.assertEqual(height, 430)
        finally:
            window._close()

    def test_top_expanded_body_wraps_and_scrolls_long_content(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            window.root.geometry(f"360x{TOP_DOCK_EXPANDED_HEIGHT}+20+20")
            now = datetime(2026, 5, 29, 12, 56, 25).astimezone()
            snapshot = ParsedSession(
                session_id="1704a2cec6fd",
                status="live",
                refreshed_at=now,
                last_event_time=now,
                line_count=115,
                token_events=11,
                today_tokens=41_100_000,
                today_cost_usd=39.31,
                week_tokens=159_500_000,
                week_cost_usd=138.23,
                week_before_today_tokens=118_400_000,
                week_before_today_cost_usd=98.92,
                day_start=now,
                week_start=now,
                budget_warnings=[
                    "日额度已用 39.31/100 USD，接近本次高峰后仍需完整显示这条很长的提醒内容"
                ],
            )
            snapshot.confirmed.cumulative_total = 451_844
            snapshot.confirmed.cumulative_input = 429_843
            snapshot.confirmed.cumulative_cached = 374_400
            snapshot.confirmed.cumulative_output = 22_001
            snapshot.confirmed.cumulative_reasoning = 16_612
            snapshot.request.input_tokens = 43_000
            snapshot.request.cached_tokens = 41_000
            snapshot.request.output_tokens = 41_000
            snapshot.request.reasoning_tokens = 100
            snapshot.request.total_tokens = 43_000
            snapshot.request.cost_usd = 0.004
            snapshot.request.estimated = True
            snapshot.activity.kind = "agent"
            snapshot.activity.detail = (
                "我现在提交这次发布资产，提交说明会按仓库的 Lore 协议来写，"
                "这是一段足够长的当前活动内容，用来证明布局不会吞掉信息。"
            )
            snapshot.slow.slowest_tool = (
                "2.6s shell_command: pytest tests/test_ui.py --very-long-option-name "
                "with extra diagnostic context"
            )
            snapshot.slow.slowest_user_wait = "无（本任务无用户确认）"
            snapshot.slow.longest_gap = "12.5s（模型启动）"
            snapshot.slow.current_gap = "距最后事件 0.4s"
            snapshot.slow.current_gap_active = True

            window.update_display(snapshot)
            window.root.update_idletasks()

            canvases = [
                widget
                for widget in _walk_widgets(window.root)
                if isinstance(widget, tk.Canvas) and widget.winfo_toplevel() == window.root
            ]
            self.assertEqual(len(canvases), 1)
            canvas = canvases[0]
            scroll_region = canvas.bbox("all")

            self.assertIsNotNone(scroll_region)
            self.assertGreater(scroll_region[3], canvas.winfo_height())
            self.assertEqual(window.top_labels["legend"].cget("text"), TOKEN_LEGEND_TEXT)
            self.assertIn("命中 ◎87%", window.top_labels["cumulative"].cget("text"))
            self.assertIn("◎87%", window.request_label.cget("text"))
            for key in ("budget", "activity", "warnings", "legend", "slow", "gap", "status"):
                label = window.top_labels[key]
                wraplength = int(float(str(label.cget("wraplength"))))
                self.assertGreaterEqual(wraplength, 96)
                self.assertLessEqual(wraplength, max(96, label.winfo_width()))
            self.assertLess(
                window.top_labels["warnings"].winfo_rooty(),
                window.top_labels["slow"].winfo_rooty(),
            )
            self.assertGreater(
                window.top_labels["legend"].winfo_rooty(),
                window.top_labels["status"].winfo_rooty(),
            )

            before = canvas.yview()
            canvas.yview_moveto(1.0)
            after = canvas.yview()
            self.assertGreater(after[0], before[0])
        finally:
            window._close()

    def test_top_expanded_header_prefers_session_title_with_fallback(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            snapshot = ParsedSession(session_title="Ship the live session switch check")

            window.update_display(snapshot)
            self.assertEqual(
                window.top_labels["title"].cget("text"),
                "Ship the live session switch check",
            )

            snapshot.session_title = ""
            window.update_display(snapshot)
            self.assertEqual(
                window.top_labels["title"].cget("text"),
                "Codex 会话 / 预算",
            )
        finally:
            window._close()

    def test_auto_scroll_label_updates_text_without_crashing(self) -> None:
        root = TokenHudWindow()
        try:
            label = AutoScrollLabel(
                root.root,
                text="short",
                animate_numbers=True,
            )
            label.pack(fill="x")
            label.set_text("↑10 ↻2 ↓3 ◇1 ∑13 $0.5000")
            label.set_text("↑20 ↻4 ↓9 ◇3 ∑29 $0.9000")
            root.root.update_idletasks()
        finally:
            root._close()

    def test_tombstone_mode_hides_and_throttles_refresh_work(self) -> None:
        window = TokenHudWindow()
        try:
            self.assertTrue(window.should_refresh_snapshot())

            window._enter_tombstone()

            self.assertFalse(window.should_refresh_snapshot())
            self.assertGreaterEqual(window.refresh_delay_ms(100), 500)

            window._exit_tombstone()

            self.assertTrue(window.should_refresh_snapshot())
        finally:
            window._close()

    def test_minimized_hidden_hud_reappears_when_codex_is_active_again(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator({}, active=True)

            window._hide_for_minimized()
            self.assertFalse(window.should_refresh_snapshot())
            self.assertTrue(window._hidden_for_minimized)

            window._attach_to_rect(rect)

            self.assertTrue(window.should_refresh_snapshot())
            self.assertFalse(window._hidden_for_minimized)
            self.assertFalse(window._tombstoned)
        finally:
            window._close()

    def test_inactive_tombstone_reappears_after_active_check_turns_true(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            locator = _FakeAnchorLocator({}, active=False)
            window.locator = locator

            window._attach_to_rect(rect)
            self.assertFalse(window.should_refresh_snapshot())
            self.assertEqual(window._hidden_reason, "inactive")

            locator.active = True
            window._attach_to_rect(rect)

            self.assertTrue(window.should_refresh_snapshot())
            self.assertEqual(window._hidden_reason, "")
            self.assertFalse(window._tombstoned)
        finally:
            window._close()


class DaemonLifecycleTests(unittest.TestCase):
    def test_renderer_settings_command_merges_partial_save_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            existing = UserConfig.defaults()
            existing.daily_budget_usd = 12.34
            existing.weekly_budget_usd = 56.78
            existing.daily_reset_time = "10:00"
            store.save(existing)
            reload_calls = 0

            def reload_user_config() -> None:
                nonlocal reload_calls
                reload_calls += 1

            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=reload_user_config,
            )
            restart_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {"action": "save", "settings": {"daily_reset_time": "09:30"}},
                context,
                restart_requested,
            )
            saved = store.load()

        self.assertEqual(status["kind"], "")
        self.assertEqual(status["restartVisible"], True)
        self.assertEqual(saved.daily_reset_time, "09:30")
        self.assertEqual(saved.daily_budget_usd, 12.34)
        self.assertEqual(saved.weekly_budget_usd, 56.78)
        self.assertIsNone(context.settings_mtime)
        self.assertEqual(reload_calls, 1)
        restart_requested.set.assert_not_called()

    def test_tk_refresh_reloads_user_config_before_snapshot(self) -> None:
        fake_context = SimpleNamespace(
            poll_ms=250,
            close=MagicMock(),
            reload_user_config=MagicMock(),
        )
        fake_window = SimpleNamespace(
            exit_reason="",
            root=SimpleNamespace(after=lambda *args, **kwargs: None),
            should_refresh_snapshot=lambda: True,
            refresh_delay_ms=lambda normal_delay_ms: normal_delay_ms,
            run=lambda: None,
            update_display=MagicMock(),
        )
        args = SimpleNamespace(compact=False)

        with (
            patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
            patch("codex_usage_hud.cli.TokenHudWindow", return_value=fake_window),
            patch(
                "codex_usage_hud.cli.build_snapshot",
                return_value=ParsedSession(status="parsed"),
            ) as build_snapshot,
        ):
            exit_code = run_tk_hud_session(args, lock_already_held=True)

        self.assertEqual(exit_code, 0)
        fake_context.reload_user_config.assert_called_once()
        build_snapshot.assert_called_once_with(fake_context)
        fake_window.update_display.assert_called_once()
        fake_context.close.assert_called_once()

    def test_main_defaults_to_renderer_first_from_auto_config(self) -> None:
        config = UserConfig.defaults()

        with (
            patch("codex_usage_hud.cli.UserConfigStore") as store_class,
            patch("codex_usage_hud.cli.run_hud_session", return_value=0) as run_session,
        ):
            store_class.return_value.load.return_value = config
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        args = run_session.call_args.args[0]
        self.assertTrue(args.renderer_hud)

    def test_hud_mode_renderer_overrides_tk_config_for_renderer_first(self) -> None:
        config = UserConfig.defaults()
        config.display_mode = "tk"

        with (
            patch("codex_usage_hud.cli.UserConfigStore") as store_class,
            patch("codex_usage_hud.cli.run_hud_session", return_value=0) as run_session,
        ):
            store_class.return_value.load.return_value = config
            exit_code = main(["--hud-mode", "renderer"])

        self.assertEqual(exit_code, 0)
        args = run_session.call_args.args[0]
        self.assertTrue(args.renderer_hud)

    def test_tk_config_skips_renderer_path(self) -> None:
        config = UserConfig.defaults()
        config.display_mode = "tk"

        with (
            patch("codex_usage_hud.cli.UserConfigStore") as store_class,
            patch("codex_usage_hud.cli.run_hud_session", return_value=0) as run_session,
        ):
            store_class.return_value.load.return_value = config
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        args = run_session.call_args.args[0]
        self.assertFalse(args.renderer_hud)

    def test_renderer_first_falls_back_to_tk_when_injection_unavailable(self) -> None:
        args = SimpleNamespace(renderer_hud=True)

        with (
            patch(
                "codex_usage_hud.cli.run_renderer_hud_session",
                return_value=RENDERER_HUD_UNAVAILABLE,
            ) as renderer_session,
            patch("codex_usage_hud.cli.run_tk_hud_session", return_value=0) as tk_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, 0)
        renderer_session.assert_called_once()
        tk_session.assert_called_once()

    def test_renderer_initial_connect_failure_writes_diagnostic_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_context = SimpleNamespace(
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="failed",
                last_error="TimeoutError: Timed out waiting for CDP command response",
                close=MagicMock(),
                timeout_seconds=1.5,
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"

            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=False),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

            diagnostic = (temp_root / "renderer_fallback.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        self.assertIn("initial_connect_failed", diagnostic)
        self.assertIn("Timed out waiting for CDP command response", diagnostic)
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_auto_mode_uses_faster_timeout_failure_limit(self) -> None:
        self.assertEqual(
            _renderer_update_failure_limit(
                "auto",
                "URLError: <urlopen error timed out>",
            ),
            AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT,
        )
        self.assertEqual(
            _renderer_update_failure_limit(
                "renderer",
                "URLError: <urlopen error timed out>",
            ),
            RENDERER_UPDATE_FAILURE_LIMIT,
        )
        self.assertEqual(
            _renderer_update_failure_limit(
                "auto",
                "RuntimeError: renderer update function did not acknowledge payload",
            ),
            RENDERER_UPDATE_FAILURE_LIMIT,
        )

    def test_renderer_refresh_delay_slows_idle_snapshots_only(self) -> None:
        context = SimpleNamespace(poll_ms=500)
        idle_snapshot = ParsedSession(status="parsed")
        idle_snapshot.request.status = "confirmed"
        running_snapshot = ParsedSession(status="parsed")
        running_snapshot.request.status = "running"

        idle_delay = _renderer_refresh_delay_seconds(context, idle_snapshot, 0.0)
        running_delay = _renderer_refresh_delay_seconds(context, running_snapshot, 0.0)
        forced_delay = _renderer_refresh_delay_seconds(
            context,
            idle_snapshot,
            0.0,
            force_fast=True,
        )

        self.assertGreaterEqual(idle_delay, 1.5)
        self.assertAlmostEqual(running_delay, 0.5)
        self.assertAlmostEqual(forced_delay, 0.5)

    def test_renderer_runtime_failures_retry_without_tk_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                reload_user_config=MagicMock(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="failed",
                last_error="TimeoutError: renderer busy during paste",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=False),
                close=MagicMock(),
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"

            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=ParsedSession(status="parsed")),
                patch("codex_usage_hud.cli._renderer_update_failure_limit", return_value=1),
                patch("codex_usage_hud.cli.time.sleep", side_effect=KeyboardInterrupt),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

            diagnostic = (temp_root / "renderer_fallback.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(exit_code, 130)
        self.assertIn("runtime_update_failed_retrying", diagnostic)
        self.assertNotEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        fake_client.update.assert_called_once()
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_wait_for_visible_codex_window_returns_when_tracker_becomes_visible(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        visible=False,
                        status="not_found",
                        reason="Codex HWND not found",
                        hwnd=0,
                    ),
                    SimpleNamespace(
                        visible=True,
                        status="visible",
                        reason="",
                        hwnd=123,
                    ),
                ]
            ),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _wait_for_visible_codex_window(
                timeout_seconds=1.0,
                poll_seconds=0.0,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 123)

    def test_wait_for_visible_codex_window_times_out_when_tracker_stays_hidden(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                return_value=SimpleNamespace(
                    visible=False,
                    status="hidden",
                    reason="Codex is hidden",
                    hwnd=456,
                )
            ),
        )

        with patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker):
            ready, status, reason, hwnd = _wait_for_visible_codex_window(
                timeout_seconds=0.0
            )

        self.assertFalse(ready)
        self.assertEqual(status, "hidden")
        self.assertEqual(reason, "Codex is hidden")
        self.assertEqual(hwnd, 456)

    def test_renderer_daemon_mode_waits_for_visible_window_before_connect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_context = SimpleNamespace(
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="failed",
                last_error="TimeoutError: Timed out waiting for CDP command response",
                close=MagicMock(),
                timeout_seconds=1.5,
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"

            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch(
                    "codex_usage_hud.cli._wait_for_visible_codex_window",
                    return_value=(False, "not_found", "Codex HWND not found", 0),
                ) as wait_window,
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=False) as wait_renderer,
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                    daemon_manager=SimpleNamespace(),
                )

            diagnostic = (temp_root / "renderer_fallback.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        wait_window.assert_called_once_with(
            timeout_seconds=DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS
        )
        wait_renderer.assert_not_called()
        self.assertIn("window_not_ready", diagnostic)
        self.assertIn("Codex HWND not found", diagnostic)
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_run_hud_session_returns_restart_code_when_codex_exits(self) -> None:
        fake_context = SimpleNamespace(poll_ms=250, close=MagicMock())
        fake_window = SimpleNamespace(
            exit_reason="daemon_codex_exited",
            root=SimpleNamespace(after=lambda *args, **kwargs: None),
            should_refresh_snapshot=lambda: False,
            refresh_delay_ms=lambda normal_delay_ms: normal_delay_ms,
            run=lambda: None,
            update_display=lambda snapshot: None,
        )
        args = SimpleNamespace(compact=False)
        daemon_manager = SimpleNamespace(poll_ms=250)

        with (
            patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
            patch("codex_usage_hud.cli.TokenHudWindow", return_value=fake_window),
        ):
            exit_code = run_hud_session(
                args,
                lock_already_held=True,
                hide_until_attached=True,
                daemon_manager=daemon_manager,
            )

        self.assertEqual(exit_code, DAEMON_RESTART_REQUESTED)
        fake_context.close.assert_called_once()

    def test_run_daemon_waits_for_next_codex_start_after_session_exit(self) -> None:
        fake_manager = SimpleNamespace(
            poll_ms=250,
            wait_for_codex=MagicMock(side_effect=[object(), object(), KeyboardInterrupt()]),
        )
        args = SimpleNamespace(daemon_poll_ms=250)
        lock_instance = MagicMock()
        lock_instance.__enter__.return_value = lock_instance
        lock_instance.__exit__.return_value = False

        with (
            patch("codex_usage_hud.cli.configure_daemon_logging", return_value=None),
            patch("codex_usage_hud.cli.hide_console_window", return_value=None),
            patch("codex_usage_hud.cli.CodexDaemonManager", return_value=fake_manager),
            patch("codex_usage_hud.cli.HudInstanceLock", return_value=lock_instance),
            patch(
                "codex_usage_hud.cli.run_hud_session",
                side_effect=[DAEMON_RESTART_REQUESTED, 0],
            ) as run_session,
        ):
            exit_code = run_daemon(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_manager.wait_for_codex.call_count, 2)
        self.assertEqual(run_session.call_count, 2)


if __name__ == "__main__":
    unittest.main()
