"""Tkinter HUD with separate top and bottom Codex window docks."""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..config import (
    USER_CONFIG_KEY,
    UserConfig,
    UserConfigStore,
    default_settings_path as shared_default_settings_path,
    effective_display_mode,
    fetch_model_prices,
    parse_thresholds as parse_config_thresholds,
    read_json_object,
    write_json_object,
)
from ..core.parser import CostEstimator, ParsedSession, RequestRound
from ..platforms.cdp_probe import cdp_port_from_env, list_targets, pick_page_target
from ..support_assets import support_qr_asset_paths
from ..updater import (
    AutoUpdateManager,
    AutoUpdateState,
    check_for_update,
    download_update_asset,
    format_update_info,
    launch_installer,
)
from .work_overlay_qt import work_overlay_max_items_for_screen_height

SettingsEntry = tk.Entry | ttk.Combobox

TOP_DOCK_TOP = 42
TOP_DOCK_LEFT = 456
TOP_DOCK_RIGHT = 224
TOP_DOCK_HEIGHT = 36
TOP_DOCK_EXPANDED_HEIGHT = 390
TOP_DOCK_MIN_WIDTH = 1
TOP_DOCK_INTERACTIVE_MIN_WIDTH = 120
TOP_DOCK_EXPANDED_INTERACTIVE_MIN_WIDTH = 220
TOP_DOCK_EXPANDED_INTERACTIVE_MIN_HEIGHT = 240
TOP_ANCHOR_TOP = 38
TOP_ANCHOR_LEFT_RATIO = 0.155
TOP_ANCHOR_RIGHT_RATIO = 0.14
TOP_ANCHOR_LEFT_MIN = 154
TOP_ANCHOR_RIGHT_MIN = 172
TOP_ANCHOR_MIN_WIDTH = 320
TOP_EXPANDED_STACK_WIDTH = 560
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
CACHE_HIT_RATE_SYMBOL = "◎"
TOKEN_LEGEND_TEXT = (
    "↑ 输入  ↻ 缓存  ↓ 输出\n"
    "◇ 推理  ∑ 合计  $ 金额\n"
    f"{CACHE_HIT_RATE_SYMBOL} 缓存命中率  ~ 估算"
)

REQUEST_DOCK_BOTTOM = 28
REQUEST_DOCK_LEFT = 520
REQUEST_DOCK_RIGHT = 322
REQUEST_DOCK_WIDTH = 380
REQUEST_DOCK_HEIGHT = 32
REQUEST_DOCK_EXPANDED_HEIGHT = 180
REQUEST_DOCK_MIN_WIDTH = 1
REQUEST_DOCK_INTERACTIVE_MIN_WIDTH = 120
REQUEST_DOCK_EXPANDED_INTERACTIVE_MIN_WIDTH = 220
REQUEST_DOCK_EXPANDED_INTERACTIVE_MIN_HEIGHT = 120
REQUEST_ANCHOR_BOTTOM = 36
REQUEST_ANCHOR_LEFT_RATIO = 0.30
REQUEST_ANCHOR_RIGHT_RATIO = 0.28
REQUEST_ANCHOR_LEFT_MIN = 298
REQUEST_ANCHOR_RIGHT_MIN = 345
REQUEST_ANCHOR_MIN_WIDTH = 260
SETTINGS_DIALOG_WIDTH = 760
SETTINGS_DIALOG_HEIGHT = 620
HUD_SETTINGS_FILENAME = "hud_settings.json"
FOLLOW_ACTIVE_MS = 33
FOLLOW_INACTIVE_MS = 120
FOLLOW_TOMBSTONE_MS = 500
MARQUEE_START_PAUSE_MS = 1500
MARQUEE_END_PAUSE_MS = 1500
MARQUEE_STEP_PX = 1
MARQUEE_INTERVAL_MS = 30
SHIMMER_INTERVAL_MS = 30
SHIMMER_STEP_PX = 3
SHIMMER_BAND_WIDTH_PX = 58
SHIMMER_HIGHLIGHT = "#FFFFFF"
COUNTER_ANIMATION_MS = 360
COUNTER_STEP_MS = 30
NUMERIC_TOKEN_RE = re.compile(r"\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?")
LONG_DISPLAY_TOKEN_RE = re.compile(r"[^\s\u4e00-\u9fff，。；、：！？（）]+")
HUD_BG = "#10161D"
HUD_PANEL_BG = "#141B24"
HUD_PANEL_BORDER = "#3A485A"
HUD_WINDOW_OUTSIDE = "#010203"
HUD_WINDOW_TRANSPARENT = "#01FE02"
HUD_HEADER_BG = "#202833"
HUD_DIVIDER = "#273241"
HUD_TEXT = "#E8EEF7"
HUD_MUTED = "#8492A6"
HUD_ACCENT = "#F3D27A"
HUD_BLUE = "#9CCBFF"
HUD_ERROR = "#FF6B6B"
HUD_PROGRESS_TRACK = "#111822"
HUD_PROGRESS_TRACK_BORDER = "#314052"
HUD_PROGRESS_TRACK_TEXT = "#657589"
HUD_PROGRESS_CACHE = "#78B8FF"
HUD_PROGRESS_CACHE_END = "#5EA7FF"
HUD_PROGRESS_CACHE_TEXT = "#07131F"
HUD_PROGRESS_DAY = "#F3D27A"
HUD_PROGRESS_DAY_END = "#FFB86B"
HUD_PROGRESS_DAY_TEXT = "#1A1305"
HUD_PROGRESS_WEEK = "#FFC68D"
HUD_PROGRESS_WEEK_END = "#FF8D5A"
HUD_PROGRESS_WEEK_TEXT = "#1F1106"
HUD_SHELL_RADIUS = 0
HUD_PROGRESS_RADIUS = None
REQUEST_BG = "#0B1016"
REQUEST_HEADER_BG = "#151D27"
REQUEST_PANEL_BG = "#101821"
REQUEST_TEXT = "#DCE7F2"
REQUEST_MUTED = "#718095"
REQUEST_HUD_BG = HUD_BG
REQUEST_HUD_HEADER_BG = HUD_HEADER_BG
REQUEST_HUD_PANEL_BG = HUD_PANEL_BG
REQUEST_HUD_TEXT = HUD_TEXT
REQUEST_HUD_MUTED = HUD_MUTED
RESIZE_EDGE_HIT_SIZE = 6
RESIZE_CORNER_HIT_SIZE = 12
HUD_GEOMETRY_LOG_FILENAME = "hud_geometry.log"
HUD_NATIVE_ANCHORS_ENV = "CODEX_USAGE_HUD_NATIVE_ANCHORS"
HUD_CDP_DOM_ENV = "CODEX_USAGE_HUD_CDP_DOM"
HUD_NATIVE_GEOMETRY_ENV = "CODEX_USAGE_HUD_NATIVE_GEOMETRY"
HUD_INTERACTION_SETTLE_MS = 120


class _HoverTip:
    """Small dynamic tooltip that polls text while the pointer stays hovered."""

    def __init__(self, widget: tk.Misc, text_provider: Callable[[], str]) -> None:
        self.widget = widget
        self.text_provider = text_provider
        self.tip: tk.Toplevel | None = None
        self.label: tk.Label | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress-1>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _show(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._hide()
        text = self.text_provider().strip()
        if not text:
            return
        tip = tk.Toplevel(self.widget)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg=HUD_DIVIDER)
        label = tk.Label(
            tip,
            text=text,
            justify="left",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_TEXT,
            padx=8,
            pady=5,
            font=("Microsoft YaHei UI", 8),
            wraplength=260,
        )
        label.pack()
        self.tip = tip
        self.label = label
        self._position()
        tip.deiconify()
        self._schedule_refresh()

    def _position(self) -> None:
        if self.tip is None or self.label is None:
            return
        x = self.widget.winfo_rootx() + max(18, self.widget.winfo_width() + 6)
        y = self.widget.winfo_rooty() + max(0, (self.widget.winfo_height() // 2) - 10)
        self.tip.geometry(f"+{x}+{y}")

    def _schedule_refresh(self) -> None:
        self._after_id = self.widget.after(250, self._refresh)

    def _refresh(self) -> None:
        self._after_id = None
        if self.tip is None or self.label is None or not self.tip.winfo_exists():
            return
        text = self.text_provider().strip()
        if not text:
            self._hide()
            return
        self.label.configure(text=text)
        self._position()
        self._schedule_refresh()

    def _hide(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        tip, self.tip = self.tip, None
        self.label = None
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass


def _hex_to_rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if text.startswith("#") and len(text) == 7:
        try:
            return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
        except ValueError:
            return fallback
    if text.startswith("#") and len(text) == 4:
        try:
            return (
                int(text[1] * 2, 16),
                int(text[2] * 2, 16),
                int(text[3] * 2, 16),
            )
        except ValueError:
            return fallback
    return fallback


@lru_cache(maxsize=4096)
def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _mix_rgb(
    base: tuple[int, int, int],
    overlay: tuple[int, int, int],
    alpha: float,
) -> tuple[int, int, int]:
    alpha = max(0.0, min(1.0, alpha))
    inverse = 1.0 - alpha
    return (
        max(0, min(255, int(round((base[0] * inverse) + (overlay[0] * alpha))))),
        max(0, min(255, int(round((base[1] * inverse) + (overlay[1] * alpha))))),
        max(0, min(255, int(round((base[2] * inverse) + (overlay[2] * alpha))))),
    )


def _lerp_rgb(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    progress: float,
) -> tuple[int, int, int]:
    return _mix_rgb(start, end, max(0.0, min(1.0, progress)))


def _rounded_rect_alpha(
    x: float,
    y: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
) -> float:
    if right <= left or bottom <= top:
        return 0.0
    radius = max(0.0, min(radius, (right - left) / 2.0, (bottom - top) / 2.0))
    if left + radius <= x <= right - radius:
        signed_distance = max(top - y, y - bottom)
    elif top + radius <= y <= bottom - radius:
        signed_distance = max(left - x, x - right)
    else:
        center_x = left + radius if x < left + radius else right - radius
        center_y = top + radius if y < top + radius else bottom - radius
        signed_distance = math.hypot(x - center_x, y - center_y) - radius
    return max(0.0, min(1.0, 0.5 - signed_distance))


def _progress_track_rgb(
    y: float,
    top: float,
    bottom: float,
    track: tuple[int, int, int],
) -> tuple[int, int, int]:
    if bottom <= top:
        return track
    position = max(0.0, min(1.0, (y - top) / (bottom - top)))
    color = _lerp_rgb(_mix_rgb(track, (255, 255, 255), 0.045), track, min(1.0, position * 1.35))
    if position > 0.62:
        color = _mix_rgb(color, (0, 0, 0), ((position - 0.62) / 0.38) * 0.16)
    return color


def _progress_fill_rgb(
    x: float,
    y: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
    fill: tuple[int, int, int],
    fill_end: tuple[int, int, int],
    *,
    gloss: bool,
) -> tuple[int, int, int]:
    width = max(1.0, right - left)
    color = _lerp_rgb(fill, fill_end, (x - left) / width)
    if bottom > top:
        y_position = max(0.0, min(1.0, (y - top) / (bottom - top)))
        if y_position <= 0.52:
            if y_position <= 0.28:
                alpha = 0.24 - (y_position / 0.28 * 0.16)
            else:
                alpha = 0.08 * (1.0 - ((y_position - 0.28) / 0.24))
            color = _mix_rgb(color, (255, 255, 255), max(0.0, alpha))
    if gloss and width > 18:
        shine_left = max(left, right - min(64.0, width * 0.42))
        if x >= shine_left and top + 3 <= y <= bottom - 3:
            shine_progress = (x - shine_left) / max(1.0, right - shine_left)
            color = _mix_rgb(color, (255, 255, 255), 0.14 * shine_progress)
    return color


@lru_cache(maxsize=64)
def _scrollbar_active_thumb_color() -> str:
    return _rgb_to_hex(
        _mix_rgb(
            _hex_to_rgb(HUD_PANEL_BORDER, (58, 72, 90)),
            _hex_to_rgb(HUD_TEXT, (232, 238, 247)),
            0.16,
        )
    )


class HudScrollbar(tk.Canvas):
    """Canvas scrollbar used to mirror the renderer HUD's slim dark rail."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        command: Callable[..., Any] | None = None,
        track: str = HUD_BG,
        thumb: str = HUD_DIVIDER,
        thumb_hover: str = HUD_PANEL_BORDER,
        thumb_active: str | None = None,
        width: int = 8,
        min_thumb_px: int = 24,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("width", max(6, int(width)))
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bg", track)
        kwargs.setdefault("takefocus", 0)
        super().__init__(master, **kwargs)
        setattr(self, "_hud_scrollbar", True)
        self._command = command
        self._thumb_color = str(thumb)
        self._thumb_hover_color = str(thumb_hover)
        self._thumb_active_color = str(thumb_active or _scrollbar_active_thumb_color())
        self._min_thumb_px = max(18, int(min_thumb_px))
        self._first = 0.0
        self._last = 1.0
        self._thumb_visible = False
        self._hover = False
        self._dragging = False
        self._drag_anchor_y = 0
        self._drag_anchor_first = 0.0
        self.configure(cursor="arrow")
        self._thumb_id = self.create_rectangle(0, 0, 0, 0, width=0, outline="")
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Motion>", self._on_motion, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self._redraw()

    def set_command(self, command: Callable[..., Any] | None) -> None:
        self._command = command

    def set(self, first: float | str, last: float | str) -> None:
        try:
            start = float(first)
            end = float(last)
        except (TypeError, ValueError):
            return
        start = max(0.0, min(1.0, start))
        end = max(start, min(1.0, end))
        self._first = start
        self._last = end
        self._redraw()

    def _track_bounds(self) -> tuple[float, float, float, float]:
        width = max(2.0, float(self.winfo_width()))
        height = max(2.0, float(self.winfo_height()))
        return 1.0, 1.0, max(2.0, width - 1.0), max(2.0, height - 1.0)

    def _thumb_bounds(self) -> tuple[float, float, float, float] | None:
        left, top, right, bottom = self._track_bounds()
        visible_fraction = max(0.0, min(1.0, self._last - self._first))
        track_length = max(0.0, bottom - top)
        if track_length <= 0.0 or visible_fraction >= 0.999:
            return None
        thumb_length = min(track_length, max(float(self._min_thumb_px), track_length * visible_fraction))
        travel = max(0.0, track_length - thumb_length)
        max_first = max(0.0, 1.0 - visible_fraction)
        thumb_top = top
        if travel > 0.0 and max_first > 0.0:
            thumb_top += travel * (self._first / max_first)
        thumb_bottom = min(bottom, thumb_top + thumb_length)
        return left, thumb_top, right, thumb_bottom

    def _thumb_contains(self, y: float) -> bool:
        thumb = self._thumb_bounds()
        return bool(thumb and thumb[1] <= y <= thumb[3])

    def _thumb_fill(self) -> str:
        if self._dragging:
            return self._thumb_active_color
        if self._hover:
            return self._thumb_hover_color
        return self._thumb_color

    def _redraw(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        thumb = self._thumb_bounds()
        if thumb is None:
            self._thumb_visible = False
            self.itemconfigure(self._thumb_id, state="hidden")
            return
        self._thumb_visible = True
        self.coords(self._thumb_id, *thumb)
        self.itemconfigure(self._thumb_id, fill=self._thumb_fill(), state="normal")

    def _on_motion(self, event: tk.Event[tk.Misc]) -> str:
        hovering = self._thumb_contains(float(event.y))
        if hovering != self._hover and not self._dragging:
            self._hover = hovering
            self._redraw()
        return "break"

    def _on_leave(self, _event: tk.Event[tk.Misc]) -> str:
        if self._hover and not self._dragging:
            self._hover = False
            self._redraw()
        return "break"

    def _on_press(self, event: tk.Event[tk.Misc]) -> str:
        if self._command is None or not self._thumb_visible:
            return "break"
        if self._thumb_contains(float(event.y)):
            self._dragging = True
            self._hover = True
            self._drag_anchor_y = int(event.y)
            self._drag_anchor_first = self._first
            self._redraw()
            return "break"
        thumb = self._thumb_bounds()
        if thumb is None:
            return "break"
        direction = -1 if float(event.y) < thumb[1] else 1
        self._command("scroll", direction, "pages")
        return "break"

    def _on_drag(self, event: tk.Event[tk.Misc]) -> str:
        if not self._dragging or self._command is None:
            return "break"
        thumb = self._thumb_bounds()
        if thumb is None:
            return "break"
        visible_fraction = max(0.0, min(1.0, self._last - self._first))
        max_first = max(0.0, 1.0 - visible_fraction)
        thumb_length = max(0.0, thumb[3] - thumb[1])
        track_travel = max(1.0, (self.winfo_height() - 2.0) - thumb_length)
        delta = float(event.y - self._drag_anchor_y)
        target = self._drag_anchor_first + ((delta / track_travel) * max_first)
        self._command("moveto", max(0.0, min(max_first, target)))
        return "break"

    def _on_release(self, _event: tk.Event[tk.Misc]) -> str:
        if self._dragging:
            self._dragging = False
            self._redraw()
        return "break"


@lru_cache(maxsize=64)
def _progress_track_surface_rows(
    *,
    width: int,
    height: int,
    bg: str,
    track: str,
    border: str,
    radius: int | None = None,
) -> tuple[str, ...]:
    bg_rgb = _hex_to_rgb(bg, (16, 22, 29))
    track_rgb = _hex_to_rgb(track, (17, 24, 34))
    border_rgb = _hex_to_rgb(border, (49, 64, 82))
    left = 1.0
    top = 1.0
    right = max(left + 1.0, float(width) - 1.0)
    bottom = max(top + 1.0, float(height) - 1.0)
    resolved_radius = (bottom - top) / 2.0 if radius is None else float(radius)
    resolved_radius = max(0.0, min(resolved_radius, (right - left) / 2.0, (bottom - top) / 2.0))
    border_width = 1.0
    inner_left = left + border_width
    inner_top = top + border_width
    inner_right = max(inner_left, right - border_width)
    inner_bottom = max(inner_top, bottom - border_width)
    inner_radius = max(0.0, resolved_radius - border_width)
    rows: list[str] = []
    for y in range(height):
        colors = []
        point_y = y + 0.5
        track_color = _progress_track_rgb(point_y, inner_top, inner_bottom, track_rgb)
        for x in range(width):
            point_x = x + 0.5
            outer_alpha = _rounded_rect_alpha(
                point_x,
                point_y,
                left,
                top,
                right,
                bottom,
                resolved_radius,
            )
            if outer_alpha <= 0.0:
                colors.append(_rgb_to_hex(bg_rgb))
                continue
            inner_alpha = _rounded_rect_alpha(
                point_x,
                point_y,
                inner_left,
                inner_top,
                inner_right,
                inner_bottom,
                inner_radius,
            )
            color = _mix_rgb(bg_rgb, border_rgb, max(0.0, outer_alpha - inner_alpha))
            track_alpha = min(1.0, inner_alpha)
            if track_alpha > 0.0:
                color = _mix_rgb(color, track_color, track_alpha)
            colors.append(_rgb_to_hex(color))
        rows.append("{" + " ".join(colors) + "}")
    return tuple(rows)


@lru_cache(maxsize=96)
def _progress_fill_surface_rows(
    *,
    width: int,
    height: int,
    fill_width: int,
    bg: str,
    track: str,
    fill: str,
    fill_end: str,
    gloss: bool,
    radius: int | None = None,
) -> tuple[str, ...]:
    bg_rgb = _hex_to_rgb(bg, (16, 22, 29))
    track_rgb = _hex_to_rgb(track, (17, 24, 34))
    fill_rgb = _hex_to_rgb(fill, _hex_to_rgb(HUD_PROGRESS_DAY, (243, 210, 122)))
    fill_end_rgb = _hex_to_rgb(fill_end, fill_rgb)
    left = 0.0
    top = 0.0
    right = max(left + 1.0, float(width))
    bottom = max(top + 1.0, float(height))
    fill_right = max(left, min(right, float(fill_width)))
    resolved_radius = (bottom - top) / 2.0 if radius is None else float(radius)
    resolved_radius = max(0.0, min(resolved_radius, (right - left) / 2.0, (bottom - top) / 2.0))
    rows: list[str] = []
    for y in range(height):
        colors = []
        point_y = y + 0.5
        track_color = _progress_track_rgb(point_y, top, bottom, track_rgb)
        for x in range(width):
            point_x = x + 0.5
            shell_alpha = _rounded_rect_alpha(
                point_x,
                point_y,
                left,
                top,
                right,
                bottom,
                resolved_radius,
            )
            color = _mix_rgb(bg_rgb, track_color, shell_alpha)
            if shell_alpha > 0.0 and point_x <= fill_right:
                color = _mix_rgb(
                    color,
                    _progress_fill_rgb(
                        point_x,
                        point_y,
                        left,
                        top,
                        fill_right,
                        bottom,
                        fill_rgb,
                        fill_end_rgb,
                        gloss=gloss,
                    ),
                    shell_alpha,
                )
            colors.append(_rgb_to_hex(color))
        rows.append("{" + " ".join(colors) + "}")
    return tuple(rows)


@lru_cache(maxsize=32)
def _rounded_shell_surface_rows(
    *,
    width: int,
    height: int,
    radius: int,
    bg: str,
    border: str,
    outside: str,
) -> tuple[str, ...]:
    bg_rgb = _hex_to_rgb(bg, (16, 22, 29))
    border_rgb = _hex_to_rgb(border, (46, 56, 70))
    outside_rgb = _hex_to_rgb(outside, (14, 18, 23))
    if radius <= 0:
        bg_hex = _rgb_to_hex(bg_rgb)
        border_hex = _rgb_to_hex(border_rgb)
        rows: list[str] = []
        for y in range(max(1, height)):
            if y == 0 or y == height - 1 or width <= 1:
                colors = [border_hex] * max(1, width)
            else:
                colors = [border_hex, *([bg_hex] * max(0, width - 2)), border_hex]
            rows.append("{" + " ".join(colors[: max(1, width)]) + "}")
        return tuple(rows)
    blend_base_rgb = outside_rgb
    if sys.platform.startswith("win"):
        # When a Win32 color-key transparent window is available, keep the true
        # outside pixels on the transparent key and blend edge antialiasing
        # against the panel background instead of the transparent key.
        blend_base_rgb = bg_rgb
    left = 0.0
    top = 0.0
    right = max(left + 1.0, float(width))
    bottom = max(top + 1.0, float(height))
    outer_radius = max(1.0, min(float(radius), right / 2.0, bottom / 2.0))
    border_width = 1.0
    inner_left = left + border_width
    inner_top = top + border_width
    inner_right = max(inner_left, right - border_width)
    inner_bottom = max(inner_top, bottom - border_width)
    inner_radius = max(1.0, outer_radius - border_width)
    rows: list[str] = []
    outside_hex = _rgb_to_hex(outside_rgb)
    bg_hex = _rgb_to_hex(bg_rgb)
    border_hex = _rgb_to_hex(border_rgb)
    middle_row = (
        "{" + " ".join([border_hex, *([bg_hex] * max(0, width - 2)), border_hex][:width]) + "}"
        if width > 1
        else "{" + border_hex + "}"
    )
    for y in range(height):
        point_y = y + 0.5
        if outer_radius > 0.0 and outer_radius <= point_y <= bottom - outer_radius:
            rows.append(middle_row)
            continue
        colors = []
        for x in range(width):
            point_x = x + 0.5
            outer_alpha = _rounded_rect_alpha(point_x, point_y, left, top, right, bottom, outer_radius)
            if outer_alpha <= 0.0:
                colors.append(outside_hex)
                continue
            inner_alpha = _rounded_rect_alpha(
                point_x,
                point_y,
                inner_left,
                inner_top,
                inner_right,
                inner_bottom,
                inner_radius,
            )
            color = _mix_rgb(blend_base_rgb, border_rgb, outer_alpha)
            if inner_alpha > 0.0:
                color = _mix_rgb(color, bg_rgb, min(1.0, inner_alpha))
            colors.append(_rgb_to_hex(color))
        rows.append("{" + " ".join(colors) + "}")
    return tuple(rows)


def _window_outside_color() -> str:
    if sys.platform.startswith("win"):
        return HUD_WINDOW_TRANSPARENT
    return HUD_WINDOW_OUTSIDE
HUD_AUTO_REANCHOR_ENV = "CODEX_USAGE_HUD_AUTO_REANCHOR"
NATIVE_ANCHOR_STABLE_FRAMES = 3

_COST_ESTIMATOR = CostEstimator()
_HUD_GEOMETRY_LOGGER = logging.getLogger("codex_usage_hud.hud_geometry")
_HUD_GEOMETRY_LOGGER.addHandler(logging.NullHandler())
_HUD_GEOMETRY_LOGGING_CONFIGURED = False


def set_cost_estimator(estimator: CostEstimator) -> None:
    """Use the current user-configured price table for Tk formatting."""
    global _COST_ESTIMATOR
    _COST_ESTIMATOR = estimator


class _QuietRotatingFileHandler(RotatingFileHandler):
    """Suppress diagnostic-log rollover noise when another HUD owns the file."""

    def handleError(self, record: logging.LogRecord) -> None:
        del record
        return


@dataclass(frozen=True)
class WindowRect:
    """Native top-level window rectangle."""

    left: int
    top: int
    right: int
    bottom: int
    hwnd: int = 0
    title: str = ""
    process: str = ""
    class_name: str = ""
    minimized: bool = False

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class HudAnchor:
    """Dockable region used for manual HUD position and width ratios."""

    left: int
    top: int
    right: int
    bottom: int
    default_x: int
    default_y: int
    default_width: int
    source: str = "geometry"

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


@dataclass(frozen=True)
class _NativeAnchorState:
    signature: tuple[str, int, int, int, int, int, int, int]
    frames: int
    anchor: HudAnchor
    window_left: int
    window_top: int
    window_width: int
    window_height: int


@dataclass
class WindowPlacement:
    """Persisted custom placement for one HUD window."""

    relative_x: int | None = None
    relative_y: int | None = None
    relative_bottom: int | None = None
    relative_x_ratio: float | None = None
    relative_y_ratio: float | None = None
    relative_bottom_ratio: float | None = None
    absolute_x: int | None = None
    absolute_y: int | None = None
    width: int | None = None
    height: int | None = None
    width_ratio: float | None = None
    anchor_x_ratio: float | None = None
    anchor_y_ratio: float | None = None
    anchor_source: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "WindowPlacement":
        if not isinstance(value, dict):
            return cls()
        return cls(
            relative_x=_optional_int(value.get("relative_x")),
            relative_y=_optional_int(value.get("relative_y")),
            relative_bottom=_optional_int(value.get("relative_bottom")),
            relative_x_ratio=_optional_float(value.get("relative_x_ratio")),
            relative_y_ratio=_optional_float(value.get("relative_y_ratio")),
            relative_bottom_ratio=_optional_float(value.get("relative_bottom_ratio")),
            absolute_x=_optional_int(value.get("absolute_x")),
            absolute_y=_optional_int(value.get("absolute_y")),
            width=_optional_int(value.get("width")),
            height=_optional_int(value.get("height")),
            width_ratio=_optional_float(value.get("width_ratio")),
            anchor_x_ratio=_optional_float(value.get("anchor_x_ratio")),
            anchor_y_ratio=_optional_float(value.get("anchor_y_ratio")),
            anchor_source=_optional_str(value.get("anchor_source")),
        )

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "relative_x": self.relative_x,
            "relative_y": self.relative_y,
            "relative_bottom": self.relative_bottom,
            "relative_x_ratio": self.relative_x_ratio,
            "relative_y_ratio": self.relative_y_ratio,
            "relative_bottom_ratio": self.relative_bottom_ratio,
            "absolute_x": self.absolute_x,
            "absolute_y": self.absolute_y,
            "width": self.width,
            "height": self.height,
            "width_ratio": self.width_ratio,
            "anchor_x_ratio": self.anchor_x_ratio,
            "anchor_y_ratio": self.anchor_y_ratio,
            "anchor_source": self.anchor_source,
        }


@dataclass
class HudSettings:
    """Persisted HUD placement and width settings."""

    top: WindowPlacement
    request: WindowPlacement

    @classmethod
    def empty(cls) -> "HudSettings":
        return cls(top=WindowPlacement(), request=WindowPlacement())

    @classmethod
    def from_dict(cls, value: Any) -> "HudSettings":
        if not isinstance(value, dict):
            return cls.empty()
        return cls(
            top=WindowPlacement.from_dict(value.get("top")),
            request=WindowPlacement.from_dict(value.get("request")),
        )

    def to_dict(self) -> dict[str, dict[str, int | float | None]]:
        return {
            "top": self.top.to_dict(),
            "request": self.request.to_dict(),
        }


class HudSettingsStore:
    """Read and write HUD geometry settings with standard-library JSON."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> HudSettings:
        return HudSettings.from_dict(read_json_object(self.path))

    def save(self, settings: HudSettings) -> None:
        try:
            raw = read_json_object(self.path)
            raw.update(settings.to_dict())
            write_json_object(self.path, raw)
        except OSError:
            return


def default_settings_path() -> Path:
    """Return the per-user HUD settings path."""
    return shared_default_settings_path()


def hud_geometry_log_path() -> Path:
    """Return the per-user HUD geometry diagnostics log path."""
    explicit = os.environ.get("CODEX_USAGE_HUD_GEOMETRY_LOG")
    if explicit:
        return Path(explicit).expanduser()
    return default_settings_path().with_name(HUD_GEOMETRY_LOG_FILENAME)


def configure_hud_geometry_logging() -> Path | None:
    """Configure a small rolling log for manual placement diagnostics."""
    global _HUD_GEOMETRY_LOGGING_CONFIGURED
    if _HUD_GEOMETRY_LOGGING_CONFIGURED:
        return hud_geometry_log_path()
    path = hud_geometry_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _QuietRotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        return None
    level_name = os.environ.get("CODEX_USAGE_HUD_GEOMETRY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _HUD_GEOMETRY_LOGGER.handlers = [
        item
        for item in _HUD_GEOMETRY_LOGGER.handlers
        if not isinstance(item, logging.NullHandler)
    ]
    _HUD_GEOMETRY_LOGGER.addHandler(handler)
    _HUD_GEOMETRY_LOGGER.setLevel(level)
    _HUD_GEOMETRY_LOGGER.propagate = False
    _HUD_GEOMETRY_LOGGING_CONFIGURED = True
    return path


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _extract_numeric_parts(text: str) -> tuple[list[str], list[str]]:
    parts: list[str] = []
    tokens: list[str] = []
    cursor = 0
    for match in NUMERIC_TOKEN_RE.finditer(text):
        parts.append(text[cursor : match.start()])
        tokens.append(match.group(0))
        cursor = match.end()
    parts.append(text[cursor:])
    return parts, tokens


def _parse_numeric_token(token: str) -> tuple[str, float, str, int, bool] | None:
    match = re.fullmatch(r"(\$?)(\d+(?:,\d{3})*(?:\.\d+)?)([kM%]?)", token)
    if match is None:
        return None
    prefix, amount, suffix = match.groups()
    decimals = len(amount.split(".", 1)[1]) if "." in amount else 0
    uses_grouping = "," in amount
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    return prefix, value, suffix, decimals, uses_grouping


def _format_numeric_token(
    value: float,
    prefix: str,
    suffix: str,
    decimals: int,
    uses_grouping: bool,
) -> str:
    if decimals <= 0:
        body = f"{int(round(value)):,}" if uses_grouping else str(int(round(value)))
    else:
        fmt = f"{{:{',' if uses_grouping else ''}.{decimals}f}}"
        body = fmt.format(value)
    return f"{prefix}{body}{suffix}"


def _can_animate_numeric_text(start_text: str, end_text: str) -> bool:
    start_parts, start_tokens = _extract_numeric_parts(start_text)
    end_parts, end_tokens = _extract_numeric_parts(end_text)
    if start_parts != end_parts or len(start_tokens) != len(end_tokens):
        return False
    if not start_tokens:
        return False
    for start_token, end_token in zip(start_tokens, end_tokens):
        start_parsed = _parse_numeric_token(start_token)
        end_parsed = _parse_numeric_token(end_token)
        if start_parsed is None or end_parsed is None:
            return False
        if start_parsed[0] != end_parsed[0] or start_parsed[2] != end_parsed[2]:
            return False
    return True


def _interpolate_numeric_text(start_text: str, end_text: str, progress: float) -> str:
    start_parts, start_tokens = _extract_numeric_parts(start_text)
    end_parts, end_tokens = _extract_numeric_parts(end_text)
    pieces: list[str] = []
    clamped = max(0.0, min(1.0, progress))
    for index, part in enumerate(end_parts[:-1]):
        pieces.append(part)
        start_parsed = _parse_numeric_token(start_tokens[index])
        end_parsed = _parse_numeric_token(end_tokens[index])
        if start_parsed is None or end_parsed is None:
            pieces.append(end_tokens[index])
            continue
        _, start_value, _, _, _ = start_parsed
        prefix, end_value, suffix, decimals, uses_grouping = end_parsed
        current_value = start_value + ((end_value - start_value) * clamped)
        pieces.append(
            _format_numeric_token(
                current_value,
                prefix,
                suffix,
                decimals,
                uses_grouping,
            )
        )
    pieces.append(end_parts[-1])
    return "".join(pieces)


class AutoScrollLabel(tk.Frame):
    """Single-line label with optional marquee scrolling and numeric tweening."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        font: Any = ("Consolas", 9),
        fg: str = "#E8EEF7",
        bg: str = "#151A20",
        justify: str = "left",
        padding_x: int = 0,
        animate_numbers: bool = False,
        static_align: str = "left",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self._padding_x = max(0, int(padding_x))
        self._fg = fg
        self._bg = bg
        self._justify = justify
        self._static_align = static_align
        self._animate_numbers = animate_numbers
        self._font = tkfont.Font(font=font)
        self._canvas = tk.Canvas(
            self,
            bg=bg,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self._canvas.pack(fill="both", expand=True)
        self._text_id = self._canvas.create_text(
            self._padding_x,
            0,
            anchor="w",
            text="",
            fill=fg,
            font=self._font,
            justify=justify,
        )
        self._target_text = str(text or "")
        self._display_text = self._target_text
        self._rendered_text_width = 0
        self._scroll_job: str | None = None
        self._counter_job: str | None = None
        self._scroll_direction = -1
        self._scroll_x = float(self._padding_x)
        self._scroll_min_x = float(self._padding_x)
        self._scroll_max_x = float(self._padding_x)
        self._scrolling_enabled = False
        self._counter_step = 0
        self._counter_steps = max(1, COUNTER_ANIMATION_MS // COUNTER_STEP_MS)
        self._counter_start_text = self._target_text
        self.bind("<Configure>", self._handle_configure)
        self.bind("<Map>", self._handle_map)
        self.bind("<Unmap>", self._handle_unmap)
        self.bind("<Destroy>", self._handle_destroy)
        self._canvas.bind("<Configure>", self._handle_configure)
        self._apply_display_text(self._display_text)

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._target_text
        if key == "fg":
            return self._fg
        if key == "bg":
            return self._bg
        if key == "font":
            return self._font
        return super().cget(key)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", None)
        bg = kwargs.pop("bg", None)
        font = kwargs.pop("font", None)
        justify = kwargs.pop("justify", None)
        if bg is not None:
            self._bg = str(bg)
            super().configure(bg=self._bg)
            self._canvas.configure(bg=self._bg)
        if fg is not None:
            self._fg = str(fg)
            self._canvas.itemconfigure(self._text_id, fill=self._fg)
        if font is not None:
            self._font = tkfont.Font(font=font)
            self._canvas.itemconfigure(self._text_id, font=self._font)
        if justify is not None:
            self._justify = str(justify)
            self._canvas.itemconfigure(self._text_id, justify=self._justify)
        result = super().configure(**kwargs)
        if text is not None:
            self.set_text(str(text))
        else:
            self._apply_display_text(self._display_text)
            self._evaluate_scroll()
        return result

    def set_text(self, new_text: str) -> None:
        """Set new text and restart counter / marquee state when needed."""
        new_value = str(new_text or "")
        if new_value == self._target_text and self._counter_job is None:
            return
        previous_display = self._display_text
        self._target_text = new_value
        self._cancel_scroll_job()
        self._cancel_counter_job()
        if (
            self._animate_numbers
            and previous_display
            and _can_animate_numeric_text(previous_display, new_value)
            and previous_display != new_value
        ):
            self._counter_start_text = previous_display
            self._counter_step = 0
            self._schedule_counter_step()
            return
        self._apply_display_text(new_value)
        self._evaluate_scroll()

    def destroy(self) -> None:
        self._cancel_jobs()
        super().destroy()

    def _handle_configure(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._apply_display_text(self._display_text)
        self._evaluate_scroll()

    def _handle_map(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._evaluate_scroll()

    def _handle_unmap(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._cancel_scroll_job()
        if self._counter_job is not None:
            self._cancel_counter_job()
            self._apply_display_text(self._target_text)

    def _handle_destroy(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._cancel_jobs()

    def _cancel_jobs(self) -> None:
        self._cancel_scroll_job()
        self._cancel_counter_job()

    def _cancel_scroll_job(self) -> None:
        if self._scroll_job is None:
            return
        try:
            self.after_cancel(self._scroll_job)
        except tk.TclError:
            pass
        self._scroll_job = None

    def _cancel_counter_job(self) -> None:
        if self._counter_job is None:
            return
        try:
            self.after_cancel(self._counter_job)
        except tk.TclError:
            pass
        self._counter_job = None

    def _schedule_counter_step(self) -> None:
        if not self.winfo_exists():
            return
        self._counter_job = self.after(COUNTER_STEP_MS, self._run_counter_step)

    def _run_counter_step(self) -> None:
        self._counter_job = None
        if not self.winfo_exists():
            return
        self._counter_step += 1
        progress = min(1.0, self._counter_step / self._counter_steps)
        self._apply_display_text(
            _interpolate_numeric_text(
                self._counter_start_text,
                self._target_text,
                progress,
            )
        )
        if progress >= 1.0:
            self._apply_display_text(self._target_text)
            self._evaluate_scroll()
            return
        self._schedule_counter_step()

    def _apply_display_text(self, text: str) -> None:
        self._display_text = str(text or "")
        self._canvas.itemconfigure(self._text_id, text=self._display_text)
        self._rendered_text_width = self._font.measure(self._display_text)
        line_height = max(1, self._font.metrics("linespace"))
        self._canvas.configure(height=line_height + 2)
        self._canvas.coords(
            self._text_id,
            self._padding_x,
            self._canvas_center_y(),
        )
        self._canvas.itemconfigure(self._text_id, anchor="w")
        self._position_static_text()

    def _available_width(self) -> int:
        width = self._canvas.winfo_width() or self.winfo_width()
        return max(1, int(width) - (self._padding_x * 2))

    def _position_static_text(self) -> None:
        available_width = self._available_width()
        x = self._padding_x
        if self._rendered_text_width <= available_width and self._static_align == "center":
            x = self._padding_x + max(0, (available_width - self._rendered_text_width) / 2)
        self._canvas.coords(
            self._text_id,
            x,
            self._canvas_center_y(),
        )
        self._scroll_x = float(x)

    def _canvas_center_y(self) -> float:
        return max(
            1.0,
            (self._canvas.winfo_height() or (self._font.metrics("linespace") + 2)) / 2,
        )

    def _evaluate_scroll(self) -> None:
        if not self.winfo_exists() or not self.winfo_ismapped():
            self._cancel_scroll_job()
            return
        if self._counter_job is not None:
            self._cancel_scroll_job()
            return
        available_width = self._available_width()
        if self._rendered_text_width <= available_width:
            self._scrolling_enabled = False
            self._cancel_scroll_job()
            self._position_static_text()
            return

        self._scrolling_enabled = True
        self._scroll_max_x = float(self._padding_x)
        self._scroll_min_x = float(
            self._padding_x - max(0, self._rendered_text_width - available_width)
        )
        self._scroll_direction = -1
        self._scroll_x = self._scroll_max_x
        self._canvas.coords(
            self._text_id,
            self._scroll_x,
            self._canvas_center_y(),
        )
        self._cancel_scroll_job()
        self._scroll_job = self.after(MARQUEE_START_PAUSE_MS, self._scroll_step)

    def _scroll_step(self) -> None:
        self._scroll_job = None
        if not self.winfo_exists() or not self.winfo_ismapped() or not self._scrolling_enabled:
            return
        next_x = self._scroll_x + (MARQUEE_STEP_PX * self._scroll_direction)
        if self._scroll_direction < 0 and next_x <= self._scroll_min_x:
            self._scroll_x = self._scroll_min_x
            self._canvas.coords(
                self._text_id,
                self._scroll_x,
                self._canvas_center_y(),
            )
            self._scroll_direction = 1
            self._scroll_job = self.after(MARQUEE_END_PAUSE_MS, self._scroll_step)
            return
        if self._scroll_direction > 0 and next_x >= self._scroll_max_x:
            self._scroll_x = self._scroll_max_x
            self._canvas.coords(
                self._text_id,
                self._scroll_x,
                self._canvas_center_y(),
            )
            self._scroll_direction = -1
            self._scroll_job = self.after(MARQUEE_START_PAUSE_MS, self._scroll_step)
            return
        self._scroll_x = next_x
        self._canvas.coords(
            self._text_id,
            self._scroll_x,
            self._canvas_center_y(),
        )
        self._scroll_job = self.after(MARQUEE_INTERVAL_MS, self._scroll_step)


class ShimmerTextLabel(tk.Frame):
    """Canvas text shimmer that keeps the highlight clipped to glyphs.

    Tk does not expose a text-mask brush like Qt, so this redraws each glyph with
    a moving color ramp. The canvas background remains untouched outside text.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        font: Any = ("Microsoft YaHei UI", 8, "bold"),
        fg: str = "#8492A6",
        bg: str = "#10161D",
        highlight: str = SHIMMER_HIGHLIGHT,
        band_width_px: int = SHIMMER_BAND_WIDTH_PX,
        step_px: int = SHIMMER_STEP_PX,
        timer_ms: int = SHIMMER_INTERVAL_MS,
        padding_x: int = 0,
        static_align: str = "left",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self._text = str(text or "")
        self._fg = str(fg)
        self._bg = str(bg)
        self._highlight = str(highlight)
        self._band_width_px = max(1, int(band_width_px))
        self._step_px = max(1, int(step_px))
        self._timer_ms = max(10, int(timer_ms))
        self._padding_x = max(0, int(padding_x))
        self._static_align = static_align
        self._font = tkfont.Font(font=font)
        self._canvas = tk.Canvas(
            self,
            bg=self._bg,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self._canvas.pack(fill="both", expand=True)
        self._char_ids: list[int] = []
        self._char_centers: list[float] = []
        self._phase_x = float(-self._band_width_px)
        self._shimmer_job: str | None = None
        self.bind("<Configure>", self._handle_configure)
        self.bind("<Map>", self._handle_map)
        self.bind("<Unmap>", self._handle_unmap)
        self.bind("<Destroy>", self._handle_destroy)
        self._canvas.bind("<Configure>", self._handle_configure)
        self._layout_text()
        self._schedule_shimmer()

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "fg":
            return self._fg
        if key == "bg":
            return self._bg
        if key == "font":
            return self._font
        if key == "highlight":
            return self._highlight
        return super().cget(key)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", None)
        bg = kwargs.pop("bg", None)
        font = kwargs.pop("font", None)
        highlight = kwargs.pop("highlight", None)
        if fg is not None:
            self._fg = str(fg)
        if bg is not None:
            self._bg = str(bg)
            super().configure(bg=self._bg)
            self._canvas.configure(bg=self._bg)
        if font is not None:
            self._font = tkfont.Font(font=font)
        if highlight is not None:
            self._highlight = str(highlight)
        result = super().configure(**kwargs)
        if text is not None:
            self.set_text(str(text))
        else:
            self._layout_text()
            self._apply_shimmer_colors()
        return result

    def set_text(self, text: str) -> None:
        next_text = str(text or "")
        if next_text == self._text:
            return
        self._text = next_text
        self._phase_x = float(-self._band_width_px)
        self._layout_text()
        self._apply_shimmer_colors()

    def destroy(self) -> None:
        self._cancel_shimmer()
        super().destroy()

    def _handle_configure(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._layout_text()
        self._apply_shimmer_colors()

    def _handle_map(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._schedule_shimmer()

    def _handle_unmap(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._cancel_shimmer()

    def _handle_destroy(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._cancel_shimmer()

    def _cancel_shimmer(self) -> None:
        if self._shimmer_job is None:
            return
        try:
            self.after_cancel(self._shimmer_job)
        except tk.TclError:
            pass
        self._shimmer_job = None

    def _schedule_shimmer(self) -> None:
        if self._shimmer_job is not None or not self.winfo_exists():
            return
        self._shimmer_job = self.after(self._timer_ms, self._run_shimmer_step)

    def _run_shimmer_step(self) -> None:
        self._shimmer_job = None
        if not self.winfo_exists() or not self.winfo_ismapped():
            return
        text_width = self._font.measure(self._text)
        limit = max(text_width, self.winfo_width()) + self._band_width_px
        self._phase_x += self._step_px
        if self._phase_x > limit:
            self._phase_x = float(-self._band_width_px)
        self._apply_shimmer_colors()
        self._schedule_shimmer()

    def _layout_text(self) -> None:
        self._canvas.delete("all")
        self._char_ids.clear()
        self._char_centers.clear()
        line_height = max(1, self._font.metrics("linespace"))
        self._canvas.configure(height=line_height + 2)
        y = max(1.0, (self._canvas.winfo_height() or (line_height + 2)) / 2)
        text_width = self._font.measure(self._text)
        available_width = max(
            1,
            (self._canvas.winfo_width() or self.winfo_width()) - (self._padding_x * 2),
        )
        x = float(self._padding_x)
        if text_width <= available_width and self._static_align == "center":
            x += max(0.0, (available_width - text_width) / 2)
        for char in self._text:
            char_width = self._font.measure(char)
            item_id = self._canvas.create_text(
                x,
                y,
                anchor="w",
                text=char,
                fill=self._fg,
                font=self._font,
            )
            self._char_ids.append(item_id)
            self._char_centers.append(x + (char_width / 2))
            x += char_width

    def _apply_shimmer_colors(self) -> None:
        for item_id, center_x in zip(self._char_ids, self._char_centers):
            distance = abs(center_x - self._phase_x)
            intensity = max(0.0, 1.0 - (distance / self._band_width_px))
            color = self._blend_color(self._fg, self._highlight, intensity * intensity)
            self._canvas.itemconfigure(item_id, fill=color)

    def _blend_color(self, start: str, end: str, progress: float) -> str:
        progress = max(0.0, min(1.0, progress))
        start_rgb = self.winfo_rgb(start)
        end_rgb = self.winfo_rgb(end)
        channels = []
        for start_value, end_value in zip(start_rgb, end_rgb):
            value = start_value + ((end_value - start_value) * progress)
            channels.append(max(0, min(255, int(round(value / 257)))))
        return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"


@dataclass(frozen=True)
class TopHudProgressMetric:
    label: str
    ratio: float
    fill: str
    fill_end: str
    fill_text: str
    track_text: str = HUD_PROGRESS_TRACK_TEXT
    right_text: str = ""


def _clamp_progress_ratio(value: float | int | None) -> float:
    try:
        ratio = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, ratio))


class TopHudProgressBar(tk.Frame):
    """Text-in-bar progress rail used by both top HUD states."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        height: int = 26,
        width: int | None = None,
        bg: str = HUD_BG,
        font: Any = ("Microsoft YaHei UI", 8, "bold"),
        padding_x: int = 12,
        gloss: bool = True,
        radius: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self._height = max(16, int(height))
        self._width = max(1, int(width)) if width is not None else None
        self._bg = bg
        self._font = tkfont.Font(font=font)
        self._padding_x = max(4, int(padding_x))
        self._gloss = bool(gloss)
        self._radius = None if radius is None else max(0, int(radius))
        self._track_surface_key: tuple[int, int, str, str, str, int | None] | None = None
        self._track_surface_image: tk.PhotoImage | None = None
        self._fill_surface_key: tuple[int, int, int, str, str, str, str, bool, int | None] | None = None
        self._fill_surface_image: tk.PhotoImage | None = None
        self._metric = TopHudProgressMetric(
            label="",
            ratio=0.0,
            fill=HUD_PROGRESS_DAY,
            fill_end=HUD_PROGRESS_DAY_END,
            fill_text=HUD_PROGRESS_DAY_TEXT,
        )
        canvas_kwargs: dict[str, Any] = {}
        if self._width is not None:
            canvas_kwargs["width"] = self._width
        self._canvas = tk.Canvas(
            self,
            bg=self._bg,
            height=self._height,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            **canvas_kwargs,
        )
        setattr(self._canvas, "_hud_progress_canvas", True)
        self._canvas.pack(fill="both", expand=True)
        _HoverTip(self._canvas, self._tooltip_text)
        self.bind("<Configure>", self._handle_configure)
        self._canvas.bind("<Configure>", self._handle_configure)
        self._draw()

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._metric.label
        if key == "bg":
            return self._bg
        if key == "font":
            return self._font
        return super().cget(key)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        metric = kwargs.pop("metric", None)
        text = kwargs.pop("text", None)
        bg = kwargs.pop("bg", None)
        font = kwargs.pop("font", None)
        if bg is not None:
            self._bg = str(bg)
            super().configure(bg=self._bg)
            self._canvas.configure(bg=self._bg)
        if font is not None:
            self._font = tkfont.Font(font=font)
        result = super().configure(**kwargs)
        if metric is not None:
            self.set_metric(metric)
        elif text is not None:
            self.set_metric(
                TopHudProgressMetric(
                    label=str(text or ""),
                    ratio=0.0,
                    fill=HUD_PROGRESS_DAY,
                    fill_end=HUD_PROGRESS_DAY_END,
                    fill_text=HUD_PROGRESS_DAY_TEXT,
                    track_text=HUD_TEXT,
                    right_text="",
                )
            )
        else:
            self._draw()
        return result

    def set_metric(self, metric: TopHudProgressMetric) -> None:
        self._metric = TopHudProgressMetric(
            label=str(metric.label or ""),
            ratio=_clamp_progress_ratio(metric.ratio),
            fill=str(metric.fill or HUD_PROGRESS_DAY),
            fill_end=str(metric.fill_end or metric.fill or HUD_PROGRESS_DAY),
            fill_text=str(metric.fill_text or HUD_PROGRESS_DAY_TEXT),
            track_text=str(metric.track_text or HUD_PROGRESS_TRACK_TEXT),
            right_text=str(metric.right_text or ""),
        )
        self._draw()

    def preferred_width(self) -> int:
        label_width = self._font.measure(self._metric.label)
        right_width = self._font.measure(self._metric.right_text) if self._metric.right_text else 0
        gap = 8 if right_width else 0
        # Match the real drawable width in _draw(): 1px inset on both sides plus text padding.
        return label_width + right_width + gap + (self._padding_x * 2) + 2

    def set_requested_width(self, width: int) -> None:
        self._width = max(1, int(width))
        self._canvas.configure(width=self._width)

    def _tooltip_text(self) -> str:
        label = self._metric.label.strip()
        right_text = self._metric.right_text.strip()
        if label and right_text:
            return f"{label} / {right_text}"
        return label or right_text

    def _handle_configure(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._draw()

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = max(1, canvas.winfo_width() or self.winfo_width())
        height = max(1, canvas.winfo_height() or self._height)
        x1, y1, x2, y2 = 1, 1, max(2, width - 1), max(2, height - 1)

        fill_width = max(0, int((x2 - x1) * self._metric.ratio))
        self._draw_progress_surface(width, height, fill_width)

        label = self._metric.label
        right_text = self._metric.right_text
        text_x = x1 + self._padding_x
        right_x = x2 - self._padding_x
        right_width = self._font.measure(right_text) if right_text else 0
        text_gap = 8 if right_width else 0
        label_max_width = max(0, right_x - right_width - text_gap - text_x)
        label = self._fit_text(label, label_max_width)
        text_y = height / 2
        if label:
            canvas.create_text(
                text_x,
                text_y,
                anchor="w",
                text=label,
                fill=self._metric.track_text,
                font=self._font,
            )
        if right_text:
            canvas.create_text(
                right_x,
                text_y,
                anchor="e",
                text=right_text,
                fill=self._metric.track_text,
                font=self._font,
            )
        if label and fill_width >= self._font.measure(label) + (self._padding_x * 2):
            canvas.create_text(
                text_x,
                text_y,
                anchor="w",
                text=label,
                fill=self._metric.fill_text,
                font=self._font,
            )
        if right_text and (x1 + fill_width) >= right_x:
            canvas.create_text(
                right_x,
                text_y,
                anchor="e",
                text=right_text,
                fill=self._metric.fill_text,
                font=self._font,
            )

    def _draw_progress_surface(self, width: int, height: int, fill_width: int) -> None:
        if width <= 1 or height <= 1:
            return
        track_key = (
            width,
            height,
            self._bg,
            HUD_PROGRESS_TRACK,
            HUD_PROGRESS_TRACK_BORDER,
            self._radius,
        )
        track_image = self._track_surface_image
        if track_image is None or self._track_surface_key != track_key:
            track_image = tk.PhotoImage(width=width, height=height)
            rows = _progress_track_surface_rows(
                width=width,
                height=height,
                bg=self._bg,
                track=HUD_PROGRESS_TRACK,
                border=HUD_PROGRESS_TRACK_BORDER,
                radius=self._radius,
            )
            track_image.put(" ".join(rows), to=(0, 0))
            self._track_surface_key = track_key
            self._track_surface_image = track_image
        self._canvas.create_image(0, 0, anchor="nw", image=track_image)

        inner_width = max(1, width - 4)
        inner_height = max(1, height - 4)
        fill_inner_width = min(inner_width, max(0, fill_width - 1))
        if fill_inner_width <= 0:
            return
        fill_key = (
            inner_width,
            inner_height,
            fill_inner_width,
            self._bg,
            HUD_PROGRESS_TRACK,
            self._metric.fill,
            self._metric.fill_end,
            self._gloss,
            self._radius,
        )
        fill_image = self._fill_surface_image
        if fill_image is None or self._fill_surface_key != fill_key:
            fill_image = tk.PhotoImage(width=inner_width, height=inner_height)
            rows = _progress_fill_surface_rows(
                width=inner_width,
                height=inner_height,
                fill_width=fill_inner_width,
                bg=self._bg,
                track=HUD_PROGRESS_TRACK,
                fill=self._metric.fill,
                fill_end=self._metric.fill_end,
                gloss=self._gloss,
                radius=self._radius,
            )
            fill_image.put(" ".join(rows), to=(0, 0))
            self._fill_surface_key = fill_key
            self._fill_surface_image = fill_image
        self._canvas.create_image(2, 2, anchor="nw", image=fill_image)

    def _fit_text(self, text: str, max_width: int) -> str:
        if not text or max_width <= 0:
            return ""
        if self._font.measure(text) <= max_width:
            return text
        suffix = "..."
        suffix_width = self._font.measure(suffix)
        if max_width <= suffix_width:
            return ""
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            candidate = text[:mid] + suffix
            if self._font.measure(candidate) <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low] + suffix

class TopHudProgressStrip(tk.Frame):
    """Collapsed top HUD strip with session/day/week rails."""

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(master, bg=HUD_BG, **kwargs)
        self._text = ""
        self._bars: list[TopHudProgressBar] = []
        self._base_widths = (72, 68, 76)
        self._column_gap = 7
        for index in range(3):
            bar = TopHudProgressBar(
                self,
                height=28,
                width=self._base_widths[index],
                bg=HUD_BG,
                font=("Microsoft YaHei UI", 7, "bold"),
                padding_x=10,
                gloss=False,
                radius=HUD_PROGRESS_RADIUS,
            )
            bar.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else self._column_gap, 0),
            )
            self.columnconfigure(index, weight=0, minsize=self._base_widths[index])
            self._bars.append(bar)
        self.rowconfigure(0, weight=1)

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        return super().cget(key)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        metrics = kwargs.pop("metrics", None)
        result = super().configure(**kwargs)
        if metrics is not None:
            self.set_metrics(list(metrics))
        if text is not None:
            self._text = str(text or "")
        return result

    def set_metrics(self, metrics: list[TopHudProgressMetric]) -> None:
        labels = []
        for index, bar in enumerate(self._bars):
            metric = metrics[index] if index < len(metrics) else TopHudProgressMetric(
                label="",
                ratio=0.0,
                fill=HUD_PROGRESS_DAY,
                fill_end=HUD_PROGRESS_DAY_END,
                fill_text=HUD_PROGRESS_DAY_TEXT,
            )
            bar.set_metric(metric)
            labels.append(metric.label)
        single_metric = len(metrics) == 1 and bool(metrics[0].label)
        if single_metric:
            self._layout_single_metric()
        else:
            self._layout_multi_metric()
        self._text = " | ".join(label for label in labels if label)

    def _layout_multi_metric(self) -> None:
        widths = [max(self._base_widths[index], bar.preferred_width()) for index, bar in enumerate(self._bars)]
        tail_width = max(widths[1], widths[2])
        for index, bar in enumerate(self._bars):
            width = widths[0] if index == 0 else tail_width
            bar.grid(
                row=0,
                column=index,
                columnspan=1,
                sticky="nsew",
                padx=(0 if index == 0 else self._column_gap, 0),
            )
            bar.set_requested_width(width)
            self.columnconfigure(
                index,
                weight=0 if index == 0 else 1,
                minsize=width,
                uniform=None if index == 0 else "top-collapsed-tail",
            )

    def _layout_single_metric(self) -> None:
        for index, bar in enumerate(self._bars):
            if index == 0:
                bar.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=0)
            else:
                bar.grid_remove()
            if index == 0:
                width = max(self._base_widths[0], bar.preferred_width())
                bar.set_requested_width(width)
                self.columnconfigure(index, weight=1, minsize=width, uniform=None)
            else:
                self.columnconfigure(index, weight=0, minsize=0, uniform=None)


class TopHudBudgetProgress(tk.Frame):
    """Expanded budget area rendered as day/week text-in-progress rails."""

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(master, bg=HUD_BG, **kwargs)
        self._text = ""
        self._day = TopHudProgressBar(
            self,
            height=36,
            bg=HUD_BG,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding_x=14,
            radius=HUD_PROGRESS_RADIUS,
        )
        self._week = TopHudProgressBar(
            self,
            height=36,
            bg=HUD_BG,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding_x=14,
            radius=HUD_PROGRESS_RADIUS,
        )
        self._meta = tk.Label(
            self,
            text="",
            anchor="w",
            justify="left",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 7),
            wraplength=360,
        )
        self._day.pack(fill="x", pady=(0, 6))
        self._week.pack(fill="x", pady=(0, 5))
        self._meta.pack(fill="x")
        self.bind("<Configure>", self._sync_meta_wrap, add="+")

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "wraplength":
            return self._meta.cget("wraplength")
        return super().cget(key)

    def config(self, cnf: Any = None, **kwargs: Any) -> Any:
        return self.configure(cnf, **kwargs)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        metrics = kwargs.pop("metrics", None)
        meta = kwargs.pop("meta", None)
        result = super().configure(**kwargs)
        if metrics is not None:
            self.set_metrics(list(metrics), meta=str(meta or ""))
        elif text is not None:
            self._text = str(text or "")
            self._meta.configure(text=self._text)
        return result

    def set_metrics(self, metrics: list[TopHudProgressMetric], *, meta: str = "") -> None:
        if metrics:
            self._day.set_metric(metrics[0])
        if len(metrics) > 1:
            self._week.set_metric(metrics[1])
        self._text = "\n".join(metric.label for metric in metrics if metric.label)
        if meta:
            self._text = f"{self._text}\n{meta}" if self._text else meta
        self._meta.configure(text=meta)

    def _sync_meta_wrap(self, event: tk.Event[tk.Misc]) -> None:
        width = max(120, int(getattr(event, "width", 360) or 360) - 4)
        self._meta.configure(wraplength=width)


class RoundedHudShell(tk.Frame):
    """Antialiased HUD shell with a continuous 1px border."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        bg: str = HUD_BG,
        border: str = HUD_PANEL_BORDER,
        outside: str = HUD_WINDOW_OUTSIDE,
        radius: int = 8,
        padx: int = 5,
        pady: int = 1,
    ) -> None:
        super().__init__(
            master,
            bg=outside,
            highlightthickness=0,
            borderwidth=0,
        )
        self._bg = bg
        self._border = border
        self._outside = outside
        self._radius = max(0, int(radius))
        self._image: tk.PhotoImage | None = None
        self._image_key: tuple[int, int, int, str, str, str] | None = None
        self._region_key: tuple[int, int, int] | None = None
        self._region_retry_job: str | None = None
        self._background = tk.Label(
            self,
            bg=outside,
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self._background.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = tk.Frame(
            self,
            bg=bg,
            padx=max(0, int(padx)),
            pady=max(0, int(pady)),
            highlightthickness=0,
            borderwidth=0,
        )
        self.content.pack(fill="both", expand=True, padx=2, pady=2)
        self.bind("<Configure>", self._handle_configure, add="+")
        self.bind("<Map>", self._handle_map, add="+")
        self.bind("<Destroy>", self._handle_destroy, add="+")

    def _handle_configure(self, event: tk.Event[tk.Misc]) -> None:
        width = max(1, int(getattr(event, "width", 0) or self.winfo_width()))
        height = max(1, int(getattr(event, "height", 0) or self.winfo_height()))
        self._draw_shell(width, height)
        self._apply_window_region(width, height)
        self._schedule_region_retry()

    def _handle_map(self, event: tk.Event[tk.Misc]) -> None:
        del event
        width = max(1, int(self.winfo_width() or 1))
        height = max(1, int(self.winfo_height() or 1))
        self._draw_shell(width, height)
        self._apply_window_region(width, height)
        self._schedule_region_retry()

    def _handle_destroy(self, event: tk.Event[tk.Misc]) -> None:
        del event
        if self._region_retry_job is None:
            return
        try:
            self.after_cancel(self._region_retry_job)
        except Exception:
            pass
        self._region_retry_job = None

    def _schedule_region_retry(self) -> None:
        if not sys.platform.startswith("win"):
            return
        if self._region_retry_job is not None:
            try:
                self.after_cancel(self._region_retry_job)
            except Exception:
                pass
        self._region_retry_job = self.after_idle(self._retry_region_after_idle)

    def _retry_region_after_idle(self) -> None:
        self._region_retry_job = None
        try:
            width = max(1, int(self.winfo_width() or 1))
            height = max(1, int(self.winfo_height() or 1))
        except Exception:
            return
        self._draw_shell(width, height)
        self._apply_window_region(width, height)

    def _draw_shell(self, width: int, height: int) -> None:
        key = (width, height, self._radius, self._bg, self._border, self._outside)
        if self._image is not None and self._image_key == key:
            return
        image = tk.PhotoImage(width=width, height=height)
        rows = _rounded_shell_surface_rows(
            width=width,
            height=height,
            radius=self._radius,
            bg=self._bg,
            border=self._border,
            outside=self._outside,
        )
        image.put(" ".join(rows), to=(0, 0))
        self._image = image
        self._image_key = key
        self._background.configure(image=image)

    def _apply_window_region(self, width: int, height: int) -> None:
        if not sys.platform.startswith("win"):
            return
        key = (width, height, self._radius)
        if self._region_key == key:
            return
        try:
            import ctypes

            hwnd = int(self.winfo_toplevel().winfo_id())
            if self._radius <= 0:
                region = ctypes.windll.gdi32.CreateRectRgn(0, 0, width + 1, height + 1)
            else:
                diameter = self._radius * 2
                region = ctypes.windll.gdi32.CreateRoundRectRgn(
                    0,
                    0,
                    width + 1,
                    height + 1,
                    diameter,
                    diameter,
                )
            if not region:
                return
            if ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
                self._region_key = key
                return
            ctypes.windll.gdi32.DeleteObject(region)
        except Exception:
            return


@dataclass(frozen=True)
class DockGeometry:
    """Original HUD geometry rules relative to the Codex window."""

    top: int
    left: int
    right: int
    height: int
    expanded_height: int
    min_width: int
    bottom: int | None = None
    fixed_width: int | None = None

    def calculate(self, rect: WindowRect, expanded: bool = False) -> tuple[int, int, int, int]:
        available_width = max(1, rect.width - self.left - self.right)
        if self.fixed_width is None:
            width = max(self.min_width, available_width)
        else:
            width = min(max(self.min_width, self.fixed_width), available_width)

        height = self.expanded_height if expanded else self.height
        if self.bottom is None:
            y = rect.top + self.top
        else:
            y = rect.bottom - self.bottom - height

        x = rect.left + self.left
        if x + width > rect.right - 12:
            width = max(self.min_width, rect.right - x - 12)
        return x, y, width, height


class AttachedHudGeometry:
    """Backward-compatible top-right helper used by older tests/adapters."""

    @staticmethod
    def calculate(
        rect: WindowRect,
        width: int,
        height: int,
        offset: int = 10,
    ) -> tuple[int, int, int, int]:
        usable_width = max(1, rect.width - (offset * 2))
        usable_height = max(1, rect.height - (offset * 2))
        final_width = min(max(240, width), usable_width)
        final_height = min(max(32, height), usable_height)
        x = rect.right - final_width - offset
        y = rect.top + offset
        return x, y, final_width, final_height


def _visual_anchor_geometry(
    target: str, rect: WindowRect, expanded: bool = False
) -> tuple[int, int, int, int]:
    """Place HUDs on Codex chrome landmarks rather than fixed old offsets."""
    if target == "top":
        height = TOP_DOCK_EXPANDED_HEIGHT if expanded else TOP_DOCK_HEIGHT
        left_margin = max(TOP_ANCHOR_LEFT_MIN, int(round(rect.width * TOP_ANCHOR_LEFT_RATIO)))
        right_margin = max(TOP_ANCHOR_RIGHT_MIN, int(round(rect.width * TOP_ANCHOR_RIGHT_RATIO)))
        min_width = TOP_ANCHOR_MIN_WIDTH
        y = rect.top + TOP_ANCHOR_TOP
    else:
        height = REQUEST_DOCK_EXPANDED_HEIGHT if expanded else REQUEST_DOCK_HEIGHT
        left_margin = max(
            REQUEST_ANCHOR_LEFT_MIN,
            int(round(rect.width * REQUEST_ANCHOR_LEFT_RATIO)),
        )
        right_margin = max(
            REQUEST_ANCHOR_RIGHT_MIN,
            int(round(rect.width * REQUEST_ANCHOR_RIGHT_RATIO)),
        )
        min_width = REQUEST_ANCHOR_MIN_WIDTH
        y = rect.bottom - REQUEST_ANCHOR_BOTTOM - height

    left_margin = _fit_anchor_left(rect.width, left_margin, right_margin, min_width)
    width = max(1, rect.width - left_margin - right_margin)
    return rect.left + left_margin, y, width, height


def _fallback_hud_anchor(
    target: str,
    rect: WindowRect,
    base_x: int,
    base_y: int,
    base_width: int,
    hud_height: int,
) -> HudAnchor:
    """Build a geometry-only anchor when native UI landmarks are unavailable."""
    if target == "top":
        title_height = max(1, min(48, rect.height))
        return HudAnchor(
            left=base_x,
            top=rect.top,
            right=base_x + max(1, base_width),
            bottom=rect.top + title_height,
            default_x=base_x,
            default_y=base_y,
            default_width=max(1, base_width),
            source="geometry",
        )
    input_top = base_y + hud_height
    input_height = max(1, min(64, rect.bottom - input_top))
    return HudAnchor(
        left=base_x,
        top=input_top,
        right=base_x + max(1, base_width),
        bottom=input_top + input_height,
        default_x=base_x,
        default_y=base_y,
        default_width=max(1, base_width),
        source="geometry",
    )


def _offset_hud_anchor(anchor: HudAnchor, dx: int, dy: int) -> HudAnchor:
    return HudAnchor(
        left=anchor.left + dx,
        top=anchor.top + dy,
        right=anchor.right + dx,
        bottom=anchor.bottom + dy,
        default_x=anchor.default_x + dx,
        default_y=anchor.default_y + dy,
        default_width=anchor.default_width,
        source=anchor.source,
    )


def _project_hud_anchor(
    anchor: HudAnchor,
    from_rect: WindowRect,
    to_rect: WindowRect,
) -> HudAnchor:
    if from_rect.width == to_rect.width and from_rect.height == to_rect.height:
        return _offset_hud_anchor(
            anchor,
            to_rect.left - from_rect.left,
            to_rect.top - from_rect.top,
        )
    x_scale = to_rect.width / max(1, from_rect.width)
    y_scale = to_rect.height / max(1, from_rect.height)

    def project_x(value: int) -> int:
        return to_rect.left + int(round((value - from_rect.left) * x_scale))

    def project_y(value: int) -> int:
        return to_rect.top + int(round((value - from_rect.top) * y_scale))

    projected = HudAnchor(
        left=project_x(anchor.left),
        top=project_y(anchor.top),
        right=project_x(anchor.right),
        bottom=project_y(anchor.bottom),
        default_x=project_x(anchor.default_x),
        default_y=project_y(anchor.default_y),
        default_width=max(1, int(round(anchor.default_width * x_scale))),
        source=anchor.source,
    )
    if projected.width <= 0 or projected.height <= 0:
        return _offset_hud_anchor(
            anchor,
            to_rect.left - from_rect.left,
            to_rect.top - from_rect.top,
        )
    return projected


def _relative_anchor_signature(
    anchor: HudAnchor,
    rect: WindowRect,
) -> tuple[str, int, int, int, int, int, int, int]:
    return (
        anchor.source,
        anchor.left - rect.left,
        anchor.top - rect.top,
        anchor.right - rect.left,
        anchor.bottom - rect.top,
        anchor.default_x - rect.left,
        anchor.default_y - rect.top,
        anchor.default_width,
    )


def _fit_anchor_left(
    window_width: int,
    left_margin: int,
    right_margin: int,
    min_width: int,
) -> int:
    if window_width - left_margin - right_margin >= min_width:
        return left_margin
    return max(8, min(left_margin, window_width - right_margin - min_width))


def _set_native_window_geometry(
    window: tk.Tk | tk.Toplevel,
    geometry: tuple[int, int, int, int],
) -> bool:
    if not _env_flag(HUD_NATIVE_GEOMETRY_ENV):
        return False
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(window.winfo_id())
        if not hwnd:
            return False
        x, y, width, height = geometry
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        swp_noownerzorder = 0x0200
        flags = swp_nozorder | swp_noactivate | swp_noownerzorder
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        return bool(
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                0,
                int(x),
                int(y),
                int(width),
                int(height),
                flags,
            )
        )
    except Exception:
        return False


class CodexWindowLocator:
    """Locate the Codex desktop window using standard-library native hooks."""

    def __init__(self) -> None:
        self._impl: _BaseLocator
        if sys.platform.startswith("win"):
            self._impl = _WindowsCodexLocator()
        elif sys.platform == "darwin":
            self._impl = _MacCodexLocator()
        else:
            self._impl = _NullCodexLocator()

    def set_dpi_aware(self) -> None:
        self._impl.set_dpi_aware()

    def find(self) -> WindowRect | None:
        return self._impl.find()

    def is_active(self, rect: WindowRect, allowed_hwnds: set[int]) -> bool:
        return self._impl.is_active(rect, allowed_hwnds)

    def dock_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> tuple[int, int, int] | None:
        return self._impl.dock_geometry(target, rect, hud_height)

    def anchor_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        return self._impl.anchor_geometry(target, rect, hud_height)


class _BaseLocator:
    def set_dpi_aware(self) -> None:
        return None

    def find(self) -> WindowRect | None:
        return None

    def is_active(self, rect: WindowRect, allowed_hwnds: set[int]) -> bool:
        del rect, allowed_hwnds
        return True

    def dock_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> tuple[int, int, int] | None:
        del target, rect, hud_height
        return None

    def anchor_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        del target, rect, hud_height
        return None


class _NullCodexLocator(_BaseLocator):
    pass


class _MacCodexLocator(_BaseLocator):
    """Best-effort macOS locator using osascript and Accessibility metadata."""

    def find(self) -> WindowRect | None:
        script = r'''
tell application "System Events"
  set matches to application processes whose name contains "Codex"
  repeat with proc in matches
    if (count of windows of proc) > 0 then
      set win to window 1 of proc
      set pos to position of win
      set siz to size of win
      set mini to false
      try
        set mini to value of attribute "AXMinimized" of win
      end try
      return (item 1 of pos as text) & tab & (item 2 of pos as text) & tab & ¬
        (item 1 of siz as text) & tab & (item 2 of siz as text) & tab & ¬
        (name of proc as text) & tab & (mini as text)
    end if
  end repeat
end tell
return ""
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.8,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = result.stdout.strip()
        if not text:
            return None
        parts = text.split("\t")
        if len(parts) < 6:
            return None
        try:
            left = int(float(parts[0]))
            top = int(float(parts[1]))
            width = int(float(parts[2]))
            height = int(float(parts[3]))
        except ValueError:
            return None
        if width < 300 or height < 200:
            return None
        return WindowRect(
            left=left,
            top=top,
            right=left + width,
            bottom=top + height,
            title="Codex",
            process=parts[4],
            minimized=parts[5].strip().lower() == "true",
        )

    def is_active(self, rect: WindowRect, allowed_hwnds: set[int]) -> bool:
        del rect, allowed_hwnds
        script = (
            'tell application "System Events" to return '
            'name of first application process whose frontmost is true'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return "codex" in result.stdout.strip().lower()


class _WindowsCodexLocator(_BaseLocator):
    """Windows locator based on user32 EnumWindows and visibility checks."""

    def __init__(self) -> None:
        self.enabled = False
        self._last_hwnd = 0
        self._tracker = None
        self._cdp_probe = None
        self._last_cdp_anchor_status: dict[str, tuple[str, str]] = {}
        try:
            import ctypes
            from ctypes import wintypes
            from ..platforms.cdp_probe import CodexCdpProbe
            from ..platforms.windows_tracker import CodexWindowTracker

            self.ctypes = ctypes
            self.wintypes = wintypes
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32
            self._top_dom_anchors_enabled = _env_flag(HUD_CDP_DOM_ENV, default=True)
            self._dom_anchors_enabled = _env_flag(HUD_CDP_DOM_ENV, default=False)
            self._native_anchors_enabled = _env_flag(HUD_NATIVE_ANCHORS_ENV)
            if self._top_dom_anchors_enabled or self._dom_anchors_enabled:
                self._cdp_probe = CodexCdpProbe()
            self._tracker = CodexWindowTracker(enable_uia=self._native_anchors_enabled)
            self.enabled = True
            self._configure_api()
        except Exception:
            self.enabled = False

    def _configure_api(self) -> None:
        wintypes = self.wintypes
        ctypes = self.ctypes
        self.user32.EnumWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def set_dpi_aware(self) -> None:
        if not self.enabled:
            return
        if self._tracker is not None:
            try:
                self._tracker.set_dpi_aware()
                return
            except Exception:
                pass
        try:
            self.user32.SetProcessDPIAware()
        except Exception:
            pass

    def find(self) -> WindowRect | None:
        if not self.enabled:
            return None
        if self._tracker is not None and getattr(self._tracker, "enabled", False):
            try:
                snapshot = self._tracker.get_window_snapshot()
                if snapshot.status in {"minimized", "hidden", "cloaked"}:
                    rect = snapshot.window_rect
                    return WindowRect(
                        hwnd=snapshot.hwnd,
                        left=rect.left if rect is not None else 0,
                        top=rect.top if rect is not None else 0,
                        right=rect.right if rect is not None else 0,
                        bottom=rect.bottom if rect is not None else 0,
                        minimized=snapshot.status in {"minimized", "hidden", "cloaked"},
                    )
                if snapshot.status == "visible" and snapshot.window_rect is not None:
                    self._last_hwnd = snapshot.hwnd
                    rect = snapshot.window_rect
                    return WindowRect(
                        hwnd=snapshot.hwnd,
                        left=rect.left,
                        top=rect.top,
                        right=rect.right,
                        bottom=rect.bottom,
                        minimized=False,
                    )
            except Exception:
                pass
        cached = self._rect_for_hwnd(self._last_hwnd)
        if cached is not None:
            return cached
        ctypes = self.ctypes
        wintypes = self.wintypes
        candidates: list[WindowRect] = []
        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def callback(hwnd: int, _: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd):
                return True
            candidate = self._rect_for_hwnd(int(hwnd), verify_codex=True)
            if candidate is None:
                return True
            candidates.append(candidate)
            return True

        self.user32.EnumWindows(enum_proc_type(callback), 0)
        if not candidates:
            self._last_hwnd = 0
            return None
        best = sorted(candidates, key=self._score_window, reverse=True)[0]
        self._last_hwnd = best.hwnd
        return best

    def _rect_for_hwnd(
        self,
        hwnd: int,
        *,
        verify_codex: bool = False,
    ) -> WindowRect | None:
        if not hwnd or not self.user32.IsWindow(hwnd) or not self.user32.IsWindowVisible(hwnd):
            return None
        ctypes = self.ctypes
        wintypes = self.wintypes
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 300 or height < 200:
            return None
        title = self._window_text(hwnd)
        class_name = self._class_name(hwnd)
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = self._process_name(pid.value)
        if verify_codex or hwnd != self._last_hwnd:
            haystack = " ".join([title, class_name, process]).lower()
            if "codex" not in haystack:
                return None
        return WindowRect(
            hwnd=int(hwnd),
            title=title,
            process=process,
            class_name=class_name,
            left=int(rect.left),
            top=int(rect.top),
            right=int(rect.right),
            bottom=int(rect.bottom),
            minimized=bool(self.user32.IsIconic(hwnd)),
        )

    def is_active(self, rect: WindowRect, allowed_hwnds: set[int]) -> bool:
        if not self.enabled:
            return True
        if self._tracker is not None and getattr(self._tracker, "enabled", False):
            try:
                return self._tracker.is_active(rect.hwnd, allowed_hwnds)
            except Exception:
                pass
        if rect.minimized or self.user32.IsIconic(rect.hwnd):
            return False
        foreground = int(self.user32.GetForegroundWindow() or 0)
        if foreground == rect.hwnd or foreground in allowed_hwnds:
            return True
        hwnd_pid = self.wintypes.DWORD()
        foreground_pid = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(rect.hwnd, self.ctypes.byref(hwnd_pid))
        self.user32.GetWindowThreadProcessId(foreground, self.ctypes.byref(foreground_pid))
        if int(foreground_pid.value or 0) == int(hwnd_pid.value or 0):
            return True
        if int(foreground_pid.value or 0) == os.getpid():
            return True
        return "codex" in self._process_name(int(foreground_pid.value or 0)).lower()

    def _window_text(self, hwnd: int) -> str:
        length = int(self.user32.GetWindowTextLengthW(hwnd) or 0)
        if length <= 0:
            return ""
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = self.ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def _process_name(self, pid: int) -> str:
        if not pid:
            return ""
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = self.wintypes.DWORD(32768)
            buffer = self.ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, self.ctypes.byref(size)
            )
            if not ok:
                return ""
            return Path(buffer.value).name
        finally:
            self.kernel32.CloseHandle(handle)

    def _score_window(self, rect: WindowRect) -> tuple[int, int]:
        process = rect.process.lower()
        title = rect.title.lower()
        class_name = rect.class_name.lower()
        score = 0
        if process == "codex.exe":
            score += 100
        if "codex" in process:
            score += 60
        if title == "codex":
            score += 40
        elif "codex" in title:
            score += 25
        if "codex" in class_name:
            score += 15
        return score, rect.width * rect.height

    def dock_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> tuple[int, int, int] | None:
        if self._tracker is None or not getattr(self._tracker, "enabled", False):
            return None
        if not rect.hwnd:
            return None
        tracker_target = "title" if target == "top" else "input"
        try:
            snapshot = self._tracker.get_dock_snapshot(
                target=tracker_target,
                hud_height=hud_height,
            )
        except Exception:
            return None
        if snapshot.status != "visible" or snapshot.dock is None:
            return None
        if rect.hwnd and snapshot.hwnd and rect.hwnd != snapshot.hwnd:
            return None
        return snapshot.dock

    def anchor_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        cdp_anchor = self._cdp_anchor_geometry(target, rect, hud_height)
        if cdp_anchor is not None:
            return cdp_anchor
        if not self._native_anchors_enabled:
            return None
        if self._tracker is None or not getattr(self._tracker, "enabled", False):
            return None
        if not rect.hwnd:
            return None
        tracker_target = "title" if target == "top" else "input"
        try:
            snapshot = self._tracker.get_dock_snapshot(
                target=tracker_target,
                hud_height=hud_height,
            )
        except Exception:
            return None
        if snapshot.status != "visible" or snapshot.dock is None:
            return None
        if rect.hwnd and snapshot.hwnd and rect.hwnd != snapshot.hwnd:
            return None
        default_x, default_y, default_width = snapshot.dock
        if target == "top":
            title_bar = snapshot.title_bar
            if title_bar is None:
                return None
            return HudAnchor(
                left=int(default_x),
                top=int(title_bar.top),
                right=int(default_x + default_width),
                bottom=int(title_bar.bottom),
                default_x=int(default_x),
                default_y=int(default_y),
                default_width=int(default_width),
                source=str(snapshot.source or "uia"),
            )
        input_box = snapshot.input_box
        if input_box is None:
            return None
        return HudAnchor(
            left=int(input_box.left),
            top=int(input_box.top),
            right=int(input_box.right),
            bottom=int(input_box.bottom),
            default_x=int(default_x),
            default_y=int(default_y),
            default_width=int(default_width),
            source=str(snapshot.source or "uia"),
        )

    def _cdp_anchor_geometry(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        if target == "top":
            if not getattr(self, "_top_dom_anchors_enabled", False):
                self._log_cdp_anchor_status(target, "disabled", "top_probe=off")
                return None
        elif not self._dom_anchors_enabled:
            self._log_cdp_anchor_status(target, "disabled", "probe=off")
            return None
        if self._cdp_probe is None:
            self._log_cdp_anchor_status(target, "disabled", "probe=none")
            return None
        try:
            snapshot = self._cdp_probe.snapshot()
        except Exception:
            self._log_cdp_anchor_status(target, "failed", "snapshot_exception")
            return None
        if snapshot is None:
            status = str(getattr(self._cdp_probe, "last_status", "none"))
            error = str(getattr(self._cdp_probe, "last_error", ""))
            self._log_cdp_anchor_status(target, status, error)
            return None
        dpr = max(0.1, float(snapshot.device_pixel_ratio or 1.0))
        if target == "top":
            header = self._physical_rect_from_cdp(snapshot.header_rect, rect, dpr)
            title = self._physical_rect_from_cdp(snapshot.title_rect, rect, dpr)
            source = header or title
            if source is None or source.width < 160:
                self._log_cdp_anchor_status(
                    target,
                    "no-title-anchor",
                    f"session={snapshot.session_id or '-'} header={bool(header)} title={bool(title)}",
                )
                return None
            right_margin = max(
                TOP_ANCHOR_RIGHT_MIN,
                int(round(source.width * TOP_ANCHOR_RIGHT_RATIO)),
            )
            if title is not None and title.left >= source.left and title.left < source.right:
                left = title.left
            else:
                left_margin = max(
                    TOP_ANCHOR_LEFT_MIN,
                    int(round(source.width * TOP_ANCHOR_LEFT_RATIO)),
                )
                left = source.left + _fit_anchor_left(
                    source.width,
                    left_margin,
                    right_margin,
                    TOP_ANCHOR_MIN_WIDTH,
                )
            right = max(left + 1, source.right - right_margin)
            y = source.top + max(0, (source.height - max(1, hud_height)) // 2)
            self._log_cdp_anchor_status(
                target,
                "ok",
                f"source=cdp:title session={snapshot.session_id or '-'} "
                f"header={bool(header)} title={bool(title)} dpr={dpr:.2f}",
            )
            return HudAnchor(
                left=left,
                top=source.top,
                right=right,
                bottom=source.bottom,
                default_x=left,
                default_y=y,
                default_width=max(1, right - left),
                source="cdp:title",
            )

        composer = self._physical_rect_from_cdp(snapshot.composer_rect, rect, dpr)
        if composer is None or composer.width < 180 or composer.height < 24:
            self._log_cdp_anchor_status(
                target,
                "no-composer-anchor",
                f"session={snapshot.session_id or '-'} composer={bool(composer)}",
            )
            return None
        self._log_cdp_anchor_status(
            target,
            "ok",
            f"source=cdp:composer session={snapshot.session_id or '-'} "
            f"composer=True dpr={dpr:.2f}",
        )
        return HudAnchor(
            left=composer.left,
            top=composer.top,
            right=composer.right,
            bottom=composer.bottom,
            default_x=composer.left,
            default_y=max(0, composer.top - max(1, hud_height)),
            default_width=max(1, composer.width),
            source="cdp:composer",
        )

    def _log_cdp_anchor_status(self, target: str, status: str, detail: str) -> None:
        signature = (str(status), str(detail))
        if self._last_cdp_anchor_status.get(target) == signature:
            return
        self._last_cdp_anchor_status[target] = signature
        _HUD_GEOMETRY_LOGGER.info(
            "cdp_anchor_status target=%s status=%s detail=%s",
            target,
            status,
            detail,
        )

    @staticmethod
    def _physical_rect_from_cdp(
        cdp_rect: Any,
        window_rect: WindowRect,
        device_pixel_ratio: float,
    ) -> WindowRect | None:
        if cdp_rect is None:
            return None
        left = window_rect.left + int(round(float(cdp_rect.left) * device_pixel_ratio))
        top = window_rect.top + int(round(float(cdp_rect.top) * device_pixel_ratio))
        right = window_rect.left + int(round(float(cdp_rect.right) * device_pixel_ratio))
        bottom = window_rect.top + int(round(float(cdp_rect.bottom) * device_pixel_ratio))
        left = max(window_rect.left, min(left, window_rect.right))
        top = max(window_rect.top, min(top, window_rect.bottom))
        right = max(window_rect.left, min(right, window_rect.right))
        bottom = max(window_rect.top, min(bottom, window_rect.bottom))
        if right - left <= 0 or bottom - top <= 0:
            return None
        return WindowRect(left=left, top=top, right=right, bottom=bottom, hwnd=window_rect.hwnd)


def _short_num(value: int | None) -> str:
    amount = int(value or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f"{sign}{amount / 1_000_000:.1f}M"
    if amount >= 10_000:
        return f"{sign}{amount / 1_000:.0f}k"
    return f"{sign}{amount:,}"


def _format_money(value: float | None) -> str:
    amount = max(0.0, float(value or 0.0))
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


def _format_realtime_money(value: float | None, estimated: bool) -> str:
    return f"{'~' if estimated else ''}{_format_money(value)}"


def _format_fixed_money(value: float | None, estimated: bool) -> str:
    amount = max(0.0, float(value or 0.0))
    marker = "~" if estimated else ""
    if amount < 1:
        return f"{marker}${amount:.3f}"
    if amount < 100:
        return f"{marker}${amount:.2f}"
    return f"{marker}${amount:.1f}"


def _fixed_token_total(value: int | None) -> str:
    return _short_num(value)


def _format_usage_money(tokens: int | None, cost: float | None) -> str:
    return f"{_short_num(tokens)}/{_format_money(cost)}"


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M:%S")


def _format_start(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M")


def _status_label(value: str) -> str:
    labels = {
        "starting": "启动中",
        "waiting": "等待日志",
        "missing": "未找到",
        "error": "出错",
        "parsed": "实时",
        "live": "实时",
        "idle": "空闲",
        "stale": "历史",
    }
    return labels.get(value, value)


def _activity_label(value: str) -> str:
    labels = {
        "idle": "空闲",
        "user": "用户输入",
        "agent": "助手消息",
        "tool call": "调用工具",
        "tool output": "工具返回",
        "assistant": "助手输出",
        "confirmed": "Token确认",
    }
    return labels.get(value, value)


def _request_status_label(value: str) -> str:
    labels = {
        "waiting": "等待",
        "running": "运行中",
        "confirmed": "已确认",
        "disabled": "已关闭",
        "error": "出错",
    }
    return labels.get(value, value)


def _gap_label(value: str) -> str:
    labels = {
        "user_wait": "等用户",
        "tool_wait": "等工具",
        "idle_between_tasks": "任务间空档",
        "model_startup": "模型启动",
        "model_or_idle": "模型思考",
        "other_gap": "执行等待",
    }
    if value == "无":
        return value
    for key, label in labels.items():
        value = value.replace(key, label)
    return value


def _gap_reason_text(snapshot: ParsedSession) -> str:
    detail = snapshot.slow.longest_gap_detail
    if detail is None:
        return snapshot.slow.longest_gap
    category = _gap_label(detail.category)
    return f"{detail.duration_seconds:.1f}s（{category}）"


def _current_gap_text(snapshot: ParsedSession) -> str:
    if snapshot.slow.current_gap_active:
        return f"距最后事件 {snapshot.slow.current_gap}"
    return snapshot.slow.current_gap


def _copyable_tool_command(snapshot: ParsedSession) -> str | None:
    call = snapshot.slow.slowest_tool_call
    if call is None:
        return None
    raw_args = (call.args or "").strip()
    if not raw_args:
        return None
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return raw_args
    if isinstance(payload, dict):
        command = payload.get("command")
        if command:
            return str(command)
    return raw_args


def _copyable_gap_detail(snapshot: ParsedSession) -> str | None:
    detail = snapshot.slow.longest_gap_detail
    if detail is None:
        return None
    return "\n".join(
        [
            f"类型: {_gap_label(detail.category)}",
            f"时长: {detail.duration_seconds:.1f}s",
            f"开始事件: {detail.from_event}",
            f"结束事件: {detail.to_event}",
            f"行号: {detail.start_line} -> {detail.end_line}",
        ]
    )


def _compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _wrap_long_display_tokens(value: Any, chunk: int = 24) -> str:
    text = str(value or "")

    def split_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if len(token) <= chunk:
            return token
        return "\n".join(
            token[index : index + chunk] for index in range(0, len(token), chunk)
        )

    return LONG_DISPLAY_TOKEN_RE.sub(split_token, text)


def _top_expanded_header_title(snapshot: ParsedSession) -> str:
    title = _compact(snapshot.session_title, 72)
    return title or TOP_EXPANDED_HEADER_FALLBACK


def _display_tokens(
    snapshot: ParsedSession,
) -> tuple[int | None, bool, int | None, bool, int | None, bool, int | None, bool]:
    request = snapshot.request
    input_tokens = request.input_tokens
    input_estimated = False
    if input_tokens is None and request.estimated:
        input_tokens = (
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens
        )
        input_estimated = input_tokens > 0

    output_tokens = request.output_tokens
    output_estimated = request.estimated and output_tokens is not None
    reasoning_tokens = request.reasoning_tokens
    total_tokens = request.total_tokens
    total_estimated = request.estimated or input_estimated
    if input_tokens is not None and (request.estimated or not total_tokens):
        total_tokens = input_tokens + int(output_tokens or 0)
        total_estimated = True

    return (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        False,
        total_tokens,
        total_estimated,
    )


def _display_cached_tokens(
    snapshot: ParsedSession,
    input_tokens: int | None,
    input_estimated: bool,
) -> tuple[int | None, bool]:
    cached_tokens = snapshot.request.cached_tokens
    cached_estimated = snapshot.request.estimated and cached_tokens is not None
    if cached_tokens is None and input_tokens is not None:
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens))
        cached_estimated = input_estimated or snapshot.request.estimated
    return cached_tokens, cached_estimated


def _format_rate_marker(value: float | None, estimated: bool) -> str:
    if value is None:
        return f"{CACHE_HIT_RATE_SYMBOL}-"
    clamped = max(0.0, min(float(value), 1.0))
    return f"{CACHE_HIT_RATE_SYMBOL}{'~' if estimated else ''}{clamped:.0%}"


def _session_cache_hit_rate(snapshot: ParsedSession) -> tuple[float | None, bool]:
    input_tokens = int(snapshot.confirmed.cumulative_input or 0)
    cached_tokens = int(snapshot.confirmed.cumulative_cached or 0)
    estimated = False
    if snapshot.request.status == "running" or input_tokens <= 0:
        (
            request_input_tokens,
            input_estimated,
            _output_tokens,
            _output_estimated,
            _reasoning_tokens,
            _reasoning_estimated,
            _total_tokens,
            _total_estimated,
        ) = _display_tokens(snapshot)
        request_cached_tokens, cached_estimated = _display_cached_tokens(
            snapshot,
            request_input_tokens,
            input_estimated,
        )
        if request_input_tokens is not None and int(request_input_tokens) > 0:
            request_input = int(request_input_tokens)
            request_cached = int(request_cached_tokens or 0)
            if snapshot.request.status == "running":
                input_tokens += request_input
                cached_tokens += request_cached
            else:
                input_tokens = request_input
                cached_tokens = request_cached
            estimated = input_estimated or cached_estimated or snapshot.request.estimated
    if input_tokens <= 0:
        return None, estimated
    cached_tokens = max(0, min(cached_tokens, input_tokens))
    return cached_tokens / max(1, input_tokens), estimated


def _session_cache_hit_rate_label(snapshot: ParsedSession) -> str:
    ratio, estimated = _session_cache_hit_rate(snapshot)
    return _format_rate_marker(ratio, estimated)


def _top_session_usage_summary(snapshot: ParsedSession, session_cost: float | None = None) -> str:
    total_tokens = int(snapshot.confirmed.cumulative_total or 0)
    total_cost = _session_cost(snapshot) if session_cost is None else session_cost
    if snapshot.request.status == "running":
        (
            _input_tokens,
            _input_estimated,
            _output_tokens,
            _output_estimated,
            _reasoning_tokens,
            _reasoning_estimated,
            request_total_tokens,
            _total_estimated,
        ) = _display_tokens(snapshot)
        total_tokens += int(request_total_tokens or 0)
        request_cost, _request_cost_estimated = _request_cost(snapshot)
        if request_cost is not None:
            total_cost = float(total_cost or 0.0) + float(request_cost)
    return f"本会话 {_format_usage_money(total_tokens, total_cost)}/{_session_cache_hit_rate_label(snapshot)}"


def _top_cache_progress_label(snapshot: ParsedSession) -> str:
    label = _session_cache_hit_rate_label(snapshot)
    if label.startswith(CACHE_HIT_RATE_SYMBOL):
        label = label[len(CACHE_HIT_RATE_SYMBOL) :]
    return f"缓存命中 {label}"


def _budget_progress_ratio(cost: float | None, limit: float | None) -> float:
    amount = max(0.0, float(cost or 0.0))
    budget = max(0.0, float(limit or 0.0))
    if budget <= 0.0:
        return 0.0
    return _clamp_progress_ratio(amount / budget)


def _budget_limit_text(limit: float | None) -> str:
    return f"总 {_format_money(limit)}"


def _top_collapsed_progress_metrics(snapshot: ParsedSession) -> list[TopHudProgressMetric]:
    return [
        TopHudProgressMetric(
            label=_top_session_usage_summary(snapshot),
            ratio=0.0,
            fill=HUD_PROGRESS_TRACK,
            fill_end=HUD_PROGRESS_TRACK,
            fill_text=HUD_TEXT,
            track_text=HUD_TEXT,
        ),
        TopHudProgressMetric(
            label=f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}",
            ratio=_budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
            fill=HUD_PROGRESS_DAY,
            fill_end=HUD_PROGRESS_DAY_END,
            fill_text=HUD_PROGRESS_DAY_TEXT,
            right_text=_budget_limit_text(snapshot.daily_limit_usd),
        ),
        TopHudProgressMetric(
            label=f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}",
            ratio=_budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
            fill=HUD_PROGRESS_WEEK,
            fill_end=HUD_PROGRESS_WEEK_END,
            fill_text=HUD_PROGRESS_WEEK_TEXT,
            right_text=_budget_limit_text(snapshot.weekly_limit_usd),
        ),
    ]


def _top_cache_progress_metric(snapshot: ParsedSession) -> TopHudProgressMetric:
    cache_ratio, _cache_estimated = _session_cache_hit_rate(snapshot)
    return TopHudProgressMetric(
        label=_top_cache_progress_label(snapshot),
        ratio=cache_ratio if cache_ratio is not None else 0.0,
        fill=HUD_PROGRESS_CACHE,
        fill_end=HUD_PROGRESS_CACHE_END,
        fill_text=HUD_PROGRESS_CACHE_TEXT,
    )


def _top_budget_progress_metrics(snapshot: ParsedSession) -> list[TopHudProgressMetric]:
    return [
        TopHudProgressMetric(
            label=f"本日累计 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}",
            ratio=_budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
            fill=HUD_PROGRESS_DAY,
            fill_end=HUD_PROGRESS_DAY_END,
            fill_text=HUD_PROGRESS_DAY_TEXT,
            right_text=_budget_limit_text(snapshot.daily_limit_usd),
        ),
        TopHudProgressMetric(
            label=f"本周累计 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}",
            ratio=_budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
            fill=HUD_PROGRESS_WEEK,
            fill_end=HUD_PROGRESS_WEEK_END,
            fill_text=HUD_PROGRESS_WEEK_TEXT,
            right_text=_budget_limit_text(snapshot.weekly_limit_usd),
        ),
    ]


def _top_budget_progress_meta(snapshot: ParsedSession) -> str:
    parts = [
        f"日窗起点 {_format_start(snapshot.day_start)}",
        f"周窗起点 {_format_start(snapshot.week_start)}",
    ]
    if snapshot.week_before_today_cost_usd > 0:
        parts.append(
            "本周拆分 "
            f"今日前 {_format_usage_money(snapshot.week_before_today_tokens, snapshot.week_before_today_cost_usd)}"
            f" + 当前日窗 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}"
        )
    if snapshot.week_adjustment_usd > 0:
        parts.append(f"人工补充 {_format_money(snapshot.week_adjustment_usd)}")
    return "  |  ".join(parts)


def _budget_warning_summary(snapshot: ParsedSession) -> str:
    if snapshot.budget_error:
        return snapshot.budget_error
    if not snapshot.budget_warnings:
        return "提醒  暂无额度提醒"
    messages: list[str] = []
    for warning in snapshot.budget_warnings:
        if warning.startswith("日额度已用") and "超过 " in warning and snapshot.daily_limit_usd > 0:
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"日已用 {snapshot.today_cost_usd / snapshot.daily_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        if warning.startswith("周额度已用") and "超过 " in warning and snapshot.weekly_limit_usd > 0:
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"周已用 {snapshot.week_cost_usd / snapshot.weekly_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        messages.append(warning)
    return "提醒  " + "；".join(messages)


def _round_cache_hit_rate_label(item: RequestRound) -> str:
    input_tokens = item.input_tokens
    if input_tokens is None or int(input_tokens) <= 0:
        return _format_rate_marker(None, item.estimated)
    cached_tokens = max(0, min(int(item.cached_tokens or 0), int(input_tokens)))
    return _format_rate_marker(cached_tokens / max(1, int(input_tokens)), item.estimated)


def _request_cost(snapshot: ParsedSession) -> tuple[float | None, bool]:
    request = snapshot.request
    if request.cost_usd is not None and not request.estimated:
        return request.cost_usd, False
    input_tokens = request.input_tokens
    cached_tokens = request.cached_tokens
    output_tokens = request.output_tokens or 0
    if input_tokens is None or request.estimated:
        input_tokens = max(
            int(input_tokens or 0),
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens,
        )
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens or 0))
    cost = _COST_ESTIMATOR.calculate(
        request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        request.reasoning_tokens or 0,
    )
    return cost, True


def _round_from_snapshot(snapshot: ParsedSession) -> RequestRound:
    (
        input_tokens,
        _input_estimated,
        output_tokens,
        _output_estimated,
        reasoning_tokens,
        _reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = _display_tokens(snapshot)
    cost, cost_estimated = _request_cost(snapshot)
    return RequestRound(
        index=1,
        status=snapshot.request.status,
        model=snapshot.request.model,
        input_tokens=input_tokens,
        cached_tokens=snapshot.request.cached_tokens
        if snapshot.request.cached_tokens is not None
        else min(snapshot.confirmed.last_cached, int(input_tokens or 0)),
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        estimated=snapshot.request.estimated or total_estimated or cost_estimated,
        cost_usd=cost,
        started_at=snapshot.request.started_at,
        completed_at=snapshot.request.completed_at,
    )


def _task_rows(snapshot: ParsedSession) -> list[RequestRound]:
    return snapshot.request_history or [_round_from_snapshot(snapshot)]


def _task_total(snapshot: ParsedSession) -> tuple[int, int, int, int, int, float | None, bool]:
    rows = _task_rows(snapshot)
    input_tokens = sum(int(item.input_tokens or 0) for item in rows)
    cached_tokens = sum(int(item.cached_tokens or 0) for item in rows)
    output_tokens = sum(int(item.output_tokens or 0) for item in rows)
    reasoning_tokens = sum(int(item.reasoning_tokens or 0) for item in rows)
    total_tokens = sum(int(item.total_tokens or 0) for item in rows)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    cost = 0.0
    has_cost = False
    estimated = False
    for item in rows:
        item_cost = item.cost_usd
        if item_cost is None:
            item_cost = _COST_ESTIMATOR.calculate(
                item.model or snapshot.request.model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
            )
            estimated = True
        if item_cost is not None:
            cost += item_cost
            has_cost = True
        estimated = estimated or item.estimated or item.status == "running"
    return (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost if has_cost else None,
        estimated,
    )


def _session_cost(snapshot: ParsedSession) -> float | None:
    if snapshot.confirmed.cumulative_cost_usd is not None:
        return snapshot.confirmed.cumulative_cost_usd
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        snapshot.confirmed.cumulative_input,
        snapshot.confirmed.cumulative_cached,
        snapshot.confirmed.cumulative_output,
        snapshot.confirmed.cumulative_reasoning,
    )


def _budget_status(snapshot: ParsedSession) -> str:
    if snapshot.budget_error:
        return "预算不可用"
    if snapshot.budget_warnings:
        tags: list[str] = []
        for warning in snapshot.budget_warnings:
            if warning.startswith("日") and "超过 " in warning:
                tags.append("日>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            elif warning.startswith("周") and "超过 " in warning:
                tags.append("周>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            else:
                tags.append("额度")
        return "提醒 " + "/".join(tags)
    return _status_label(snapshot.status)


def _request_counter(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = _display_tokens(snapshot)
    cost, cost_estimated = _request_cost(snapshot)
    cached_tokens, cached_estimated = _display_cached_tokens(
        snapshot,
        input_tokens,
        input_estimated,
    )
    return " ".join(
        [
            f"↑{'~' if input_estimated else ''}{_short_num(input_tokens)}",
            f"↻{'~' if cached_estimated else ''}{_short_num(cached_tokens)}",
            f"↓{'~' if output_estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if reasoning_estimated else ''}{_short_num(reasoning_tokens)}",
            f"∑{'~' if total_estimated else ''}{_short_num(total_tokens)}",
            _format_realtime_money(cost, cost_estimated),
        ]
    )


def _request_total_line(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost,
        estimated,
    ) = _task_total(snapshot)
    return " ".join(
        [
            _format_fixed_money(cost, estimated),
            f"∑{_fixed_token_total(total_tokens)}",
            f"↑{'~' if estimated else ''}{_short_num(input_tokens)}",
            f"↻{'~' if estimated else ''}{_short_num(cached_tokens)}",
            _session_cache_hit_rate_label(snapshot),
            f"↓{'~' if estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if estimated else ''}{_short_num(reasoning_tokens)}",
        ]
    )


def _round_is_running(item: RequestRound) -> bool:
    return item.status == "running" and item.completed_at is None and item.started_at is not None


def _round_elapsed_text(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return "--:--:--"
    if started_at.tzinfo is None:
        current = (now or datetime.now()).replace(tzinfo=None)
    else:
        current = (now or datetime.now().astimezone()).astimezone(started_at.tzinfo)
    elapsed_seconds = max(0, int((current - started_at).total_seconds()))
    return f"{elapsed_seconds}s".rjust(8)


def _round_time_text(
    item: RequestRound,
    *,
    now: datetime | None = None,
) -> str:
    if _round_is_running(item):
        return _round_elapsed_text(item.started_at, now=now)
    time_source = item.completed_at or item.started_at
    return "--:--:--" if time_source is None else time_source.astimezone().strftime("%H:%M:%S")


def _round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    index_width: int | None = None,
    money_width: int | None = None,
    total_width: int | None = None,
    now: datetime | None = None,
) -> str:
    cost = item.cost_usd
    estimated = item.estimated or cost is None
    if cost is None:
        cost = _COST_ESTIMATOR.calculate(
            item.model or fallback_model,
            item.input_tokens or 0,
            item.cached_tokens or 0,
            item.output_tokens or 0,
            item.reasoning_tokens or 0,
        )
    time_text = _round_time_text(item, now=now)
    index_text = str(item.index)
    money_text = _format_fixed_money(cost, estimated)
    total_text = _fixed_token_total(item.total_tokens)
    if index_width is not None:
        index_text = index_text.rjust(index_width)
    if money_width is not None:
        money_text = money_text.rjust(money_width)
    if total_width is not None:
        total_text = total_text.rjust(total_width)
    return (
        f"#{index_text} {money_text} "
        f"∑{total_text} {time_text} "
        f"↑{_short_num(item.input_tokens)} ↻{_short_num(item.cached_tokens)} "
        f"{_round_cache_hit_rate_label(item)} "
        f"↓{_short_num(item.output_tokens)} ◇{_short_num(item.reasoning_tokens)}"
    )


def _round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
) -> tuple[int, int, int]:
    index_width = max((len(str(item.index)) for item in rows), default=1)
    money_width = 1
    total_width = 1
    for item in rows:
        cost = item.cost_usd
        estimated = item.estimated or cost is None
        if cost is None:
            cost = _COST_ESTIMATOR.calculate(
                item.model or fallback_model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
            )
        money_width = max(money_width, len(_format_fixed_money(cost, estimated)))
        total_width = max(total_width, len(_fixed_token_total(item.total_tokens)))
    return index_width, money_width, total_width


class TokenHudWindow:
    """Two-window HUD: top session/budget bar and bottom request bar."""

    def __init__(
        self,
        compact: bool = False,
        follow_ms: int = FOLLOW_ACTIVE_MS,
        hide_until_attached: bool = False,
        tombstone_follow_ms: int = FOLLOW_TOMBSTONE_MS,
        user_settings_store: UserConfigStore | None = None,
        update_manager: AutoUpdateManager | None = None,
    ) -> None:
        self.compact = compact
        self.follow_ms = max(16, int(follow_ms))
        self.inactive_follow_ms = max(self.follow_ms, FOLLOW_INACTIVE_MS)
        self.tombstone_follow_ms = max(50, int(tombstone_follow_ms))
        self.hide_until_attached = bool(hide_until_attached)
        self.settings_store = HudSettingsStore()
        self.settings = self.settings_store.load()
        self.user_settings_store = user_settings_store or UserConfigStore()
        self.user_settings = self.user_settings_store.load()
        self._settings_dialog: tk.Toplevel | None = None
        self._settings_entries: dict[str, SettingsEntry] = {}
        self._settings_price_rows: list[dict[str, tk.Entry]] = []
        self._settings_body_frame: tk.Frame | None = None
        self._settings_actions_frame: tk.Frame | None = None
        self._settings_status_label: tk.Label | None = None
        self._settings_tab_buttons: dict[str, tk.Button] = {}
        self._settings_canvas: tk.Canvas | None = None
        self._settings_prices_body: tk.Frame | None = None
        self._settings_support_images: list[tk.PhotoImage] = []
        self._settings_update_check_button: tk.Button | None = None
        self._settings_update_install_button: tk.Button | None = None
        self._settings_loading_layer: tk.Frame | None = None
        self._settings_loading_kicker_label: tk.Label | None = None
        self._settings_loading_title_label: tk.Label | None = None
        self._settings_loading_body_label: tk.Label | None = None
        self._settings_loading_track: tk.Canvas | None = None
        self._settings_loading_indicator: int | None = None
        self._settings_loading_glow: int | None = None
        self._settings_loading_anim_job: str | None = None
        self._settings_update_poll_job: str | None = None
        self._settings_update_flow = ""
        self._settings_loading_position = 0
        self._settings_loading_direction = 1
        self._settings_display_mode_touched = False
        self._settings_configured_display_mode = str(self.user_settings.display_mode)
        self._settings_active_tab = "settings"
        self._settings_pending_tab = ""
        self._settings_build_job: str | None = None
        self._settings_prewarm_job: str | None = None
        self._mode_switch_request = ""
        self._restart_codex_for_renderer = False
        self._geometry_log_path = configure_hud_geometry_logging()
        self._use_dom_anchors = _env_flag(HUD_CDP_DOM_ENV, default=False)
        self._use_top_dom_anchors = _env_flag(HUD_CDP_DOM_ENV, default=True)
        self._use_native_anchors = _env_flag(HUD_NATIVE_ANCHORS_ENV)
        self.update_manager = update_manager
        self._update_state = AutoUpdateState(current_version=__version__)
        self._top_update_button: tk.Button | None = None
        self.locator = CodexWindowLocator()
        self.locator.set_dpi_aware()
        _HUD_GEOMETRY_LOGGER.info(
            "hud_started dom_anchors=%s native_anchors=%s follow_ms=%s settings_path=%s log_path=%s",
            self._use_dom_anchors,
            self._use_native_anchors,
            self.follow_ms,
            self.settings_store.path,
            self._geometry_log_path,
        )
        self.top_geometry = DockGeometry(
            top=TOP_DOCK_TOP,
            left=TOP_DOCK_LEFT,
            right=TOP_DOCK_RIGHT,
            height=TOP_DOCK_HEIGHT if not compact else min(TOP_DOCK_HEIGHT, 30),
            expanded_height=TOP_DOCK_EXPANDED_HEIGHT,
            min_width=TOP_DOCK_MIN_WIDTH,
        )
        self.request_geometry = DockGeometry(
            top=0,
            left=REQUEST_DOCK_LEFT,
            right=REQUEST_DOCK_RIGHT,
            height=REQUEST_DOCK_HEIGHT,
            expanded_height=REQUEST_DOCK_EXPANDED_HEIGHT,
            min_width=REQUEST_DOCK_MIN_WIDTH,
            bottom=REQUEST_DOCK_BOTTOM,
            fixed_width=REQUEST_DOCK_WIDTH,
        )

        outside_color = _window_outside_color()
        self.root = tk.Tk()
        self.root.title("codex-usage-hud")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=outside_color)
        self._configure_transparent_window(self.root, outside_color)
        self.root.bind("<Escape>", self._close)
        self._bind_window_interactions(self.root, "top")
        self._exit_reason = ""

        self.top_expanded = False
        self.request_expanded = False
        self._attached = False
        self._hidden_for_minimized = False
        self._hidden_reason = ""
        self._tombstoned = False
        self._focus_state_active: bool | None = None
        self._last_rect: WindowRect | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._drag_window: tk.Toplevel | tk.Tk | None = None
        self._move_target = ""
        self._resize_target = ""
        self._resize_window: tk.Toplevel | tk.Tk | None = None
        self._resize_edge = ""
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._resize_start_left = 0
        self._resize_start_top = 0
        self._resize_start_width = 0
        self._resize_start_height = 0
        self._press_at: tuple[int, int] | None = None
        self._press_target = ""
        self._top_manual_position: tuple[int, int] | None = None
        self._request_manual_position: tuple[int, int] | None = None
        self._last_geometry_clamp: dict[
            str,
            tuple[int, int, int, int, int, int, int, int, str],
        ] = {}
        self._last_applied_geometry: dict[str, tuple[int, int, int, int]] = {}
        self._last_geometry_backend: dict[str, str] = {}
        self._native_anchor_candidates: dict[str, _NativeAnchorState] = {}
        self._stable_native_anchors: dict[str, _NativeAnchorState] = {}
        self._last_anchor_decisions: dict[str, tuple[str, str, str, str]] = {}
        self._last_budget_log_signature: tuple[int, int, int, str, str] | None = None
        self._snapshot = ParsedSession(status="waiting")
        self._top_collapsed_width_override: int | None = None
        self._ui_interaction_hold_until = 0.0
        self._top_rebuild_job: str | None = None
        self._request_rebuild_job: str | None = None
        self._follow_job: str | None = None

        self.top_labels: dict[str, tk.Misc] = {}
        self.request_label: tk.Misc | None = None
        self.request_text: tk.Text | None = None

        self._rebuild_top_ui()

        self.request_root = tk.Toplevel(self.root)
        self.request_root.title("codex-usage-hud request")
        self.request_root.overrideredirect(True)
        self.request_root.attributes("-topmost", True)
        self.request_root.configure(bg=outside_color)
        self._configure_transparent_window(self.request_root, outside_color)
        self.request_root.bind("<Escape>", self._close)
        self._bind_window_interactions(self.request_root, "request")
        self._rebuild_request_ui()
        self._set_alpha(self.root, 0.94)
        self._set_alpha(self.request_root, 0.74)
        self._apply_free_defaults()
        self._schedule_settings_dialog_prewarm()
        if self.hide_until_attached:
            self._enter_tombstone("waiting")
            self.sync_codex_window()
        self._follow_job = self.root.after(self.follow_ms, self._follow_codex_window)

    def _move_handle(self, parent: tk.Misc, target: str, window: tk.Tk | tk.Toplevel) -> tk.Label:
        label = tk.Label(
            parent,
            text="≡",
            bg=str(parent.cget("bg")),
            fg="#8EA0B5",
            font=("Consolas", 10, "bold"),
            cursor="fleur",
            width=2,
        )
        setattr(label, "_hud_handle", True)
        label.bind(
            "<ButtonPress-1>",
            lambda event, t=target, w=window: self._start_move(event, t, w),
        )
        label.bind("<B1-Motion>", self._move_window)
        label.bind("<ButtonRelease-1>", self._finish_move)
        return label

    @staticmethod
    def _configure_transparent_window(
        window: tk.Tk | tk.Toplevel,
        outside_color: str,
    ) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            window.wm_attributes("-transparentcolor", outside_color)
        except tk.TclError:
            return

    def _bind_window_interactions(
        self,
        window: tk.Tk | tk.Toplevel,
        target: str,
    ) -> None:
        window.bind(
            "<Motion>",
            lambda event, t=target, w=window: self._handle_window_motion(event, t, w),
            add="+",
        )
        window.bind(
            "<Leave>",
            lambda event, w=window: self._handle_window_leave(event, w),
            add="+",
        )
        window.bind(
            "<ButtonPress-1>",
            lambda event, t=target, w=window: self._handle_window_press(event, t, w),
            add="+",
        )
        window.bind(
            "<B1-Motion>",
            lambda event, t=target, w=window: self._handle_window_drag(event, t, w),
            add="+",
        )
        window.bind(
            "<ButtonRelease-1>",
            lambda event, t=target, w=window: self._handle_window_release(event, t, w),
            add="+",
        )

    @staticmethod
    def _point_in_hit_rect(
        x: int,
        y: int,
        *,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> bool:
        return left <= x < right and top <= y < bottom

    def _resize_edge_from_pointer(
        self,
        window: tk.Tk | tk.Toplevel,
        target: str,
        x_root: int,
        y_root: int,
    ) -> str:
        try:
            local_x = int(x_root) - int(window.winfo_rootx())
            local_y = int(y_root) - int(window.winfo_rooty())
        except Exception:
            return ""
        width = max(1, int(window.winfo_width()))
        height = max(1, int(window.winfo_height()))
        edge = RESIZE_EDGE_HIT_SIZE
        corner = RESIZE_CORNER_HIT_SIZE
        edge_hit = max(1, edge - 2)
        corner_hit = max(1, corner - 2)
        if (
            self.top_expanded if target == "top" else self.request_expanded
        ):
            if target == "top":
                if self._point_in_hit_rect(
                    local_x,
                    local_y,
                    left=2,
                    top=max(2, height - corner),
                    right=2 + corner_hit,
                    bottom=max(2, height - 2),
                ):
                    return "bottom-left"
                if self._point_in_hit_rect(
                    local_x,
                    local_y,
                    left=max(2, width - corner),
                    top=max(2, height - corner),
                    right=max(2, width - 2),
                    bottom=max(2, height - 2),
                ):
                    return "bottom-right"
            else:
                if self._point_in_hit_rect(
                    local_x,
                    local_y,
                    left=2,
                    top=2,
                    right=2 + corner_hit,
                    bottom=2 + corner_hit,
                ):
                    return "top-left"
                if self._point_in_hit_rect(
                    local_x,
                    local_y,
                    left=max(2, width - corner),
                    top=2,
                    right=max(2, width - 2),
                    bottom=2 + corner_hit,
                ):
                    return "top-right"
        if self._point_in_hit_rect(
            local_x,
            local_y,
            left=2,
            top=2,
            right=2 + edge_hit,
            bottom=max(2, height - 2),
        ):
            return "left"
        if self._point_in_hit_rect(
            local_x,
            local_y,
            left=max(2, width - edge),
            top=2,
            right=max(2, width - 2),
            bottom=max(2, height - 2),
        ):
            return "right"
        return ""

    @staticmethod
    def _resize_cursor(edge: str) -> str:
        if edge == "left" or edge == "right":
            return "sb_h_double_arrow"
        if edge == "top-left" or edge == "bottom-right":
            return "size_nw_se"
        if edge == "top-right" or edge == "bottom-left":
            return "size_ne_sw"
        return ""

    @staticmethod
    def _blocks_window_interaction(widget: object) -> bool:
        return bool(
            getattr(widget, "_hud_handle", False)
            or isinstance(widget, (tk.Button, tk.Text, HudScrollbar, tk.Scrollbar))
        )

    def _set_window_cursor(self, window: tk.Tk | tk.Toplevel, cursor: str) -> None:
        try:
            window.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _handle_window_motion(
        self,
        event: Any,
        target: str,
        window: tk.Tk | tk.Toplevel,
    ) -> None:
        if self._resize_window is window:
            return
        if self._blocks_window_interaction(getattr(event, "widget", None)):
            self._set_window_cursor(window, "")
            return
        edge = self._resize_edge_from_pointer(window, target, event.x_root, event.y_root)
        self._set_window_cursor(window, self._resize_cursor(edge))

    def _handle_window_leave(
        self,
        event: Any,
        window: tk.Tk | tk.Toplevel,
    ) -> None:
        del event
        if self._resize_window is window:
            return
        self._set_window_cursor(window, "")

    def _handle_window_press(
        self,
        event: Any,
        target: str,
        window: tk.Tk | tk.Toplevel,
    ) -> str | None:
        if self._blocks_window_interaction(getattr(event, "widget", None)):
            self._set_window_cursor(window, "")
            return None
        edge = self._resize_edge_from_pointer(window, target, event.x_root, event.y_root)
        if edge:
            self._set_window_cursor(window, self._resize_cursor(edge))
            return self._start_resize(event, target, window, edge)
        self._press_at = (event.x_root, event.y_root)
        self._press_target = target
        self._set_window_cursor(window, "")
        return None

    def _handle_window_drag(
        self,
        event: Any,
        target: str,
        window: tk.Tk | tk.Toplevel,
    ) -> str | None:
        del target
        if self._resize_window is window:
            return self._resize_window_size(event)
        return None

    def _handle_window_release(
        self,
        event: Any,
        target: str,
        window: tk.Tk | tk.Toplevel,
    ) -> str | None:
        del target
        if self._resize_window is window:
            result = self._finish_resize(event)
            self._set_window_cursor(window, "")
            return result
        return self._release_pointer(event)

    def _settings_button(self, parent: tk.Misc) -> tk.Button:
        button = tk.Button(
            parent,
            text="⚙",
            command=self._open_settings_dialog,
            bg=str(parent.cget("bg")),
            fg="#A9BCD2",
            activebackground="#2E3846",
            activeforeground=HUD_ACCENT,
            relief="flat",
            padx=5,
            pady=1,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        )
        setattr(button, "_hud_handle", True)
        return button

    def _update_button(self, parent: tk.Misc) -> tk.Button:
        button = tk.Button(
            parent,
            text="↓",
            command=self._handle_update_action,
            bg=str(parent.cget("bg")),
            fg=HUD_BLUE,
            activebackground="#2E3846",
            activeforeground=HUD_ACCENT,
            relief="flat",
            padx=5,
            pady=1,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        )
        setattr(button, "_hud_handle", True)
        _HoverTip(button, self._update_tooltip_text)
        self._top_update_button = button
        return button

    def _update_tooltip_text(self) -> str:
        return self._update_state.title or self._update_state.message

    def _handle_update_action(self) -> None:
        if self.update_manager is None:
            return
        self._update_state = self.update_manager.handle_click()
        self._render_update_button()
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._set_settings_status(
                self._update_state.message or self._update_state.title,
                kind="error" if self._update_state.error else "",
            )

    def _render_update_button(self) -> None:
        button = self._top_update_button
        if button is None or not button.winfo_exists():
            return
        state = self._update_state
        if not state.visible:
            if button.winfo_manager():
                button.pack_forget()
            return
        glyph = "⇪" if state.icon == "install" else "↓"
        color = HUD_ACCENT if state.icon == "install" else HUD_BLUE
        if state.phase in {"paused", "error"}:
            color = "#FFB86B"
        button.configure(text=glyph, fg=color)
        if not button.winfo_manager():
            button.pack(side="left", padx=(4, 0))

    def _cancel_settings_dialog_prewarm(self) -> None:
        if self._settings_prewarm_job is None:
            return
        try:
            self.root.after_cancel(self._settings_prewarm_job)
        except tk.TclError:
            pass
        self._settings_prewarm_job = None

    def _schedule_settings_dialog_prewarm(self) -> None:
        if self._settings_prewarm_job is not None:
            return
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            return
        try:
            self._settings_prewarm_job = self.root.after_idle(self._prewarm_settings_dialog)
        except tk.TclError:
            self._settings_prewarm_job = None

    def _prewarm_settings_dialog(self) -> None:
        self._settings_prewarm_job = None
        dialog = self._ensure_settings_dialog_shell()
        if dialog is None:
            return
        if not self._settings_dialog_populated():
            self._select_settings_tab("settings")
        try:
            dialog.withdraw()
        except tk.TclError:
            return

    def _settings_dialog_populated(self) -> bool:
        return bool(
            self._settings_body_frame is not None
            and self._settings_actions_frame is not None
            and self._settings_body_frame.winfo_children()
            and self._settings_actions_frame.winfo_children()
        )

    def _ensure_settings_dialog_shell(self) -> tk.Toplevel | None:
        dialog = self._settings_dialog
        if dialog is not None and dialog.winfo_exists():
            return dialog
        self._cancel_settings_dialog_prewarm()
        settings = self.user_settings_store.load()
        self.user_settings = settings
        self._settings_configured_display_mode = str(settings.display_mode)
        self._settings_display_mode_touched = False
        dialog = tk.Toplevel(self.root)
        self._settings_dialog = dialog
        dialog.withdraw()
        dialog.title(f"codex-usage-hud v{__version__} 设置")
        dialog.configure(bg=HUD_BG)
        dialog.attributes("-topmost", True)
        dialog.overrideredirect(True)
        dialog.geometry(
            self._centered_settings_geometry(
                dialog,
                SETTINGS_DIALOG_WIDTH,
                SETTINGS_DIALOG_HEIGHT,
            )
        )
        dialog.minsize(620, 480)
        dialog.protocol("WM_DELETE_WINDOW", self._hide_settings_dialog)
        dialog.bind("<Destroy>", self._settings_dialog_destroyed, add="+")

        head = tk.Frame(dialog, bg=REQUEST_HEADER_BG, padx=12, pady=10)
        head.pack(fill="x")
        title = tk.Label(
            head,
            text=f"codex-usage-hud v{__version__}",
            anchor="w",
            bg=REQUEST_HEADER_BG,
            fg=HUD_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        title.pack(side="left", fill="x", expand=True)
        tk.Button(
            head,
            text="×",
            command=self._hide_settings_dialog,
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=8,
            pady=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="right")
        self._bind_settings_dialog_drag(dialog, head, title)

        tabs = tk.Frame(dialog, bg=HUD_BG, padx=12, pady=8)
        tabs.pack(fill="x")
        self._settings_tab_buttons = {
            "settings": self._settings_tab_button(tabs, "设置", "settings"),
            "support": self._settings_tab_button(tabs, "请作者喝咖啡", "support"),
            "about": self._settings_tab_button(tabs, "版本更新", "about"),
        }
        self._settings_tab_buttons["settings"].pack(side="left", padx=(0, 6))
        self._settings_tab_buttons["support"].pack(side="left", padx=(0, 6))
        self._settings_tab_buttons["about"].pack(side="left")

        body_shell = tk.Frame(dialog, bg=HUD_BG)
        body_shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            body_shell,
            bg=HUD_BG,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        scrollbar = HudScrollbar(
            body_shell,
            command=canvas.yview,
            track=HUD_BG,
            thumb=HUD_DIVIDER,
            thumb_hover=HUD_PANEL_BORDER,
            width=8,
        )
        body = tk.Frame(canvas, bg=HUD_BG, padx=12, pady=12)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(body_window, width=event.width),
        )
        self._settings_canvas = canvas
        self._bind_settings_scroll_tree(canvas)
        self._settings_body_frame = body

        actions = tk.Frame(dialog, bg=REQUEST_HEADER_BG, padx=12, pady=10)
        actions.pack(fill="x")
        self._settings_status_label = tk.Label(
            actions,
            text="",
            anchor="w",
            justify="left",
            bg=REQUEST_HEADER_BG,
            fg="#A9BCD2",
            font=("Microsoft YaHei UI", 8),
        )
        self._settings_status_label.pack(side="left", fill="x", expand=True)
        action_buttons = tk.Frame(actions, bg=REQUEST_HEADER_BG)
        action_buttons.pack(side="right")
        self._settings_actions_frame = action_buttons
        return dialog

    def _open_settings_dialog(self, tab: str = "settings") -> None:
        dialog = self._ensure_settings_dialog_shell()
        if dialog is None:
            return
        try:
            is_hidden = dialog.state() == "withdrawn"
        except tk.TclError:
            return
        if is_hidden:
            self.user_settings = self.user_settings_store.load()
            self._settings_configured_display_mode = str(self.user_settings.display_mode)
            self._settings_display_mode_touched = False
            try:
                dialog.deiconify()
            except tk.TclError:
                return
            if self._settings_active_tab == tab == "settings" and self._settings_dialog_populated():
                self._refresh_settings_dialog_values()
            elif self._settings_dialog_populated():
                self._select_settings_tab(tab)
            else:
                self._set_settings_status("正在加载设置...")
                self._schedule_settings_tab(tab)
        elif self._settings_active_tab != tab:
            self._select_settings_tab(tab)
        self._raise_settings_dialog()

    def _mark_ui_interaction(self, duration_ms: int = HUD_INTERACTION_SETTLE_MS) -> None:
        grace_seconds = max(0, int(duration_ms)) / 1000.0
        self._ui_interaction_hold_until = max(
            self._ui_interaction_hold_until,
            time.monotonic() + grace_seconds,
        )

    def _ui_interaction_active(self, now: float | None = None) -> bool:
        return self._ui_interaction_hold_until > (time.monotonic() if now is None else now)

    def _settings_dialog_visible(self) -> bool:
        dialog = self._settings_dialog
        if dialog is None or not dialog.winfo_exists():
            return False
        try:
            return dialog.state() != "withdrawn"
        except tk.TclError:
            return False

    def _hide_settings_dialog(self) -> None:
        dialog = self._settings_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        try:
            dialog.withdraw()
        except tk.TclError:
            return

    def _work_overlay_selectable_max(self) -> int:
        return work_overlay_max_items_for_screen_height(self.root.winfo_screenheight())

    def _work_overlay_setting_text(self, count: int) -> str:
        return str(
            min(
                self._work_overlay_selectable_max(),
                max(0, int(count)),
            )
        )

    def _work_overlay_setting_values(self) -> list[str]:
        return [str(item) for item in range(0, self._work_overlay_selectable_max() + 1)]

    def _refresh_settings_dialog_values(self) -> None:
        if self._settings_active_tab != "settings":
            return
        settings = self.user_settings
        self._settings_configured_display_mode = str(settings.display_mode)
        self._settings_display_mode_touched = False
        values: dict[str, object] = {
            "daily_budget_usd": settings.daily_budget_usd,
            "weekly_budget_usd": settings.weekly_budget_usd,
            "daily_reset_time": settings.daily_reset_time,
            "weekly_reset_time": settings.weekly_reset_time,
            "work_overlay_max_items": settings.work_overlay_max_items,
            "budget_thresholds": ",".join(str(item) for item in settings.budget_thresholds),
            "weekly_adjustment_usd": settings.weekly_adjustment_usd,
            "pricing_url": settings.pricing_url,
        }
        for key, value in values.items():
            widget = self._settings_entries.get(key)
            if widget is None:
                continue
            if isinstance(widget, ttk.Combobox):
                if key == "work_overlay_max_items":
                    widget.configure(values=self._work_overlay_setting_values())
                    widget.set(self._work_overlay_setting_text(int(value)))
                    continue
                widget.set(str(value))
            else:
                widget.delete(0, "end")
                widget.insert(0, str(value))
        weekday = self._settings_entries.get("weekly_reset_weekday")
        if isinstance(weekday, ttk.Combobox):
            weekday.set(
                f"{settings.weekly_reset_weekday} "
                + ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][settings.weekly_reset_weekday]
            )
        mode = self._settings_entries.get("display_mode")
        if isinstance(mode, ttk.Combobox):
            mode.set(
                {
                    "renderer": "renderer - 优先 renderer 注入，失败回退 Tk",
                    "tk": "tk - 仅使用 Tk 窗口",
                }.get(settings.display_mode, "auto - 优先 renderer 注入，失败回退 Tk")
            )
        if self._settings_prices_body is not None:
            self._replace_price_rows(
                self._settings_prices_body,
                self._settings_price_rows,
                settings.model_prices,
            )
        self._set_settings_status("设置将保存到本地配置文件")

    def _raise_settings_dialog(self) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            return
        try:
            if not dialog.winfo_exists():
                return
            dialog.lift()
            dialog.attributes("-topmost", True)
        except tk.TclError:
            return

    def _settings_tab_button(
        self,
        parent: tk.Misc,
        text: str,
        tab: str,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=lambda: self._select_settings_tab(tab),
            bg=HUD_BG,
            fg="#A9BCD2",
            activebackground=HUD_HEADER_BG,
            activeforeground=HUD_ACCENT,
            relief="flat",
            padx=9,
            pady=5,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        )

    def _settings_dialog_destroyed(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self._settings_dialog:
            return
        if self._settings_build_job is not None:
            try:
                self.root.after_cancel(self._settings_build_job)
            except tk.TclError:
                pass
            self._settings_build_job = None
        for job_name in ("_settings_loading_anim_job", "_settings_update_poll_job"):
            job = getattr(self, job_name, None)
            if job is None:
                continue
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, job_name, None)
        self._settings_prewarm_job = None
        self._settings_pending_tab = ""
        self._settings_dialog = None
        self._settings_entries = {}
        self._settings_price_rows = []
        self._settings_body_frame = None
        self._settings_actions_frame = None
        self._settings_status_label = None
        self._settings_tab_buttons = {}
        self._settings_canvas = None
        self._settings_prices_body = None
        self._settings_support_images = []
        self._settings_update_check_button = None
        self._settings_update_install_button = None
        self._settings_loading_layer = None
        self._settings_loading_kicker_label = None
        self._settings_loading_title_label = None
        self._settings_loading_body_label = None
        self._settings_loading_track = None
        self._settings_loading_indicator = None
        self._settings_loading_glow = None
        self._settings_loading_anim_job = None
        self._settings_update_poll_job = None
        self._settings_update_flow = ""
        self._settings_loading_position = 0
        self._settings_loading_direction = 1
        self._settings_display_mode_touched = False
        self._settings_active_tab = "settings"

    @staticmethod
    def _active_display_mode() -> str:
        return "tk"

    @staticmethod
    def _effective_runtime_mode(display_mode: str) -> str:
        return effective_display_mode(display_mode)

    @staticmethod
    def _renderer_debugger_available(timeout_seconds: float = 0.35) -> bool:
        try:
            target = pick_page_target(list_targets(cdp_port_from_env(), timeout_seconds))
        except Exception:
            return False
        return bool(target.get("webSocketDebuggerUrl"))

    def _centered_settings_geometry(
        self,
        dialog: tk.Toplevel,
        width: int,
        height: int,
    ) -> str:
        del dialog
        screen_width = max(1, int(self.root.winfo_screenwidth()))
        screen_height = max(1, int(self.root.winfo_screenheight()))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        return f"{width}x{height}+{x}+{y}"

    def _bind_settings_dialog_drag(
        self,
        dialog: tk.Toplevel,
        *widgets: tk.Widget,
    ) -> None:
        drag_offset = {"x": 0, "y": 0}

        def start_drag(event: tk.Event[tk.Misc]) -> None:
            self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
            drag_offset["x"] = int(event.x_root) - dialog.winfo_x()
            drag_offset["y"] = int(event.y_root) - dialog.winfo_y()

        def move_drag(event: tk.Event[tk.Misc]) -> str:
            self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
            x = int(event.x_root) - drag_offset["x"]
            y = int(event.y_root) - drag_offset["y"]
            dialog.geometry(f"+{x}+{y}")
            return "break"

        for widget in widgets:
            widget.bind("<Button-1>", start_drag, add="+")
            widget.bind("<B1-Motion>", move_drag, add="+")

    @staticmethod
    def _wheel_units(event: tk.Event[tk.Misc]) -> int:
        button = getattr(event, "num", None)
        if button == 4:
            return -3
        if button == 5:
            return 3
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return 0
        return (-1 if delta > 0 else 1) * max(1, abs(delta) // 120)

    def _scroll_settings_body(self, event: tk.Event[tk.Misc]) -> str | None:
        canvas = self._settings_canvas
        if canvas is None or not canvas.winfo_exists():
            return None
        units = self._wheel_units(event)
        if not units:
            return None
        canvas.yview_scroll(units, "units")
        return "break"

    def _bind_settings_scroll_tree(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._scroll_settings_body, add="+")
        widget.bind("<Button-4>", self._scroll_settings_body, add="+")
        widget.bind("<Button-5>", self._scroll_settings_body, add="+")
        for child in widget.winfo_children():
            self._bind_settings_scroll_tree(child)

    def _schedule_settings_tab(self, tab: str = "settings") -> None:
        self._settings_pending_tab = tab if tab in {"settings", "support", "about"} else "settings"
        if self._settings_build_job is not None:
            return
        try:
            self._settings_build_job = self.root.after_idle(self._flush_settings_tab)
        except tk.TclError:
            self._settings_build_job = None

    def _flush_settings_tab(self) -> None:
        self._settings_build_job = None
        tab = self._settings_pending_tab or "settings"
        self._settings_pending_tab = ""
        if not self._settings_dialog_visible():
            return
        self._select_settings_tab(tab)

    def _select_settings_tab(self, tab: str = "settings") -> None:
        if self._settings_body_frame is None or self._settings_actions_frame is None:
            return
        active_tab = tab if tab in {"settings", "support", "about"} else "settings"
        if (
            active_tab == self._settings_active_tab
            and self._settings_body_frame.winfo_children()
            and self._settings_actions_frame.winfo_children()
        ):
            self._raise_settings_dialog()
            return
        self._settings_active_tab = active_tab
        for name, button in self._settings_tab_buttons.items():
            selected = name == active_tab
            button.configure(
                bg=HUD_HEADER_BG if selected else HUD_BG,
                fg=HUD_ACCENT if selected else "#A9BCD2",
                font=("Microsoft YaHei UI", 9, "bold" if selected else "normal"),
            )
        for child in self._settings_body_frame.winfo_children():
            child.destroy()
        for child in self._settings_actions_frame.winfo_children():
            child.destroy()
        self._settings_support_images = []
        if active_tab == "support":
            self._build_support_panel(
                self._settings_body_frame,
                self._settings_actions_frame,
            )
        elif active_tab == "about":
            self._build_about_panel(
                self._settings_body_frame,
                self._settings_actions_frame,
            )
        else:
            self._build_settings_panel(
                self._settings_body_frame,
                self._settings_actions_frame,
            )
        self._bind_settings_scroll_tree(self._settings_body_frame)

    def _settings_field(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        key: str,
        label: str,
        value: object,
        *,
        columnspan: int = 1,
    ) -> tk.Entry:
        frame = tk.Frame(parent, bg=HUD_BG)
        frame.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=5,
            pady=5,
        )
        tk.Label(
            frame,
            text=label,
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        entry = tk.Entry(
            frame,
            bg=HUD_PANEL_BG,
            fg=HUD_TEXT,
            insertbackground=HUD_TEXT,
            relief="flat",
        )
        entry.insert(0, str(value))
        entry.pack(fill="x", ipady=4)
        self._settings_entries[key] = entry
        return entry

    def _build_settings_panel(
        self,
        body: tk.Frame,
        actions: tk.Frame,
    ) -> None:
        settings = self.user_settings
        self._settings_entries = {}
        self._settings_price_rows = []
        grid = tk.Frame(body, bg=HUD_BG)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._settings_field(grid, 0, 0, "daily_budget_usd", "日额度 USD", settings.daily_budget_usd)
        self._settings_field(grid, 0, 1, "weekly_budget_usd", "周额度 USD", settings.weekly_budget_usd)
        self._settings_field(grid, 1, 0, "daily_reset_time", "日额度重置时间", settings.daily_reset_time)

        weekly_frame = tk.Frame(grid, bg=HUD_BG)
        weekly_frame.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        tk.Label(
            weekly_frame,
            text="周额度重置",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        weekly_controls = tk.Frame(weekly_frame, bg=HUD_BG)
        weekly_controls.pack(fill="x")
        weekday = ttk.Combobox(
            weekly_controls,
            values=["0 周一", "1 周二", "2 周三", "3 周四", "4 周五", "5 周六", "6 周日"],
            state="readonly",
            width=10,
        )
        weekday.set(f"{settings.weekly_reset_weekday} " + ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][settings.weekly_reset_weekday])
        weekday.pack(side="left", fill="x", expand=True, padx=(0, 6))
        weekly_time = tk.Entry(
            weekly_controls,
            bg=HUD_PANEL_BG,
            fg=HUD_TEXT,
            insertbackground=HUD_TEXT,
            relief="flat",
        )
        weekly_time.insert(0, str(settings.weekly_reset_time))
        weekly_time.pack(side="left", fill="x", expand=True, ipady=4)
        self._settings_entries["weekly_reset_weekday"] = weekday
        self._settings_entries["weekly_reset_time"] = weekly_time

        mode_frame = tk.Frame(grid, bg=HUD_BG)
        mode_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        tk.Label(
            mode_frame,
            text="HUD 显示方案",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        mode = ttk.Combobox(
            mode_frame,
            values=[
                "auto - 优先 renderer 注入，失败回退 Tk",
                "renderer - 优先 renderer 注入，失败回退 Tk",
                "tk - 仅使用 Tk 窗口",
            ],
            state="readonly",
        )
        mode.set(
            {
                "renderer": "renderer - 优先 renderer 注入，失败回退 Tk",
                "tk": "tk - 仅使用 Tk 窗口",
            }.get(self._active_display_mode(), "renderer - 优先 renderer 注入，失败回退 Tk")
        )
        mode.pack(fill="x")
        mode.bind("<<ComboboxSelected>>", self._on_display_mode_selected, add="+")
        self._settings_entries["display_mode"] = mode

        bubble_frame = tk.Frame(grid, bg=HUD_BG)
        bubble_frame.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        tk.Label(
            bubble_frame,
            text="会话气泡最大显示数（0 表示不启用）",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        bubble_limit = ttk.Combobox(
            bubble_frame,
            values=self._work_overlay_setting_values(),
            state="readonly",
        )
        bubble_limit.set(self._work_overlay_setting_text(settings.work_overlay_max_items))
        bubble_limit.pack(fill="x")
        self._settings_entries["work_overlay_max_items"] = bubble_limit

        self._settings_field(
            grid,
            3,
            0,
            "budget_thresholds",
            "超额提醒阈值",
            ",".join(str(item) for item in settings.budget_thresholds),
        )
        self._settings_field(
            grid,
            3,
            1,
            "weekly_adjustment_usd",
            "本周补充已使用额度 USD",
            settings.weekly_adjustment_usd,
        )
        pricing_frame = tk.Frame(grid, bg=HUD_BG)
        pricing_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        tk.Label(
            pricing_frame,
            text="计费单价获取地址",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        pricing_controls = tk.Frame(pricing_frame, bg=HUD_BG)
        pricing_controls.pack(fill="x")
        pricing_entry = tk.Entry(
            pricing_controls,
            bg=HUD_PANEL_BG,
            fg=HUD_TEXT,
            insertbackground=HUD_TEXT,
            relief="flat",
        )
        pricing_entry.insert(0, str(settings.pricing_url))
        pricing_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self._settings_entries["pricing_url"] = pricing_entry
        tk.Button(
            pricing_controls,
            text="拉取",
            command=lambda: self._settings_fetch_prices(
                self._settings_entries,
                self._settings_price_rows,
                prices_body,
            ),
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(8, 0))

        price_table = tk.Frame(grid, bg=HUD_BG)
        price_table.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=5, pady=(8, 0))
        tk.Label(
            price_table,
            text="模型单价（USD / 1M tokens）",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x")
        header = tk.Frame(price_table, bg=HUD_BG)
        header.pack(fill="x", pady=(6, 2))
        for index, text in enumerate(["模型", "输入", "缓存", "输出", "推理"]):
            header.columnconfigure(index, weight=2 if index == 0 else 1)
            tk.Label(
                header,
                text=text,
                anchor="w",
                bg=HUD_BG,
                fg=HUD_MUTED,
                font=("Microsoft YaHei UI", 8, "bold"),
            ).grid(row=0, column=index, sticky="ew", padx=(0, 6))
        prices_body = tk.Frame(price_table, bg=HUD_BG)
        prices_body.pack(fill="x")
        self._settings_prices_body = prices_body
        self._replace_price_rows(prices_body, self._settings_price_rows, settings.model_prices)
        tk.Button(
            price_table,
            text="添加模型",
            command=lambda: self._add_price_row(prices_body, self._settings_price_rows),
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=9,
            pady=3,
            cursor="hand2",
        ).pack(anchor="w", pady=(6, 0))

        footer = tk.Frame(grid, bg=HUD_BG)
        footer.grid(row=6, column=0, columnspan=2, sticky="ew", padx=5, pady=(10, 0))
        tk.Button(
            footer,
            text="退出 HUD",
            command=self._confirm_full_exit,
            bg=HUD_BG,
            fg="#A9BCD2",
            activebackground=HUD_HEADER_BG,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=4,
            pady=2,
            cursor="hand2",
        ).pack(side="left")
        tk.Label(
            footer,
            text=f"配置文件：{self.user_settings_store.path}",
            anchor="e",
            justify="right",
            bg=HUD_BG,
            fg="#A9BCD2",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")

        tk.Button(
            actions,
            text="导出 JSON",
            command=lambda: self._settings_export(
                self._settings_entries,
                self._settings_price_rows,
            ),
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=9,
            pady=4,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            actions,
            text="保存",
            command=lambda: self._settings_save(
                self._settings_entries,
                self._settings_price_rows,
            ),
            bg=HUD_ACCENT,
            fg=HUD_BG,
            activebackground="#FFE59A",
            activeforeground=HUD_BG,
            relief="flat",
            padx=11,
            pady=4,
        ).pack(side="left")
        self._set_settings_status("设置将保存到本地配置文件")

    def _build_support_panel(self, body: tk.Frame, actions: tk.Frame) -> None:
        settings = self.user_settings
        support = tk.Frame(body, bg=HUD_BG)
        support.pack(fill="both", expand=True)
        tk.Label(
            support,
            text="如果这个 HUD 帮你节省了排查 token 和费用的时间，可以扫码支持维护。",
            justify="left",
            anchor="w",
            bg=HUD_BG,
            fg=HUD_TEXT,
            wraplength=680,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x")
        qr_grid = tk.Frame(support, bg=HUD_BG)
        qr_grid.pack(fill="x", pady=(12, 10))
        qr_grid.columnconfigure(0, weight=1)
        qr_grid.columnconfigure(1, weight=1)
        for item in support_qr_asset_paths():
            index = len(qr_grid.winfo_children())
            card = tk.Frame(
                qr_grid,
                bg=HUD_PANEL_BG,
                padx=10,
                pady=10,
                highlightthickness=1,
                highlightbackground=HUD_DIVIDER,
            )
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0 if index % 2 == 0 else 6, 0 if index % 2 else 6),
                pady=6,
            )
            tk.Label(
                card,
                text=f"{item['label']}  {item['hint']}",
                anchor="w",
                bg=HUD_PANEL_BG,
                fg=HUD_TEXT,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(fill="x")
            self._pack_support_qr_image(card, item["path"])
        tk.Label(
            support,
            text=f"项目链接：{settings.support_url}\n配置文件：{self.user_settings_store.path}",
            justify="left",
            anchor="nw",
            bg=HUD_BG,
            fg="#A9BCD2",
            wraplength=680,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", pady=(8, 0))
        tk.Button(
            actions,
            text="关闭",
            command=self._hide_settings_dialog,
            bg=HUD_ACCENT,
            fg=HUD_BG,
            activebackground="#FFE59A",
            activeforeground=HUD_BG,
            relief="flat",
            padx=11,
            pady=4,
        ).pack(side="left")
        self._set_settings_status("赞赏码资源来自本地打包文件")

    def _build_about_panel(self, body: tk.Frame, actions: tk.Frame) -> None:
        about = tk.Frame(body, bg=HUD_BG)
        about.pack(fill="both", expand=True)
        lines = [
            f"当前版本：v{__version__}",
            "更新源：GitHub Releases / mingbingfeng/codex-usage-hud",
            "Windows 安装包：codex-usage-hud-v*-windows-x64-setup.exe",
            "自动更新会下载最新版安装包并启动安装器；安装器会先关闭正在运行的 HUD，再替换本地文件。",
            f"配置文件：{self.user_settings_store.path}",
        ]
        tk.Label(
            about,
            text="\n".join(lines),
            justify="left",
            anchor="nw",
            bg=HUD_BG,
            fg=HUD_TEXT,
            wraplength=680,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x")
        tk.Button(
            actions,
            text="检查更新",
            command=self._settings_check_update,
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=9,
            pady=4,
        )
        self._settings_update_check_button.pack(side="left", padx=(0, 6))
        self._settings_update_install_button = tk.Button(
            actions,
            text="安装更新",
            command=self._settings_install_update,
            bg=HUD_ACCENT,
            fg=HUD_BG,
            activebackground="#FFE59A",
            activeforeground=HUD_BG,
            relief="flat",
            padx=11,
            pady=4,
        )
        self._settings_update_install_button.pack(side="left", padx=(0, 6))
        tk.Button(
            actions,
            text="关闭",
            command=self._hide_settings_dialog,
            bg="#2E3846",
            fg=HUD_TEXT,
            activebackground=HUD_DIVIDER,
            activeforeground=HUD_TEXT,
            relief="flat",
            padx=9,
            pady=4,
        ).pack(side="left")
        self._sync_about_update_controls()
        self._set_settings_status("可检查 GitHub Release 并启动 Windows 安装器")

    def _settings_check_update(self) -> None:
        if self.update_manager is None:
            info = check_for_update(current_version=__version__)
            message = format_update_info(info)
            self._set_settings_status(message, kind="error" if info.error else "")
            if info.error:
                messagebox.showerror("检查更新失败", message, parent=self._settings_dialog)
            return
        self._update_state = self.update_manager.request_check(auto_download=False)
        self._set_settings_status(self._update_state.message or "正在检查更新...")
        self._sync_about_update_controls()
        self._start_settings_update_feedback(
            flow="check",
            kicker="正在检查",
            title="正在检查更新",
            body="HUD 正在查询 GitHub Release。通常只需 1 到 3 秒。",
        )

    def _settings_install_update(self) -> None:
        if self.update_manager is None:
            info = check_for_update(current_version=__version__)
            if info.error:
                message = format_update_info(info)
                self._set_settings_status(message, kind="error")
                messagebox.showerror("安装更新失败", message, parent=self._settings_dialog)
                return
            if not info.available:
                self._set_settings_status(format_update_info(info))
                return
            try:
                installer = download_update_asset(info)
                launch_installer(installer)
            except Exception as exc:
                self._set_settings_status(f"安装更新失败：{exc}", kind="error")
                messagebox.showerror("安装更新失败", str(exc), parent=self._settings_dialog)
                return
            self._set_settings_status(f"已启动 {info.asset_name}，安装器会先关闭当前 HUD。")
            return
        self._update_state = self.update_manager.request_install()
        self._set_settings_status(self._update_state.message or "正在准备安装更新...")
        self._sync_about_update_controls()
        self._start_settings_update_feedback(
            flow="install",
            kicker="正在准备",
            title="正在检查并准备安装更新",
            body="HUD 会先检查 GitHub Release，再后台下载 Windows 安装包。",
        )

    def _sync_about_update_controls(self) -> None:
        check_button = self._settings_update_check_button
        install_button = self._settings_update_install_button
        if check_button is None or install_button is None:
            return
        state = self._update_state
        phase = str(state.phase or "")
        check_text = "检查更新"
        install_text = "安装更新"
        check_state = "normal"
        install_state = "normal"
        if phase == "checking":
            check_text = "检查中..."
            install_text = "请稍候"
            check_state = "disabled"
            install_state = "disabled"
        elif phase == "downloading":
            install_text = (
                f"下载中 {state.progress_text}"
                if state.progress_text
                else "下载中..."
            )
            check_state = "disabled"
            install_state = "disabled"
        elif phase == "ready":
            install_text = "打开安装器"
        check_button.configure(text=check_text, state=check_state)
        install_button.configure(text=install_text, state=install_state)

    def _start_settings_update_feedback(
        self,
        *,
        flow: str,
        kicker: str,
        title: str,
        body: str,
    ) -> None:
        self._settings_update_flow = flow
        self._show_settings_loading_overlay(kicker=kicker, title=title, body=body)
        self._poll_settings_update_feedback()

    def _poll_settings_update_feedback(self) -> None:
        self._settings_update_poll_job = None
        flow = self._settings_update_flow
        manager = self.update_manager
        if not flow or manager is None:
            self._hide_settings_loading_overlay()
            return
        self._update_state = manager.status()
        self._render_update_button()
        self._sync_about_update_controls()
        state = self._update_state
        if flow == "check" and state.phase == "checking":
            self._show_settings_loading_overlay(
                kicker="正在检查",
                title="正在检查更新",
                body="HUD 正在查询 GitHub Release。通常只需 1 到 3 秒。",
            )
            self._settings_update_poll_job = self.root.after(
                120, self._poll_settings_update_feedback
            )
            return
        if flow == "install" and state.phase in {"checking", "downloading"}:
            if state.phase == "checking":
                body = "HUD 正在查询 GitHub Release，并准备下载安装包。"
            else:
                body = (
                    f"当前进度：{state.progress_text}\n\n下载完成后会自动启动安装器。"
                    if state.progress_text
                    else "正在下载 Windows 安装包。\n\n下载完成后会自动启动安装器。"
                )
            self._show_settings_loading_overlay(
                kicker="正在下载" if state.phase == "downloading" else "正在准备",
                title="正在下载安装更新"
                if state.phase == "downloading"
                else "正在检查并准备安装更新",
                body=body,
            )
            self._settings_update_poll_job = self.root.after(
                120, self._poll_settings_update_feedback
            )
            return
        self._settings_update_flow = ""
        self._hide_settings_loading_overlay()
        message = state.message or state.title or "更新操作已完成。"
        self._set_settings_status(message, kind="error" if state.error else "")
        if state.error:
            title = "检查更新失败" if flow == "check" else "安装更新失败"
            messagebox.showerror(title, message, parent=self._settings_dialog)

    def _show_settings_loading_overlay(
        self,
        *,
        kicker: str,
        title: str,
        body: str,
    ) -> None:
        dialog = self._settings_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        layer = self._settings_loading_layer
        if layer is None or not layer.winfo_exists():
            layer = tk.Frame(dialog, bg=HUD_BG, highlightthickness=0, bd=0)
            layer.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._settings_loading_layer = layer

            shell = tk.Frame(
                layer,
                bg=HUD_PANEL_BG,
                highlightthickness=1,
                highlightbackground=HUD_DIVIDER,
                padx=18,
                pady=16,
            )
            shell.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(
                shell,
                text="codex-usage-hud",
                anchor="w",
                bg=HUD_PANEL_BG,
                fg=HUD_ACCENT,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(fill="x")
            kicker_label = tk.Label(
                shell,
                text=kicker,
                anchor="w",
                bg=HUD_PANEL_BG,
                fg=HUD_MUTED,
                font=("Microsoft YaHei UI", 9),
            )
            kicker_label.pack(fill="x", pady=(6, 0))
            self._settings_loading_kicker_label = kicker_label
            title_label = tk.Label(
                shell,
                text=title,
                anchor="w",
                justify="left",
                bg=HUD_PANEL_BG,
                fg=HUD_TEXT,
                font=("Microsoft YaHei UI", 14, "bold"),
                pady=3,
            )
            title_label.pack(fill="x")
            self._settings_loading_title_label = title_label
            body_label = tk.Label(
                shell,
                text=body,
                anchor="w",
                justify="left",
                bg=HUD_PANEL_BG,
                fg="#B8C6D8",
                font=("Microsoft YaHei UI", 10),
                wraplength=324,
            )
            body_label.pack(fill="x")
            self._settings_loading_body_label = body_label
            track = tk.Canvas(
                shell,
                width=324,
                height=8,
                bg=HUD_PANEL_BG,
                highlightthickness=0,
                bd=0,
            )
            track.pack(fill="x", pady=(14, 0))
            track.create_rectangle(0, 1, 324, 7, fill="#1A2430", outline="")
            self._settings_loading_indicator = track.create_rectangle(
                0, 1, 92, 7, fill=HUD_ACCENT, outline=""
            )
            self._settings_loading_glow = track.create_rectangle(
                0, 1, 48, 7, fill="#FFE7A0", outline=""
            )
            self._settings_loading_track = track
            self._settings_loading_position = 0
            self._settings_loading_direction = 1
        if self._settings_loading_kicker_label is not None:
            self._settings_loading_kicker_label.configure(text=kicker)
        if self._settings_loading_title_label is not None:
            self._settings_loading_title_label.configure(text=title)
        if self._settings_loading_body_label is not None:
            self._settings_loading_body_label.configure(text=body)
        self._animate_settings_loading_overlay()

    def _animate_settings_loading_overlay(self) -> None:
        if self._settings_loading_anim_job is not None:
            return

        def step() -> None:
            self._settings_loading_anim_job = None
            track = self._settings_loading_track
            indicator = self._settings_loading_indicator
            glow = self._settings_loading_glow
            if (
                track is None
                or indicator is None
                or glow is None
                or not track.winfo_exists()
            ):
                return
            self._settings_loading_position += 7 * self._settings_loading_direction
            if self._settings_loading_position >= 232:
                self._settings_loading_position = 232
                self._settings_loading_direction = -1
            elif self._settings_loading_position <= 0:
                self._settings_loading_position = 0
                self._settings_loading_direction = 1
            position = self._settings_loading_position
            track.coords(indicator, position, 1, position + 92, 7)
            track.coords(glow, position + 20, 1, position + 60, 7)
            self._settings_loading_anim_job = self.root.after(34, step)

        self._settings_loading_anim_job = self.root.after(34, step)

    def _hide_settings_loading_overlay(self) -> None:
        if self._settings_loading_anim_job is not None:
            try:
                self.root.after_cancel(self._settings_loading_anim_job)
            except tk.TclError:
                pass
            self._settings_loading_anim_job = None
        layer = self._settings_loading_layer
        self._settings_loading_layer = None
        self._settings_loading_kicker_label = None
        self._settings_loading_title_label = None
        self._settings_loading_body_label = None
        self._settings_loading_track = None
        self._settings_loading_indicator = None
        self._settings_loading_glow = None
        self._settings_loading_position = 0
        self._settings_loading_direction = 1
        if layer is not None and layer.winfo_exists():
            try:
                layer.destroy()
            except tk.TclError:
                pass

    def _pack_support_qr_image(self, parent: tk.Misc, path: str) -> None:
        try:
            image = tk.PhotoImage(file=path)
        except tk.TclError as exc:
            tk.Label(
                parent,
                text=f"图片无法显示：{exc}",
                justify="left",
                anchor="w",
                bg=HUD_PANEL_BG,
                fg="#FFB86B",
                wraplength=260,
                font=("Microsoft YaHei UI", 8),
            ).pack(fill="x", pady=(8, 0))
            return
        self._settings_support_images.append(image)
        label = tk.Label(
            parent,
            image=image,
            bg=HUD_PANEL_BG,
            bd=0,
            highlightthickness=0,
        )
        label.pack(pady=(8, 0))

    def _add_price_row(
        self,
        parent: tk.Misc,
        rows: list[dict[str, tk.Entry]],
        model: str = "",
        price: Any = None,
    ) -> dict[str, tk.Entry]:
        row = tk.Frame(parent, bg=HUD_BG)
        row.pack(fill="x", pady=2)
        values = {
            "model": model,
            "input": self._price_value(price, "input"),
            "cached_input": self._price_value(price, "cached_input"),
            "output": self._price_value(price, "output"),
            "reasoning": self._price_value(price, "reasoning"),
        }
        fields: dict[str, tk.Entry] = {}
        for index, key in enumerate(["model", "input", "cached_input", "output", "reasoning"]):
            row.columnconfigure(index, weight=2 if index == 0 else 1)
            entry = tk.Entry(
                row,
                bg=HUD_PANEL_BG,
                fg=HUD_TEXT,
                insertbackground=HUD_TEXT,
                relief="flat",
            )
            entry.insert(0, str(values[key]))
            entry.grid(row=0, column=index, sticky="ew", padx=(0, 6), ipady=4)
            fields[key] = entry
        rows.append(fields)
        return fields

    def _replace_price_rows(
        self,
        parent: tk.Misc,
        rows: list[dict[str, tk.Entry]],
        prices: Any,
    ) -> None:
        for child in parent.winfo_children():
            child.destroy()
        rows.clear()
        items = sorted(prices.items()) if isinstance(prices, dict) else []
        if not items:
            items = [("gpt-5.5", {"input": 5, "cached_input": 0.5, "output": 30, "reasoning": 30})]
        for model, price in items:
            self._add_price_row(parent, rows, model, price)

    @staticmethod
    def _price_value(price: Any, key: str) -> object:
        if price is None:
            return 0
        if isinstance(price, dict):
            return price.get(key, 0)
        return getattr(price, key, 0)

    def _set_settings_status(self, message: str, *, kind: str = "") -> None:
        if self._settings_status_label is None:
            return
        self._settings_status_label.configure(
            text=message,
            fg="#FFB86B" if kind == "error" else "#A9BCD2",
        )

    def _selected_display_mode(self, entries: dict[str, SettingsEntry]) -> str:
        if not self._settings_display_mode_touched:
            return str(self._settings_configured_display_mode or self.user_settings.display_mode)
        return str(entries["display_mode"].get()).split(" ", 1)[0]

    def _on_display_mode_selected(self, event: tk.Event[tk.Misc]) -> None:
        widget = event.widget
        if not isinstance(widget, ttk.Combobox):
            return
        self._settings_display_mode_touched = True
        selected_mode = str(widget.get()).split(" ", 1)[0]
        target_mode = self._effective_runtime_mode(selected_mode)
        if target_mode == self._active_display_mode():
            if selected_mode != self._settings_configured_display_mode:
                self._set_settings_status(
                    "当前显示方案无需立即切换；点击保存后会写入新的启动偏好。"
                    if selected_mode != "auto"
                    else "已改为自动模式；当前 Tk 方案会继续运行，点击保存后会写入新的启动偏好。"
                )
            return

        if target_mode == "renderer":
            debugger_available = self._renderer_debugger_available()
            message = (
                "当前 Codex 已开启本地调试端口，HUD 可以直接切换到 Renderer 内嵌模式，无需重启 Codex。"
                "\n\n是否现在应用？"
                if debugger_available
                else "当前 Codex 还不是调试/CDP 启动。要立即切换到 Renderer 内嵌模式，需要先以调试模式重启 Codex App。"
                "\n\n是否现在重启并应用？"
            )
            title = "立即切换到 Renderer"
            if messagebox.askyesno(title, message, parent=self._settings_dialog):
                self._apply_display_mode_selection(restart_codex=not debugger_available)
                return
        else:
            message = (
                "准备切换到 Tk 独立窗口。HUD 会立即从 Codex 内嵌显示切换为桌面悬浮窗，当前统计会继续保留。"
                "\n\n是否现在应用？"
            )
            if messagebox.askyesno("立即切换到 Tk", message, parent=self._settings_dialog):
                self._apply_display_mode_selection(restart_codex=False)
                return

        self._set_settings_status("已保留方案选择，点击保存后会在下次切换或下次启动时生效。")

    def _apply_display_mode_selection(self, *, restart_codex: bool) -> None:
        try:
            config = self._config_from_settings_dialog(
                self._settings_entries,
                self._settings_price_rows,
            )
            self.user_settings_store.save(config)
        except (OSError, ValueError) as exc:
            self._set_settings_status(f"保存失败：{exc}", kind="error")
            messagebox.showerror("保存失败", str(exc), parent=self._settings_dialog)
            return
        self.user_settings = config
        self._mode_switch_request = self._effective_runtime_mode(config.display_mode)
        self._restart_codex_for_renderer = bool(
            restart_codex and self._mode_switch_request == "renderer"
        )
        self._set_settings_status("正在应用新的 HUD 显示方案...")
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.destroy()
        self.close("display_mode_switch")

    def _config_from_settings_dialog(
        self,
        entries: dict[str, SettingsEntry],
        price_rows: list[dict[str, tk.Entry]],
    ) -> UserConfig:
        price_payload: dict[str, dict[str, object]] = {}
        for row in price_rows:
            model = row["model"].get().strip()
            if not model:
                continue
            price_payload[model] = {
                "input": row["input"].get(),
                "cached_input": row["cached_input"].get(),
                "output": row["output"].get(),
                "reasoning": row["reasoning"].get(),
            }
        raw: dict[str, object] = {
            "daily_budget_usd": entries["daily_budget_usd"].get(),
            "weekly_budget_usd": entries["weekly_budget_usd"].get(),
            "daily_reset_time": entries["daily_reset_time"].get(),
            "weekly_reset_time": entries["weekly_reset_time"].get(),
            "weekly_reset_weekday": str(entries["weekly_reset_weekday"].get()).split(" ", 1)[0],
            "display_mode": self._selected_display_mode(entries),
            "work_overlay_max_items": entries["work_overlay_max_items"].get(),
            "budget_thresholds": parse_config_thresholds(entries["budget_thresholds"].get()),
            "weekly_adjustment_usd": entries["weekly_adjustment_usd"].get(),
            "pricing_url": entries["pricing_url"].get(),
            "support_url": self.user_settings.support_url,
            "model_prices": price_payload,
        }
        return UserConfig.from_dict(raw)

    def _settings_save(
        self,
        entries: dict[str, SettingsEntry],
        price_rows: list[dict[str, tk.Entry]],
    ) -> None:
        try:
            config = self._config_from_settings_dialog(entries, price_rows)
            self.user_settings_store.save(config)
        except (OSError, ValueError) as exc:
            self._set_settings_status(f"保存失败：{exc}", kind="error")
            messagebox.showerror("保存失败", str(exc), parent=self._settings_dialog)
            return
        self.user_settings = config
        next_runtime_mode = self._effective_runtime_mode(config.display_mode)
        self._set_settings_status(
            "已保存到本地配置；预算和价格会自动刷新。"
            if next_runtime_mode == self._active_display_mode()
            else "已保存到本地配置；当前会话仍保持 Tk，Renderer 方案会在下次切换或启动时生效。"
        )

    def _settings_fetch_prices(
        self,
        entries: dict[str, SettingsEntry],
        price_rows: list[dict[str, tk.Entry]],
        prices_parent: tk.Misc,
    ) -> None:
        url = entries["pricing_url"].get().strip()
        try:
            fetched = fetch_model_prices(url)
            config = self._config_from_settings_dialog(entries, price_rows).with_price_updates(
                fetched,
                pricing_url=url,
            )
            self.user_settings_store.save(config)
        except (OSError, ValueError) as exc:
            self._set_settings_status(f"拉取失败：{exc}", kind="error")
            messagebox.showerror("拉取失败", str(exc), parent=self._settings_dialog)
            return
        self.user_settings = config
        self._replace_price_rows(prices_parent, price_rows, config.model_prices)
        self._set_settings_status(f"已拉取并保存 {len(fetched)} 个模型价格。")

    def _settings_export(
        self,
        entries: dict[str, SettingsEntry],
        price_rows: list[dict[str, tk.Entry]],
    ) -> None:
        try:
            config = self._config_from_settings_dialog(entries, price_rows)
            payload = json.dumps({"user": config.to_dict()}, indent=2, ensure_ascii=False)
            self.root.clipboard_clear()
            self.root.clipboard_append(payload)
            self.root.update()
        except (tk.TclError, ValueError) as exc:
            self._set_settings_status(f"导出失败：{exc}", kind="error")
            return
        self._set_settings_status("设置 JSON 已复制到剪贴板。")

    def _confirm_full_exit(self) -> None:
        message = (
            "这会完全退出 HUD，并停止后台守护进程（如果当前正在运行）。"
            "\n\n是否继续？"
        )
        if not messagebox.askyesno("退出 HUD", message, parent=self._settings_dialog):
            self._set_settings_status("已取消退出。")
            return
        self._set_settings_status("正在退出 HUD...")
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.destroy()
        self.close("settings_exit")

    def _open_support_dialog(self) -> None:
        self._open_settings_dialog("support")

    def _open_support_image(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self._settings_dialog)

    def _rebuild_top_ui(self) -> None:
        for child in self.root.winfo_children():
            if child is getattr(self, "request_root", None):
                continue
            child.destroy()
        self.top_labels.clear()
        self._top_update_button = None
        outside_color = _window_outside_color()
        self.root.configure(bg=outside_color)
        self._configure_transparent_window(self.root, outside_color)
        shell = RoundedHudShell(
            self.root,
            bg=HUD_BG,
            border=HUD_PANEL_BORDER,
            outside=outside_color,
            radius=HUD_SHELL_RADIUS,
            padx=5,
            pady=1,
        )
        shell.pack(fill="both", expand=True)
        frame = shell.content
        if self.top_expanded:
            self._build_top_expanded(frame)
        else:
            self._build_top_collapsed(frame)
        self._render_top()

    def _build_top_collapsed(self, frame: tk.Frame) -> None:
        controls = tk.Frame(frame, bg=HUD_BG)
        controls.pack(side="left", padx=(0, 4))
        self._move_handle(controls, "top", self.root).pack(side="left")
        self._update_button(controls).pack(side="left", padx=(4, 0))
        self._settings_button(frame).pack(side="right", padx=(4, 0))
        self.top_labels["bar"] = TopHudProgressStrip(frame)
        self.top_labels["bar"].pack(side="left", fill="both", expand=True)

    def _build_top_expanded(self, frame: tk.Frame) -> None:
        header = tk.Frame(frame, bg=HUD_HEADER_BG, padx=6, pady=3)
        header.pack(fill="x", pady=(0, 7))
        controls = tk.Frame(header, bg=HUD_HEADER_BG)
        controls.pack(side="left", padx=(0, 4))
        self._move_handle(controls, "top", self.root).pack(side="left")
        self._update_button(controls).pack(side="left", padx=(4, 0))
        self._settings_button(header).pack(side="right", padx=(4, 0))
        cache_progress = TopHudProgressBar(
            header,
            height=24,
            width=148,
            bg=HUD_HEADER_BG,
            font=("Microsoft YaHei UI", 8, "bold"),
            padding_x=10,
            radius=HUD_PROGRESS_RADIUS,
        )
        cache_progress.pack(side="right", fill="y", padx=(8, 4))
        self.top_labels["cache_progress"] = cache_progress
        self.top_labels["title"] = tk.Label(
            header,
            text=TOP_EXPANDED_HEADER_FALLBACK,
            anchor="w",
            bg=HUD_HEADER_BG,
            fg=HUD_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.top_labels["title"].pack(side="left")
        self.top_labels["session"] = tk.Label(
            header,
            text="",
            anchor="e",
            justify="right",
            bg=HUD_HEADER_BG,
            fg=HUD_MUTED,
            font=("Microsoft YaHei UI", 8),
        )
        self.top_labels["session"].pack(side="left", fill="x", expand=True, padx=(12, 0))

        body = tk.Frame(frame, bg=HUD_BG)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            body,
            bg=HUD_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = HudScrollbar(
            body,
            command=canvas.yview,
            track=HUD_BG,
            thumb=HUD_DIVIDER,
            thumb_hover=HUD_PANEL_BORDER,
            width=8,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=HUD_BG)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def section(parent: tk.Misc, text: str, *, bg: str = HUD_BG) -> None:
            tk.Label(
                parent,
                text=text,
                anchor="w",
                justify="left",
                bg=bg,
                fg=HUD_MUTED,
                font=("Microsoft YaHei UI", 7, "bold"),
            ).pack(fill="x", pady=(0, 1))

        def divider(parent: tk.Misc, *, bg: str = HUD_BG) -> None:
            del bg
            tk.Frame(parent, bg=HUD_DIVIDER, height=1).pack(fill="x", pady=(4, 5))

        def dynamic_label(
            key: str,
            parent: tk.Misc,
            *,
            fg: str,
            font: tuple[str, int] | tuple[str, int, str],
            bg: str = HUD_BG,
            pady: tuple[int, int] = (0, 3),
        ) -> tk.Label:
            label = tk.Label(
                parent,
                text="",
                anchor="w",
                justify="left",
                bg=bg,
                fg=fg,
                font=font,
                wraplength=300,
            )

            def sync_label_wrap(_event: tk.Event[tk.Misc], widget: tk.Label = label) -> None:
                widget.update_idletasks()
                parent_width = widget.master.winfo_width() if widget.master is not None else 0
                label_width = widget.winfo_width()
                widths = [value for value in (label_width, parent_width) if int(value) > 0]
                available_width = min(widths) if widths else max(label_width, parent_width, 0)
                wraplength = max(96, int(available_width) - 4)
                try:
                    current = int(float(str(widget.cget("wraplength"))))
                except (tk.TclError, TypeError, ValueError):
                    current = -1
                if current != wraplength:
                    widget.configure(wraplength=wraplength)

            label.bind("<Configure>", sync_label_wrap, add="+")
            if parent is not None:
                parent.bind("<Configure>", sync_label_wrap, add="+")
            if key == "slow":
                setattr(label, "_hud_handle", True)
                label.bind("<Button-1>", self._copy_slowest_tool_command)
            if key == "gap":
                setattr(label, "_hud_handle", True)
                label.bind("<Button-1>", self._copy_longest_gap_detail)
            label.pack(fill="x", pady=pady)
            self.top_labels[key] = label
            return label

        left = tk.Frame(content, bg=HUD_BG)
        right = tk.Frame(content, bg=HUD_PANEL_BG, padx=8, pady=5)

        def arrange_top_content(width: int) -> None:
            for column in range(2):
                content.columnconfigure(column, weight=0, minsize=0)
            content.rowconfigure(0, weight=0)
            content.rowconfigure(1, weight=0)
            if width < TOP_EXPANDED_STACK_WIDTH:
                right.configure(width=1)
                right.grid_propagate(True)
                left.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
                right.grid(row=1, column=0, sticky="ew", padx=0, pady=(7, 0))
                content.columnconfigure(0, weight=1, minsize=max(1, width))
                return
            side_width = min(260, max(230, int(width * 0.37)))
            left_width = max(1, width - side_width - 12)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=0)
            right.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
            content.columnconfigure(0, weight=1, minsize=left_width)
            content.columnconfigure(1, weight=0, minsize=side_width)
            content.rowconfigure(0, weight=1)
            right.configure(width=side_width)
            right.grid_propagate(False)

        def sync_top_scroll_region(event: tk.Event[tk.Misc]) -> None:
            del event
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_top_canvas_width(event: tk.Event[tk.Misc]) -> None:
            width = max(1, int(event.width))
            canvas.itemconfigure(content_window, width=width)
            arrange_top_content(width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        def scroll_top_body(event: tk.Event[tk.Misc]) -> str | None:
            units = 0
            button = getattr(event, "num", None)
            if button == 4:
                units = -3
            elif button == 5:
                units = 3
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                if delta:
                    units = (-1 if delta > 0 else 1) * max(1, abs(delta) // 120)
            if units:
                canvas.yview_scroll(units, "units")
                return "break"
            return None

        def bind_top_scroll(widget: tk.Misc) -> None:
            widget.bind("<MouseWheel>", scroll_top_body, add="+")
            widget.bind("<Button-4>", scroll_top_body, add="+")
            widget.bind("<Button-5>", scroll_top_body, add="+")
            for child in widget.winfo_children():
                bind_top_scroll(child)

        canvas.bind("<Configure>", sync_top_canvas_width)
        content.bind("<Configure>", sync_top_scroll_region)
        arrange_top_content(600)

        section(left, "实时请求")
        dynamic_label(
            "confirmed",
            left,
            fg=HUD_ACCENT,
            font=("Consolas", 11, "bold"),
            pady=(0, 3),
        )
        dynamic_label(
            "cumulative",
            left,
            fg="#DDE7F2",
            font=("Consolas", 9),
            pady=(0, 3),
        )
        divider(left)
        section(left, "额度")
        budget_progress = TopHudBudgetProgress(left)
        budget_progress.pack(fill="x", pady=(0, 5))
        self.top_labels["budget"] = budget_progress
        section(left, "当前活动")
        dynamic_label(
            "activity",
            left,
            fg=HUD_BLUE,
            font=("Microsoft YaHei UI", 8),
            pady=(0, 4),
        )

        section(right, "提醒", bg=HUD_PANEL_BG)
        dynamic_label(
            "warnings",
            right,
            fg="#FFB86B",
            font=("Microsoft YaHei UI", 8),
            bg=HUD_PANEL_BG,
            pady=(0, 5),
        )
        divider(right, bg=HUD_PANEL_BG)
        section(right, "等待", bg=HUD_PANEL_BG)
        dynamic_label(
            "slow",
            right,
            fg="#DDE7F2",
            font=("Microsoft YaHei UI", 8),
            bg=HUD_PANEL_BG,
            pady=(0, 4),
        )
        dynamic_label(
            "gap",
            right,
            fg="#B9C2CC",
            font=("Microsoft YaHei UI", 8),
            bg=HUD_PANEL_BG,
            pady=(0, 0),
        )
        divider(right, bg=HUD_PANEL_BG)
        section(right, "状态", bg=HUD_PANEL_BG)
        dynamic_label(
            "status",
            right,
            fg="#A9BCD2",
            font=("Microsoft YaHei UI", 8),
            bg=HUD_PANEL_BG,
            pady=(0, 0),
        )
        divider(right, bg=HUD_PANEL_BG)
        section(right, "符号说明", bg=HUD_PANEL_BG)
        dynamic_label(
            "legend",
            right,
            fg="#C7D4E4",
            font=("Microsoft YaHei UI", 8),
            bg=HUD_PANEL_BG,
            pady=(0, 0),
        )
        bind_top_scroll(content)

    def _rebuild_request_ui(self) -> None:
        for child in self.request_root.winfo_children():
            child.destroy()
        outside_color = _window_outside_color()
        self.request_root.configure(bg=outside_color)
        self._configure_transparent_window(self.request_root, outside_color)
        self.request_text = None
        shell = RoundedHudShell(
            self.request_root,
            bg=REQUEST_HUD_BG,
            border=HUD_PANEL_BORDER,
            outside=outside_color,
            radius=HUD_SHELL_RADIUS,
            padx=6,
            pady=2,
        )
        shell.pack(fill="both", expand=True)
        frame = shell.content
        if self.request_expanded:
            self._build_request_expanded(frame)
        else:
            self._build_request_collapsed(frame)
        self._render_request()

    def _build_request_collapsed(self, frame: tk.Frame) -> None:
        frame.configure(bg=REQUEST_HUD_BG, padx=8, pady=4)
        self._move_handle(frame, "request", self.request_root).pack(side="left", padx=(0, 4))
        self.request_label = AutoScrollLabel(
            frame,
            text="↑- ↻- ↓- ◇- ∑- $0.0000",
            bg=REQUEST_HUD_BG,
            fg=HUD_ACCENT,
            font=("Consolas", 9, "bold"),
            animate_numbers=True,
            static_align="left",
        )
        self.request_label.pack(side="left", fill="both", expand=True)

    def _build_request_expanded(self, frame: tk.Frame) -> None:
        frame.configure(bg=REQUEST_HUD_BG, padx=8, pady=5)
        header = tk.Frame(frame, bg=REQUEST_HUD_HEADER_BG, padx=5, pady=2)
        header.pack(fill="x", pady=(0, 4))
        self._move_handle(header, "request", self.request_root).pack(side="left", padx=(0, 4))
        self.request_label = AutoScrollLabel(
            header,
            text="最近模型请求轮次",
            bg=REQUEST_HUD_HEADER_BG,
            fg=HUD_ACCENT,
            font=("Consolas", 9, "bold"),
            animate_numbers=True,
            static_align="left",
        )
        self.request_label.pack(side="left", fill="x", expand=True)

        list_header = tk.Frame(frame, bg=REQUEST_HUD_BG)
        list_header.pack(fill="x", pady=(0, 2))
        tk.Label(
            list_header,
            text="轮次流水",
            anchor="w",
            bg=REQUEST_HUD_BG,
            fg=REQUEST_HUD_MUTED,
            font=("Microsoft YaHei UI", 7, "bold"),
        ).pack(side="left")
        tk.Label(
            list_header,
            text="最新在上",
            anchor="e",
            bg=REQUEST_HUD_BG,
            fg=REQUEST_HUD_MUTED,
            font=("Microsoft YaHei UI", 7),
        ).pack(side="right")

        body = tk.Frame(frame, bg=REQUEST_HUD_PANEL_BG, padx=0, pady=0)
        body.pack(fill="x", expand=False)
        scrollbar = HudScrollbar(
            body,
            track=REQUEST_HUD_PANEL_BG,
            thumb=HUD_DIVIDER,
            thumb_hover=HUD_PANEL_BORDER,
            width=8,
        )
        self.request_text = tk.Text(
            body,
            bg=REQUEST_HUD_PANEL_BG,
            fg=REQUEST_HUD_TEXT,
            insertbackground=REQUEST_HUD_TEXT,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 8),
            height=8,
            wrap="none",
            yscrollcommand=scrollbar.set,
            padx=4,
            pady=2,
            spacing3=0,
            selectbackground=HUD_DIVIDER,
            selectforeground=HUD_TEXT,
        )
        self.request_text.tag_configure("recent", foreground=HUD_ACCENT)
        self.request_text.tag_configure("normal", foreground=REQUEST_HUD_TEXT)
        self.request_text.tag_configure("muted", foreground=REQUEST_HUD_MUTED)
        scrollbar.set_command(self.request_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.request_text.pack(side="left", fill="x", expand=True)
        self.request_text.configure(state="disabled")

    def _release_pointer(self, event: Any) -> str:
        if self._press_at is None:
            return "break"
        dx = abs(event.x_root - self._press_at[0])
        dy = abs(event.y_root - self._press_at[1])
        target = self._press_target
        self._press_at = None
        self._press_target = ""
        self._drag_origin = None
        self._drag_window = None
        if dx <= 4 and dy <= 4:
            if target == "top":
                self.toggle_top_expanded()
            elif target == "request":
                self.toggle_request_expanded()
        return "break"

    def _start_move(self, event: Any, target: str, window: tk.Tk | tk.Toplevel) -> str:
        self._move_target = target
        self._drag_window = window
        self._drag_origin = (event.x_root, event.y_root)
        _HUD_GEOMETRY_LOGGER.info(
            "move_start target=%s x=%s y=%s width=%s",
            target,
            window.winfo_x(),
            window.winfo_y(),
            window.winfo_width(),
        )
        return "break"

    def _move_window(self, event: Any) -> str:
        if self._drag_origin is None or self._drag_window is None:
            return "break"
        self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
        old_x, old_y = self._drag_origin
        dx = event.x_root - old_x
        dy = event.y_root - old_y
        self._drag_origin = (event.x_root, event.y_root)
        x = self._drag_window.winfo_x() + dx
        y = self._drag_window.winfo_y() + dy
        self._drag_window.geometry(f"+{x}+{y}")
        if self._drag_window == self.root:
            self._top_manual_position = (x, y)
        else:
            self._request_manual_position = (x, y)
        return "break"

    def _finish_move(self, event: Any) -> str:
        del event
        target = self._move_target
        window = self._drag_window
        self._move_target = ""
        self._drag_window = None
        self._drag_origin = None
        if target and window is not None:
            self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
            self._remember_window_position(target, window, reason="move")
            self._save_settings()
        return "break"

    def _start_resize(
        self,
        event: Any,
        target: str,
        window: tk.Tk | tk.Toplevel,
        edge: str,
    ) -> str:
        self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
        self._resize_target = target
        self._resize_window = window
        self._resize_edge = edge
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._resize_start_left = window.winfo_x()
        self._resize_start_top = window.winfo_y()
        self._resize_start_width = max(
            self._interactive_min_width(
                target,
                self.top_expanded if target == "top" else self.request_expanded,
            ),
            window.winfo_width(),
        )
        self._resize_start_height = max(
            self._interactive_min_height(
                target,
                self.top_expanded if target == "top" else self.request_expanded,
            ),
            window.winfo_height(),
        )
        _HUD_GEOMETRY_LOGGER.info(
            "resize_start target=%s edge=%s x=%s y=%s width=%s height=%s",
            target,
            edge,
            window.winfo_x(),
            window.winfo_y(),
            window.winfo_width(),
            window.winfo_height(),
        )
        return "break"

    def _resize_window_size(self, event: Any) -> str:
        if self._resize_window is None:
            return "break"
        self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
        dx = event.x_root - self._resize_start_x
        dy = event.y_root - self._resize_start_y
        expanded = (
            self.top_expanded
            if self._resize_target == "top"
            else self.request_expanded
        )
        min_width = self._interactive_min_width(
            self._resize_target,
            expanded,
        )
        min_height = self._interactive_min_height(self._resize_target, expanded)
        start_right = self._resize_start_left + self._resize_start_width
        start_bottom = self._resize_start_top + self._resize_start_height
        width = self._resize_start_width
        height = self._resize_start_height
        x = self._resize_start_left
        y = self._resize_start_top
        edge = self._resize_edge
        if edge in {"left", "bottom-left", "top-left"}:
            width = max(min_width, self._resize_start_width - dx)
            x = start_right - width
        elif edge in {"right", "bottom-right", "top-right"}:
            width = max(min_width, self._resize_start_width + dx)
        if expanded and edge in {"bottom-left", "bottom-right"}:
            height = max(min_height, self._resize_start_height + dy)
        elif expanded and edge in {"top-left", "top-right"}:
            height = max(min_height, self._resize_start_height - dy)
            y = start_bottom - height
        self._resize_window.geometry(f"{width}x{height}+{x}+{y}")
        return "break"

    def _finish_resize(self, event: Any) -> str:
        del event
        target = self._resize_target
        window = self._resize_window
        self._resize_target = ""
        self._resize_window = None
        self._resize_edge = ""
        if target and window is not None:
            self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
            self._remember_window_position(target, window, reason="resize")
            self._remember_window_width(target, window, reason="resize")
            self._remember_window_height(target, window, reason="resize")
            self._save_settings()
            self._apply_geometry()
        return "break"

    def toggle_top_expanded(self) -> None:
        self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
        self.top_expanded = not self.top_expanded
        self._apply_geometry()
        self._schedule_top_rebuild()

    def toggle_request_expanded(self) -> None:
        self._mark_ui_interaction(duration_ms=HUD_INTERACTION_SETTLE_MS)
        self.request_expanded = not self.request_expanded
        self._apply_geometry()
        self._schedule_request_rebuild()

    def _schedule_top_rebuild(self) -> None:
        if self._top_rebuild_job is not None:
            return
        try:
            self._top_rebuild_job = self.root.after_idle(self._flush_top_rebuild)
        except tk.TclError:
            self._top_rebuild_job = None

    def _flush_top_rebuild(self) -> None:
        self._top_rebuild_job = None
        self._rebuild_top_ui()
        self._apply_geometry()

    def _schedule_request_rebuild(self) -> None:
        if self._request_rebuild_job is not None:
            return
        try:
            self._request_rebuild_job = self.root.after_idle(self._flush_request_rebuild)
        except tk.TclError:
            self._request_rebuild_job = None

    def _flush_request_rebuild(self) -> None:
        self._request_rebuild_job = None
        self._rebuild_request_ui()
        self._apply_geometry()

    def _follow_codex_window(self) -> None:
        self._follow_job = None
        self.sync_codex_window()
        try:
            self._follow_job = self.root.after(
                self._next_follow_delay(),
                self._follow_codex_window,
            )
        except tk.TclError:
            self._follow_job = None

    def sync_codex_window(self) -> None:
        """Synchronize HUD visibility and geometry with the current Codex window."""
        if self._move_target or self._resize_target:
            return
        now = time.monotonic()
        if self._ui_interaction_active(now):
            if self._attached and self._last_rect is not None:
                self._apply_geometry()
            return
        rect = self.locator.find()
        if rect is None:
            if self.hide_until_attached:
                self._enter_tombstone("waiting")
            else:
                self._enter_free_mode()
        elif rect.minimized:
            self._hide_for_minimized()
        else:
            self._attach_to_rect(rect)

    def _attach_to_rect(self, rect: WindowRect) -> None:
        self._attached = True
        self._last_rect = rect
        active = self.locator.is_active(rect, self._hud_hwnds())
        if not active:
            self._enter_tombstone("inactive")
            return
        self._exit_tombstone("attached")
        self._apply_focus_state(True)
        self._set_alpha(self.root, 0.94)
        self._set_alpha(self.request_root, 0.94 if self.request_expanded else 0.74)
        self._apply_geometry()

    def _enter_free_mode(self) -> None:
        self._exit_tombstone("free-mode")
        self._attached = False
        self._last_rect = None
        self._apply_focus_state(True)
        self._set_alpha(self.root, 0.90)
        self._set_alpha(self.request_root, 0.76 if not self.request_expanded else 0.90)
        if self._top_manual_position is None or self._request_manual_position is None:
            self._apply_free_defaults()

    def _hide_for_minimized(self) -> None:
        self._enter_tombstone("minimized")
        self._hidden_for_minimized = True

    def _enter_tombstone(self, reason: str = "inactive") -> None:
        """Hide HUD chrome while Codex is not foreground and pause expensive refreshes."""
        if self._tombstoned and self._hidden_reason == reason:
            return
        self._focus_state_active = None
        try:
            self.root.withdraw()
            self.request_root.withdraw()
        except tk.TclError:
            return
        self._tombstoned = True
        self._hidden_reason = reason
        _HUD_GEOMETRY_LOGGER.info("hud_hidden reason=%s", reason)

    def _exit_tombstone(self, reason: str = "visible") -> None:
        if not self._tombstoned and not self._hidden_for_minimized:
            if self._hud_windows_withdrawn():
                self._ensure_hud_visible(reason)
            return
        previous_reason = self._hidden_reason
        if not self._ensure_hud_visible(reason):
            return
        self._tombstoned = False
        self._hidden_for_minimized = False
        self._hidden_reason = ""
        _HUD_GEOMETRY_LOGGER.info(
            "hud_shown reason=%s previous_reason=%s",
            reason,
            previous_reason,
        )

    def _ensure_hud_visible(self, reason: str = "visible") -> bool:
        try:
            self.root.deiconify()
            self.request_root.deiconify()
        except tk.TclError:
            return False
        try:
            if self.root.state() != "withdrawn" and self.request_root.state() != "withdrawn":
                _HUD_GEOMETRY_LOGGER.info("hud_visibility_recovered reason=%s", reason)
        except tk.TclError:
            return False
        return True

    def _hud_windows_withdrawn(self) -> bool:
        try:
            return self.root.state() == "withdrawn" or self.request_root.state() == "withdrawn"
        except tk.TclError:
            return False

    def _set_window_topmost(self, window: tk.Tk | tk.Toplevel, enabled: bool) -> None:
        try:
            window.attributes("-topmost", bool(enabled))
        except tk.TclError:
            pass

    def _apply_focus_state(self, active: bool) -> None:
        if self._focus_state_active is active:
            return
        self._focus_state_active = active
        windows: list[tk.Tk | tk.Toplevel] = [self.root, self.request_root]
        for window in windows:
            self._set_window_topmost(window, active)
            try:
                if active:
                    window.lift()
                else:
                    window.lower()
            except tk.TclError:
                continue
        dialog = self._settings_dialog
        if dialog is not None and dialog.winfo_exists():
            try:
                if active:
                    self._raise_settings_dialog()
                else:
                    self._set_window_topmost(dialog, False)
                    dialog.lower()
            except tk.TclError:
                return

    def _next_follow_delay(self) -> int:
        if self._tombstoned:
            return self.tombstone_follow_ms
        if self._focus_state_active is False:
            return self.inactive_follow_ms
        return self.follow_ms

    def should_refresh_snapshot(self) -> bool:
        """Return whether parser refresh work should run for the visible HUD."""
        return not self._tombstoned

    def refresh_delay_ms(self, normal_delay_ms: int) -> int:
        """Throttle parser refreshes while the HUD is hidden in tombstone mode."""
        delay = max(100, int(normal_delay_ms))
        if self._tombstoned:
            return max(delay, FOLLOW_TOMBSTONE_MS)
        return delay

    def _apply_geometry(self) -> None:
        if self._attached and self._last_rect is not None:
            top = self._attached_geometry("top", self._last_rect, self.top_expanded)
            request = self._attached_geometry(
                "request", self._last_rect, self.request_expanded
            )
            self._apply_window_geometry("top", self.root, top)
            self._apply_window_geometry("request", self.request_root, request)
            return
        self._apply_free_defaults(keep_existing=True)

    def _apply_window_geometry(
        self,
        target: str,
        window: tk.Tk | tk.Toplevel,
        geometry: tuple[int, int, int, int],
    ) -> None:
        """Avoid asking Tk to re-apply identical screen geometry every frame."""
        if self._last_applied_geometry.get(target) == geometry:
            return
        if _set_native_window_geometry(window, geometry):
            backend = "native-setwindowpos"
        else:
            x, y, width, height = geometry
            window.geometry(f"{width}x{height}+{x}+{y}")
            backend = "tk-geometry"
        if self._last_geometry_backend.get(target) != backend:
            self._last_geometry_backend[target] = backend
            _HUD_GEOMETRY_LOGGER.info(
                "geometry_backend target=%s backend=%s",
                target,
                backend,
            )
        self._last_applied_geometry[target] = geometry

    def _attached_geometry(
        self, target: str, rect: WindowRect, expanded: bool
    ) -> tuple[int, int, int, int]:
        legacy_x, legacy_y, legacy_width, height = _visual_anchor_geometry(
            target,
            rect,
            expanded,
        )
        placement = self._placement(target)
        default_height = height
        height = self._window_height(target, expanded, placement)
        if target != "top" and height != default_height:
            legacy_y -= height - default_height
        anchor = self._target_anchor(
            target,
            rect,
            expanded,
            legacy_x,
            legacy_y,
            legacy_width,
            height,
        )
        has_anchor_position = self._has_anchor_position(placement, anchor)
        has_width_ratio = (
            placement.width_ratio is not None
            and (
                placement.anchor_source is None
                or placement.anchor_source == anchor.source
            )
        )
        width_base = (
            anchor.width
            if has_width_ratio and placement.anchor_source == anchor.source
            else legacy_width
        )
        auto_width = self._top_collapsed_auto_width() if target == "top" and not expanded else None

        if auto_width is not None:
            width = auto_width
            width_mode = "content-auto"
        elif has_width_ratio:
            width = int(round(width_base * placement.width_ratio))
            width_mode = (
                "anchor-ratio"
                if placement.anchor_source == anchor.source
                else "legacy-ratio"
            )
        else:
            width = placement.width or anchor.default_width
            width_mode = "saved-width" if placement.width else "anchor-default"
        x = anchor.default_x
        y = anchor.default_y
        position_mode = "anchor-default"

        if has_anchor_position:
            position_mode = "anchor-ratio"
            x = anchor.left + int(round(anchor.width * float(placement.anchor_x_ratio or 0.0)))
            if target == "top":
                y = anchor.top + int(round(anchor.height * float(placement.anchor_y_ratio or 0.0)))
            else:
                bottom_y = anchor.top + int(
                    round(anchor.height * float(placement.anchor_y_ratio or 0.0))
                )
                y = bottom_y - height
        elif target == "top":
            if placement.relative_x_ratio is not None and placement.relative_y_ratio is not None:
                position_mode = "window-relative"
                x = rect.left + int(round(rect.width * placement.relative_x_ratio))
                y = rect.top + int(round(rect.height * placement.relative_y_ratio))
        elif (
            placement.relative_x_ratio is not None
            and placement.relative_bottom_ratio is not None
        ):
            position_mode = "window-relative"
            x = rect.left + int(round(rect.width * placement.relative_x_ratio))
            bottom = int(round(rect.height * placement.relative_bottom_ratio))
            y = rect.bottom - bottom - height
        if self._maybe_reanchor_geometry_to_dom(
            target,
            placement,
            anchor,
            x,
            y,
            width,
            height,
        ):
            position_mode = "auto-reanchored"
            width_mode = "anchor-ratio"
        self._log_anchor_decision(
            target,
            anchor,
            placement,
            position_mode,
            width_mode,
        )

        min_width = self._interactive_min_width(target, expanded)
        min_x = rect.left + 8
        max_x = max(min_x, rect.right - min_width - 12)
        original_x = x
        original_y = y
        original_width = width
        original_height = height
        x = max(min_x, min(x, max_x))
        min_y = rect.top + 8
        max_height = max(
            self._interactive_min_height(target, expanded),
            rect.bottom - min_y - 8,
        )
        height = min(height, max_height)
        max_y = max(min_y, rect.bottom - height - 8)
        y = max(min_y, min(y, max_y))
        max_width = max(1, rect.right - x - 12)
        width = max(1, min(max(min_width, width), max_width))
        if (
            x != original_x
            or y != original_y
            or width != original_width
            or height != original_height
        ):
            signature = (
                original_x,
                x,
                original_y,
                y,
                original_width,
                width,
                original_height,
                height,
                anchor.source,
            )
            should_log = self._last_geometry_clamp.get(target) != signature
            self._last_geometry_clamp[target] = signature
        else:
            should_log = False
            self._last_geometry_clamp.pop(target, None)
        if should_log:
            _HUD_GEOMETRY_LOGGER.info(
                "geometry_clamp target=%s source=%s x=%s->%s y=%s->%s width=%s->%s "
                "height=%s->%s anchor=(%s,%s,%s,%s)",
                target,
                anchor.source,
                original_x,
                x,
                original_y,
                y,
                original_width,
                width,
                original_height,
                height,
                anchor.left,
                anchor.top,
                anchor.right,
                anchor.bottom,
            )
        return x, y, width, height

    def _log_anchor_decision(
        self,
        target: str,
        anchor: HudAnchor,
        placement: WindowPlacement,
        position_mode: str,
        width_mode: str,
    ) -> None:
        signature = (
            anchor.source,
            placement.anchor_source or "",
            position_mode,
            width_mode,
        )
        if self._last_anchor_decisions.get(target) == signature:
            return
        self._last_anchor_decisions[target] = signature
        _HUD_GEOMETRY_LOGGER.info(
            "anchor_decision target=%s anchor_source=%s placement_source=%s "
            "position_mode=%s width_mode=%s anchor=(%s,%s,%s,%s)",
            target,
            anchor.source,
            placement.anchor_source or "-",
            position_mode,
            width_mode,
            anchor.left,
            anchor.top,
            anchor.right,
            anchor.bottom,
        )

    def _maybe_reanchor_geometry_to_dom(
        self,
        target: str,
        placement: WindowPlacement,
        anchor: HudAnchor,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        if not _env_flag(HUD_AUTO_REANCHOR_ENV):
            return False
        if placement.anchor_source != "geometry":
            return False
        if not anchor.source.startswith("cdp:"):
            return False
        if anchor.width <= 0 or anchor.height <= 0:
            return False
        previous_source = placement.anchor_source
        previous_x_ratio = placement.anchor_x_ratio
        previous_y_ratio = placement.anchor_y_ratio
        previous_width_ratio = placement.width_ratio
        placement.anchor_x_ratio = (x - anchor.left) / max(1, anchor.width)
        if target == "top":
            placement.anchor_y_ratio = (y - anchor.top) / max(1, anchor.height)
        else:
            placement.anchor_y_ratio = ((y + height) - anchor.top) / max(
                1,
                anchor.height,
            )
        placement.width_ratio = width / max(1, anchor.width)
        placement.anchor_source = anchor.source
        _HUD_GEOMETRY_LOGGER.info(
            "anchor_reanchored target=%s from_source=%s to_source=%s "
            "anchor_x_ratio=%s->%.6f anchor_y_ratio=%s->%.6f width_ratio=%s->%.6f",
            target,
            previous_source,
            anchor.source,
            previous_x_ratio,
            placement.anchor_x_ratio,
            previous_y_ratio,
            placement.anchor_y_ratio,
            previous_width_ratio,
            placement.width_ratio,
        )
        return True

    def _target_anchor(
        self,
        target: str,
        rect: WindowRect,
        expanded: bool,
        legacy_x: int | None = None,
        legacy_y: int | None = None,
        legacy_width: int | None = None,
        height: int | None = None,
    ) -> HudAnchor:
        if legacy_x is None or legacy_y is None or legacy_width is None or height is None:
            legacy_x, legacy_y, legacy_width, height = _visual_anchor_geometry(
                target,
                rect,
                expanded,
            )
        if (
            self._use_dom_anchors
            or self._use_native_anchors
            or (target == "top" and self._use_top_dom_anchors)
        ):
            if self._ui_interaction_active():
                projected = self._projected_stable_native_anchor(target, rect)
                if projected is not None:
                    return projected
                return _fallback_hud_anchor(
                    target,
                    rect,
                    legacy_x,
                    legacy_y,
                    legacy_width,
                    height,
                )
            native = self._stable_native_anchor(target, rect, height)
            if native is not None:
                return native
        return _fallback_hud_anchor(
            target,
            rect,
            legacy_x,
            legacy_y,
            legacy_width,
            height,
        )

    def _stable_native_anchor(
        self,
        target: str,
        rect: WindowRect,
        hud_height: int,
    ) -> HudAnchor | None:
        projected = self._projected_stable_native_anchor(target, rect)
        state = self._stable_native_anchors.get(target)
        if (
            state is not None
            and projected is not None
            and state.window_width == rect.width
            and state.window_height == rect.height
            and (state.window_left != rect.left or state.window_top != rect.top)
        ):
            return projected

        native = self.locator.anchor_geometry(target, rect, hud_height)
        if native is None:
            self._native_anchor_candidates.pop(target, None)
            return projected

        signature = _relative_anchor_signature(native, rect)
        previous = self._native_anchor_candidates.get(target)
        frames = (
            previous.frames + 1
            if previous is not None and previous.signature == signature
            else 1
        )
        state = _NativeAnchorState(
            signature=signature,
            frames=frames,
            anchor=native,
            window_left=rect.left,
            window_top=rect.top,
            window_width=rect.width,
            window_height=rect.height,
        )
        self._native_anchor_candidates[target] = state
        if frames >= NATIVE_ANCHOR_STABLE_FRAMES:
            previous_stable = self._stable_native_anchors.get(target)
            if previous_stable is None or previous_stable.signature != signature:
                _HUD_GEOMETRY_LOGGER.info(
                    "native_anchor_stable target=%s source=%s frames=%s "
                    "window=(%s,%s,%s,%s) anchor=(%s,%s,%s,%s)",
                    target,
                    native.source,
                    frames,
                    rect.left,
                    rect.top,
                    rect.right,
                    rect.bottom,
                    native.left,
                    native.top,
                    native.right,
                    native.bottom,
                )
            self._stable_native_anchors[target] = state
            return native

        return projected

    def _projected_stable_native_anchor(
        self,
        target: str,
        rect: WindowRect,
    ) -> HudAnchor | None:
        state = self._stable_native_anchors.get(target)
        if state is None:
            return None
        source_rect = WindowRect(
            left=state.window_left,
            top=state.window_top,
            right=state.window_left + state.window_width,
            bottom=state.window_top + state.window_height,
        )
        return _project_hud_anchor(
            state.anchor,
            source_rect,
            rect,
        )

    @staticmethod
    def _has_anchor_position(placement: WindowPlacement, anchor: HudAnchor) -> bool:
        return (
            placement.anchor_x_ratio is not None
            and placement.anchor_y_ratio is not None
            and placement.anchor_source == anchor.source
        )

    @staticmethod
    def _interactive_min_width(target: str, expanded: bool) -> int:
        if target == "top":
            return (
                TOP_DOCK_EXPANDED_INTERACTIVE_MIN_WIDTH
                if expanded
                else TOP_DOCK_INTERACTIVE_MIN_WIDTH
            )
        return (
            REQUEST_DOCK_EXPANDED_INTERACTIVE_MIN_WIDTH
            if expanded
            else REQUEST_DOCK_INTERACTIVE_MIN_WIDTH
        )

    @staticmethod
    def _interactive_min_height(target: str, expanded: bool) -> int:
        if target == "top":
            return (
                TOP_DOCK_EXPANDED_INTERACTIVE_MIN_HEIGHT
                if expanded
                else TOP_DOCK_HEIGHT
            )
        return (
            REQUEST_DOCK_EXPANDED_INTERACTIVE_MIN_HEIGHT
            if expanded
            else REQUEST_DOCK_HEIGHT
        )

    def _window_height(
        self,
        target: str,
        expanded: bool,
        placement: WindowPlacement | None = None,
    ) -> int:
        if target == "top":
            default = TOP_DOCK_EXPANDED_HEIGHT if expanded else TOP_DOCK_HEIGHT
        else:
            default = REQUEST_DOCK_EXPANDED_HEIGHT if expanded else REQUEST_DOCK_HEIGHT
        if not expanded:
            return default
        custom = (placement or self._placement(target)).height
        return max(self._interactive_min_height(target, expanded), custom or default)

    def _apply_free_defaults(self, keep_existing: bool = False) -> None:
        screen_width = self.root.winfo_screenwidth()
        top_width, top_height = self._top_size()
        request_width, request_height = self._request_size()
        if self._top_manual_position is None or not keep_existing:
            if (
                self.settings.top.absolute_x is not None
                and self.settings.top.absolute_y is not None
            ):
                self._top_manual_position = (
                    self.settings.top.absolute_x,
                    self.settings.top.absolute_y,
                )
            else:
                self._top_manual_position = (
                    max(20, screen_width - top_width - 40),
                    48,
                )
        if self._request_manual_position is None or not keep_existing:
            if (
                self.settings.request.absolute_x is not None
                and self.settings.request.absolute_y is not None
            ):
                self._request_manual_position = (
                    self.settings.request.absolute_x,
                    self.settings.request.absolute_y,
                )
            else:
                self._request_manual_position = (
                    max(20, screen_width - request_width - 40),
                    48 + top_height + 8,
                )
        tx, ty = self._top_manual_position
        rx, ry = self._request_manual_position
        self._apply_window_geometry("top", self.root, (tx, ty, top_width, top_height))
        self._apply_window_geometry(
            "request",
            self.request_root,
            (rx, ry, request_width, request_height),
        )

    def _top_size(self) -> tuple[int, int]:
        expanded = self.top_expanded
        auto_width = self._top_collapsed_auto_width() if not expanded else None
        if auto_width is not None:
            width = auto_width
        else:
            width = max(
                self._interactive_min_width("top", expanded),
                self.settings.top.width or (480 if not self.compact else 360),
            )
        return (
            width,
            self._window_height("top", expanded, self.settings.top),
        )

    def _top_collapsed_auto_width(self) -> int | None:
        return self._top_collapsed_width_override

    def _measure_top_collapsed_auto_width(self) -> int | None:
        if self.top_expanded:
            return None
        bar = self.top_labels.get("bar")
        if not isinstance(bar, TopHudProgressStrip) or not self.root.winfo_exists():
            return None
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return None
        return max(
            self._interactive_min_width("top", False),
            int(self.root.winfo_reqwidth()),
        )

    def _request_size(self) -> tuple[int, int]:
        expanded = self.request_expanded
        return (
            max(
                self._interactive_min_width("request", expanded),
                self.settings.request.width or REQUEST_DOCK_WIDTH,
            ),
            self._window_height("request", expanded, self.settings.request),
        )

    def _placement(self, target: str) -> WindowPlacement:
        return self.settings.top if target == "top" else self.settings.request

    def _remember_window_position(
        self,
        target: str,
        window: tk.Tk | tk.Toplevel,
        *,
        reason: str,
    ) -> None:
        placement = self._placement(target)
        x = int(window.winfo_x())
        y = int(window.winfo_y())
        height = int(window.winfo_height())
        placement.absolute_x = x
        placement.absolute_y = y
        if target == "top":
            self._top_manual_position = (x, y)
        else:
            self._request_manual_position = (x, y)

        if self._attached and self._last_rect is not None:
            anchor = self._target_anchor(
                target,
                self._last_rect,
                self.top_expanded if target == "top" else self.request_expanded,
            )
            placement.relative_x = x - self._last_rect.left
            placement.relative_x_ratio = placement.relative_x / max(1, self._last_rect.width)
            placement.anchor_x_ratio = (x - anchor.left) / max(1, anchor.width)
            placement.anchor_source = anchor.source
            if target == "top":
                placement.relative_y = y - self._last_rect.top
                placement.relative_y_ratio = (
                    placement.relative_y / max(1, self._last_rect.height)
                )
                placement.relative_bottom = None
                placement.relative_bottom_ratio = None
                placement.anchor_y_ratio = (y - anchor.top) / max(1, anchor.height)
            else:
                placement.relative_y = None
                placement.relative_y_ratio = None
                placement.relative_bottom = self._last_rect.bottom - (y + height)
                placement.relative_bottom_ratio = (
                    placement.relative_bottom / max(1, self._last_rect.height)
                )
                placement.anchor_y_ratio = ((y + height) - anchor.top) / max(
                    1,
                    anchor.height,
                )
            _HUD_GEOMETRY_LOGGER.info(
                "position_saved target=%s reason=%s source=%s x=%s y=%s "
                "anchor_x_ratio=%.6f anchor_y_ratio=%.6f width_ratio=%s",
                target,
                reason,
                anchor.source,
                x,
                y,
                placement.anchor_x_ratio,
                placement.anchor_y_ratio,
                placement.width_ratio,
            )
        else:
            placement.relative_x_ratio = None
            placement.relative_y_ratio = None
            placement.relative_bottom_ratio = None
            placement.anchor_x_ratio = None
            placement.anchor_y_ratio = None
            placement.anchor_source = None
            _HUD_GEOMETRY_LOGGER.info(
                "position_saved_free target=%s reason=%s x=%s y=%s width_ratio=%s",
                target,
                reason,
                x,
                y,
                placement.width_ratio,
            )

    def _remember_window_width(
        self,
        target: str,
        window: tk.Tk | tk.Toplevel,
        *,
        reason: str,
    ) -> None:
        placement = self._placement(target)
        expanded = self.top_expanded if target == "top" else self.request_expanded
        width = max(
            self._interactive_min_width(target, expanded),
            int(window.winfo_width()),
        )
        placement.width = width

        if self._attached and self._last_rect is not None:
            anchor = self._target_anchor(target, self._last_rect, expanded)
            placement.width_ratio = width / max(1, anchor.width)
            placement.anchor_source = anchor.source
            _HUD_GEOMETRY_LOGGER.info(
                "width_saved target=%s reason=%s source=%s width=%s "
                "anchor_width=%s width_ratio=%.6f",
                target,
                reason,
                anchor.source,
                width,
                anchor.width,
                placement.width_ratio,
            )
        else:
            placement.width_ratio = None
            _HUD_GEOMETRY_LOGGER.info(
                "width_saved_free target=%s reason=%s width=%s",
                target,
                reason,
                width,
            )

    def _remember_window_height(
        self,
        target: str,
        window: tk.Tk | tk.Toplevel,
        *,
        reason: str,
    ) -> None:
        expanded = self.top_expanded if target == "top" else self.request_expanded
        if not expanded:
            return
        placement = self._placement(target)
        height = max(
            self._interactive_min_height(target, expanded),
            int(window.winfo_height()),
        )
        placement.height = height
        _HUD_GEOMETRY_LOGGER.info(
            "height_saved target=%s reason=%s height=%s",
            target,
            reason,
            height,
        )

    def _save_settings(self) -> None:
        self.settings_store.save(self.settings)

    def _hud_hwnds(self) -> set[int]:
        hwnds: set[int] = set()
        windows: list[tk.Tk | tk.Toplevel] = [self.root, self.request_root]
        dialog = self._settings_dialog
        if dialog is not None and dialog.winfo_exists():
            windows.append(dialog)
        for window in windows:
            try:
                hwnds.add(int(window.winfo_id()))
            except Exception:
                pass
        return hwnds

    def _set_alpha(self, window: tk.Tk | tk.Toplevel, value: float) -> None:
        try:
            window.attributes("-alpha", max(0.25, min(1.0, value)))
        except tk.TclError:
            pass

    def _copy_slowest_tool_command(self, event: object | None = None) -> str:
        del event
        command = _copyable_tool_command(self._snapshot)
        if not command:
            return "break"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(command)
        except tk.TclError:
            return "break"
        return "break"

    def _copy_longest_gap_detail(self, event: object | None = None) -> str:
        del event
        detail = _copyable_gap_detail(self._snapshot)
        if not detail:
            return "break"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(detail)
        except tk.TclError:
            return "break"
        return "break"

    def _close(self, event: object | None = None) -> str:
        del event
        self.close("user")
        return "break"

    @property
    def exit_reason(self) -> str:
        return self._exit_reason

    @property
    def mode_switch_request(self) -> str:
        return self._mode_switch_request

    @property
    def restart_codex_for_renderer(self) -> bool:
        return self._restart_codex_for_renderer

    def close(self, reason: str = "user") -> None:
        """Destroy both HUD windows and cancel Tk timers owned by the widgets."""
        if not self._exit_reason:
            self._exit_reason = reason
        for job in (
            self._top_rebuild_job,
            self._request_rebuild_job,
            self._settings_build_job,
            self._settings_prewarm_job,
            self._settings_loading_anim_job,
            self._settings_update_poll_job,
            self._follow_job,
        ):
            if job is None:
                continue
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._top_rebuild_job = None
        self._request_rebuild_job = None
        self._settings_build_job = None
        self._settings_prewarm_job = None
        self._settings_loading_anim_job = None
        self._settings_update_poll_job = None
        self._follow_job = None
        try:
            self.request_root.destroy()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def update_display(
        self,
        parsed_session: ParsedSession,
        update_state: AutoUpdateState | None = None,
    ) -> None:
        """Refresh both HUD windows with the latest parsed session snapshot."""
        self._snapshot = parsed_session
        if update_state is not None:
            self._update_state = update_state
            if self._settings_dialog_visible() and self._settings_active_tab == "about":
                self._sync_about_update_controls()
        self._log_budget_snapshot(parsed_session)
        self._render_top()
        self._render_request()

    def _log_budget_snapshot(self, snapshot: ParsedSession) -> None:
        day_start = snapshot.day_start.isoformat() if snapshot.day_start is not None else ""
        week_start = snapshot.week_start.isoformat() if snapshot.week_start is not None else ""
        signature = (
            int(round(snapshot.today_cost_usd * 100)),
            int(round(snapshot.week_cost_usd * 100)),
            int(round(snapshot.week_before_today_cost_usd * 100)),
            day_start,
            week_start,
        )
        if signature == self._last_budget_log_signature:
            return
        self._last_budget_log_signature = signature
        _HUD_GEOMETRY_LOGGER.info(
            "budget_snapshot today=%.6f week=%.6f week_before_today=%.6f "
            "day_start=%s week_start=%s",
            snapshot.today_cost_usd,
            snapshot.week_cost_usd,
            snapshot.week_before_today_cost_usd,
            day_start,
            week_start,
        )

    def _render_top(self) -> None:
        snapshot = self._snapshot
        confirmed = snapshot.confirmed
        session_cost = _session_cost(snapshot)
        self._render_update_button()
        title_label = self.top_labels.get("title")
        if title_label is not None:
            title_label.configure(text=_top_expanded_header_title(snapshot))
        bar = self.top_labels.get("bar")
        if bar is not None:
            text = (
                f"{_top_session_usage_summary(snapshot, session_cost)} | "
                f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
                f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
                f"状态 {_budget_status(snapshot)}"
            )
            metrics = _top_collapsed_progress_metrics(snapshot)
            if snapshot.error and snapshot.status in {"missing", "error"}:
                text = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
                metrics = [
                    TopHudProgressMetric(
                        label=text,
                        ratio=1.0,
                        fill=HUD_ERROR,
                        fill_end=HUD_ERROR,
                        fill_text="#1A1012",
                    )
                ]
            bar.configure(text=text, metrics=metrics)
            self._top_collapsed_width_override = (
                self._measure_top_collapsed_auto_width()
                if snapshot.status != "waiting"
                else None
            )
            if (
                not self.top_expanded
                and not self._move_target
                and not self._resize_target
                and hasattr(self, "request_root")
            ):
                self._apply_geometry()
            return

        cache_progress = self.top_labels.get("cache_progress")
        if isinstance(cache_progress, TopHudProgressBar):
            cache_progress.configure(metric=_top_cache_progress_metric(snapshot))

        values = {
            "session": (
                f"会话 {snapshot.session_id[-12:]} | "
                f"行 {snapshot.line_count} | 确认 {snapshot.token_events}"
            ),
            "confirmed": (
                "本次请求  "
                f"{_request_counter(snapshot)}"
            ),
            "cumulative": (
                "累计确认  "
                f"总 {confirmed.cumulative_total:,}   "
                f"输入 {confirmed.cumulative_input:,}   "
                f"缓存 {confirmed.cumulative_cached:,}   "
                f"命中 {_session_cache_hit_rate_label(snapshot)}\n"
                f"输出 {confirmed.cumulative_output:,}   "
                f"推理 {confirmed.cumulative_reasoning:,}   "
                f"金额 {_format_money(session_cost)}"
            ),
            "budget": _top_budget_progress_meta(snapshot),
            "warnings": self._format_notice(snapshot),
            "activity": (
                f"{_activity_label(snapshot.activity.kind)}："
                f"{_compact(snapshot.activity.detail, 135)}"
            ),
            "legend": TOKEN_LEGEND_TEXT,
            "slow": self._format_slow_panel(snapshot),
            "gap": self._format_gap_panel(snapshot),
            "status": (
                f"{_budget_status(snapshot)}\n"
                f"最后 {_format_time(snapshot.last_event_time)}  刷新 {_format_time(snapshot.refreshed_at)}"
            ),
        }
        for key, text in values.items():
            label = self.top_labels.get(key)
            if label is not None:
                if key == "budget" and isinstance(label, TopHudBudgetProgress):
                    label.configure(
                        text=_wrap_long_display_tokens(text),
                        metrics=_top_budget_progress_metrics(snapshot),
                        meta=_top_budget_progress_meta(snapshot),
                    )
                else:
                    label.configure(text=_wrap_long_display_tokens(text))
                if key == "slow":
                    copyable = _copyable_tool_command(snapshot) is not None
                    label.configure(
                        cursor="hand2" if copyable else "arrow",
                        fg="#F3D27A" if copyable else "#DDE7F2",
                    )
                if key == "gap":
                    copyable = _copyable_gap_detail(snapshot) is not None
                    label.configure(
                        cursor="hand2" if copyable else "arrow",
                        fg="#BCD7FF" if copyable else "#B9C2CC",
                    )
                if key == "warnings":
                    has_warning = bool(
                        snapshot.error
                        or snapshot.request.error
                        or snapshot.budget_error
                        or snapshot.budget_warnings
                    )
                    label.configure(fg="#FFB86B" if has_warning else HUD_MUTED)

    def _render_request(self) -> None:
        snapshot = self._snapshot
        if self.request_label is not None:
            text = _request_total_line(snapshot)
            if snapshot.request.error:
                text = f"本次 Token 出错 | {snapshot.request.error}"
            self.request_label.configure(
                text=text,
                fg=HUD_ERROR if snapshot.request.error else HUD_ACCENT,
            )

        if self.request_text is None:
            return
        rows = _task_rows(snapshot)[-30:]
        if not rows:
            rows = [_round_from_snapshot(snapshot)]
        display_rows = list(reversed(rows))
        index_width, money_width, total_width = _round_entry_widths(
            display_rows,
            snapshot.request.model,
        )
        entries = [
            _round_entry(
                item,
                snapshot.request.model,
                index_width=index_width,
                money_width=money_width,
                total_width=total_width,
            )
            for item in display_rows
        ]
        yview = self.request_text.yview()
        should_follow_head = not yview or yview[0] <= 0.02
        previous_top = yview[0] if yview else 0.0
        self.request_text.configure(state="normal")
        self.request_text.delete("1.0", "end")
        if entries:
            for index, entry in enumerate(entries):
                tag = "recent" if index == 0 else "normal"
                suffix = "\n" if index < len(entries) - 1 else ""
                self.request_text.insert("end", entry + suffix, tag)
        else:
            self.request_text.insert(
                "1.0",
                f"本次请求({_request_status_label(snapshot.request.status)})  {_request_counter(snapshot)}",
                "muted",
            )
        self.request_text.configure(state="disabled")
        self.request_text.yview_moveto(0.0 if should_follow_head else previous_top)

    def _format_warnings(self, snapshot: ParsedSession) -> str:
        return _budget_warning_summary(snapshot)

    def _format_notice(self, snapshot: ParsedSession) -> str:
        notice = self._format_warnings(snapshot)
        if snapshot.error:
            notice = f"{notice}  |  错误 {_compact(snapshot.error, 80)}"
        return notice

    def _format_slow_panel(self, snapshot: ParsedSession) -> str:
        return "\n".join(
            [
                f"最慢工具  {snapshot.slow.slowest_tool}",
                f"最慢等待  {snapshot.slow.slowest_user_wait}",
            ]
        )

    def _format_gap_panel(self, snapshot: ParsedSession) -> str:
        detail = snapshot.slow.longest_gap_detail
        if detail is None:
            longest = snapshot.slow.longest_gap
        else:
            longest = _gap_reason_text(snapshot)
        return (
            f"最长响应等待  {longest}\n"
            f"当前  {_current_gap_text(snapshot)}"
        )

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self.root.mainloop()
