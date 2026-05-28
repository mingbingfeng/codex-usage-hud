"""Unit tests for HUD geometry and budget helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.cli import budget_warnings, parse_thresholds
from codex_usage_hud.core.parser import (
    GapTiming,
    ParsedSession,
    RequestRound,
    SlowSummary,
    ToolCallTiming,
)
from codex_usage_hud.ui.tk_hud import (
    REQUEST_DOCK_BOTTOM,
    REQUEST_DOCK_EXPANDED_HEIGHT,
    REQUEST_DOCK_HEIGHT,
    REQUEST_DOCK_LEFT,
    REQUEST_DOCK_RIGHT,
    REQUEST_DOCK_WIDTH,
    TOP_DOCK_EXPANDED_HEIGHT,
    TOP_DOCK_HEIGHT,
    TOP_DOCK_LEFT,
    TOP_DOCK_RIGHT,
    TOP_DOCK_TOP,
    AttachedHudGeometry,
    DockGeometry,
    HudSettings,
    HudSettingsStore,
    AutoScrollLabel,
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
        self.assertEqual(expanded, (556, 92, 520, 285))

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
                    width_ratio=0.75,
                ),
                request=WindowPlacement(
                    relative_x=520,
                    relative_bottom=28,
                    absolute_x=920,
                    absolute_y=790,
                    width=420,
                    width_ratio=1.1,
                ),
            )

            store.save(settings)
            loaded = store.load()

        self.assertEqual(loaded.top.relative_x, 460)
        self.assertEqual(loaded.top.relative_y, 44)
        self.assertEqual(loaded.top.width, 640)
        self.assertEqual(loaded.top.width_ratio, 0.75)
        self.assertEqual(loaded.request.relative_bottom, 28)
        self.assertEqual(loaded.request.width, 420)
        self.assertEqual(loaded.request.width_ratio, 1.1)


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

    def test_attached_geometry_scales_with_saved_width_ratio(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.settings.top.relative_x = None
            window.settings.top.relative_y = None
            window.settings.top.relative_x_ratio = None
            window.settings.top.relative_y_ratio = None
            window.settings.top.width_ratio = 0.5
            x, y, width, height = window._attached_geometry("top", rect, False)

            self.assertEqual((x, y, height), (286, 88, 36))
            self.assertEqual(width, 421)
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


if __name__ == "__main__":
    unittest.main()
