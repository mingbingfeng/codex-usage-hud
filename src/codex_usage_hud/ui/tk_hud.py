"""Tkinter HUD with separate top and bottom Codex window docks."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.parser import CostEstimator, ParsedSession, RequestRound

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
HUD_SETTINGS_FILENAME = "hud_settings.json"
FOLLOW_ACTIVE_MS = 16
FOLLOW_TOMBSTONE_MS = 500
MARQUEE_START_PAUSE_MS = 1500
MARQUEE_END_PAUSE_MS = 1500
MARQUEE_STEP_PX = 1
MARQUEE_INTERVAL_MS = 30
COUNTER_ANIMATION_MS = 360
COUNTER_STEP_MS = 30
NUMERIC_TOKEN_RE = re.compile(r"\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?")
LONG_DISPLAY_TOKEN_RE = re.compile(r"[^\s\u4e00-\u9fff，。；、：！？（）]+")
HUD_BG = "#10161D"
HUD_PANEL_BG = "#141B24"
HUD_HEADER_BG = "#202833"
HUD_DIVIDER = "#273241"
HUD_TEXT = "#E8EEF7"
HUD_MUTED = "#8492A6"
HUD_ACCENT = "#F3D27A"
HUD_BLUE = "#9CCBFF"
REQUEST_BG = "#0B1016"
REQUEST_HEADER_BG = "#151D27"
REQUEST_PANEL_BG = "#101821"
REQUEST_TEXT = "#DCE7F2"
REQUEST_MUTED = "#718095"
HUD_GEOMETRY_LOG_FILENAME = "hud_geometry.log"
HUD_NATIVE_ANCHORS_ENV = "CODEX_USAGE_HUD_NATIVE_ANCHORS"

_COST_ESTIMATOR = CostEstimator()
_HUD_GEOMETRY_LOGGER = logging.getLogger("codex_usage_hud.hud_geometry")
_HUD_GEOMETRY_LOGGER.addHandler(logging.NullHandler())
_HUD_GEOMETRY_LOGGING_CONFIGURED = False


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
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return HudSettings.empty()
        return HudSettings.from_dict(raw)

    def save(self, settings: HudSettings) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(settings.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            return


def default_settings_path() -> Path:
    """Return the per-user HUD settings path."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
        return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "codex-usage-hud"
            / HUD_SETTINGS_FILENAME
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME


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
        handler = RotatingFileHandler(
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def _fit_anchor_left(
    window_width: int,
    left_margin: int,
    right_margin: int,
    min_width: int,
) -> int:
    if window_width - left_margin - right_margin >= min_width:
        return left_margin
    return max(8, min(left_margin, window_width - right_margin - min_width))


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
        try:
            import ctypes
            from ctypes import wintypes
            from ..platforms.windows_tracker import CodexWindowTracker

            self.ctypes = ctypes
            self.wintypes = wintypes
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32
            self._native_anchors_enabled = _env_flag(HUD_NATIVE_ANCHORS_ENV)
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


def _round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    index_width: int | None = None,
    money_width: int | None = None,
    total_width: int | None = None,
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
    time_source = item.completed_at or item.started_at
    time_text = "--:--:--" if time_source is None else time_source.astimezone().strftime("%H:%M:%S")
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
    ) -> None:
        self.compact = compact
        self.follow_ms = max(16, int(follow_ms))
        self.tombstone_follow_ms = max(50, int(tombstone_follow_ms))
        self.hide_until_attached = bool(hide_until_attached)
        self.settings_store = HudSettingsStore()
        self.settings = self.settings_store.load()
        self._geometry_log_path = configure_hud_geometry_logging()
        self._use_native_anchors = _env_flag(HUD_NATIVE_ANCHORS_ENV)
        self.locator = CodexWindowLocator()
        self.locator.set_dpi_aware()
        _HUD_GEOMETRY_LOGGER.info(
            "hud_started native_anchors=%s follow_ms=%s settings_path=%s log_path=%s",
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

        self.root = tk.Tk()
        self.root.title("codex-usage-hud")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0E1217")
        self.root.bind("<Escape>", self._close)
        self._exit_reason = ""

        self.top_expanded = False
        self.request_expanded = False
        self._attached = False
        self._hidden_for_minimized = False
        self._hidden_reason = ""
        self._tombstoned = False
        self._last_rect: WindowRect | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._drag_window: tk.Toplevel | tk.Tk | None = None
        self._move_target = ""
        self._resize_target = ""
        self._resize_window: tk.Toplevel | tk.Tk | None = None
        self._resize_start_x = 0
        self._resize_start_y = 0
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
        self._last_budget_log_signature: tuple[int, int, int, str, str] | None = None
        self._snapshot = ParsedSession(status="waiting")

        self.top_labels: dict[str, tk.Misc] = {}
        self.request_label: tk.Misc | None = None
        self.request_text: tk.Text | None = None

        self._rebuild_top_ui()

        self.request_root = tk.Toplevel(self.root)
        self.request_root.title("codex-usage-hud request")
        self.request_root.overrideredirect(True)
        self.request_root.attributes("-topmost", True)
        self.request_root.configure(bg="#0E1217")
        self.request_root.bind("<Escape>", self._close)
        self._rebuild_request_ui()
        self._set_alpha(self.root, 0.94)
        self._set_alpha(self.request_root, 0.74)
        self._apply_free_defaults()
        if self.hide_until_attached:
            self._enter_tombstone("waiting")
            self.sync_codex_window()
        self.root.after(self.follow_ms, self._follow_codex_window)

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

    def _resize_handle(
        self, parent: tk.Misc, target: str, window: tk.Tk | tk.Toplevel
    ) -> tk.Label:
        label = tk.Label(
            parent,
            text="⇲",
            bg=str(parent.cget("bg")),
            fg="#B8C7DE",
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="sb_h_double_arrow",
            width=3,
        )
        setattr(label, "_hud_handle", True)
        label.bind(
            "<ButtonPress-1>",
            lambda event, t=target, w=window: self._start_resize(event, t, w),
        )
        label.bind("<B1-Motion>", self._resize_window_size)
        label.bind("<ButtonRelease-1>", self._finish_resize)
        return label

    def _rebuild_top_ui(self) -> None:
        for child in self.root.winfo_children():
            if child is getattr(self, "request_root", None):
                continue
            child.destroy()
        self.top_labels.clear()
        self.root.configure(bg=HUD_BG)
        frame = tk.Frame(self.root, bg=HUD_BG, padx=8, pady=4)
        frame.pack(fill="both", expand=True)
        if self.top_expanded:
            self._build_top_expanded(frame)
        else:
            self._build_top_collapsed(frame)
        self._bind_click_tree(frame, "top", self.root)
        self._render_top()

    def _build_top_collapsed(self, frame: tk.Frame) -> None:
        self._move_handle(frame, "top", self.root).pack(side="left", padx=(0, 4))
        self._resize_handle(frame, "top", self.root).pack(side="right", padx=(6, 0))
        self.top_labels["bar"] = AutoScrollLabel(
            frame,
            text="读取 token...",
            bg=HUD_BG,
            fg=HUD_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
            static_align="left",
        )
        self.top_labels["bar"].pack(side="left", fill="both", expand=True)

    def _build_top_expanded(self, frame: tk.Frame) -> None:
        header = tk.Frame(frame, bg=HUD_HEADER_BG, padx=6, pady=3)
        header.pack(fill="x", pady=(0, 7))
        self._move_handle(header, "top", self.root).pack(side="left", padx=(0, 4))
        close = tk.Button(
            header,
            text="×",
            command=self._close,
            bg="#2E3846",
            fg=HUD_TEXT,
            relief="flat",
            padx=6,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        )
        close.pack(side="right", padx=(4, 0))
        self._resize_handle(header, "top", self.root).pack(side="right", padx=(6, 0))
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
        scrollbar = tk.Scrollbar(
            body,
            orient="vertical",
            bg=HUD_HEADER_BG,
            troughcolor=HUD_BG,
            activebackground=HUD_DIVIDER,
            width=9,
            relief="flat",
            borderwidth=0,
            elementborderwidth=0,
            highlightthickness=0,
        )
        canvas = tk.Canvas(
            body,
            bg=HUD_BG,
            highlightthickness=0,
            borderwidth=0,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=canvas.yview)
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

            def sync_label_wrap(event: tk.Event[tk.Misc], widget: tk.Label = label) -> None:
                wraplength = max(96, int(event.width) - 4)
                try:
                    current = int(float(str(widget.cget("wraplength"))))
                except (tk.TclError, TypeError, ValueError):
                    current = -1
                if current != wraplength:
                    widget.configure(wraplength=wraplength)

            label.bind("<Configure>", sync_label_wrap, add="+")
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
        dynamic_label(
            "budget",
            left,
            fg="#FFD879",
            font=("Microsoft YaHei UI", 8),
            pady=(0, 5),
        )
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
        self.request_root.configure(bg=REQUEST_BG)
        self.request_text = None
        if self.request_expanded:
            self._build_request_expanded()
        else:
            self._build_request_collapsed()
        self._bind_click_tree(self.request_root, "request", self.request_root)
        self._render_request()

    def _build_request_collapsed(self) -> None:
        frame = tk.Frame(self.request_root, bg=REQUEST_BG, padx=8, pady=4)
        frame.pack(fill="both", expand=True)
        self._move_handle(frame, "request", self.request_root).pack(side="left", padx=(0, 4))
        self._resize_handle(frame, "request", self.request_root).pack(
            side="right",
            padx=(6, 0),
        )
        self.request_label = AutoScrollLabel(
            frame,
            text="↑- ↻- ↓- ◇- ∑- $0.0000",
            bg=REQUEST_BG,
            fg=HUD_ACCENT,
            font=("Consolas", 9, "bold"),
            animate_numbers=True,
            static_align="left",
        )
        self.request_label.pack(side="left", fill="both", expand=True)

    def _build_request_expanded(self) -> None:
        frame = tk.Frame(self.request_root, bg=REQUEST_BG, padx=8, pady=5)
        frame.pack(fill="both", expand=True)
        header = tk.Frame(frame, bg=REQUEST_HEADER_BG, padx=5, pady=2)
        header.pack(fill="x", pady=(0, 4))
        self._move_handle(header, "request", self.request_root).pack(side="left", padx=(0, 4))
        self._resize_handle(header, "request", self.request_root).pack(side="right", padx=(6, 0))
        self.request_label = AutoScrollLabel(
            header,
            text="最近模型请求轮次",
            bg=REQUEST_HEADER_BG,
            fg=HUD_ACCENT,
            font=("Consolas", 9, "bold"),
            animate_numbers=True,
            static_align="left",
        )
        self.request_label.pack(side="left", fill="x", expand=True)

        list_header = tk.Frame(frame, bg=REQUEST_BG)
        list_header.pack(fill="x", pady=(0, 2))
        tk.Label(
            list_header,
            text="轮次流水",
            anchor="w",
            bg=REQUEST_BG,
            fg=REQUEST_MUTED,
            font=("Microsoft YaHei UI", 7, "bold"),
        ).pack(side="left")
        tk.Label(
            list_header,
            text="最新在上",
            anchor="e",
            bg=REQUEST_BG,
            fg="#566477",
            font=("Microsoft YaHei UI", 7),
        ).pack(side="right")

        body = tk.Frame(frame, bg=REQUEST_PANEL_BG, padx=0, pady=0)
        body.pack(fill="x", expand=False)
        scrollbar = tk.Scrollbar(
            body,
            orient="vertical",
            bg=REQUEST_HEADER_BG,
            troughcolor=REQUEST_PANEL_BG,
            activebackground=HUD_DIVIDER,
            width=9,
            relief="flat",
            borderwidth=0,
            elementborderwidth=0,
            highlightthickness=0,
        )
        self.request_text = tk.Text(
            body,
            bg=REQUEST_PANEL_BG,
            fg=REQUEST_TEXT,
            insertbackground=REQUEST_TEXT,
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
        self.request_text.tag_configure("normal", foreground=REQUEST_TEXT)
        self.request_text.tag_configure("muted", foreground=REQUEST_MUTED)
        scrollbar.configure(command=self.request_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.request_text.pack(side="left", fill="x", expand=True)
        self.request_text.configure(state="disabled")

    def _bind_click_tree(self, widget: tk.Misc, target: str, window: tk.Tk | tk.Toplevel) -> None:
        if getattr(widget, "_hud_handle", False):
            return
        if isinstance(widget, (tk.Button, tk.Text, tk.Scrollbar)):
            return
        widget.bind("<ButtonPress-1>", lambda event, t=target, w=window: self._start_pointer(event, t, w), add="+")
        widget.bind("<B1-Motion>", self._drag_if_free, add="+")
        widget.bind("<ButtonRelease-1>", self._release_pointer, add="+")
        for child in widget.winfo_children():
            self._bind_click_tree(child, target, window)

    def _start_pointer(self, event: Any, target: str, window: tk.Tk | tk.Toplevel) -> None:
        self._press_at = (event.x_root, event.y_root)
        self._press_target = target
        del window

    def _drag_if_free(self, event: Any) -> None:
        del event

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
            self._remember_window_position(target, window, reason="move")
            self._save_settings()
        return "break"

    def _start_resize(self, event: Any, target: str, window: tk.Tk | tk.Toplevel) -> str:
        self._resize_target = target
        self._resize_window = window
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
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
            "resize_start target=%s x=%s y=%s width=%s height=%s",
            target,
            window.winfo_x(),
            window.winfo_y(),
            window.winfo_width(),
            window.winfo_height(),
        )
        return "break"

    def _resize_window_size(self, event: Any) -> str:
        if self._resize_window is None:
            return "break"
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
        width = max(min_width, self._resize_start_width + dx)
        height = self._resize_window.winfo_height()
        if expanded:
            height = max(min_height, self._resize_start_height + dy)
        x = self._resize_window.winfo_x()
        y = self._resize_window.winfo_y()
        self._resize_window.geometry(f"{width}x{height}+{x}+{y}")
        return "break"

    def _finish_resize(self, event: Any) -> str:
        del event
        target = self._resize_target
        window = self._resize_window
        self._resize_target = ""
        self._resize_window = None
        if target and window is not None:
            self._remember_window_position(target, window, reason="resize")
            self._remember_window_width(target, window, reason="resize")
            self._remember_window_height(target, window, reason="resize")
            self._save_settings()
            self._apply_geometry()
        return "break"

    def toggle_top_expanded(self) -> None:
        self.top_expanded = not self.top_expanded
        self._rebuild_top_ui()
        self._apply_geometry()

    def toggle_request_expanded(self) -> None:
        self.request_expanded = not self.request_expanded
        self._rebuild_request_ui()
        self._apply_geometry()

    def _follow_codex_window(self) -> None:
        self.sync_codex_window()
        self.root.after(self._next_follow_delay(), self._follow_codex_window)

    def sync_codex_window(self) -> None:
        """Synchronize HUD visibility and geometry with the current Codex window."""
        if self._move_target or self._resize_target:
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
        self._exit_tombstone("active")
        self._set_alpha(self.root, 0.94 if active else 0.55)
        self._set_alpha(self.request_root, (0.94 if self.request_expanded else 0.74) if active else 0.45)
        self._apply_geometry()

    def _enter_free_mode(self) -> None:
        self._exit_tombstone("free-mode")
        self._attached = False
        self._last_rect = None
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
            self.root.lift()
            self.request_root.lift()
            self.root.attributes("-topmost", True)
            self.request_root.attributes("-topmost", True)
        except tk.TclError:
            return False
        try:
            if self.root.state() == "withdrawn" or self.request_root.state() == "withdrawn":
                _HUD_GEOMETRY_LOGGER.info("hud_visibility_recovered reason=%s", reason)
        except tk.TclError:
            return False
        return True

    def _next_follow_delay(self) -> int:
        return self.tombstone_follow_ms if self._tombstoned else self.follow_ms

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
            self.root.geometry(f"{top[2]}x{top[3]}+{top[0]}+{top[1]}")
            self.request_root.geometry(f"{request[2]}x{request[3]}+{request[0]}+{request[1]}")
            return
        self._apply_free_defaults(keep_existing=True)

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

        if has_width_ratio:
            width = int(round(width_base * placement.width_ratio))
        else:
            width = placement.width or anchor.default_width
        x = anchor.default_x
        y = anchor.default_y

        if has_anchor_position:
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
                x = rect.left + int(round(rect.width * placement.relative_x_ratio))
                y = rect.top + int(round(rect.height * placement.relative_y_ratio))
        elif (
            placement.relative_x_ratio is not None
            and placement.relative_bottom_ratio is not None
        ):
            x = rect.left + int(round(rect.width * placement.relative_x_ratio))
            bottom = int(round(rect.height * placement.relative_bottom_ratio))
            y = rect.bottom - bottom - height

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
        if self._use_native_anchors:
            native = self.locator.anchor_geometry(target, rect, height)
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
        self.root.geometry(f"{top_width}x{top_height}+{tx}+{ty}")
        self.request_root.geometry(f"{request_width}x{request_height}+{rx}+{ry}")

    def _top_size(self) -> tuple[int, int]:
        expanded = self.top_expanded
        width = max(
            self._interactive_min_width("top", expanded),
            self.settings.top.width or (480 if not self.compact else 360),
        )
        return (
            width,
            self._window_height("top", expanded, self.settings.top),
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
        for window in [self.root, self.request_root]:
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

    def close(self, reason: str = "user") -> None:
        """Destroy both HUD windows and cancel Tk timers owned by the widgets."""
        if not self._exit_reason:
            self._exit_reason = reason
        try:
            self.request_root.destroy()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def update_display(self, parsed_session: ParsedSession) -> None:
        """Refresh both HUD windows with the latest parsed session snapshot."""
        self._snapshot = parsed_session
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
        title_label = self.top_labels.get("title")
        if title_label is not None:
            title_label.configure(text=_top_expanded_header_title(snapshot))
        bar = self.top_labels.get("bar")
        if bar is not None:
            text = (
                f"会话 {_format_usage_money(confirmed.cumulative_total, session_cost)} | "
                f"命中 {_session_cache_hit_rate_label(snapshot)} | "
                f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
                f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
                f"状态 {_budget_status(snapshot)}"
            )
            if snapshot.error and snapshot.status in {"missing", "error"}:
                text = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
            bar.configure(text=text, fg="#FFB86B" if snapshot.budget_warnings else HUD_TEXT)
            return

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
            "budget": self._format_budget(snapshot),
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
                        snapshot.error or snapshot.budget_error or snapshot.budget_warnings
                    )
                    label.configure(fg="#FFB86B" if has_warning else HUD_MUTED)

    def _render_request(self) -> None:
        snapshot = self._snapshot
        if self.request_label is not None:
            if self.request_expanded:
                self.request_label.configure(text=_request_total_line(snapshot))
            else:
                text = _request_total_line(snapshot)
                if snapshot.request.error:
                    text = f"本次 Token 出错 | {snapshot.request.error}"
                self.request_label.configure(text=text)

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

    def _format_budget(self, snapshot: ParsedSession) -> str:
        day_ratio = (
            snapshot.today_cost_usd / snapshot.daily_limit_usd
            if snapshot.daily_limit_usd > 0
            else 0.0
        )
        week_ratio = (
            snapshot.week_cost_usd / snapshot.weekly_limit_usd
            if snapshot.weekly_limit_usd > 0
            else 0.0
        )
        text = (
            f"今日累计  {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}  "
            f"额度 {snapshot.today_cost_usd:.2f}/{snapshot.daily_limit_usd:.0f} USD "
            f"({day_ratio:.0%})  起点 {_format_start(snapshot.day_start)}\n"
            f"本周累计  {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}  "
            f"额度 {snapshot.week_cost_usd:.2f}/{snapshot.weekly_limit_usd:.0f} USD "
            f"({week_ratio:.0%})  起点 {_format_start(snapshot.week_start)}"
        )
        if snapshot.week_before_today_cost_usd > 0:
            text += (
                "\n本周拆分  "
                f"今日前 {_format_usage_money(snapshot.week_before_today_tokens, snapshot.week_before_today_cost_usd)}"
                f" + 当前日窗 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}"
            )
        return text

    def _format_warnings(self, snapshot: ParsedSession) -> str:
        if snapshot.budget_error:
            return snapshot.budget_error
        if snapshot.budget_warnings:
            return "提醒  " + "；".join(snapshot.budget_warnings)
        return "提醒  暂无额度提醒"

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
