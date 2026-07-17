"""Unit tests for HUD geometry and budget helpers."""

from __future__ import annotations

import os
import re
import sys
import json
import math
import tempfile
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import codex_usage_hud.cli as cli_module
import codex_usage_hud.ui.qt_hud as qt_hud_module
import codex_usage_hud.ui.tk_hud as tk_hud_module
from codex_usage_hud.cli import (
    AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT,
    ACTIVE_WORK_STALE_SECONDS,
    DAEMON_STARTUP_RENDERER,
    DAEMON_RESTART_REQUESTED,
    DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS,
    DesktopWorkOverlay,
    HudAlreadyRunningError,
    HudInstanceLock,
    HUD_SWITCH_TO_RENDERER,
    HUD_SWITCH_TO_RENDERER_RESTART_CODEX,
    RENDERER_HUD_UNAVAILABLE,
    RENDERER_UPDATE_FAILURE_LIMIT,
    RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
    SessionSnapshotCache,
    UsageSummaryCache,
    _VisibleAppErrorCache,
    _TkSnapshotPump,
    _TkWorkOverlayCommandPump,
    _active_session_switch_pending,
    _apply_visible_app_error,
    _build_session_switch_controller,
    _enable_crash_diagnostics,
    active_work_items_for_snapshot,
    _prepare_codex_window_for_renderer,
    _prepare_codex_window_for_tk,
    _renderer_refresh_delay_seconds,
    _renderer_should_refresh_active_work_items,
    _wait_for_visible_codex_window,
    _renderer_update_failure_limit,
    _handle_renderer_settings_command,
    budget_warnings,
    launch_codex_app,
    main,
    parse_thresholds,
    run_renderer_hud_session,
    run_qt_hud_session,
    snapshot_to_text,
    run_daemon,
    run_hud_session,
    run_loading_feedback_helper,
    run_work_overlay_helper,
    run_tk_hud_session,
    cleanup_stale_loading_feedback_files,
    work_item_to_overlay_dict,
    stop_running_hud,
    usage_before_today_in_week,
)
from codex_usage_hud.platforms.session_switch import (
    SessionSwitchController,
    SessionSwitchRequest,
    SessionSwitchResult,
)
from codex_usage_hud.config import (
    UserConfig,
    UserConfigStore,
    dismiss_warning_for_today,
    warning_dismissed_today,
)
from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.core.runtime_errors import RuntimeErrorRegistry
from codex_usage_hud.platforms.cdp_probe import CdpDomSnapshot, CdpRect
from codex_usage_hud.platforms.codex_theme import CodexThemeExport, CodexThemeSnapshot, HudThemeTokens
from codex_usage_hud.ui import QtHudWindow
from codex_usage_hud.ui.qt_hud import _qt_contrast, _qt_stylesheet
from codex_usage_hud.ui.renderer_hud import payload_from_snapshot
from codex_usage_hud.core.parser import (
    Activity,
    GapTiming,
    JsonlSessionParser,
    ParsedSession,
    RequestRound,
    RequestTokens,
    SlowSummary,
    ToolCallTiming,
    UsageSummary,
    WorkStatusItem,
)
from codex_usage_hud.ui.tk_hud import (
    REQUEST_DOCK_BOTTOM,
    REQUEST_DOCK_EXPANDED_HEIGHT,
    REQUEST_DOCK_HEIGHT,
    REQUEST_DOCK_LEFT,
    REQUEST_DOCK_RIGHT,
    REQUEST_DOCK_WIDTH,
    HUD_CDP_DOM_ENV,
    SETTINGS_DIALOG_HEIGHT,
    SETTINGS_DIALOG_WIDTH,
    TOP_ACTIVITY_TRAIL_VIEWPORT_HEIGHT,
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
    HudScrollbar,
    AutoScrollLabel,
    ShimmerTextLabel,
    HUD_BG,
    HUD_HEADER_BG,
    HUD_PANEL_BORDER,
    HUD_PROGRESS_DAY,
    HUD_PROGRESS_DAY_END,
    HUD_PROGRESS_RADIUS,
    HUD_PROGRESS_TRACK,
    HUD_SHELL_RADIUS,
    HUD_TEXT,
    HUD_WINDOW_OUTSIDE,
    HUD_WINDOW_TRANSPARENT,
    RoundedHudShell,
    TopHudProgressMetric,
    TopHudProgressStrip,
    TokenHudWindow,
    WindowPlacement,
    WindowRect,
    _WindowsCodexLocator,
    _can_animate_numeric_text,
    _copyable_gap_detail,
    _copyable_tool_command,
    _budget_warning_summary,
    _fixed_token_total,
    _interpolate_numeric_text,
    _progress_fill_surface_rows,
    _progress_fill_surface_transparency_rows,
    _request_total_line,
    _round_entry,
    _round_entry_widths,
    _rounded_shell_surface_rows,
    _collapsed_progress_strip_should_scroll,
    _top_budget_progress_metrics,
    _top_collapsed_progress_metrics,
    _visual_anchor_geometry,
    _win32_region_api,
)
from codex_usage_hud.ui.work_overlay_qt import (
    WORK_OVERLAY_TEXT_WRAP_WIDTH,
    WORK_OVERLAY_TOP_OFFSET,
    _completed_badge_palette,
    _completed_pending_caption_opacity,
    _completed_pending_finish_progress,
    _completed_pending_launch_progress,
    _completed_pending_launch_scale,
    _completed_pending_particle_state,
    _completed_badge_restore_slot_moves,
    _completed_badge_slot_moves,
    _completed_badge_slot_rects,
    _completed_restore_staged_items,
    _card_height_circle_rect_for_rect,
    _card_yield_delay_ms,
    _card_yield_rect_for_circle_path,
    _detect_transition,
    _detect_transition_item_id,
    _energy_ring_rect_for_completed_rect,
    _find_item_rect,
    _find_item_position,
    _item_dismiss_key,
    _mark_item_dismissed,
    _multiline_elided_text,
    _overlay_payload_signature,
    _overlay_hover_hit_test,
    _ordered_overlay_items,
    _overlay_items_required_height,
    _overlay_window_top_y,
    _pending_workdir_window_rect,
    _point_in_inscribed_circle,
    _round_badge_palette,
    _remembered_card_rect_for_layout,
    _transition_palette,
    _transition_clearance_offset,
    _transition_hides_source_before_effect_reset,
    _transition_layout_width,
    _transition_rect_for_progress,
    _transition_required_height,
    _transition_slot_shift_progress,
    _theme_contrast_ratio,
    _visible_overlay_items,
    _workdir_link_hover_visible_for_item,
    _workdir_clickable_for_item,
    _workdir_link_pending_for_item,
    _work_overlay_header_text,
    _workdir_display_name,
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

    def winfo_rootx(self) -> int:
        return self._x

    def winfo_rooty(self) -> int:
        return self._y

    def geometry(self, value: str) -> None:
        match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", value)
        if match is None:
            raise AssertionError(f"unexpected geometry: {value!r}")
        width, height, x, y = match.groups()
        self._width = int(width)
        self._height = int(height)
        self._x = int(x)
        self._y = int(y)


class _RecordingGeometryWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def geometry(self, value: str) -> None:
        self.calls.append(value)


class _FakeAnchorLocator:
    def __init__(
        self,
        anchors: dict[str, HudAnchor],
        *,
        active: bool = True,
        header_roi: WindowRect | None = None,
        bottom_roi: WindowRect | None = None,
        window_rect: WindowRect | None = None,
    ) -> None:
        self.anchors = anchors
        self.active = active
        self.header_roi = header_roi
        self.bottom_roi = bottom_roi
        self.window_rect = window_rect
        self.header_roi_change_callback: Callable[[], None] | None = None
        self.header_roi_calls = 0
        self.bottom_roi_calls = 0

    def set_dpi_aware(self) -> None:
        return None

    def find(self) -> WindowRect | None:
        return self.window_rect

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

    def header_roi_geometry(self, rect: WindowRect) -> WindowRect | None:
        del rect
        self.header_roi_calls += 1
        return self.header_roi

    def bottom_roi_geometry(self, rect: WindowRect) -> WindowRect | None:
        del rect
        self.bottom_roi_calls += 1
        return self.bottom_roi

    def set_header_roi_change_callback(self, callback: Callable[[], None] | None) -> None:
        self.header_roi_change_callback = callback


def _attached_geometry_after_stable(
    window: TokenHudWindow,
    target: str,
    rect: WindowRect,
    expanded: bool = False,
) -> tuple[int, int, int, int]:
    return window._attached_geometry(target, rect, expanded)


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


class _FileBackedUsageParser:
    def __init__(self) -> None:
        self.loads: list[Path] = []

    def load_records_lenient(self, path: Path) -> list[dict[str, object]]:
        self.loads.append(path)
        parts = path.read_text(encoding="utf-8").strip().split(",")
        provider, tokens, cost = (
            (parts[0], parts[1], parts[2])
            if len(parts) == 3
            else ("unknown", parts[0], parts[1])
        )
        return [
            {"provider": provider, "tokens": float(tokens), "cost": float(cost)}
        ]

    def usage_events(self, records: list[dict[str, object]]) -> list[dict[str, object]]:
        return records

    def session_model_provider(self, records: list[dict[str, object]]) -> str:
        return str(records[0].get("provider") or "unknown")

    def summarize_usage_events(
        self,
        events: list[dict[str, object]],
        start: datetime,
    ) -> UsageSummary:
        del start
        return UsageSummary(
            tokens=sum(int(event["tokens"]) for event in events),
            cost_usd=round(sum(float(event["cost"]) for event in events), 6),
        )


def _walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
    widgets = [widget]
    for child in widget.winfo_children():
        widgets.extend(_walk_widgets(child))
    return widgets


def _flush_tk(window: TokenHudWindow, iterations: int = 3) -> None:
    for _ in range(iterations):
        window.root.update_idletasks()
        window.root.update()


def _settle_top_animation(window: TokenHudWindow) -> None:
    end = getattr(window, "_top_animation_end", None)
    if end is not None:
        window._settle_top_animation(end)
    _flush_tk(window)


def _stop_background_jobs(window: TokenHudWindow) -> None:
    for attr in ("_follow_job", "_settings_prewarm_job"):
        job = getattr(window, attr, None)
        if not job:
            continue
        try:
            window.root.after_cancel(job)
        except tk.TclError:
            pass
        setattr(window, attr, None)


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


def _last_renderer_diagnostic_record(text: str) -> dict[str, object]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("renderer_fallback.log had no records")
    return json.loads(lines[-1])


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

    def test_tk_budget_warning_summary_only_mentions_ratio_and_threshold(self) -> None:
        snapshot = ParsedSession()
        snapshot.today_cost_usd = 59.12
        snapshot.week_cost_usd = 210.0
        snapshot.daily_limit_usd = 85.0
        snapshot.weekly_limit_usd = 400.0
        snapshot.budget_warnings = [
            "日额度已用 59.12/85 USD (70%)，超过 50% 阈值",
            "周额度已用 210.00/400 USD (52%)，超过 50% 阈值",
        ]

        self.assertEqual(
            _budget_warning_summary(snapshot),
            "预警  日已用 70%，超过 50% 阈值；周已用 52%，超过 50% 阈值",
        )

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

    def test_usage_summary_cache_can_reuse_or_force_aggregate_scan(self) -> None:
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
            stale, _ = cache.summarize(
                Path(temp_dir),
                day_start,
                week_start,
                allow_stale=True,
            )
            forced, _ = cache.summarize(
                Path(temp_dir),
                day_start,
                week_start,
                force_rescan=True,
            )

        self.assertEqual(first.tokens, 28)
        self.assertEqual(stale.tokens, 28)
        self.assertEqual(forced.tokens, 28)
        self.assertEqual(parser.loads, 2)

    def test_usage_summary_cache_refreshes_current_file_without_full_rescan(self) -> None:
        parser = _FileBackedUsageParser()
        cache = UsageSummaryCache(  # type: ignore[arg-type]
            parser,
            min_rescan_seconds=60.0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir) / "sessions"
            sessions_root.mkdir()
            current = sessions_root / "current.jsonl"
            other = sessions_root / "other.jsonl"
            current.write_text("3190737,4.744937", encoding="utf-8")
            other.write_text("100,0.01", encoding="utf-8")
            day_start = datetime(2026, 6, 30, 0, 0)
            week_start = datetime(2026, 6, 29, 0, 0)

            first, _ = cache.summarize(sessions_root, day_start, week_start)
            current.write_text("3764042,5.165528", encoding="utf-8")
            refreshed, _ = cache.summarize(
                sessions_root,
                day_start,
                week_start,
                allow_stale=True,
                refresh_paths=(current,),
            )

        self.assertEqual(first.tokens, 3_190_837)
        self.assertEqual(first.cost_usd, 4.754937)
        self.assertEqual(refreshed.tokens, 3_764_142)
        self.assertEqual(refreshed.cost_usd, 5.175528)
        self.assertEqual(parser.loads.count(current), 2)
        self.assertEqual(parser.loads.count(other), 1)

    def test_usage_summary_cache_includes_archived_sessions_sibling(self) -> None:
        parser = _FakeUsageParser()
        cache = UsageSummaryCache(parser)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir) / "sessions"
            archived_root = Path(temp_dir) / "archived_sessions"
            sessions_root.mkdir()
            archived_root.mkdir()
            (sessions_root / "session.jsonl").write_text("{}", encoding="utf-8")
            (archived_root / "archived.jsonl").write_text("{}", encoding="utf-8")
            day_start = datetime(2026, 5, 28, 10, 0)
            week_start = datetime(2026, 5, 25, 10, 0)

            day_total, week_total = cache.summarize(sessions_root, day_start, week_start)

        self.assertEqual(day_total.tokens, 56)
        self.assertEqual(week_total.tokens, 50)
        self.assertEqual(parser.loads, 2)

    def test_usage_summary_cache_filters_cached_provider_contributions_without_rescan(self) -> None:
        parser = _FileBackedUsageParser()
        cache = UsageSummaryCache(  # type: ignore[arg-type]
            parser,
            min_rescan_seconds=60.0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir) / "sessions"
            sessions_root.mkdir()
            custom = sessions_root / "custom.jsonl"
            muyuan = sessions_root / "muyuan.jsonl"
            custom.write_text("custom,100,1.0", encoding="utf-8")
            muyuan.write_text("muyuan,200,2.0", encoding="utf-8")
            day_start = datetime(2026, 7, 16, 0, 0)
            week_start = datetime(2026, 7, 13, 0, 0)

            all_day, _ = cache.summarize(sessions_root, day_start, week_start)
            custom_day, _ = cache.summarize(
                sessions_root,
                day_start,
                week_start,
                allow_stale=True,
                included_providers={"custom"},
            )
            muyuan_day, _ = cache.summarize(
                sessions_root,
                day_start,
                week_start,
                allow_stale=True,
                included_providers={"muyuan"},
            )

        self.assertEqual((all_day.tokens, all_day.cost_usd), (300, 3.0))
        self.assertEqual((custom_day.tokens, custom_day.cost_usd), (100, 1.0))
        self.assertEqual((muyuan_day.tokens, muyuan_day.cost_usd), (200, 2.0))
        self.assertEqual(len(parser.loads), 2)

    def test_renderer_current_session_path_filter_matches_only_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "sessions" / "current.jsonl"
            other = root / "sessions" / "other.jsonl"
            current.parent.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            other.write_text("{}\n", encoding="utf-8")

            self.assertTrue(cli_module._paths_only_current_session({current}, current))
            self.assertFalse(
                cli_module._paths_only_current_session({current, other}, current)
            )
            self.assertFalse(cli_module._paths_only_current_session(set(), current))

    def test_renderer_budget_aggregate_skips_current_session_only_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "sessions" / "current.jsonl"
            current.parent.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(session_path=current)
            signature = ("sessions", 1, "day", "week")

            self.assertFalse(
                cli_module._renderer_should_refresh_budget_aggregate(
                    latest_snapshot=snapshot,
                    latest_budget_signature=signature,
                    budget_signature=signature,
                    file_change_reasons={"session", "sessions-root"},
                    file_change_paths={current},
                )
            )

    def test_renderer_budget_aggregate_skips_settings_only_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "sessions" / "current.jsonl"
            current.parent.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(session_path=current)
            signature = ("sessions", "day", "week")

            self.assertFalse(
                cli_module._renderer_should_refresh_budget_aggregate(
                    latest_snapshot=snapshot,
                    latest_budget_signature=signature,
                    budget_signature=signature,
                    file_change_reasons={"settings"},
                    file_change_paths={Path(temp_dir) / "hud_settings.json"},
                )
            )

    def test_renderer_budget_aggregate_skips_known_non_current_session_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            current = root / "current.jsonl"
            other = root / "other.jsonl"
            root.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            other.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(session_path=current)
            signature = ("sessions", 1, "day", "week")

            self.assertFalse(
                cli_module._renderer_should_refresh_budget_aggregate(
                    latest_snapshot=snapshot,
                    latest_budget_signature=signature,
                    budget_signature=signature,
                    file_change_reasons={"sessions-root"},
                    file_change_paths={other},
                )
            )

    def test_renderer_budget_aggregate_refreshes_for_unknown_sessions_root_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            current = root / "current.jsonl"
            root.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(session_path=current)
            signature = ("sessions", 1, "day", "week")

            self.assertTrue(
                cli_module._renderer_should_refresh_budget_aggregate(
                    latest_snapshot=snapshot,
                    latest_budget_signature=signature,
                    budget_signature=signature,
                    file_change_reasons={"sessions-root"},
                    file_change_paths=set(),
                )
            )

    def test_build_snapshot_can_skip_active_work_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(status="parsed", session_path=session_path)
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    session_id="",
                    session_file=None,
                    resolve=MagicMock(return_value=(session_path, "renderer:thread")),
                ),
                sessions_root=root,
                parser=SimpleNamespace(
                    parse_file_incremental=MagicMock(return_value=(snapshot, object()))
                ),
                current_session_tail_state=None,
                sse_tracker=None,
                active_session_tracker=None,
                visible_app_error_cache=SimpleNamespace(
                    resolve=MagicMock(return_value="")
                ),
                platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                user_config=UserConfig.defaults(),
                usage_cache=SimpleNamespace(
                    summarize=MagicMock(return_value=(UsageSummary(), UsageSummary()))
                ),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
            )

            with patch(
                "codex_usage_hud.cli.active_work_items_for_snapshot",
                return_value=[],
            ) as active_work:
                result = cli_module.build_snapshot(
                    context,
                    refresh_budget_aggregate=False,
                    refresh_active_work_items=False,
                )

        active_work.assert_not_called()
        self.assertEqual(result.active_work_items, [])
        summarize_kwargs = context.usage_cache.summarize.call_args.kwargs
        self.assertTrue(summarize_kwargs["allow_stale"])
        self.assertFalse(summarize_kwargs["force_rescan"])
        self.assertEqual(summarize_kwargs["refresh_paths"], (session_path,))

    def test_build_snapshot_can_refresh_known_non_current_budget_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "session.jsonl"
            other_path = root / "other.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            other_path.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(status="parsed", session_path=session_path)
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    session_id="",
                    session_file=None,
                    resolve=MagicMock(return_value=(session_path, "renderer:thread")),
                ),
                sessions_root=root,
                parser=SimpleNamespace(
                    parse_file_incremental=MagicMock(return_value=(snapshot, object()))
                ),
                current_session_tail_state=None,
                sse_tracker=None,
                active_session_tracker=None,
                visible_app_error_cache=SimpleNamespace(
                    resolve=MagicMock(return_value="")
                ),
                platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                user_config=UserConfig.defaults(),
                usage_cache=SimpleNamespace(
                    summarize=MagicMock(return_value=(UsageSummary(), UsageSummary()))
                ),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
            )

            cli_module.build_snapshot(
                context,
                refresh_budget_aggregate=False,
                refresh_budget_paths=(other_path,),
                refresh_active_work_items=False,
            )

        summarize_kwargs = context.usage_cache.summarize.call_args.kwargs
        self.assertTrue(summarize_kwargs["allow_stale"])
        self.assertFalse(summarize_kwargs["force_rescan"])
        self.assertEqual(summarize_kwargs["refresh_paths"], (other_path,))

    def test_build_snapshot_uses_incremental_parser_for_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(status="parsed", session_path=session_path)
            next_tail_state = object()
            parser = SimpleNamespace(
                parse_file=MagicMock(side_effect=AssertionError("full parse used")),
                parse_file_incremental=MagicMock(return_value=(snapshot, next_tail_state)),
            )
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    session_id="",
                    session_file=None,
                    resolve=MagicMock(return_value=(session_path, "renderer:thread")),
                ),
                sessions_root=root,
                parser=parser,
                current_session_tail_state=None,
                sse_tracker=None,
                active_session_tracker=None,
                visible_app_error_cache=SimpleNamespace(
                    resolve=MagicMock(return_value="")
                ),
                platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                user_config=UserConfig.defaults(),
                usage_cache=SimpleNamespace(
                    summarize=MagicMock(return_value=(UsageSummary(), UsageSummary()))
                ),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
            )

            result = cli_module.build_snapshot(
                context,
                refresh_budget_aggregate=False,
                refresh_active_work_items=False,
            )

        self.assertIs(result, snapshot)
        parser.parse_file.assert_not_called()
        parser.parse_file_incremental.assert_called_once_with(
            session_path,
            None,
            sse_tracker=None,
        )
        self.assertIs(context.current_session_tail_state, next_tail_state)

    def test_build_snapshot_uses_cold_session_cache_without_full_parser_or_usage_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(status="loading", session_path=session_path)
            parser = SimpleNamespace(
                parse_file_incremental=MagicMock(
                    side_effect=AssertionError("synchronous JSONL parse used")
                )
            )
            cache = SimpleNamespace(snapshot_for=MagicMock(return_value=snapshot))
            usage_cache = SimpleNamespace(
                summarize=MagicMock(return_value=(UsageSummary(), UsageSummary()))
            )
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    session_id="thread-1",
                    session_file=None,
                    resolve=MagicMock(return_value=(session_path, "renderer:thread-1")),
                ),
                sessions_root=root,
                parser=parser,
                session_snapshot_cache=cache,
                current_session_tail_state=None,
                sse_tracker=None,
                active_session_tracker=None,
                visible_app_error_cache=SimpleNamespace(resolve=MagicMock(return_value="")),
                platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                user_config=UserConfig.defaults(),
                usage_cache=usage_cache,
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
            )

            result = cli_module.build_snapshot(
                context,
                refresh_budget_aggregate=False,
                refresh_active_work_items=False,
                refresh_current_session_usage=False,
            )

        self.assertIs(result, snapshot)
        cache.snapshot_for.assert_called_once_with(session_path, session_id="thread-1")
        parser.parse_file_incremental.assert_not_called()
        self.assertEqual(usage_cache.summarize.call_args.kwargs["refresh_paths"], ())

    def test_cold_session_cache_returns_preview_then_publishes_hydrated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-07-13T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "thread-1"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = RuntimeEventBus()
            hydrated = threading.Event()
            events.subscribe(
                lambda event: hydrated.set()
                if event.type == "session_snapshot_hydrated"
                else None
            )
            cache = SessionSnapshotCache(
                JsonlSessionParser(),
                event_bus=events,
                preview_bytes=128,
            )
            try:
                preview = cache.snapshot_for(path, session_id="thread-1")
                self.assertEqual(preview.status, "loading")
                self.assertTrue(hydrated.wait(1.0))
                complete = cache.snapshot_for(path, session_id="thread-1")
            finally:
                cache.close()

        self.assertEqual(complete.status, "parsed")
        self.assertEqual(complete.session_id, "thread-1")

    def test_build_snapshot_records_renderer_unmatched_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            snapshot = ParsedSession(status="parsed", session_path=session_path)
            registry = RuntimeErrorRegistry(clock=lambda: 100.0)
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    session_id="",
                    session_file=None,
                    resolve=MagicMock(return_value=(None, "renderer-unmatched")),
                ),
                sessions_root=root,
                parser=SimpleNamespace(parse_file=MagicMock(return_value=snapshot)),
                sse_tracker=None,
                active_session_tracker=SimpleNamespace(
                    latest_session_id="thread-123",
                    latest_title="Broken Thread",
                    latest_source="renderer-unmatched",
                    title_for_session=MagicMock(return_value="Broken Thread"),
                ),
                visible_app_error_cache=SimpleNamespace(
                    resolve=MagicMock(return_value="")
                ),
                platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                user_config=UserConfig.defaults(),
                usage_cache=SimpleNamespace(
                    summarize=MagicMock(return_value=(UsageSummary(), UsageSummary()))
                ),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
                runtime_errors=registry,
            )

            with patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root):
                cli_module.build_snapshot(
                    context,
                    refresh_budget_aggregate=False,
                    refresh_active_work_items=False,
                )
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["code"], "active_session.unmatched_thread")
        self.assertEqual(payload[0]["context"]["selectionSource"], "renderer-unmatched")
        self.assertEqual(payload[0]["context"]["sessionPath"], "")
        self.assertEqual(payload[0]["context"]["threadId"], "thread-123")
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("active_session.unmatched_thread", diagnostic)
        self.assertIn("thread-123", diagnostic)
        record = _last_renderer_diagnostic_record(diagnostic)
        self.assertEqual(record["stage"], "runtime_error_recorded")
        self.assertEqual(record["source"], "active_session")
        self.assertEqual(record["severity"], "error")
        self.assertEqual(record["code"], "active_session.unmatched_thread")
        self.assertEqual(record["message"], payload[0]["message"])
        self.assertEqual(record["context"]["threadId"], "thread-123")
        self.assertEqual(record["context"]["selectionSource"], "renderer-unmatched")
        self.assertIn("firstSeenAt", record)
        self.assertIn("lastSeenAt", record)

    def test_build_snapshot_treats_renderer_waiting_as_non_error_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            context = SimpleNamespace(
                reload_user_config=MagicMock(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(None, "renderer-waiting")),
                    session_id="",
                    session_file=None,
                ),
                sessions_root=sessions_root,
                parser=SimpleNamespace(),
                sse_tracker=None,
                active_session_tracker=None,
                visible_app_error_cache=SimpleNamespace(resolve=MagicMock(return_value="")),
                platform=SimpleNamespace(),
                usage_cache=SimpleNamespace(
                    summarize=MagicMock(
                        return_value=(
                            UsageSummary(),
                            UsageSummary(),
                        )
                    )
                ),
                user_config=UserConfig.defaults(),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=[],
                pre_send_estimator=None,
                activity_monitor=None,
            )

            snapshot = cli_module.build_snapshot(
                context,
                refresh_budget_aggregate=False,
                refresh_active_work_items=False,
            )

        self.assertEqual(snapshot.status, "waiting")
        self.assertEqual(snapshot.error, "")
        payload = payload_from_snapshot(snapshot).to_json()
        self.assertFalse(payload["warning"])
        self.assertEqual(payload["topDetails"]["warnings"], "")

    def test_build_snapshot_treats_renderer_pending_mapping_as_non_error_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            for source in ("renderer-pending-session", "renderer-pending-map"):
                context = SimpleNamespace(
                    reload_user_config=MagicMock(),
                    session_resolver=SimpleNamespace(
                        resolve=MagicMock(return_value=(None, source)),
                        session_id="",
                        session_file=None,
                    ),
                    sessions_root=sessions_root,
                    parser=SimpleNamespace(),
                    sse_tracker=None,
                    active_session_tracker=None,
                    visible_app_error_cache=SimpleNamespace(
                        resolve=MagicMock(return_value="")
                    ),
                    platform=SimpleNamespace(get_active_app_error=MagicMock(return_value="")),
                    usage_cache=SimpleNamespace(
                        summarize=MagicMock(
                            return_value=(UsageSummary(), UsageSummary())
                        )
                    ),
                    user_config=UserConfig.defaults(),
                    daily_budget_usd=100.0,
                    weekly_budget_usd=400.0,
                    budget_thresholds=[],
                    pre_send_estimator=None,
                    activity_monitor=None,
                )

                snapshot = cli_module.build_snapshot(
                    context,
                    refresh_budget_aggregate=False,
                    refresh_active_work_items=False,
                )

                self.assertEqual(snapshot.status, "waiting")
                self.assertEqual(snapshot.error, "")

    def test_record_cdp_update_failure_adds_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = RuntimeErrorRegistry(clock=lambda: 250.0)
            context = SimpleNamespace(runtime_errors=registry)
            client = SimpleNamespace(
                last_status="failed",
                last_error="RuntimeError: renderer update function did not acknowledge payload",
                timeout_seconds=0.45,
            )

            with patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root):
                cli_module._record_cdp_update_failure(context, client, failures=2)
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["code"], "cdp.update_failed")
        self.assertEqual(payload[0]["context"]["failures"], 2)
        self.assertEqual(payload[0]["context"]["status"], "failed")
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("cdp.update_failed", diagnostic)
        self.assertIn("renderer update function did not acknowledge payload", diagnostic)
        record = _last_renderer_diagnostic_record(diagnostic)
        self.assertEqual(record["stage"], "runtime_error_recorded")
        self.assertEqual(record["source"], "cdp")
        self.assertEqual(record["severity"], "error")
        self.assertEqual(record["code"], "cdp.update_failed")
        self.assertEqual(record["message"], payload[0]["message"])
        self.assertEqual(record["context"]["failures"], 2)
        self.assertEqual(record["context"]["status"], "failed")
        self.assertIn("firstSeenAt", record)
        self.assertIn("lastSeenAt", record)

    def test_renderer_budget_aggregate_refreshes_for_non_jsonl_sessions_root_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            current = root / "current.jsonl"
            other = root / "notes.txt"
            root.mkdir()
            current.write_text("{}\n", encoding="utf-8")
            other.write_text("changed\n", encoding="utf-8")
            snapshot = ParsedSession(session_path=current)
            signature = ("sessions", 1, "day", "week")

            self.assertTrue(
                cli_module._renderer_should_refresh_budget_aggregate(
                    latest_snapshot=snapshot,
                    latest_budget_signature=signature,
                    budget_signature=signature,
                    file_change_reasons={"sessions-root"},
                    file_change_paths={other},
                )
            )

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

    def test_active_session_switch_pending_detects_new_resolver_path(self) -> None:
        old_path = Path(tempfile.gettempdir()) / "old-session.jsonl"
        new_path = Path(tempfile.gettempdir()) / "new-session.jsonl"
        snapshot = ParsedSession(session_id="old-session", status="parsed")
        snapshot.session_path = old_path
        snapshot.selection_source = "cdp:Old"
        context = SimpleNamespace(
            session_resolver=SimpleNamespace(
                resolve=lambda: (new_path, "cdp:New"),
                session_id="",
            ),
        )

        self.assertTrue(_active_session_switch_pending(context, snapshot))

    def test_active_work_items_follow_session_creation_order_desc(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def write_session(path: Path, session_id: str, prompt: str, offset: int) -> None:
            timestamp = (now + timedelta(seconds=offset)).isoformat()
            rows = [
                {
                    "timestamp": timestamp,
                    "type": "session_meta",
                    "payload": {"id": session_id, "cwd": f"E:\\Project\\{session_id}"},
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": timestamp,
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.5"},
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": prompt},
                },
            ]
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "session-current.jsonl"
            worker = root / "session-worker.jsonl"
            write_session(current, "session-current", "Current visible work", -2)
            write_session(worker, "session-worker", "Background thread work", -1)
            snapshot = parser.parse_file(current)
            snapshot.session_title = "Current task"
            snapshot.selection_source = "cdp:Current task"
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, snapshot, current)

        self.assertGreaterEqual(len(items), 2)
        self.assertEqual([item.id for item in items[:2]], ["session-worker", "session-current"])
        self.assertTrue(any(item.current for item in items))
        self.assertIn("Background thread work", " ".join(item.detail for item in items))
        self.assertEqual(items[0].workdir, "E:\\Project\\session-worker")

    def test_active_work_items_respect_configured_overlay_limit(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def write_session(path: Path, session_id: str, prompt: str, offset: int) -> None:
            timestamp = (now + timedelta(seconds=offset)).isoformat()
            rows = [
                {
                    "timestamp": timestamp,
                    "type": "session_meta",
                    "payload": {"id": session_id, "cwd": f"E:\\Project\\{session_id}"},
                },
                {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "task_started"}},
                {"timestamp": timestamp, "type": "turn_context", "payload": {"model": "gpt-5.5"}},
                {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "user_message", "message": prompt}},
            ]
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "session-current.jsonl"
            worker_a = root / "session-worker-a.jsonl"
            worker_b = root / "session-worker-b.jsonl"
            write_session(current, "session-current", "Current visible work", -3)
            write_session(worker_a, "session-worker-a", "Background thread A", -2)
            write_session(worker_b, "session-worker-b", "Background thread B", -1)
            snapshot = parser.parse_file(current)
            config = UserConfig.defaults()
            config.work_overlay_max_items = 2
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
                user_config=config,
            )

            items = active_work_items_for_snapshot(context, snapshot, current)

        self.assertEqual(len(items), 2)
        self.assertEqual([item.id for item in items], ["session-worker-b", "session-worker-a"])

    def test_active_work_items_include_notification_only_provider(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def write_session(
            path: Path,
            session_id: str,
            provider: str,
            originator: str,
            source: str,
            offset: int,
        ) -> None:
            timestamp = (now + timedelta(seconds=offset)).isoformat()
            rows = [
                {
                    "timestamp": timestamp,
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "cwd": f"E:\\Project\\{session_id}",
                        "model_provider": provider,
                        "originator": originator,
                        "source": source,
                    },
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": session_id},
                },
            ]
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "app.jsonl"
            selected_cli = root / "muyuan.jsonl"
            notification_cli = root / "notice.jsonl"
            excluded_cli = root / "unused.jsonl"
            write_session(current, "app", "custom", "Codex Desktop", "vscode", -3)
            write_session(selected_cli, "selected-cli", "muyuan", "codex-tui", "cli", -2)
            write_session(notification_cli, "notification-cli", "notice", "codex-tui", "cli", -1)
            write_session(excluded_cli, "excluded-cli", "unused", "codex-tui", "cli", 0)
            snapshot = parser.parse_file(current)
            config = UserConfig.defaults()
            config.provider_scope_mode = "custom"
            config.selected_providers = ["muyuan"]
            config.notification_only_providers = ["notice"]
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
                user_config=config,
            )

            items = active_work_items_for_snapshot(context, snapshot, current)

        self.assertEqual(
            {item.id for item in items},
            {"app", "selected-cli", "notification-cli"},
        )
        self.assertEqual(context.app_provider, "custom")

    def test_active_work_items_are_empty_when_overlay_disabled(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()
        snapshot = ParsedSession(
            session_id="session-current",
            session_title="Current task",
            status="parsed",
        )
        snapshot.session_started_at = now
        config = UserConfig.defaults()
        config.work_overlay_max_items = 0
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=parser,
            active_session_tracker=None,
            user_config=config,
        )

        items = active_work_items_for_snapshot(context, snapshot, None)

        self.assertEqual(items, [])

    def test_work_overlay_payload_uses_primary_screen_status_fields(self) -> None:
        item = WorkStatusItem(
            id="session-a",
            title="Ship primary screen bubbles",
            status="running",
            status_label="运行中",
            detail="用户输入：实现桌面气泡",
            session_id="thread-123",
            target_title="Ship primary screen bubbles",
            round_index=3,
            model_name="gpt-5.5",
            status_text="gpt-5.5 正在思考",
            last_text="上一轮输出保留在气泡里",
            elapsed_text="已处理 12s",
            progress="1.2k tokens | 12s",
            tokens_text="1.2k",
            cost_text="$0.012",
            cache_hit_text="67%",
            workdir_name="codex-usage-hud",
            source="activity",
            workdir="E:\\Project\\codex-usage-hud",
            model_provider="muyuan",
            client_kind="cli",
            task_started_at=datetime(2026, 6, 16, 10, 0, 0).astimezone(),
            current=True,
        )

        payload = work_item_to_overlay_dict(item)

        self.assertEqual(payload["statusLabel"], "运行中")
        self.assertEqual(payload["title"], "Ship primary screen bubbles")
        self.assertEqual(payload["sessionId"], "thread-123")
        self.assertEqual(payload["targetTitle"], "Ship primary screen bubbles")
        self.assertEqual(payload["roundIndex"], 3)
        self.assertEqual(payload["modelName"], "gpt-5.5")
        self.assertEqual(payload["statusText"], "gpt-5.5 正在思考")
        self.assertEqual(payload["lastText"], "上一轮输出保留在气泡里")
        self.assertEqual(payload["elapsedText"], "已处理 12s")
        self.assertEqual(payload["tokensText"], "1.2k")
        self.assertEqual(payload["costText"], "$0.012")
        self.assertEqual(payload["cacheHitText"], "67%")
        self.assertEqual(payload["workdirName"], "codex-usage-hud")
        self.assertEqual(payload["workdir"], "E:\\Project\\codex-usage-hud")
        self.assertEqual(payload["modelProvider"], "muyuan")
        self.assertEqual(payload["clientKind"], "cli")
        self.assertTrue(str(payload["taskStartedAt"]).startswith("2026-06-16T10:00:00"))
        self.assertTrue(payload["current"])
        self.assertIn("tokens", str(payload["progress"]))

    def test_work_overlay_top_offset_uses_screen_geometry_top(self) -> None:
        self.assertEqual(_overlay_window_top_y(0), WORK_OVERLAY_TOP_OFFSET)
        self.assertEqual(_overlay_window_top_y(48), 48 + WORK_OVERLAY_TOP_OFFSET)
        self.assertEqual(_overlay_window_top_y(-120), -120 + WORK_OVERLAY_TOP_OFFSET)

    def test_work_overlay_card_to_completed_starts_as_card_height_circle(self) -> None:
        card_rect = (90.0, 424.0, 430.0, 110.0)

        circle_rect = _card_height_circle_rect_for_rect(card_rect)

        self.assertEqual(circle_rect, (410.0, 424.0, 110.0, 110.0))

    def test_work_overlay_card_yield_clears_circle_path(self) -> None:
        card_rect = (90.0, 306.0, 430.0, 110.0)
        circle_rect = (352.0, 0.0, 168.0, 180.0)

        yield_rect = _card_yield_rect_for_circle_path(card_rect, circle_rect)

        self.assertEqual(yield_rect, (-86.0, 306.0, 430.0, 110.0))
        self.assertLessEqual(
            yield_rect[0] + yield_rect[2],
            circle_rect[0] - 8.0,
        )

    def test_work_overlay_card_yield_delay_follows_vertical_pass_order(self) -> None:
        source_circle = _card_height_circle_rect_for_rect((90.0, 424.0, 430.0, 110.0))
        target_circle = (528.0, 0.0, 168.0, 180.0)
        lower_blocker = (266.0, 306.0, 430.0, 110.0)
        upper_blocker = (266.0, 188.0, 430.0, 110.0)

        lower_delay = _card_yield_delay_ms(
            lower_blocker,
            source_circle,
            target_circle,
            520,
        )
        upper_delay = _card_yield_delay_ms(
            upper_blocker,
            source_circle,
            target_circle,
            520,
        )

        self.assertGreater(upper_delay, lower_delay)

    def test_work_overlay_energy_ring_rect_expands_completed_widget(self) -> None:
        source = (262.0, 0.0, 168.0, 180.0)

        ring_rect = _energy_ring_rect_for_completed_rect(source)

        self.assertEqual(ring_rect, (236.0, -26.0, 220.0, 220.0))
        self.assertLess(ring_rect[0], source[0])
        self.assertGreater(ring_rect[0] + ring_rect[2], source[0] + source[2])

    def test_work_overlay_completed_pending_particle_state_jitters_on_orbit(self) -> None:
        angle_a, jitter_a, pulse_a = _completed_pending_particle_state(0.2, 1, 3)
        angle_b, jitter_b, pulse_b = _completed_pending_particle_state(0.6, 1, 3)

        self.assertGreaterEqual(angle_a, 0.0)
        self.assertLess(angle_a, math.tau)
        self.assertNotEqual(angle_a, angle_b)
        self.assertNotEqual(jitter_a, jitter_b)
        self.assertGreaterEqual(pulse_a, 0.0)
        self.assertLessEqual(pulse_a, 1.0)
        self.assertGreaterEqual(pulse_b, 0.0)
        self.assertLessEqual(pulse_b, 1.0)

    def test_work_overlay_completed_pending_launch_has_press_and_rebound(self) -> None:
        self.assertEqual(_completed_pending_launch_progress(0.0), 0.0)
        self.assertEqual(_completed_pending_launch_progress(1.0), 1.0)
        self.assertLess(_completed_pending_launch_scale(0.12), 1.0)
        self.assertGreater(_completed_pending_launch_scale(0.34), 1.0)
        self.assertEqual(_completed_pending_launch_scale(0.7), 1.0)

    def test_work_overlay_completed_pending_caption_fades_out_after_completion(self) -> None:
        self.assertEqual(_completed_pending_finish_progress(0.0), 0.0)
        self.assertEqual(_completed_pending_finish_progress(10.0), 1.0)
        self.assertLess(_completed_pending_caption_opacity(0.04, completed=False), 1.0)
        self.assertEqual(_completed_pending_caption_opacity(0.2, completed=False), 1.0)
        self.assertEqual(
            _completed_pending_caption_opacity(
                0.2,
                completed=True,
                finish_elapsed_seconds=0.1,
            ),
            1.0,
        )
        self.assertEqual(
            _completed_pending_caption_opacity(
                0.2,
                completed=True,
                finish_elapsed_seconds=10.0,
            ),
            0.0,
        )

    def test_work_overlay_completed_workdir_pending_uses_badge_not_link(self) -> None:
        self.assertFalse(_workdir_link_pending_for_item({"status": "recent"}, True))
        self.assertFalse(_workdir_link_pending_for_item({"status": "running"}, True))
        self.assertFalse(_workdir_link_pending_for_item({"status": "running"}, False))

    def test_work_overlay_completed_workdir_hover_stays_invisible(self) -> None:
        self.assertFalse(_workdir_link_hover_visible_for_item({"status": "recent"}))
        self.assertTrue(_workdir_link_hover_visible_for_item({"status": "running"}))

    def test_running_work_overlay_item_uses_model_name_and_current_round(self) -> None:
        now = datetime.now().astimezone()
        snapshot = ParsedSession(
            session_id="session-running",
            session_title="Focus current round",
            request=RequestTokens(
                status="running",
                round_index=3,
                model="gpt-5.5",
                updated_at=now,
                started_at=now - timedelta(seconds=12),
            ),
            task_started_at=now - timedelta(minutes=2),
            activity=Activity(kind="idle", detail="", timestamp=now),
        )
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=JsonlSessionParser(),
            active_session_tracker=None,
        )

        items = active_work_items_for_snapshot(context, snapshot, None)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].round_index, 3)
        self.assertEqual(items[0].model_name, "gpt-5.5")
        self.assertEqual(items[0].status_text, "gpt-5.5 正在思考")

    def test_desktop_work_overlay_reads_new_click_commands_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            overlay._command_offset = 0

            overlay._command_path.write_text(
                (
                    '{"action":"activateSession","sessionId":"thread-1","targetTitle":"Thread One"}\n'
                    '{"action":"activateSession","sessionId":"thread-2","targetTitle":"Thread Two"}\n'
                ),
                encoding="utf-8",
            )

            commands = overlay.take_commands()

            self.assertEqual(
                commands,
                [
                    {
                        "action": "activateSession",
                        "sessionId": "thread-1",
                        "targetTitle": "Thread One",
                    },
                    {
                        "action": "activateSession",
                        "sessionId": "thread-2",
                        "targetTitle": "Thread Two",
                    },
                ],
            )
            self.assertEqual(overlay.take_commands(), [])

    def test_work_overlay_command_pump_uses_file_watcher_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            overlay.take_commands = MagicMock(return_value=[])
            overlay.mark_switch_completed = MagicMock()
            session_controller = MagicMock()
            watchers: list[object] = []

            class FakeWatcher:
                def __init__(self, callback: Callable[[set[str], set[Path]], None], **kwargs: object) -> None:
                    self.callback = callback
                    self.kwargs = kwargs
                    self.specs = []
                    self.closed = False
                    watchers.append(self)

                def update(self, specs: object) -> None:
                    self.specs = list(specs)

                def close(self) -> None:
                    self.closed = True

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                pump = cli_module._WorkOverlayCommandPump(
                    overlay,
                    session_controller,
                    poll_ms=60,
                )
                pump.start()

            self.assertEqual(len(watchers), 1)
            watcher = watchers[0]
            self.assertGreaterEqual(watcher.kwargs["fallback_poll_seconds"], 1.0)
            self.assertEqual(len(watcher.specs), 1)
            self.assertEqual(watcher.specs[0].path, overlay._command_path)
            self.assertEqual(watcher.specs[0].reason, "work-overlay-command")
            overlay.take_commands.assert_called_once()

            watcher.callback({"work-overlay-command"}, {overlay._command_path})
            self.assertEqual(overlay.take_commands.call_count, 2)

            pump.close()
            self.assertTrue(watcher.closed)

    def test_work_overlay_command_pump_publishes_helper_runtime_error(self) -> None:
        runtime_events = RuntimeEventBus(clock=lambda: 123.0)
        registry = RuntimeErrorRegistry(clock=lambda: 123.0)
        overlay = SimpleNamespace(
            command_path=Path(tempfile.gettempdir()) / "work-overlay-commands.jsonl",
            take_commands=MagicMock(
                return_value=[
                    {
                        "action": "runtimeError",
                        "source": "work_overlay_helper",
                        "code": "state_read_failed",
                        "message": "Unable to read overlay state.",
                        "severity": "error",
                        "context": {"stateFile": "work-overlay.json"},
                    }
                ]
            ),
        )
        session_controller = MagicMock()

        handled = cli_module._handle_work_overlay_commands(
            overlay,
            session_controller,
            runtime_events=runtime_events,
            runtime_errors=registry,
        )

        self.assertEqual(handled, 1)
        session_controller.activate_session.assert_not_called()
        events = runtime_events.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "runtime_error")
        self.assertEqual(events[0].source, "work_overlay_helper")
        self.assertEqual(
            events[0].error["code"],
            "work_overlay_helper.state_read_failed",
        )
        self.assertEqual(
            events[0].error["context"]["stateFile"],
            "work-overlay.json",
        )
        self.assertEqual(
            events[0].context["error"]["context"]["stateFile"],
            "work-overlay.json",
        )
        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["source"], "work_overlay_helper")
        self.assertEqual(
            payload[0]["code"],
            "work_overlay_helper.state_read_failed",
        )

    def test_work_overlay_helper_runtime_error_writes_normal_mode_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_events = RuntimeEventBus(clock=lambda: 150.0)
            registry = RuntimeErrorRegistry(clock=lambda: 150.0)
            context = SimpleNamespace(runtime_errors=registry)
            cli_module._ensure_runtime_error_diagnostics(context)
            overlay = SimpleNamespace(
                command_path=root / "work-overlay-commands.jsonl",
                take_commands=MagicMock(
                    return_value=[
                        {
                            "action": "runtimeError",
                            "source": "work_overlay_helper",
                            "code": "state_read_failed",
                            "message": "Unable to read overlay state.",
                            "severity": "error",
                            "context": {"stateFile": "work-overlay.json"},
                        }
                    ]
                ),
            )
            session_controller = MagicMock()

            with patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root):
                handled = cli_module._handle_work_overlay_commands(
                    overlay,
                    session_controller,
                    runtime_events=runtime_events,
                    runtime_errors=registry,
                )
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        self.assertEqual(handled, 1)
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("work_overlay_helper.state_read_failed", diagnostic)
        self.assertIn("Unable to read overlay state.", diagnostic)
        record = _last_renderer_diagnostic_record(diagnostic)
        self.assertEqual(record["stage"], "runtime_error_recorded")
        self.assertEqual(record["source"], "work_overlay_helper")
        self.assertEqual(record["severity"], "error")
        self.assertEqual(
            record["code"],
            "work_overlay_helper.state_read_failed",
        )
        self.assertEqual(record["message"], "Unable to read overlay state.")
        self.assertEqual(record["context"], {"stateFile": "work-overlay.json"})
        self.assertEqual(record["firstSeenAt"], 150.0)
        self.assertEqual(record["lastSeenAt"], 150.0)

    def test_work_overlay_command_pump_uses_cdp_before_window_prepare(self) -> None:
        command = {
            "action": "activateSession",
            "sessionId": "thread-1",
            "targetTitle": "Thread One",
        }
        overlay = SimpleNamespace(
            take_commands=MagicMock(
                return_value=[command]
            ),
            mark_switch_completed=MagicMock(),
        )
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                return_value=SessionSwitchResult(
                    ok=True,
                    status="switched",
                    backend="cdp",
                    requested_session_id="thread-1",
                    requested_title="Thread One",
                    active_title="Thread One",
                )
            )
        )
        pump = _TkWorkOverlayCommandPump(overlay, session_controller)

        with patch(
            "codex_usage_hud.cli._prepare_codex_window_for_tk",
            return_value=(True, "visible", "", 321),
        ) as prepare_window:
            handled = pump.drain_once()

        self.assertEqual(handled, 1)
        overlay.take_commands.assert_called_once()
        overlay.mark_switch_completed.assert_called_once_with(command)
        session_controller.activate_session.assert_called_once_with(
            session_id="thread-1",
            title="Thread One",
            workdir="",
        )
        self.assertEqual(prepare_window.call_count, 1)
        self.assertEqual(
            prepare_window.call_args_list[0].kwargs["timeout_seconds"],
            cli_module.WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS,
        )

    def test_cli_work_overlay_command_never_activates_app_session(self) -> None:
        session_controller = SimpleNamespace(activate_session=MagicMock())

        result = cli_module._handle_work_overlay_command(
            {
                "action": "activateSession",
                "sessionId": "cli-thread",
                "targetTitle": "CLI Thread",
                "clientKind": "cli",
            },
            session_controller,
        )

        self.assertIsNone(result)
        session_controller.activate_session.assert_not_called()

    def test_cli_workdir_is_plain_text_without_hover_or_click(self) -> None:
        cli_item = {
            "clientKind": "cli",
            "sessionId": "cli-thread",
            "workdir": r"E:\\Project\\cli",
            "status": "running",
        }
        app_item = {
            "clientKind": "app",
            "sessionId": "app-thread",
            "workdir": r"E:\\Project\\app",
            "status": "running",
        }

        self.assertFalse(_workdir_clickable_for_item(cli_item))
        self.assertFalse(_workdir_link_hover_visible_for_item(cli_item))
        self.assertTrue(_workdir_clickable_for_item(app_item))
        self.assertTrue(_workdir_link_hover_visible_for_item(app_item))

    def test_work_overlay_command_retries_after_cdp_transport_failure(self) -> None:
        command = {
            "action": "activateSession",
            "sessionId": "thread-1",
            "targetTitle": "Thread One",
        }
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                side_effect=[
                    SessionSwitchResult(
                        ok=False,
                        status="cdp-error",
                        backend="cdp",
                        requested_session_id="thread-1",
                        requested_title="Thread One",
                        message="target unavailable",
                    ),
                    SessionSwitchResult(
                        ok=True,
                        status="switch-requested",
                        backend="cdp",
                        requested_session_id="thread-1",
                        requested_title="Thread One",
                        active_session_id="thread-1",
                        active_title="Thread One",
                        matched_by="session-id",
                    ),
                ]
            )
        )
        activation_meta: dict[str, object] = {}

        with patch(
            "codex_usage_hud.cli._prepare_codex_window_for_work_overlay_switch",
            return_value=(True, "visible", "", 321),
        ) as prepare_window:
            result = cli_module._handle_work_overlay_command(
                command,
                session_controller,
                activation_meta=activation_meta,
            )

        self.assertIsNotNone(result)
        self.assertTrue(result.ok)
        self.assertEqual(session_controller.activate_session.call_count, 2)
        prepare_window.assert_called_once()
        self.assertTrue(activation_meta["windowPrepared"])

    def test_work_overlay_activation_event_carries_structured_state_and_wakeup(self) -> None:
        command = {
            "action": "activateSession",
            "sessionId": "thread-1",
            "targetTitle": "Thread One",
            "requestedAt": time.time() - 0.05,
        }
        overlay = SimpleNamespace(
            take_commands=MagicMock(return_value=[command]),
            mark_switch_completed=MagicMock(),
        )
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                return_value=SessionSwitchResult(
                    ok=True,
                    status="switch-requested",
                    backend="cdp",
                    requested_session_id="thread-1",
                    requested_title="Thread One",
                    active_session_id="thread-1",
                    active_title="Thread One",
                    matched_by="session-id",
                )
            )
        )
        runtime_events = RuntimeEventBus()

        handled = cli_module._handle_work_overlay_commands(
            overlay,
            session_controller,
            prepare_window=False,
            runtime_events=runtime_events,
        )

        self.assertEqual(handled, 1)
        events = runtime_events.drain()
        self.assertEqual(
            [event.type for event in events],
            ["overlay_command_received", "active_session_changed"],
        )
        command_context = events[0].context
        self.assertEqual(command_context["requestedSessionId"], "thread-1")
        self.assertEqual(command_context["activeSessionId"], "thread-1")
        self.assertEqual(command_context["backend"], "cdp")
        self.assertEqual(command_context["status"], "switch-requested")
        self.assertEqual(command_context["matchedBy"], "session-id")
        self.assertFalse(command_context["windowPrepared"])
        self.assertGreaterEqual(float(command_context["latencyMs"]), 0.0)
        self.assertEqual(events[1].source, "work_overlay")
        self.assertEqual(events[1].session, "thread-1")
        self.assertEqual(events[1].context["reason"], "overlay_session_activation")

    def test_failed_work_overlay_activation_does_not_publish_active_session_wakeup(self) -> None:
        command = {
            "action": "activateSession",
            "sessionId": "thread-1",
            "targetTitle": "Thread One",
        }
        overlay = SimpleNamespace(
            take_commands=MagicMock(return_value=[command]),
            mark_switch_completed=MagicMock(),
        )
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                return_value=SessionSwitchResult(
                    ok=False,
                    status="thread-not-found",
                    backend="cdp",
                    requested_session_id="thread-1",
                    requested_title="Thread One",
                )
            )
        )
        runtime_events = RuntimeEventBus()

        cli_module._handle_work_overlay_commands(
            overlay,
            session_controller,
            prepare_window=True,
            runtime_events=runtime_events,
        )

        self.assertEqual(
            [event.type for event in runtime_events.drain()],
            ["overlay_command_received"],
        )
        session_controller.activate_session.assert_called_once()

    def test_current_session_overlay_command_refocuses_codex_after_already_active_result(self) -> None:
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                return_value=SessionSwitchResult(
                    ok=True,
                    status="already-active",
                    backend="cdp",
                    requested_session_id="thread-1",
                    requested_title="Thread One",
                    active_session_id="thread-1",
                    active_title="Thread One",
                    matched_by="active-session-id",
                )
            )
        )

        with (
            patch(
                "codex_usage_hud.cli._prepare_codex_window_for_tk",
                return_value=(True, "visible", "", 321),
            ) as prepare_window,
            patch(
                "codex_usage_hud.cli._refocus_codex_window_after_current_session_click",
                return_value=(True, "visible", "", 321),
            ) as refocus_window,
        ):
            cli_module._handle_work_overlay_command(
                {
                    "action": "activateSession",
                    "sessionId": "thread-1",
                    "targetTitle": "Thread One",
                    "current": True,
                },
                session_controller,
                prepare_window=True,
            )

        session_controller.activate_session.assert_called_once_with(
            session_id="thread-1",
            title="Thread One",
            workdir="",
        )
        prepare_window.assert_not_called()
        refocus_window.assert_called_once()

    def test_work_overlay_switch_activates_codex_on_macos(self) -> None:
        with (
            patch.object(sys, "platform", "darwin"),
            patch("codex_usage_hud.cli.launch_codex_app", return_value=True) as launch,
        ):
            result = cli_module._prepare_codex_window_for_work_overlay_switch()

        self.assertEqual(result, (True, "activated", "", 0))
        launch.assert_called_once_with(debugger=False)

    def test_desktop_work_overlay_writes_state_with_atomic_json_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"

            with patch("codex_usage_hud.cli.write_json_object") as write_json:
                overlay._write_state([{"id": "thread-1"}], close=False)

            write_json.assert_called_once()
            path_arg, payload_arg = write_json.call_args.args
            self.assertEqual(path_arg, overlay._state_path)
            self.assertEqual(payload_arg["commandPath"], str(overlay._command_path))
            self.assertEqual(payload_arg["items"], [{"id": "thread-1"}])
            self.assertEqual(payload_arg["itemLimit"], 2)
            self.assertFalse(payload_arg["close"])

    def test_desktop_work_overlay_skips_unchanged_state_until_keepalive(self) -> None:
        item = WorkStatusItem(
            id="thread-1",
            title="Desktop bubble",
            session_id="thread-1",
            status="running",
            status_label="运行中",
            detail="正在处理",
        )
        overlay = DesktopWorkOverlay(item_limit=2)

        with (
            patch.object(overlay, "_runtime_available", return_value=True),
            patch.object(overlay, "_theme_payload", return_value={"variant": "dark"}),
            patch.object(overlay, "_start"),
            patch("codex_usage_hud.cli.write_json_object") as write_json,
        ):
            overlay.update([item])
            overlay.update([item])

        write_json.assert_called_once()

    def test_work_overlay_helper_uses_qfilesystemwatcher_for_state_updates(self) -> None:
        source = (
            PROJECT_ROOT
            / "src"
            / "codex_usage_hud"
            / "ui"
            / "work_overlay_qt.py"
        ).read_text(encoding="utf-8")

        self.assertIn("QFileSystemWatcher", source)
        self.assertNotIn("poll_timer.start(WORK_OVERLAY_POLL_MS)", source)

    def test_desktop_work_overlay_appends_transition_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            overlay._transition_audit_path = root / "work-overlay-transitions.jsonl"
            overlay._last_payload_items = [
                {
                    "id": "thread-1",
                    "sessionId": "thread-1",
                    "title": "Thread One",
                    "status": "running",
                    "pendingAccounting": False,
                }
            ]

            overlay._write_state(
                [
                    {
                        "id": "thread-1",
                        "sessionId": "thread-1",
                        "title": "Thread One",
                        "status": "recent",
                        "pendingAccounting": True,
                    }
                ],
                close=False,
            )

            lines = overlay._transition_audit_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["transition"], "card_to_completed")
        self.assertEqual(event["sessionId"], "thread-1")
        self.assertEqual(event["oldStatus"], "running")
        self.assertEqual(event["newStatus"], "recent")
        self.assertTrue(event["newPendingAccounting"])

    def test_desktop_work_overlay_marks_switch_completed_in_cached_state(self) -> None:
        overlay = DesktopWorkOverlay(item_limit=2)
        overlay._last_payload_items = [
            {
                "id": "thread-1",
                "sessionId": "thread-1",
                "targetTitle": "Thread One",
                "current": False,
            },
            {
                "id": "thread-2",
                "sessionId": "thread-2",
                "targetTitle": "Thread Two",
                "current": True,
            },
        ]
        overlay._last_theme_payload = {"variant": "dark"}

        with patch("codex_usage_hud.cli.write_json_object") as write_json:
            marked = overlay.mark_switch_completed(
                {
                    "action": "activateSession",
                    "sessionId": "thread-1",
                    "targetTitle": "Thread One",
                }
            )

        self.assertTrue(marked)
        written_items = write_json.call_args.args[1]["items"]
        self.assertTrue(written_items[0]["current"])
        self.assertFalse(written_items[1]["current"])
        self.assertEqual(write_json.call_args.args[1]["theme"], {"variant": "dark"})
        self.assertEqual(overlay._last_payload_items, written_items)

    def test_desktop_work_overlay_holds_switch_completed_for_next_update(self) -> None:
        overlay = DesktopWorkOverlay(item_limit=2)
        overlay._last_payload_items = [
            {"id": "thread-1", "sessionId": "thread-1", "targetTitle": "Thread One"},
            {"id": "thread-2", "sessionId": "thread-2", "targetTitle": "Thread Two"},
        ]
        next_items = [
            WorkStatusItem(
                id="thread-1",
                title="Thread One",
                session_id="thread-1",
                target_title="Thread One",
                status="recent",
                status_label="刚完成",
                detail="done",
                current=False,
            ),
            WorkStatusItem(
                id="thread-2",
                title="Thread Two",
                session_id="thread-2",
                target_title="Thread Two",
                status="running",
                status_label="运行中",
                detail="running",
                current=True,
            ),
        ]

        with (
            patch.object(overlay, "_runtime_available", return_value=True),
            patch.object(overlay, "_theme_payload", return_value={}),
            patch.object(overlay, "_start"),
            patch("codex_usage_hud.cli.write_json_object") as write_json,
        ):
            self.assertTrue(
                overlay.mark_switch_completed(
                    {
                        "action": "activateSession",
                        "sessionId": "thread-1",
                        "targetTitle": "Thread One",
                    }
                )
            )
            overlay.update(next_items)

        written_items = write_json.call_args_list[-1].args[1]["items"]
        self.assertTrue(written_items[0]["current"])
        self.assertFalse(written_items[1]["current"])

    def test_desktop_work_overlay_skips_when_pyside6_unavailable(self) -> None:
        item = WorkStatusItem(
            id="thread-1",
            title="Desktop bubble",
            status="running",
            status_label="运行中",
            detail="正在处理",
        )
        overlay = DesktopWorkOverlay(item_limit=2)

        with (
            patch("codex_usage_hud.cli.importlib.util.find_spec", return_value=None),
            patch("codex_usage_hud.cli._append_renderer_diagnostic") as diagnostic,
            patch.object(overlay, "_start") as start,
        ):
            overlay.update([item])
            overlay.update([item])

        start.assert_not_called()
        diagnostic.assert_called_once()
        self.assertTrue(overlay.enabled)
        self.assertIsNone(overlay._process)

    def test_desktop_work_overlay_starts_when_pyside6_available(self) -> None:
        item = WorkStatusItem(
            id="thread-1",
            title="Desktop bubble",
            status="running",
            status_label="运行中",
            detail="正在处理",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            fake_process = SimpleNamespace(
                poll=MagicMock(return_value=None),
                wait=MagicMock(return_value=None),
            )

            with (
                patch("codex_usage_hud.cli.importlib.util.find_spec", return_value=object()),
                patch("codex_usage_hud.cli.subprocess.Popen", return_value=fake_process) as popen,
            ):
                # The first HUD snapshot is historical persisted state and
                # must not open a bubble; the following event may publish it.
                overlay.update([item])
                popen.assert_not_called()
                self.assertFalse(overlay._state_path.exists())
                overlay.update([item])

            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertIn("--work-overlay-helper", command)
            self.assertIn(str(overlay._state_path), command)
            self.assertIs(overlay._process, fake_process)
            self.assertTrue(overlay._state_path.exists())
            overlay.close()

    def test_desktop_work_overlay_suppresses_first_snapshot_items(self) -> None:
        item = WorkStatusItem(
            id="thread-startup",
            title="Historical task",
            session_id="thread-startup",
            status="running",
            status_label="运行中",
            detail="Persisted session state",
        )
        overlay = DesktopWorkOverlay(item_limit=2)

        with (
            patch.object(overlay, "_runtime_available", return_value=True),
            patch.object(overlay, "_theme_payload", return_value={}),
            patch.object(overlay, "_start"),
            patch("codex_usage_hud.cli.write_json_object") as write_json,
        ):
            overlay.update([item])
            write_json.assert_not_called()
            overlay.update([item])

        write_json.assert_called_once()
        self.assertEqual(write_json.call_args.args[1]["items"][0]["id"], "thread-startup")

    def test_desktop_work_overlay_keep_alive_refreshes_cached_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            overlay._last_payload_items = [{"id": "thread-1"}]
            overlay._last_theme_payload = {"variant": "dark"}
            overlay._last_state_write_at = 1.0
            overlay._process = SimpleNamespace(poll=MagicMock(return_value=None))

            with (
                patch("codex_usage_hud.cli.time.monotonic", return_value=17.0),
                patch("codex_usage_hud.cli.write_json_object") as write_json,
                patch.object(overlay, "_start") as start,
            ):
                overlay.keep_alive()

        write_json.assert_called_once()
        path_arg, payload_arg = write_json.call_args.args
        self.assertEqual(path_arg, overlay._state_path)
        self.assertEqual(payload_arg["items"], [{"id": "thread-1"}])
        self.assertEqual(payload_arg["theme"], {"variant": "dark"})
        self.assertFalse(payload_arg["close"])
        start.assert_not_called()

    def test_desktop_work_overlay_keep_alive_uses_conservative_idle_interval(self) -> None:
        self.assertGreaterEqual(
            cli_module.WORK_OVERLAY_KEEPALIVE_SECONDS,
            cli_module.WORK_OVERLAY_STALE_SECONDS * 0.5,
        )
        self.assertLess(
            cli_module.WORK_OVERLAY_KEEPALIVE_SECONDS,
            cli_module.WORK_OVERLAY_STALE_SECONDS,
        )

    def test_desktop_work_overlay_keep_alive_restarts_clean_helper_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            overlay = DesktopWorkOverlay(item_limit=2)
            overlay._state_path = root / "work-overlay-123-1.json"
            overlay._command_path = root / "work-overlay-123-1-commands.jsonl"
            overlay._last_payload_items = [{"id": "thread-1"}]
            overlay._last_theme_payload = {}
            overlay._last_state_write_at = 1.0
            overlay._process = SimpleNamespace(
                poll=MagicMock(return_value=0),
                returncode=0,
            )

            with (
                patch("codex_usage_hud.cli.time.monotonic", return_value=17.0),
                patch("codex_usage_hud.cli.write_json_object"),
                patch.object(overlay, "_start") as start,
                patch("codex_usage_hud.cli._append_renderer_diagnostic") as diagnostic,
            ):
                overlay.keep_alive()

        self.assertIsNone(overlay._process)
        self.assertEqual(overlay._restart_blocked_until, 0.0)
        start.assert_called_once()
        diagnostic.assert_not_called()

    def test_desktop_work_overlay_exports_runtime_theme_tokens_when_available(self) -> None:
        overlay = DesktopWorkOverlay(item_limit=2)
        overlay._theme_probe = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                source="cdp",
                hud_tokens=SimpleNamespace(
                    to_dict=lambda: {
                        "variant": "dark",
                        "accent": "#339cff",
                        "surface": "#181818",
                    }
                ),
            )
        )

        self.assertEqual(
            overlay._theme_payload(),
            {
                "variant": "dark",
                "accent": "#339cff",
                "surface": "#181818",
            },
        )

    def test_desktop_work_overlay_accepts_persisted_theme_tokens(self) -> None:
        overlay = DesktopWorkOverlay(item_limit=2)
        overlay._theme_probe = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                source="persisted",
                hud_tokens=SimpleNamespace(
                    to_dict=lambda: {
                        "variant": "light",
                        "accent": "#0969da",
                        "surface": "#ffffff",
                    }
                ),
            )
        )

        self.assertEqual(
            overlay._theme_payload(),
            {
                "variant": "light",
                "accent": "#0969da",
                "surface": "#ffffff",
            },
        )

    def test_loading_feedback_writes_state_with_atomic_json_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feedback = cli_module.HudLoadingFeedback(
                "Switching HUD",
                "Opening Tk overlay...",
                enabled=True,
            )
            feedback._state_path = root / "loading-123-1.json"

            with patch("codex_usage_hud.cli.write_json_object") as write_json:
                feedback._write_state(close=True)

            write_json.assert_called_once()
            path_arg, payload_arg = write_json.call_args.args
            self.assertEqual(path_arg, feedback._state_path)
            self.assertEqual(payload_arg["title"], "Switching HUD")
            self.assertEqual(payload_arg["message"], "Opening Tk overlay...")
            self.assertFalse(payload_arg["restartVisible"])
            self.assertTrue(payload_arg["close"])

    def test_loading_feedback_consumes_restart_request_from_start_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feedback = cli_module.HudLoadingFeedback(
                "还差一步：重启 Codex",
                "准备好后点击按钮继续。",
                enabled=True,
            )
            feedback._state_path = root / "loading-123-1.json"
            feedback._restart_request_path = cli_module._loading_feedback_restart_path(
                feedback._state_path
            )
            feedback._restart_request_path.write_text(
                '{"action":"restart_codex"}',
                encoding="utf-8",
            )

            self.assertTrue(feedback.take_codex_restart_request())
            self.assertFalse(feedback._restart_request_path.exists())
            self.assertFalse(feedback.take_codex_restart_request())

    def test_loading_feedback_uses_renderer_bubble_top_right_geometry(self) -> None:
        self.assertEqual(
            cli_module._loading_feedback_top_right_geometry(
                screen_width=1920,
                screen_height=1080,
                width=228,
                height=146,
            ),
            (1674, 72),
        )

    def test_cleanup_keeps_stale_restart_card_while_owner_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "loading-123-1.json"
            state_path.write_text(
                '{"ownerPid":123,"restartVisible":true}',
                encoding="utf-8",
            )
            old = time.time() - cli_module.LOADING_FEEDBACK_STALE_SECONDS - 5.0
            os.utime(state_path, (old, old))

            with (
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root),
                patch("codex_usage_hud.cli._process_exists", return_value=True),
            ):
                cleanup_stale_loading_feedback_files()

            self.assertTrue(state_path.exists())

    def test_no_startup_prompt_flag_does_not_hide_restart_card(self) -> None:
        with patch.object(sys, "platform", "win32"):
            self.assertTrue(
                cli_module._loading_feedback_enabled(
                    SimpleNamespace(no_startup_prompt=True)
                )
            )

    def test_work_overlay_session_switch_uses_search_fallback_by_default(self) -> None:
        class FakeCdpBackend:
            name = "cdp"

            def __init__(self, *, timeout_seconds: float) -> None:
                self.timeout_seconds = timeout_seconds

        class FakeNativeBackend:
            name = "windows-search"

            def __init__(self, platform: object) -> None:
                self.platform = platform

        with patch("codex_usage_hud.cli.CdpSessionSwitchBackend", FakeCdpBackend), patch(
            "codex_usage_hud.cli.WindowsSearchSessionSwitchBackend",
            FakeNativeBackend,
        ), patch.dict(os.environ, {"CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH": ""}, clear=False):
            controller = _build_session_switch_controller(
                SimpleNamespace(),
                prefer_native_search=False,
            )
            self.assertEqual(
                [backend.name for backend in controller._backends],
                ["cdp", "windows-search"],
            )

            native_first = _build_session_switch_controller(
                SimpleNamespace(),
                prefer_native_search=True,
            )
            self.assertEqual(
                [backend.name for backend in native_first._backends],
                ["windows-search", "cdp"],
            )

        with patch("codex_usage_hud.cli.CdpSessionSwitchBackend", FakeCdpBackend), patch(
            "codex_usage_hud.cli.WindowsSearchSessionSwitchBackend",
            FakeNativeBackend,
        ), patch.dict(os.environ, {"CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH": "off"}, clear=False):
            disabled = _build_session_switch_controller(
                SimpleNamespace(),
                prefer_native_search=False,
            )
            self.assertEqual([backend.name for backend in disabled._backends], ["cdp"])

    def test_session_switch_controller_continues_after_backend_exception(self) -> None:
        class FailingBackend:
            name = "cdp"

            def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
                del request
                raise RuntimeError("boom")

        class FallbackBackend:
            name = "windows-search"

            def activate(self, request: SessionSwitchRequest) -> SessionSwitchResult:
                return SessionSwitchResult(
                    ok=True,
                    status="switched",
                    backend=self.name,
                    requested_session_id=request.session_id,
                    requested_title=request.title,
                    active_title=request.title,
                )

        controller = SessionSwitchController([FailingBackend(), FallbackBackend()])

        result = controller.activate_session(session_id="thread-1", title="Thread One")

        self.assertTrue(result.ok)
        self.assertEqual(result.backend, "windows-search")
        self.assertEqual(result.active_title, "Thread One")

    def test_crash_diagnostics_can_be_disabled(self) -> None:
        with patch.object(sys, "platform", "win32"), patch.dict(
            os.environ,
            {"CODEX_USAGE_HUD_CRASH_DIAGNOSTICS": "off"},
            clear=False,
        ):
            self.assertIsNone(_enable_crash_diagnostics())

    def test_work_overlay_dismissal_stays_hidden_until_next_task(self) -> None:
        original = {
            "id": "session-a",
            "taskStartedAt": "2026-06-16T10:00:00+08:00",
            "startedAt": "2026-06-16T10:00:00+08:00",
            "status": "running",
            "statusText": "正在思考",
            "lastText": "先分析一下",
            "current": True,
        }
        dismissed = {"session-a": _item_dismiss_key(original)}

        completed_same_task = {
            **original,
            "status": "recent",
            "statusText": "已完成",
            "lastText": "这轮做完了",
        }
        self.assertEqual(
            _visible_overlay_items([completed_same_task], dismissed, item_limit=4),
            [],
        )
        self.assertEqual(dismissed, {"session-a": _item_dismiss_key(original)})

        next_task = {
            **original,
            "taskStartedAt": "2026-06-16T10:05:00+08:00",
            "startedAt": "2026-06-16T10:05:00+08:00",
            "status": "running",
            "statusText": "正在思考",
            "lastText": "下一轮重新开始",
        }
        self.assertEqual(
            _visible_overlay_items([next_task], dismissed, item_limit=4),
            [next_task],
        )
        self.assertEqual(dismissed, {})

    def test_work_overlay_dismissal_filters_refresh_while_item_is_still_live(self) -> None:
        completed = {
            "id": "session-a",
            "taskStartedAt": "2026-06-16T10:00:00+08:00",
            "startedAt": "2026-06-16T10:00:00+08:00",
            "status": "recent",
            "statusText": "已完成",
            "lastText": "这轮做完了",
            "current": True,
        }
        dismissed: dict[str, str] = {}

        _mark_item_dismissed(dismissed, completed)

        self.assertEqual(
            _visible_overlay_items([completed], dismissed, item_limit=4),
            [],
        )
        self.assertEqual(dismissed, {"session-a": _item_dismiss_key(completed)})

    def test_work_overlay_error_does_not_inherit_running_dismissal(self) -> None:
        running = {
            "id": "session-a",
            "taskStartedAt": "2026-06-16T10:00:00+08:00",
            "startedAt": "2026-06-16T10:00:00+08:00",
            "status": "running",
            "statusText": "正在思考",
            "current": True,
        }
        dismissed = {"session-a": _item_dismiss_key(running)}
        error = {
            **running,
            "status": "error",
            "statusText": "exceeded retry limit, last status: 429 Too Many Requests",
            "detail": "exceeded retry limit, last status: 429 Too Many Requests",
        }

        self.assertEqual(
            _visible_overlay_items([error], dismissed, item_limit=4),
            [error],
        )
        self.assertEqual(dismissed, {})

    def test_work_overlay_marks_visible_codex_error(self) -> None:
        snapshot = ParsedSession(
            session_id="session-error",
            session_title="Rate limited thread",
            request=RequestTokens(
                status="error",
                error="exceeded retry limit, last status: 429 Too Many Requests",
            ),
        )
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=JsonlSessionParser(),
            active_session_tracker=None,
        )

        items = active_work_items_for_snapshot(context, snapshot, None)
        payload = work_item_to_overlay_dict(items[0])

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["statusLabel"], "出错")
        self.assertIn("429 Too Many Requests", str(payload["statusText"]))

    def test_work_overlay_visible_app_error_overrides_completed_task(self) -> None:
        now = datetime.now().astimezone()
        snapshot = ParsedSession(
            session_id="session-error",
            session_title="Rate limited thread",
            task_started_at=now - timedelta(seconds=20),
            task_completed_at=now,
            request=RequestTokens(status="confirmed"),
        )
        _apply_visible_app_error(
            snapshot,
            "exceeded retry limit, last status: 429 Too Many Requests",
        )
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=JsonlSessionParser(),
            active_session_tracker=None,
        )

        items = active_work_items_for_snapshot(context, snapshot, None)

        self.assertEqual(items[0].status, "error")
        self.assertIn("429 Too Many Requests", items[0].status_text)

    def test_visible_app_error_cache_holds_transient_cdp_miss(self) -> None:
        now = datetime.now().astimezone()
        cache = _VisibleAppErrorCache()
        snapshot = ParsedSession(
            session_id="session-error",
            refreshed_at=now,
            task_started_at=now - timedelta(seconds=10),
        )

        self.assertIn(
            "429 Too Many Requests",
            cache.resolve(
                snapshot,
                "exceeded retry limit, last status: 429 Too Many Requests",
            ),
        )

        snapshot.refreshed_at = now + timedelta(seconds=30)
        self.assertIn("429 Too Many Requests", cache.resolve(snapshot, ""))

        snapshot.refreshed_at = now + timedelta(seconds=61)
        self.assertEqual(cache.resolve(snapshot, ""), "")

    def test_visible_app_error_cache_resets_for_next_task(self) -> None:
        now = datetime.now().astimezone()
        cache = _VisibleAppErrorCache()
        snapshot = ParsedSession(
            session_id="session-error",
            refreshed_at=now,
            task_started_at=now - timedelta(seconds=10),
        )
        cache.resolve(snapshot, "exceeded retry limit, last status: 429 Too Many Requests")
        snapshot.task_started_at = now + timedelta(seconds=5)
        snapshot.refreshed_at = now + timedelta(seconds=6)

        self.assertEqual(cache.resolve(snapshot, ""), "")

    def test_visible_app_error_overrides_request_status(self) -> None:
        snapshot = ParsedSession(request=RequestTokens(status="running", source="sse"))

        _apply_visible_app_error(
            snapshot,
            "exceeded retry limit, last status: 429 Too Many Requests",
        )

        self.assertEqual(snapshot.request.status, "error")
        self.assertEqual(snapshot.request.source, "app")
        self.assertIn("429 Too Many Requests", snapshot.request.error)

    def test_renderer_refreshes_active_work_items_for_current_session_file_change(self) -> None:
        session_path = Path("session-current.jsonl")
        snapshot = ParsedSession(session_path=session_path)

        refresh = _renderer_should_refresh_active_work_items(
            latest_snapshot=snapshot,
            latest_active_work_refresh_at=100.0,
            now_monotonic=101.0,
            active_work_refresh_pending=False,
            file_change_reasons={"session"},
            file_change_paths={session_path},
        )

        self.assertTrue(refresh)

    def test_work_overlay_recent_item_keeps_last_output_text(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def row(offset: int, row_type: str, payload: dict[str, object]) -> dict[str, object]:
            return {
                "timestamp": (now + timedelta(seconds=offset)).isoformat(),
                "type": row_type,
                "payload": payload,
            }

        token_payload = {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 3,
                    "total_tokens": 120,
                },
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 3,
                    "total_tokens": 120,
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session-current.jsonl"
            running_rows = [
                row(-6, "session_meta", {"id": "session-current"}),
                row(-5, "event_msg", {"type": "task_started"}),
                row(-3, "event_msg", {"type": "agent_message", "message": "最后一轮输出文本"}),
                row(-1, "event_msg", token_payload),
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in running_rows),
                encoding="utf-8",
            )
            running_snapshot = parser.parse_file(path)
            running_snapshot.session_title = "Current task"
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )

            running_items = active_work_items_for_snapshot(context, running_snapshot, path)

            rows = [
                *running_rows,
                row(0, "event_msg", {"type": "task_complete"}),
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                encoding="utf-8",
            )
            snapshot = parser.parse_file(path)
            snapshot.session_title = "Current task"
            items = active_work_items_for_snapshot(context, snapshot, path)

        self.assertIn(running_items[0].status, {"running", "active"})
        self.assertEqual(items[0].status_label, "刚完成")
        self.assertEqual(items[0].status_text, "已完成")
        self.assertIn("已处理", items[0].elapsed_text)
        self.assertEqual(items[0].last_text, "最后一轮输出文本")

    def test_work_overlay_confirmed_tokens_do_not_mark_session_complete(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()
        token_payload = {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 3,
                    "total_tokens": 120,
                },
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 3,
                    "total_tokens": 120,
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session-current.jsonl"
            rows = [
                {
                    "timestamp": (now + timedelta(seconds=-4)).isoformat(),
                    "type": "session_meta",
                    "payload": {"id": "session-current"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-3)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-2)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "输出后仍可能继续处理"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-1)).isoformat(),
                    "type": "event_msg",
                    "payload": token_payload,
                },
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                encoding="utf-8",
            )
            snapshot = parser.parse_file(path)
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, snapshot, path)

        self.assertEqual(items[0].status_label, "处理中")
        self.assertNotEqual(items[0].status_text, "已完成")

    def test_work_overlay_final_answer_marks_pending_completed_after_running_seen(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def row(offset: int, row_type: str, payload: dict[str, object]) -> dict[str, object]:
            return {
                "timestamp": (now + timedelta(seconds=offset)).isoformat(),
                "type": row_type,
                "payload": payload,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session-current.jsonl"
            running_rows = [
                row(-8, "session_meta", {"id": "session-current"}),
                row(-7, "event_msg", {"type": "task_started"}),
                row(-6, "event_msg", {"type": "user_message", "message": "answer quickly"}),
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in running_rows),
                encoding="utf-8",
            )
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )
            running_snapshot = parser.parse_file(path)
            running_items = active_work_items_for_snapshot(context, running_snapshot, path)

            rows = [
                *running_rows,
                row(
                    -3,
                    "event_msg",
                    {
                        "type": "agent_message",
                        "message": "1",
                        "phase": "final_answer",
                    },
                ),
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                encoding="utf-8",
            )
            snapshot = parser.parse_file(path)
            items = active_work_items_for_snapshot(context, snapshot, path)

        self.assertIn(running_items[0].status, {"running", "active"})
        self.assertIsNone(snapshot.task_completed_at)
        self.assertEqual(items[0].status, "recent")
        self.assertEqual(items[0].status_label, "刚完成")
        self.assertEqual(items[0].status_text, "已完成")
        self.assertTrue(items[0].pending_accounting)
        self.assertTrue(work_item_to_overlay_dict(items[0])["pendingAccounting"])

    def test_current_completed_task_hides_without_prior_running_overlay(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()
        token_payload = {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 4,
                    "total_tokens": 130,
                },
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 4,
                    "total_tokens": 130,
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session-current.jsonl"
            running_rows = [
                {
                    "timestamp": (now + timedelta(seconds=-40)).isoformat(),
                    "type": "session_meta",
                    "payload": {"id": "session-current"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-35)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-30)).isoformat(),
                    "type": "event_msg",
                    "payload": token_payload,
                },
                {
                    "timestamp": (now + timedelta(seconds=-25)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "收尾说明文本"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-20)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                },
            ]
            rows = running_rows[:-1]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                encoding="utf-8",
            )
            running_snapshot = parser.parse_file(path)
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )
            running_items = active_work_items_for_snapshot(context, running_snapshot, path)

            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in running_rows),
                encoding="utf-8",
            )
            completed_snapshot = parser.parse_file(path)

            fresh_context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )
            fresh_items = active_work_items_for_snapshot(fresh_context, completed_snapshot, path)

            items = active_work_items_for_snapshot(context, completed_snapshot, path)

        self.assertIn(running_items[0].status, {"running", "active"})
        self.assertEqual(completed_snapshot.request.status, "confirmed")
        self.assertEqual(fresh_items, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "recent")
        self.assertEqual(items[0].status_label, "刚完成")
        self.assertEqual(items[0].status_text, "已完成")
        self.assertTrue(items[0].elapsed_text.startswith("已处理 "))
        self.assertEqual(items[0].tokens_text, "130")
        self.assertIn("$", items[0].cost_text)
        self.assertTrue(items[0].workdir_name == "" or len(items[0].workdir_name) <= 32)

    def test_completed_work_overlay_uses_all_current_task_rounds(self) -> None:
        now = datetime.now().astimezone()
        rounds = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=2_000_000,
                cached_tokens=1_920_000,
                output_tokens=0,
                reasoning_tokens=0,
                total_tokens=2_000_000,
                estimated=False,
                cost_usd=2.22,
                started_at=now - timedelta(minutes=11),
                completed_at=now - timedelta(minutes=1),
            ),
            RequestRound(
                index=2,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=420_000,
                cached_tokens=361_200,
                output_tokens=0,
                reasoning_tokens=0,
                total_tokens=420_000,
                estimated=False,
                cost_usd=0.09,
                started_at=now - timedelta(minutes=1),
                completed_at=now,
            ),
        ]
        running_snapshot = ParsedSession(
            session_id="session-completed",
            session_title="Multi-round task",
            request=RequestTokens(
                status="running",
                input_tokens=420_000,
                cached_tokens=361_200,
                total_tokens=420_000,
                cost_usd=0.09,
                updated_at=now,
            ),
            request_history=rounds,
            activity=Activity(kind="agent", detail="最后一轮处理中", timestamp=now),
            last_output=Activity(kind="agent", detail="最后一轮处理中", timestamp=now),
            task_started_at=now - timedelta(minutes=11),
        )
        completed_snapshot = ParsedSession(
            session_id="session-completed",
            session_title="Multi-round task",
            request=RequestTokens(
                status="confirmed",
                input_tokens=420_000,
                cached_tokens=361_200,
                total_tokens=420_000,
                cost_usd=0.09,
                updated_at=now,
            ),
            request_history=rounds,
            activity=Activity(kind="agent", detail="完成说明", timestamp=now),
            last_output=Activity(kind="agent", detail="完成说明", timestamp=now),
            task_started_at=now - timedelta(minutes=11),
            task_completed_at=now,
        )
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=JsonlSessionParser(),
            active_session_tracker=None,
        )

        active_work_items_for_snapshot(context, running_snapshot, None)
        items = active_work_items_for_snapshot(context, completed_snapshot, None)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "recent")
        self.assertEqual(items[0].tokens_text, "2.42M")
        self.assertEqual(items[0].cost_text, "$2.31")
        self.assertEqual(items[0].cache_hit_text, "94%")

    def test_completed_task_ignores_active_stale_filter(self) -> None:
        now = datetime.now().astimezone()
        running_snapshot = ParsedSession(
            session_id="session-completed",
            session_title="Long completed task",
            cwd="E:\\Project\\codex-usage-hud",
            request=RequestTokens(
                status="running",
                input_tokens=100,
                cached_tokens=25,
                total_tokens=140,
                cost_usd=0.0123,
                updated_at=now,
            ),
            activity=Activity(
                kind="agent",
                detail="最后输出",
                timestamp=now,
            ),
            last_output=Activity(
                kind="agent",
                detail="完成说明",
                timestamp=now,
            ),
            task_started_at=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 180),
        )
        snapshot = ParsedSession(
            session_id="session-completed",
            session_title="Long completed task",
            cwd="E:\\Project\\codex-usage-hud",
            request=RequestTokens(
                status="confirmed",
                input_tokens=100,
                cached_tokens=25,
                total_tokens=140,
                cost_usd=0.0123,
                updated_at=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 60),
            ),
            activity=Activity(
                kind="agent",
                detail="最后输出",
                timestamp=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 60),
            ),
            last_output=Activity(
                kind="agent",
                detail="完成说明",
                timestamp=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 60),
            ),
            task_started_at=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 180),
            task_completed_at=now - timedelta(seconds=ACTIVE_WORK_STALE_SECONDS + 120),
        )
        context = SimpleNamespace(
            sessions_root=Path(tempfile.gettempdir()) / "missing-codex-work-root",
            parser=JsonlSessionParser(),
            active_session_tracker=None,
        )
        running_items = active_work_items_for_snapshot(context, running_snapshot, None)

        items = active_work_items_for_snapshot(context, snapshot, None)

        self.assertIn(running_items[0].status, {"running", "active"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "recent")
        self.assertEqual(items[0].elapsed_text, "已处理 1m00s")
        self.assertEqual(items[0].cache_hit_text, "25%")

    def test_completed_overlay_items_order_oldest_to_newest_before_active_items(self) -> None:
        completed_latest = {
            "id": "session-completed-latest",
            "status": "recent",
            "statusText": "已完成",
            "lastText": "完成态",
            "updatedAt": "2026-06-17T10:06:00+08:00",
        }
        completed_oldest = {
            "id": "session-completed-oldest",
            "status": "recent",
            "statusText": "已完成",
            "lastText": "更早完成",
            "updatedAt": "2026-06-17T10:01:00+08:00",
        }
        active = {
            "id": "session-active",
            "status": "running",
            "statusText": "运行中",
            "lastText": "进行中",
        }

        ordered = _ordered_overlay_items([completed_latest, active, completed_oldest])

        self.assertEqual(
            [item["id"] for item in ordered],
            ["session-completed-oldest", "session-completed-latest", "session-active"],
        )

    def test_completed_badge_hover_ignores_bounding_box_corner(self) -> None:
        self.assertFalse(
            _point_in_inscribed_circle(
                104,
                54,
                left=100,
                top=50,
                width=168,
                height=168,
            )
        )
        self.assertFalse(
            _overlay_hover_hit_test(
                104,
                54,
                circle_rects=[(100, 50, 168, 168)],
            )
        )

    def test_completed_badge_hover_accepts_circle_center(self) -> None:
        self.assertTrue(
            _overlay_hover_hit_test(
                184,
                134,
                circle_rects=[(100, 50, 168, 168)],
            )
        )

    def test_overlay_hover_hit_test_keeps_active_card_rectangles(self) -> None:
        self.assertTrue(
            _overlay_hover_hit_test(
                24,
                42,
                rects=[(0, 0, 430, 118)],
            )
        )

    def test_historical_completed_overlay_item_does_not_show_on_startup(self) -> None:
        now = datetime.now().astimezone()
        current_snapshot = ParsedSession(
            session_id="session-current",
            session_title="Current running task",
            request=RequestTokens(status="running", updated_at=now),
            activity=Activity(
                kind="agent",
                detail="还在继续",
                timestamp=now,
            ),
            task_started_at=now - timedelta(seconds=20),
        )
        historical_completed = ParsedSession(
            session_id="session-history",
            session_title="Old finished task",
            request=RequestTokens(status="confirmed", updated_at=now - timedelta(minutes=2)),
            activity=Activity(
                kind="agent",
                detail="历史完成",
                timestamp=now - timedelta(minutes=2),
            ),
            last_output=Activity(
                kind="agent",
                detail="历史完成",
                timestamp=now - timedelta(minutes=2),
            ),
            task_started_at=now - timedelta(minutes=4),
            task_completed_at=now - timedelta(minutes=2),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_path = root / "current.jsonl"
            historical_path = root / "history.jsonl"
            current_path.write_text("", encoding="utf-8")
            historical_path.write_text("", encoding="utf-8")
            context = SimpleNamespace(
                sessions_root=root,
                parser=SimpleNamespace(
                    parse_file=MagicMock(
                        side_effect=lambda path: historical_completed
                        if Path(path) == historical_path
                        else current_snapshot
                    )
                ),
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, current_snapshot, current_path)

        self.assertEqual([item.id for item in items], ["session-current"])

    def test_fresh_historical_completed_overlay_item_shows_on_startup(self) -> None:
        now = datetime.now().astimezone()
        current_snapshot = ParsedSession(
            session_id="session-current",
            session_title="Current running task",
            request=RequestTokens(status="running", updated_at=now),
            activity=Activity(
                kind="agent",
                detail="还在继续",
                timestamp=now,
            ),
            task_started_at=now - timedelta(seconds=20),
        )
        historical_completed = ParsedSession(
            session_id="session-history",
            session_title="Fresh finished task",
            request=RequestTokens(status="confirmed", updated_at=now - timedelta(seconds=15)),
            activity=Activity(
                kind="agent",
                detail="刚刚完成",
                timestamp=now - timedelta(seconds=15),
            ),
            last_output=Activity(
                kind="agent",
                detail="刚刚完成",
                timestamp=now - timedelta(seconds=15),
            ),
            task_started_at=now - timedelta(minutes=1),
            task_completed_at=now - timedelta(seconds=15),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_path = root / "current.jsonl"
            historical_path = root / "history.jsonl"
            current_path.write_text("", encoding="utf-8")
            historical_path.write_text("", encoding="utf-8")
            context = SimpleNamespace(
                sessions_root=root,
                parser=SimpleNamespace(
                    parse_file=MagicMock(
                        side_effect=lambda path: historical_completed
                        if Path(path) == historical_path
                        else current_snapshot
                    )
                ),
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, current_snapshot, current_path)

        self.assertEqual(
            [item.id for item in items],
            ["session-current", "session-history"],
        )

    def test_current_completed_overlay_item_shows_on_startup(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()

        def completed_rows(session_id: str, offset_minutes: int) -> list[dict[str, object]]:
            return [
                {
                    "timestamp": (now - timedelta(minutes=offset_minutes + 4)).isoformat(),
                    "type": "session_meta",
                    "payload": {"id": session_id},
                },
                {
                    "timestamp": (now - timedelta(minutes=offset_minutes + 3)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": (now - timedelta(minutes=offset_minutes + 2)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "历史完成输出"},
                },
                {
                    "timestamp": (now - timedelta(minutes=offset_minutes + 1)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                },
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = [root / "history-a.jsonl", root / "history-b.jsonl"]
            for index, path in enumerate(paths):
                rows = completed_rows(f"session-history-{index}", index * 3)
                path.write_text(
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                    encoding="utf-8",
                )
            current_snapshot = parser.parse_file(paths[0])
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, current_snapshot, paths[0])

        self.assertEqual([item.id for item in items], ["session-history-0"])

    def test_aborted_task_does_not_stay_active(self) -> None:
        parser = JsonlSessionParser()
        now = datetime.now().astimezone()
        token_payload = {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 80,
                    "cached_input_tokens": 20,
                    "output_tokens": 15,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 95,
                },
                "total_token_usage": {
                    "input_tokens": 80,
                    "cached_input_tokens": 20,
                    "output_tokens": 15,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 95,
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session-current.jsonl"
            rows = [
                {
                    "timestamp": (now + timedelta(seconds=-15)).isoformat(),
                    "type": "session_meta",
                    "payload": {"id": "session-current"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-14)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": (now + timedelta(seconds=-13)).isoformat(),
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "上一轮正常输出"}],
                    },
                },
                {
                    "timestamp": (now + timedelta(seconds=-12)).isoformat(),
                    "type": "event_msg",
                    "payload": token_payload,
                },
                {
                    "timestamp": (now + timedelta(seconds=-11)).isoformat(),
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "text": (
                                    "<turn_aborted>\n"
                                    "The user interrupted the previous turn on purpose.\n"
                                    "</turn_aborted>"
                                )
                            }
                        ],
                    },
                },
                {
                    "timestamp": (now + timedelta(seconds=-10)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "turn_aborted", "reason": "interrupted"},
                },
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
                encoding="utf-8",
            )
            snapshot = parser.parse_file(path)
            context = SimpleNamespace(
                sessions_root=root,
                parser=parser,
                active_session_tracker=None,
            )

            items = active_work_items_for_snapshot(context, snapshot, path)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertFalse(snapshot.slow.current_gap_active)
        self.assertEqual(items, [])

    def test_windows_rounded_shell_rows_do_not_paint_black_corner_base(self) -> None:
        if not sys.platform.startswith("win"):
            self.skipTest("Windows-specific rounded shell behavior")
        rows = _rounded_shell_surface_rows(
            width=40,
            height=20,
            radius=9,
            bg=HUD_BG,
            border=HUD_PANEL_BORDER,
            outside=HUD_WINDOW_TRANSPARENT,
        )

        self.assertIn(HUD_WINDOW_TRANSPARENT.lower(), rows[0].lower())
        self.assertNotIn(HUD_WINDOW_OUTSIDE.lower(), rows[0].lower())

    def test_square_shell_rows_render_full_rectangular_border(self) -> None:
        rows = _rounded_shell_surface_rows(
            width=6,
            height=4,
            radius=0,
            bg=HUD_BG,
            border=HUD_PANEL_BORDER,
            outside=HUD_WINDOW_TRANSPARENT,
        )

        self.assertEqual(
            rows[0].lower(),
            "{" + " ".join([HUD_PANEL_BORDER.lower()] * 6) + "}",
        )
        self.assertEqual(
            rows[1].lower(),
            "{"
            + " ".join(
                [
                    HUD_PANEL_BORDER.lower(),
                    HUD_BG.lower(),
                    HUD_BG.lower(),
                    HUD_BG.lower(),
                    HUD_BG.lower(),
                    HUD_PANEL_BORDER.lower(),
                ]
            )
            + "}",
        )
        self.assertNotIn(HUD_WINDOW_TRANSPARENT.lower(), "".join(rows).lower())

    def test_work_overlay_header_text_formats_time_elapsed_and_title(self) -> None:
        started_at = datetime(2026, 6, 15, 9, 8, 7).astimezone()
        text = _work_overlay_header_text(
            started_at,
            "已处理 42s",
            "这是一个很长很长很长很长很长很长很长很长很长很长的会话标题",
        )

        self.assertTrue(text.startswith("09:08:07 | 已处理 42s | "))
        self.assertIn("...", text)

    def test_work_overlay_multiline_elided_text_limits_body_to_three_lines(self) -> None:
        try:
            import PySide6  # noqa: F401
            from PySide6.QtGui import QFont
            from PySide6.QtWidgets import QApplication
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = QApplication.instance() or QApplication(sys.argv[:1])
            app.setQuitOnLastWindowClosed(False)
            text = (
                "结论：有残留，而且 Codex App 现在确实还会尝试调用已卸载的 OMX，"
                "主要命中点是配置和启动目录、环境变量、探测缓存以及旧的依赖回退逻辑，"
                "这一段需要被压成三行以内，否则就会和底部状态栏发生重叠，"
                "而且长路径和长命令在窄卡片里很容易把第四行挤出来。"
            )
            display = _multiline_elided_text(
                text,
                font=QFont("Microsoft YaHei UI", 8),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
                max_lines=3,
            )

            self.assertLessEqual(len(display.splitlines()), 3)
            self.assertNotEqual(display, text)
            self.assertTrue(display.endswith("…"))
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_pending_workdir_window_expands_short_anchor_leftward(self) -> None:
        x, y, width, height = _pending_workdir_window_rect(
            300,
            40,
            28,
            18,
            pending=True,
            screen_left=0,
        )

        self.assertEqual((y, width, height), (40, 150, 18))
        self.assertEqual(x + width, 328)

    def test_pending_workdir_window_keeps_normal_anchor_size_when_idle(self) -> None:
        self.assertEqual(
            _pending_workdir_window_rect(
                300,
                40,
                28,
                18,
                pending=False,
                screen_left=0,
            ),
            (300, 40, 28, 18),
        )

    def test_work_overlay_helper_delegates_to_qt_runner(self) -> None:
        runner = MagicMock(return_value=7)
        with patch("codex_usage_hud.cli._work_overlay_helper_qt", return_value=runner):
            exit_code = run_work_overlay_helper("overlay-state.json")

        self.assertEqual(exit_code, 7)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], "overlay-state.json")

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

    def test_budget_progress_metrics_split_overflow_from_main_fill(self) -> None:
        snapshot = ParsedSession(
            today_tokens=6600000,
            today_cost_usd=112.0,
            daily_limit_usd=100.0,
            week_tokens=124700000,
            week_cost_usd=128.0,
            weekly_limit_usd=100.0,
        )

        collapsed = _top_collapsed_progress_metrics(snapshot)
        budget = _top_budget_progress_metrics(snapshot)

        self.assertEqual(collapsed[1].right_text, "112%")
        self.assertEqual(collapsed[1].ratio, 1.0)
        self.assertAlmostEqual(collapsed[1].overflow_ratio, 0.12, places=3)
        self.assertEqual(collapsed[2].right_text, "128%")
        self.assertEqual(collapsed[2].ratio, 1.0)
        self.assertAlmostEqual(collapsed[2].overflow_ratio, 0.28, places=3)

        self.assertEqual(budget[0].right_text, "")
        self.assertAlmostEqual(budget[0].overflow_ratio, 0.12, places=3)
        self.assertEqual(budget[0].overflow_badge, "+12% / +$12.00")
        self.assertEqual(budget[1].right_text, "")
        self.assertAlmostEqual(budget[1].overflow_ratio, 0.28, places=3)
        self.assertEqual(budget[1].overflow_badge, "+28% / +$28.00")

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


class WorkOverlayTransitionTests(unittest.TestCase):
    def assertRectAlmostEqual(
        self,
        actual: tuple[float, float, float, float],
        expected: tuple[float, float, float, float],
        *,
        places: int = 3,
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=places)

    @staticmethod
    def _light_overlay_theme() -> dict[str, str]:
        return {
            "surface": "#f7f8fb",
            "panelSurface": "#fafbfc",
            "panelBorder": "#c8d2dc",
            "text": "#111111",
            "muted": "#5e6a78",
            "accent": "#0969da",
            "info": "#8250df",
            "warning": "#bf8700",
            "error": "#cf222e",
            "success": "#1a7f37",
            "requestPanelSurface": "#ffffff",
            "requestText": "#1f2328",
            "requestMuted": "#656d76",
            "progressOverflowBadge": "#f4c7c3",
            "progressOverflowBadgeEdge": "#cf222e",
        }

    @staticmethod
    def _dark_overlay_theme() -> dict[str, str]:
        return {
            "surface": "#181818",
            "panelSurface": "#1f1f1f",
            "panelBorder": "#333333",
            "text": "#ffffff",
            "muted": "#a0a0a0",
            "accent": "#339cff",
            "info": "#ad7bf9",
            "warning": "#ffb86b",
            "error": "#fa423e",
            "success": "#40c977",
            "requestPanelSurface": "#202020",
            "requestText": "#e8e8e8",
            "requestMuted": "#8f8f8f",
            "progressOverflowBadge": "#7F3E3A",
            "progressOverflowBadgeEdge": "#FF875A",
        }

    @staticmethod
    def _hex_distance(left: str, right: str) -> float:
        def rgb(value: str) -> tuple[int, int, int]:
            text = str(value).strip().lstrip("#")
            return (
                int(text[0:2], 16),
                int(text[2:4], 16),
                int(text[4:6], 16),
            )

        left_rgb = rgb(left)
        right_rgb = rgb(right)
        return sum((a - b) ** 2 for a, b in zip(left_rgb, right_rgb)) ** 0.5

    def test_detect_card_to_completed_transition(self) -> None:
        old_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
            {"id": "2", "status": "recent", "title": "已完成"},
        ]
        new_items = [
            {"id": "1", "status": "recent", "title": "已完成"},
            {"id": "2", "status": "recent", "title": "已完成"},
        ]
        result = _detect_transition(old_items, new_items)
        self.assertEqual(result, "card_to_completed")
        self.assertEqual(_detect_transition_item_id(old_items, new_items), "1")

    def test_detect_completed_to_card_transition(self) -> None:
        old_items = [
            {"id": "1", "status": "recent", "title": "已完成"},
            {"id": "2", "status": "tool", "title": "正在执行"},
        ]
        new_items = [
            {"id": "1", "status": "tool", "title": "继续执行"},
            {"id": "2", "status": "tool", "title": "正在执行"},
        ]
        result = _detect_transition(old_items, new_items)
        self.assertEqual(result, "completed_to_card")
        self.assertEqual(_detect_transition_item_id(old_items, new_items), "1")

    def test_no_transition_when_kinds_unchanged(self) -> None:
        old_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
            {"id": "2", "status": "recent", "title": "已完成"},
        ]
        new_items = [
            {"id": "1", "status": "tool", "title": "更新了标题"},
            {"id": "2", "status": "recent", "title": "已完成 2"},
        ]
        result = _detect_transition(old_items, new_items)
        self.assertIsNone(result)
        self.assertEqual(_detect_transition_item_id(old_items, new_items), "")

    def test_no_transition_for_empty_ids(self) -> None:
        old_items = [
            {"id": "", "status": "tool", "title": "正在执行"},
        ]
        new_items = [
            {"id": "", "status": "recent", "title": "已完成"},
        ]

        result = _detect_transition(old_items, new_items)

        self.assertIsNone(result)
        self.assertEqual(_detect_transition_item_id(old_items, new_items), "")

    def test_no_transition_with_new_items(self) -> None:
        old_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
        ]
        new_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
            {"id": "2", "status": "recent", "title": "新完成"},
        ]
        result = _detect_transition(old_items, new_items)
        self.assertIsNone(result)

    def test_no_transition_with_removed_items(self) -> None:
        old_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
            {"id": "2", "status": "recent", "title": "已完成"},
        ]
        new_items = [
            {"id": "1", "status": "tool", "title": "正在执行"},
        ]
        result = _detect_transition(old_items, new_items)
        self.assertIsNone(result)

    def test_completed_dismiss_hides_source_before_effect_reset(self) -> None:
        self.assertTrue(_transition_hides_source_before_effect_reset("completed_dismiss"))
        self.assertFalse(_transition_hides_source_before_effect_reset("card_to_completed"))
        self.assertFalse(_transition_hides_source_before_effect_reset("completed_to_card"))

    def test_overlay_payload_signature_changes_when_theme_changes(self) -> None:
        items = [{"id": "1", "status": "tool", "title": "正在执行"}]

        light_signature = _overlay_payload_signature(items, self._light_overlay_theme())
        dark_signature = _overlay_payload_signature(items, self._dark_overlay_theme())

        self.assertNotEqual(light_signature, dark_signature)

    def test_workdir_display_always_uses_leaf_even_when_duplicates_exist(self) -> None:
        items = [
            {"id": "a", "status": "tool", "workdir": r"E:\Work\client\app"},
            {"id": "b", "status": "tool", "workdir": r"D:\Archive\client\app"},
            {"id": "c", "status": "tool", "workdir": r"E:\Work\server\app"},
        ]

        self.assertEqual(
            [_workdir_display_name(item) for item in items],
            ["app", "app", "app"],
        )

    def test_workdir_display_falls_back_to_workdir_name_leaf(self) -> None:
        item = {"id": "a", "status": "tool", "workdirName": r"Alpha\app"}

        self.assertEqual(_workdir_display_name(item), "app")

    def test_round_badge_palette_uses_status_color_and_contrast_text(self) -> None:
        light_theme = self._light_overlay_theme()
        dark_theme = self._dark_overlay_theme()

        light_tool = _round_badge_palette("tool", light_theme)
        light_error = _round_badge_palette("error", light_theme)
        dark_tool = _round_badge_palette("tool", dark_theme)

        self.assertNotEqual(light_tool["background"], light_error["background"])
        self.assertEqual(light_tool["text"], light_theme["text"])
        self.assertEqual(light_error["text"], light_theme["surface"])
        self.assertEqual(dark_tool["text"], dark_theme["surface"])

    def test_completed_transition_palette_matches_completed_badge_palette(self) -> None:
        light_theme = self._light_overlay_theme()

        badge_palette = _completed_badge_palette(light_theme)
        transition_palette = _transition_palette("card_to_completed", light_theme)

        self.assertEqual(transition_palette["fillStart"], badge_palette["fillStart"])
        self.assertEqual(transition_palette["fillMid"], badge_palette["fillMid"])
        self.assertEqual(transition_palette["fillEnd"], badge_palette["fillEnd"])
        self.assertEqual(transition_palette["border"], badge_palette["border"])
        self.assertEqual(transition_palette["markText"], badge_palette["checkText"])

    def test_completed_badge_palette_keeps_readable_text_and_accent_ring(self) -> None:
        light_theme = self._light_overlay_theme()
        dark_theme = self._dark_overlay_theme()

        for theme in (light_theme, dark_theme):
            palette = _completed_badge_palette(theme)
            self.assertLess(
                self._hex_distance(palette["fillMid"], theme["panelSurface"]),
                self._hex_distance(palette["fillMid"], theme["success"]),
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["fillMid"], palette["checkText"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["fillMid"], palette["titleText"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["fillMid"], palette["workdirText"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["fillMid"], palette["elapsedText"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["statBoxFill"], palette["statValue"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["statBoxFill"], palette["statLabel"]),
                4.5,
            )
            self.assertGreaterEqual(
                _theme_contrast_ratio(palette["fillMid"], palette["ring"]),
                2.15,
            )
            self.assertNotEqual(palette["ring"], palette["dashedRing"])
            self.assertNotEqual(palette["fillMid"], palette["fillEnd"])

    def test_find_completed_item_position_aligns_right_with_spacing(self) -> None:
        items = [
            {"id": "oldest", "status": "recent"},
            {"id": "latest", "status": "recent"},
        ]

        oldest = _find_item_position(items, "oldest", "completed", layout_width=430)
        latest = _find_item_position(items, "latest", "completed", layout_width=430)

        self.assertEqual(oldest, (86, 0))
        self.assertEqual(latest, (262, 0))

    def test_find_active_item_position_respects_completed_row_and_right_alignment(self) -> None:
        items = [
            {"id": "done", "status": "recent"},
            {"id": "running", "status": "tool"},
        ]

        active = _find_item_position(items, "running", "card", layout_width=520)

        self.assertEqual(active, (90, 188))

    def test_completed_badge_slot_rects_put_latest_on_right(self) -> None:
        items = [
            {"id": "oldest", "status": "recent"},
            {"id": "middle", "status": "recent"},
            {"id": "latest", "status": "recent"},
        ]

        slots = _completed_badge_slot_rects(items, layout_width=520)

        self.assertRectAlmostEqual(slots["oldest"], (0.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(slots["middle"], (176.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(slots["latest"], (352.0, 0.0, 168.0, 168.0))

    def test_existing_completed_badge_moves_left_for_new_completed_slot(self) -> None:
        old_items = [
            {"id": "old", "status": "recent"},
            {"id": "running", "status": "tool"},
        ]
        new_items = [
            {"id": "old", "status": "recent"},
            {"id": "running", "status": "recent"},
        ]

        moves = _completed_badge_slot_moves(old_items, new_items, layout_width=430)

        self.assertRectAlmostEqual(moves["old"][0], (262.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["old"][1], (86.0, 0.0, 168.0, 168.0))

    def test_remaining_completed_badge_moves_right_after_restore(self) -> None:
        old_items = [
            {"id": "old", "status": "recent"},
            {"id": "restoring", "status": "recent"},
        ]
        new_items = [
            {"id": "old", "status": "recent"},
            {"id": "restoring", "status": "tool"},
        ]

        moves = _completed_badge_slot_moves(old_items, new_items, layout_width=430)

        self.assertRectAlmostEqual(moves["old"][0], (86.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["old"][1], (262.0, 0.0, 168.0, 168.0))

    def test_completed_restore_stages_clicked_badge_at_right_edge(self) -> None:
        old_items = [
            {"id": "oldest", "status": "recent"},
            {"id": "restoring", "status": "recent"},
            {"id": "latest", "status": "recent"},
            {"id": "active", "status": "tool"},
        ]

        staged_items = _completed_restore_staged_items(old_items, "restoring")
        slots = _completed_badge_slot_rects(staged_items, layout_width=520)

        self.assertEqual([item["id"] for item in staged_items[:3]], ["oldest", "latest", "restoring"])
        self.assertRectAlmostEqual(slots["oldest"], (0.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(slots["latest"], (176.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(slots["restoring"], (352.0, 0.0, 168.0, 168.0))

    def test_completed_restore_moves_remaining_badges_in_two_phases(self) -> None:
        old_items = [
            {"id": "oldest", "status": "recent"},
            {"id": "restoring", "status": "recent"},
            {"id": "latest", "status": "recent"},
            {"id": "active", "status": "tool"},
        ]
        new_items = [
            {"id": "oldest", "status": "recent"},
            {"id": "restoring", "status": "tool"},
            {"id": "latest", "status": "recent"},
            {"id": "active", "status": "tool"},
        ]

        moves = _completed_badge_restore_slot_moves(
            old_items,
            new_items,
            "restoring",
            layout_width=520,
        )

        self.assertRectAlmostEqual(moves["latest"][0], (352.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["latest"][1], (176.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["latest"][2], (352.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["oldest"][0], (0.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["oldest"][1], (0.0, 0.0, 168.0, 168.0))
        self.assertRectAlmostEqual(moves["oldest"][2], (176.0, 0.0, 168.0, 168.0))

    def test_transition_layout_width_keeps_wider_completed_row_during_restore(self) -> None:
        old_items = [
            {"id": "oldest", "status": "recent"},
            {"id": "restoring", "status": "recent"},
            {"id": "latest", "status": "recent"},
            {"id": "active", "status": "tool"},
        ]
        new_items = [
            {"id": "oldest", "status": "recent"},
            {"id": "restoring", "status": "tool"},
            {"id": "latest", "status": "recent"},
            {"id": "active", "status": "tool"},
        ]

        self.assertEqual(_transition_layout_width(old_items, new_items), 520)

    def test_overlay_required_height_includes_cards_pushed_down_by_restore(self) -> None:
        items = [
            {"id": "remaining", "status": "recent"},
            {"id": "restoring", "status": "tool"},
            {"id": "first", "status": "tool"},
            {"id": "second", "status": "tool"},
            {"id": "third", "status": "tool"},
        ]

        self.assertEqual(_overlay_items_required_height(items, layout_width=430), 652)

    def test_card_to_completed_morphs_to_right_edge_before_vertical_rise(self) -> None:
        source = (0.0, 188.0, 430.0, 110.0)
        target = (262.0, 0.0, 168.0, 168.0)

        start = _transition_rect_for_progress("card_to_completed", source, target, 0.0)
        after_morph = _transition_rect_for_progress("card_to_completed", source, target, 0.35)
        moving = _transition_rect_for_progress("card_to_completed", source, target, 0.75)
        final = _transition_rect_for_progress("card_to_completed", source, target, 1.0)

        self.assertRectAlmostEqual(start, source)
        self.assertRectAlmostEqual(after_morph, (262.0, 159.0, 168.0, 168.0))
        self.assertEqual(moving[2:], (168.0, 168.0))
        self.assertAlmostEqual(moving[0], after_morph[0])
        self.assertLess(moving[1], after_morph[1])
        self.assertRectAlmostEqual(final, target)

    def test_top_card_to_completed_circle_stays_inside_transition_canvas(self) -> None:
        source = (0.0, 0.0, 430.0, 110.0)
        target = (262.0, 0.0, 168.0, 168.0)

        after_morph = _transition_rect_for_progress("card_to_completed", source, target, 0.35)
        required_height = _transition_required_height("card_to_completed", source, target)

        self.assertRectAlmostEqual(after_morph, (262.0, 0.0, 168.0, 168.0))
        self.assertGreaterEqual(required_height, 168)
        self.assertLessEqual(after_morph[1] + after_morph[3], required_height)

    def test_completed_to_card_returns_on_right_edge_before_expanding(self) -> None:
        source = (262.0, 0.0, 168.0, 168.0)
        target = (0.0, 188.0, 430.0, 110.0)

        moving = _transition_rect_for_progress("completed_to_card", source, target, 0.25)
        before_expand = _transition_rect_for_progress("completed_to_card", source, target, 0.55)
        final = _transition_rect_for_progress("completed_to_card", source, target, 1.0)

        self.assertEqual(moving[2:], (168.0, 168.0))
        self.assertAlmostEqual(moving[0], source[0])
        self.assertGreater(moving[1], source[1])
        self.assertRectAlmostEqual(before_expand, (262.0, 159.0, 168.0, 168.0))
        self.assertRectAlmostEqual(final, target)

    def test_card_clearance_moves_left_before_completed_circle_takes_off(self) -> None:
        self.assertEqual(_transition_clearance_offset("card_to_completed", 0.30), 0.0)
        self.assertLess(_transition_clearance_offset("card_to_completed", 0.50), 0.0)
        self.assertLess(_transition_clearance_offset("card_to_completed", 0.80), 0.0)
        self.assertAlmostEqual(_transition_clearance_offset("card_to_completed", 1.0), 0.0)

    def test_completed_badge_slot_shift_waits_until_morph_pause(self) -> None:
        self.assertEqual(_transition_slot_shift_progress("card_to_completed", 0.30), 0.0)
        self.assertGreater(_transition_slot_shift_progress("card_to_completed", 0.50), 0.0)
        self.assertEqual(_transition_slot_shift_progress("card_to_completed", 0.75), 1.0)

    def test_restore_clearance_uses_reverse_path_timing(self) -> None:
        self.assertLess(_transition_clearance_offset("completed_to_card", 0.10), 0.0)
        self.assertLess(_transition_clearance_offset("completed_to_card", 0.35), 0.0)
        self.assertAlmostEqual(_transition_clearance_offset("completed_to_card", 0.70), 0.0)

    def test_remembered_card_rect_keeps_stack_y_and_current_right_edge(self) -> None:
        remembered = (90.0, 306.0, 430.0, 110.0)

        rect = _remembered_card_rect_for_layout(remembered, layout_width=430)

        self.assertRectAlmostEqual(rect, (0.0, 306.0, 430.0, 110.0))

    def test_find_item_rect_uses_stack_spacing_between_completed_row_and_cards(self) -> None:
        items = [
            {"id": "done", "status": "recent"},
            {"id": "first", "status": "tool"},
            {"id": "second", "status": "tool"},
        ]

        first = _find_item_rect(items, "first", "card", layout_width=430)
        second = _find_item_rect(items, "second", "card", layout_width=430)

        self.assertRectAlmostEqual(first, (0.0, 188.0, 430.0, 110.0))
        self.assertRectAlmostEqual(second, (0.0, 306.0, 430.0, 110.0))


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

    def test_request_total_line_matches_renderer_token_order(self) -> None:
        snapshot = ParsedSession()
        snapshot.request.input_tokens = 194_000
        snapshot.request.cached_tokens = 93_000
        snapshot.request.output_tokens = 852
        snapshot.request.reasoning_tokens = 516
        snapshot.request.total_tokens = 295_000
        snapshot.request.cost_usd = 0.094
        snapshot.request.estimated = False

        line = _request_total_line(snapshot)

        self.assertTrue(line.startswith("$0.094 ↑194k"))
        self.assertLess(line.index("↑194k"), line.index("◎48%"))
        self.assertLess(line.index("◎48%"), line.index("↓852"))
        self.assertLess(line.index("↻93k"), line.index("∑295k"))
        self.assertTrue(line.endswith("↻93k ∑295k"))

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

    def test_round_entry_matches_renderer_token_order(self) -> None:
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

        entry = _round_entry(item, "gpt-5.4")

        self.assertTrue(entry.startswith("#33 $0.094 20:36:26 ↑194k"))
        self.assertLess(entry.index("↑194k"), entry.index("◎48%"))
        self.assertLess(entry.index("◎48%"), entry.index("↓852"))
        self.assertLess(entry.index("↻93k"), entry.index("∑295k"))
        self.assertTrue(entry.endswith("↻93k ∑295k"))

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

        first = _round_entry(rows[0], "gpt-5.4", widths=widths)
        second = _round_entry(rows[1], "gpt-5.4", widths=widths)

        self.assertTrue(first.startswith("#  9 $0.100 --:--:-- ↑1,000"))
        self.assertTrue(first.endswith("↻0 ∑2,000"))
        self.assertTrue(second.startswith("#128 $12.34 --:--:-- ↑ 1.0M"))
        self.assertTrue(second.endswith("↻0 ∑ 1.2M"))


class HudSettingsStoreTests(unittest.TestCase):
    def test_settings_round_trip_persists_only_pinned_position_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            store = HudSettingsStore(path)
            settings = HudSettings(
                top=WindowPlacement(
                    pinned=True,
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
                    collapsed_width_locked=True,
                ),
                request=WindowPlacement(
                    pinned=False,
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
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(loaded.top.pinned)
        self.assertFalse(loaded.request.pinned)
        self.assertIsNone(loaded.top.relative_x)
        self.assertIsNone(loaded.top.relative_y)
        self.assertEqual(loaded.top.width, 640)
        self.assertEqual(loaded.top.height, 390)
        self.assertIsNone(loaded.top.width_ratio)
        self.assertIsNone(loaded.top.anchor_x_ratio)
        self.assertIsNone(loaded.top.anchor_y_ratio)
        self.assertIsNone(loaded.top.anchor_source)
        self.assertFalse(loaded.top.collapsed_width_locked)
        self.assertIsNone(loaded.request.relative_bottom)
        self.assertIsNone(loaded.request.width)
        self.assertIsNone(loaded.request.height)
        self.assertIsNone(loaded.request.width_ratio)
        self.assertIsNone(loaded.request.anchor_x_ratio)
        self.assertIsNone(loaded.request.anchor_y_ratio)
        self.assertIsNone(loaded.request.anchor_source)
        self.assertFalse(loaded.request.collapsed_width_locked)
        self.assertEqual(
            raw["top"],
            {
                "pinned": True,
                "absolute_x": 900,
                "absolute_y": 50,
                "width": 640,
                "height": 390,
            },
        )
        self.assertEqual(raw["request"], {"pinned": False})

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

    def test_warning_dismissal_preserves_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                '{"user":{"daily_budget_usd":12.5},"top":{"width":320}}',
                encoding="utf-8",
            )

            dismiss_warning_for_today(path, now=datetime(2026, 6, 21, 12, 0))
            raw = json.loads(path.read_text(encoding="utf-8"))
            dismissed_same_day = warning_dismissed_today(
                path,
                now=datetime(2026, 6, 21, 23, 59),
            )
            dismissed_next_day = warning_dismissed_today(
                path,
                now=datetime(2026, 6, 22, 0, 1),
            )

        self.assertTrue(dismissed_same_day)
        self.assertFalse(dismissed_next_day)
        self.assertEqual(raw["runtime"]["warning_dismissed_date"], "2026-06-21")
        self.assertEqual(raw["user"]["daily_budget_usd"], 12.5)
        self.assertEqual(raw["top"]["width"], 320)


@pytest.mark.ui
@pytest.mark.qt_ui
class QtHudWindowLifecycleTests(unittest.TestCase):
    def test_qt_manual_input_priority_temporarily_defers_refresh_work(self) -> None:
        if getattr(qt_hud_module, "QApplication", None) is None:
            self.skipTest("PySide6 unavailable")
        window = object.__new__(qt_hud_module._QtHudWindowImpl)
        window._click_priority_hold_until = 0.0
        window._pointer_priority_hold_until = 0.0
        window._interaction_block_until = 0.0
        window.hide_until_attached = False
        window._attached = True

        window._mark_pointer_priority()

        self.assertTrue(window._pointer_priority_active())
        self.assertTrue(window.should_defer_background_work())
        self.assertFalse(window.should_refresh_snapshot())
        self.assertEqual(
            window.refresh_delay_ms(1000),
            qt_hud_module.QT_HUD_POINTER_REFRESH_DELAY_MS,
        )

        window._mark_click_priority()
        self.assertEqual(
            window.refresh_delay_ms(1000),
            qt_hud_module.QT_HUD_CLICK_REFRESH_DELAY_MS,
        )

    def test_qt_follow_skips_hide_while_manual_input_is_active(self) -> None:
        if getattr(qt_hud_module, "QApplication", None) is None:
            self.skipTest("PySide6 unavailable")

        class _Panel:
            def __init__(self) -> None:
                self.hidden = False

            def geometry_interaction_active(self) -> bool:
                return False

            def hide(self) -> None:
                self.hidden = True

        class _Locator:
            def __init__(self) -> None:
                self.find_called = False

            def find(self) -> None:
                self.find_called = True
                return None

        window = object.__new__(qt_hud_module._QtHudWindowImpl)
        window._interaction_block_until = 0.0
        window._click_priority_hold_until = time.monotonic() + 1.0
        window._pointer_priority_hold_until = 0.0
        window._attached = True
        window.top_window = _Panel()
        window.request_window = _Panel()
        window._settings_dialog = None
        window.locator = _Locator()

        self.assertTrue(window._follow_codex_window())

        self.assertFalse(window.locator.find_called)
        self.assertFalse(window.top_window.hidden)
        self.assertFalse(window.request_window.hidden)

    def test_qt_panel_toggle_uses_press_position_when_release_position_moves(self) -> None:
        if getattr(qt_hud_module, "QApplication", None) is None:
            self.skipTest("PySide6 unavailable")
        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication([])
            app.setQuitOnLastWindowClosed(False)
            panel = qt_hud_module._PanelWindow(
                target="top",
                width=320,
                collapsed_height=36,
                expanded_height=240,
                on_interaction=lambda: None,
            )
            try:
                panel._dragging = False
                panel._drag_origin = qt_hud_module.QPoint(1, 1)
                panel._drag_window_origin = qt_hud_module.QPoint(2, 2)
                panel._toggle_press_position = qt_hud_module.QPoint(8, 8)
                panel._toggle_press_global = qt_hud_module.QPoint(80, 80)
                panel._should_toggle_from_click = lambda position: position == qt_hud_module.QPoint(8, 8)
                toggles = []
                panel.toggle_expanded = lambda: toggles.append(True)

                handled = panel._finish_toggle_click(
                    qt_hud_module.QPoint(81, 80),
                    qt_hud_module.QPoint(160, 160),
                )

                self.assertTrue(handled)
                self.assertEqual(toggles, [True])
                self.assertIsNone(panel._drag_origin)
                self.assertIsNone(panel._drag_window_origin)
                self.assertIsNone(panel._toggle_press_position)
                self.assertIsNone(panel._toggle_press_global)
            finally:
                panel.close()
                panel.deleteLater()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_panel_resize_grip_only_shows_when_expanded(self) -> None:
        if getattr(qt_hud_module, "QApplication", None) is None:
            self.skipTest("PySide6 unavailable")
        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication([])
            app.setQuitOnLastWindowClosed(False)
            panels = [
                qt_hud_module._PanelWindow(
                    target="top",
                    width=320,
                    collapsed_height=36,
                    expanded_height=240,
                    on_interaction=lambda: None,
                ),
                qt_hud_module._PanelWindow(
                    target="request",
                    width=320,
                    collapsed_height=32,
                    expanded_height=160,
                    on_interaction=lambda: None,
                    grow_from_bottom=True,
                ),
            ]
            try:
                for panel in panels:
                    panel.show()
                    app.processEvents()
                    self.assertFalse(panel._grip.isVisible())
                    panel.set_expanded(True)
                    app.processEvents()
                    self.assertTrue(panel._grip.isVisible())
                    panel.set_expanded(False)
                    app.processEvents()
                    self.assertFalse(panel._grip.isVisible())
            finally:
                for panel in panels:
                    panel.close()
                    panel.deleteLater()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_panel_resize_tracking_ignores_settings_dialog_children(self) -> None:
        if getattr(qt_hud_module, "QApplication", None) is None:
            self.skipTest("PySide6 unavailable")
        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication([])
            app.setQuitOnLastWindowClosed(False)
            panel = qt_hud_module._PanelWindow(
                target="top",
                width=320,
                collapsed_height=36,
                expanded_height=240,
                on_interaction=lambda: None,
            )
            dialog = qt_hud_module.QDialog(panel)
            button = qt_hud_module.QPushButton("保存", dialog)
            try:
                panel._install_resize_cursor_tracking(panel)

                self.assertTrue(bool(panel.property("qtHudResizeCursorTracking")))
                self.assertFalse(bool(dialog.property("qtHudResizeCursorTracking")))
                self.assertFalse(bool(button.property("qtHudResizeCursorTracking")))
            finally:
                dialog.close()
                dialog.deleteLater()
                panel.close()
                panel.deleteLater()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_hud_window_updates_closes_and_keeps_core_widgets(self) -> None:
        try:
            import PySide6  # noqa: F401
            from PySide6.QtWidgets import QLabel, QFrame, QPushButton
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
                store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "geometry_settings.json")
                hud_store.save(HudSettings.empty())
                class _FakeUpdateState:
                    def __init__(self, message: str = "可安装更新") -> None:
                        self.message = message

                    def to_dict(self) -> dict[str, object]:
                        return {
                            "visible": True,
                            "icon": "install",
                            "phase": "ready",
                            "title": self.message,
                            "message": self.message,
                        }

                class _FakeUpdateManager:
                    def __init__(self) -> None:
                        self.clicks = 0

                    def status(self) -> _FakeUpdateState:
                        return _FakeUpdateState()

                    def handle_click(self) -> _FakeUpdateState:
                        self.clicks += 1
                        return _FakeUpdateState("已启动安装器")

                update_manager = _FakeUpdateManager()
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=store,
                        hud_settings_store=hud_store,
                        update_manager=update_manager,
                    )
                try:
                    self.assertEqual(window.top_window.width(), qt_hud_module.QT_HUD_TOP_WIDTH)
                    self.assertEqual(window.top_window.height(), qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                    self.assertEqual(window.request_window.width(), qt_hud_module.QT_HUD_REQUEST_WIDTH)
                    self.assertEqual(window.request_window.height(), qt_hud_module.QT_HUD_REQUEST_COLLAPSED_HEIGHT)
                    top_stack = window.top_window._stack
                    collapsed = top_stack.widget(0)
                    expanded = top_stack.widget(1)
                    window.update_display(
                        ParsedSession(
                            session_id="qt-test-session",
                            session_title="Qt HUD test",
                            status="parsed",
                            line_count=12,
                            token_events=2,
                            today_tokens=100,
                            week_tokens=200,
                            daily_limit_usd=100.0,
                            weekly_limit_usd=400.0,
                        )
                    )
                    window.attach_to_rect(WindowRect(left=100, top=120, right=1300, bottom=920))
                    _top_x, _top_y, top_anchor_width, _top_height = _visual_anchor_geometry(
                        "top",
                        WindowRect(left=100, top=120, right=1300, bottom=920),
                        False,
                    )
                    self.assertGreaterEqual(window.top_window.x(), 100)
                    self.assertEqual(window.top_window.width(), top_anchor_width)
                    self.assertLess(window.request_window.y(), 920)
                    window.top_window.set_expanded(True)

                    self.assertTrue(window.top_window.isVisible())
                    self.assertTrue(window.request_window.isVisible())
                    self.assertIs(top_stack.widget(0), collapsed)
                    self.assertIs(top_stack.widget(1), expanded)
                    self.assertGreaterEqual(window.top_window.width(), qt_hud_module.QT_HUD_TOP_STACK_WIDTH)
                    top_grid = window.top_window._top_grid
                    top_left = window.top_window._top_left
                    top_right = window.top_window._top_right
                    self.assertIsNotNone(top_grid)
                    self.assertIsNotNone(top_left)
                    self.assertIsNotNone(top_right)
                    assert top_grid is not None
                    assert top_left is not None
                    assert top_right is not None
                    _row_span, _column_span = 0, 0
                    left_row, left_column, _row_span, _column_span = top_grid.getItemPosition(
                        top_grid.indexOf(top_left)
                    )
                    row, column, _row_span, _column_span = top_grid.getItemPosition(
                        top_grid.indexOf(top_right)
                    )
                    self.assertEqual((left_row, left_column), (0, 0))
                    self.assertEqual((row, column), (0, 1))
                    self.assertLessEqual(top_left.geometry().right(), top_right.geometry().left())
                    self.assertLessEqual(top_right.geometry().right(), window.top_window.width())
                    self.assertGreater(top_left.width(), 0)
                    self.assertGreater(top_right.width(), 0)
                    self.assertTrue(window.top_window.session_meta.isVisible())
                    self.assertTrue(window.top_window.cache_progress.isVisible())
                    self.assertEqual(window.mode_switch_request, "")
                    request_collapsed_y = window.request_window.y()
                    request_bottom = window.request_window.geometry().bottom()
                    request_header = window.request_window.findChild(QFrame, "qtHudRequestExpandedHeader")
                    self.assertIsNotNone(request_header)
                    assert request_header is not None
                    window.request_window.set_expanded(True)
                    self.assertIsNotNone(window.request_window._animation)
                    assert window.request_window._animation is not None
                    request_target = window.request_window._animation.endValue()
                    self.assertEqual(
                        request_target.y(),
                        request_collapsed_y
                        - (
                            qt_hud_module.QT_HUD_REQUEST_EXPANDED_HEIGHT
                            - qt_hud_module.QT_HUD_REQUEST_COLLAPSED_HEIGHT
                        ),
                    )
                    self.assertEqual(request_target.height(), qt_hud_module.QT_HUD_REQUEST_EXPANDED_HEIGHT)
                    header_point = request_header.mapTo(window.request_window, request_header.rect().center())
                    request_content_point = window.request_window.request_scroll.mapTo(
                        window.request_window,
                        window.request_window.request_scroll.rect().center(),
                    )
                    self.assertTrue(window.request_window._should_toggle_from_click(header_point))
                    self.assertTrue(window.request_window._should_start_drag_from_click(header_point))
                    self.assertFalse(window.request_window._should_toggle_from_click(request_content_point))
                    self.assertFalse(window.request_window._should_start_drag_from_click(request_content_point))
                    window.request_window.set_expanded(False)
                    assert window.request_window._animation is not None
                    request_collapsed_target = window.request_window._animation.endValue()
                    self.assertEqual(request_collapsed_target.y(), request_collapsed_y)
                    self.assertEqual(request_collapsed_target.bottom(), request_bottom)
                    window.request_window.set_expanded(True)
                    window.request_window.update_payload(
                        {
                            "requestLine": "请求流水 | confirmed | gpt-5.5",
                            "requestStatus": "error",
                            "requestRowDetails": [
                                {
                                    "text": "#92 $0.152 19:10:17 ↑241k ◎100% ↓702 ◇286 ↻241k ∑242k",
                                    "prefix": "#92 $0.152 ",
                                    "time": "19:10:17",
                                    "suffix": " ↑241k ◎100% ↓702 ◇286 ↻241k ∑242k",
                                    "running": True,
                                    "startedAt": (datetime.now() - timedelta(seconds=7)).isoformat(),
                                }
                            ],
                        }
                    )
                    self.assertEqual(window.request_window.request_line.property("state"), "error")
                    self.assertTrue(window.request_window._row_labels[0].time.text().strip().endswith("s"))
                    top_labels = [
                        label.text()
                        for label in window.top_window.findChildren(QLabel)
                    ]
                    request_labels = [
                        label.text()
                        for label in window.request_window.findChildren(QLabel)
                    ]
                    self.assertNotIn("⋮⋮", top_labels)
                    self.assertNotIn("⋮⋮", request_labels)
                    self.assertIn("轮次流水", request_labels)
                    self.assertIn("最新在上", request_labels)
                    self.assertTrue(any(label.strip().endswith("s") for label in request_labels))
                    self.assertLessEqual(window.request_window._row_labels[0].maximumHeight(), 24)
                    self.assertEqual(window.request_window.rows_layout.spacing(), 0)
                    self.assertEqual(
                        window.request_window.request_scroll.verticalScrollBarPolicy(),
                        qt_hud_module.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
                    )
                    window.top_window.update_payload(
                        {
                            "topLine": "更新计划保留tk模式",
                            "topDetails": {
                                "title": "更新计划保留tk模式",
                                "session": "会话 85adaf5e5dab | 行 651 | 确认 15",
                                "sessionCost": "$11.66",
                                "sessionTokens": "15.3M",
                                "sessionRounds": "15 轮确认",
                                "sessionMix": "缓存命中 97%",
                                "sessionAverage": "均值 1.0M /轮",
                                "sessionComposition": "输入 / 缓存 / 输出 / 推理",
                                "sessionInputTokens": "241k",
                                "sessionCachedTokens": "241k",
                                "sessionOutputTokens": "702",
                                "sessionReasoningTokens": "286",
                                "warnings": "日额度已超过 80% 阈值",
                                "heavyRoundsSummary": "Top 3",
                                "heavyRounds": [
                                    {"title": "#92 $0.152 · ∑242k", "detail": "消耗构成"}
                                ],
                                "currentTaskLabel": "当前需求",
                                "currentTask": "更新计划保留tk模式",
                                "executingLabel": "正在执行",
                                "executing": "python -m unittest",
                                "activityState": "已完成",
                                "activityElapsedLabel": "已运行",
                                "activityElapsed": "34m25s",
                                "activityGapLabel": "当前等待",
                                "activityGap": "92轮",
                                "activityLastLabel": "需求轮次",
                                "activityLast": "15",
                                "slow": "最慢工具",
                                "gap": "最长等待",
                                "activityTrail": [
                                    {
                                        "time": "19:10:17",
                                        "title": "任务",
                                        "detail": "更新计划保留tk模式",
                                    },
                                    {"time": "19:09:49", "title": "轮次", "detail": "$0.123 · ∑241k"},
                                    {"time": "19:09:33", "title": "工具调用", "detail": "shell_command"},
                                    {"time": "19:09:23", "title": "工具完成", "detail": "Exit code: 0"},
                                    {"time": "19:08:56", "title": "轮次", "detail": "$0.168 · ∑240k"},
                                ],
                            },
                            "topCopies": {
                                "slow": "python -m unittest",
                                "gap": "等待详情",
                            },
                            "updateState": _FakeUpdateState().to_dict(),
                            "topProgress": {
                                "collapsed": [
                                    {"label": "本会话 15.3M", "ratio": 0.6, "tone": "session"}
                                ],
                                "cache": {
                                    "label": "缓存命中 97%",
                                    "rightText": "15.3M",
                                    "ratio": 0.97,
                                    "tone": "cache",
                                },
                                "budget": [
                                    {"label": "今日 $10.99/$100", "ratio": 0.11, "tone": "day"},
                                    {
                                        "label": "本周 $296.6/$400",
                                        "rightText": "$296.6",
                                        "overflowBadge": "超出 14%",
                                        "overflowRatio": 0.14,
                                        "ratio": 1.0,
                                        "tone": "week",
                                    },
                                ],
                            },
                        }
                    )
                    self.assertEqual(window.top_window.session_cost.text(), "$11.66")
                    self.assertEqual(window.top_window.session_input_tokens.text(), "241k")
                    self.assertEqual(window.top_window.session_cached_tokens.text(), "241k")
                    self.assertEqual(window.top_window.session_output_tokens.text(), "702")
                    self.assertEqual(window.top_window.session_reasoning_tokens.text(), "286")
                    self.assertGreaterEqual(window.top_window.session_input_tokens.width(), 52)
                    self.assertEqual(window.top_window.activity_elapsed.text(), "34m25s")
                    self.assertTrue(window.top_window.current_task.isVisible())
                    self.assertTrue(window.top_window.executing.isVisible())
                    self.assertIn("更新计划保留tk模式", window.top_window.current_task.text())
                    self.assertIn("python -m unittest", window.top_window.executing.text())
                    self.assertIn("15.3M", window.top_window.cache_progress.toolTip())
                    self.assertIn("超出 14%", window.top_window._budget_progress[1].toolTip())
                    self.assertEqual(window.top_window._activity_rows[0][1].width(), 24)
                    self.assertEqual(window.top_window._activity_rows[0][2].objectName(), "qtHudLabel-activity-title")
                    self.assertEqual(window.top_window._activity_rows[0][3].objectName(), "qtHudLabel-activity-detail")
                    header = window.top_window.findChild(QFrame, "qtHudPanelHeader")
                    self.assertIsNotNone(header)
                    assert header is not None
                    header_point = header.mapTo(window.top_window, header.rect().center())
                    content_point = window.top_window.current_task.mapTo(
                        window.top_window,
                        window.top_window.current_task.rect().center(),
                    )
                    self.assertTrue(window.top_window._should_toggle_from_click(header_point))
                    self.assertTrue(window.top_window._should_start_drag_from_click(header_point))
                    self.assertFalse(window.top_window._should_toggle_from_click(content_point))
                    self.assertFalse(window.top_window._should_start_drag_from_click(content_point))
                    expected_trail_height = (
                        qt_hud_module.QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT
                        * qt_hud_module.QT_HUD_ACTIVITY_TRAIL_VISIBLE_ROWS
                        + 6
                    )
                    self.assertEqual(window.top_window.trail_scroll.minimumHeight(), expected_trail_height)
                    self.assertGreaterEqual(window.top_window.trail_scroll.height(), expected_trail_height)
                    self.assertEqual(window.top_window.gap_chip.height(), 22)
                    self.assertEqual(window.top_window.slow_chip.height(), 22)
                    right_height_before_more = top_right.height()
                    trail_scroll_height_before_more = window.top_window.trail_scroll.height()
                    trail_container_height_before_more = window.top_window.trail_container.minimumHeight()
                    for time_label, marker, title_label, detail_label in window.top_window._activity_rows[:4]:
                        self.assertEqual(time_label.objectName(), "qtHudLabel-activity-time")
                        self.assertEqual(marker.size().toTuple(), (24, 38))
                        self.assertEqual(marker.parentWidget().height(), 38)
                        title_bottom = title_label.mapTo(window.top_window, title_label.rect().bottomLeft()).y()
                        detail_top = detail_label.mapTo(window.top_window, detail_label.rect().topLeft()).y()
                        self.assertLess(title_bottom, detail_top)
                    self.assertTrue(window.top_window.load_more.isEnabled())
                    window.top_window.load_more.click()
                    self.assertEqual(window.top_window.trail_scroll.height(), trail_scroll_height_before_more)
                    self.assertEqual(top_right.height(), right_height_before_more)
                    self.assertGreater(
                        window.top_window.trail_container.minimumHeight(),
                        trail_container_height_before_more,
                    )
                    self.assertEqual(window.top_window.load_more.text(), "已显示全部")
                    self.assertEqual(window.top_window.current_task._copy_text, "更新计划保留tk模式")
                    self.assertEqual(window.top_window.slow_chip._copy_text, "python -m unittest")
                    self.assertTrue(window.top_window.update_button.isVisible())
                    self.assertEqual(window.top_window.update_button.text(), "⇪")
                    settings_buttons = [
                        button
                        for button in window.top_window.findChildren(QPushButton)
                        if button.toolTip() == "设置"
                    ]
                    self.assertGreaterEqual(len(settings_buttons), 2)
                    self.assertTrue(all(button.text() != "Settings" for button in settings_buttons))
                    self.assertTrue(all(button.text() == "⚙" for button in settings_buttons))
                    self.assertFalse(any(label.text() == "更新计划保留tk模式" for label in collapsed.findChildren(QLabel)))
                    window.top_window.update_button.click()
                    self.assertEqual(update_manager.clicks, 1)
                    self.assertIn("已启动安装器", window.top_window.update_button.toolTip())
                    self.assertTrue(window.top_window.warning_panel.isVisible())
                    window.top_window.warning_close.click()
                    self.assertFalse(window.top_window.warning_panel.isVisible())
                    self.assertTrue(warning_dismissed_today(store.path))
                    window.open_settings()
                    dialog = window._settings_dialog
                    self.assertIsNotNone(dialog)
                    assert dialog is not None
                    self.assertEqual(dialog.tabs.count(), 3)
                    self.assertEqual(dialog.tabs.tabText(0), "设置")
                    self.assertEqual(dialog.tabs.tabText(1), "请作者喝咖啡")
                    self.assertEqual(dialog.tabs.tabText(2), "版本更新")
                    frame_names = {frame.objectName() for frame in dialog.findChildren(QFrame)}
                    self.assertIn("qtHudSettingsDialog", frame_names)
                    self.assertIn("qtHudSettingsHead", frame_names)
                    self.assertIn("qtHudSettingsActions", frame_names)
                    dialog.tabs.setCurrentIndex(1)
                    support_cards = dialog.findChildren(QFrame, "qtHudSupportQrCard")
                    self.assertGreaterEqual(len(support_cards), 1)
                    support_image = dialog.findChild(QLabel, "qtHudSupportQrImage")
                    self.assertIsNotNone(support_image)
                    assert support_image is not None
                    self.assertGreaterEqual(support_image.minimumWidth(), 260)
                    self.assertGreaterEqual(support_image.maximumHeight(), 360)
                    dialog.tabs.setCurrentIndex(0)
                    self.assertTrue(dialog.windowFlags() & qt_hud_module.Qt.WindowType.FramelessWindowHint)
                    self.assertEqual(dialog.save_button.property("primary"), "true")
                    action_texts = [button.text() for button in dialog.findChildren(QPushButton)]
                    self.assertNotIn("立即切换", action_texts)
                    self.assertGreater(dialog.price_table.rowCount(), 0)
                    max_price_height = (
                        dialog.price_table.horizontalHeader().height()
                        + (dialog.price_table.verticalHeader().defaultSectionSize() * min(dialog.price_table.rowCount(), 4))
                        + (dialog.price_table.frameWidth() * 2)
                        + 14
                    )
                    self.assertLessEqual(dialog.price_table.height(), max_price_height)
                    self.assertGreater(dialog.price_table.height(), dialog.price_table.horizontalHeader().height())
                    self.assertEqual(
                        dialog.price_table.verticalScrollBarPolicy(),
                        qt_hud_module.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
                    )
                    self.assertEqual(dialog.display_mode.currentData(), "qt")
                    self.assertEqual(dialog.display_mode.parentWidget().layout().itemAt(0).widget().text(), "HUD 显示方案")
                    self.assertEqual(dialog.daily_budget.parentWidget().layout().itemAt(0).widget().text(), "日额度 USD")
                    self.assertEqual(dialog.weekly_reset.parentWidget().parentWidget().layout().itemAt(0).widget().text(), "周额度重置")
                    self.assertEqual(dialog.pricing_url.parentWidget().parentWidget().layout().itemAt(0).widget().text(), "计费单价获取地址")
                    dialog.daily_budget.setText("12.5")
                    self.assertGreaterEqual(dialog.work_overlay_max_items.count(), 2)
                    self.assertEqual(dialog.work_overlay_max_items.itemData(0), 0)
                    self.assertIn("不启用", dialog.work_overlay_max_items.itemText(0))
                    overlay_index = dialog.work_overlay_max_items.findData(3)
                    self.assertGreaterEqual(overlay_index, 0)
                    dialog.work_overlay_max_items.setCurrentIndex(overlay_index)
                    dialog._save_only()
                    saved = store.load()
                    self.assertEqual(saved.daily_budget_usd, 12.5)
                    self.assertEqual(saved.work_overlay_max_items, 3)
                    tk_index = dialog.display_mode.findData("tk")
                    self.assertGreaterEqual(tk_index, 0)
                    with patch.object(
                        qt_hud_module.QMessageBox,
                        "question",
                        return_value=qt_hud_module.QMessageBox.StandardButton.No,
                    ) as question:
                        dialog.display_mode.setCurrentIndex(tk_index)
                    question.assert_called_once()
                    self.assertNotEqual(store.load().display_mode, "tk")
                    self.assertEqual(window.mode_switch_request, "")

                    with patch.object(
                        qt_hud_module.QMessageBox,
                        "question",
                        return_value=qt_hud_module.QMessageBox.StandardButton.Yes,
                    ) as question:
                        dialog._on_display_mode_selected(tk_index)
                    question.assert_called_once()
                    switched = store.load()
                    self.assertEqual(switched.display_mode, "renderer")
                    self.assertEqual(window.mode_switch_request, "renderer")
                    self.assertEqual(window.exit_reason, "display_mode_switch")
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_expanded_layout_stacks_only_when_extremely_narrow(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
            )
            try:
                panel.resize(qt_hud_module.QT_HUD_TOP_WIDTH, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                panel.show()
                app.processEvents()

                grid = panel._top_grid
                left = panel._top_left
                right = panel._top_right
                self.assertIsNotNone(grid)
                self.assertIsNotNone(left)
                self.assertIsNotNone(right)
                assert grid is not None
                assert left is not None
                assert right is not None
                _row_span, _column_span = 0, 0
                left_row, left_column, _row_span, _column_span = grid.getItemPosition(
                    grid.indexOf(left)
                )
                right_row, right_column, _row_span, _column_span = grid.getItemPosition(
                    grid.indexOf(right)
                )
                self.assertEqual((left_row, left_column), (0, 0))
                self.assertEqual((right_row, right_column), (0, 1))

                panel.resize(qt_hud_module.QT_HUD_TOP_STACK_WIDTH - 1, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                app.processEvents()

                left_row, left_column, _row_span, _column_span = grid.getItemPosition(
                    grid.indexOf(left)
                )
                right_row, right_column, _row_span, _column_span = grid.getItemPosition(
                    grid.indexOf(right)
                )
                self.assertEqual((left_row, left_column), (0, 0))
                self.assertEqual((right_row, right_column), (1, 0))
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_collapsed_progress_strip_matches_tk_layout_threshold(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            strip = qt_hud_module._TopCollapsedProgressStrip()
            try:
                strip.show()
                first_label = "本会话 3.8M/$3.70/97%"
                metrics = [
                    {"label": first_label, "ratio": 0.25, "tone": "session"},
                    {"label": "今日 18.9M/$8.96", "rightText": "总 $100.00", "ratio": 0.35, "tone": "day"},
                    {"label": "本周 39.5M/$33.41", "rightText": "总 $400.00", "ratio": 0.42, "tone": "week"},
                ]
                strip.resize(507, 28)
                strip.set_metrics(metrics)
                app.processEvents()

                self.assertFalse(strip.scrolling_enabled)
                first_width = strip.rails[0].preferred_width()
                expected_tail_width = (507 - first_width - 14) // 2
                self.assertEqual(strip.rails[0].width(), first_width)
                self.assertEqual(strip.rails[1].width(), strip.rails[2].width())
                self.assertEqual(strip.rails[1].width(), expected_tail_width)
                self.assertEqual(strip.rails[0].height(), qt_hud_module.QT_COLLAPSED_PROGRESS_RAIL_HEIGHT)
                self.assertEqual(strip.rails[0].y(), (strip.height() - strip.rails[0].height()) // 2)
                font = qt_hud_module.QFont(strip.rails[0].font())
                font.setPointSize(max(8, font.pointSize()))
                font.setBold(True)
                font_metrics = qt_hud_module.QFontMetrics(font)
                self.assertEqual(
                    font_metrics.elidedText(
                        first_label,
                        qt_hud_module.Qt.TextElideMode.ElideRight,
                        strip.rails[0].width() - 28,
                    ),
                    first_label,
                )

                narrow_width = strip.rails[0].preferred_width() + 14 + 80
                strip.resize(narrow_width, 28)
                strip.set_metrics(metrics)
                app.processEvents()

                self.assertTrue(strip.scrolling_enabled)
                self.assertLess(strip._scroll_min_x, 0.0)
                self.assertGreater(strip.rails[0].width(), 72)
            finally:
                strip.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_collapsed_progress_layout_does_not_depend_on_parent_visibility(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            strip = qt_hud_module._TopCollapsedProgressStrip()
            try:
                strip.resize(507, 28)
                metrics = [
                    {"label": "本会话 3.8M/$3.70/97%", "ratio": 0.25, "tone": "session"},
                    {"label": "今日 18.9M/$8.96", "rightText": "总 $100.00", "ratio": 0.35, "tone": "day"},
                    {"label": "本周 39.5M/$33.41", "rightText": "总 $400.00", "ratio": 0.42, "tone": "week"},
                ]

                strip.hide()
                strip.set_metrics(metrics)
                strip.show()
                strip._layout_for_current_metrics()
                app.processEvents()

                self.assertEqual(sum(1 for rail in strip.rails if rail.isVisible()), 3)
                self.assertFalse(strip.scrolling_enabled)
                self.assertGreater(strip.rails[1].width(), 0)
                self.assertGreater(strip.rails[2].width(), 0)
            finally:
                strip.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_warning_dismissal_survives_cached_payload_refresh(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
                store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "geometry_settings.json")
                hud_store.save(HudSettings.empty())
                window = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=store,
                    hud_settings_store=hud_store,
                )
                try:
                    snapshot = ParsedSession(
                        session_id="qt-dismiss-warning",
                        status="parsed",
                        today_cost_usd=52.0,
                        daily_limit_usd=100.0,
                        budget_warnings=[
                            "日额度已用 52.00/100 USD (52%)，超过 50% 阈值"
                        ],
                    )
                    window.update_display(snapshot)
                    window.attach_to_rect(WindowRect(left=100, top=120, right=1300, bottom=920))
                    window.top_window.set_expanded(True)

                    self.assertTrue(window.top_window.warning_panel.isVisible())
                    window.top_window.warning_close.click()
                    self.assertFalse(window.top_window.warning_panel.isVisible())
                    self.assertTrue(warning_dismissed_today(store.path))

                    window._refresh_latest_payload()

                    self.assertFalse(window.top_window.warning_panel.isVisible())
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_hud_reuses_and_saves_shared_geometry_settings(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(
                    HudSettings(
                        top=WindowPlacement(
                            pinned=True,
                            absolute_x=123,
                            absolute_y=145,
                            width=640,
                            height=455,
                        ),
                        request=WindowPlacement(
                            pinned=True,
                            absolute_x=321,
                            absolute_y=654,
                            width=430,
                            height=205,
                        ),
                    )
                )
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    self.assertEqual(window.top_window.width(), 640)
                    self.assertEqual(window.top_window._expanded_height, 455)
                    self.assertEqual((window.top_window.x(), window.top_window.y()), (123, 145))
                    self.assertTrue(window.top_window._manual_positioned)
                    self.assertEqual(window.request_window.width(), 430)
                    self.assertEqual(window.request_window._expanded_height, 205)
                    self.assertEqual(
                        (window.request_window.x(), window.request_window.y()),
                        (321, 654),
                    )
                    self.assertTrue(window.request_window._manual_positioned)

                    window.top_window.move(222, 244)
                    window._remember_panel_geometry("top", window.top_window, "move")
                    moved = hud_store.load()
                    self.assertEqual(moved.top.absolute_x, 222)
                    self.assertEqual(moved.top.absolute_y, 244)

                    window.request_window._expanded = True
                    window.request_window.setMinimumHeight(1)
                    window.request_window.setMaximumHeight(16777215)
                    window.request_window.resize(460, 230)
                    window._remember_panel_geometry("request", window.request_window, "resize")
                    resized = hud_store.load()
                    self.assertEqual(resized.request.width, 460)
                    self.assertEqual(resized.request.height, 230)
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_pin_button_is_compact_borderless_and_centered(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
            )
            try:
                pin = panel._pin_buttons[0]
                panel.resize(520, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                panel.show()
                app.processEvents()

                self.assertEqual(pin.objectName(), "qtHudPinButton")
                self.assertEqual((pin.width(), pin.height()), (20, 24))
                self.assertEqual(pin.property("pinned"), "false")
                self.assertAlmostEqual(
                    pin.mapTo(panel.shell, pin.rect().center()).y(),
                    panel.shell.contentsRect().center().y(),
                    delta=4,
                )

                panel.set_pinned(True)
                self.assertEqual(pin.property("pinned"), "true")
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_tk_pin_button_is_compact_borderless(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            button = window._pin_buttons["top"][0]

            self.assertEqual(int(button.cget("borderwidth")), 0)
            self.assertEqual(int(button.cget("highlightthickness")), 0)
            self.assertLessEqual(int(button.cget("padx")), 2)
            self.assertEqual(int(button.cget("pady")), 0)
            self.assertEqual(int(button.cget("width")), 2)
        finally:
            window._close()

    def test_qt_pin_button_toggles_persistence_without_expanding_panel(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1300, bottom=920)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window._impl.locator = _FakeAnchorLocator(
                        {},
                        header_roi=WindowRect(left=360, top=144, right=1120, bottom=190),
                    )
                    window.attach_to_rect(rect)
                    window.top_window.move(410, 180)
                    window.top_window.resize(620, window.top_window.height())

                    self.assertFalse(window.top_window.expanded)
                    window.top_window._pin_buttons[0].click()

                    saved = hud_store.load().top
                    self.assertTrue(saved.pinned)
                    self.assertEqual((saved.absolute_x, saved.absolute_y), (410, 180))
                    self.assertEqual(saved.width, 620)
                    self.assertFalse(window.top_window.expanded)

                    window.top_window.move(520, 240)
                    window.top_window._pin_buttons[0].click()

                    cleared = hud_store.load().top
                    self.assertFalse(cleared.pinned)
                    self.assertIsNone(cleared.absolute_x)
                    self.assertIsNone(cleared.width)
                    self.assertEqual((window.top_window.x(), window.top_window.y()), (360, 144))
                    self.assertEqual(window.top_window.width(), 760)
                    self.assertFalse(window.top_window.expanded)
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_panel_detects_side_border_resize_edges(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
            )
            try:
                panel.resize(520, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                panel.show()
                app.processEvents()

                self.assertEqual(panel._resize_edge_at(qt_hud_module.QPoint(3, 18)), "left")
                self.assertEqual(panel._resize_edge_at(qt_hud_module.QPoint(516, 18)), "right")
                self.assertEqual(panel._resize_edge_at(qt_hud_module.QPoint(260, 18)), "")
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_panel_border_hover_updates_cursor_from_shell_and_child(self) -> None:
        try:
            import PySide6  # noqa: F401
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtWidgets import QLabel
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            changes: list[tuple[str, str, int]] = []
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
                on_geometry_changed=lambda target, widget, reason: changes.append(
                    (target, reason, widget.width())
                ),
            )
            try:
                panel.resize(520, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                child = QLabel("child", panel.shell)
                child.setGeometry(512, 8, 8, 20)
                child.show()
                panel.show()
                app.processEvents()

                shell_point = panel.shell.mapFrom(panel, qt_hud_module.QPoint(516, 18))
                shell_event = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseMove,
                    QPointF(shell_point),
                    QPointF(panel.shell.mapToGlobal(shell_point)),
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(panel.shell, shell_event)
                self.assertEqual(panel.cursor().shape(), qt_hud_module.Qt.CursorShape.SizeHorCursor)
                self.assertEqual(panel.shell.cursor().shape(), qt_hud_module.Qt.CursorShape.SizeHorCursor)

                center_point = panel.shell.mapFrom(panel, qt_hud_module.QPoint(260, 18))
                center_event = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseMove,
                    QPointF(center_point),
                    QPointF(panel.shell.mapToGlobal(center_point)),
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(panel.shell, center_event)
                self.assertEqual(panel.cursor().shape(), qt_hud_module.Qt.CursorShape.ArrowCursor)
                self.assertEqual(panel.shell.cursor().shape(), qt_hud_module.Qt.CursorShape.ArrowCursor)

                child_point = child.mapFrom(panel, qt_hud_module.QPoint(516, 18))
                child_event = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseMove,
                    QPointF(child_point),
                    QPointF(child.mapToGlobal(child_point)),
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(child, child_event)
                self.assertEqual(panel.cursor().shape(), qt_hud_module.Qt.CursorShape.SizeHorCursor)
                self.assertEqual(child.cursor().shape(), qt_hud_module.Qt.CursorShape.SizeHorCursor)

                child_press = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseButtonPress,
                    QPointF(child_point),
                    QPointF(child.mapToGlobal(child_point)),
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(child, child_press)
                child_move_point = child.mapFrom(panel, qt_hud_module.QPoint(556, 18))
                child_move = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseMove,
                    QPointF(child_move_point),
                    QPointF(child.mapToGlobal(child_move_point)),
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(child, child_move)
                child_release = QMouseEvent(
                    qt_hud_module.QEvent.Type.MouseButtonRelease,
                    QPointF(child_move_point),
                    QPointF(child.mapToGlobal(child_move_point)),
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.MouseButton.NoButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                )
                qt_hud_module.QApplication.sendEvent(child, child_release)

                self.assertEqual(panel.width(), 560)
                self.assertIn(("top", "resize", 560), changes)
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_collapsed_progress_strip_is_vertically_centered(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
            )
            try:
                panel.resize(520, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                panel.show()
                panel.update_payload(
                    {
                        "topProgress": {
                            "collapsed": [
                                {"label": "本会话 3.8M/$3.70/97%", "ratio": 0.25, "tone": "session"},
                                {"label": "今日 62.7M/$85.00", "ratio": 0.85, "tone": "day"},
                                {"label": "本周 62.7M/$425.00", "ratio": 0.42, "tone": "week"},
                            ]
                        }
                    }
                )
                app.processEvents()

                strip = panel._collapsed_strip
                self.assertIsNotNone(strip)
                assert strip is not None
                strip_y = strip.mapTo(panel, qt_hud_module.QPoint(0, 0)).y()
                self.assertEqual(strip_y, (qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT - strip.height()) // 2)
                self.assertEqual(strip.rails[0].height(), qt_hud_module.QT_COLLAPSED_PROGRESS_RAIL_HEIGHT)
                self.assertEqual(strip.rails[0].y(), (strip.height() - strip.rails[0].height()) // 2)
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_panel_right_border_drag_resizes_and_reports_geometry(self) -> None:
        try:
            import PySide6  # noqa: F401
            from PySide6.QtTest import QTest
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            app = qt_hud_module.QApplication.instance() or qt_hud_module.QApplication(sys.argv[:1])
            changes: list[tuple[str, str, int]] = []
            panel = qt_hud_module._TopPanel(
                on_settings=lambda: None,
                on_update_action=lambda: None,
                on_dismiss_warnings=lambda: None,
                on_interaction=lambda: None,
                on_geometry_changed=lambda target, widget, reason: changes.append(
                    (target, reason, widget.width())
                ),
            )
            try:
                panel.resize(520, qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT)
                panel.show()
                app.processEvents()

                QTest.mousePress(
                    panel,
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                    qt_hud_module.QPoint(516, 18),
                )
                QTest.mouseMove(panel, qt_hud_module.QPoint(556, 18))
                QTest.mouseRelease(
                    panel,
                    qt_hud_module.Qt.MouseButton.LeftButton,
                    qt_hud_module.Qt.KeyboardModifier.NoModifier,
                    qt_hud_module.QPoint(556, 18),
                )
                app.processEvents()

                self.assertEqual(panel.width(), 560)
                self.assertIn(("top", "resize", 560), changes)
            finally:
                panel.close()
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_attached_resize_is_session_only_until_pinned(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920)
                _anchor_x, _anchor_y, anchor_width, _anchor_height = _visual_anchor_geometry("top", rect, False)
                window = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    window.attach_to_rect(rect)
                    target_width = max(120, anchor_width // 2)
                    window.top_window.resize(target_width, window.top_window.height())
                    window._remember_panel_geometry("top", window.top_window, "resize")
                    self.assertIn("top", window._impl._session_manual_targets)
                    self.assertEqual(window.settings.top.width, target_width)
                    saved = hud_store.load()
                    self.assertFalse(saved.top.pinned)
                    self.assertIsNone(saved.top.width)
                    self.assertIsNone(saved.top.width_ratio)
                    self.assertIsNone(saved.top.anchor_source)

                    window.attach_to_rect(WindowRect(left=180, top=180, right=1180, bottom=980))
                    self.assertEqual(window.top_window.width(), target_width)
                finally:
                    window.close("test")

                wider = WindowRect(left=100, top=120, right=1500, bottom=920)
                restored = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    expected_width = restored._attached_panel_geometry("top", wider, False)[2]
                    restored.attach_to_rect(wider)
                    self.assertEqual(restored.top_window.width(), expected_width)
                    self.assertNotEqual(restored.top_window.width(), target_width)
                finally:
                    restored.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_manual_geometry_resets_when_session_changes(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1300, bottom=920)
                roi = WindowRect(left=360, top=144, right=1120, bottom=196)
                window = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    window._impl.locator = _FakeAnchorLocator(
                        {},
                        header_roi=roi,
                        window_rect=rect,
                    )
                    window.attach_to_rect(rect)
                    window.update_display(ParsedSession(session_id="session-a"))

                    window.top_window.move(410, 180)
                    window.top_window.resize(620, window.top_window.height())
                    window._remember_panel_geometry("top", window.top_window, "move")
                    self.assertIn("top", window._impl._session_manual_targets)

                    window.update_display(ParsedSession(session_id="session-a"))
                    self.assertIn("top", window._impl._session_manual_targets)
                    self.assertEqual((window.top_window.x(), window.top_window.y()), (410, 180))

                    window.update_display(ParsedSession(session_id="session-b"))
                    self.assertNotIn("top", window._impl._session_manual_targets)
                    self.assertFalse(window.top_window._manual_positioned)
                    self.assertIsNone(window.settings.top.width)
                    self.assertEqual(
                        (window.top_window.x(), window.top_window.y(), window.top_window.width()),
                        (360, 144, 760),
                    )
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_attached_windows_follow_moved_codex_rect(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920)
                moved = WindowRect(left=180, top=170, right=1180, bottom=970)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", side_effect=[rect, moved]):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        self.assertTrue(window._follow_timer.isActive())

                        window._follow_codex_window()

                        expected_top = window._attached_panel_geometry("top", moved, False)
                        expected_request = window._attached_panel_geometry("request", moved, False)
                        self.assertEqual(
                            (window.top_window.x(), window.top_window.y(), window.top_window.width()),
                            (expected_top[0], expected_top[1], expected_top[2]),
                        )
                        self.assertEqual(
                            (window.request_window.x(), window.request_window.y(), window.request_window.width()),
                            (expected_request[0], expected_request[1], expected_request[2]),
                        )
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_follow_skips_while_top_panel_is_being_dragged(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=rect) as find:
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        window.top_window.move(444, 255)
                        window.top_window._drag_origin = qt_hud_module.QPoint(0, 0)
                        window.top_window._drag_window_origin = window.top_window.pos()
                        window.top_window._dragging = True

                        self.assertTrue(window._follow_codex_window())

                        self.assertEqual((window.top_window.x(), window.top_window.y()), (444, 255))
                        self.assertEqual(find.call_count, 1)
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_attached_manual_position_is_current_session_only(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920)
                moved = WindowRect(left=160, top=150, right=1160, bottom=950)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.attach_to_rect(rect)
                    window.top_window.move(rect.left + 240, rect.top + 90)
                    window._remember_panel_geometry("top", window.top_window, "move")
                    saved_top = hud_store.load().top
                    self.assertFalse(saved_top.pinned)
                    self.assertIsNone(saved_top.absolute_x)
                    self.assertIsNone(saved_top.relative_x_ratio)
                    self.assertIsNone(saved_top.anchor_x_ratio)

                    window.attach_to_rect(moved)

                    self.assertEqual(
                        (window.top_window.x(), window.top_window.y()),
                        (rect.left + 240, rect.top + 90),
                    )
                finally:
                    window.close("test")

                restored = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    expected = restored._attached_panel_geometry("top", moved, False)
                    restored.attach_to_rect(moved)
                    self.assertEqual(
                        (restored.top_window.x(), restored.top_window.y()),
                        (expected[0], expected[1]),
                    )
                finally:
                    restored.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_initial_place_ignores_legacy_unpinned_geometry(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.path.write_text(
                    json.dumps(
                        {
                            "top": {
                                "pinned": False,
                                "absolute_x": 12,
                                "absolute_y": 34,
                                "relative_x_ratio": 0.25,
                                "relative_y_ratio": 0.20,
                                "width_ratio": 0.5,
                            },
                            "request": {
                                "pinned": False,
                                "absolute_x": 56,
                                "absolute_y": 78,
                                "relative_x_ratio": 0.70,
                                "relative_bottom_ratio": 0.10,
                                "anchor_x_ratio": 0.2,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                rect = WindowRect(left=200, top=160, right=1200, bottom=960)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=rect):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    expected_top = window._attached_panel_geometry("top", rect, False)
                    expected_request = window._attached_panel_geometry("request", rect, False)
                    self.assertEqual(
                        (window.top_window.x(), window.top_window.y()),
                        (expected_top[0], expected_top[1]),
                    )
                    self.assertEqual(
                        (window.request_window.x(), window.request_window.y()),
                        (expected_request[0], expected_request[1]),
                    )
                    self.assertNotEqual((window.top_window.x(), window.top_window.y()), (12, 34))
                    self.assertNotEqual((window.request_window.x(), window.request_window.y()), (56, 78))
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_initial_place_ignores_legacy_unpinned_geometry_while_codex_inactive(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.path.write_text(
                    json.dumps(
                        {
                            "top": {
                                "pinned": False,
                                "absolute_x": 12,
                                "absolute_y": 34,
                                "relative_x_ratio": 0.25,
                                "relative_y_ratio": 0.20,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                rect = WindowRect(left=200, top=160, right=1200, bottom=960)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=rect), patch.object(
                    qt_hud_module.CodexWindowLocator,
                    "is_active",
                    return_value=False,
                ):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    expected_top = window._attached_panel_geometry("top", rect, False)
                    self.assertEqual(
                        (window.top_window.x(), window.top_window.y()),
                        (expected_top[0], expected_top[1]),
                    )
                    self.assertNotEqual((window.top_window.x(), window.top_window.y()), (12, 34))
                    self.assertFalse(window.top_window._manual_positioned)
                    self.assertFalse(window.top_window.isVisible())
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_manual_top_position_stays_put_when_anchor_shifts_without_window_move(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920)
                first_anchor = HudAnchor(
                    left=260,
                    top=150,
                    right=820,
                    bottom=190,
                    default_x=260,
                    default_y=150,
                    default_width=560,
                    source="test-title",
                )
                second_anchor = HudAnchor(
                    left=260,
                    top=184,
                    right=820,
                    bottom=224,
                    default_x=260,
                    default_y=184,
                    default_width=560,
                    source="test-title",
                )
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.locator = _FakeAnchorLocator({"top": first_anchor})
                    window.attach_to_rect(rect)
                    window.top_window.move(340, 210)
                    window._remember_panel_geometry("top", window.top_window, "move")
                    window.top_window._manual_positioned = True

                    window.locator = _FakeAnchorLocator({"top": second_anchor})
                    window.attach_to_rect(rect)

                    self.assertEqual((window.top_window.x(), window.top_window.y()), (340, 210))
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_pinned_top_position_stays_put_when_anchor_shifts_without_window_move(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                rect = WindowRect(left=100, top=0, right=1100, bottom=800, hwnd=321)
                first_anchor = HudAnchor(
                    left=260,
                    top=30,
                    right=820,
                    bottom=70,
                    default_x=260,
                    default_y=30,
                    default_width=560,
                    source="test-title",
                )
                second_anchor = HudAnchor(
                    left=260,
                    top=60,
                    right=820,
                    bottom=100,
                    default_x=260,
                    default_y=60,
                    default_width=560,
                    source="test-title",
                )
                settings = HudSettings.empty()
                settings.top.pinned = True
                settings.top.absolute_x = 340
                settings.top.absolute_y = 36
                settings.top.width = 560
                settings.top.height = qt_hud_module.QT_HUD_TOP_EXPANDED_HEIGHT
                hud_store.save(settings)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.locator = _FakeAnchorLocator({"top": first_anchor})
                    window.attach_to_rect(rect)

                    expected_x = int(settings.top.absolute_x or 0)
                    expected_y = int(settings.top.absolute_y or 0)
                    self.assertEqual((window.top_window.x(), window.top_window.y()), (expected_x, expected_y))
                    self.assertTrue(window.top_window._manual_positioned)

                    window.locator = _FakeAnchorLocator({"top": second_anchor})
                    window.attach_to_rect(rect)

                    self.assertEqual((window.top_window.x(), window.top_window.y()), (expected_x, expected_y))
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_manual_top_position_survives_inactive_rect_jitter(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                visible = WindowRect(left=100, top=120, right=1100, bottom=920, hwnd=321)
                inactive_jitter = WindowRect(left=100, top=154, right=1100, bottom=954, hwnd=321)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=visible):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.locator.find = MagicMock(side_effect=[inactive_jitter, visible])
                    window.locator.is_active = MagicMock(side_effect=[False, True])
                    window.top_window.move(340, 210)
                    window._remember_panel_geometry("top", window.top_window, "move")
                    window.top_window._manual_positioned = True

                    self.assertFalse(window._follow_codex_window())
                    self.assertTrue(window._follow_codex_window())

                    self.assertEqual((window.top_window.x(), window.top_window.y()), (340, 210))
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_manual_top_position_survives_same_hwnd_vertical_rect_jitter(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                rect = WindowRect(left=100, top=120, right=1100, bottom=920, hwnd=321)
                jitter = WindowRect(left=100, top=154, right=1100, bottom=954, hwnd=321)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.attach_to_rect(rect)
                    window.top_window.move(340, 210)
                    window._remember_panel_geometry("top", window.top_window, "move")
                    window.top_window._manual_positioned = True

                    window.attach_to_rect(jitter)

                    self.assertEqual((window.top_window.x(), window.top_window.y()), (340, 210))
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_top_expanded_uses_auto_geometry_not_saved_anchor(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                rect = WindowRect(left=100, top=100, right=1100, bottom=900)
                anchor = HudAnchor(
                    left=260,
                    top=150,
                    right=820,
                    bottom=150 + qt_hud_module.QT_HUD_TOP_COLLAPSED_HEIGHT,
                    default_x=260,
                    default_y=150,
                    default_width=560,
                    source="test-title",
                )
                settings = HudSettings.empty()
                settings.top.anchor_x_ratio = 0.0
                settings.top.anchor_y_ratio = 1.0
                settings.top.anchor_source = "test-title"
                settings.top.relative_x_ratio = (anchor.left - rect.left) / max(1, rect.width)
                settings.top.relative_y_ratio = (anchor.top - rect.top) / max(1, rect.height)
                hud_store.save(settings)
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=False,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                try:
                    window.locator = SimpleNamespace(
                        header_roi_geometry=lambda _rect: None,
                        bottom_roi_geometry=lambda _rect: None,
                        anchor_geometry=lambda target, _rect, _height: anchor if target == "top" else None,
                        find=lambda: rect,
                        is_active=lambda _rect, _hwnds: True,
                    )

                    _x, y, _width, _height = window._attached_panel_geometry("top", rect, True)

                    self.assertEqual(y, _visual_anchor_geometry("top", rect, True)[1])
                    self.assertNotEqual(y, anchor.bottom)
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_hides_and_restores_with_codex_visibility(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                visible = WindowRect(left=100, top=120, right=1100, bottom=920)
                minimized = WindowRect(left=100, top=120, right=1100, bottom=920, minimized=True)
                with patch.object(
                    qt_hud_module.CodexWindowLocator,
                    "find",
                    side_effect=[visible, None, minimized, visible],
                ):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())

                        window._follow_codex_window()
                        self.assertFalse(window.top_window.isVisible())
                        self.assertFalse(window.request_window.isVisible())
                        self.assertFalse(window._attached)

                        window._follow_codex_window()
                        self.assertFalse(window.top_window.isVisible())
                        self.assertFalse(window.request_window.isVisible())

                        window._follow_codex_window()
                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())
                        self.assertTrue(window._attached)
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_hides_and_restores_when_codex_is_inactive(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                visible = WindowRect(left=100, top=120, right=1100, bottom=920, hwnd=321)
                with (
                    patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=visible),
                    patch.object(
                        qt_hud_module.CodexWindowLocator,
                        "is_active",
                        side_effect=[True, False, True],
                    ) as is_active,
                ):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())

                        self.assertFalse(window._follow_codex_window())
                        self.assertFalse(window.top_window.isVisible())
                        self.assertFalse(window.request_window.isVisible())
                        self.assertTrue(window._attached)

                        self.assertTrue(window._follow_codex_window())
                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())
                        self.assertGreaterEqual(is_active.call_count, 3)
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_free_positioning_survives_missing_codex(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=False,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        window.top_window.move(222, 244)
                        window.request_window.move(333, 444)

                        window._follow_codex_window()
                        window._refresh_latest_payload()

                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())
                        self.assertEqual((window.top_window.x(), window.top_window.y()), (222, 244))
                        self.assertEqual((window.request_window.x(), window.request_window.y()), (333, 444))
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_free_positioning_survives_missing_codex_after_attach(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=False,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                    try:
                        window.attach_to_rect(WindowRect(left=100, top=120, right=1100, bottom=920))
                        window.top_window.move(222, 244)
                        window.request_window.move(333, 444)

                        window._follow_codex_window()

                        self.assertFalse(window._attached)
                        self.assertTrue(window.top_window.isVisible())
                        self.assertTrue(window.request_window.isVisible())
                        self.assertEqual((window.top_window.x(), window.top_window.y()), (222, 244))
                        self.assertEqual((window.request_window.x(), window.request_window.y()), (333, 444))
                    finally:
                        window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_attached_geometry_uses_roi_and_ignores_locator_anchor(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
                    window = QtHudWindow(
                        hide_until_attached=True,
                        user_settings_store=user_store,
                        hud_settings_store=hud_store,
                    )
                window._impl.locator = _FakeAnchorLocator(
                    {
                        "top": HudAnchor(
                            left=220,
                            top=130,
                            right=920,
                            bottom=178,
                            default_x=220,
                            default_y=136,
                            default_width=700,
                            source="cdp:title",
                        )
                    },
                    header_roi=WindowRect(left=360, top=144, right=1120, bottom=190),
                )
                try:
                    window.attach_to_rect(WindowRect(left=100, top=120, right=1300, bottom=920))

                    self.assertEqual((window.top_window.x(), window.top_window.y()), (360, 144))
                    self.assertEqual(window.top_window.width(), 760)

                    window.top_window.resize(350, window.top_window.height())
                    window._remember_panel_geometry("top", window.top_window, "resize")
                    saved = hud_store.load()
                    self.assertFalse(saved.top.pinned)
                    self.assertIsNone(saved.top.width)
                    self.assertIsNone(saved.top.anchor_source)
                    self.assertIsNone(saved.top.width_ratio)
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_stylesheet_uses_readable_light_theme_surfaces(self) -> None:
        tokens = HudThemeTokens.from_theme(
            CodexThemeExport.from_share_string(
                'codex-theme-v1:{"codeThemeId":"github-light","theme":{"accent":"#0969da",'
                '"contrast":40,"fonts":{"code":null,"ui":null},"ink":"#24292f",'
                '"opaqueWindows":false,"semanticColors":{"diffAdded":"#1a7f37",'
                '"diffRemoved":"#cf222e","skill":"#8250df"},"surface":"#ffffff"}}'
            )
        ).to_dict()
        stylesheet = _qt_stylesheet(tokens)

        self.assertIn("rgba(255, 255, 255, 236)", stylesheet)
        self.assertNotIn("rgba(16, 22, 29, 236)", stylesheet)
        self.assertNotIn("rgba(255, 255, 255, 18)", stylesheet)
        self.assertNotIn("#1C2632", stylesheet)
        self.assertNotIn("#111820", stylesheet)
        self.assertIn("QFrame#qtHudShell[target=\"request\"]", stylesheet)
        self.assertIn("QFrame#qtHudRequestCollapsed", stylesheet)
        self.assertIn("QComboBox QAbstractItemView", stylesheet)
        self.assertIn("QComboBox QListView", stylesheet)
        self.assertIn("selection-background-color:", stylesheet)
        self.assertNotIn("background: #151D27;", stylesheet)
        self.assertIn(f"background: {tokens['requestSurface']};", stylesheet)
        self.assertIn(f"color: {tokens['requestText']};", stylesheet)
        self.assertIn("color: #24292f;", stylesheet)
        self.assertNotIn("__QT_HUD_SETTINGS_POPUP_BACKGROUND__", stylesheet)
        self.assertGreaterEqual(_qt_contrast(tokens["surface"], tokens["text"]), 4.5)
        self.assertGreaterEqual(_qt_contrast(tokens["requestSurface"], tokens["requestText"]), 4.5)

    def test_qt_hud_applies_renderer_theme_tokens(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                window = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    window._apply_payload(
                        {
                            "topLine": "theme test",
                            "requestLine": "request theme test",
                            "topProgress": {
                                "collapsed": [
                                    {"label": "今日", "ratio": 0.5, "tone": "day"}
                                ],
                                "budget": [
                                    {"label": "本周", "ratio": 0.25, "tone": "week"}
                                ],
                            },
                            "theme": {
                                "variant": "dark",
                                "tokens": {
                                    "surface": "#010203",
                                    "panelSurface": "#111213",
                                    "panelBorder": "#212223",
                                    "headerSurface": "#313233",
                                    "text": "#414243",
                                    "muted": "#515253",
                                    "accent": "#616263",
                                    "info": "#717273",
                                    "progressTrack": "#818283",
                                    "progressTrackBorder": "#919293",
                                    "progressTrackText": "#A1A2A3",
                                    "progressDay": "#B1B2B3",
                                    "progressWeek": "#C1C2C3",
                                    "progressCache": "#D1D2D3",
                                },
                            },
                        }
                    )

                    self.assertIn("#010203", window.top_window.styleSheet())
                    self.assertIn("#414243", window.request_window.styleSheet())
                    self.assertEqual(
                        window.top_window._collapsed_progress[0]._theme["progressDay"],
                        "#B1B2B3",
                    )
                    self.assertEqual(
                        window.top_window._budget_progress[0]._theme["progressWeek"],
                        "#C1C2C3",
                    )
                    window.open_settings()
                    dialog = window._settings_dialog
                    self.assertIsNotNone(dialog)
                    assert dialog is not None
                    self.assertIn("#010203", dialog.styleSheet())
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform

    def test_qt_update_display_injects_codex_theme_payload(self) -> None:
        try:
            import PySide6  # noqa: F401
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        previous_platform = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                user_store = UserConfigStore(Path(temp_dir) / "user_settings.json")
                user_store.save(UserConfig.defaults())
                hud_store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                hud_store.save(HudSettings.empty())
                snapshot = CodexThemeSnapshot.from_probe_result(
                    {
                        "mode": "dark",
                        "effectiveVariant": "dark",
                        "darkCodeThemeId": "linear",
                        "darkTheme": {
                            "accent": "#112233",
                            "contrast": 60,
                            "fonts": {"code": None, "ui": "Inter"},
                            "ink": "#f1f2f3",
                            "opaqueWindows": True,
                            "semanticColors": {
                                "diffAdded": "#334455",
                                "diffRemoved": "#556677",
                                "skill": "#778899",
                            },
                            "surface": "#020304",
                        },
                    },
                    source="persisted",
                )
                self.assertIsNotNone(snapshot)
                assert snapshot is not None
                expected_tokens = snapshot.hud_tokens.to_dict()
                window = QtHudWindow(
                    hide_until_attached=True,
                    user_settings_store=user_store,
                    hud_settings_store=hud_store,
                )
                try:
                    window._impl._theme_probe = SimpleNamespace(snapshot=lambda: snapshot)
                    window.update_display(
                        ParsedSession(
                            session_id="qt-theme-probe",
                            session_title="Qt theme probe",
                            status="parsed",
                            line_count=4,
                        )
                    )

                    self.assertEqual(window._theme_tokens["surface"], expected_tokens["surface"])
                    self.assertEqual(window._theme_tokens["accent"], expected_tokens["accent"])
                    self.assertIn(str(expected_tokens["surface"]), window.top_window.styleSheet())
                    self.assertEqual(
                        window.top_window._collapsed_progress[0]._theme["progressDay"],
                        expected_tokens["progressDay"],
                    )
                    self.assertEqual(
                        window._latest_payload.to_json()["theme"]["source"],
                        "persisted",
                    )
                finally:
                    window.close("test")
        finally:
            if previous_platform is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = previous_platform


@pytest.mark.ui
@pytest.mark.tk_ui
class TokenHudWindowLifecycleTests(unittest.TestCase):
    def test_top_toggle_reuses_prebuilt_frames_and_keeps_request_window(self) -> None:
        window = TokenHudWindow()
        try:
            self.assertEqual(window.request_root.winfo_exists(), 1)
            top_shell = window._top_shell
            collapsed_frame = window._top_collapsed_frame
            expanded_frame = window._top_expanded_frame
            self.assertIsNotNone(top_shell)
            self.assertIsNotNone(collapsed_frame)
            self.assertIsNotNone(expanded_frame)
            assert top_shell is not None
            assert collapsed_frame is not None
            assert expanded_frame is not None

            window.toggle_top_expanded()
            self.assertEqual(window.request_root.winfo_exists(), 1)
            self.assertEqual(top_shell.winfo_exists(), 1)
            self.assertEqual(collapsed_frame.winfo_exists(), 1)
            self.assertEqual(expanded_frame.winfo_exists(), 1)
            self.assertEqual(collapsed_frame.winfo_manager(), "place")
            self.assertEqual(expanded_frame.winfo_manager(), "place")

            window.toggle_top_expanded()
            self.assertEqual(window.request_root.winfo_exists(), 1)
            self.assertEqual(top_shell.winfo_exists(), 1)
            self.assertEqual(collapsed_frame.winfo_exists(), 1)
            self.assertEqual(expanded_frame.winfo_exists(), 1)
            self.assertEqual(collapsed_frame.winfo_manager(), "place")
            self.assertEqual(expanded_frame.winfo_manager(), "place")
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
            with patch.object(window.locator, "find", return_value=None):
                window._open_settings_dialog()
            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            _flush_tk(window)
            widgets = _walk_widgets(dialog)
            button_texts = {
                str(widget.cget("text"))
                for widget in widgets
                if isinstance(widget, tk.Button)
            }

            self.assertFalse(any(isinstance(widget, ttk.Notebook) for widget in widgets))
            self.assertFalse(any(isinstance(widget, tk.Scrollbar) for widget in widgets))
            self.assertTrue(any(isinstance(widget, HudScrollbar) for widget in widgets))
            self.assertTrue(dialog.overrideredirect())
            width, height, x, y = _parse_tk_geometry(dialog.geometry())
            self.assertEqual(width, SETTINGS_DIALOG_WIDTH)
            self.assertEqual(height, SETTINGS_DIALOG_HEIGHT)
            self.assertAlmostEqual(x, (dialog.winfo_screenwidth() - width) // 2, delta=2)
            self.assertAlmostEqual(y, (dialog.winfo_screenheight() - height) // 2, delta=2)
            self.assertIn("设置", button_texts)
            self.assertIn("请作者喝咖啡", button_texts)
            self.assertIn("拉取", button_texts)
            self.assertIn("导出 JSON", button_texts)
            self.assertIn("保存", button_texts)
            self.assertIn("退出 HUD", button_texts)
            self.assertIn("display_mode", window._settings_entries)
            display_mode = window._settings_entries["display_mode"]
            self.assertIsInstance(display_mode, ttk.Combobox)
            self.assertIn("qt - Qt 独立窗口", display_mode.cget("values"))
            self.assertIn("work_overlay_max_items", window._settings_entries)
            self.assertNotIn("support_url", window._settings_entries)
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
            with patch.object(canvas, "yview_scroll") as scroll:
                result = window._scroll_settings_combobox(SimpleNamespace(delta=-120, num=None))
            self.assertEqual(result, "break")
            scroll.assert_called_once_with(1, "units")
        finally:
            window._close()

    def test_settings_dropdown_wheel_guards_are_registered(self) -> None:
        qt_source = Path(qt_hud_module.__file__).read_text(encoding="utf-8")
        renderer_source = Path(payload_from_snapshot.__code__.co_filename).read_text(encoding="utf-8")

        self.assertIn("class _SettingsComboBox(QComboBox):", qt_source)
        self.assertEqual(qt_source.count("_SettingsComboBox()"), 3)
        self.assertIn('root.addEventListener("wheel"', renderer_source)
        self.assertIn("select[data-setting-key]", renderer_source)
        self.assertIn("passive: false", renderer_source)

    def test_settings_dialog_centers_on_codex_window_when_available(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=160, top=90, right=1360, bottom=890)
            with patch.object(window.locator, "find", return_value=rect):
                window._open_settings_dialog()
            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None

            width, height, x, y = _parse_tk_geometry(dialog.geometry())

            self.assertEqual(width, SETTINGS_DIALOG_WIDTH)
            self.assertEqual(height, SETTINGS_DIALOG_HEIGHT)
            self.assertEqual(x, rect.left + (rect.width - width) // 2)
            self.assertEqual(y, rect.top + (rect.height - height) // 2)
        finally:
            window._close()

    def test_top_expanded_uses_hud_scrollbar(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _flush_tk(window)

            widgets = _walk_widgets(window.root)

            self.assertFalse(any(isinstance(widget, tk.Scrollbar) for widget in widgets))
            self.assertTrue(any(isinstance(widget, HudScrollbar) for widget in widgets))
        finally:
            window._close()

    def test_request_expanded_uses_hud_scrollbar(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_request_expanded()
            _flush_tk(window, iterations=5)

            widgets = _walk_widgets(window.request_root)

            self.assertFalse(any(isinstance(widget, tk.Scrollbar) for widget in widgets))
            self.assertTrue(any(isinstance(widget, HudScrollbar) for widget in widgets))
        finally:
            window._close()

    def test_request_expanded_toggle_strip_stays_below_rows(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_request_expanded()
            _flush_tk(window, iterations=5)

            self.assertIsNotNone(window.request_text)
            assert window.request_text is not None
            request_label_y = max(
                window.request_label.winfo_rooty(),
                window.request_label.master.winfo_rooty(),
            )
            if request_label_y <= 0:
                request_label_y = (
                    window.request_root.winfo_rooty()
                    + window.request_root.winfo_height()
                    - window.request_label.winfo_height()
                )
            self.assertGreater(
                request_label_y,
                window.request_text.winfo_rooty(),
            )
            self.assertGreaterEqual(
                request_label_y,
                window.request_text.winfo_rooty() + window.request_text.winfo_height() - 2,
            )
        finally:
            window._close()

    def test_tk_hud_shells_use_square_outer_corners(self) -> None:
        try:
            window = TokenHudWindow()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            top_shell = next(
                widget for widget in window.root.winfo_children() if isinstance(widget, RoundedHudShell)
            )
            request_shell = next(
                widget
                for widget in window.request_root.winfo_children()
                if isinstance(widget, RoundedHudShell)
            )

            self.assertEqual(top_shell._radius, HUD_SHELL_RADIUS)
            self.assertEqual(request_shell._radius, HUD_SHELL_RADIUS)
        finally:
            window._close()

    def test_win32_region_api_binds_64_bit_safe_signatures(self) -> None:
        import ctypes
        from ctypes import wintypes

        class FakeFunction:
            def __init__(self, result: int = 1) -> None:
                self.result = result
                self.argtypes = None
                self.restype = None

            def __call__(self, *args: object) -> int:
                return self.result

        dlls: dict[str, SimpleNamespace] = {}

        def fake_dll(name: str, *, use_last_error: bool = False) -> SimpleNamespace:
            del use_last_error
            if name == "gdi32":
                dll = SimpleNamespace(
                    CreateRectRgn=FakeFunction(),
                    CreateRoundRectRgn=FakeFunction(),
                    DeleteObject=FakeFunction(),
                )
            elif name == "user32":
                dll = SimpleNamespace(IsWindow=FakeFunction(), SetWindowRgn=FakeFunction())
            else:
                raise OSError(name)
            dlls[name] = dll
            return dll

        _win32_region_api.cache_clear()
        try:
            with patch.object(sys, "platform", "win32"), patch(
                "ctypes.WinDLL",
                side_effect=fake_dll,
                create=True,
            ):
                api = _win32_region_api()
        finally:
            _win32_region_api.cache_clear()

        self.assertIsNotNone(api)
        self.assertEqual(
            dlls["gdi32"].CreateRectRgn.argtypes,
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.assertIs(dlls["gdi32"].CreateRectRgn.restype, wintypes.HANDLE)
        self.assertEqual(
            dlls["gdi32"].CreateRoundRectRgn.argtypes,
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ],
        )
        self.assertIs(dlls["gdi32"].CreateRoundRectRgn.restype, wintypes.HANDLE)
        self.assertEqual(dlls["gdi32"].DeleteObject.argtypes, [wintypes.HANDLE])
        self.assertIs(dlls["gdi32"].DeleteObject.restype, wintypes.BOOL)
        self.assertEqual(dlls["user32"].IsWindow.argtypes, [wintypes.HWND])
        self.assertIs(dlls["user32"].IsWindow.restype, wintypes.BOOL)
        self.assertEqual(
            dlls["user32"].SetWindowRgn.argtypes,
            [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL],
        )
        self.assertIs(dlls["user32"].SetWindowRgn.restype, ctypes.c_int)

    def test_top_progress_bars_keep_rounded_radius(self) -> None:
        window = TokenHudWindow()
        try:
            strip = window.top_labels["bar"]
            self.assertTrue(all(bar._radius is HUD_PROGRESS_RADIUS for bar in strip._bars))

            window.toggle_top_expanded()
            _flush_tk(window)

            self.assertIs(window.top_labels["cache_progress"]._radius, HUD_PROGRESS_RADIUS)
            budget = window.top_labels["budget"]
            self.assertIs(budget._day._radius, HUD_PROGRESS_RADIUS)
            self.assertIs(budget._week._radius, HUD_PROGRESS_RADIUS)
        finally:
            window._close()

    def test_small_progress_fill_uses_full_rail_left_cap(self) -> None:
        rows = _progress_fill_surface_rows(
            width=120,
            height=32,
            fill_width=6,
            bg=HUD_BG,
            track=HUD_PROGRESS_TRACK,
            fill=HUD_PROGRESS_DAY,
            fill_end=HUD_PROGRESS_DAY_END,
            gloss=False,
            radius=HUD_PROGRESS_RADIUS,
        )

        top_row = rows[0].strip("{}").split()
        self.assertTrue(all(color == top_row[0] for color in top_row[:6]))

        middle_row = rows[16].strip("{}").split()
        self.assertTrue(any(color != middle_row[6] for color in middle_row[:6]))
        self.assertTrue(all(color == middle_row[6] for color in middle_row[6:12]))

    def test_progress_fill_rows_preserve_rounded_corner_background(self) -> None:
        rows = _progress_fill_surface_rows(
            width=24,
            height=20,
            fill_width=16,
            bg=HUD_HEADER_BG,
            track=HUD_PROGRESS_TRACK,
            fill=HUD_PROGRESS_DAY,
            fill_end=HUD_PROGRESS_DAY_END,
            gloss=False,
            radius=HUD_PROGRESS_RADIUS,
        )

        top_row = rows[0].strip("{}").split()
        self.assertTrue(all(color == HUD_HEADER_BG.lower() for color in top_row[:5]))
        self.assertTrue(all(color == HUD_HEADER_BG.lower() for color in top_row[-5:]))

    def test_progress_fill_surface_transparency_keeps_unfilled_right_side_clear(self) -> None:
        rows = _progress_fill_surface_transparency_rows(
            width=24,
            height=20,
            fill_width=16,
            radius=HUD_PROGRESS_RADIUS,
        )

        middle_row = set(rows[10])
        self.assertFalse(any(x in middle_row for x in range(0, 16)))
        self.assertTrue(all(x in middle_row for x in range(16, 24)))

    def test_request_expanded_reuses_top_palette(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_request_expanded()
            _flush_tk(window)

            self.assertEqual(str(window.request_label.cget("bg")), tk_hud_module.REQUEST_HUD_HEADER_BG)
            self.assertEqual(str(window.request_text.cget("bg")), tk_hud_module.REQUEST_HUD_PANEL_BG)
            self.assertEqual(str(window.request_text.cget("fg")), tk_hud_module.REQUEST_HUD_TEXT)
            self.assertEqual(
                window.request_text.tag_cget("normal", "foreground"),
                tk_hud_module.REQUEST_HUD_TEXT,
            )

            labels = [
                widget
                for widget in _walk_widgets(window.request_root)
                if isinstance(widget, tk.Label) and str(widget.cget("text")) == "轮次流水"
            ]
            self.assertEqual(len(labels), 1)
            self.assertEqual(str(labels[0].cget("bg")), tk_hud_module.REQUEST_HUD_BG)
        finally:
            window._close()

    def test_settings_dialog_hides_and_reopens_without_rebuilding_shell(self) -> None:
        window = TokenHudWindow()
        try:
            window._open_settings_dialog()
            _flush_tk(window)
            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None

            with patch.object(dialog, "withdraw", wraps=dialog.withdraw) as withdraw:
                window._hide_settings_dialog()
                withdraw.assert_called_once_with()

            self.assertEqual(window._settings_dialog, dialog)

            with (
                patch.object(dialog, "deiconify", wraps=dialog.deiconify) as deiconify,
                patch.object(window, "_select_settings_tab", wraps=window._select_settings_tab) as select_tab,
            ):
                window._open_settings_dialog()

            deiconify.assert_called_once_with()
            select_tab.assert_not_called()
        finally:
            window._close()

    def test_settings_dialog_reopen_while_visible_only_raises_window(self) -> None:
        window = TokenHudWindow()
        try:
            window._open_settings_dialog()
            _flush_tk(window)

            with patch.object(window, "_select_settings_tab", wraps=window._select_settings_tab) as select_tab:
                window._open_settings_dialog()

            select_tab.assert_not_called()
        finally:
            window._close()

    def test_settings_dialog_shell_is_prewarmed_while_hidden(self) -> None:
        window = TokenHudWindow()
        try:
            self.assertIsNone(window._settings_dialog)

            _flush_tk(window)

            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            self.assertEqual(dialog.state(), "withdrawn")
            self.assertIn("daily_budget_usd", window._settings_entries)
        finally:
            window._close()

    def test_settings_dialog_opens_shell_before_idle_tab_build(self) -> None:
        window = TokenHudWindow()
        try:
            with patch.object(window, "_select_settings_tab", wraps=window._select_settings_tab) as select_tab:
                window._open_settings_dialog()
                select_tab.assert_not_called()
                self.assertIsNotNone(window._settings_dialog)
                self.assertIsNotNone(window._settings_build_job)

                _flush_tk(window)

            select_tab.assert_called_once_with("settings")
            self.assertIn("daily_budget_usd", window._settings_entries)
        finally:
            window._close()

    def test_settings_dialog_is_raised_when_hud_refreshes(self) -> None:
        window = TokenHudWindow()
        try:
            dialog = SimpleNamespace(
                winfo_exists=lambda: True,
                lift=MagicMock(),
                attributes=MagicMock(),
                lower=MagicMock(),
            )
            window._settings_dialog = dialog

            window._apply_focus_state(True)

            dialog.lift.assert_called_once_with()
            dialog.attributes.assert_called_once_with("-topmost", True)
        finally:
            window._close()

    def test_ui_interaction_settle_window_skips_tombstone_polling(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            locator = SimpleNamespace(
                find=MagicMock(return_value=rect),
                is_active=MagicMock(return_value=False),
            )
            window.locator = locator
            window._attached = True
            window._last_rect = rect
            window._mark_ui_interaction(duration_ms=120)
            window._apply_geometry = MagicMock()

            window.sync_codex_window()

            locator.find.assert_not_called()
            locator.is_active.assert_not_called()
            window._apply_geometry.assert_called_once_with()
        finally:
            window._close()

    def test_toggle_shows_cached_top_panel_and_starts_animation_without_rebuild(self) -> None:
        window = TokenHudWindow()
        try:
            window._apply_geometry = MagicMock()
            window._rebuild_top_ui = MagicMock()
            expanded_frame = window._top_expanded_frame
            collapsed_frame = window._top_collapsed_frame
            self.assertIsNotNone(expanded_frame)
            self.assertIsNotNone(collapsed_frame)
            assert expanded_frame is not None
            assert collapsed_frame is not None

            window.toggle_top_expanded()

            window._apply_geometry.assert_not_called()
            window._rebuild_top_ui.assert_not_called()
            self.assertIsNone(window._top_rebuild_job)
            self.assertTrue(window.top_expanded)
            self.assertEqual(expanded_frame.winfo_manager(), "place")
            self.assertEqual(collapsed_frame.winfo_manager(), "place")
            self.assertTrue(window._top_animation_active())
        finally:
            window._close()

    def test_collapsed_state_prewarms_expanded_core_fields(self) -> None:
        window = TokenHudWindow()
        try:
            snapshot = ParsedSession(session_id="prewarm-expanded-core", status="live")
            snapshot.confirmed.cumulative_total = 123_456
            snapshot.confirmed.cumulative_input = 100_000
            snapshot.confirmed.cumulative_cached = 80_000
            snapshot.confirmed.cumulative_output = 23_456
            snapshot.today_tokens = 123_456
            snapshot.today_cost_usd = 1.23

            window.update_display(snapshot)
            self.assertFalse(window.top_expanded)
            _flush_tk(window)

            self.assertIsNone(window._top_core_prewarm_job)
            self.assertEqual(window.top_labels["topSessionTokens"].cget("text"), "123k")
            self.assertTrue(str(window.top_labels["topSessionCost"].cget("text")).startswith("$"))
            self.assertEqual(window.top_labels["cache_progress"].cget("text"), "缓存命中 80%")
        finally:
            window._close()

    def test_attached_toggle_skips_slow_anchor_probe_during_interaction(self) -> None:
        window = TokenHudWindow()
        try:
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            locator = SimpleNamespace(
                anchor_geometry=MagicMock(
                    side_effect=AssertionError("slow anchor probe should not run in click path")
                )
            )
            window.locator = locator
            window._attached = True
            window._last_rect = rect
            window._apply_window_geometry = MagicMock()

            window.toggle_top_expanded()

            locator.anchor_geometry.assert_not_called()
            self.assertTrue(window.top_expanded)
            self.assertIsNone(window._top_rebuild_job)
        finally:
            window._close()

    def test_settings_exit_confirms_before_closing_hud(self) -> None:
        window = TokenHudWindow()
        try:
            window._open_settings_dialog()

            with (
                patch("codex_usage_hud.ui.tk_hud.messagebox.askyesno", return_value=True) as askyesno,
                patch.object(window, "close") as close,
            ):
                window._confirm_full_exit()

            askyesno.assert_called_once()
            close.assert_called_once_with("settings_exit")
        finally:
            window._close()

    def test_tk_cdp_dom_env_does_not_change_hud_auto_geometry(self) -> None:
        rect = WindowRect(left=100, top=50, right=1300, bottom=850)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(HUD_CDP_DOM_ENV, None)
            window = TokenHudWindow()
            try:
                self.assertFalse(window._theme_probe.enabled)
                self.assertEqual(
                    window._attached_geometry("top", rect, False),
                    _visual_anchor_geometry("top", rect, False),
                )
            finally:
                window._close()

        with patch.dict(os.environ, {HUD_CDP_DOM_ENV: "1"}, clear=False):
            window = TokenHudWindow()
            try:
                self.assertFalse(window._theme_probe.enabled)
                self.assertEqual(
                    window._attached_geometry("top", rect, False),
                    _visual_anchor_geometry("top", rect, False),
                )
            finally:
                window._close()

    def test_windows_locator_roi_geometry_works_when_demo_overlay_disabled(self) -> None:
        class _FakeRoiTracker:
            enabled = True

            def get_header_roi_snapshot(self) -> SimpleNamespace:
                return SimpleNamespace(
                    status="visible",
                    hwnd=321,
                    roi=WindowRect(left=360, top=64, right=1120, bottom=120),
                )

            def get_bottom_roi_snapshot(self) -> SimpleNamespace:
                return SimpleNamespace(
                    status="visible",
                    hwnd=321,
                    roi=WindowRect(left=520, top=732, right=1000, bottom=786),
                )

        locator = object.__new__(_WindowsCodexLocator)
        locator._header_roi_demo_enabled = False
        locator._tracker = _FakeRoiTracker()
        rect = WindowRect(hwnd=321, left=100, top=50, right=1300, bottom=850)

        header = locator.header_roi_geometry(rect)
        bottom = locator.bottom_roi_geometry(rect)

        self.assertIsNotNone(header)
        self.assertIsNotNone(bottom)
        assert header is not None
        assert bottom is not None
        self.assertEqual((header.left, header.top, header.right, header.bottom), (360, 64, 1120, 120))
        self.assertEqual((bottom.left, bottom.top, bottom.right, bottom.bottom), (520, 732, 1000, 786))

    def test_top_uses_auto_geometry_without_dom_anchor_dependency(self) -> None:
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

            self.assertEqual(top, _visual_anchor_geometry("top", rect, False))
            self.assertEqual(request, _visual_anchor_geometry("request", rect, False))
        finally:
            window._close()

    def test_top_auto_geometry_ignores_dom_anchor_changes(self) -> None:
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

            expected = _visual_anchor_geometry("top", rect, False)
            self.assertEqual(first, expected)
            self.assertEqual(second, expected)
        finally:
            window._close()

    def test_windows_locator_rejects_browser_process_with_codex_title(self) -> None:
        self.assertFalse(_WindowsCodexLocator._is_codex_process("chrome.exe"))
        self.assertFalse(_WindowsCodexLocator._is_codex_process("msedge.exe"))
        self.assertTrue(_WindowsCodexLocator._is_codex_process("Codex.exe"))

    def test_top_cdp_anchor_prefers_computed_title_slot(self) -> None:
        locator = object.__new__(_WindowsCodexLocator)
        locator._top_dom_anchors_enabled = True
        locator._dom_anchors_enabled = False
        locator._last_cdp_anchor_status = {}
        locator._cdp_probe = SimpleNamespace(
            snapshot=lambda: CdpDomSnapshot(
                session_id="thread-123",
                title="Selected Thread",
                device_pixel_ratio=1.0,
                header_rect=CdpRect(20.0, 24.0, 920.0, 68.0),
                title_rect=CdpRect(60.0, 28.0, 320.0, 64.0),
                top_slot_rect=CdpRect(340.0, 24.0, 760.0, 68.0),
            ),
            last_status="ok",
            last_error="",
        )
        rect = WindowRect(left=100, top=50, right=1500, bottom=900)

        anchor = locator._cdp_anchor_geometry("top", rect, TOP_DOCK_HEIGHT)

        self.assertEqual(
            anchor,
            HudAnchor(
                left=440,
                top=74,
                right=860,
                bottom=118,
                default_x=440,
                default_y=78,
                default_width=420,
                source="cdp:title",
            ),
        )

    def test_windows_locator_does_not_enable_cdp_anchors_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(HUD_CDP_DOM_ENV, None)

            locator = _WindowsCodexLocator()

        if locator.enabled:
            self.assertFalse(locator._top_dom_anchors_enabled)
            self.assertFalse(locator._dom_anchors_enabled)
            self.assertIsNone(locator._cdp_probe)

    def test_windows_locator_disables_roi_demo_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tk_hud_module.HUD_UIA_ROI_DEMO_ENV, None)

            locator = _WindowsCodexLocator()

        if locator.enabled:
            self.assertFalse(locator._header_roi_demo_enabled)

    def test_tk_header_roi_demo_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tk_hud_module.HUD_UIA_ROI_DEMO_ENV, None)
            window = TokenHudWindow()
            try:
                self.assertFalse(window._use_header_roi_demo)
                self.assertIsNone(window._header_roi_overlay)
                self.assertIsNone(window._bottom_roi_overlay)
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

            self.assertIn("本会话 452k/", window.top_labels["bar"].cget("text"))
            self.assertIn("/87%", window.top_labels["bar"].cget("text"))
            self.assertIn("今日 41.1M/$39.31", window.top_labels["bar"].cget("text"))
            self.assertIn("本周 159.5M/$138.23", window.top_labels["bar"].cget("text"))
        finally:
            window._close()

    def test_collapsed_hud_applies_live_codex_theme_tokens(self) -> None:
        window = TokenHudWindow()
        try:
            themed_export = CodexThemeExport.from_share_string(
                'codex-theme-v1:{"codeThemeId":"codex","theme":{"accent":"#339cff",'
                '"contrast":60,"fonts":{"code":null,"ui":null},"ink":"#ffffff",'
                '"opaqueWindows":false,"semanticColors":{"diffAdded":"#40c977",'
                '"diffRemoved":"#fa423e","skill":"#ad7bf9"},"surface":"#181818"},'
                '"variant":"dark"}'
            )
            tokens = HudThemeTokens.from_theme(themed_export)
            window._theme_probe = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    source="cdp",
                    hud_tokens=tokens,
                )
            )
            window.update_display(ParsedSession())

            self.assertEqual(str(window.request_label.cget("fg")).lower(), "#339cff")
        finally:
            window._close()

    def test_collapsed_hud_applies_persisted_codex_theme_tokens(self) -> None:
        window = TokenHudWindow()
        try:
            themed_export = CodexThemeExport.from_share_string(
                'codex-theme-v1:{"codeThemeId":"github","theme":{"accent":"#0969da",'
                '"contrast":40,"fonts":{"code":null,"ui":null},"ink":"#1f2328",'
                '"opaqueWindows":false,"semanticColors":{"diffAdded":"#1a7f37",'
                '"diffRemoved":"#cf222e","skill":"#8250df"},"surface":"#ffffff"},'
                '"variant":"light"}'
            )
            tokens = HudThemeTokens.from_theme(themed_export)
            window._theme_probe = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    source="persisted",
                    hud_tokens=tokens,
                )
            )
            window.update_display(ParsedSession())

            self.assertEqual(str(window.request_label.cget("fg")).lower(), "#0969da")
            strip = window.top_labels["bar"]
            budget_bar = strip._bars[1]
            self.assertNotEqual(budget_bar._metric.track_text.lower(), "#c1c7d0")
            self.assertGreaterEqual(
                tk_hud_module._contrast_ratio_hex(
                    budget_bar._metric.track_text,
                    tk_hud_module.HUD_PROGRESS_TRACK,
                ),
                4.5,
            )
        finally:
            window._close()

    def test_settings_dialog_uses_readable_live_theme_colors(self) -> None:
        window = TokenHudWindow()
        try:
            themed_export = CodexThemeExport.from_share_string(
                'codex-theme-v1:{"codeThemeId":"codex","theme":{"accent":"#339cff",'
                '"contrast":45,"fonts":{"code":null,"ui":null},"ink":"#1a1c1f",'
                '"opaqueWindows":false,"semanticColors":{"diffAdded":"#40c977",'
                '"diffRemoved":"#fa423e","skill":"#8250df"},"surface":"#ffffff"},'
                '"variant":"light"}'
            )
            tokens = HudThemeTokens.from_theme(themed_export)
            window._theme_probe = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    source="persisted",
                    hud_tokens=tokens,
                )
            )
            window.update_display(ParsedSession())
            window._open_settings_dialog("settings")
            _flush_tk(window, iterations=5)

            self.assertIsNotNone(window._settings_status_label)
            status_label = window._settings_status_label
            status_bg = str(status_label.cget("bg"))
            status_fg = str(status_label.cget("fg"))
            self.assertGreaterEqual(
                tk_hud_module._contrast_ratio_hex(status_fg, status_bg),
                4.5,
            )

            entries = [
                widget
                for widget in _walk_widgets(window._settings_dialog)
                if type(widget) is tk.Entry
            ]
            self.assertTrue(entries)
            for entry in entries[:4]:
                self.assertGreaterEqual(
                    tk_hud_module._contrast_ratio_hex(
                        str(entry.cget("fg")),
                        str(entry.cget("bg")),
                    ),
                    4.5,
                )

            for button in window._settings_tab_buttons.values():
                self.assertGreaterEqual(
                    tk_hud_module._contrast_ratio_hex(
                        str(button.cget("fg")),
                        str(button.cget("bg")),
                    ),
                    4.5,
                )

            style = ttk.Style(window.root)
            combo_fg = style.lookup(
                "CodexUsageHud.TCombobox",
                "foreground",
                ("readonly",),
            ) or style.lookup("CodexUsageHud.TCombobox", "foreground")
            combo_bg = style.lookup(
                "CodexUsageHud.TCombobox",
                "fieldbackground",
                ("readonly",),
            ) or style.lookup("CodexUsageHud.TCombobox", "fieldbackground")
            self.assertGreaterEqual(
                tk_hud_module._contrast_ratio_hex(combo_fg, combo_bg),
                4.5,
            )
            combo_select_fg = style.lookup(
                "CodexUsageHud.TCombobox",
                "selectforeground",
                ("readonly",),
            ) or style.lookup("CodexUsageHud.TCombobox", "selectforeground")
            combo_select_bg = style.lookup(
                "CodexUsageHud.TCombobox",
                "selectbackground",
                ("readonly",),
            ) or style.lookup("CodexUsageHud.TCombobox", "selectbackground")
            self.assertGreaterEqual(
                tk_hud_module._contrast_ratio_hex(combo_select_fg, combo_select_bg),
                4.5,
            )

            combos = [
                widget
                for widget in _walk_widgets(window._settings_dialog)
                if isinstance(widget, ttk.Combobox)
            ]
            self.assertTrue(combos)
            for combo in combos:
                window._style_settings_combobox_popup(combo)
                popdown = str(
                    combo.tk.call("ttk::combobox::PopdownWindow", str(combo))
                )
                listbox = f"{popdown}.f.l"
                popup_bg = str(combo.tk.call(listbox, "cget", "-background"))
                popup_fg = str(combo.tk.call(listbox, "cget", "-foreground"))
                popup_select_bg = str(combo.tk.call(listbox, "cget", "-selectbackground"))
                popup_select_fg = str(combo.tk.call(listbox, "cget", "-selectforeground"))
                self.assertGreaterEqual(
                    tk_hud_module._contrast_ratio_hex(popup_fg, popup_bg),
                    4.5,
                )
                self.assertGreaterEqual(
                    tk_hud_module._contrast_ratio_hex(popup_select_fg, popup_select_bg),
                    4.5,
                )
        finally:
            window._close()

    def test_collapsed_top_auto_width_expands_past_saved_width(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            snapshot = ParsedSession()
            snapshot.confirmed.cumulative_total = 6_900_000
            snapshot.confirmed.cumulative_input = 6_690_000
            snapshot.confirmed.cumulative_cached = 5_970_000
            snapshot.today_tokens = 41_100_000
            snapshot.today_cost_usd = 39.31
            snapshot.daily_limit_usd = 100.0
            snapshot.week_tokens = 159_500_000
            snapshot.week_cost_usd = 138.23
            snapshot.weekly_limit_usd = 300.0
            window.settings.top.width = 120
            window.settings.top.collapsed_width_locked = False
            window.root.geometry("120x36+20+20")

            window.update_display(snapshot)
            _flush_tk(window)

            requested = window.root.winfo_reqwidth()
            self.assertGreater(requested, 120)
            self.assertEqual(window.root.winfo_width(), requested)
            strip = window.top_labels["bar"]
            self.assertFalse(strip._scrolling_enabled)
            session_bar, *tail_bars = strip._bars
            available = (
                session_bar._canvas.winfo_width()
                - ((session_bar._padding_x * 2) + session_bar._overflow_reserved_width() + 2)
            )
            self.assertEqual(
                session_bar._fit_text(session_bar._metric.label, max(0, available)),
                session_bar._metric.label,
            )
            for bar in tail_bars:
                available = (
                    bar._canvas.winfo_width()
                    - ((bar._padding_x * 2) + bar._overflow_reserved_width() + 2)
                )
                if bar._metric.right_text:
                    available -= bar._font.measure(bar._metric.right_text) + 8
                    self.assertGreaterEqual(
                        bar._canvas.winfo_width(),
                        bar._font.measure(bar._metric.right_text)
                        + (bar._padding_x * 2)
                        + bar._overflow_reserved_width()
                        + 2,
                    )
                self.assertNotEqual(bar._fit_text(bar._metric.label, max(0, available)), "")
            window.root.geometry(f"{requested + 160}x{window.root.winfo_height()}+20+20")
            _flush_tk(window)
            widths = [bar.winfo_width() for bar in strip._bars]
            self.assertEqual(widths[1], widths[2])
            self.assertEqual(sum(widths) + 14, strip.winfo_width())
            window.settings.top.width = 900
            self.assertLess(window._top_size()[0], 900)
            self.assertEqual(window._top_size()[0], window.root.winfo_reqwidth())
        finally:
            window._close()

    def test_collapsed_top_manual_width_survives_expand_roundtrip(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            snapshot = ParsedSession()
            snapshot.confirmed.cumulative_total = 6_900_000
            snapshot.confirmed.cumulative_input = 6_690_000
            snapshot.confirmed.cumulative_cached = 5_970_000
            snapshot.today_tokens = 41_100_000
            snapshot.today_cost_usd = 39.31
            snapshot.daily_limit_usd = 100.0
            snapshot.week_tokens = 159_500_000
            snapshot.week_cost_usd = 138.23
            snapshot.weekly_limit_usd = 300.0

            window.update_display(snapshot)
            _flush_tk(window)

            resized_width = window.root.winfo_reqwidth() + 160
            window.root.geometry(
                f"{resized_width}x{window.root.winfo_height()}+20+20"
            )
            _flush_tk(window)
            window._remember_window_width("top", window.root, reason="test-resize")

            self.assertFalse(window.settings.top.collapsed_width_locked)
            self.assertEqual(window.settings.top.width, resized_width)

            window.toggle_top_expanded()
            _flush_tk(window)
            self.assertEqual(window.root.winfo_width(), resized_width)

            window.toggle_top_expanded()
            _flush_tk(window)
            self.assertEqual(window.root.winfo_width(), resized_width)
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

    def test_attached_geometry_ignores_legacy_saved_width_ratio(self) -> None:
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
            expected_x, expected_y, expected_width, expected_height = _visual_anchor_geometry(
                "top",
                rect,
                False,
            )

            self.assertEqual((x, y, height), (expected_x, expected_y, expected_height))
            self.assertEqual(width, expected_width)
        finally:
            window._close()

    def test_move_saves_current_session_position_without_anchor_or_ratio(self) -> None:
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

            self.assertIsNone(window.settings.request.width_ratio)
            self.assertIsNone(window.settings.request.anchor_x_ratio)
            self.assertIsNone(window.settings.request.anchor_y_ratio)
            self.assertIn("request", window._session_manual_targets)
            self.assertEqual(window._request_manual_position, (650, 660))
        finally:
            window._close()

    def test_resize_saves_current_session_width_without_anchor_or_ratio(self) -> None:
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

            self.assertEqual(window.settings.top.width, 400)
            self.assertIsNone(window.settings.top.width_ratio)
            self.assertIsNone(window.settings.top.anchor_x_ratio)
            self.assertIsNone(window.settings.top.anchor_y_ratio)
            self.assertIn("top", window._session_manual_targets)
        finally:
            window._close()

    def test_tk_manual_geometry_resets_when_session_changes(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            rect = WindowRect(left=100, top=50, right=1300, bottom=850)
            window.locator = _FakeAnchorLocator(
                {},
                header_roi=WindowRect(left=360, top=64, right=1120, bottom=120),
            )
            window._attached = True
            window._last_rect = rect
            window.update_display(ParsedSession(session_id="session-a"))

            fake = _FakeWindow(x=420, y=92, width=400, height=TOP_DOCK_HEIGHT)
            window._remember_window_position("top", fake, reason="test-move")
            window._remember_window_width("top", fake, reason="test-resize")
            self.assertIn("top", window._session_manual_targets)
            self.assertEqual(
                window._attached_geometry("top", rect, False),
                (420, 92, 400, TOP_DOCK_HEIGHT),
            )

            window.update_display(ParsedSession(session_id="session-a"))
            self.assertIn("top", window._session_manual_targets)

            window.update_display(ParsedSession(session_id="session-b"))
            self.assertNotIn("top", window._session_manual_targets)
            self.assertIsNone(window._top_manual_position)
            self.assertIsNone(window.settings.top.width)
            self.assertEqual(
                window._attached_geometry("top", rect, False),
                (360, 64, 760, TOP_DOCK_HEIGHT),
            )
        finally:
            window._close()

    def test_tk_pin_button_toggles_persistence_without_expanding_hud(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = TokenHudWindow()
            try:
                _stop_background_jobs(window)
                store = HudSettingsStore(Path(temp_dir) / "hud_settings.json")
                store.save(HudSettings.empty())
                window.settings_store = store
                window.settings = HudSettings.empty()
                window._sync_pin_buttons()
                rect = WindowRect(left=100, top=50, right=1300, bottom=850)
                window.locator = _FakeAnchorLocator(
                    {},
                    header_roi=WindowRect(left=360, top=64, right=1120, bottom=120),
                )
                window._attached = True
                window._last_rect = rect
                window.root.geometry(f"620x{TOP_DOCK_HEIGHT}+410+180")
                _flush_tk(window)

                self.assertFalse(window.top_expanded)
                window._pin_buttons["top"][0].invoke()
                _flush_tk(window)

                saved = store.load().top
                self.assertTrue(saved.pinned)
                self.assertEqual((saved.absolute_x, saved.absolute_y), (410, 180))
                self.assertEqual(saved.width, 620)
                self.assertFalse(window.top_expanded)

                window.root.geometry(f"620x{TOP_DOCK_HEIGHT}+520+240")
                _flush_tk(window)
                window._pin_buttons["top"][0].invoke()
                _flush_tk(window)

                cleared = store.load().top
                self.assertFalse(cleared.pinned)
                self.assertIsNone(cleared.absolute_x)
                self.assertIsNone(cleared.width)
                self.assertEqual(
                    (window.root.winfo_x(), window.root.winfo_y(), window.root.winfo_width()),
                    (360, 64, 760),
                )
                self.assertFalse(window.top_expanded)
            finally:
                window._close()

    def test_tk_whole_panel_drag_moves_window_and_preserves_click_toggle(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            window.root.geometry("400x36+100+80")
            _flush_tk(window)
            window._save_settings = MagicMock()
            press = SimpleNamespace(widget=window.root, x_root=180, y_root=95)
            drag = SimpleNamespace(widget=window.root, x_root=210, y_root=112)
            release = SimpleNamespace(widget=window.root, x_root=210, y_root=112)

            self.assertIsNone(window._handle_window_press(press, "top", window.root))
            self.assertEqual(window._handle_window_drag(drag, "top", window.root), "break")
            self.assertEqual(window._handle_window_release(release, "top", window.root), "break")
            _flush_tk(window)

            self.assertEqual((window.root.winfo_x(), window.root.winfo_y()), (130, 97))
            self.assertEqual(window._top_manual_position, (130, 97))
            self.assertFalse(window.top_expanded)
            window._save_settings.assert_called_once_with()

            click = SimpleNamespace(widget=window.root, x_root=200, y_root=100)
            self.assertIsNone(window._handle_window_press(click, "top", window.root))
            self.assertEqual(window._handle_window_release(click, "top", window.root), "break")
            self.assertTrue(window.top_expanded)
        finally:
            window._close()

    def test_tk_top_expanded_body_does_not_toggle_or_drag_window(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            window.root.geometry("400x280+100+80")
            window.top_expanded = True
            window._show_top_state(True)
            _flush_tk(window)
            window._save_settings = MagicMock()
            body = window.top_labels["top_body_canvas"].master
            press = SimpleNamespace(widget=body, x_root=220, y_root=150)
            drag = SimpleNamespace(widget=body, x_root=260, y_root=180)
            release = SimpleNamespace(widget=body, x_root=260, y_root=180)

            self.assertIsNone(window._handle_window_press(press, "top", window.root))
            self.assertIsNone(window._handle_window_drag(drag, "top", window.root))
            self.assertEqual(window._handle_window_release(release, "top", window.root), "break")

            self.assertTrue(window.top_expanded)
            self.assertEqual((window.root.winfo_x(), window.root.winfo_y()), (100, 80))
            self.assertIsNone(window._drag_window)
            window._save_settings.assert_not_called()

            header = window.top_labels["title"].master
            click = SimpleNamespace(widget=header, x_root=220, y_root=96)
            self.assertIsNone(window._handle_window_press(click, "top", window.root))
            self.assertEqual(window._handle_window_release(click, "top", window.root), "break")
            self.assertFalse(window.top_expanded)
        finally:
            window._close()

    def test_tk_whole_panel_drag_moves_request_window_and_saves_position(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            window.request_root.geometry("380x32+100+140")
            _flush_tk(window)
            window._save_settings = MagicMock()
            press = SimpleNamespace(widget=window.request_root, x_root=220, y_root=156)
            drag = SimpleNamespace(widget=window.request_root, x_root=255, y_root=174)
            release = SimpleNamespace(widget=window.request_root, x_root=255, y_root=174)

            self.assertIsNone(window._handle_window_press(press, "request", window.request_root))
            self.assertEqual(window._handle_window_drag(drag, "request", window.request_root), "break")
            self.assertEqual(window._handle_window_release(release, "request", window.request_root), "break")
            _flush_tk(window)

            self.assertEqual((window.request_root.winfo_x(), window.request_root.winfo_y()), (135, 158))
            self.assertEqual(window._request_manual_position, (135, 158))
            self.assertEqual(window.settings.request.absolute_x, 135)
            self.assertEqual(window.settings.request.absolute_y, 158)
            window._save_settings.assert_called_once_with()
        finally:
            window._close()

    def test_tk_whole_panel_drag_ignores_interactive_controls_and_resize_edges(self) -> None:
        window = TokenHudWindow()
        try:
            _stop_background_jobs(window)
            window.root.geometry("400x36+100+80")
            _flush_tk(window)
            button = tk.Button(window.root)
            press = SimpleNamespace(widget=button, x_root=180, y_root=95)
            drag = SimpleNamespace(widget=button, x_root=220, y_root=120)

            self.assertIsNone(window._handle_window_press(press, "top", window.root))
            self.assertIsNone(window._handle_window_drag(drag, "top", window.root))
            self.assertEqual((window.root.winfo_x(), window.root.winfo_y()), (100, 80))
            self.assertIsNone(window._drag_window)

            edge_press = SimpleNamespace(widget=window.root, x_root=103, y_root=95)
            with patch.object(window, "_start_resize", return_value="break") as start_resize:
                self.assertEqual(window._handle_window_press(edge_press, "top", window.root), "break")
            start_resize.assert_called_once()
            self.assertIsNone(window._drag_window)
        finally:
            window._close()

    def test_top_collapsed_left_edge_resize_moves_left_and_only_changes_width(self) -> None:
        window = TokenHudWindow()
        try:
            fake = _FakeWindow(x=420, y=92, width=400, height=TOP_DOCK_HEIGHT)

            window._start_resize(
                SimpleNamespace(x_root=820, y_root=92),
                "top",
                fake,
                "left",
            )
            window._resize_window_size(SimpleNamespace(x_root=780, y_root=40))

            self.assertEqual(
                (fake.winfo_x(), fake.winfo_y(), fake.winfo_width(), fake.winfo_height()),
                (380, 92, 440, TOP_DOCK_HEIGHT),
            )
        finally:
            window._close()

    def test_top_collapsed_pointer_hit_test_matches_legacy_left_edge_zone(self) -> None:
        window = object.__new__(TokenHudWindow)
        window.top_expanded = False
        window.request_expanded = False
        fake = _FakeWindow(x=420, y=92, width=400, height=TOP_DOCK_HEIGHT)

        self.assertEqual(window._resize_edge_from_pointer(fake, "top", 422, 100), "left")
        self.assertEqual(window._resize_edge_from_pointer(fake, "top", 420, 100), "")
        self.assertEqual(window._resize_edge_from_pointer(fake, "top", 426, 100), "")

    def test_top_expanded_bottom_corner_resize_keeps_top_fixed(self) -> None:
        window = TokenHudWindow()
        try:
            window.top_expanded = True
            fake = _FakeWindow(x=120, y=80, width=420, height=TOP_DOCK_EXPANDED_HEIGHT)

            window._start_resize(
                SimpleNamespace(x_root=540, y_root=470),
                "top",
                fake,
                "bottom-right",
            )
            window._resize_window_size(SimpleNamespace(x_root=580, y_root=500))

            self.assertEqual(
                (fake.winfo_x(), fake.winfo_y(), fake.winfo_width(), fake.winfo_height()),
                (120, 80, 460, TOP_DOCK_EXPANDED_HEIGHT + 30),
            )
        finally:
            window._close()

    def test_request_expanded_pointer_hit_test_matches_legacy_top_right_corner(self) -> None:
        window = object.__new__(TokenHudWindow)
        window.top_expanded = False
        window.request_expanded = True
        fake = _FakeWindow(x=220, y=600, width=320, height=REQUEST_DOCK_EXPANDED_HEIGHT)

        self.assertEqual(window._resize_edge_from_pointer(fake, "request", 529, 604), "top-right")
        self.assertEqual(window._resize_edge_from_pointer(fake, "request", 528, 611), "top-right")
        self.assertEqual(window._resize_edge_from_pointer(fake, "request", 527, 613), "")

    def test_request_expanded_top_corner_resize_keeps_bottom_fixed(self) -> None:
        window = TokenHudWindow()
        try:
            window.request_expanded = True
            fake = _FakeWindow(x=220, y=600, width=320, height=REQUEST_DOCK_EXPANDED_HEIGHT)

            window._start_resize(
                SimpleNamespace(x_root=220, y_root=600),
                "request",
                fake,
                "top-left",
            )
            window._resize_window_size(SimpleNamespace(x_root=180, y_root=570))

            self.assertEqual(
                (fake.winfo_x(), fake.winfo_y(), fake.winfo_width(), fake.winfo_height()),
                (180, 570, 360, REQUEST_DOCK_EXPANDED_HEIGHT + 30),
            )
            self.assertEqual(fake.winfo_y() + fake.winfo_height(), 780)
        finally:
            window._close()

    def test_request_auto_geometry_ignores_legacy_input_anchor(self) -> None:
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

            self.assertEqual((x, y, width, height), _visual_anchor_geometry("request", rect, False))
        finally:
            window._close()

    def test_native_anchor_is_disabled_by_default_for_scroll_stability(self) -> None:
        window = object.__new__(TokenHudWindow)
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
        window.settings = HudSettings.empty()
        window.settings.request.anchor_x_ratio = 0.0
        window.settings.request.anchor_y_ratio = 0.0
        window.settings.request.anchor_source = "test-moving-uia"
        window.settings.request.width = None
        window.settings.request.width_ratio = None
        window.settings.request.relative_x_ratio = None
        window.settings.request.relative_bottom_ratio = None
        window._last_geometry_clamp = {}

        x, y, width, height = window._attached_geometry("request", rect, False)

        self.assertEqual((x, y, width, height), (460, 782, 495, 32))

    def test_top_roi_at_window_top_is_not_clamped_below_title_bar(self) -> None:
        rect = WindowRect(left=100, top=0, right=1300, bottom=850)
        roi = WindowRect(left=360, top=0, right=1120, bottom=38)
        window = object.__new__(TokenHudWindow)
        window.locator = _FakeAnchorLocator({}, header_roi=roi)
        window.settings = HudSettings.empty()
        window.top_expanded = False
        window._top_collapsed_width_override = None
        window._last_geometry_clamp = {}

        self.assertEqual(
            window._attached_geometry("top", rect, False),
            (360, 0, 760, TOP_DOCK_HEIGHT),
        )

    def test_qt_top_roi_at_window_top_is_not_clamped_below_title_bar(self) -> None:
        rect = WindowRect(left=100, top=0, right=1300, bottom=850)
        roi = WindowRect(left=360, top=0, right=1120, bottom=38)
        impl = object.__new__(qt_hud_module._QtHudWindowImpl)
        impl.locator = _FakeAnchorLocator({}, header_roi=roi)
        impl.settings = HudSettings.empty()
        impl.top_window = SimpleNamespace(_target_height=lambda _expanded: TOP_DOCK_HEIGHT)
        impl.request_window = SimpleNamespace()
        impl._session_manual_targets = set()

        self.assertEqual(
            impl._attached_panel_geometry("top", rect, False),
            (360, 0, 760, TOP_DOCK_HEIGHT),
        )

    def test_tk_header_roi_event_schedules_window_sync(self) -> None:
        class _Root:
            def __init__(self) -> None:
                self.calls: list[tuple[int, Callable[[], None]]] = []

            def after(self, delay: int, callback: Callable[[], None]) -> str:
                self.calls.append((delay, callback))
                return "after-id"

        window = object.__new__(TokenHudWindow)
        window.root = _Root()
        window._header_roi_refresh_job = None
        window._refresh_header_roi_geometry = MagicMock()

        window._schedule_header_roi_refresh()

        self.assertEqual(window._header_roi_refresh_job, "after-id")
        self.assertEqual(window.root.calls[0][0], 0)
        window.root.calls[0][1]()
        window._refresh_header_roi_geometry.assert_called_once()
        self.assertIsNone(window._header_roi_refresh_job)

    def test_qt_header_roi_event_schedules_window_follow(self) -> None:
        impl = object.__new__(qt_hud_module._QtHudWindowImpl)
        impl._header_roi_refresh_queued = False
        impl._refresh_header_roi_geometry = MagicMock()

        with patch.object(
            qt_hud_module.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callback(),
        ) as single_shot:
            impl._schedule_header_roi_refresh()

        single_shot.assert_called_once()
        impl._refresh_header_roi_geometry.assert_called_once()
        self.assertFalse(impl._header_roi_refresh_queued)

    def test_tk_header_roi_event_refreshes_only_top_geometry(self) -> None:
        rect = WindowRect(hwnd=321, left=100, top=50, right=1300, bottom=850)
        locator = _FakeAnchorLocator(
            {},
            header_roi=WindowRect(hwnd=321, left=360, top=64, right=1120, bottom=120),
            bottom_roi=WindowRect(hwnd=321, left=520, top=732, right=1000, bottom=786),
            window_rect=rect,
        )
        window = object.__new__(TokenHudWindow)
        window.locator = locator
        window.settings = HudSettings.empty()
        window.top_expanded = False
        window._top_collapsed_width_override = None
        window._last_geometry_clamp = {}
        window._attached = True
        window._last_rect = rect
        window._move_target = None
        window._resize_target = None
        window._top_animation_active = lambda: False
        window._hud_hwnds = lambda: set()
        window._exit_tombstone = MagicMock()
        window._apply_focus_state = MagicMock()
        window._set_alpha = MagicMock()
        window._apply_window_geometry = MagicMock()
        window._sync_header_roi_demo = MagicMock()
        window.root = MagicMock()

        window._refresh_header_roi_geometry()

        window._apply_window_geometry.assert_called_once()
        self.assertEqual(window._apply_window_geometry.call_args.args[0], "top")
        self.assertEqual(locator.header_roi_calls, 1)
        self.assertEqual(locator.bottom_roi_calls, 0)

    def test_qt_header_roi_event_refreshes_only_top_geometry(self) -> None:
        class _Panel:
            def __init__(self) -> None:
                self._manual_positioned = False
                self.expanded = False
                self.moves: list[tuple[int, int]] = []
                self.resizes: list[tuple[int, int]] = []

            def _target_height(self, expanded: bool) -> int:
                del expanded
                return TOP_DOCK_HEIGHT

            def geometry_interaction_active(self) -> bool:
                return False

            def height(self) -> int:
                return TOP_DOCK_HEIGHT

            def resize(self, width: int, height: int) -> None:
                self.resizes.append((width, height))

            def move(self, x: int, y: int) -> None:
                self.moves.append((x, y))

            def isVisible(self) -> bool:
                return True

            def show(self) -> None:
                return None

        rect = WindowRect(hwnd=321, left=100, top=50, right=1300, bottom=850)
        locator = _FakeAnchorLocator(
            {},
            header_roi=WindowRect(hwnd=321, left=360, top=64, right=1120, bottom=120),
            bottom_roi=WindowRect(hwnd=321, left=520, top=732, right=1000, bottom=786),
            window_rect=rect,
        )
        impl = object.__new__(qt_hud_module._QtHudWindowImpl)
        impl.locator = locator
        impl.settings = HudSettings.empty()
        impl.top_window = _Panel()
        impl.request_window = SimpleNamespace(geometry_interaction_active=lambda: False)
        impl._session_manual_targets = set()
        impl._attached = True
        impl._last_rect = rect
        impl._hud_hidden_by_follow = False
        impl.hide_until_attached = True
        impl._hud_hwnds = lambda: set()
        impl._sync_header_roi_demo = MagicMock()

        impl._refresh_header_roi_geometry()

        self.assertEqual(impl.top_window.moves, [(360, 64)])
        self.assertEqual(impl.top_window.resizes, [(760, TOP_DOCK_HEIGHT)])
        self.assertEqual(locator.header_roi_calls, 1)
        self.assertEqual(locator.bottom_roi_calls, 0)

    def test_tk_follow_same_rect_skips_geometry_recompute(self) -> None:
        rect = WindowRect(hwnd=321, left=100, top=50, right=1300, bottom=850)
        window = object.__new__(TokenHudWindow)
        window.locator = _FakeAnchorLocator({}, window_rect=rect)
        window._attached = True
        window._last_rect = rect
        window._move_target = None
        window._resize_target = None
        window._top_animation_active = lambda: False
        window._manual_input_active = lambda _now=None: False
        window._ui_interaction_active = lambda _now=None: False
        window._hud_hwnds = lambda: set()
        window._exit_tombstone = MagicMock()
        window._apply_focus_state = MagicMock()
        window._attach_to_rect = MagicMock()

        window.sync_codex_window()

        window._attach_to_rect.assert_not_called()

    def test_qt_follow_same_rect_skips_geometry_recompute(self) -> None:
        rect = WindowRect(hwnd=321, left=100, top=50, right=1300, bottom=850)
        impl = object.__new__(qt_hud_module._QtHudWindowImpl)
        impl.locator = _FakeAnchorLocator({}, window_rect=rect)
        impl._attached = True
        impl._last_rect = rect
        impl._geometry_interaction_active = lambda: False
        impl._hud_hwnds = lambda: set()
        impl._hud_hidden_by_follow = False
        impl.hide_until_attached = True
        impl.top_window = SimpleNamespace(isVisible=lambda: True, show=lambda: None)
        impl.request_window = SimpleNamespace(isVisible=lambda: True, show=lambda: None)
        impl.attach_to_rect = MagicMock()

        self.assertTrue(impl._follow_codex_window())

        impl.attach_to_rect.assert_not_called()

    def test_roi_demo_positions_tk_huds_with_existing_hud_height(self) -> None:
        class _RecordingOverlay:
            def __init__(self) -> None:
                self.rect: WindowRect | None = None

            def update(self, rect: WindowRect | None) -> None:
                self.rect = rect

            def hide(self, reason: str = "hidden") -> None:
                del reason
                self.rect = None

        window = object.__new__(TokenHudWindow)
        rect = WindowRect(left=100, top=50, right=1300, bottom=850)
        window.locator = _FakeAnchorLocator(
            {},
            header_roi=WindowRect(left=360, top=64, right=1120, bottom=120),
            bottom_roi=WindowRect(left=520, top=732, right=1000, bottom=786),
        )
        window.settings = HudSettings.empty()
        window._use_header_roi_demo = True
        window.top_expanded = False
        window.request_expanded = False
        window._top_collapsed_width_override = None
        window._last_geometry_clamp = {}
        header_overlay = _RecordingOverlay()
        bottom_overlay = _RecordingOverlay()
        window._header_roi_overlay = header_overlay
        window._bottom_roi_overlay = bottom_overlay

        top = window._attached_geometry("top", rect, False)
        top_expanded = window._attached_geometry("top", rect, True)
        request = window._attached_geometry("request", rect, False)
        top_roi = window._roi_demo_geometry("top", rect, TOP_DOCK_HEIGHT)
        request_roi = window._roi_demo_geometry("request", rect, REQUEST_DOCK_HEIGHT)
        window._sync_header_roi_demo(rect)
        window._sync_bottom_roi_demo(rect)

        self.assertEqual(top, (360, 64, 760, TOP_DOCK_HEIGHT))
        self.assertEqual(top_expanded, (360, 64, 760, TOP_DOCK_EXPANDED_HEIGHT))
        self.assertEqual(request, (520, 732, 480, REQUEST_DOCK_HEIGHT))
        self.assertEqual((top_roi.left, top_roi.top, top_roi.width, top_roi.height), (360, 64, 760, TOP_DOCK_HEIGHT))
        self.assertEqual((request_roi.left, request_roi.top, request_roi.width, request_roi.height), (520, 732, 480, REQUEST_DOCK_HEIGHT))
        self.assertEqual(header_overlay.rect, top_roi)
        self.assertEqual(bottom_overlay.rect, request_roi)

    def test_native_anchor_no_longer_controls_request_geometry(self) -> None:
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
            self.assertEqual(first, _visual_anchor_geometry("request", rect, False))
            self.assertEqual(stable, _visual_anchor_geometry("request", rect, False))
            self.assertEqual(translated, _visual_anchor_geometry("request", moved_rect, False))
        finally:
            window._close()

    def test_request_geometry_uses_auto_fallback_during_resize_gate(self) -> None:
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
            self.assertEqual(stable, _visual_anchor_geometry("request", rect, False))

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

            self.assertEqual(projected, _visual_anchor_geometry("request", resized_rect, False))
            self.assertNotEqual(projected, (683, 768, 700, 32))
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
            window.settings.top.pinned = True
            window.settings.top.height = 430

            _, _, _, height = window._attached_geometry("top", rect, True)

            self.assertEqual(height, 430)
        finally:
            window._close()

    def test_top_expanded_body_wraps_and_scrolls_long_content(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        window = TokenHudWindow(
            user_settings_store=UserConfigStore(Path(temp_dir.name) / "hud_settings.json")
        )
        try:
            window.toggle_top_expanded()
            _settle_top_animation(window)
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
                    "日额度已用 39.31/100 USD (39%)，超过 50% 阈值，这条额外说明应该被 HUD 摘要省略"
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
            snapshot.task_index = 1
            snapshot.task_count = 1
            snapshot.task_started_at = now - timedelta(minutes=12)
            snapshot.request.started_at = now - timedelta(minutes=1)
            snapshot.request_history = [
                RequestRound(
                    index=index,
                    status="completed",
                    model="gpt-5.5",
                    input_tokens=10_000 + index,
                    cached_tokens=8_000,
                    output_tokens=1_200,
                    reasoning_tokens=300,
                    total_tokens=11_500 + index,
                    estimated=False,
                    cost_usd=0.05 + index / 100,
                    started_at=now - timedelta(minutes=8 - index),
                    completed_at=now - timedelta(minutes=8 - index, seconds=-20),
                    activity_summary=f"轨迹节点 {index}",
                )
                for index in range(1, 7)
            ]
            snapshot.activity.kind = "agent"
            snapshot.activity.detail = (
                "我现在提交这次发布资产，提交说明会按仓库的 Lore 协议来写，"
                "这是一段足够长的当前活动内容，用来证明布局不会吞掉信息。"
            )
            snapshot.activity.timestamp = now
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
                and not getattr(widget, "_hud_progress_canvas", False)
                and not getattr(widget, "_hud_scrollbar", False)
                and not getattr(widget, "_hud_activity_trail_canvas", False)
                and not getattr(widget, "_hud_activity_marker_canvas", False)
            ]
            self.assertEqual(len(canvases), 1)
            canvas = canvases[0]
            scroll_region = canvas.bbox("all")

            self.assertIsNotNone(scroll_region)
            self.assertGreater(scroll_region[3], canvas.winfo_height())
            self.assertEqual(window.top_labels["topSessionCost"].cget("text"), "$1.62")
            self.assertEqual(window.top_labels["topSessionTokens"].cget("text"), "452k")
            self.assertIn("87%", window.top_labels["topSessionMix"].cget("text"))
            self.assertIn("缓存命中 87%", window.top_labels["cache_progress"].cget("text"))
            self.assertEqual(window.top_labels["topSessionInputTokens"].cget("text"), "430k")
            self.assertEqual(window.top_labels["topSessionCachedTokens"].cget("text"), "374k")
            for key in (
                "topSessionInputTokens",
                "topSessionCachedTokens",
                "topSessionOutputTokens",
                "topSessionReasoningTokens",
            ):
                label = window.top_labels[key]
                self.assertEqual(label.winfo_manager(), "grid")
                text_width = int(
                    window.root.tk.call(
                        "font",
                        "measure",
                        label.cget("font"),
                        label.cget("text"),
                    )
                )
                self.assertGreaterEqual(label.winfo_width(), text_width)
            self.assertIn("◎87%", window.request_label.cget("text"))
            self.assertEqual(
                window.top_labels["warnings"].cget("text"),
                "预警  日已用 39%，超过 50% 阈值",
            )
            self.assertEqual(
                window.top_labels["budget"].cget("bg"),
                window.top_labels["budget"].master.cget("bg"),
            )
            self.assertLessEqual(
                window.top_labels["topSessionMix"].winfo_rootx()
                + window.top_labels["topSessionMix"].winfo_width(),
                window.top_labels["topSessionAverage"].winfo_rootx(),
            )
            self.assertEqual(int(float(str(window.top_labels["topSessionAverage"].cget("width")))), 0)
            self.assertIn("/轮", window.top_labels["topSessionAverage"].cget("text"))
            for key in ("warnings",):
                label = window.top_labels[key]
                wraplength = int(float(str(label.cget("wraplength"))))
                self.assertGreaterEqual(wraplength, 96)
                self.assertLessEqual(wraplength, max(96, label.winfo_width()))
            self.assertEqual(int(float(str(window.top_labels["topCurrentTask"].cget("wraplength")))), 0)
            self.assertEqual(int(float(str(window.top_labels["topCurrentTask"].cget("height")))), 1)
            self.assertNotIn("\n", window.top_labels["topCurrentTask"].cget("text"))
            self.assertEqual(int(float(str(window.top_labels["topExecuting"].cget("wraplength")))), 0)
            self.assertEqual(int(float(str(window.top_labels["topExecuting"].cget("height")))), 1)
            self.assertNotIn("\n", window.top_labels["topExecuting"].cget("text"))
            self.assertLess(
                window.top_labels["warnings"].winfo_rooty(),
                window.top_labels["topCurrentTask"].winfo_rooty(),
            )
            self.assertGreaterEqual(len(window.top_labels["topHeavyRounds"].winfo_children()), 1)
            self.assertGreaterEqual(len(window.top_labels["topActivityTrail"].winfo_children()), 1)
            trail_viewport = window.top_labels["topActivityTrailViewport"]
            trail_canvas = window.top_labels["topActivityTrailCanvas"]
            self.assertGreaterEqual(trail_viewport.winfo_height(), TOP_ACTIVITY_TRAIL_VIEWPORT_HEIGHT)
            self.assertEqual(len(window.top_labels["topActivityTrail"].winfo_children()), 4)
            first_activity_row = window.top_labels["topActivityTrail"].winfo_children()[0]
            _time_label, marker, title_label, detail_label = getattr(first_activity_row, "_hud_activity_widgets")
            marker_items = marker.find_all()
            oval_items = [item for item in marker_items if marker.type(item) == "oval"]
            self.assertTrue(oval_items)
            x1, y1, x2, y2 = marker.coords(oval_items[0])
            self.assertGreater(x1, 0)
            self.assertGreater(y1, 0)
            self.assertLess(x2, marker.winfo_width())
            self.assertLess(y2, marker.winfo_height())
            outer_scroll_region_before_load_more = canvas.bbox("all")

            window._load_more_top_activity()
            window.root.update_idletasks()

            self.assertGreaterEqual(trail_viewport.winfo_height(), TOP_ACTIVITY_TRAIL_VIEWPORT_HEIGHT)
            self.assertEqual(canvas.bbox("all"), outer_scroll_region_before_load_more)
            self.assertGreater(len(window.top_labels["topActivityTrail"].winfo_children()), 4)
            self.assertGreater(trail_canvas.bbox("all")[3], trail_canvas.winfo_height())
            self.assertGreaterEqual(window._top_activity_visible_count, 8)
            self.assertTrue(str(title_label.bind("<MouseWheel>")))
            self.assertTrue(str(detail_label.bind("<MouseWheel>")))
            parent_scroll = MagicMock(return_value="parent-scroll")
            window._top_scroll_handler = parent_scroll
            trail_canvas.yview_moveto(0.0)
            self.assertEqual(
                window._top_activity_scroll_handler(SimpleNamespace(delta=120, num=None)),
                "parent-scroll",
            )
            trail_canvas.yview_moveto(1.0)
            self.assertEqual(
                window._top_activity_scroll_handler(SimpleNamespace(delta=-120, num=None)),
                "parent-scroll",
            )
            self.assertEqual(parent_scroll.call_count, 2)

            snapshot.activity.detail = "新的活动轨迹到达后保持已展开数量"
            snapshot.activity.timestamp = now + timedelta(seconds=1)
            snapshot.last_event_time = now + timedelta(seconds=1)
            window.update_display(snapshot)
            window.root.update_idletasks()
            self.assertGreaterEqual(window._top_activity_visible_count, 8)

            before = canvas.yview()
            canvas.yview_moveto(1.0)
            after = canvas.yview()
            self.assertGreater(after[0], before[0])
            trail_children = tuple(window.top_labels["topActivityTrail"].winfo_children())
            window.update_display(snapshot)
            window.root.update_idletasks()
            self.assertEqual(
                trail_children,
                tuple(window.top_labels["topActivityTrail"].winfo_children()),
            )
            self.assertGreaterEqual(canvas.yview()[0], after[0] - 0.02)
        finally:
            window._close()

    def test_top_session_composition_elides_narrow_token_values(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _settle_top_animation(window)
            window.root.geometry(f"320x{TOP_DOCK_EXPANDED_HEIGHT}+20+20")
            now = datetime(2026, 6, 21, 12, 0, 0).astimezone()
            snapshot = ParsedSession(
                session_id="narrow-token-layout",
                status="live",
                refreshed_at=now,
                last_event_time=now,
                token_events=4,
            )
            snapshot.confirmed.cumulative_input = 1_400_000
            snapshot.confirmed.cumulative_cached = 1_300_000
            snapshot.confirmed.cumulative_output = 9_303
            snapshot.confirmed.cumulative_reasoning = 3_742
            snapshot.confirmed.cumulative_total = 1_413_045

            window.update_display(snapshot)
            for _ in range(4):
                window.root.update_idletasks()

            self.assertEqual(window.top_labels["topSessionInputTokens"].cget("text"), "1.4M")
            self.assertEqual(window.top_labels["topSessionCachedTokens"].cget("text"), "1.3M")

            token_grid = window.top_labels["topSessionInputTokens"].master.master
            token_grid_right = token_grid.winfo_rootx() + token_grid.winfo_width()
            for key in (
                "topSessionInputTokens",
                "topSessionCachedTokens",
                "topSessionOutputTokens",
                "topSessionReasoningTokens",
            ):
                label = window.top_labels[key]
                token = label.master
                token_right = token.winfo_rootx() + token.winfo_width()
                self.assertLessEqual(token_right, token_grid_right)
                text_width = int(
                    window.root.tk.call(
                        "font",
                        "measure",
                        label.cget("font"),
                        label.cget("text"),
                    )
                )
                self.assertLessEqual(text_width, label.winfo_width())

            title = window.top_labels["topSessionInsightTitle"]
            mix = window.top_labels["topSessionMix"]
            average = window.top_labels["topSessionAverage"]
            title_right = title.winfo_rootx() + title.winfo_width()
            self.assertGreaterEqual(mix.winfo_rootx(), title_right)
            self.assertLessEqual(mix.winfo_rootx() - title_right, 14)
            self.assertLess(mix.winfo_rootx() + mix.winfo_width(), average.winfo_rootx())
        finally:
            window._close()

    def test_top_expanded_redesign_uses_live_theme_tokens(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _settle_top_animation(window)
            themed_export = CodexThemeExport.from_share_string(
                'codex-theme-v1:{"codeThemeId":"github","theme":{"accent":"#0969da",'
                '"contrast":40,"fonts":{"code":null,"ui":null},"ink":"#1f2328",'
                '"opaqueWindows":false,"semanticColors":{"diffAdded":"#1a7f37",'
                '"diffRemoved":"#cf222e","skill":"#8250df"},"surface":"#ffffff"},'
                '"variant":"light"}'
            )
            tokens = HudThemeTokens.from_theme(themed_export)
            window._theme_probe = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    source="persisted",
                    hud_tokens=tokens,
                )
            )
            snapshot = ParsedSession(
                session_id="theme-top-expanded",
                budget_warnings=["日额度已用 80.00/100 USD (80%)，超过 80% 阈值"],
            )

            window.update_display(snapshot)
            window.root.update_idletasks()

            self.assertEqual(tk_hud_module.HUD_WARNING.lower(), str(tokens.warning).lower())
            self.assertNotEqual(tk_hud_module.HUD_WARNING.lower(), "#ffb86b")
            warning_bg = tk_hud_module._theme_tint_surface(
                tk_hud_module.HUD_WARNING,
                tk_hud_module.HUD_PANEL_BG,
                0.13,
            )
            warning_chip_bg = tk_hud_module._theme_tint_surface(
                tk_hud_module.HUD_WARNING,
                tk_hud_module.HUD_PANEL_BG,
                0.14,
            )
            self.assertEqual(str(window.top_labels["warnings_panel"].cget("bg")).lower(), warning_bg)
            self.assertEqual(str(window.top_labels["topActivityState"].cget("bg")).lower(), warning_chip_bg)
            self.assertEqual(
                str(window.top_labels["topActivityState"].cget("highlightbackground")).lower(),
                tk_hud_module.HUD_WARNING.lower(),
            )
            self.assertEqual(
                str(window.top_labels["topSessionInsight"].cget("bg")).lower(),
                tk_hud_module.REQUEST_PANEL_BG.lower(),
            )
            self.assertEqual(
                str(window.top_labels["topActivityLoadMore"].cget("bg")).lower(),
                tk_hud_module._theme_tint_surface(
                    tk_hud_module.HUD_BLUE,
                    tk_hud_module.HUD_PANEL_BG,
                    0.08,
                ),
            )
            heavy_row = window.top_labels["topHeavyRounds"].winfo_children()[0]
            self.assertEqual(str(heavy_row.cget("bg")).lower(), tk_hud_module.REQUEST_PANEL_BG.lower())

            window._render_top_activity_trail(
                {
                    "activityTrailContext": "theme-token-colors",
                    "activityTrail": [
                        {
                            "time": "12:02",
                            "title": "正在执行",
                            "detail": "主题色节点",
                            "tooltip": "正在执行",
                            "active": True,
                        },
                        {
                            "time": "12:01",
                            "title": "准备",
                            "detail": "普通节点",
                            "tooltip": "准备",
                        },
                    ],
                }
            )
            window.root.update_idletasks()
            first_marker = getattr(window.top_labels["topActivityTrail"].winfo_children()[0], "_hud_activity_widgets")[1]
            marker_items = first_marker.find_all()
            line_items = [item for item in marker_items if first_marker.type(item) == "line"]
            oval_items = [item for item in marker_items if first_marker.type(item) == "oval"]
            self.assertTrue(line_items)
            self.assertTrue(oval_items)
            self.assertEqual(first_marker.itemcget(line_items[0], "fill").lower(), tk_hud_module.HUD_DIVIDER.lower())
            self.assertEqual(first_marker.itemcget(oval_items[-1], "fill").lower(), tk_hud_module.HUD_ACCENT.lower())
        finally:
            window._close()

    def test_top_expanded_columns_align_on_wide_layout(self) -> None:
        self.skipTest("Legacy Tk HUD layout is not a renderer-only release gate")
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _flush_tk(window)
            window.root.geometry(f"720x{TOP_DOCK_EXPANDED_HEIGHT}+20+20")
            window.update_display(ParsedSession(session_id="wide-layout"))
            window.root.update_idletasks()

            heavy_card = window.top_labels["topHeavyCard"]
            activity_card = window.top_labels["topActivityCard"]
            heavy_bottom = heavy_card.winfo_rooty() + heavy_card.winfo_height()
            activity_bottom = activity_card.winfo_rooty() + activity_card.winfo_height()
            self.assertLessEqual(abs(heavy_bottom - activity_bottom), 1)
        finally:
            window._close()

    def test_top_activity_trail_draws_connector_for_two_nodes(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _flush_tk(window)

            window._render_top_activity_trail(
                {
                    "activityTrailContext": "two-node-test",
                    "activityTrail": [
                        {
                            "time": "12:01",
                            "title": "第一步",
                            "detail": "准备",
                            "tooltip": "第一步",
                        },
                        {
                            "time": "12:02",
                            "title": "第二步",
                            "detail": "执行",
                            "tooltip": "第二步",
                        },
                    ],
                }
            )
            window.root.update_idletasks()

            rows = window.top_labels["topActivityTrail"].winfo_children()
            self.assertEqual(len(rows), 2)
            first_marker = getattr(rows[0], "_hud_activity_widgets")[1]
            second_marker = getattr(rows[1], "_hud_activity_widgets")[1]
            self.assertIn("line", [first_marker.type(item) for item in first_marker.find_all()])
            self.assertIn("line", [second_marker.type(item) for item in second_marker.find_all()])
        finally:
            window._close()

    def test_top_expanded_header_prefers_session_title_with_fallback(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _settle_top_animation(window)
            snapshot = ParsedSession(session_title="Ship the live session switch check")

            window.update_display(snapshot)
            self.assertEqual(
                window.top_labels["title"].cget("text"),
                "Ship the live session switch check",
            )
            self.assertFalse(window.top_labels["topTaskOrdinalActivity"].winfo_manager())

            snapshot.session_title = ""
            window.update_display(snapshot)
            self.assertEqual(
                window.top_labels["title"].cget("text"),
                "Codex 会话 / 预算",
            )
        finally:
            window._close()

    def test_top_expanded_header_does_not_show_close_button(self) -> None:
        window = TokenHudWindow()
        try:
            window.toggle_top_expanded()
            _flush_tk(window)

            button_texts = {
                str(widget.cget("text"))
                for widget in _walk_widgets(window.root)
                if isinstance(widget, tk.Button) and widget.winfo_ismapped()
            }

            self.assertNotIn("×", button_texts)
        finally:
            window._close()

    def test_auto_scroll_label_updates_text_without_crashing(self) -> None:
        root = TokenHudWindow()
        try:
            _stop_background_jobs(root)
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

    def test_collapsed_progress_strip_scroll_threshold_matches_renderer_behavior(self) -> None:
        widths = [166, 178, 195]
        self.assertFalse(
            _collapsed_progress_strip_should_scroll(
                widths,
                available_width=507,
                gap=7,
            )
        )
        self.assertTrue(
            _collapsed_progress_strip_should_scroll(
                widths,
                available_width=210,
                gap=7,
            )
        )

    def test_top_hud_progress_strip_enters_scroll_mode_only_when_tail_space_collapses(self) -> None:
        try:
            root = TokenHudWindow()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            _stop_background_jobs(root)
            host = tk.Frame(root.root, bg=HUD_BG)
            host.place(x=0, y=0, width=507, height=28)
            strip = TopHudProgressStrip(host)
            strip.pack(fill="both", expand=True)
            strip.set_metrics(
                [
                    TopHudProgressMetric(label="本会话 10.2M/$5.09/94%", ratio=0.25, fill=HUD_PROGRESS_DAY, fill_end=HUD_PROGRESS_DAY_END, fill_text=HUD_TEXT),
                    TopHudProgressMetric(label="今日 18.9M/$8.96", right_text="总 $100.00", ratio=0.35, fill=HUD_PROGRESS_DAY, fill_end=HUD_PROGRESS_DAY_END, fill_text=HUD_TEXT),
                    TopHudProgressMetric(label="本周 39.5M/$33.41", right_text="总 $400.00", ratio=0.42, fill=HUD_PROGRESS_DAY, fill_end=HUD_PROGRESS_DAY_END, fill_text=HUD_TEXT),
                ]
            )
            root.root.update_idletasks()
            root.root.update()
            self.assertFalse(strip._scrolling_enabled)

            host.place_configure(width=210)
            root.root.update_idletasks()
            root.root.update()
            self.assertTrue(strip._scrolling_enabled)
            self.assertLess(strip._scroll_min_x, 0.0)
        finally:
            root._close()

    def test_shimmer_text_label_updates_text_without_crashing(self) -> None:
        root = TokenHudWindow()
        try:
            _stop_background_jobs(root)
            label = ShimmerTextLabel(
                root.root,
                text="正在思考",
                fg="#8492A6",
                bg="#10161D",
            )
            label.pack(fill="x")
            label.set_text("正在思考...")
            root.root.update_idletasks()
            root.root.update()

            self.assertEqual(label.cget("text"), "正在思考...")
            self.assertTrue(label._char_ids)
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

    def test_click_priority_temporarily_defers_refresh_work(self) -> None:
        window = object.__new__(TokenHudWindow)
        window._tombstoned = False
        window._ui_interaction_hold_until = 0.0
        window._click_priority_hold_until = 0.0
        window._pointer_priority_hold_until = 0.0

        window._mark_click_priority()

        self.assertTrue(window._click_priority_active())
        self.assertFalse(window.should_refresh_snapshot())
        self.assertEqual(
            window.refresh_delay_ms(1000),
            tk_hud_module.HUD_CLICK_REFRESH_DELAY_MS,
        )

        window._click_priority_hold_until = time.monotonic() - 0.001
        self.assertTrue(window.should_refresh_snapshot())

    def test_pointer_priority_defers_refresh_below_click_priority(self) -> None:
        window = object.__new__(TokenHudWindow)
        window._tombstoned = False
        window._ui_interaction_hold_until = 0.0
        window._click_priority_hold_until = 0.0
        window._pointer_priority_hold_until = 0.0

        window._mark_pointer_priority()

        self.assertTrue(window._pointer_priority_active())
        self.assertFalse(window.should_refresh_snapshot())
        self.assertEqual(
            window.refresh_delay_ms(1000),
            tk_hud_module.HUD_POINTER_REFRESH_DELAY_MS,
        )

        window._mark_click_priority()
        self.assertEqual(
            window.refresh_delay_ms(1000),
            tk_hud_module.HUD_CLICK_REFRESH_DELAY_MS,
        )

        window._click_priority_hold_until = time.monotonic() - 0.001
        self.assertEqual(
            window.refresh_delay_ms(1000),
            tk_hud_module.HUD_POINTER_REFRESH_DELAY_MS,
        )

    def test_top_animation_temporarily_defers_refresh_work(self) -> None:
        window = object.__new__(TokenHudWindow)
        window._tombstoned = False
        window._ui_interaction_hold_until = 0.0
        window._click_priority_hold_until = 0.0
        window._pointer_priority_hold_until = 0.0
        window._top_animation_job = "animation"
        window._top_animation_start = None

        self.assertFalse(window.should_refresh_snapshot())
        self.assertEqual(
            window.refresh_delay_ms(100),
            tk_hud_module.TOP_HUD_ANIMATION_REFRESH_DELAY_MS,
        )

        window._top_animation_job = None
        self.assertTrue(window.should_refresh_snapshot())

    def test_click_rebuilds_are_scheduled_before_idle_work(self) -> None:
        window = object.__new__(TokenHudWindow)
        window.root = SimpleNamespace(after=MagicMock(return_value="job"))
        window._top_rebuild_job = None
        window._request_rebuild_job = None

        window._schedule_top_rebuild()
        window._schedule_request_rebuild()

        self.assertEqual(window.root.after.call_args_list[0].args[0], 0)
        self.assertEqual(
            window.root.after.call_args_list[0].args[1].__name__,
            "_flush_top_rebuild",
        )
        self.assertEqual(window.root.after.call_args_list[1].args[0], 0)
        self.assertEqual(
            window.root.after.call_args_list[1].args[1].__name__,
            "_flush_request_rebuild",
        )

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
            self.assertEqual(window.root.state(), "withdrawn")
            self.assertEqual(window.request_root.state(), "withdrawn")
            self.assertTrue(window._tombstoned)
            self.assertEqual(window._hidden_reason, "inactive")

            locator.active = True
            window._attach_to_rect(rect)

            self.assertTrue(window.should_refresh_snapshot())
            self.assertEqual(window._hidden_reason, "")
            self.assertFalse(window._tombstoned)
            self.assertTrue(bool(window.root.attributes("-topmost")))
            self.assertTrue(bool(window.request_root.attributes("-topmost")))
        finally:
            window._close()

    def test_hud_hwnds_include_settings_dialog(self) -> None:
        window = TokenHudWindow()
        try:
            _flush_tk(window)
            dialog = window._settings_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None

            self.assertIn(int(dialog.winfo_id()), window._hud_hwnds())
        finally:
            window._close()


class TkSnapshotPumpTests(unittest.TestCase):
    def test_snapshot_pump_is_single_flight_while_worker_is_busy(self) -> None:
        context = SimpleNamespace(reload_user_config=MagicMock())
        started = threading.Event()
        release = threading.Event()
        call_count = 0

        def slow_build(_context: object) -> ParsedSession:
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(1.0)
            return ParsedSession(status="parsed")

        with patch("codex_usage_hud.cli.build_snapshot", side_effect=slow_build):
            pump = _TkSnapshotPump(context)
            try:
                self.assertTrue(pump.request_refresh())
                self.assertTrue(started.wait(1.0))
                self.assertFalse(pump.request_refresh())
            finally:
                release.set()
                pump.close()

        self.assertEqual(call_count, 1)

    def test_snapshot_pump_returns_error_snapshot_when_worker_fails(self) -> None:
        context = SimpleNamespace(reload_user_config=MagicMock())

        with patch(
            "codex_usage_hud.cli.build_snapshot",
            side_effect=RuntimeError("boom"),
        ):
            pump = _TkSnapshotPump(context)
            try:
                self.assertTrue(pump.request_refresh())
                snapshot = None
                for _ in range(50):
                    snapshot = pump.take_latest()
                    if snapshot is not None:
                        break
                    threading.Event().wait(0.02)
            finally:
                pump.close()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.status, "error")
        self.assertIn("boom", snapshot.error)

    def test_snapshot_pump_discards_worker_result_after_close(self) -> None:
        context = SimpleNamespace(reload_user_config=MagicMock())
        started = threading.Event()
        release = threading.Event()

        def slow_build(_context: object) -> ParsedSession:
            started.set()
            release.wait(1.0)
            return ParsedSession(status="parsed")

        with patch("codex_usage_hud.cli.build_snapshot", side_effect=slow_build):
            pump = _TkSnapshotPump(context)
            self.assertTrue(pump.request_refresh())
            self.assertTrue(started.wait(1.0))
            pump.close()
            release.set()
            threading.Event().wait(0.05)

        self.assertIsNone(pump.take_latest())
        self.assertFalse(pump.request_refresh())


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
            exit_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {
                    "action": "save",
                    "settings": {
                        "daily_reset_time": "09:30",
                        "provider_scope_mode": "custom",
                        "notification_only_providers": ["notice"],
                    },
                },
                context,
                restart_requested,
                exit_requested,
            )
            saved = store.load()

        self.assertEqual(status["kind"], "")
        self.assertEqual(status["restartVisible"], False)
        self.assertEqual(saved.daily_reset_time, "09:30")
        self.assertEqual(saved.notification_only_providers, ["notice"])
        self.assertEqual(saved.daily_budget_usd, 12.34)
        self.assertEqual(saved.weekly_budget_usd, 56.78)
        self.assertIsNone(context.settings_mtime)
        self.assertEqual(reload_calls, 1)
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_apply_display_mode_normalizes_qt_to_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            store.save(UserConfig.defaults())
            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=MagicMock(),
            )
            restart_requested = MagicMock()
            exit_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {"action": "applyDisplayMode", "settings": {"display_mode": "qt"}},
                context,
                restart_requested,
                exit_requested,
            )
            saved = store.load()

        self.assertEqual(saved.display_mode, "renderer")
        self.assertNotIn("switchMode", status)
        self.assertFalse(status["restartVisible"])
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_apply_display_mode_normalizes_tk_to_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            store.save(UserConfig.defaults())
            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=MagicMock(),
            )
            restart_requested = MagicMock()
            exit_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {"action": "applyDisplayMode", "settings": {"display_mode": "tk"}},
                context,
                restart_requested,
                exit_requested,
            )
            saved = store.load()

        self.assertEqual(saved.display_mode, "renderer")
        self.assertNotIn("switchMode", status)
        self.assertFalse(status["restartVisible"])
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_install_desktop_overlay_starts_optional_dependency_install(self) -> None:
        context = SimpleNamespace()
        restart_requested = MagicMock()
        exit_requested = MagicMock()

        with (
            patch(
                "codex_usage_hud.cli._desktop_overlay_dependency_status",
                return_value={
                    "installed": False,
                    "canInstall": True,
                    "installing": False,
                    "requiresRestart": False,
                },
            ),
            patch("codex_usage_hud.cli._start_desktop_overlay_install", return_value=True) as install,
        ):
            status = _handle_renderer_settings_command(
                {"action": "installDesktopOverlay"},
                context,
                restart_requested,
                exit_requested,
            )

        self.assertEqual(status["kind"], "")
        self.assertIn("已开始安装气泡组件", status["message"])
        install.assert_called_once()
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_enable_desktop_overlay_rechecks_and_enables_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            config = UserConfig.defaults()
            config.work_overlay_max_items = 0
            store.save(config)
            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=MagicMock(),
            )
            restart_requested = MagicMock()
            exit_requested = MagicMock()
            overlay = SimpleNamespace(reset_runtime_availability=MagicMock(return_value=True))

            with (
                patch(
                    "codex_usage_hud.cli._desktop_overlay_dependency_status",
                    return_value={
                        "installed": True,
                        "version": "6.8.1",
                        "canInstall": True,
                        "installing": False,
                        "requiresRestart": False,
                    },
                ),
                patch("codex_usage_hud.cli._work_overlay_screen_max_items", return_value=4),
            ):
                status = _handle_renderer_settings_command(
                    {"action": "enableDesktopOverlay"},
                    context,
                    restart_requested,
                    exit_requested,
                    work_overlay=overlay,
                )

            saved = store.load()

        self.assertEqual(status["kind"], "")
        self.assertIn("会话进度气泡已启用", status["message"])
        self.assertEqual(saved.work_overlay_max_items, 4)
        overlay.reset_runtime_availability.assert_called_once()
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_exit_command_requests_full_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            store.save(UserConfig.defaults())
            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=MagicMock(),
            )
            restart_requested = MagicMock()
            exit_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {"action": "exit"},
                context,
                restart_requested,
                exit_requested,
            )

        self.assertEqual(status["kind"], "")
        self.assertIn("后台守护进程", status["message"])
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_called_once_with()

    def test_renderer_dismiss_warnings_command_persists_today(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            store.save(UserConfig.defaults())
            context = SimpleNamespace(
                settings_store=store,
                settings_mtime=store.mtime(),
                reload_user_config=MagicMock(),
            )
            restart_requested = MagicMock()
            exit_requested = MagicMock()

            status = _handle_renderer_settings_command(
                {"action": "dismissWarningsToday"},
                context,
                restart_requested,
                exit_requested,
            )
            dismissed = warning_dismissed_today(store.path)

        self.assertEqual(status["kind"], "")
        self.assertIn("今天不再显示", status["message"])
        self.assertTrue(dismissed)
        context.reload_user_config.assert_not_called()
        restart_requested.set.assert_not_called()
        exit_requested.set.assert_not_called()

    def test_renderer_payload_suppresses_dismissed_budget_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            dismiss_warning_for_today(path)
            snapshot = ParsedSession(
                session_id="dismissed-renderer-warning",
                budget_warnings=["日额度已用 52.00/100 USD (52%)，超过 50% 阈值"],
                today_cost_usd=52.0,
                daily_limit_usd=100.0,
            )

            payload = payload_from_snapshot(snapshot, settings_path=path)

        self.assertEqual(payload.top_details["warnings"], "")
        self.assertFalse(payload.warning)

    def test_renderer_check_update_uses_async_update_manager(self) -> None:
        context = SimpleNamespace(settings_store=None, settings_mtime=None, reload_user_config=None)
        restart_requested = MagicMock()
        exit_requested = MagicMock()
        update_manager = SimpleNamespace(
            request_check=MagicMock(
                return_value=SimpleNamespace(message="正在检查更新...", error="")
            )
        )

        status = _handle_renderer_settings_command(
            {"action": "checkUpdate"},
            context,
            restart_requested,
            exit_requested,
            update_manager,
        )

        update_manager.request_check.assert_called_once_with(auto_download=False)
        self.assertEqual(status["kind"], "")
        self.assertIn("正在检查更新", status["message"])

    def test_renderer_install_update_uses_async_update_manager(self) -> None:
        context = SimpleNamespace(settings_store=None, settings_mtime=None, reload_user_config=None)
        restart_requested = MagicMock()
        exit_requested = MagicMock()
        update_manager = SimpleNamespace(
            request_install=MagicMock(
                return_value=SimpleNamespace(message="正在下载安装更新...", title="", error="")
            )
        )

        status = _handle_renderer_settings_command(
            {"action": "installUpdate"},
            context,
            restart_requested,
            exit_requested,
            update_manager,
        )

        update_manager.request_install.assert_called_once_with()
        self.assertEqual(status["kind"], "")
        self.assertIn("正在下载安装更新", status["message"])

    def test_legacy_tk_hud_session_returns_renderer_only_unavailable(self) -> None:
        loading = SimpleNamespace(close=MagicMock())

        with patch("codex_usage_hud.cli.HudInstanceLock") as instance_lock:
            exit_code = run_tk_hud_session(
                SimpleNamespace(compact=False),
                loading_feedback=loading,
            )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        loading.close.assert_called_once_with()
        instance_lock.assert_not_called()

    def test_legacy_qt_hud_session_returns_renderer_only_unavailable(self) -> None:
        loading = SimpleNamespace(close=MagicMock())

        with patch("codex_usage_hud.cli.HudInstanceLock") as instance_lock:
            exit_code = run_qt_hud_session(
                SimpleNamespace(compact=False),
                loading_feedback=loading,
            )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        loading.close.assert_called_once_with()
        instance_lock.assert_not_called()

    def test_legacy_tk_window_session_stub_closes_context(self) -> None:
        fake_context = SimpleNamespace(close=MagicMock())

        exit_code = cli_module._run_tk_window_session(
            fake_context,
            SimpleNamespace(compact=False),
        )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        fake_context.close.assert_called_once_with()

    def test_legacy_qt_window_session_stub_closes_context(self) -> None:
        fake_context = SimpleNamespace(close=MagicMock())

        exit_code = cli_module._run_qt_window_session(
            fake_context,
            SimpleNamespace(compact=False),
        )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        fake_context.close.assert_called_once_with()
    def test_cli_import_does_not_eagerly_import_qt_hud(self) -> None:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(SRC_ROOT)
            if not existing_pythonpath
            else str(SRC_ROOT) + os.pathsep + existing_pythonpath
        )
        script = (
            "import sys\n"
            "import codex_usage_hud.cli\n"
            "names = [\n"
            "    'PySide6',\n"
            "    'PySide6.QtCore',\n"
            "    'codex_usage_hud.ui.qt_hud',\n"
            "    'codex_usage_hud.ui.tk_hud',\n"
            "    'codex_usage_hud.ui.work_overlay_qt',\n"
            "]\n"
            "print('\\n'.join(f'{name}={name in sys.modules}' for name in names))\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PySide6=False", result.stdout)
        self.assertIn("PySide6.QtCore=False", result.stdout)
        self.assertIn("codex_usage_hud.ui.qt_hud=False", result.stdout)
        self.assertIn("codex_usage_hud.ui.tk_hud=False", result.stdout)
        self.assertIn("codex_usage_hud.ui.work_overlay_qt=False", result.stdout)

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
        self.assertEqual(args.runtime_hud_mode, "renderer")

    def test_build_runtime_context_uses_renderer_bridge_instead_of_native_title_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            platform = SimpleNamespace(
                get_codex_data_dir=MagicMock(return_value=temp_root),
                suspend_native_active_title=MagicMock(),
            )
            settings_store = SimpleNamespace(
                load=MagicMock(return_value=UserConfig.defaults()),
                mtime=MagicMock(return_value=None),
            )
            tracker = SimpleNamespace(start=MagicMock(), close=MagicMock())
            args = SimpleNamespace(
                sessions_root=None,
                sse_db=None,
                state_db=None,
                active_session_poll_ms=250,
                no_follow_active_session=False,
                session_id=None,
                session_file=None,
                auto_switch_idle_seconds=30.0,
                no_sse=True,
                poll_ms=500,
                hud_mode="renderer",
                runtime_hud_mode="renderer",
            )

            with (
                patch("codex_usage_hud.cli.get_current_platform", return_value=platform),
                patch("codex_usage_hud.cli.UserConfigStore", return_value=settings_store),
                patch(
                    "codex_usage_hud.cli.ActiveSessionTracker",
                    return_value=tracker,
                ) as tracker_class,
            ):
                context = cli_module.build_runtime_context(args)

            try:
                platform.suspend_native_active_title.assert_called_once_with(True)
                tracker.start.assert_called_once()
                self.assertIs(context.active_session_tracker, tracker)
                self.assertFalse(
                    tracker_class.call_args.kwargs["start_background_watcher"]
                )
                events = []
                context.runtime_events.subscribe(events.append)
                context.runtime_errors.record(
                    source="active_session",
                    code="unmatched_thread",
                    message="Renderer thread could not be mapped",
                    context={"sessionId": "session-a"},
                )
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].type, "runtime_error")
                self.assertEqual(events[0].session, "session-a")
            finally:
                context.close()

    def test_legacy_active_session_diagnostics_flag_is_accepted_but_noop(self) -> None:
        default_args = cli_module.build_parser().parse_args([])
        diagnostic_args = cli_module.build_parser().parse_args(
            ["--legacy-active-session-diagnostics"]
        )

        self.assertFalse(default_args.legacy_active_session_diagnostics)
        self.assertTrue(diagnostic_args.legacy_active_session_diagnostics)

    def test_build_runtime_context_ignores_legacy_active_session_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            platform = SimpleNamespace(
                get_codex_data_dir=MagicMock(return_value=temp_root),
                suspend_native_active_title=MagicMock(),
            )
            settings_store = SimpleNamespace(
                load=MagicMock(return_value=UserConfig.defaults()),
                mtime=MagicMock(return_value=None),
            )
            tracker = SimpleNamespace(start=MagicMock(), close=MagicMock())
            args = SimpleNamespace(
                sessions_root=None,
                sse_db=None,
                state_db=None,
                active_session_poll_ms=250,
                no_follow_active_session=False,
                session_id=None,
                session_file=None,
                auto_switch_idle_seconds=30.0,
                no_sse=True,
                poll_ms=500,
                hud_mode="renderer",
                runtime_hud_mode="renderer",
                legacy_active_session_diagnostics=True,
            )

            with (
                patch("codex_usage_hud.cli.get_current_platform", return_value=platform),
                patch("codex_usage_hud.cli.UserConfigStore", return_value=settings_store),
                patch(
                    "codex_usage_hud.cli.ActiveSessionTracker",
                    return_value=tracker,
                ) as tracker_class,
            ):
                context = cli_module.build_runtime_context(args)

            try:
                platform.suspend_native_active_title.assert_called_once_with(True)
                tracker.start.assert_called_once()
                self.assertFalse(
                    tracker_class.call_args.kwargs["start_background_watcher"]
                )
            finally:
                context.close()

    def test_build_runtime_context_disables_pre_send_estimator_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            platform = SimpleNamespace(
                get_codex_data_dir=MagicMock(return_value=temp_root),
                suspend_native_active_title=MagicMock(),
            )
            settings_store = SimpleNamespace(
                load=MagicMock(return_value=UserConfig.defaults()),
                mtime=MagicMock(return_value=None),
            )
            tracker = SimpleNamespace(start=MagicMock(), close=MagicMock())
            args = SimpleNamespace(
                sessions_root=None,
                sse_db=None,
                state_db=None,
                active_session_poll_ms=250,
                no_follow_active_session=False,
                session_id=None,
                session_file=None,
                auto_switch_idle_seconds=30.0,
                no_sse=True,
                poll_ms=500,
                hud_mode="renderer",
                runtime_hud_mode="renderer",
            )

            with (
                patch("codex_usage_hud.cli.get_current_platform", return_value=platform),
                patch("codex_usage_hud.cli.UserConfigStore", return_value=settings_store),
                patch(
                    "codex_usage_hud.cli.ActiveSessionTracker",
                    return_value=tracker,
                ),
            ):
                context = cli_module.build_runtime_context(args)

            try:
                self.assertIsNone(context.pre_send_estimator)
            finally:
                context.close()

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
        self.assertEqual(args.runtime_hud_mode, "renderer")

    def test_qt_config_normalizes_to_renderer_path(self) -> None:
        config = UserConfig.defaults()
        config.display_mode = "qt"

        with (
            patch("codex_usage_hud.cli.UserConfigStore") as store_class,
            patch("codex_usage_hud.cli.run_hud_session", return_value=0) as run_session,
        ):
            store_class.return_value.load.return_value = config
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        args = run_session.call_args.args[0]
        self.assertTrue(args.renderer_hud)
        self.assertEqual(args.runtime_hud_mode, "renderer")
        self.assertIsNone(args.standalone_hud_mode)

    def test_tk_config_normalizes_to_renderer_path(self) -> None:
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
        self.assertTrue(args.renderer_hud)
        self.assertEqual(args.runtime_hud_mode, "renderer")
        self.assertIsNone(args.standalone_hud_mode)

    def test_renderer_unavailable_does_not_fallback_to_qt_or_tk(self) -> None:
        args = SimpleNamespace(renderer_hud=True)

        with (
            patch(
                "codex_usage_hud.cli.run_renderer_hud_session",
                return_value=RENDERER_HUD_UNAVAILABLE,
            ) as renderer_session,
            patch("codex_usage_hud.cli.run_qt_hud_session", return_value=0) as qt_session,
            patch("codex_usage_hud.cli.run_tk_hud_session", return_value=0) as tk_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        renderer_session.assert_called_once()
        qt_session.assert_not_called()
        tk_session.assert_not_called()

    def test_qt_fallback_path_is_not_used_when_renderer_is_unavailable(self) -> None:
        args = SimpleNamespace(renderer_hud=True)

        with (
            patch(
                "codex_usage_hud.cli.run_renderer_hud_session",
                return_value=RENDERER_HUD_UNAVAILABLE,
            ),
            patch("codex_usage_hud.cli.run_qt_hud_session", return_value=0) as qt_session,
            patch("codex_usage_hud.cli.run_tk_hud_session", return_value=0) as tk_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        qt_session.assert_not_called()
        tk_session.assert_not_called()

    def test_run_hud_session_returns_unknown_renderer_exit_without_fallback(self) -> None:
        args = SimpleNamespace(renderer_hud=True)
        renderer_exit = 99

        with (
            patch(
                "codex_usage_hud.cli.run_renderer_hud_session",
                return_value=renderer_exit,
            ) as renderer_session,
            patch("codex_usage_hud.cli.run_tk_hud_session", return_value=0) as tk_session,
            patch("codex_usage_hud.cli.run_qt_hud_session", return_value=0) as qt_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, renderer_exit)
        renderer_session.assert_called_once()
        tk_session.assert_not_called()
        qt_session.assert_not_called()

    def test_tk_close_destroys_windows_without_forced_gc(self) -> None:
        window = object.__new__(tk_hud_module.TokenHudWindow)
        window._exit_reason = ""
        window._top_rebuild_job = None
        window._top_core_prewarm_job = None
        window._top_deferred_render_job = None
        window._top_animation_job = None
        window._request_rebuild_job = None
        window._settings_build_job = None
        window._settings_prewarm_job = None
        window._settings_loading_anim_job = None
        window._settings_update_poll_job = None
        window._follow_job = None
        window.request_root = MagicMock()
        window.root = MagicMock()
        window._release_tk_image_references = MagicMock()
        window._clear_tk_widget_references = MagicMock()

        tk_hud_module.TokenHudWindow.close(window, "display_mode_switch")

        window.request_root.destroy.assert_called_once_with()
        window.root.destroy.assert_called_once_with()
        window._clear_tk_widget_references.assert_called_once_with()
        self.assertEqual(window.exit_reason, "display_mode_switch")

    def test_legacy_tk_window_session_stub_does_not_touch_runtime(self) -> None:
        fake_context = SimpleNamespace(close=MagicMock())
        fake_window = SimpleNamespace(close=MagicMock())

        exit_code = cli_module._run_tk_window_session(
            fake_context,
            SimpleNamespace(compact=False),
            existing_window=fake_window,
            close_context=False,
        )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        fake_window.close.assert_not_called()
        fake_context.close.assert_not_called()
    def test_run_hud_session_with_legacy_tk_preference_runs_renderer_only(self) -> None:
        args = SimpleNamespace(renderer_hud=False)

        with (
            patch(
                "codex_usage_hud.cli.run_tk_hud_session",
                return_value=0,
            ) as tk_session,
            patch("codex_usage_hud.cli.run_qt_hud_session", return_value=0) as qt_session,
            patch("codex_usage_hud.cli.run_renderer_hud_session", return_value=0) as renderer_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, 0)
        renderer_session.assert_called_once()
        tk_session.assert_not_called()
        qt_session.assert_not_called()

    def test_run_hud_session_does_not_use_tk_switch_to_renderer(self) -> None:
        args = SimpleNamespace(renderer_hud=False)

        with (
            patch(
                "codex_usage_hud.cli.run_tk_hud_session",
                return_value=HUD_SWITCH_TO_RENDERER,
            ) as tk_session,
            patch("codex_usage_hud.cli.run_renderer_hud_session", return_value=0) as renderer_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, 0)
        renderer_session.assert_called_once()
        renderer_args = renderer_session.call_args.args[0]
        self.assertTrue(renderer_args.renderer_hud)
        self.assertEqual(renderer_args.runtime_hud_mode, "renderer")
        tk_session.assert_not_called()

    def test_run_hud_session_does_not_restart_from_legacy_tk_switch(self) -> None:
        args = SimpleNamespace(renderer_hud=False)

        with (
            patch(
                "codex_usage_hud.cli.run_tk_hud_session",
                return_value=HUD_SWITCH_TO_RENDERER_RESTART_CODEX,
            ) as tk_session,
            patch("codex_usage_hud.cli._restart_codex_for_renderer", return_value=True) as restart_codex,
            patch("codex_usage_hud.cli.run_renderer_hud_session", return_value=0) as renderer_session,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, 0)
        renderer_session.assert_called_once()
        tk_session.assert_not_called()
        restart_codex.assert_not_called()
        self.assertFalse(renderer_session.call_args.kwargs["launched_codex"])

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
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=False),
                patch(
                    "codex_usage_hud.cli._assign_fresh_renderer_cdp_port",
                    side_effect=RuntimeError("no available CDP port"),
                ),
                patch("codex_usage_hud.cli._loading_feedback_enabled", return_value=False),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(no_startup_prompt=True),
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

    def test_renderer_initial_connect_timeout_uses_restart_card_not_modal(self) -> None:
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
            restart_card = MagicMock()
            restart_card.start.return_value = restart_card
            restart_card.offer_codex_restart.return_value = False

            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=False),
                patch(
                    "codex_usage_hud.cli._assign_fresh_renderer_cdp_port",
                    side_effect=RuntimeError("no available CDP port"),
                ),
                patch(
                    "codex_usage_hud.cli._create_loading_feedback",
                    return_value=restart_card,
                ) as create_loading,
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(no_startup_prompt=True),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        create_loading.assert_called_once()
        restart_card.offer_codex_restart.assert_called_once()
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_existing_codex_daemon_attach_uses_one_short_startup_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_context = SimpleNamespace(
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="failed",
                last_error="TimeoutError: timed out",
                close=MagicMock(),
                timeout_seconds=0.35,
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"
            daemon = SimpleNamespace()
            loading = MagicMock()
            loading.offer_codex_restart.return_value = False
            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch(
                    "codex_usage_hud.cli.RendererHudClient",
                    return_value=fake_client,
                ) as client_class,
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli._codex_processes_running", return_value=True),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli._wait_for_visible_codex_window") as wait_window,
                patch("codex_usage_hud.cli.wait_for_renderer") as wait_renderer,
                patch(
                    "codex_usage_hud.cli._assign_fresh_renderer_cdp_port",
                    side_effect=RuntimeError("no available CDP port"),
                ),
                patch("codex_usage_hud.cli._loading_feedback_enabled", return_value=False),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(no_startup_prompt=True),
                    lock_already_held=True,
                    daemon_manager=daemon,
                    loading_feedback=loading,
                )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        self.assertEqual(
            client_class.call_args.kwargs["timeout_seconds"],
            cli_module.RENDERER_CDP_TIMEOUT_SECONDS,
        )
        wait_renderer.assert_not_called()
        wait_window.assert_not_called()

    def test_renderer_cdp_port_selection_reuses_persisted_port_for_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "renderer_cdp_state.json").write_text(
                '{"lastSuccessfulPort":9444}\n',
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                port = cli_module._select_initial_renderer_cdp_port()
                env_port = os.environ.get(cli_module.CDP_PORT_ENV)

        self.assertEqual(port, 9444)
        self.assertEqual(env_port, "9444")

    def test_renderer_cdp_port_selection_honors_explicit_fixed_port(self) -> None:
        with patch.dict(os.environ, {cli_module.CDP_PORT_ENV: "9444"}, clear=True):
            port = cli_module._select_initial_renderer_cdp_port()
            env_port = os.environ.get(cli_module.CDP_PORT_ENV)

        self.assertEqual(port, 9444)
        self.assertEqual(env_port, "9444")

    def test_renderer_initial_connect_waits_for_start_card_restart_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            active_tracker = SimpleNamespace(close=MagicMock())
            fake_context = SimpleNamespace(
                poll_ms=500,
                platform=SimpleNamespace(refresh_cdp_probe=MagicMock()),
                active_session_tracker=active_tracker,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    active_session_tracker=active_tracker,
                    resolve=MagicMock(return_value=(None, "activity")),
                ),
                close=MagicMock(),
            )
            failed_client = SimpleNamespace(
                last_status="failed",
                last_error="URLError: <urlopen error timed out>",
                close=MagicMock(),
                timeout_seconds=1.0,
                port=9229,
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"
            fake_work_overlay = MagicMock()
            restart_card = MagicMock()
            restart_card.start.return_value = restart_card
            restart_card.offer_codex_restart.return_value = True
            restart_card.wait_for_codex_restart_request.return_value = True

            def assign_fresh_port() -> int:
                os.environ[cli_module.CDP_PORT_ENV] = "9444"
                return 9444

            with (
                patch.dict(os.environ, {cli_module.CDP_PORT_ENV: "9229"}),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch(
                    "codex_usage_hud.cli.RendererHudClient",
                    return_value=failed_client,
                ) as client_class,
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch(
                    "codex_usage_hud.cli.DesktopWorkOverlay",
                    return_value=fake_work_overlay,
                ),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch(
                    "codex_usage_hud.cli.wait_for_renderer",
                    side_effect=[False, True],
                ) as wait_renderer,
                patch(
                    "codex_usage_hud.cli._assign_fresh_renderer_cdp_port",
                    side_effect=assign_fresh_port,
                ) as assign_port,
                patch(
                    "codex_usage_hud.cli._create_loading_feedback",
                    return_value=restart_card,
                ) as create_loading,
                patch("codex_usage_hud.cli._restart_codex_for_renderer") as restart_codex,
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, HUD_SWITCH_TO_RENDERER_RESTART_CODEX)
        self.assertEqual(client_class.call_count, 1)
        self.assertEqual(wait_renderer.call_count, 1)
        assign_port.assert_called_once_with()
        create_loading.assert_called_once()
        restart_card.offer_codex_restart.assert_called_once()
        restart_card.wait_for_codex_restart_request.assert_called_once_with()
        restart_card.close.assert_called_once_with()
        restart_codex.assert_not_called()
        failed_client.close.assert_called()
        active_tracker.close.assert_not_called()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_run_hud_session_restarts_codex_and_retries_renderer(self) -> None:
        args = SimpleNamespace(renderer_hud=True)

        with (
            patch(
                "codex_usage_hud.cli.run_renderer_hud_session",
                side_effect=[HUD_SWITCH_TO_RENDERER_RESTART_CODEX, 0],
            ) as renderer_session,
            patch("codex_usage_hud.cli._restart_codex_for_renderer", return_value=True) as restart_codex,
        ):
            exit_code = run_hud_session(args)

        self.assertEqual(exit_code, 0)
        restart_codex.assert_called_once_with()
        self.assertEqual(renderer_session.call_count, 2)
        self.assertFalse(renderer_session.call_args_list[0].kwargs["launched_codex"])
        self.assertTrue(renderer_session.call_args_list[1].kwargs["launched_codex"])

    def test_daemon_passes_its_own_codex_launch_to_renderer_attach(self) -> None:
        manager = SimpleNamespace(
            wait_for_codex=MagicMock(),
            poll_seconds=0.1,
        )
        loading = MagicMock()
        loading.start.return_value = loading
        args = SimpleNamespace(daemon_poll_ms=500, no_startup_prompt=True)

        with (
            patch("codex_usage_hud.cli.CodexDaemonManager", return_value=manager),
            patch("codex_usage_hud.cli.HudInstanceLock"),
            patch(
                "codex_usage_hud.cli._daemon_startup_decision",
                return_value=cli_module.DaemonStartupDecision(
                    DAEMON_STARTUP_RENDERER,
                    launch_codex=True,
                ),
            ),
            patch("codex_usage_hud.cli._create_loading_feedback", return_value=loading),
            patch("codex_usage_hud.cli._select_initial_renderer_cdp_port"),
            patch("codex_usage_hud.cli.launch_codex_app", return_value=True),
            patch("codex_usage_hud.cli.run_renderer_hud_session", return_value=0) as run_renderer,
        ):
            self.assertEqual(run_daemon(args), 0)

        run_renderer.assert_called_once()
        self.assertTrue(run_renderer.call_args.kwargs["launched_codex"])

    def test_daemon_shows_progress_immediately_for_existing_codex(self) -> None:
        manager = SimpleNamespace(
            wait_for_codex=MagicMock(),
            poll_seconds=0.1,
        )
        loading = MagicMock()
        loading.start.return_value = loading
        args = SimpleNamespace(daemon_poll_ms=500, no_startup_prompt=True)

        with (
            patch("codex_usage_hud.cli.CodexDaemonManager", return_value=manager),
            patch("codex_usage_hud.cli.HudInstanceLock"),
            patch(
                "codex_usage_hud.cli._daemon_startup_decision",
                return_value=cli_module.DaemonStartupDecision(
                    cli_module.DAEMON_STARTUP_WAIT,
                ),
            ),
            patch(
                "codex_usage_hud.cli._create_loading_feedback",
                return_value=loading,
            ) as create_loading,
            patch("codex_usage_hud.cli.run_hud_session", return_value=0) as run_hud,
        ):
            self.assertEqual(run_daemon(args), 0)

        create_loading.assert_called_once_with(
            args,
            title="正在启动 Renderer HUD",
            message="正在检查 Codex 的 CDP 连接…",
        )
        loading.start.assert_called_once_with()
        self.assertIs(run_hud.call_args.kwargs["loading_feedback"], loading)

    def test_daemon_startup_launches_missing_codex_directly_with_renderer(self) -> None:
        manager = SimpleNamespace(snapshot=MagicMock(return_value=SimpleNamespace(found=False)))

        decision = cli_module._daemon_startup_decision(
            SimpleNamespace(no_startup_prompt=True),
            manager,
        )

        self.assertEqual(decision.mode, DAEMON_STARTUP_RENDERER)
        self.assertTrue(decision.launch_codex)

    def test_daemon_startup_attaches_to_existing_codex_without_relaunch(self) -> None:
        manager = SimpleNamespace(snapshot=MagicMock(return_value=SimpleNamespace(found=True)))

        decision = cli_module._daemon_startup_decision(SimpleNamespace(), manager)

        self.assertEqual(decision.mode, cli_module.DAEMON_STARTUP_WAIT)
        self.assertFalse(decision.launch_codex)

    def test_legacy_auto_mode_uses_renderer_failure_limit(self) -> None:
        self.assertEqual(
            _renderer_update_failure_limit(
                "auto",
                "URLError: <urlopen error timed out>",
            ),
            RENDERER_UPDATE_FAILURE_LIMIT,
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

    def test_renderer_file_watch_specs_cover_session_settings_and_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
            )
            session_path = root / "sessions" / "2026" / "06" / "session.jsonl"

            specs = cli_module._renderer_file_watch_specs(context, session_path)

        reasons_by_name = {(spec.path.name, spec.kind): spec.reason for spec in specs}
        self.assertEqual(reasons_by_name[("hud_settings.json", "file")], "settings")
        self.assertEqual(reasons_by_name[("session_index.jsonl", "file")], "session-map")
        self.assertEqual(reasons_by_name[("state_5.sqlite", "file")], "session-map")
        self.assertEqual(reasons_by_name[("session.jsonl", "file")], "session")
        self.assertIn(("sessions", "tree"), reasons_by_name)
        self.assertIn(("archived_sessions", "tree"), reasons_by_name)

    def test_renderer_file_watch_specs_skip_recursive_session_trees_on_macos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=sessions_root,
            )

            with patch.object(cli_module.sys, "platform", "darwin"):
                specs = cli_module._renderer_file_watch_specs(context, session_path)

        self.assertFalse(any(spec.kind == "tree" for spec in specs))
        self.assertTrue(
            any(spec.kind == "file" and spec.reason == "session" for spec in specs)
        )
        self.assertTrue(
            any(spec.kind == "file" and spec.reason == "session-map" for spec in specs)
        )

    def test_renderer_file_event_source_coalesces_reasons_and_updates_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0,
                )
                watcher = created[0]
                watcher.callback({"session-map", "settings"}, {root / "state_5.sqlite"})
                session_path = root / "sessions" / "session.jsonl"
                source.update_session_path(session_path)
                specs = list(watcher.specs)
                source.close()

        self.assertTrue(wake_event.is_set())
        self.assertEqual(source.take_reasons(), {"session-map", "settings"})
        self.assertTrue(any(spec.path == session_path for spec in specs))

    def test_renderer_file_event_source_debounces_native_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0.05,
                )
                watcher = created[0]
                watcher.callback({"sessions-root"}, {root / "sessions" / "one.jsonl"})
                watcher.callback({"settings"}, {root / "hud_settings.json"})
                self.assertFalse(wake_event.is_set())
                self.assertTrue(wake_event.wait(0.5))
                reasons, paths = source.take_changes()
                source.close()

        self.assertEqual(reasons, {"sessions-root", "settings"})
        self.assertEqual(
            paths,
            {root / "sessions" / "one.jsonl", root / "hud_settings.json"},
        )

    def test_renderer_file_event_source_wakes_immediately_for_current_session_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=10.0,
                )
                watcher = created[0]
                session_path = root / "sessions" / "one.jsonl"
                watcher.callback({"session"}, {session_path})
                reasons, paths = source.take_changes()
                source.close()

        self.assertTrue(wake_event.is_set())
        self.assertEqual(reasons, {"session"})
        self.assertEqual(paths, {session_path})

    def test_renderer_file_event_source_wakes_immediately_for_session_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=10.0,
                )
                watcher = created[0]
                state_db_path = root / "state_5.sqlite"
                watcher.callback(
                    {"session-map", "settings"},
                    {state_db_path, root / "hud_settings.json"},
                )
                reasons, paths = source.take_changes()
                source.close()

        self.assertTrue(wake_event.is_set())
        self.assertEqual(reasons, {"session-map", "settings"})
        self.assertEqual(paths, {state_db_path, root / "hud_settings.json"})

    def test_renderer_file_event_source_publishes_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            runtime_events = RuntimeEventBus()
            events = []
            runtime_events.subscribe(events.append)
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
                runtime_events=runtime_events,
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0,
                )
                watcher = created[0]
                session_path = root / "sessions" / "one.jsonl"
                settings_path = root / "hud_settings.json"
                watcher.callback({"session", "settings"}, {session_path, settings_path})
                reasons, paths = source.take_changes()
                source.close()

        self.assertTrue(wake_event.is_set())
        self.assertEqual(reasons, {"session", "settings"})
        self.assertEqual(paths, {session_path, settings_path})
        self.assertEqual(
            sorted(event.type for event in events),
            ["session_file_changed", "settings_changed"],
        )
        for event in events:
            self.assertEqual(event.source, "file_watcher")
            self.assertEqual(
                sorted(event.context["reasons"]),
                ["session", "settings"],
            )

    def test_renderer_file_event_source_records_degraded_polling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            registry = RuntimeErrorRegistry(clock=lambda: 50.0)
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
                runtime_errors=registry,
            )

            class FakeWatcher:
                event_driven = False

                def __init__(self, callback, **kwargs):
                    del callback, kwargs

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with (
                patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root),
            ):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0,
                )
                source.update_session_path(root / "sessions" / "session.jsonl")
                source.close()
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["code"], "file_watcher.degraded")
        self.assertEqual(payload[0]["severity"], "warning")
        self.assertIn("sessions-root", payload[0]["context"]["reasons"])
        self.assertEqual(payload[0]["context"]["mode"], "polling")
        self.assertEqual(payload[0]["context"]["cause"], "native_unavailable")
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("file_watcher.degraded", diagnostic)
        self.assertIn("Renderer file watcher is using polling fallback.", diagnostic)
        record = _last_renderer_diagnostic_record(diagnostic)
        self.assertEqual(record["stage"], "runtime_error_recorded")
        self.assertEqual(record["source"], "file_watcher")
        self.assertEqual(record["severity"], "warning")
        self.assertEqual(record["code"], "file_watcher.degraded")
        self.assertEqual(
            record["message"],
            "Renderer file watcher is using polling fallback.",
        )
        self.assertEqual(record["context"]["mode"], "polling")
        self.assertEqual(record["context"]["cause"], "native_unavailable")
        self.assertIn("firstSeenAt", record)
        self.assertIn("lastSeenAt", record)

    def test_renderer_file_event_source_resolves_degraded_when_event_driven_recovers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            registry = RuntimeErrorRegistry(clock=lambda: 70.0)
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
                runtime_errors=registry,
            )

            class FakeWatcher:
                event_driven = False
                polling_cause = "native_unavailable"

                def __init__(self, callback, **kwargs):
                    del callback, kwargs

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with (
                patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root),
            ):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0,
                )
                first_session_path = root / "sessions" / "one.jsonl"
                second_session_path = root / "sessions" / "two.jsonl"
                source.update_session_path(first_session_path)
                self.assertEqual(len(registry.to_payload()), 1)
                source._watcher.event_driven = True
                source._watcher.polling_cause = ""
                source.update_session_path(second_session_path)
                source.close()
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        self.assertEqual(registry.to_payload(), [])
        self.assertIn("runtime_error_resolved", diagnostic)
        self.assertIn("file_watcher.degraded", diagnostic)
        record = json.loads(diagnostic.strip().splitlines()[-1])
        self.assertEqual(record["stage"], "runtime_error_resolved")
        self.assertEqual(record["source"], "file_watcher")
        self.assertEqual(record["severity"], "warning")
        self.assertEqual(record["code"], "file_watcher.degraded")
        self.assertEqual(
            record["message"],
            "Renderer file watcher is using polling fallback.",
        )
        self.assertEqual(record["context"]["cause"], "native_unavailable")
        self.assertEqual(record["firstSeenAt"], 70.0)
        self.assertEqual(record["lastSeenAt"], 70.0)

    def test_renderer_file_event_source_records_overflow_without_polluting_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wake_event = threading.Event()
            registry = RuntimeErrorRegistry(clock=lambda: 60.0)
            context = SimpleNamespace(
                settings_store=SimpleNamespace(path=root / "hud_settings.json"),
                session_index_path=root / "session_index.jsonl",
                state_db_path=root / "state_5.sqlite",
                sessions_root=root / "sessions",
                runtime_errors=registry,
            )
            created: list[object] = []

            class FakeWatcher:
                event_driven = True

                def __init__(self, callback, **kwargs):
                    del kwargs
                    self.callback = callback
                    self.specs = []
                    created.append(self)

                def update(self, specs):
                    self.specs = list(specs)

                def close(self):
                    return None

            with (
                patch("codex_usage_hud.cli.FileChangeWatcher", FakeWatcher),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=root),
            ):
                source = cli_module._RendererFileEventSource(
                    context,
                    wake_event,
                    debounce_seconds=0,
                )
                watcher = created[0]
                session_path = root / "sessions" / "one.jsonl"
                watcher.callback(
                    {"sessions-root", "file_watcher.overflow"},
                    {session_path},
                )
                reasons, paths = source.take_changes()
                source.close()
                diagnostic = (root / "renderer_fallback.log").read_text(
                    encoding="utf-8"
                )

        self.assertTrue(wake_event.is_set())
        self.assertEqual(reasons, {"sessions-root"})
        self.assertEqual(paths, {session_path})
        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["code"], "file_watcher.overflow")
        self.assertEqual(payload[0]["severity"], "warning")
        self.assertEqual(payload[0]["context"]["reasons"], ["sessions-root"])
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("file_watcher.overflow", diagnostic)
        self.assertIn("Windows file watcher overflowed", diagnostic)

    def test_renderer_event_idle_wait_uses_native_watcher_while_running(self) -> None:
        snapshot = ParsedSession(status="parsed")
        snapshot.request.status = "running"
        file_events = SimpleNamespace(event_driven=True)

        self.assertTrue(
            cli_module._renderer_event_idle_wait_enabled(
                file_events,
                snapshot,
                {"phase": "idle"},
                0.5,
                force_fast=False,
            )
        )
        self.assertFalse(
            cli_module._renderer_event_idle_wait_enabled(
                file_events,
                snapshot,
                {"phase": "idle"},
                0.5,
                force_fast=True,
            )
        )

    def test_renderer_active_session_click_prefers_parent_identity_row(self) -> None:
        renderer_source = Path(payload_from_snapshot.__code__.co_filename).read_text(
            encoding="utf-8"
        )

        self.assertIn("activeSessionIdentitySelector", renderer_source)
        self.assertIn("activeSessionBindingName", renderer_source)
        self.assertIn("binding(JSON.stringify(payload));", renderer_source)
        self.assertIn(
            "const identityRow = event.target?.closest?.(activeSessionIdentitySelector);",
            renderer_source,
        )
        self.assertIn("const row = identityRow || event.target?.closest?.", renderer_source)
        self.assertIn("cleanActiveSessionTitle", renderer_source)

    def test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text('{"type":"session"}\n', encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            settings_store = SimpleNamespace(
                path=settings_path,
                mtime=MagicMock(return_value=settings_path.stat().st_mtime),
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=settings_store,
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "pinned:file")),
                ),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_bridge = MagicMock()
            fake_bridge.start.return_value = "http://127.0.0.1:8765"
            fake_work_overlay = MagicMock()
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            with (
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=[0.0, KeyboardInterrupt],
                ),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        build_snapshot.assert_called_once_with(
            fake_context,
            refresh_budget_aggregate=True,
            refresh_budget_paths=(),
        )
        fake_client.update.assert_called_once()
        self.assertEqual(fake_work_overlay.update.call_count, 1)
        fake_work_overlay.keep_alive.assert_called_once()

    def test_run_renderer_hud_session_does_not_create_startup_loading_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=KeyboardInterrupt,
                ),
                patch("codex_usage_hud.cli._create_loading_feedback") as create_loading,
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        create_loading.assert_not_called()
        fake_client.update.assert_called_once()

    def test_renderer_loop_does_not_use_runtime_signature_as_refresh_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=RuntimeEventBus(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch(
                    "codex_usage_hud.cli._renderer_runtime_signature",
                    side_effect=[("initial",), ("changed-without-event",)],
                ) as runtime_signature,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=[0.0, KeyboardInterrupt],
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        runtime_signature.assert_not_called()
        build_snapshot.assert_called_once()
        fake_client.update.assert_called_once()
        fake_work_overlay.keep_alive.assert_called_once()

    def test_renderer_loop_bootstraps_active_session_before_first_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=RuntimeEventBus(),
                close=MagicMock(),
            )
            call_order: list[str] = []
            startup_payloads: list[dict[str, object]] = []

            def update_startup(payload: dict[str, object]) -> bool:
                startup = payload.get("payloadDomains", {}).get("startup", {})
                call_order.append(f"startup:{startup.get('progress')}")
                return True

            def bootstrap_active_session(*, startup_payload: dict[str, object]) -> bool:
                call_order.append("bootstrap")
                startup_payloads.append(startup_payload)
                return True

            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                set_active_session_callback=MagicMock(),
                set_settings_command_callback=MagicMock(),
                set_attachments_callback=MagicMock(),
                set_layout_callback=MagicMock(),
                bootstrap_active_session=MagicMock(side_effect=bootstrap_active_session),
                update=MagicMock(return_value=True),
                show_startup=MagicMock(side_effect=update_startup),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            loading_feedback = MagicMock()
            loading_feedback.close.side_effect = lambda: call_order.append("native_close")

            def build_snapshot_order(*args: object, **kwargs: object) -> ParsedSession:
                del args, kwargs
                call_order.append("snapshot")
                return snapshot

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", side_effect=build_snapshot_order),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=[0.0, KeyboardInterrupt],
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                    loading_feedback=loading_feedback,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(
            call_order[:4],
            ["startup:18", "native_close", "bootstrap", "startup:35"],
        )
        self.assertIn("snapshot", call_order)
        loading_feedback.close.assert_called_once_with()
        self.assertEqual(
            startup_payloads[0]["payloadDomains"]["startup"]["step"],
            "第 1 步，共 4 步",
        )
        self.assertEqual(
            fake_client.show_startup.call_args.args[0]["payloadDomains"]["startup"]["step"],
            "第 2 步，共 4 步",
        )
        fake_client.set_settings_command_callback.assert_called_once()

    def test_renderer_loop_registers_audit_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=RuntimeEventBus(),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                set_active_session_callback=MagicMock(),
                set_settings_command_callback=MagicMock(),
                set_attachments_callback=MagicMock(),
                set_layout_callback=MagicMock(),
                bootstrap_active_session=MagicMock(return_value=True),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file, session_id="session-a")

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch("codex_usage_hud.cli.hud_runtime_dir", return_value=temp_root),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=[0.0, KeyboardInterrupt],
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        # audit callback wiring was removed; CLI must not depend on it.
        self.assertFalse(hasattr(fake_client, "set_audit_callback"))

    def test_renderer_loop_publishes_update_state_event_before_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            emitted_events = []
            runtime_events.subscribe(emitted_events.append)
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            idle_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            downloading_state = SimpleNamespace(to_dict=lambda: {"phase": "downloading"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(side_effect=[idle_state, downloading_state]),
                status=MagicMock(return_value=downloading_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=[0.0, KeyboardInterrupt],
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"settings"})
        self.assertEqual(partial_payload["updateState"]["phase"], "downloading")
        self.assertNotIn("topLine", partial_payload)
        update_events = [
            event for event in emitted_events if event.type == "update_state_changed"
        ]
        self.assertEqual(len(update_events), 1)
        self.assertEqual(update_events[0].source, "update_manager")
        self.assertEqual(update_events[0].context["previous"]["phase"], "idle")
        self.assertEqual(update_events[0].context["current"]["phase"], "downloading")

    def test_renderer_loop_handles_bridge_settings_command_without_cdp_poll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            runtime_events = RuntimeEventBus()
            events = []
            runtime_events.subscribe(events.append)
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                command_callback = kwargs["command_callback"]

                def start() -> str:
                    command_callback({"action": "exit", "id": "bridge-command"})
                    return "http://127.0.0.1:8765"

                fake_bridge.start = MagicMock(side_effect=start)
                return fake_bridge

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 0)
        fake_client.take_settings_command.assert_not_called()
        fake_command_pump.start.assert_called_once()
        fake_command_pump.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_update_manager.close.assert_called_once()
        fake_context.close.assert_called_once()
        self.assertEqual([event.type for event in events], ["settings_command_received"])
        self.assertEqual(events[0].source, "settings_bridge")
        self.assertEqual(events[0].context["action"], "exit")

    def test_renderer_loop_handles_check_update_command_with_settings_only_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                request_check=MagicMock(
                    return_value=SimpleNamespace(message="正在检查更新...", error="")
                ),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback({"action": "checkUpdate", "id": "bridge-command"})
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch("codex_usage_hud.cli._work_overlay_screen_max_items", return_value=4),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"settings"})
        self.assertEqual(
            partial_payload["settingsCommandStatus"]["message"],
            "正在检查更新...",
        )
        self.assertNotIn("topLine", partial_payload)
        fake_update_manager.request_check.assert_called_once_with(auto_download=False)

    def test_renderer_loop_handles_enable_desktop_overlay_with_settings_and_overlay_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            store = UserConfigStore(settings_path)
            config = UserConfig.defaults()
            config.work_overlay_max_items = 0
            store.save(config)
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=store,
                settings_mtime=store.mtime(),
                user_config=store.load(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )

            def reload_user_config() -> None:
                fake_context.user_config = store.load()
                fake_context.settings_mtime = store.mtime()

            fake_context.reload_user_config = reload_user_config
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback({"action": "enableDesktopOverlay", "id": "bridge-command"})
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch(
                    "codex_usage_hud.cli._desktop_overlay_dependency_status",
                    return_value={
                        "installed": True,
                        "version": "6.8.1",
                        "canInstall": True,
                        "installing": False,
                        "requiresRestart": False,
                    },
                ),
                patch("codex_usage_hud.cli._work_overlay_screen_max_items", return_value=4),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"settings", "overlay"})
        self.assertEqual(partial_payload["settings"]["work_overlay_max_items"], 4)
        self.assertTrue(partial_payload["desktopOverlayDependency"]["installed"])
        self.assertNotIn("topLine", partial_payload)
        fake_work_overlay.reset_runtime_availability.assert_called_once()

    def test_renderer_loop_handles_dismiss_warnings_with_current_session_and_settings_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                settings_mtime=None,
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_context.reload_user_config = MagicMock()
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(
                status="parsed",
                session_path=session_file,
                budget_warnings=["日额度已用 52.00/100 USD (52%)，超过 50% 阈值"],
                today_cost_usd=52.0,
                daily_limit_usd=100.0,
            )
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback({"action": "dismissWarningsToday", "id": "bridge-command"})
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch("codex_usage_hud.cli._work_overlay_screen_max_items", return_value=4),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"currentSession", "settings"})
        self.assertEqual(partial_payload["topDetails"]["warnings"], "")
        self.assertFalse(partial_payload["warning"])
        self.assertEqual(
            partial_payload["settingsCommandStatus"]["message"],
            "今天不再显示预算预警。",
        )

    def test_renderer_loop_handles_overlay_only_save_with_settings_and_overlay_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            store = UserConfigStore(settings_path)
            initial_config = UserConfig.defaults()
            initial_config.work_overlay_max_items = 1
            store.save(initial_config)
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=store,
                settings_mtime=store.mtime(),
                user_config=store.load(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )

            def reload_user_config() -> None:
                fake_context.user_config = store.load()
                fake_context.settings_mtime = store.mtime()

            fake_context.reload_user_config = reload_user_config
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback(
                    {
                        "action": "save",
                        "id": "bridge-command",
                        "settings": {
                            **fake_context.user_config.to_dict(),
                            "work_overlay_max_items": 4,
                        },
                    }
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"settings", "overlay"})
        self.assertEqual(partial_payload["settings"]["work_overlay_max_items"], 4)
        self.assertIn("workOverlaySelectableMax", partial_payload)
        self.assertNotIn("topLine", partial_payload)

    def test_renderer_loop_handles_apply_display_mode_with_settings_only_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            store = UserConfigStore(settings_path)
            store.save(UserConfig.defaults())
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=store,
                settings_mtime=store.mtime(),
                user_config=store.load(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )

            def reload_user_config() -> None:
                fake_context.user_config = store.load()
                fake_context.settings_mtime = store.mtime()

            fake_context.reload_user_config = reload_user_config
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback(
                    {
                        "action": "applyDisplayMode",
                        "id": "bridge-command",
                        "settings": {
                            **fake_context.user_config.to_dict(),
                            "display_mode": "qt",
                        },
                    }
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"settings"})
        self.assertEqual(partial_payload["settings"]["display_mode"], "renderer")
        self.assertEqual(
            partial_payload["settingsCommandStatus"]["message"],
            "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。",
        )
        self.assertNotIn("topLine", partial_payload)

    def test_renderer_loop_handles_fetch_prices_with_current_session_and_settings_payload(
        self,
    ) -> None:
        from codex_usage_hud.config import ModelPrice
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            store = UserConfigStore(settings_path)
            initial_config = UserConfig.defaults()
            initial_config.pricing_url = "https://example.com/prices.json"
            initial_config.model_prices = {}
            store.save(initial_config)
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=store,
                settings_mtime=store.mtime(),
                user_config=store.load(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                parser=SimpleNamespace(
                    cost_estimator=SimpleNamespace(
                        calculate=MagicMock(return_value=None),
                    )
                ),
                close=MagicMock(),
            )

            def reload_user_config() -> None:
                fake_context.user_config = store.load()
                fake_context.settings_mtime = store.mtime()
                prices = fake_context.user_config.price_table()

                def calculate(
                    model: str,
                    input_tokens: int,
                    cached_tokens: int,
                    output_tokens: int,
                    reasoning_tokens: int,
                ) -> float | None:
                    del output_tokens, reasoning_tokens
                    model_prices = prices.get(model)
                    if not model_prices:
                        return None
                    input_price = float(model_prices.get("input") or 0.0)
                    cached_price = float(model_prices.get("cached_input") or 0.0)
                    uncached_tokens = max(0, int(input_tokens) - int(cached_tokens))
                    return (
                        uncached_tokens * input_price + int(cached_tokens) * cached_price
                    ) / 1_000_000

                fake_context.parser.cost_estimator = SimpleNamespace(calculate=calculate)

            fake_context.reload_user_config = reload_user_config
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            snapshot.request.model = "gpt-5.5"
            snapshot.estimate_base = BaseEstimate(session_history_tokens=10000, padding_tokens=50)
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback(
                    {
                        "action": "fetchPrices",
                        "id": "bridge-command",
                        "settings": {
                            **fake_context.user_config.to_dict(),
                            "pricing_url": "https://example.com/prices.json",
                        },
                    }
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch(
                    "codex_usage_hud.cli.fetch_model_prices",
                    return_value={
                        "gpt-5.5": ModelPrice(
                            input=5.0,
                            cached_input=0.5,
                            output=15.0,
                            reasoning=15.0,
                        )
                    },
                ),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"currentSession", "settings"})
        self.assertFalse(partial_payload["preSendHasPrices"])
        self.assertEqual(partial_payload["preSendInputPrice"], 0.0)
        self.assertIn("gpt-5.5", partial_payload["settings"]["model_prices"])
        self.assertNotIn("topProgress", partial_payload)

    def test_renderer_loop_handles_budget_only_save_with_current_session_budget_and_settings_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            store = UserConfigStore(settings_path)
            initial_config = UserConfig.defaults()
            initial_config.daily_budget_usd = 100.0
            initial_config.weekly_adjustment_usd = 0.0
            store.save(initial_config)
            runtime_events = RuntimeEventBus()
            callbacks: dict[str, object] = {}

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=store,
                settings_mtime=store.mtime(),
                user_config=store.load(),
                daily_budget_usd=100.0,
                weekly_budget_usd=400.0,
                budget_thresholds=list(fake_context.user_config.budget_thresholds) if False else [0.5, 0.8, 0.9, 1.0],
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )

            def reload_user_config() -> None:
                fake_context.user_config = store.load()
                fake_context.settings_mtime = store.mtime()
                fake_context.daily_budget_usd = fake_context.user_config.daily_budget_usd
                fake_context.weekly_budget_usd = fake_context.user_config.weekly_budget_usd
                fake_context.budget_thresholds = list(fake_context.user_config.budget_thresholds)

            fake_context.reload_user_config = reload_user_config
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            snapshot.today_tokens = 12000
            snapshot.today_cost_usd = 60.0
            snapshot.week_tokens = 40000
            snapshot.week_cost_usd = 180.0
            snapshot.week_adjustment_usd = 0.0
            snapshot.daily_limit_usd = 100.0
            snapshot.weekly_limit_usd = 400.0
            snapshot.budget_warnings = ["日额度已用 60.00/100 USD (60%)，超过 50% 阈值"]
            delay_calls = 0

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["command"] = kwargs["command_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            def delay_then_enqueue_command(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                callback = callbacks["command"]
                assert callable(callback)
                callback(
                    {
                        "action": "save",
                        "id": "bridge-command",
                        "settings": {
                            **fake_context.user_config.to_dict(),
                            "daily_budget_usd": 200.0,
                            "weekly_adjustment_usd": 20.0,
                        },
                    }
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_enqueue_command,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(
            set(partial_payload["payloadDomains"]),
            {"currentSession", "budget", "settings"},
        )
        self.assertIn("200", partial_payload["topProgress"]["budget"][0]["rightText"])
        self.assertIn("200", partial_payload["topLine"])
        self.assertEqual(partial_payload["settings"]["daily_budget_usd"], 200.0)
        self.assertEqual(partial_payload["settings"]["weekly_adjustment_usd"], 20.0)

    def test_renderer_loop_handles_safe_settings_file_change_with_partial_budget_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            runtime_events = RuntimeEventBus()

            old_config = UserConfig.defaults()
            new_config = UserConfig.defaults()
            new_config.daily_budget_usd = 200.0
            new_config.weekly_adjustment_usd = 20.0

            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=2.0),
                    load=MagicMock(return_value=new_config),
                ),
                settings_mtime=1.0,
                user_config=old_config,
                daily_budget_usd=old_config.daily_budget_usd,
                weekly_budget_usd=old_config.weekly_budget_usd,
                weekly_adjustment_usd=old_config.weekly_adjustment_usd,
                budget_thresholds=list(old_config.budget_thresholds),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            snapshot.today_tokens = 12000
            snapshot.today_cost_usd = 60.0
            snapshot.week_tokens = 40000
            snapshot.week_cost_usd = 180.0
            snapshot.week_adjustment_usd = 0.0
            snapshot.daily_limit_usd = 100.0
            snapshot.weekly_limit_usd = 400.0
            snapshot.budget_warnings = ["日额度已用 60.00/100 USD (60%)，超过 50% 阈值"]
            delay_calls = 0

            def delay_then_settings_event(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                runtime_events.publish(
                    "settings_changed",
                    source="file_watcher",
                    context={"reasons": ["settings"]},
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_settings_event,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(
            set(partial_payload["payloadDomains"]),
            {"currentSession", "budget", "settings"},
        )
        self.assertIn("200", partial_payload["topProgress"]["budget"][0]["rightText"])
        self.assertEqual(partial_payload["settings"]["daily_budget_usd"], 200.0)
        self.assertEqual(fake_context.daily_budget_usd, 200.0)
        self.assertEqual(fake_context.weekly_adjustment_usd, 20.0)

    def test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=KeyboardInterrupt,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        fake_client.take_settings_command.assert_not_called()
        fake_client.update.assert_called_once()

    def test_renderer_loop_handles_active_session_bridge_event_without_cdp_poll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            active_tracker = SimpleNamespace(
                observe_conversation_ref=MagicMock(return_value=True)
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                active_session_tracker=active_tracker,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                active_session_callback = kwargs["active_session_callback"]

                def start() -> str:
                    active_session_callback(
                        {
                            "sessionId": "thread-1",
                            "title": "Live Thread",
                            "reason": "click",
                            "matchedBy": "session-id",
                            "observedAt": int(time.time() * 1000) - 120,
                        }
                    )
                    return "http://127.0.0.1:8765"

                fake_bridge.start = MagicMock(side_effect=start)
                return fake_bridge

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=KeyboardInterrupt,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        active_tracker.observe_conversation_ref.assert_called_once_with(
            session_id="thread-1",
            title="Live Thread",
            source="renderer",
        )
        fake_client.take_settings_command.assert_not_called()
        fake_client.update.assert_called_once()
        fake_command_pump.start.assert_called_once()
        fake_command_pump.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_update_manager.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_renderer_loop_forwards_renderer_new_session_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            active_tracker = SimpleNamespace(
                observe_conversation_ref=MagicMock(return_value=True)
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(None, "renderer-new-session")),
                ),
                active_session_tracker=active_tracker,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            snapshot = ParsedSession(status="waiting", selection_source="renderer-new-session")

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                active_session_callback = kwargs["active_session_callback"]

                def start() -> str:
                    active_session_callback({"newSession": True, "reason": "new-session"})
                    return "http://127.0.0.1:8765"

                fake_bridge.start = MagicMock(side_effect=start)
                return fake_bridge

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot),
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=KeyboardInterrupt,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        active_tracker.observe_conversation_ref.assert_called_once_with(
            session_id="",
            title="",
            source="renderer",
            new_session=True,
        )

    def test_renderer_loop_refreshes_pending_session_on_exact_mapping_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            state_db_path = temp_root / "state_5.sqlite"
            active_tracker = SimpleNamespace(
                set_change_callback=MagicMock(),
                invalidate_mapping_cache=MagicMock(),
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                state_db_path=state_db_path,
                active_session_tracker=active_tracker,
                session_resolver=SimpleNamespace(
                    active_session_tracker=active_tracker,
                    resolve=MagicMock(return_value=(None, "renderer-pending-map")),
                ),
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(
                start=MagicMock(return_value="http://127.0.0.1:8765"),
                close=MagicMock(),
            )
            pending_snapshot = ParsedSession(
                status="waiting",
                selection_source="renderer-pending-map",
            )
            resolved_path = temp_root / "resolved.jsonl"
            resolved_path.write_text("{}\n", encoding="utf-8")
            resolved_snapshot = ParsedSession(
                status="parsed",
                session_path=resolved_path,
                selection_source="renderer:Exact Thread",
            )

            class FakeFileEvents:
                event_driven = True

                def __init__(self, *args, **kwargs):
                    del args, kwargs
                    self._changes = [
                        (set(), set()),
                        ({"session-map"}, {state_db_path}),
                    ]

                def take_changes(self):
                    return self._changes.pop(0) if self._changes else (set(), set())

                def update_session_path(self, _path):
                    return None

                def close(self):
                    return None

            delay_calls = 0

            def delay_after_mapping(*args, **kwargs):
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls >= 2:
                    raise KeyboardInterrupt
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch("codex_usage_hud.cli._WorkOverlayCommandPump", return_value=fake_command_pump),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch(
                    "codex_usage_hud.cli._RendererFileEventSource",
                    FakeFileEvents,
                ),
                patch(
                    "codex_usage_hud.cli.build_snapshot",
                    side_effect=[pending_snapshot, resolved_snapshot],
                ) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_after_mapping,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 2)
        active_tracker.invalidate_mapping_cache.assert_called_once_with()
        self.assertEqual(fake_client.update.call_count, 2)

    def test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            emitted_events = []
            runtime_events.subscribe(emitted_events.append)
            active_tracker = SimpleNamespace(
                observe_conversation_ref=MagicMock(return_value=True)
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                active_session_tracker=active_tracker,
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            callbacks: dict[str, object] = {}
            snapshot = ParsedSession(status="parsed", session_path=session_file)

            def bridge_factory(*args: object, **kwargs: object) -> object:
                del args
                callbacks["active"] = kwargs["active_session_callback"]
                fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
                return fake_bridge

            delay_calls = 0

            def delay_then_wakeup(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 2:
                    raise KeyboardInterrupt
                if delay_calls > 1:
                    return 0.0
                callback = callbacks["active"]
                callback({"sessionId": "thread-2", "title": "Other Thread"})
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", side_effect=bridge_factory),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_wakeup,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 2)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"sessionSwitch"})
        self.assertNotIn("topDetails", partial_payload)
        self.assertNotIn("requestRows", partial_payload)
        self.assertNotEqual(
            build_snapshot.call_args_list[1].kwargs.get("refresh_active_work_items"),
            False,
        )
        active_tracker.observe_conversation_ref.assert_called_once_with(
            session_id="thread-2",
            title="Other Thread",
            source="renderer",
        )
        self.assertEqual(
            [event.type for event in emitted_events],
            ["active_session_changed"],
        )
        self.assertEqual(emitted_events[0].context, {"reason": "renderer_bridge"})

    def test_renderer_loop_wakes_for_tracker_active_session_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            emitted_events = []
            runtime_events.subscribe(emitted_events.append)
            callbacks: dict[str, object] = {}
            callback_values: list[object] = []

            def set_change_callback(callback: object) -> None:
                callback_values.append(callback)
                callbacks["tracker"] = callback

            active_tracker = SimpleNamespace(
                set_change_callback=MagicMock(side_effect=set_change_callback)
            )
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "cdp:Live Thread")),
                ),
                active_session_tracker=active_tracker,
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def delay_then_tracker_wakeup(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 2:
                    raise KeyboardInterrupt
                if delay_calls > 1:
                    return 0.0
                callback = callbacks["tracker"]
                assert callable(callback)
                callback()
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_tracker_wakeup,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 2)
        self.assertNotEqual(
            build_snapshot.call_args_list[1].kwargs.get("refresh_active_work_items"),
            False,
        )
        self.assertIsNone(callback_values[-1])
        self.assertEqual(
            [event.type for event in emitted_events],
            ["active_session_changed"],
        )
        self.assertEqual(emitted_events[0].source, "active_session")

    def test_renderer_loop_wakes_for_runtime_error_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                update_payload=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def delay_then_runtime_error(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                runtime_events.publish(
                    "runtime_error",
                    source="cdp",
                    session="session-a",
                    context={"action": "recorded"},
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_runtime_error,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 1)
        self.assertEqual(fake_client.update.call_count, 1)
        self.assertEqual(fake_client.update_payload.call_count, 1)
        partial_payload = fake_client.update_payload.call_args.args[0]
        self.assertEqual(set(partial_payload["payloadDomains"]), {"diagnostics"})
        self.assertIn("runtimeErrors", partial_payload)
        self.assertNotIn("topLine", partial_payload)

    def test_renderer_loop_wakes_for_settings_runtime_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def delay_then_settings_event(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                runtime_events.publish(
                    "settings_changed",
                    source="file_watcher",
                    context={"reasons": ["settings"]},
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_settings_event,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        self.assertEqual(build_snapshot.call_count, 2)
        self.assertEqual(fake_client.update.call_count, 2)

    def test_renderer_loop_handles_layout_event_without_snapshot_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            session_file = temp_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            settings_path = temp_root / "hud_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            runtime_events = RuntimeEventBus()
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(
                    path=settings_path,
                    mtime=MagicMock(return_value=settings_path.stat().st_mtime),
                ),
                user_config=UserConfig.defaults(),
                session_resolver=SimpleNamespace(
                    resolve=MagicMock(return_value=(session_file, "renderer:Live Thread")),
                ),
                runtime_events=runtime_events,
                close=MagicMock(),
            )
            fake_client = SimpleNamespace(
                last_status="ok",
                last_error="",
                timeout_seconds=1.0,
                take_settings_command=MagicMock(return_value=None),
                update=MagicMock(return_value=True),
                close=MagicMock(),
            )
            fake_work_overlay = MagicMock()
            fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})
            fake_update_manager = SimpleNamespace(
                tick=MagicMock(return_value=fake_update_state),
                status=MagicMock(return_value=fake_update_state),
                close=MagicMock(),
            )
            fake_command_pump = SimpleNamespace(start=MagicMock(), close=MagicMock())
            fake_bridge = SimpleNamespace(close=MagicMock())
            fake_bridge.start = MagicMock(return_value="http://127.0.0.1:8765")
            snapshot = ParsedSession(status="parsed", session_path=session_file)
            delay_calls = 0

            def delay_then_layout_event(*args: object, **kwargs: object) -> float:
                nonlocal delay_calls
                del args, kwargs
                delay_calls += 1
                if delay_calls > 1:
                    raise KeyboardInterrupt
                runtime_events.publish(
                    "renderer_layout_changed",
                    source="renderer_layout",
                    context={"reason": "move", "panel": "top"},
                )
                return 0.0

            with (
                patch("codex_usage_hud.cli._select_initial_renderer_cdp_port", return_value=9229),
                patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
                patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
                patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
                patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_work_overlay),
                patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
                patch(
                    "codex_usage_hud.cli._WorkOverlayCommandPump",
                    return_value=fake_command_pump,
                ),
                patch("codex_usage_hud.cli._build_session_switch_controller"),
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=snapshot) as build_snapshot,
                patch("codex_usage_hud.cli._desktop_overlay_dependency_status", return_value={}),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=delay_then_layout_event,
                ),
            ):
                exit_code = run_renderer_hud_session(
                    SimpleNamespace(),
                    lock_already_held=True,
                )

        self.assertEqual(exit_code, 130)
        build_snapshot.assert_called_once()
        fake_client.update.assert_called_once()
        fake_work_overlay.keep_alive.assert_called_once()

    def test_renderer_runtime_failures_retry_without_tk_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_context = SimpleNamespace(
                poll_ms=500,
                settings_store=SimpleNamespace(path=temp_root / "hud_settings.json"),
                user_config=UserConfig.defaults(),
                reload_user_config=MagicMock(),
                runtime_errors=RuntimeErrorRegistry(clock=lambda: 300.0),
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
                patch(
                    "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                    return_value=(True, "visible", "", 123),
                ),
                patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
                patch("codex_usage_hud.cli.build_snapshot", return_value=ParsedSession(status="parsed")),
                patch("codex_usage_hud.cli._renderer_update_failure_limit", return_value=1),
                patch(
                    "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                    side_effect=KeyboardInterrupt,
                ),
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
        self.assertIn("runtime_error_recorded", diagnostic)
        self.assertIn("cdp.update_failed", diagnostic)
        self.assertNotIn("runtime_update_failed_retrying", diagnostic)
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

    def test_prepare_codex_window_focuses_visible_but_inactive_window(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=321,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=321,
                    ),
                ]
            ),
            is_active=MagicMock(side_effect=[False, True]),
            activate_main_window=MagicMock(return_value=321),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=True),
            patch("codex_usage_hud.cli._activate_running_codex_app", return_value=True) as activate_app,
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _prepare_codex_window_for_renderer(
                timeout_seconds=0.0,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 321)
        activate_app.assert_called_once()
        tracker.activate_main_window.assert_not_called()

    def test_prepare_codex_window_does_not_restart_hidden_tray_process(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        status="not_found",
                        reason="Codex HWND not found",
                        hwnd=0,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=654,
                    ),
                ]
            ),
            is_active=MagicMock(return_value=True),
            activate_main_window=MagicMock(side_effect=[0, 654]),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=True),
            patch("codex_usage_hud.cli._activate_running_codex_app", return_value=False) as activate_app,
            patch("codex_usage_hud.cli._restart_codex_for_renderer", return_value=True) as restart_codex,
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _prepare_codex_window_for_renderer(
                timeout_seconds=1.0,
                poll_seconds=0.0,
                launch_if_missing=True,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 654)
        activate_app.assert_called_once()
        restart_codex.assert_not_called()

    def test_prepare_codex_window_reactivates_existing_tray_instance_for_tk(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        status="not_found",
                        reason="Codex HWND not found",
                        hwnd=0,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=777,
                    ),
                ]
            ),
            is_active=MagicMock(return_value=True),
            activate_main_window=MagicMock(return_value=0),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=True),
            patch("codex_usage_hud.cli._activate_running_codex_app", return_value=True) as activate_app,
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _prepare_codex_window_for_tk(
                timeout_seconds=1.0,
                poll_seconds=0.0,
                launch_if_missing=True,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 777)
        activate_app.assert_called_once()

    def test_prepare_codex_window_waits_until_window_is_foreground_after_activation(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=777,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=777,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=777,
                    ),
                ]
            ),
            is_active=MagicMock(side_effect=[False, False, True]),
            activate_main_window=MagicMock(return_value=777),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=False),
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _prepare_codex_window_for_tk(
                timeout_seconds=1.0,
                poll_seconds=0.0,
                launch_if_missing=False,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 777)
        tracker.activate_main_window.assert_called_once()
        self.assertEqual(tracker.is_active.call_count, 3)

    def test_prepare_codex_window_legacy_tk_alias_launches_renderer(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                side_effect=[
                    SimpleNamespace(
                        status="not_found",
                        reason="Codex HWND not found",
                        hwnd=0,
                    ),
                    SimpleNamespace(
                        status="visible",
                        reason="",
                        hwnd=777,
                    ),
                ]
            ),
            is_active=MagicMock(return_value=True),
            activate_main_window=MagicMock(return_value=0),
        )

        with (
            patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=False),
            patch("codex_usage_hud.cli.launch_codex_app", return_value=True) as launch_app,
            patch("codex_usage_hud.cli.time.sleep", return_value=None),
        ):
            ready, status, reason, hwnd = _prepare_codex_window_for_tk(
                timeout_seconds=1.0,
                poll_seconds=0.0,
                launch_if_missing=True,
            )

        self.assertTrue(ready)
        self.assertEqual(status, "visible")
        self.assertEqual(reason, "")
        self.assertEqual(hwnd, 777)
        launch_app.assert_called_once_with(debugger=True)

    def test_prepare_missing_desktop_launches_with_fixed_default_cdp_port(self) -> None:
        tracker = SimpleNamespace(
            enabled=True,
            get_window_snapshot=MagicMock(
                return_value=SimpleNamespace(
                    status="not_found",
                    reason="Codex HWND not found",
                    hwnd=0,
                )
            ),
            is_active=MagicMock(return_value=False),
            activate_main_window=MagicMock(return_value=0),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(sys, "platform", "win32"),
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "codex_usage_hud.cli.hud_runtime_dir",
                    return_value=Path(temp_dir),
                ),
                patch("codex_usage_hud.cli.CodexWindowTracker", return_value=tracker),
                patch("codex_usage_hud.cli._codex_processes_running", return_value=False),
                patch("codex_usage_hud.cli.launch_codex_app", return_value=True) as launch_app,
            ):
                ready, status, reason, hwnd = _prepare_codex_window_for_renderer(
                    timeout_seconds=0.0,
                    poll_seconds=0.0,
                    launch_if_missing=True,
                )
                selected_port = os.environ.get(cli_module.CDP_PORT_ENV)

        self.assertFalse(ready)
        self.assertEqual(status, "not_found")
        self.assertEqual(reason, "Codex HWND not found")
        self.assertEqual(hwnd, 0)
        self.assertEqual(selected_port, str(cli_module.DEFAULT_CDP_PORT))
        launch_app.assert_called_once_with(debugger=True)

    def test_launch_codex_app_debugger_uses_macos_open_args(self) -> None:
        with (
            patch.object(sys, "platform", "darwin"),
            patch("codex_usage_hud.cli.cdp_port_from_env", return_value=9333),
            patch.dict(os.environ, {}, clear=True),
            patch("codex_usage_hud.cli.subprocess.Popen") as popen,
        ):
            launched = launch_codex_app(debugger=True)

        self.assertTrue(launched)
        command = popen.call_args.args[0]
        self.assertEqual(command[:4], ["open", "-a", "Codex", "--args"])
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertIn("--remote-allow-origins=http://127.0.0.1:9333", command)

    def test_run_renderer_hud_session_prepares_window_before_connect_in_manual_mode(self) -> None:
        fake_context = SimpleNamespace(
            settings_store=SimpleNamespace(path=Path("hud_settings.json")),
            user_config=UserConfig.defaults(),
            poll_ms=250,
            close=MagicMock(),
            reload_user_config=MagicMock(),
        )
        fake_client = SimpleNamespace(
            last_status="ok",
            last_error="",
            close=MagicMock(),
            timeout_seconds=1.0,
            take_settings_command=MagicMock(return_value=None),
            update=MagicMock(side_effect=KeyboardInterrupt),
        )
        fake_bridge = MagicMock()
        fake_bridge.start.return_value = "http://127.0.0.1:8765"

        with (
            patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
            patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
            patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
            patch("codex_usage_hud.cli._codex_processes_running", return_value=False),
            patch(
                "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                return_value=(True, "visible", "", 123),
            ) as prepare_window,
            patch(
                "codex_usage_hud.cli._wait_for_visible_codex_window",
                return_value=(True, "visible", "", 123),
            ),
            patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
            patch("codex_usage_hud.cli.build_snapshot", return_value=ParsedSession(status="parsed")),
        ):
            exit_code = run_renderer_hud_session(
                SimpleNamespace(),
                lock_already_held=True,
            )

        self.assertEqual(exit_code, 130)
        prepare_window.assert_called_once_with(
            timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
            launch_if_missing=True,
        )
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_context.close.assert_called_once()

    def test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep(self) -> None:
        runtime_events = RuntimeEventBus()
        events = []
        runtime_events.subscribe(events.append)
        fake_context = SimpleNamespace(
            settings_store=SimpleNamespace(path=Path("hud_settings.json")),
            user_config=UserConfig.defaults(),
            poll_ms=250,
            platform=SimpleNamespace(),
            runtime_events=runtime_events,
            close=MagicMock(),
            reload_user_config=MagicMock(),
        )
        command_started = threading.Event()
        command_finished = threading.Event()
        fake_snapshot = ParsedSession(status="parsed")
        fake_update_state = SimpleNamespace(to_dict=lambda: {"phase": "idle"})

        def take_commands() -> list[dict[str, object]]:
            if command_started.is_set():
                return []
            command_started.set()
            return [
                {
                    "action": "activateSession",
                    "sessionId": "thread-1",
                    "targetTitle": "Thread One",
                }
            ]

        fake_overlay = SimpleNamespace(
            configure=MagicMock(),
            update=MagicMock(),
            close=MagicMock(),
            take_commands=MagicMock(side_effect=take_commands),
        )
        fake_update_manager = SimpleNamespace(
            tick=MagicMock(return_value=fake_update_state),
            status=MagicMock(return_value=fake_update_state),
            close=MagicMock(),
        )
        fake_client = SimpleNamespace(
            last_status="ok",
            last_error="",
            close=MagicMock(),
            timeout_seconds=1.0,
            take_settings_command=MagicMock(return_value=None),
            update=MagicMock(),
        )
        fake_bridge = MagicMock()
        fake_bridge.start.return_value = "http://127.0.0.1:8765"
        session_controller = SimpleNamespace(
            activate_session=MagicMock(
                side_effect=lambda **kwargs: (
                    command_finished.set(),
                    SessionSwitchResult(
                        ok=True,
                        status="switched",
                        backend="cdp",
                        requested_session_id=kwargs["session_id"],
                        requested_title=kwargs["title"],
                        active_title=kwargs["title"],
                    ),
                )[1]
            )
        )

        update_calls = 0

        def update_side_effect(*args: object, **kwargs: object) -> bool:
            nonlocal update_calls
            update_calls += 1
            self.assertTrue(command_finished.wait(timeout=1.0))
            return True

        fake_client.update.side_effect = update_side_effect

        with (
            patch.object(sys, "platform", "win32"),
            patch("codex_usage_hud.cli.build_runtime_context", return_value=fake_context),
            patch("codex_usage_hud.cli.RendererHudClient", return_value=fake_client),
            patch("codex_usage_hud.cli.SettingsBridgeServer", return_value=fake_bridge),
            patch("codex_usage_hud.cli.DesktopWorkOverlay", return_value=fake_overlay) as overlay_class,
            patch("codex_usage_hud.cli.AutoUpdateManager", return_value=fake_update_manager),
            patch(
                "codex_usage_hud.cli._build_session_switch_controller",
                return_value=session_controller,
            ),
            patch(
                "codex_usage_hud.cli._prepare_codex_window_for_renderer",
                return_value=(True, "visible", "", 123),
            ) as prepare_window,
            patch("codex_usage_hud.cli._primary_screen_height", return_value=1080),
            patch(
                "codex_usage_hud.cli._prepare_codex_window_for_tk",
                return_value=(True, "visible", "", 321),
            ) as prepare_overlay_window,
            patch(
                "codex_usage_hud.cli._refocus_codex_window_after_current_session_click",
                return_value=(True, "visible", "", 321),
            ) as refocus_overlay_window,
            patch("codex_usage_hud.cli.wait_for_renderer", return_value=True),
            patch("codex_usage_hud.cli.build_snapshot", return_value=fake_snapshot),
            patch(
                "codex_usage_hud.cli._renderer_refresh_delay_seconds",
                side_effect=KeyboardInterrupt,
            ),
        ):
            exit_code = run_renderer_hud_session(
                SimpleNamespace(hud_mode="renderer"),
                lock_already_held=True,
            )

        self.assertEqual(exit_code, 130)
        overlay_class.assert_called_once_with(item_limit=6)
        self.assertTrue(command_started.is_set())
        self.assertTrue(command_finished.is_set())
        fake_overlay.take_commands.assert_called()
        session_controller.activate_session.assert_called_once_with(
            session_id="thread-1",
            title="Thread One",
            workdir="",
        )
        prepare_window.assert_called_once_with(
            timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
            launch_if_missing=True,
        )
        prepare_overlay_window.assert_not_called()
        refocus_overlay_window.assert_called_once()
        fake_client.close.assert_called_once()
        fake_bridge.close.assert_called_once()
        fake_update_manager.close.assert_called_once()
        fake_context.close.assert_called_once()
        self.assertEqual(
            [event.type for event in events],
            ["overlay_command_received", "active_session_changed"],
        )
        self.assertEqual(events[0].source, "work_overlay")
        self.assertEqual(events[0].context["action"], "activateSession")
        self.assertEqual(events[0].context["sessionId"], "thread-1")
        self.assertEqual(events[0].context["activeSessionId"], "")
        self.assertFalse(events[0].context["windowPrepared"])
        self.assertEqual(events[1].source, "work_overlay")
        self.assertEqual(events[1].context["reason"], "overlay_session_activation")

    def test_legacy_tk_hud_session_no_longer_prepares_or_opens_tk(self) -> None:
        loading = SimpleNamespace(close=MagicMock())

        with (
            patch("codex_usage_hud.cli.build_runtime_context") as build_context,
            patch("codex_usage_hud.cli._prepare_codex_window_for_tk") as prepare_window,
        ):
            exit_code = run_tk_hud_session(
                SimpleNamespace(compact=False),
                lock_already_held=True,
                loading_feedback=loading,
            )

        self.assertEqual(exit_code, RENDERER_HUD_UNAVAILABLE)
        build_context.assert_not_called()
        prepare_window.assert_not_called()
        loading.close.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
