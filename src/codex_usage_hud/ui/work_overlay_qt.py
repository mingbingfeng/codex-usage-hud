"""PySide6 desktop overlay used by the standalone work-bubble helper."""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import normalize_work_overlay_max_items
from ..core import parse_timestamp

try:  # pragma: no cover - optional desktop overlay runtime dependency
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QFont as _QFont
    from PySide6.QtGui import QFontMetrics as _QFontMetrics
    from PySide6.QtGui import QTextLayout as _QTextLayout
    from PySide6.QtGui import QTextOption as _QTextOption
except Exception:  # pragma: no cover - depends on local runtime
    _Qt = None
    _QFont = None
    _QFontMetrics = None
    _QTextLayout = None
    _QTextOption = None

WORK_OVERLAY_POINTER_SYNC_MS = 60
WORK_OVERLAY_WIDTH = 430
WORK_OVERLAY_MARGIN = 16
WORK_OVERLAY_TOP_OFFSET = 56
WORK_OVERLAY_CLOSE_SIZE = 22
WORK_OVERLAY_TEXT_WRAP_WIDTH = WORK_OVERLAY_WIDTH - 28
WORK_OVERLAY_BODY_MAX_LINES = 3
WORK_OVERLAY_CARD_X_PADDING = 10
WORK_OVERLAY_CARD_Y_PADDING = 8
WORK_OVERLAY_CARD_SPACING = 7
WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT = 160
WORK_OVERLAY_COMPLETED_BADGE_SIZE = 168
WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT = 180
WORK_OVERLAY_COMPLETED_BADGE_SPACING = 8
WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS = 520
WORK_OVERLAY_STACK_SPACING = 8
WORK_OVERLAY_TRANSITION_CARD_HEIGHT = 110
WORK_OVERLAY_TRANSITION_SHRINK_MS = 220
WORK_OVERLAY_TRANSITION_PAUSE_MS = 140
WORK_OVERLAY_TRANSITION_MOVE_MS = 280
WORK_OVERLAY_TRANSITION_SHIFT_MS = 240
WORK_OVERLAY_RESTORE_FADE_OUT_MS = 200
WORK_OVERLAY_RESTORE_SHIFT_MS = 300
WORK_OVERLAY_RESTORE_FADE_IN_MS = 150
WORK_OVERLAY_RESTORE_DESCEND_MS = 400
WORK_OVERLAY_COMPLETED_ANNIHILATION_MS = 1200
WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN = 16
WORK_OVERLAY_TRANSITION_CLEARANCE_PX = (
    WORK_OVERLAY_COMPLETED_BADGE_SIZE + WORK_OVERLAY_COMPLETED_BADGE_SPACING
)
WORK_OVERLAY_SHIMMER_TIMER_MS = 30
WORK_OVERLAY_SHIMMER_STEP_PX = 3.5
WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX = 58
WORK_OVERLAY_SHIMMER_HIGHLIGHT = "#FFFFFF"
WORK_OVERLAY_SHIMMER_PEAK_ALPHA = 245
WORK_OVERLAY_SWITCH_PENDING_SLOW_SECONDS = 3.0
WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS = 45.0
WORK_OVERLAY_SWITCH_PENDING_TIMER_MS = 120
WORK_OVERLAY_SWITCH_PENDING_MIN_WIDTH = 150
WORK_OVERLAY_COMPLETED_PENDING_LAUNCH_SECONDS = 0.45
WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS = 0.85
WORK_OVERLAY_EMPTY_GRACE_SECONDS = 0.8
WORK_OVERLAY_STATE_READ_FAILURE_GRACE_SECONDS = 1.2
DEFAULT_WORK_OVERLAY_THEME: dict[str, str] = {
    "surface": "#10161D",
    "panelSurface": "#141B24",
    "panelBorder": "#263241",
    "text": "#DCE7F2",
    "muted": "#8492A6",
    "accent": "#F3D27A",
    "info": "#9CCBFF",
    "warning": "#FFB86B",
    "error": "#FF6B6B",
    "success": "#8FE3A1",
    "requestPanelSurface": "#10161D",
    "requestText": "#B8C6D8",
    "requestMuted": "#5E6A78",
    "progressOverflowBadge": "#7F3E3A",
    "progressOverflowBadgeEdge": "#FF875A",
}


def work_overlay_max_items_for_screen_height(screen_height: int) -> int:
    available_height = max(
        1,
        int(screen_height) - WORK_OVERLAY_TOP_OFFSET - (WORK_OVERLAY_MARGIN * 2),
    )
    return max(1, available_height // WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT)


def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _compact_workdir_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return "..." + text[-max(0, limit - 3) :]


def _workdir_parts(value: object) -> list[str]:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return []
    return [part for part in text.replace("/", "\\").split("\\") if part]


def _workdir_leaf(value: object) -> str:
    parts = _workdir_parts(value)
    return parts[-1] if parts else ""


def _workdir_display_name(item: Mapping[str, object]) -> str:
    return _workdir_leaf(item.get("workdir")) or _workdir_leaf(item.get("workdirName"))


def _multiline_elided_text(
    value: object,
    *,
    font: object,
    width: int,
    max_lines: int = WORK_OVERLAY_BODY_MAX_LINES,
) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if (
        _QTextLayout is None
        or _QTextOption is None
        or _QFontMetrics is None
        or _Qt is None
        or not isinstance(font, _QFont)
        or width <= 0
        or max_lines <= 0
    ):
        return text

    option = _QTextOption()
    option.setWrapMode(_QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout = _QTextLayout(text, font)
    layout.setTextOption(option)
    visible_lines: list[tuple[int, int]] = []
    layout.beginLayout()
    try:
        while len(visible_lines) < max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, int(width)))
            visible_lines.append((line.textStart(), line.textLength()))
    finally:
        layout.endLayout()

    if not visible_lines:
        return text

    consumed = sum(length for _start, length in visible_lines)
    if consumed >= len(text):
        return text

    metrics = _QFontMetrics(font)
    lines: list[str] = []
    for index, (start, length) in enumerate(visible_lines):
        if index < len(visible_lines) - 1:
            lines.append(text[start : start + length].rstrip())
            continue
        remaining = text[start:].lstrip()
        lines.append(metrics.elidedText(remaining, _Qt.TextElideMode.ElideRight, max(1, int(width))))
    return "\n".join(line for line in lines if line)


def _item_is_completed(item: Mapping[str, object]) -> bool:
    return str(item.get("status") or "") == "recent"


OverlayRect = tuple[float, float, float, float]


def _item_id(item: Mapping[str, object]) -> str:
    return str(item.get("id") or "").strip()


def _switch_item_key(item: Mapping[str, object]) -> str:
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    title = str(item.get("targetTitle") or item.get("title") or "").strip()
    workdir = str(item.get("workdir") or "").strip()
    if not session_id and not title and not workdir:
        return ""
    return json.dumps(
        {
            "sessionId": session_id,
            "title": title,
            "workdir": workdir,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _pending_workdir_window_rect(
    anchor_x: int,
    anchor_y: int,
    anchor_width: int,
    anchor_height: int,
    *,
    pending: bool,
    screen_left: int = 0,
) -> tuple[int, int, int, int]:
    width = max(1, int(anchor_width))
    height = max(1, int(anchor_height))
    x = int(anchor_x)
    y = int(anchor_y)
    if not pending:
        return x, y, width, height
    right = x + width
    width = max(width, WORK_OVERLAY_SWITCH_PENDING_MIN_WIDTH)
    x = max(int(screen_left), right - width)
    return x, y, width, height


def _item_kind(item: Mapping[str, object]) -> str:
    return "completed" if _item_is_completed(item) else "card"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _ease_out_cubic(value: float) -> float:
    value = _clamp01(value)
    return 1.0 - pow(1.0 - value, 3)


def _ease_in_out_cubic(value: float) -> float:
    value = _clamp01(value)
    if value < 0.5:
        return 4.0 * value * value * value
    return 1.0 - pow(-2.0 * value + 2.0, 3) / 2.0


def _transition_total_ms() -> int:
    return (
        WORK_OVERLAY_TRANSITION_SHRINK_MS
        + WORK_OVERLAY_TRANSITION_PAUSE_MS
        + WORK_OVERLAY_TRANSITION_MOVE_MS
    )


def _detect_transition(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
) -> str | None:
    old_by_id = {_item_id(item): item for item in old_items if _item_id(item)}
    for item in new_items:
        item_id = _item_id(item)
        if not item_id or item_id not in old_by_id:
            continue
        old_kind = _item_kind(old_by_id[item_id])
        new_kind = _item_kind(item)
        if old_kind == "card" and new_kind == "completed":
            return "card_to_completed"
        if old_kind == "completed" and new_kind == "card":
            return "completed_to_card"
    return None


def _detect_transition_item_id(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
) -> str:
    old_by_id = {_item_id(item): item for item in old_items if _item_id(item)}
    for item in new_items:
        item_id = _item_id(item)
        if item_id and item_id in old_by_id and _item_kind(old_by_id[item_id]) != _item_kind(item):
            return item_id
    return ""


def _completed_badge_slot_rects(
    items: Sequence[Mapping[str, object]],
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> dict[str, OverlayRect]:
    completed_items = [item for item in items if _item_is_completed(item)]
    rects: dict[str, OverlayRect] = {}
    completed_count = len(completed_items)
    for index, item in enumerate(completed_items):
        item_id = _item_id(item)
        if not item_id:
            continue
        rects[item_id] = _completed_slot_rect(
            completed_count - 1 - index,
            completed_count,
            layout_width=layout_width,
        )
    return rects


def _completed_slot_rect(
    index_from_right: int,
    completed_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> OverlayRect:
    completed_count = max(0, int(completed_count))
    if completed_count <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    index_from_right = max(0, min(completed_count - 1, int(index_from_right)))
    index_from_left = completed_count - 1 - index_from_right
    row_width = _completed_badge_row_width(completed_count)
    start_x = max(0, int(layout_width) - row_width)
    return (
        float(
            start_x
            + index_from_left
            * (WORK_OVERLAY_COMPLETED_BADGE_SIZE + WORK_OVERLAY_COMPLETED_BADGE_SPACING)
        ),
        0.0,
        float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
        float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
    )


def _card_slot_rect(
    index_from_top: int,
    completed_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> OverlayRect:
    row_top = 0
    if int(completed_count) > 0:
        row_top += WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT + WORK_OVERLAY_STACK_SPACING
    row_top += max(0, int(index_from_top)) * (
        WORK_OVERLAY_TRANSITION_CARD_HEIGHT + WORK_OVERLAY_STACK_SPACING
    )
    x = max(0, int(layout_width) - WORK_OVERLAY_WIDTH)
    return (
        float(x),
        float(row_top),
        float(WORK_OVERLAY_WIDTH),
        float(WORK_OVERLAY_TRANSITION_CARD_HEIGHT),
    )


def _overlay_required_height_for_counts(
    completed_count: int,
    card_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> int:
    del layout_width
    bottoms: list[float] = [1.0]
    completed_count = max(0, int(completed_count))
    card_count = max(0, int(card_count))
    if completed_count > 0:
        bottoms.append(float(WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT))
    if card_count > 0:
        last_card = _card_slot_rect(card_count - 1, completed_count)
        bottoms.append(last_card[1] + last_card[3])
    return max(1, int(math.ceil(max(bottoms))))


def _overlay_window_top_y(screen_top: int) -> int:
    return int(screen_top) + WORK_OVERLAY_TOP_OFFSET


def _find_item_rect(
    items: Sequence[Mapping[str, object]],
    item_id: str,
    kind: str,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> OverlayRect:
    if not item_id:
        return (0.0, 0.0, 0.0, 0.0)
    completed_items = [item for item in items if _item_is_completed(item)]
    active_items = [item for item in items if not _item_is_completed(item)]
    if kind == "completed":
        return _completed_badge_slot_rects(items, layout_width=layout_width).get(
            item_id,
            (0.0, 0.0, 0.0, 0.0),
        )

    active_index = next(
        (
            idx
            for idx, item in enumerate(active_items)
            if _item_id(item) == item_id
        ),
        -1,
    )
    if active_index < 0:
        return (0.0, 0.0, 0.0, 0.0)
    return _card_slot_rect(
        active_index,
        len(completed_items),
        layout_width=layout_width,
    )


def _find_item_position(
    items: Sequence[Mapping[str, object]],
    item_id: str,
    kind: str,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> tuple[int, int]:
    rect = _find_item_rect(items, item_id, kind, layout_width=layout_width)
    return int(rect[0]), int(rect[1])


def _remembered_card_rect_for_layout(
    rect: OverlayRect,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> OverlayRect:
    return (
        float(max(0, int(layout_width) - int(rect[2]))),
        rect[1],
        rect[2],
        rect[3],
    )


def _rect_center(rect: OverlayRect) -> tuple[float, float]:
    return rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0


def _rect_from_center(center_x: float, center_y: float, width: float, height: float) -> OverlayRect:
    return (
        center_x - width / 2.0,
        center_y - height / 2.0,
        width,
        height,
    )


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _lerp_rect(start: OverlayRect, end: OverlayRect, progress: float) -> OverlayRect:
    progress = _clamp01(progress)
    return (
        _lerp(start[0], end[0], progress),
        _lerp(start[1], end[1], progress),
        _lerp(start[2], end[2], progress),
        _lerp(start[3], end[3], progress),
    )


def _circle_rect_at_rect_center(rect: OverlayRect) -> OverlayRect:
    center_x, center_y = _rect_center(rect)
    size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
    return _rect_from_center(center_x, center_y, size, size)


def _right_edge_circle_rect_for_rect(rect: OverlayRect) -> OverlayRect:
    size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
    center_y = rect[1] + rect[3] / 2.0
    top = max(0.0, center_y - size / 2.0)
    return (
        rect[0] + rect[2] - size,
        top,
        size,
        size,
    )


def _card_height_circle_rect_for_rect(rect: OverlayRect) -> OverlayRect:
    diameter = max(1.0, float(rect[3]))
    return (
        float(rect[0]) + float(rect[2]) - diameter,
        float(rect[1]),
        diameter,
        diameter,
    )


def _card_yield_rect_for_circle_path(
    card_rect: OverlayRect,
    circle_rect: OverlayRect,
) -> OverlayRect:
    target_right = float(circle_rect[0]) - WORK_OVERLAY_COMPLETED_BADGE_SPACING
    current_right = float(card_rect[0]) + float(card_rect[2])
    offset_x = min(0.0, target_right - current_right)
    return (
        float(card_rect[0]) + offset_x,
        float(card_rect[1]),
        float(card_rect[2]),
        float(card_rect[3]),
    )


def _card_yield_delay_ms(
    card_rect: OverlayRect,
    source_circle_rect: OverlayRect,
    target_circle_rect: OverlayRect,
    duration_ms: int,
) -> int:
    source_y = float(source_circle_rect[1])
    target_y = float(target_circle_rect[1])
    source_center_y = source_y + float(source_circle_rect[3]) / 2.0
    target_center_y = target_y + float(target_circle_rect[3]) / 2.0
    travel = abs(source_center_y - target_center_y)
    if travel <= 1.0:
        return 0
    card_center_y = float(card_rect[1]) + float(card_rect[3]) / 2.0
    progress = abs(source_center_y - card_center_y) / travel
    progress = max(0.0, min(0.82, progress))
    return max(0, int(round(max(1, duration_ms) * progress)))


def _energy_ring_rect_for_completed_rect(rect: OverlayRect) -> OverlayRect:
    circle_size = max(
        1.0,
        min(
            float(rect[2]),
            float(rect[3]),
            float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
        ),
    )
    max_radius = circle_size / 2.0 + float(WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN)
    half_size = max_radius + 10.0
    center_x = float(rect[0]) + circle_size / 2.0
    center_y = float(rect[1]) + circle_size / 2.0
    return (
        center_x - half_size,
        center_y - half_size,
        half_size * 2.0,
        half_size * 2.0,
    )


def _completed_pending_particle_state(
    elapsed_seconds: float,
    index: int,
    count: int,
) -> tuple[float, float, float]:
    elapsed = max(0.0, float(elapsed_seconds))
    count = max(1, int(count))
    index = max(0, int(index))
    base = (math.tau / float(count)) * float(index)
    speed = 4.8 + 0.58 * math.sin(elapsed * 3.4 + index * 1.37)
    angle = (
        base
        + elapsed * speed
        + 0.18 * math.sin(elapsed * 11.5 + index * 2.1)
        + 0.05 * math.sin(elapsed * 24.0 + index * 0.73)
    ) % math.tau
    radial_jitter = (
        0.62 * math.sin(elapsed * 15.0 + index * 1.91)
        + 0.34 * math.sin(elapsed * 27.0 + index * 0.43)
    )
    link_pulse = _clamp01((math.sin(elapsed * 9.0 + index * 2.2) - 0.35) / 0.65)
    return angle, radial_jitter, link_pulse


def _completed_pending_launch_progress(elapsed_seconds: float) -> float:
    return _clamp01(
        float(elapsed_seconds) / max(0.001, WORK_OVERLAY_COMPLETED_PENDING_LAUNCH_SECONDS)
    )


def _completed_pending_launch_scale(elapsed_seconds: float) -> float:
    progress = _completed_pending_launch_progress(elapsed_seconds)
    if progress >= 1.0:
        return 1.0
    if progress < 0.52:
        return 1.0 - 0.032 * math.sin(math.pi * progress / 0.52)
    return 1.0 + 0.012 * math.sin(math.pi * (progress - 0.52) / 0.48)


def _completed_pending_finish_progress(elapsed_seconds: float) -> float:
    return _clamp01(
        float(elapsed_seconds) / max(0.001, WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS)
    )


def _completed_pending_caption_opacity(
    pending_elapsed_seconds: float,
    *,
    completed: bool,
    finish_elapsed_seconds: float = 0.0,
) -> float:
    if completed:
        progress = _completed_pending_finish_progress(finish_elapsed_seconds)
        if progress <= 0.35:
            return 1.0
        return 1.0 - _ease_out_cubic((progress - 0.35) / 0.65)
    return _clamp01(float(pending_elapsed_seconds) / 0.16)


def _workdir_link_pending_for_item(
    item: Mapping[str, object],
    pending: bool,
) -> bool:
    del item, pending
    return False


def _item_is_cli(item: Mapping[str, object]) -> bool:
    return str(item.get("clientKind") or "").strip().lower() == "cli"


def _workdir_clickable_for_item(item: Mapping[str, object]) -> bool:
    if _item_is_cli(item):
        return False
    workdir = str(item.get("workdir") or "").strip()
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
    return bool(workdir and (session_id or target_title))


def _workdir_link_hover_visible_for_item(item: Mapping[str, object]) -> bool:
    return not _item_is_cli(item) and not _item_is_completed(item)


def _transition_required_height(
    transition_type: str,
    source_rect: OverlayRect,
    target_rect: OverlayRect,
) -> int:
    rects = [
        source_rect,
        target_rect,
        _transition_rect_for_progress(transition_type, source_rect, target_rect, 0.0),
        _transition_rect_for_progress(transition_type, source_rect, target_rect, 0.35),
        _transition_rect_for_progress(transition_type, source_rect, target_rect, 0.75),
        _transition_rect_for_progress(transition_type, source_rect, target_rect, 1.0),
    ]
    return max(1, int(math.ceil(max(rect[1] + rect[3] for rect in rects))))


def _transition_rect_for_progress(
    transition_type: str,
    source_rect: OverlayRect,
    target_rect: OverlayRect,
    progress: float,
) -> OverlayRect:
    progress = _clamp01(progress)
    total_ms = float(_transition_total_ms())
    shrink_end = WORK_OVERLAY_TRANSITION_SHRINK_MS / total_ms
    pause_end = (WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms

    if transition_type == "card_to_completed":
        source_circle = _right_edge_circle_rect_for_rect(source_rect)
        if progress <= shrink_end:
            return _lerp_rect(source_rect, source_circle, _ease_out_cubic(progress / shrink_end))
        if progress <= pause_end:
            return source_circle
        move_progress = (progress - pause_end) / max(0.001, 1.0 - pause_end)
        return _lerp_rect(source_circle, target_rect, _ease_in_out_cubic(move_progress))

    if transition_type == "completed_to_card":
        target_circle = _right_edge_circle_rect_for_rect(target_rect)
        move_end = WORK_OVERLAY_TRANSITION_MOVE_MS / total_ms
        pause_end = (WORK_OVERLAY_TRANSITION_MOVE_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms
        if progress <= move_end:
            return _lerp_rect(source_rect, target_circle, _ease_in_out_cubic(progress / move_end))
        if progress <= pause_end:
            return target_circle
        expand_progress = (progress - pause_end) / max(0.001, 1.0 - pause_end)
        return _lerp_rect(target_circle, target_rect, _ease_out_cubic(expand_progress))

    return source_rect


def _transition_slot_shift_progress(transition_type: str, progress: float) -> float:
    progress = _clamp01(progress)
    total_ms = float(_transition_total_ms())
    shrink_end = WORK_OVERLAY_TRANSITION_SHRINK_MS / total_ms
    pause_end = (WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms

    if transition_type == "card_to_completed":
        if progress <= shrink_end:
            return 0.0
        if progress <= pause_end:
            return _ease_out_cubic(
                (progress - shrink_end) / max(0.001, pause_end - shrink_end)
            )
        return 1.0

    if transition_type == "completed_to_card":
        move_end = WORK_OVERLAY_TRANSITION_MOVE_MS / total_ms
        if progress <= move_end:
            return _ease_out_cubic(progress / max(0.001, move_end))
        return 1.0

    return 1.0


def _transition_clearance_offset(
    transition_type: str,
    progress: float,
    *,
    distance: float = float(WORK_OVERLAY_TRANSITION_CLEARANCE_PX),
) -> float:
    progress = _clamp01(progress)
    total_ms = float(_transition_total_ms())

    if transition_type == "card_to_completed":
        shrink_end = WORK_OVERLAY_TRANSITION_SHRINK_MS / total_ms
        pause_end = (WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms
        if progress <= shrink_end:
            return 0.0
        if progress <= pause_end:
            shift = _ease_out_cubic(
                (progress - shrink_end) / max(0.001, pause_end - shrink_end)
            )
            return -distance * shift
        return_start = pause_end + (1.0 - pause_end) * 0.72
        if progress <= return_start:
            return -distance
        rebound = _ease_out_cubic((progress - return_start) / max(0.001, 1.0 - return_start))
        return -distance * (1.0 - rebound)

    if transition_type == "completed_to_card":
        move_end = WORK_OVERLAY_TRANSITION_MOVE_MS / total_ms
        pause_end = (WORK_OVERLAY_TRANSITION_MOVE_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms
        lead_in_end = min(move_end, 0.18)
        if progress <= lead_in_end:
            lead_in = _ease_out_cubic(progress / max(0.001, lead_in_end))
            return -distance * lead_in
        if progress <= move_end:
            return -distance
        if progress <= pause_end:
            rebound = _ease_out_cubic((progress - move_end) / max(0.001, pause_end - move_end))
            return -distance * (1.0 - rebound)
        return 0.0

    return 0.0


def _completed_badge_slot_moves(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> dict[str, tuple[OverlayRect, OverlayRect]]:
    old_rects = _completed_badge_slot_rects(old_items, layout_width=layout_width)
    new_rects = _completed_badge_slot_rects(new_items, layout_width=layout_width)
    return {
        item_id: (old_rects[item_id], new_rects[item_id])
        for item_id in old_rects.keys() & new_rects.keys()
        if old_rects[item_id] != new_rects[item_id]
    }


def _completed_restore_staged_items(
    old_items: Sequence[Mapping[str, object]],
    item_id: str,
) -> list[Mapping[str, object]]:
    clicked: Mapping[str, object] | None = None
    remaining_completed: list[Mapping[str, object]] = []
    other_items: list[Mapping[str, object]] = []
    for item in old_items:
        if not _item_is_completed(item):
            other_items.append(item)
            continue
        if _item_id(item) == item_id:
            clicked = item
        else:
            remaining_completed.append(item)
    staged = list(remaining_completed)
    if clicked is not None:
        staged.append(clicked)
    staged.extend(other_items)
    return staged


def _completed_badge_restore_slot_moves(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
    item_id: str,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> dict[str, tuple[OverlayRect, OverlayRect, OverlayRect]]:
    old_rects = _completed_badge_slot_rects(old_items, layout_width=layout_width)
    staged_rects = _completed_badge_slot_rects(
        _completed_restore_staged_items(old_items, item_id),
        layout_width=layout_width,
    )
    new_rects = _completed_badge_slot_rects(new_items, layout_width=layout_width)
    return {
        completed_id: (
            old_rects[completed_id],
            staged_rects[completed_id],
            new_rects[completed_id],
        )
        for completed_id in old_rects.keys() & staged_rects.keys() & new_rects.keys()
        if (
            old_rects[completed_id] != staged_rects[completed_id]
            or staged_rects[completed_id] != new_rects[completed_id]
        )
    }


def _overlay_item_timestamp_seconds(
    item: Mapping[str, object],
    *keys: str,
) -> float:
    for key in keys:
        value = item.get(key)
        if isinstance(value, datetime):
            return value.timestamp()
        text = str(value or "").strip()
        if not text:
            continue
        parsed = parse_timestamp(text)
        if parsed is not None:
            return parsed.timestamp()
    return 0.0


def _ordered_overlay_items(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    completed: list[Mapping[str, object]] = []
    active: list[Mapping[str, object]] = []
    for item in items:
        if _item_is_completed(item):
            completed.append(item)
        else:
            active.append(item)
    completed.sort(
        key=lambda item: _overlay_item_timestamp_seconds(
            item,
            "updatedAt",
            "taskStartedAt",
            "startedAt",
        )
    )
    return completed + active


def _completed_badge_row_width(count: int) -> int:
    if count <= 0:
        return 0
    return (
        WORK_OVERLAY_COMPLETED_BADGE_SIZE * count
        + WORK_OVERLAY_COMPLETED_BADGE_SPACING * max(0, count - 1)
    )


def _transition_layout_width(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
) -> int:
    old_completed_count = sum(1 for item in old_items if _item_is_completed(item))
    new_completed_count = sum(1 for item in new_items if _item_is_completed(item))
    return max(
        WORK_OVERLAY_WIDTH,
        _completed_badge_row_width(old_completed_count),
        _completed_badge_row_width(new_completed_count),
    )


def _overlay_items_required_height(
    items: Sequence[Mapping[str, object]],
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
) -> int:
    completed_items = [item for item in items if _item_is_completed(item)]
    active_items = [item for item in items if not _item_is_completed(item)]
    bottoms: list[float] = [1.0]
    if completed_items:
        bottoms.append(float(WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT))
    for item in active_items:
        item_id = _item_id(item)
        if not item_id:
            continue
        rect = _find_item_rect(items, item_id, "card", layout_width=layout_width)
        bottoms.append(rect[1] + rect[3])
    return max(1, int(math.ceil(max(bottoms))))


def _point_in_rect(
    point_x: int,
    point_y: int,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
) -> bool:
    return left <= point_x < (left + width) and top <= point_y < (top + height)


def _point_in_inscribed_circle(
    point_x: int,
    point_y: int,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
) -> bool:
    if not _point_in_rect(point_x, point_y, left=left, top=top, width=width, height=height):
        return False
    radius = min(width, height) / 2.0
    center_x = left + (width / 2.0)
    center_y = top + (height / 2.0)
    dx = float(point_x) - center_x
    dy = float(point_y) - center_y
    return (dx * dx + dy * dy) <= (radius * radius)


def _overlay_hover_hit_test(
    point_x: int,
    point_y: int,
    *,
    rects: Sequence[tuple[int, int, int, int]] = (),
    circle_rects: Sequence[tuple[int, int, int, int]] = (),
) -> bool:
    for left, top, width, height in rects:
        if _point_in_rect(point_x, point_y, left=left, top=top, width=width, height=height):
            return True
    for left, top, width, height in circle_rects:
        if _point_in_inscribed_circle(
            point_x,
            point_y,
            left=left,
            top=top,
            width=width,
            height=height,
        ):
            return True
    return False


def _work_overlay_header_text(
    started_at: datetime | str | None,
    elapsed_text: str,
    title: str,
    *,
    title_limit: int = 28,
) -> str:
    timestamp = started_at
    if isinstance(started_at, str):
        timestamp = parse_timestamp(started_at)
    start_text = ""
    if isinstance(timestamp, datetime):
        start_text = (
            timestamp.astimezone().strftime("%H:%M:%S")
            if timestamp.tzinfo is not None
            else timestamp.strftime("%H:%M:%S")
        )
    title_text = _compact_work_text(title, title_limit)
    parts = [part for part in (start_text, elapsed_text.strip(), title_text) if part]
    return " | ".join(parts) if parts else "Codex 工作"


def _item_dismiss_key(item: Mapping[str, object]) -> str:
    status = str(item.get("status") or "")
    error_text = str(item.get("statusText") or item.get("detail") or "") if status == "error" else ""
    return json.dumps(
        {
            "id": item.get("id"),
            "errorText": error_text,
            "status": "error" if status == "error" else "work",
            "taskStartedAt": item.get("taskStartedAt") or item.get("startedAt") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _mark_item_dismissed(
    dismissed_instances: dict[str, str],
    item: Mapping[str, object],
) -> None:
    item_id = _item_id(item)
    if item_id:
        dismissed_instances[item_id] = _item_dismiss_key(item)


def _visible_overlay_items(
    items: Sequence[Mapping[str, object]],
    dismissed_instances: dict[str, str],
    *,
    item_limit: int,
) -> list[Mapping[str, object]]:
    visible: list[Mapping[str, object]] = []
    live_ids: set[str] = set()
    for item in items[:item_limit]:
        item_id = str(item.get("id") or "")
        if item_id:
            live_ids.add(item_id)
        dismiss_key = _item_dismiss_key(item)
        if item_id and dismissed_instances.get(item_id) == dismiss_key:
            continue
        if item_id and item_id in dismissed_instances:
            dismissed_instances.pop(item_id, None)
        visible.append(item)
    for item_id in list(dismissed_instances):
        if item_id not in live_ids:
            dismissed_instances.pop(item_id, None)
    return visible


def _transition_hides_source_before_effect_reset(transition_type: str) -> bool:
    return transition_type == "completed_dismiss"


def _theme_hex(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if text.startswith("#") and len(text) in {4, 7}:
        return text
    return fallback


def _theme_hex_to_rgb(value: object, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = _theme_hex(value, "")
    if len(text) == 4:
        text = f"#{text[1] * 2}{text[2] * 2}{text[3] * 2}"
    if len(text) != 7:
        return fallback
    try:
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    except ValueError:
        return fallback


def _theme_rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return (
        f"#{max(0, min(255, int(rgb[0]))):02x}"
        f"{max(0, min(255, int(rgb[1]))):02x}"
        f"{max(0, min(255, int(rgb[2]))):02x}"
    )


def _theme_mix(base: object, overlay: object, alpha: float, *, fallback: str) -> str:
    alpha = max(0.0, min(1.0, float(alpha)))
    base_rgb = _theme_hex_to_rgb(base, _theme_hex_to_rgb(fallback, (16, 22, 29)))
    overlay_rgb = _theme_hex_to_rgb(overlay, _theme_hex_to_rgb(fallback, (16, 22, 29)))
    channels = []
    for base_channel, overlay_channel in zip(base_rgb, overlay_rgb):
        channels.append(
            int(round((base_channel * (1.0 - alpha)) + (overlay_channel * alpha)))
        )
    return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"


def _theme_relative_luma(
    value: object,
    fallback: tuple[int, int, int] = (0, 0, 0),
) -> float:
    red, green, blue = _theme_hex_to_rgb(value, fallback)
    channels = []
    for channel in (red, green, blue):
        normalized = channel / 255.0
        if normalized <= 0.03928:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return (channels[0] * 0.2126) + (channels[1] * 0.7152) + (channels[2] * 0.0722)


def _theme_contrast_ratio(left: object, right: object) -> float:
    left_luma = _theme_relative_luma(left)
    right_luma = _theme_relative_luma(right)
    lighter = max(left_luma, right_luma)
    darker = min(left_luma, right_luma)
    return (lighter + 0.05) / (darker + 0.05)


def _theme_contrast_choice(
    background: object,
    primary: object,
    secondary: object,
    *,
    fallback: str,
) -> str:
    primary_hex = _theme_hex(primary, fallback)
    secondary_hex = _theme_hex(secondary, fallback)
    if not primary_hex:
        return secondary_hex or fallback
    if not secondary_hex:
        return primary_hex
    if _theme_contrast_ratio(background, primary_hex) >= _theme_contrast_ratio(
        background,
        secondary_hex,
    ):
        return primary_hex
    return secondary_hex


def _theme_emphasis_ink(
    background: object,
    *,
    fallback: str,
) -> str:
    return _theme_contrast_choice(
        background,
        "#ffffff",
        "#111111",
        fallback=fallback,
    )


def _theme_readable_color(
    color: object,
    background: object,
    *,
    fallback: str,
    min_ratio: float = 4.5,
) -> str:
    background_hex = _theme_hex(background, "#10161d")
    candidate = _theme_hex(color, fallback)
    if _theme_contrast_ratio(background_hex, candidate) >= min_ratio:
        return candidate

    fallback_hex = _theme_hex(fallback, candidate)
    candidate_rgb = _theme_hex_to_rgb(candidate, _theme_hex_to_rgb(fallback_hex, (255, 255, 255)))
    fallback_rgb = _theme_hex_to_rgb(fallback_hex, candidate_rgb)
    best = candidate
    best_ratio = _theme_contrast_ratio(background_hex, candidate)
    for ratio in (0.18, 0.32, 0.46, 0.60, 0.74, 0.88, 1.0):
        mixed = _theme_rgb_to_hex(
            (
                int(round(candidate_rgb[0] + ((fallback_rgb[0] - candidate_rgb[0]) * ratio))),
                int(round(candidate_rgb[1] + ((fallback_rgb[1] - candidate_rgb[1]) * ratio))),
                int(round(candidate_rgb[2] + ((fallback_rgb[2] - candidate_rgb[2]) * ratio))),
            )
        )
        mixed_ratio = _theme_contrast_ratio(background_hex, mixed)
        if mixed_ratio > best_ratio:
            best = mixed
            best_ratio = mixed_ratio
        if mixed_ratio >= min_ratio:
            return mixed
    high_contrast = _theme_emphasis_ink(background_hex, fallback=fallback_hex)
    high_ratio = _theme_contrast_ratio(background_hex, high_contrast)
    return high_contrast if high_ratio > best_ratio else best


def _resolved_overlay_theme(theme_tokens: Mapping[str, object] | None) -> dict[str, str]:
    resolved = dict(DEFAULT_WORK_OVERLAY_THEME)
    if theme_tokens is None:
        return resolved
    for key, fallback in DEFAULT_WORK_OVERLAY_THEME.items():
        resolved[key] = _theme_hex(theme_tokens.get(key), fallback)
    return resolved


def _overlay_payload_signature(
    items: Sequence[Mapping[str, object]],
    theme_tokens: Mapping[str, object] | None = None,
) -> str:
    return json.dumps(
        {
            "items": list(items),
            "theme": _resolved_overlay_theme(theme_tokens),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _color_for(
    status: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> tuple[str, str, str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    base_card = theme["requestPanelSurface"]
    base_border = theme["panelBorder"]
    if status == "error":
        accent = theme["error"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.16, fallback=base_card),
            _theme_mix(base_border, accent, 0.55, fallback=base_border),
        )
    if status == "waiting_user":
        accent = theme["warning"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.12, fallback=base_card),
            _theme_mix(base_border, accent, 0.45, fallback=base_border),
        )
    if status == "tool":
        accent = theme["info"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.12, fallback=base_card),
            _theme_mix(base_border, accent, 0.45, fallback=base_border),
        )
    if status == "recent":
        accent = theme["success"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.11, fallback=base_card),
            _theme_mix(base_card, accent, 0.16, fallback=base_card),
            _theme_mix(base_border, accent, 0.55, fallback=base_border),
        )
    accent = theme["accent"]
    return (
        accent,
        _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
        _theme_mix(base_card, accent, 0.12, fallback=base_card),
        _theme_mix(base_border, accent, 0.40, fallback=base_border),
    )


def _round_badge_palette(
    status: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    accent, _, _, _ = _color_for(status, theme)
    background = _theme_mix(accent, theme["surface"], 0.10, fallback=accent)
    border = _theme_mix(theme["panelBorder"], accent, 0.68, fallback=theme["panelBorder"])
    text = _theme_contrast_choice(
        background,
        theme["text"],
        theme["surface"],
        fallback=theme["text"],
    )
    return {
        "background": background,
        "border": border,
        "text": text,
    }


def _completed_badge_palette(
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    text = theme["text"]
    surface = theme["surface"]
    panel = theme["panelSurface"]
    request_panel = theme["requestPanelSurface"]
    is_light = _theme_relative_luma(surface) >= _theme_relative_luma(text)
    success = _theme_mix(theme["success"], theme["accent"], 0.10, fallback=theme["success"])
    accent = _theme_mix(theme["accent"], theme["success"], 0.20, fallback=theme["accent"])
    fill_start = _theme_mix(
        panel,
        accent,
        0.05 if is_light else 0.10,
        fallback=panel,
    )
    fill_mid = _theme_mix(
        request_panel,
        success,
        0.10 if is_light else 0.16,
        fallback=request_panel,
    )
    fill_end = _theme_mix(
        surface,
        accent,
        0.04 if is_light else 0.12,
        fallback=surface,
    )
    border = _theme_readable_color(
        _theme_mix(theme["panelBorder"], accent, 0.52, fallback=theme["panelBorder"]),
        fill_mid,
        fallback=text,
        min_ratio=1.8,
    )
    primary_ink = _theme_readable_color(
        text,
        fill_mid,
        fallback=_theme_emphasis_ink(fill_mid, fallback=text),
    )
    secondary_ink = _theme_readable_color(theme["muted"], fill_mid, fallback=primary_ink)
    check_text = _theme_readable_color(
        _theme_mix(accent, text, 0.36 if is_light else 0.22, fallback=accent),
        fill_mid,
        fallback=primary_ink,
    )
    elapsed_ink = _theme_readable_color(
        _theme_mix(theme["muted"], success, 0.14, fallback=theme["muted"]),
        fill_mid,
        fallback=primary_ink,
    )
    ring = _theme_readable_color(
        _theme_mix(accent, success, 0.25, fallback=accent),
        fill_mid,
        fallback=text,
        min_ratio=2.15,
    )
    dashed_ring = _theme_readable_color(
        _theme_mix(ring, theme["muted"], 0.30, fallback=ring),
        fill_mid,
        fallback=text,
        min_ratio=1.8,
    )
    stat_box_fill = _theme_mix(
        request_panel,
        success,
        0.07 if is_light else 0.13,
        fallback=request_panel,
    )
    stat_box_border = _theme_readable_color(
        _theme_mix(theme["panelBorder"], accent, 0.34, fallback=theme["panelBorder"]),
        stat_box_fill,
        fallback=primary_ink,
        min_ratio=1.8,
    )
    stat_value = _theme_readable_color(text, stat_box_fill, fallback=primary_ink)
    stat_label = _theme_readable_color(theme["muted"], stat_box_fill, fallback=stat_value)
    return {
        "fillStart": fill_start,
        "fillMid": fill_mid,
        "fillEnd": fill_end,
        "border": border,
        "ring": ring,
        "dashedRing": dashed_ring,
        "titleText": primary_ink,
        "workdirText": secondary_ink,
        "checkText": check_text,
        "elapsedText": elapsed_ink,
        "statBoxFill": stat_box_fill,
        "statBoxBorder": stat_box_border,
        "statValue": stat_value,
        "statLabel": stat_label,
    }


def _transition_palette(
    transition_type: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    if transition_type == "card_to_completed":
        completed = _completed_badge_palette(theme)
        return {
            "fillStart": completed["fillStart"],
            "fillMid": completed["fillMid"],
            "fillEnd": completed["fillEnd"],
            "border": completed["border"],
            "titleText": completed["titleText"],
            "subtitleText": completed["statLabel"],
            "markText": completed["checkText"],
        }
    base = _theme_mix(theme["accent"], theme["info"], 0.22, fallback=theme["accent"])
    fill_start = _theme_mix(base, theme["text"], 0.12, fallback=base)
    fill_mid = _theme_mix(base, theme["info"], 0.26, fallback=base)
    fill_end = _theme_mix(
        theme["requestPanelSurface"],
        base,
        0.52,
        fallback=theme["requestPanelSurface"],
    )
    border = _theme_mix(theme["panelBorder"], base, 0.62, fallback=theme["panelBorder"])
    primary_ink = _theme_contrast_choice(
        fill_mid,
        theme["text"],
        theme["surface"],
        fallback=theme["text"],
    )
    subtitle_ink = _theme_mix(primary_ink, theme["info"], 0.34, fallback=primary_ink)
    return {
        "fillStart": fill_start,
        "fillMid": fill_mid,
        "fillEnd": fill_end,
        "border": border,
        "titleText": primary_ink,
        "subtitleText": subtitle_ink,
        "markText": primary_ink,
    }


def _work_overlay_command_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-commands.jsonl")


def run_work_overlay_helper_qt(
    state_file: str | Path,
    *,
    process_exists: Callable[[int], bool],
    owner_pid_from_path: Callable[[Path], int | None],
    item_limit: int,
    stale_seconds: float,
    overlay_alpha: float,
    hover_alpha: float,
    header_title_limit: int,
) -> int:
    try:
        from PySide6.QtCore import (
            QAbstractAnimation,
            QEasingCurve,
            QFileSystemWatcher,
            QParallelAnimationGroup,
            QPauseAnimation,
            QPoint,
            QPointF,
            Property,
            QPropertyAnimation,
            QRect,
            QRectF,
            QSequentialAnimationGroup,
            QSize,
            Qt,
            QTimer,
        )
        from PySide6.QtGui import (
            QColor,
            QCursor,
            QFont,
            QFontMetrics,
            QLinearGradient,
            QPainter,
            QPainterPath,
            QPen,
            QRadialGradient,
            QTextLayout,
            QTextOption,
        )
        from PySide6.QtWidgets import (
            QApplication,
            QFrame,
            QGraphicsOpacityEffect,
            QHBoxLayout,
            QLabel,
            QSizePolicy,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - depends on local runtime
        raise RuntimeError("PySide6 is required for the desktop work overlay helper.") from exc

    path = Path(str(state_file)).expanduser()

    def read_state() -> dict[str, object] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    app = QApplication.instance() or QApplication([Path(sys.argv[0]).name or "codex-hud-overlay"])
    app.setQuitOnLastWindowClosed(False)
    owner_pid = owner_pid_from_path(path)

    widget_attrs = Qt.WidgetAttribute
    focus_policy = Qt.FocusPolicy
    mouse_buttons = Qt.MouseButton
    alignment = Qt.AlignmentFlag
    text_format = Qt.TextFormat
    window_type = Qt.WindowType

    class ShimmerTextLabel(QWidget):
        """QPainter text-mask shimmer for active work status text."""

        def __init__(
            self,
            text: str = "",
            parent: QWidget | None = None,
            *,
            base_color: str = "#8492A6",
            highlight_color: str = WORK_OVERLAY_SHIMMER_HIGHLIGHT,
            band_width_px: float = WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX,
            step_px: float = WORK_OVERLAY_SHIMMER_STEP_PX,
            timer_ms: int = WORK_OVERLAY_SHIMMER_TIMER_MS,
        ) -> None:
            super().__init__(parent)
            self._text = str(text or "")
            self._base_color = QColor(base_color)
            self._highlight_color = QColor(highlight_color)
            self._band_width_px = max(1.0, float(band_width_px))
            self._step_px = max(0.25, float(step_px))
            self._timer_ms = max(10, int(timer_ms))
            self._phase_x = -self._band_width_px
            self._shimmer_enabled = True
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._advance_shimmer)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            self._timer.start(self._timer_ms)

        def text(self) -> str:
            return self._text

        def setText(self, text: str) -> None:
            next_text = str(text or "")
            if next_text == self._text:
                return
            self._text = next_text
            self._phase_x = -self._band_width_px
            self.updateGeometry()
            self.update()

        def setBaseColor(self, color: str) -> None:
            self._base_color = QColor(color)
            self.update()

        def setHighlightColor(self, color: str) -> None:
            self._highlight_color = QColor(color)
            self.update()

        def setShimmerEnabled(self, enabled: bool) -> None:
            next_enabled = bool(enabled)
            if next_enabled == self._shimmer_enabled:
                return
            self._shimmer_enabled = next_enabled
            self._phase_x = -self._band_width_px
            if self._shimmer_enabled and self.isVisible():
                self._timer.start(self._timer_ms)
            else:
                self._timer.stop()
            self.update()

        def hasHeightForWidth(self) -> bool:
            return True

        def heightForWidth(self, width: int) -> int:
            return self._layout_height(max(1, int(width)))

        def sizeHint(self) -> QSize:
            width = self.width() or WORK_OVERLAY_TEXT_WRAP_WIDTH
            return QSize(width, self.heightForWidth(width))

        def minimumSizeHint(self) -> QSize:
            width = self.width() or WORK_OVERLAY_TEXT_WRAP_WIDTH
            return QSize(1, self.heightForWidth(width))

        def showEvent(self, event: object) -> None:
            if self._shimmer_enabled and not self._timer.isActive():
                self._timer.start(self._timer_ms)
            super().showEvent(event)

        def hideEvent(self, event: object) -> None:
            self._timer.stop()
            super().hideEvent(event)

        def paintEvent(self, event: object) -> None:
            del event
            path = self._text_path(max(1, self.width()))
            if path.isEmpty():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)

            painter.setBrush(self._base_color)
            painter.drawPath(path)

            if not self._shimmer_enabled:
                return

            transparent = QColor(self._highlight_color)
            transparent.setAlpha(0)
            peak = QColor(self._highlight_color)
            peak.setAlpha(WORK_OVERLAY_SHIMMER_PEAK_ALPHA)
            shoulder = QColor(self._highlight_color)
            shoulder.setAlpha(90)
            gradient = QLinearGradient(
                QPointF(self._phase_x - self._band_width_px, 0.0),
                QPointF(self._phase_x + self._band_width_px, 0.0),
            )
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(0.36, shoulder)
            gradient.setColorAt(0.5, peak)
            gradient.setColorAt(0.64, shoulder)
            gradient.setColorAt(1.0, transparent)
            painter.setBrush(gradient)
            painter.drawPath(path)

        def _advance_shimmer(self) -> None:
            if not self.isVisible():
                return
            limit = max(self.width(), self._text_width()) + self._band_width_px
            self._phase_x += self._step_px
            if self._phase_x > limit:
                self._phase_x = -self._band_width_px
            self.update()

        def _text_width(self) -> float:
            width = 0.0
            for _, _, _, _, line_width in self._layout_lines(max(1, self.width())):
                width = max(width, line_width)
            return width

        def _layout_height(self, width: int) -> int:
            lines = self._layout_lines(width)
            if not lines:
                return max(1, int(self.fontMetrics().height() + 2))
            bottom = max(y + line_height for y, _, _, line_height, _ in lines)
            return max(1, int(bottom + 2.999))

        def _layout_lines(self, width: int) -> list[tuple[float, str, float, float, float]]:
            text = self._text
            if not text:
                return []
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            layout = QTextLayout(text, self.font())
            layout.setTextOption(option)
            lines: list[tuple[float, str, float, float, float]] = []
            y = 0.0
            layout.beginLayout()
            try:
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(max(1, width))
                    line.setPosition(QPointF(0.0, y))
                    start = line.textStart()
                    length = line.textLength()
                    segment = text[start : start + length]
                    lines.append(
                        (
                            y,
                            segment,
                            float(line.ascent()),
                            float(line.height()),
                            float(line.naturalTextWidth()),
                        )
                    )
                    y += float(line.height())
            finally:
                layout.endLayout()
            return lines

        def _text_path(self, width: int) -> QPainterPath:
            path = QPainterPath()
            text = self._text
            if not text:
                return path
            for y, segment, line_ascent, _, _ in self._layout_lines(width):
                if not segment:
                    continue
                baseline = y + line_ascent
                path.addText(QPointF(0.0, baseline), self.font(), segment)
            return path

    class CloseButtonWindow(QWidget):
        def __init__(self, dismiss_callback: Callable[[Mapping[str, object]], None]) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            super().__init__(None, flags)
            self._dismiss_callback = dismiss_callback
            self._item: Mapping[str, object] = {}
            self._bg = QColor("#10161D")
            self._hover_bg = QColor("#263241")
            self._fg = QColor("#A9B6C6")
            self._hover_fg = QColor("#F3D27A")
            self._hover = False
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)

        def configure(
            self,
            item: Mapping[str, object],
            *,
            background: str,
            hover_background: str,
            foreground: str,
            hover_foreground: str,
            opacity: float,
        ) -> None:
            self._item = dict(item)
            self._bg = QColor(background)
            self._hover_bg = QColor(hover_background)
            self._fg = QColor(foreground)
            self._hover_fg = QColor(hover_foreground)
            self.setWindowOpacity(opacity)
            self.update()

        def enterEvent(self, event: object) -> None:
            self._hover = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event: object) -> None:
            self._hover = False
            self.update()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == mouse_buttons.LeftButton and self.rect().contains(
                event.position().toPoint()
            ):
                self._dismiss_callback(self._item)
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._hover_bg if self._hover else self._bg)
            painter.drawRoundedRect(self.rect(), 6, 6)
            painter.setPen(self._hover_fg if self._hover else self._fg)
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
            painter.drawText(self.rect(), alignment.AlignCenter, "×")

    class WorkdirLinkWindow(QWidget):
        def __init__(
            self,
            activate_callback: Callable[[Mapping[str, object]], None],
        ) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            super().__init__(None, flags)
            self._activate_callback = activate_callback
            self._item: Mapping[str, object] = {}
            self._hover = False
            self._pending = False
            self._pending_started_at = 0.0
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def configure(
            self,
            item: Mapping[str, object],
            *,
            opacity: float,
            pending: bool = False,
            pending_started_at: float = 0.0,
        ) -> None:
            self._item = dict(item)
            self._pending = bool(pending)
            self._pending_started_at = float(pending_started_at or 0.0)
            self.setWindowOpacity(opacity)
            tooltip = str(
                item.get("targetTitle") or item.get("title") or item.get("workdir") or ""
            ).strip()
            if self._pending:
                tooltip = (tooltip + "\n" if tooltip else "") + "正在前往会话..."
            self.setToolTip(tooltip)
            self.update()

        def enterEvent(self, event: object) -> None:
            self._hover = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event: object) -> None:
            self._hover = False
            self.update()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == mouse_buttons.LeftButton and self.rect().contains(
                event.position().toPoint()
            ):
                self._activate_callback(self._item)
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            # Keep a near-transparent fill so Windows still treats the hot area
            # as a hit-testable layered window instead of letting clicks pass through.
            fill = QColor(255, 255, 255, 1)
            if self._hover and _workdir_link_hover_visible_for_item(self._item):
                fill = QColor(156, 203, 255, 18)
            if self._pending:
                fill = QColor(156, 203, 255, 42)
            painter.setBrush(fill)
            painter.drawRoundedRect(self.rect(), 4, 4)
            if not self._pending:
                return
            elapsed = max(0.0, time.monotonic() - self._pending_started_at)
            side = min(16, max(10, self.height() - 8))
            spinner_rect = QRectF(6, (self.height() - side) / 2, side, side)
            spinner = QPen(QColor(156, 203, 255, 230), 2.0)
            spinner.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(spinner)
            start_angle = int((elapsed * 540.0) % 360.0 * 16)
            painter.drawArc(spinner_rect, -start_angle, -270 * 16)
            if self.width() < 86:
                return
            painter.setPen(QColor(220, 238, 255, 238))
            painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
            label = (
                "仍在前往会话..."
                if elapsed >= WORK_OVERLAY_SWITCH_PENDING_SLOW_SECONDS
                else "正在前往会话..."
            )
            text_rect = self.rect().adjusted(side + 12, 0, -4, 0)
            painter.drawText(text_rect, alignment.AlignVCenter | alignment.AlignLeft, label)

    class CardSwitchPendingOverlayWidget(QWidget):
        def __init__(self, parent: QWidget) -> None:
            super().__init__(parent)
            self._switch_pending = False
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed = False
            self._switch_pending_completed_at = 0.0
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._advance)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.hide()

        def set_switch_pending(
            self,
            pending: bool,
            started_at: float,
            *,
            completed: bool = False,
            completed_at: float = 0.0,
        ) -> None:
            pending = bool(pending)
            started_at = float(started_at or 0.0)
            completed = bool(completed)
            completed_at = float(completed_at or 0.0)
            if self._switch_pending == pending and (
                not pending
                or abs(self._switch_pending_started_at - started_at) < 0.001
            ) and self._switch_pending_completed == completed and (
                not completed
                or abs(self._switch_pending_completed_at - completed_at) < 0.001
            ):
                return
            self._switch_pending = pending
            self._switch_pending_started_at = started_at if pending else 0.0
            self._switch_pending_completed = completed if pending else False
            self._switch_pending_completed_at = completed_at if pending and completed else 0.0
            if pending:
                self.show()
                self.raise_()
                if not self._timer.isActive():
                    self._timer.start(24)
            else:
                self._timer.stop()
                self.hide()
            self.update()

        def _advance(self) -> None:
            if not self._switch_pending:
                self._timer.stop()
                self.hide()
                return
            if (
                self._switch_pending_completed
                and self._switch_pending_completed_at > 0.0
                and time.monotonic() - self._switch_pending_completed_at
                > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
            ):
                self._timer.stop()
                self.hide()
                return
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            if not self._switch_pending:
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            now = time.monotonic()
            started_at = self._switch_pending_started_at or now
            elapsed = max(0.0, now - started_at)
            completed = self._switch_pending_completed
            finish_elapsed = (
                max(0.0, now - self._switch_pending_completed_at)
                if completed and self._switch_pending_completed_at > 0.0
                else 0.0
            )
            finish_progress = (
                _completed_pending_finish_progress(finish_elapsed)
                if completed
                else 0.0
            )
            motion_alpha = 1.0 - _ease_out_cubic(finish_progress)
            launch_progress = 1.0 if completed else _completed_pending_launch_progress(elapsed)

            rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            center = QPointF(rect.center())
            painter.setPen(Qt.PenStyle.NoPen)
            veil_opacity = _completed_pending_caption_opacity(
                elapsed,
                completed=completed,
                finish_elapsed_seconds=finish_elapsed,
            )
            painter.setBrush(QColor(3, 17, 27, int(54 * veil_opacity)))
            painter.drawRoundedRect(rect, 10.0, 10.0)

            self._draw_card_quantum_frame(
                painter,
                rect,
                elapsed,
                launch_progress=launch_progress,
                finish_progress=finish_progress,
                motion_alpha=motion_alpha,
                completed=completed,
            )
            self._draw_card_caption(
                painter,
                center,
                elapsed,
                completed=completed,
                finish_elapsed=finish_elapsed,
            )

        def _draw_card_quantum_frame(
            self,
            painter: QPainter,
            rect: QRectF,
            elapsed: float,
            *,
            launch_progress: float,
            finish_progress: float,
            motion_alpha: float,
            completed: bool,
        ) -> None:
            frame_rect = rect.adjusted(8.0, 7.0, -8.0, -7.0)
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if launch_progress < 1.0:
                alpha = int(155 * (1.0 - launch_progress))
                launch_rect = frame_rect.adjusted(
                    -10.0 * _ease_out_cubic(launch_progress),
                    -5.0 * _ease_out_cubic(launch_progress),
                    10.0 * _ease_out_cubic(launch_progress),
                    5.0 * _ease_out_cubic(launch_progress),
                )
                painter.setPen(QPen(QColor(82, 218, 255, alpha), 1.6))
                painter.drawRoundedRect(launch_rect, 12.0, 12.0)

            if completed:
                eased = _ease_out_cubic(finish_progress)
                alpha = int(185 * (1.0 - finish_progress))
                finish_rect = frame_rect.adjusted(
                    -18.0 * eased,
                    -8.0 * eased,
                    18.0 * eased,
                    8.0 * eased,
                )
                painter.setPen(QPen(QColor(102, 255, 218, alpha), 2.0))
                painter.drawRoundedRect(finish_rect, 14.0, 14.0)

            painter.setPen(QPen(QColor(93, 216, 255, int(128 * motion_alpha)), 1.1))
            painter.drawRoundedRect(frame_rect, 10.0, 10.0)

            points: list[QPointF] = []
            pulses: list[float] = []
            count = 3
            for index in range(count):
                angle, jitter, pulse = _completed_pending_particle_state(elapsed, index, count)
                points.append(self._card_perimeter_point(frame_rect, angle / math.tau, jitter))
                pulses.append(pulse)

            for index, point in enumerate(points):
                next_index = (index + 1) % len(points)
                strength = min(pulses[index], pulses[next_index])
                if strength <= 0.08:
                    continue
                pen = QPen(
                    QColor(102, 255, 218, int(120 * strength * motion_alpha)),
                    max(0.45, 1.0 * strength),
                )
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(point, points[next_index])

            for index, point in enumerate(points):
                pulse = pulses[index]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(66, 225, 255, int((78 + pulse * 80) * motion_alpha)))
                painter.drawEllipse(point, 4.7 + pulse * 1.8, 4.7 + pulse * 1.8)
                painter.setBrush(QColor(238, 255, 255, int(238 * motion_alpha)))
                painter.drawEllipse(point, 1.7, 1.7)
            painter.restore()

        def _card_perimeter_point(
            self,
            rect: QRectF,
            progress: float,
            jitter: float,
        ) -> QPointF:
            width = max(1.0, rect.width())
            height = max(1.0, rect.height())
            distance = (float(progress) % 1.0) * (width + height) * 2.0
            if distance <= width:
                point = QPointF(rect.left() + distance, rect.top())
            elif distance <= width + height:
                point = QPointF(rect.right(), rect.top() + distance - width)
            elif distance <= width * 2.0 + height:
                point = QPointF(rect.right() - (distance - width - height), rect.bottom())
            else:
                point = QPointF(rect.left(), rect.bottom() - (distance - width * 2.0 - height))
            center = rect.center()
            dx = point.x() - center.x()
            dy = point.y() - center.y()
            length = max(1.0, math.hypot(dx, dy))
            return QPointF(
                point.x() + (dx / length) * jitter * 2.8,
                point.y() + (dy / length) * jitter * 2.8,
            )

        def _draw_card_caption(
            self,
            painter: QPainter,
            center: QPointF,
            pending_elapsed: float,
            *,
            completed: bool,
            finish_elapsed: float,
        ) -> None:
            opacity = _completed_pending_caption_opacity(
                pending_elapsed,
                completed=completed,
                finish_elapsed_seconds=finish_elapsed,
            )
            if opacity <= 0.001:
                return
            finish_progress = (
                _completed_pending_finish_progress(finish_elapsed)
                if completed
                else 0.0
            )
            scale = 1.0 + (0.055 * math.sin(math.pi * min(1.0, finish_progress)) if completed else 0.0)
            text = "已跳转" if completed else "正在跳转"
            panel_rect = QRectF(center.x() - 47.0, center.y() - 16.0, 94.0, 32.0)

            painter.save()
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)
            painter.setPen(
                QPen(
                    QColor(118, 255, 202, int(210 * opacity))
                    if completed
                    else QColor(76, 213, 255, int(180 * opacity)),
                    1.0,
                )
            )
            painter.setBrush(QColor(5, 23, 34, int(152 * opacity)))
            painter.drawRoundedRect(panel_rect, 10.0, 10.0)
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.setPen(
                QColor(204, 255, 232, int(245 * opacity))
                if completed
                else QColor(229, 250, 255, int(245 * opacity))
            )
            painter.drawText(panel_rect, alignment.AlignCenter, text)
            painter.restore()

    class ClickHotspotWindow(QWidget):
        def __init__(
            self,
            callback: Callable[[Mapping[str, object]], None],
            *,
            circle: bool = False,
            hover_color: str = "#9CCBFF",
        ) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            super().__init__(None, flags)
            self._callback = callback
            self._item: Mapping[str, object] = {}
            self._circle = bool(circle)
            self._hover = False
            self._hover_color = QColor(hover_color)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def configure(
            self,
            item: Mapping[str, object],
            *,
            opacity: float,
            tooltip: str = "",
            hover_color: str | None = None,
        ) -> None:
            self._item = dict(item)
            self.setWindowOpacity(opacity)
            self.setToolTip(tooltip)
            if hover_color is not None:
                self._hover_color = QColor(hover_color)
            self.update()

        def enterEvent(self, event: object) -> None:
            self._hover = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event: object) -> None:
            self._hover = False
            self.update()
            super().leaveEvent(event)

        def _contains_point(self, point: QPoint) -> bool:
            if not self.rect().contains(point):
                return False
            if not self._circle:
                return True
            center = self.rect().center()
            radius = min(self.width(), self.height()) / 2.0
            dx = float(point.x() - center.x())
            dy = float(point.y() - center.y())
            return (dx * dx + dy * dy) <= radius * radius

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == mouse_buttons.LeftButton and self._contains_point(
                event.position().toPoint()
            ):
                self._callback(self._item)
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(255, 255, 255, 1)
            if self._hover:
                fill = QColor(self._hover_color)
                fill.setAlpha(18)
            painter.setBrush(fill)
            if self._circle:
                painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
            else:
                painter.drawRoundedRect(self.rect(), 6, 6)

    class EnergyRingAnnihilationWidget(QWidget):
        """Transient completion-circle disappearance effect."""

        def __init__(
            self,
            *,
            max_radius: float,
            center: QPointF,
        ) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            transparent_input = getattr(window_type, "WindowTransparentForInput", 0)
            if transparent_input:
                flags |= transparent_input
            super().__init__(None, flags)
            self._progress = 0.0
            self._max_radius = max(10.0, float(max_radius))
            self._center = QPointF(center) if center is not None else QPointF(0.0, 0.0)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(focus_policy.NoFocus)

        def set_core_center(self, center: QPointF) -> None:
            self._center = QPointF(center)
            self.update()

        def get_progress(self) -> float:
            return self._progress

        def set_progress(self, value: float) -> None:
            value = _clamp01(value)
            if abs(self._progress - value) < 0.00001:
                return
            self._progress = value
            self.update()

        progress = Property(float, get_progress, set_progress)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = self._center
            if center.isNull():
                center = QPointF(self.width() / 2.0, self.height() / 2.0)

            progress = _clamp01(self._progress)
            outer_radius = 10.0 + (self._max_radius - 10.0) * progress
            inner_radius = 0.0
            if progress > 0.1:
                inner_radius = (outer_radius * 0.85) * ((progress - 0.1) / 0.9)

            alpha = max(0, int(255 * (1.0 - progress * progress)))
            if outer_radius <= inner_radius or alpha <= 0:
                return

            ring_path = QPainterPath()
            ring_path.addEllipse(center, outer_radius, outer_radius)
            if inner_radius > 0.0:
                inner_path = QPainterPath()
                inner_path.addEllipse(center, inner_radius, inner_radius)
                ring_path = ring_path.subtracted(inner_path)

            gradient = QRadialGradient(center, outer_radius)
            mid_point = _clamp01(inner_radius / max(1.0, outer_radius))
            core_color = QColor(0, 191, 255, alpha)
            edge_color = QColor(135, 206, 250, int(alpha * 0.3))
            transparent_color = QColor(135, 206, 250, 0)
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(mid_point, QColor(0, 0, 0, 0))
            gradient.setColorAt(mid_point + (1.0 - mid_point) * 0.3, core_color)
            gradient.setColorAt(0.95, edge_color)
            gradient.setColorAt(1.0, transparent_color)

            painter.setBrush(gradient)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(ring_path)

            if progress > 0.4:
                delayed = _clamp01(progress - 0.4)
                delayed_outer = 10.0 + (self._max_radius - 10.0) * delayed
                delayed_alpha = max(0, int(100 * (1.0 - delayed)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 191, 255, delayed_alpha), 1.5))
                painter.drawEllipse(center, delayed_outer, delayed_outer)

    class CompletedBadgeWidget(QWidget):
        """Animated circular summary for finished work."""

        def __init__(
            self,
            item: Mapping[str, object],
            parent: QWidget | None = None,
            *,
            animate_intro: bool = True,
            theme_tokens: Mapping[str, object] | None = None,
        ) -> None:
            super().__init__(parent)
            self._item: Mapping[str, object] = dict(item)
            self._theme_tokens = _resolved_overlay_theme(theme_tokens)
            self._started_at = time.monotonic()
            self._progress = 0.0 if animate_intro else 1.0
            self._switch_pending = False
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed = False
            self._switch_pending_completed_at = 0.0
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._advance)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.setFixedSize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )
            if animate_intro:
                self._timer.start(16)

        def set_item(self, item: Mapping[str, object]) -> None:
            self._item = dict(item)
            self.update()

        def set_theme_tokens(
            self,
            theme_tokens: Mapping[str, object] | None,
        ) -> None:
            self._theme_tokens = _resolved_overlay_theme(theme_tokens)
            self.update()

        def set_switch_pending(
            self,
            pending: bool,
            started_at: float,
            *,
            completed: bool = False,
            completed_at: float = 0.0,
        ) -> None:
            pending = bool(pending)
            started_at = float(started_at or 0.0)
            completed = bool(completed)
            completed_at = float(completed_at or 0.0)
            if self._switch_pending == pending and (
                not pending
                or abs(self._switch_pending_started_at - started_at) < 0.001
            ) and self._switch_pending_completed == completed and (
                not completed
                or abs(self._switch_pending_completed_at - completed_at) < 0.001
            ):
                return
            self._switch_pending = pending
            self._switch_pending_started_at = started_at if pending else 0.0
            self._switch_pending_completed = completed if pending else False
            self._switch_pending_completed_at = completed_at if pending and completed else 0.0
            if pending and not self._timer.isActive():
                self._timer.start(24)
            elif not pending and self._progress >= 1.0:
                self._timer.stop()
            self.update()

        def sizeHint(self) -> QSize:
            return QSize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )

        def _advance(self) -> None:
            elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
            if self._progress < 1.0:
                self._progress = min(1.0, elapsed_ms / WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS)
            if self._progress >= 1.0 and not self._switch_pending:
                self._timer.stop()
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            paint_scale = min(
                max(1.0, float(self.width())) / float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
                max(1.0, float(self.height())) / float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
            )
            painter.translate(
                (float(self.width()) - WORK_OVERLAY_COMPLETED_BADGE_SIZE * paint_scale) / 2.0,
                0.0,
            )
            painter.scale(paint_scale, paint_scale)

            eased = 1.0 - pow(1.0 - max(0.0, min(1.0, self._progress)), 3)
            palette = _completed_badge_palette(self._theme_tokens)
            final_size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
            pending_elapsed = (
                max(0.0, time.monotonic() - self._switch_pending_started_at)
                if self._switch_pending
                else 0.0
            )
            launch_scale = (
                _completed_pending_launch_scale(pending_elapsed)
                if not self._switch_pending_completed
                else 1.0
            )
            if abs(launch_scale - 1.0) > 0.0001:
                launch_center = QPointF(final_size / 2.0, final_size / 2.0)
                painter.translate(launch_center)
                painter.scale(launch_scale, launch_scale)
                painter.translate(-launch_center)
            start_height = 118.0
            start_width = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
            start_rect = QRectF(
                0.0,
                (WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT - start_height) / 2.0,
                start_width,
                start_height,
            )
            end_rect = QRectF(
                0.0,
                0.0,
                final_size,
                final_size,
            )
            rect = QRectF(
                start_rect.x() + (end_rect.x() - start_rect.x()) * eased,
                start_rect.y() + (end_rect.y() - start_rect.y()) * eased,
                start_rect.width() + (end_rect.width() - start_rect.width()) * eased,
                start_rect.height() + (end_rect.height() - start_rect.height()) * eased,
            )
            radius = 10.0 + ((final_size / 2.0) - 10.0) * eased

            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor(palette["fillStart"]))
            gradient.setColorAt(0.5, QColor(palette["fillMid"]))
            gradient.setColorAt(1.0, QColor(palette["fillEnd"]))
            painter.setBrush(gradient)
            painter.setPen(QPen(QColor(palette["border"]), 1.4))
            painter.drawRoundedRect(rect, radius, radius)

            if eased < 0.24:
                return

            content_alpha = int(255 * min(1.0, (eased - 0.24) / 0.42))
            painter.save()
            painter.setOpacity(content_alpha / 255.0)
            center = QPointF(end_rect.center())

            outer_pen = QPen(QColor(palette["ring"]), 2.0)
            painter.setPen(outer_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(end_rect.adjusted(4.0, 4.0, -4.0, -4.0))

            dashed_color = QColor(palette["dashedRing"])
            dashed_color.setAlpha(145)
            dashed_pen = QPen(dashed_color, 1.1)
            dashed_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed_pen)
            painter.drawEllipse(end_rect.adjusted(17.0, 17.0, -17.0, -17.0))

            title = str(self._item.get("title") or "Codex 工作").strip()
            workdir = _workdir_display_name(self._item)
            self._draw_arc_text(
                painter,
                title,
                center=center,
                radius=67.0,
                start_degrees=-149.0,
                end_degrees=-31.0,
                font=QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold),
                color=QColor(palette["titleText"]),
                bottom=False,
            )
            if workdir:
                self._draw_arc_text(
                    painter,
                    workdir,
                    center=center,
                    radius=78.0,
                    start_degrees=145.0,
                    end_degrees=35.0,
                    font=QFont("Microsoft YaHei UI", 9),
                    color=QColor(palette["workdirText"]),
                    bottom=True,
                    compact_spacing=True,
                )

            check_font = QFont("Segoe UI Symbol", 38, QFont.Weight.Bold)
            painter.setFont(check_font)
            painter.setPen(QColor(palette["checkText"]))
            painter.drawText(
                QRectF(center.x() - 34.0, center.y() - 66.0, 68.0, 56.0),
                alignment.AlignCenter,
                "✓",
            )

            elapsed = str(self._item.get("elapsedText") or "已处理 --").strip()
            painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor(palette["elapsedText"]))
            painter.drawText(
                QRectF(center.x() - 54.0, center.y() - 10.0, 108.0, 18.0),
                alignment.AlignCenter,
                _compact_work_text(elapsed, 18),
            )

            stats = [
                ("Tokens", str(self._item.get("tokensText") or "0").strip() or "0"),
                ("Cost", str(self._item.get("costText") or "$0").strip() or "$0"),
                ("Cache", str(self._item.get("cacheHitText") or "--").strip() or "--"),
            ]
            box_width = 44.0
            box_height = 25.0
            spacing = 5.0
            start_x = center.x() - ((box_width * 3.0 + spacing * 2.0) / 2.0)
            y = center.y() + 14.0
            for index, (label, value) in enumerate(stats):
                box = QRectF(start_x + index * (box_width + spacing), y, box_width, box_height)
                stat_border = QColor(palette["statBoxBorder"])
                stat_border.setAlpha(220)
                painter.setPen(QPen(stat_border, 0.8))
                stat_fill = QColor(palette["statBoxFill"])
                stat_fill.setAlpha(235)
                painter.setBrush(stat_fill)
                painter.drawRoundedRect(box, 6.0, 6.0)
                painter.setPen(QColor(palette["statValue"]))
                painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
                painter.drawText(box.adjusted(1.0, 1.0, -1.0, -11.0), alignment.AlignCenter, value)
                painter.setPen(QColor(palette["statLabel"]))
                painter.setFont(QFont("Microsoft YaHei UI", 5))
                painter.drawText(box.adjusted(1.0, 13.0, -1.0, -1.0), alignment.AlignCenter, label)

            painter.restore()
            if self._switch_pending:
                self._draw_switch_pending_quantum(
                    painter,
                    end_rect,
                    completed=self._switch_pending_completed,
                    completed_at=self._switch_pending_completed_at,
                )

        def _arc_limited_text(self, text: str, font: QFont, max_width: float) -> str:
            compact = " ".join(str(text or "").split())
            if not compact:
                return ""
            metrics = QFontMetrics(font)
            if metrics.horizontalAdvance(compact) <= max_width:
                return compact
            suffix = "..."
            available = max(1, int(max_width - metrics.horizontalAdvance(suffix)))
            result = ""
            for char in compact:
                if metrics.horizontalAdvance(result + char) > available:
                    break
                result += char
            return (result.rstrip() + suffix) if result else suffix

        def _draw_arc_text(
            self,
            painter: QPainter,
            text: str,
            *,
            center: QPointF,
            radius: float,
            start_degrees: float,
            end_degrees: float,
            font: QFont,
            color: QColor,
            bottom: bool,
            compact_spacing: bool = False,
        ) -> None:
            span = abs(end_degrees - start_degrees)
            max_width = math.radians(span) * radius * 0.86
            arc_text = self._arc_limited_text(text, font, max_width)
            if not arc_text:
                return
            painter.save()
            painter.setFont(font)
            painter.setPen(color)
            metrics = painter.fontMetrics()
            widths = [max(1, metrics.horizontalAdvance(char)) for char in arc_text]
            total_width = float(sum(widths))
            tracking_px = 0.0
            layout_width = total_width + tracking_px * max(0, len(widths) - 1)
            effective_span = span
            if compact_spacing:
                effective_span = min(span, math.degrees(layout_width / max(1.0, radius)))
            degrees_per_px = effective_span / max(1.0, layout_width)
            direction = 1.0 if end_degrees >= start_degrees else -1.0
            current = start_degrees + direction * ((span - effective_span) / 2.0)
            for char, width in zip(arc_text, widths):
                angle = current + direction * (width * degrees_per_px / 2.0)
                radians = math.radians(angle)
                x = center.x() + math.cos(radians) * radius
                y = center.y() + math.sin(radians) * radius
                painter.save()
                painter.translate(QPointF(x, y))
                rotation = angle - 90.0 if bottom else angle + 90.0
                painter.rotate(rotation)
                painter.drawText(
                    QRectF(-width / 2.0 - 1.0, -metrics.ascent(), width + 2.0, metrics.height()),
                    alignment.AlignCenter,
                    char,
                )
                painter.restore()
                current += direction * (width + tracking_px) * degrees_per_px
            painter.restore()

        def _draw_switch_pending_quantum(
            self,
            painter: QPainter,
            rect: QRectF,
            *,
            completed: bool,
            completed_at: float,
        ) -> None:
            started_at = self._switch_pending_started_at or time.monotonic()
            elapsed = max(0.0, time.monotonic() - started_at)
            finish_elapsed = (
                max(0.0, time.monotonic() - completed_at)
                if completed and completed_at > 0.0
                else 0.0
            )
            finish_progress = (
                _completed_pending_finish_progress(finish_elapsed)
                if completed
                else 0.0
            )
            motion_alpha = 1.0 - _ease_out_cubic(finish_progress)
            launch_progress = 1.0 if completed else _completed_pending_launch_progress(elapsed)
            center = QPointF(rect.center())
            orbit_radius = min(rect.width(), rect.height()) / 2.0 - 7.0

            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if launch_progress < 1.0:
                self._draw_switch_pending_launch(painter, center, orbit_radius, launch_progress)
            if completed:
                self._draw_switch_pending_finish(painter, center, orbit_radius, finish_progress)

            ring_alpha = motion_alpha if completed else max(0.18, motion_alpha)
            ring_color = QColor(93, 216, 255, int(170 * ring_alpha))
            painter.setPen(QPen(ring_color, 1.6))
            painter.drawEllipse(center, orbit_radius, orbit_radius)

            collapse_color = QColor(119, 255, 210, int(60 * motion_alpha))
            collapse_pen = QPen(collapse_color, 0.9)
            collapse_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(collapse_pen)
            wobble = 1.8 * math.sin(elapsed * 5.5)
            painter.drawEllipse(center, orbit_radius - 10.0 + wobble, orbit_radius - 10.0 + wobble)

            points: list[QPointF] = []
            pulses: list[float] = []
            count = 3
            for index in range(count):
                angle, radial_jitter, link_pulse = _completed_pending_particle_state(
                    elapsed,
                    index,
                    count,
                )
                particle_radius = orbit_radius + radial_jitter * 4.2
                point = QPointF(
                    center.x() + math.cos(angle) * particle_radius,
                    center.y() + math.sin(angle) * particle_radius,
                )
                points.append(point)
                pulses.append(link_pulse)

            for index, point in enumerate(points):
                next_index = (index + 1) % len(points)
                strength = min(pulses[index], pulses[next_index])
                if strength <= 0.08:
                    continue
                filament = QColor(102, 255, 218, int(150 * strength * motion_alpha))
                pen = QPen(filament, max(0.55, 1.15 * strength))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(point, points[next_index])

            for index, point in enumerate(points):
                pulse = pulses[index]
                glow_radius = 5.8 + pulse * 2.2
                glow = QColor(66, 225, 255, int((95 + pulse * 90) * motion_alpha))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(point, glow_radius, glow_radius)

                core = QColor(238, 255, 255, int(242 * motion_alpha))
                painter.setBrush(core)
                painter.drawEllipse(point, 2.0, 2.0)

            self._draw_switch_pending_caption(
                painter,
                center,
                elapsed,
                completed=completed,
                finish_elapsed=finish_elapsed,
            )
            painter.restore()

        def _draw_switch_pending_launch(
            self,
            painter: QPainter,
            center: QPointF,
            orbit_radius: float,
            progress: float,
        ) -> None:
            progress = _clamp01(progress)
            eased = _ease_out_cubic(progress)
            alpha = int(210 * (1.0 - progress))
            if alpha <= 0:
                return

            wave_center = QPointF(center.x(), center.y() + orbit_radius * 0.74)
            wave_radius_x = 14.0 + (orbit_radius * 0.58) * eased
            wave_radius_y = 4.0 + (orbit_radius * 0.16) * eased
            wave_pen = QPen(QColor(100, 255, 218, alpha), 1.4)
            wave_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(wave_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(wave_center, wave_radius_x, wave_radius_y)

            sweep_rect = QRectF(
                center.x() - orbit_radius,
                center.y() - orbit_radius,
                orbit_radius * 2.0,
                orbit_radius * 2.0,
            )
            sweep_pen = QPen(QColor(84, 220, 255, min(255, int(alpha * 1.15))), 3.1)
            sweep_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(sweep_pen)
            start_degrees = -115.0 + 395.0 * eased
            painter.drawArc(
                sweep_rect,
                int(-start_degrees * 16),
                int(-76.0 * (1.0 - progress * 0.35) * 16),
            )

            inner_pen = QPen(QColor(228, 255, 255, int(alpha * 0.58)), 1.0)
            inner_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(inner_pen)
            painter.drawArc(
                sweep_rect.adjusted(10.0, 10.0, -10.0, -10.0),
                int(-(start_degrees + 28.0) * 16),
                int(-34.0 * 16),
            )

        def _draw_switch_pending_finish(
            self,
            painter: QPainter,
            center: QPointF,
            orbit_radius: float,
            progress: float,
        ) -> None:
            progress = _clamp01(progress)
            eased = _ease_out_cubic(progress)
            alpha = int(170 * (1.0 - progress))
            if alpha <= 0:
                return
            finish_radius = orbit_radius * (0.42 + 0.58 * eased)
            finish_pen = QPen(QColor(102, 255, 218, alpha), 2.0)
            finish_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(finish_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, finish_radius, finish_radius)

            sweep_rect = QRectF(
                center.x() - orbit_radius,
                center.y() - orbit_radius,
                orbit_radius * 2.0,
                orbit_radius * 2.0,
            )
            sweep_pen = QPen(QColor(230, 255, 255, int(alpha * 0.75)), 2.4)
            sweep_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(sweep_pen)
            painter.drawArc(
                sweep_rect.adjusted(5.0, 5.0, -5.0, -5.0),
                int((-60.0 - 240.0 * eased) * 16),
                int(-92.0 * (1.0 - progress * 0.4) * 16),
            )

        def _draw_switch_pending_caption(
            self,
            painter: QPainter,
            center: QPointF,
            pending_elapsed: float,
            *,
            completed: bool,
            finish_elapsed: float,
        ) -> None:
            opacity = _completed_pending_caption_opacity(
                pending_elapsed,
                completed=completed,
                finish_elapsed_seconds=finish_elapsed,
            )
            if opacity <= 0.001:
                return

            finish_progress = (
                _completed_pending_finish_progress(finish_elapsed)
                if completed
                else 0.0
            )
            scale = 1.0 + (0.07 * math.sin(math.pi * min(1.0, finish_progress)) if completed else 0.0)
            text = "已跳转" if completed else "正在跳转"
            panel_rect = QRectF(center.x() - 45.0, center.y() - 16.0, 90.0, 32.0)

            painter.save()
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)

            panel_alpha = int(150 * opacity)
            border_alpha = int((210 if completed else 180) * opacity)
            text_alpha = int(245 * opacity)
            panel_color = QColor(5, 23, 34, panel_alpha)
            border_color = QColor(118, 255, 202, border_alpha) if completed else QColor(76, 213, 255, border_alpha)
            text_color = QColor(204, 255, 232, text_alpha) if completed else QColor(229, 250, 255, text_alpha)

            painter.setPen(QPen(border_color, 1.0))
            painter.setBrush(panel_color)
            painter.drawRoundedRect(panel_rect, 10.0, 10.0)

            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.setPen(text_color)
            painter.drawText(panel_rect, alignment.AlignCenter, text)
            painter.restore()

    class OverlayWindow(QWidget):
        def __init__(self) -> None:
            flags = (
                window_type.Tool
                | window_type.FramelessWindowHint
                | window_type.WindowStaysOnTopHint
            )
            transparent_input = getattr(window_type, "WindowTransparentForInput", 0)
            if transparent_input:
                flags |= transparent_input
            super().__init__(None, flags)
            self._dismissed_instances: dict[str, str] = {}
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self._raw_items: list[Mapping[str, object]] = []
            self._previous_visible_items: list[Mapping[str, object]] = []
            self._command_path = _work_overlay_command_path(path)
            self._item_limit = normalize_work_overlay_max_items(item_limit, item_limit)
            self._theme_tokens = dict(DEFAULT_WORK_OVERLAY_THEME)
            self._close_windows: list[CloseButtonWindow] = []
            self._workdir_windows: list[WorkdirLinkWindow] = []
            self._completed_check_windows: list[ClickHotspotWindow] = []
            self._close_anchors: list[tuple[QWidget, Mapping[str, object], str, str, str]] = []
            self._workdir_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._completed_check_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._card_hover_anchors: list[QWidget] = []
            self._completed_hover_anchors: list[QWidget] = []
            self._item_widgets: list[dict[str, Any]] = []
            self.circles: list[dict[str, Any]] = []
            self.rects: list[dict[str, Any]] = []
            self._empty_since = 0.0
            self._state_read_failed_at = 0.0
            self._last_runtime_error_signature = ""
            self._last_runtime_error_at = 0.0
            self._layout_width = WORK_OVERLAY_WIDTH
            self._layout_items: list[Mapping[str, object]] = []
            self._transition_in_progress = False
            self._transition_type = ""
            self._transition_item_id = ""
            self._transition_started_at = 0.0
            self._transition_required_height = 0
            self._transition_card_widget: QWidget | None = None
            self._transition_badge_widget: QWidget | None = None
            self._transition_annihilation_widget: QWidget | None = None
            self._transition_animation_group: Any | None = None
            self._transition_source_effect: QGraphicsOpacityEffect | None = None
            self._transition_source_widget: QWidget | None = None
            self._transition_hidden_widget: QWidget | None = None
            self._settled_completed_intro_ids: set[str] = set()
            self._switch_pending_key = ""
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed_at = 0.0
            self._transition_watchdog = QTimer(self)
            self._transition_watchdog.setSingleShot(True)
            self._transition_watchdog.timeout.connect(self._handle_transition_timeout)
            self._switch_pending_timer = QTimer(self)
            self._switch_pending_timer.timeout.connect(self._tick_switch_pending)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setWindowOpacity(overlay_alpha)

            self._shell = QWidget(self)
            self._shell.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self._shell.setGeometry(0, 0, WORK_OVERLAY_WIDTH, 1)

        def _wrapped_label_height(self, label: QLabel, width: int) -> int:
            return max(label.sizeHint().height(), label.heightForWidth(width), label.minimumSizeHint().height())

        def dismiss_item(self, item: Mapping[str, object]) -> None:
            item_id = str(item.get("id") or "")
            if (
                item_id
                and _item_is_completed(item)
                and not self._transition_in_progress
                and self._record_widget_for_kind(item_id, "completed") is not None
            ):
                self._start_completed_dismiss_transition(dict(item))
                return
            if item_id:
                _mark_item_dismissed(self._dismissed_instances, item)
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self.render_items(self._raw_items)

        def _switch_pending_active_for_item(self, item: Mapping[str, object]) -> bool:
            if not self._switch_pending_key or self._switch_pending_started_at <= 0.0:
                return False
            if _switch_item_key(item) != self._switch_pending_key:
                return False
            if self._switch_pending_completed_at > 0.0:
                return (
                    time.monotonic() - self._switch_pending_completed_at
                    <= WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                )
            return (
                time.monotonic() - self._switch_pending_started_at
                <= WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS
            )

        def _switch_pending_completed_for_item(self, item: Mapping[str, object]) -> bool:
            if self._switch_pending_completed_at <= 0.0:
                return False
            if _switch_item_key(item) != self._switch_pending_key:
                return False
            return (
                time.monotonic() - self._switch_pending_completed_at
                <= WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
            )

        def _set_switch_pending(self, item: Mapping[str, object]) -> None:
            key = _switch_item_key(item)
            if not key:
                return
            self._switch_pending_key = key
            self._switch_pending_started_at = time.monotonic()
            self._switch_pending_completed_at = 0.0
            if not self._switch_pending_timer.isActive():
                self._switch_pending_timer.start(WORK_OVERLAY_SWITCH_PENDING_TIMER_MS)
            self._sync_completed_pending_animations()
            self.reposition_interactive_windows()

        def _clear_switch_pending(self) -> None:
            self._switch_pending_key = ""
            self._switch_pending_started_at = 0.0
            self._switch_pending_completed_at = 0.0
            self._switch_pending_timer.stop()
            self._sync_completed_pending_animations()
            for window in self._workdir_windows:
                window.update()

        def _complete_switch_pending(self) -> None:
            if not self._switch_pending_key:
                return
            if self._switch_pending_completed_at <= 0.0:
                self._switch_pending_completed_at = time.monotonic()
            if not self._switch_pending_timer.isActive():
                self._switch_pending_timer.start(WORK_OVERLAY_SWITCH_PENDING_TIMER_MS)
            self._sync_completed_pending_animations()
            self.reposition_interactive_windows()

        def _sync_switch_pending(self, items: Sequence[Mapping[str, object]]) -> None:
            if not self._switch_pending_key:
                return
            if self._switch_pending_completed_at > 0.0:
                if (
                    time.monotonic() - self._switch_pending_completed_at
                    > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                ):
                    self._clear_switch_pending()
                else:
                    self._sync_completed_pending_animations()
                return
            elapsed = time.monotonic() - self._switch_pending_started_at
            if elapsed > WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS:
                self._clear_switch_pending()
                return
            pending_items = [
                item
                for item in items
                if _switch_item_key(item) == self._switch_pending_key
            ]
            if not pending_items:
                self._clear_switch_pending()
                return
            if any(bool(item.get("current")) for item in pending_items):
                self._complete_switch_pending()

        def _tick_switch_pending(self) -> None:
            if not self._switch_pending_key:
                self._switch_pending_timer.stop()
                return
            if self._switch_pending_completed_at > 0.0:
                if (
                    time.monotonic() - self._switch_pending_completed_at
                    > WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS
                ):
                    self._clear_switch_pending()
                    return
                for window in self._workdir_windows:
                    window.update()
                self._sync_completed_pending_animations()
                return
            if (
                time.monotonic() - self._switch_pending_started_at
                > WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS
            ):
                self._clear_switch_pending()
                return
            for window in self._workdir_windows:
                window.update()
            self._sync_completed_pending_animations()

        def _sync_completed_pending_animations(self) -> None:
            for record in self._item_widgets:
                item = record.get("item")
                pending = (
                    isinstance(item, Mapping)
                    and self._switch_pending_active_for_item(item)
                )
                completed = (
                    isinstance(item, Mapping)
                    and self._switch_pending_completed_for_item(item)
                )
                if record.get("kind") == "completed":
                    badge = record.get("badge")
                    if isinstance(badge, CompletedBadgeWidget):
                        badge.set_switch_pending(
                            pending,
                            self._switch_pending_started_at if pending else 0.0,
                            completed=completed,
                            completed_at=self._switch_pending_completed_at if completed else 0.0,
                        )
                    continue
                if record.get("kind") == "card":
                    switch_overlay = record.get("switch_overlay")
                    card = record.get("card")
                    if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                        if isinstance(card, QWidget):
                            switch_overlay.setGeometry(card.rect())
                        switch_overlay.set_switch_pending(
                            pending,
                            self._switch_pending_started_at if pending else 0.0,
                            completed=completed,
                            completed_at=self._switch_pending_completed_at if completed else 0.0,
                        )
                        if pending:
                            switch_overlay.raise_()

        def switch_item(self, item: Mapping[str, object]) -> None:
            if _item_is_cli(item):
                return
            session_id = str(item.get("sessionId") or item.get("id") or "").strip()
            target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
            if not session_id and not target_title:
                return
            payload = {
                "action": "activateSession",
                "sessionId": session_id,
                "targetTitle": target_title,
                "title": str(item.get("title") or "").strip(),
                "workdir": str(item.get("workdir") or "").strip(),
                "clientKind": str(item.get("clientKind") or "unknown").strip().lower(),
                "requestedAt": time.time(),
                "current": bool(item.get("current")),
            }
            try:
                self._command_path.parent.mkdir(parents=True, exist_ok=True)
                with self._command_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                return
            self._set_switch_pending(item)

        def emit_runtime_error(
            self,
            *,
            code: str,
            message: str,
            severity: str = "error",
            context: Mapping[str, object] | None = None,
        ) -> None:
            payload = {
                "action": "runtimeError",
                "source": "work_overlay_helper",
                "code": str(code or "helper_error"),
                "message": str(message or "Desktop work overlay helper error."),
                "severity": str(severity or "error"),
                "context": dict(context or {}),
                "reportedAt": time.time(),
            }
            signature = json.dumps(
                {
                    "code": payload["code"],
                    "message": payload["message"],
                    "severity": payload["severity"],
                    "context": payload["context"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            now = time.monotonic()
            if (
                signature == self._last_runtime_error_signature
                and (now - self._last_runtime_error_at) < 1.0
            ):
                return
            self._last_runtime_error_signature = signature
            self._last_runtime_error_at = now
            command_path = self._command_path or _work_overlay_command_path(path)
            try:
                command_path.parent.mkdir(parents=True, exist_ok=True)
                with command_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                return

        def hide_overlay(self) -> None:
            self.hide()
            for close_window in self._close_windows:
                close_window.hide()
            for workdir_window in self._workdir_windows:
                workdir_window.hide()
            for check_window in self._completed_check_windows:
                check_window.hide()

        def shutdown(self) -> None:
            self.hide_overlay()
            for close_window in self._close_windows:
                close_window.close()
            self._close_windows.clear()
            for workdir_window in self._workdir_windows:
                workdir_window.close()
            self._workdir_windows.clear()
            for check_window in self._completed_check_windows:
                check_window.close()
            self._completed_check_windows.clear()
            self.close()
            app.quit()

        def poll_state(self) -> None:
            state = read_state()
            if state is None:
                now = time.time()
                if owner_pid is not None and not process_exists(owner_pid):
                    self.shutdown()
                    return
                if self._state_read_failed_at <= 0.0:
                    self._state_read_failed_at = now
                    return
                if (
                    now - self._state_read_failed_at
                ) < WORK_OVERLAY_STATE_READ_FAILURE_GRACE_SECONDS:
                    return
                self.emit_runtime_error(
                    code="state_read_failed",
                    message="Desktop work overlay helper could not read state file.",
                    context={"stateFile": str(path)},
                )
                self.shutdown()
                return
            self._state_read_failed_at = 0.0
            should_close = bool(state.get("close"))
            updated_at = float(state.get("updatedAt") or 0.0)
            file_stale = updated_at > 0 and (time.time() - updated_at) > stale_seconds
            if owner_pid is not None and not process_exists(owner_pid):
                self.shutdown()
                return
            if should_close or file_stale:
                self.shutdown()
                return
            raw_items = state.get("items") or []
            items = [item for item in raw_items if isinstance(item, Mapping)]
            command_path_text = str(state.get("commandPath") or "").strip()
            self._command_path = (
                Path(command_path_text).expanduser()
                if command_path_text
                else _work_overlay_command_path(path)
            )
            theme_payload = state.get("theme")
            self._theme_tokens = _resolved_overlay_theme(
                theme_payload if isinstance(theme_payload, Mapping) else None
            )
            screen = app.primaryScreen()
            screen_height = (
                screen.availableGeometry().height()
                if screen is not None
                else self.geometry().height()
            )
            self._item_limit = normalize_work_overlay_max_items(
                state.get("itemLimit"),
                item_limit,
                max_items=work_overlay_max_items_for_screen_height(screen_height),
            )
            self.render_items(items)

        def sync_pointer_state(self) -> None:
            if not self.isVisible():
                target = overlay_alpha
            else:
                cursor_pos = QCursor.pos()
                inside_overlay = _overlay_hover_hit_test(
                    cursor_pos.x(),
                    cursor_pos.y(),
                    rects=[
                        bounds
                        for anchor in self._card_hover_anchors
                        if (bounds := self._widget_global_bounds(anchor)) is not None
                    ],
                    circle_rects=[
                        bounds
                        for anchor in self._completed_hover_anchors
                        if (bounds := self._widget_global_bounds(anchor)) is not None
                    ],
                )
                target = (
                    hover_alpha
                    if inside_overlay
                    else overlay_alpha
                )
            if abs(self.windowOpacity() - target) < 0.01:
                return
            self.setWindowOpacity(target)
            for close_window in self._close_windows:
                close_window.setWindowOpacity(target)
            for workdir_window in self._workdir_windows:
                workdir_window.setWindowOpacity(target)
            for check_window in self._completed_check_windows:
                check_window.setWindowOpacity(target)

        @staticmethod
        def _item_identity(item: Mapping[str, object], index: int) -> str:
            item_id = str(item.get("id") or "").strip()
            return item_id or f"overlay-index-{index}"

        @staticmethod
        def _item_widget_kind(item: Mapping[str, object]) -> str:
            return "completed" if _item_is_completed(item) else "card"

        @staticmethod
        def _widget_global_bounds(widget: QWidget) -> tuple[int, int, int, int] | None:
            if not widget.isVisible():
                return None
            top_left = widget.mapToGlobal(QPoint(0, 0))
            return (
                top_left.x(),
                top_left.y(),
                max(1, widget.width()),
                max(1, widget.height()),
            )

        def _clear_shell(self) -> None:
            self._close_anchors.clear()
            self._workdir_anchors.clear()
            self._completed_check_anchors.clear()
            self._card_hover_anchors.clear()
            self._completed_hover_anchors.clear()
            self._item_widgets.clear()
            self.circles.clear()
            self.rects.clear()
            for child in list(self._shell.findChildren(QWidget)):
                if child.parent() is self._shell:
                    child.deleteLater()

        def _build_item_widget(self, item: Mapping[str, object]) -> None:
            self._build_item_card(item)

        def _update_item_widget(self, record: dict[str, Any], item: Mapping[str, object]) -> None:
            if record.get("kind") == "completed":
                self._update_completed_badge(record, item)
            else:
                self._update_item_card(record, item)

        def _build_completed_row(
            self,
            completed_items: Sequence[Mapping[str, object]],
        ) -> None:
            for item in completed_items:
                self._build_completed_badge(item)

        def _build_completed_badge(
            self,
            item: Mapping[str, object],
        ) -> None:
            item_id = _item_id(item)
            animate_intro = item_id not in self._settled_completed_intro_ids
            badge = CompletedBadgeWidget(
                item,
                self._shell,
                animate_intro=animate_intro,
                theme_tokens=self._theme_tokens,
            )
            if item_id:
                self._settled_completed_intro_ids.add(item_id)
            hover_anchor = QWidget(badge)
            hover_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            hover_anchor.setFixedSize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
            )
            hover_anchor.move(0, 0)
            hover_anchor.show()
            workdir_anchor = QWidget(badge)
            workdir_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            workdir_anchor.setFixedSize(WORK_OVERLAY_COMPLETED_BADGE_SIZE - 24, 40)
            workdir_anchor.move(12, 124)
            workdir_anchor.show()
            check_anchor = QWidget(badge)
            check_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            check_anchor.setFixedSize(68, 56)
            check_anchor.move(50, 18)
            check_anchor.show()
            record = {
                "kind": "completed",
                "item_id": item_id,
                "badge": badge,
                "hover_anchor": hover_anchor,
                "workdir_anchor": workdir_anchor,
                "check_anchor": check_anchor,
            }
            self._item_widgets.append(record)
            self._update_completed_badge(record, item)

        def _update_completed_badge(
            self,
            record: dict[str, Any],
            item: Mapping[str, object],
        ) -> None:
            badge = record["badge"]
            record["item"] = dict(item)
            badge.set_theme_tokens(self._theme_tokens)
            badge.set_item(item)
            pending = self._switch_pending_active_for_item(item)
            completed = self._switch_pending_completed_for_item(item)
            badge.set_switch_pending(
                pending,
                self._switch_pending_started_at if pending else 0.0,
                completed=completed,
                completed_at=self._switch_pending_completed_at if completed else 0.0,
            )
            self._completed_hover_anchors.append(record["hover_anchor"])
            workdir_text = _workdir_display_name(item)
            if workdir_text and _workdir_clickable_for_item(item):
                self._workdir_anchors.append((record["workdir_anchor"], dict(item)))
            self._completed_check_anchors.append((record["check_anchor"], dict(item)))

        def _build_item_card(self, item: Mapping[str, object]) -> None:
            card = QFrame(self._shell)
            card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            card.setFixedWidth(WORK_OVERLAY_WIDTH)
            card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(
                WORK_OVERLAY_CARD_X_PADDING,
                WORK_OVERLAY_CARD_Y_PADDING,
                WORK_OVERLAY_CARD_X_PADDING,
                WORK_OVERLAY_CARD_Y_PADDING,
            )
            card_layout.setSpacing(WORK_OVERLAY_CARD_SPACING)

            head_layout = QHBoxLayout()
            head_layout.setContentsMargins(0, 0, 0, 0)
            head_layout.setSpacing(8)

            header = QLabel("", card)
            header.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            header.setWordWrap(False)
            header.setTextFormat(text_format.PlainText)
            header.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
            header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            head_layout.addWidget(header, 1)

            close_anchor = QWidget(card)
            close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
            head_layout.addWidget(close_anchor, 0)
            card_layout.addLayout(head_layout)

            detail = QLabel("", card)
            detail.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            detail.setWordWrap(True)
            detail.setTextFormat(text_format.PlainText)
            detail.setAlignment(alignment.AlignTop | alignment.AlignLeft)
            detail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            detail.setFont(QFont("Microsoft YaHei UI", 8))
            detail.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
            card_layout.addWidget(detail)

            footer_container = QWidget(card)
            footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            footer_layout = QHBoxLayout(footer_container)
            footer_layout.setContentsMargins(0, 0, 0, 0)
            footer_layout.setSpacing(8)

            status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
            status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
            status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            status_label.setMinimumWidth(1)
            status_label.setFixedHeight(status_label.fontMetrics().height() + 4)
            footer_layout.addWidget(status_label, 1)

            round_badge = QLabel("", footer_container)
            round_badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            round_badge.setTextFormat(text_format.PlainText)
            round_badge.setAlignment(alignment.AlignCenter)
            round_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            round_badge.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
            round_badge.setFixedHeight(18)
            round_badge.setVisible(False)

            workdir_label = QLabel("", footer_container)
            workdir_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            workdir_label.setWordWrap(False)
            workdir_label.setTextFormat(text_format.PlainText)
            workdir_label.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
            workdir_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
            workdir_label.setMaximumWidth(170)
            workdir_label.setFixedHeight(workdir_label.fontMetrics().height() + 4)
            workdir_label.setStyleSheet(
                "QLabel {"
                "color: #5E6A78;"
                "border: none;"
                "background: transparent;"
                "}"
            )
            footer_layout.addWidget(workdir_label, 0)
            footer_layout.addWidget(round_badge, 0)

            card_layout.addWidget(footer_container)
            switch_overlay = CardSwitchPendingOverlayWidget(card)

            record = {
                "kind": "card",
                "item_id": _item_id(item),
                "card": card,
                "header": header,
                "detail": detail,
                "footer_container": footer_container,
                "status_label": status_label,
                "round_badge": round_badge,
                "workdir_label": workdir_label,
                "switch_overlay": switch_overlay,
                "close_anchor": close_anchor,
            }
            self._item_widgets.append(record)
            self._update_item_card(record, item)

        def _update_item_card(
            self,
            record: dict[str, Any],
            item: Mapping[str, object],
            *,
            collect_anchors: bool = True,
        ) -> None:
            record["item"] = dict(item)
            status = str(item.get("status") or "")
            accent, pill_bg, card_bg, border_color = _color_for(
                status,
                self._theme_tokens,
            )
            theme = self._theme_tokens
            elapsed_text = str(item.get("elapsedText") or "").strip() or "已处理 --"
            header_text = _work_overlay_header_text(
                str(item.get("startedAt") or ""),
                elapsed_text,
                str(item.get("title") or "Codex 工作"),
                title_limit=header_title_limit,
            )

            card = record["card"]
            card.setStyleSheet(
                "QFrame {"
                f"background-color: {card_bg};"
                f"border: 1px solid {border_color};"
                "border-radius: 10px;"
                "}"
            )

            header = record["header"]
            header.setText(header_text)
            header.setStyleSheet(
                "QLabel {"
                f"color: {accent if status == 'recent' else _theme_mix(theme['text'], theme['muted'], 0.36, fallback=theme['text'])};"
                "border: none;"
                "background: transparent;"
                "}"
            )

            round_badge = record["round_badge"]
            round_index = max(0, int(item.get("roundIndex") or 0))
            badge_visible = status != "recent" and round_index > 0
            if badge_visible:
                round_badge_theme = _round_badge_palette(status, theme)
                round_badge.setText(str(round_index))
                round_badge.setToolTip(f"当前第 {round_index} 轮")
                round_badge.setStyleSheet(
                    "QLabel {"
                    f"color: {round_badge_theme['text']};"
                    f"background-color: {round_badge_theme['background']};"
                    f"border: 1px solid {round_badge_theme['border']};"
                    "border-radius: 9px;"
                    "padding: 0 6px;"
                    "}"
                )
                round_badge.adjustSize()
                round_badge.setFixedWidth(max(18, round_badge.sizeHint().width()))
                round_badge.setVisible(True)
            else:
                round_badge.setText("")
                round_badge.setToolTip("")
                round_badge.setFixedWidth(18)
                round_badge.setVisible(False)

            body_text = str(item.get("lastText") or item.get("detail") or "").strip()
            detail = record["detail"]
            detail_text = _multiline_elided_text(
                body_text,
                font=detail.font(),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
            )
            detail.setText(detail_text)
            detail.setToolTip(body_text if detail_text and detail_text != body_text else "")
            detail.setFixedHeight(self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH))
            detail.setStyleSheet(
                "QLabel {"
                f"color: {theme['requestText']};"
                "border: none;"
                "background: transparent;"
                "}"
            )

            workdir_text = _workdir_display_name(item)
            full_workdir = str(item.get("workdir") or "").strip()
            workdir_clickable = _workdir_clickable_for_item(item)
            status_text = str(item.get("statusText") or item.get("statusLabel") or "").strip()
            footer_container = record["footer_container"]
            footer_container.setVisible(bool(status_text or workdir_text))

            status_label = record["status_label"]
            if status_text:
                status_text_color = accent if status in {"recent", "error"} else theme["muted"]
                footer_status_text = _compact_work_text(
                    status_text,
                    48 if workdir_text else 80,
                )
                status_label.setText(footer_status_text)
                status_label.setBaseColor(status_text_color)
                status_label.setShimmerEnabled(status != "recent")
                status_label.setVisible(True)
            else:
                status_label.setText("")
                status_label.setShimmerEnabled(False)
                status_label.setVisible(False)

            workdir_label = record["workdir_label"]
            if workdir_text:
                workdir_label.setText(_compact_workdir_text(workdir_text, 40))
                workdir_label.setToolTip((full_workdir or workdir_text) if workdir_clickable else "")
                workdir_label.setStyleSheet(
                    "QLabel {"
                    f"color: {theme['info'] if workdir_clickable else theme['requestMuted']};"
                    "border: none;"
                    "background: transparent;"
                    "}"
                )
                workdir_label.setVisible(True)
            else:
                workdir_label.setText("")
                workdir_label.setToolTip("")
                workdir_label.setVisible(False)

            if collect_anchors:
                self._card_hover_anchors.append(card)
                self._close_anchors.append(
                    (record["close_anchor"], dict(item), card_bg, pill_bg, accent)
                )
                if workdir_clickable:
                    self._workdir_anchors.append((record["workdir_label"], dict(item)))
            switch_overlay = record.get("switch_overlay")
            if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                pending = self._switch_pending_active_for_item(item)
                completed = self._switch_pending_completed_for_item(item)
                switch_overlay.setGeometry(card.rect())
                switch_overlay.set_switch_pending(
                    pending,
                    self._switch_pending_started_at if pending else 0.0,
                    completed=completed,
                    completed_at=self._switch_pending_completed_at if completed else 0.0,
                )
                if pending:
                    switch_overlay.raise_()

        def _build_transition_card_widget(self, item: Mapping[str, object]) -> QFrame:
            card = QFrame(self._shell)
            card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            card.setMinimumSize(1, 1)
            card.setMaximumSize(16777215, 16777215)
            card.resize(WORK_OVERLAY_WIDTH, WORK_OVERLAY_TRANSITION_CARD_HEIGHT)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(
                WORK_OVERLAY_CARD_X_PADDING,
                WORK_OVERLAY_CARD_Y_PADDING,
                WORK_OVERLAY_CARD_X_PADDING,
                WORK_OVERLAY_CARD_Y_PADDING,
            )
            card_layout.setSpacing(WORK_OVERLAY_CARD_SPACING)

            head_layout = QHBoxLayout()
            head_layout.setContentsMargins(0, 0, 0, 0)
            head_layout.setSpacing(8)

            header = QLabel("", card)
            header.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            header.setWordWrap(False)
            header.setTextFormat(text_format.PlainText)
            header.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
            header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            head_layout.addWidget(header, 1)

            close_anchor = QWidget(card)
            close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
            head_layout.addWidget(close_anchor, 0)
            card_layout.addLayout(head_layout)

            detail = QLabel("", card)
            detail.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            detail.setWordWrap(True)
            detail.setTextFormat(text_format.PlainText)
            detail.setAlignment(alignment.AlignTop | alignment.AlignLeft)
            detail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            detail.setFont(QFont("Microsoft YaHei UI", 8))
            detail.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
            card_layout.addWidget(detail)

            footer_container = QWidget(card)
            footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            footer_layout = QHBoxLayout(footer_container)
            footer_layout.setContentsMargins(0, 0, 0, 0)
            footer_layout.setSpacing(8)

            status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
            status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
            status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            status_label.setMinimumWidth(1)
            status_label.setFixedHeight(status_label.fontMetrics().height() + 4)
            footer_layout.addWidget(status_label, 1)

            round_badge = QLabel("", footer_container)
            round_badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            round_badge.setTextFormat(text_format.PlainText)
            round_badge.setAlignment(alignment.AlignCenter)
            round_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            round_badge.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
            round_badge.setFixedHeight(18)
            round_badge.setVisible(False)

            workdir_label = QLabel("", footer_container)
            workdir_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            workdir_label.setWordWrap(False)
            workdir_label.setTextFormat(text_format.PlainText)
            workdir_label.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
            workdir_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
            workdir_label.setMaximumWidth(170)
            workdir_label.setFixedHeight(workdir_label.fontMetrics().height() + 4)
            workdir_label.setStyleSheet(
                "QLabel {"
                "color: #5E6A78;"
                "border: none;"
                "background: transparent;"
                "}"
            )
            footer_layout.addWidget(workdir_label, 0)
            footer_layout.addWidget(round_badge, 0)
            card_layout.addWidget(footer_container)

            record = {
                "kind": "card",
                "item_id": _item_id(item),
                "card": card,
                "header": header,
                "detail": detail,
                "footer_container": footer_container,
                "status_label": status_label,
                "round_badge": round_badge,
                "workdir_label": workdir_label,
                "close_anchor": close_anchor,
            }
            self._update_item_card(record, item, collect_anchors=False)
            return card

        def _widget_shell_rect(self, widget: QWidget) -> QRectF:
            top_left = widget.mapTo(self._shell, QPoint(0, 0))
            return QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(max(1, widget.width())),
                float(max(1, widget.height())),
            )

        def _record_for_item_kind(
            self,
            item_id: str,
            kind: str,
        ) -> dict[str, Any] | None:
            for record in self._item_widgets:
                if record.get("kind") == kind and str(record.get("item_id") or "") == item_id:
                    return record
            return None

        def _record_visual_widget(self, record: Mapping[str, Any]) -> QWidget | None:
            widget = record.get("badge") if record.get("kind") == "completed" else record.get("card")
            return widget if isinstance(widget, QWidget) else None

        def _record_widget_for_kind(
            self,
            item_id: str,
            kind: str,
        ) -> QWidget | None:
            record = self._record_for_item_kind(item_id, kind)
            return self._record_visual_widget(record) if record is not None else None

        def _sync_record_arrays(
            self,
            visible_items: Sequence[Mapping[str, object]],
        ) -> None:
            unused_records = list(self._item_widgets)
            self.circles = []
            self.rects = []
            for item in visible_items:
                item_id = _item_id(item)
                kind = self._item_widget_kind(item)
                record = next(
                    (
                        candidate
                        for candidate in unused_records
                        if str(candidate.get("kind") or "") == kind
                        and (
                            not item_id
                            or str(candidate.get("item_id") or "") == item_id
                        )
                    ),
                    None,
                )
                if record is None:
                    continue
                unused_records.remove(record)
                if kind == "completed":
                    self.circles.append(record)
                else:
                    self.rects.append(record)

        def _completed_record_geometry(
            self,
            index_from_right: int,
            completed_count: int,
        ) -> QRect:
            return self._completed_badge_geometry_for_slot(
                _completed_slot_rect(
                    index_from_right,
                    completed_count,
                    layout_width=self._layout_width,
                )
            )

        def _card_record_geometry(
            self,
            index_from_top: int,
            completed_count: int,
        ) -> QRect:
            return self._card_geometry_for_slot(
                _card_slot_rect(
                    index_from_top,
                    completed_count,
                    layout_width=self._layout_width,
                )
            )

        def _qrect_from_rectf(self, rect: QRectF) -> QRect:
            return QRect(
                int(round(rect.x())),
                int(round(rect.y())),
                max(1, int(round(rect.width()))),
                max(1, int(round(rect.height()))),
            )

        def _badge_geometry_for_slot(
            self,
            widget: QWidget,
            slot_rect: OverlayRect,
        ) -> QRect:
            return QRect(
                int(round(slot_rect[0])),
                widget.y(),
                max(1, widget.width()),
                max(1, widget.height()),
            )

        def _completed_badge_geometry_for_slot(self, slot_rect: OverlayRect) -> QRect:
            return QRect(
                int(round(slot_rect[0])),
                int(round(slot_rect[1])),
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )

        def _card_geometry_for_slot(self, slot_rect: OverlayRect) -> QRect:
            return QRect(
                int(round(slot_rect[0])),
                int(round(slot_rect[1])),
                WORK_OVERLAY_WIDTH,
                WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
            )

        def _sync_item_widget_geometries(
            self,
            visible_items: Sequence[Mapping[str, object]],
        ) -> None:
            self._sync_record_arrays(visible_items)
            completed_count = len(self.circles)
            for list_idx, record in enumerate(self.circles):
                widget = self._record_visual_widget(record)
                if widget is None:
                    continue
                index_from_right = completed_count - 1 - list_idx
                widget.setGeometry(
                    self._completed_record_geometry(index_from_right, completed_count)
                )
                widget.show()

            for index_from_top, record in enumerate(self.rects):
                widget = self._record_visual_widget(record)
                if widget is None:
                    continue
                widget.setGeometry(
                    self._card_record_geometry(index_from_top, completed_count)
                )
                switch_overlay = record.get("switch_overlay")
                if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                    switch_overlay.setGeometry(widget.rect())
                    if switch_overlay.isVisible():
                        switch_overlay.raise_()
                widget.show()

        def _animate_widget_geometry(
            self,
            widget: QWidget,
            target: QRect,
            duration_ms: int,
            easing: QEasingCurve.Type,
        ) -> QPropertyAnimation:
            animation = QPropertyAnimation(widget, b"geometry")
            animation.setStartValue(widget.geometry())
            animation.setEndValue(target)
            animation.setDuration(duration_ms)
            animation.setEasingCurve(easing)
            return animation

        def _animate_effect_opacity(
            self,
            effect: QGraphicsOpacityEffect,
            start: float,
            end: float,
            duration_ms: int,
        ) -> QPropertyAnimation:
            animation = QPropertyAnimation(effect, b"opacity")
            animation.setStartValue(float(start))
            animation.setEndValue(float(end))
            animation.setDuration(duration_ms)
            animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
            return animation

        def _build_annihilation_widget(self, source_widget: QWidget) -> EnergyRingAnnihilationWidget:
            source_top_left = source_widget.mapToGlobal(QPoint(0, 0))
            circle_size = float(
                max(
                    1,
                    min(
                        source_widget.width(),
                        source_widget.height(),
                        WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                    ),
                )
            )
            max_radius = circle_size / 2.0 + float(WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN)
            side = int(math.ceil(max_radius * 2.0 + 20.0))
            center_global = QPointF(
                float(source_top_left.x()) + circle_size / 2.0,
                float(source_top_left.y()) + circle_size / 2.0,
            )
            x = int(round(center_global.x() - side / 2.0))
            y = int(round(center_global.y() - side / 2.0))
            screen = app.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                if side <= available.width():
                    x = max(available.left(), min(x, available.right() - side + 1))
                if side <= available.height():
                    y = max(available.top(), min(y, available.bottom() - side + 1))
            center = QPointF(center_global.x() - float(x), center_global.y() - float(y))
            widget = EnergyRingAnnihilationWidget(max_radius=max_radius, center=center)
            widget.setGeometry(x, y, side, side)
            widget.setWindowOpacity(self.windowOpacity())
            widget.show()
            widget.raise_()
            return widget

        def _build_annihilation_animation(
            self,
            widget: EnergyRingAnnihilationWidget,
        ) -> QParallelAnimationGroup:
            duration_ms = WORK_OVERLAY_COMPLETED_ANNIHILATION_MS
            group = QParallelAnimationGroup(self)
            animation = QPropertyAnimation(widget, b"progress")
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setDuration(duration_ms)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(animation)
            return group

        def _hide_transition_interactive_windows(self) -> None:
            for window in [
                *self._close_windows,
                *self._workdir_windows,
                *self._completed_check_windows,
            ]:
                window.hide()

        def _transition_item_payload(
            self,
            item_id: str,
            old_items: Sequence[Mapping[str, object]],
            new_items: Sequence[Mapping[str, object]],
        ) -> Mapping[str, object]:
            for items in (new_items, old_items):
                for item in items:
                    if _item_id(item) == item_id:
                        return item
            return {}

        def _add_noop_animation(
            self,
            group: QParallelAnimationGroup,
            duration_ms: int,
        ) -> None:
            group.addAnimation(QPauseAnimation(max(1, int(duration_ms)), self))

        def _start_transition_watchdog(self, duration_ms: int) -> None:
            self._transition_watchdog.start(max(500, int(duration_ms) + 900))

        def _touch_transition_watchdog(self, duration_ms: int) -> None:
            if self._transition_in_progress:
                self._start_transition_watchdog(duration_ms)

        def _stop_transition_animation_group(self) -> None:
            group = self._transition_animation_group
            if group is None:
                return
            try:
                if group.state() != QAbstractAnimation.State.Stopped:
                    group.stop()
                group.deleteLater()
            except RuntimeError:
                pass
            self._transition_animation_group = None

        def _handle_transition_timeout(self) -> None:
            if self._transition_in_progress:
                self._end_transition()

        def _transition_records_for_current_layout(
            self,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            circles = [record for record in self.circles if record.get("kind") == "completed"]
            rects = [record for record in self.rects if record.get("kind") == "card"]
            if circles or rects:
                return list(circles), list(rects)
            return (
                [record for record in self._item_widgets if record.get("kind") == "completed"],
                [record for record in self._item_widgets if record.get("kind") == "card"],
            )

        def _set_transition_height_for_counts(
            self,
            completed_count: int,
            card_count: int,
            extra_rects: Sequence[QRect] = (),
        ) -> None:
            bottoms = [
                float(
                    _overlay_required_height_for_counts(
                        completed_count,
                        card_count,
                        layout_width=self._layout_width,
                    )
                )
            ]
            bottoms.extend(float(rect.y() + rect.height()) for rect in extra_rects)
            self._transition_required_height = max(
                self._transition_required_height,
                max(1, int(math.ceil(max(bottoms)))),
            )
            self._sync_overlay_geometry()

        def _start_completed_dismiss_transition(
            self,
            item: Mapping[str, object],
        ) -> None:
            item_id = _item_id(item)
            clicked_record = self._record_for_item_kind(item_id, "completed")
            source_widget = (
                self._record_visual_widget(clicked_record)
                if clicked_record is not None
                else None
            )
            if not item_id or clicked_record is None or source_widget is None:
                _mark_item_dismissed(self._dismissed_instances, item)
                self._last_payload_signature = ""
                self._last_structure_signature = ""
                self.render_items(self._raw_items)
                return

            try:
                _mark_item_dismissed(self._dismissed_instances, item)
                self._transition_in_progress = True
                self._transition_type = "completed_dismiss"
                self._transition_item_id = item_id
                self._transition_started_at = time.monotonic()
                self._hide_transition_interactive_windows()
                self._transition_source_widget = source_widget
                self._transition_hidden_widget = source_widget

                effect = QGraphicsOpacityEffect(source_widget)
                effect.setOpacity(1.0)
                source_widget.setGraphicsEffect(effect)
                source_widget.show()
                source_widget.raise_()
                self._transition_source_effect = effect

                annihilation_widget = self._build_annihilation_widget(source_widget)
                self._transition_annihilation_widget = annihilation_widget

                duration_ms = WORK_OVERLAY_COMPLETED_ANNIHILATION_MS
                self._start_transition_watchdog(duration_ms)
                self._touch_transition_watchdog(duration_ms)
                group = QParallelAnimationGroup(self)
                group.addAnimation(
                    self._animate_effect_opacity(
                        effect,
                        1.0,
                        0.0,
                        duration_ms,
                    )
                )
                group.addAnimation(self._build_annihilation_animation(annihilation_widget))

                circles, rects = self._transition_records_for_current_layout()
                remaining_circles = [record for record in circles if record is not clicked_record]
                completed_count_after = len(remaining_circles)
                self._set_transition_height_for_counts(len(circles), len(rects))
                self._set_transition_height_for_counts(completed_count_after, len(rects))
                shift_delay_ms = max(0, duration_ms - WORK_OVERLAY_RESTORE_SHIFT_MS)
                for list_idx, record in enumerate(remaining_circles):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    index_from_right = completed_count_after - 1 - list_idx
                    sequence = QSequentialAnimationGroup(self)
                    if shift_delay_ms > 0:
                        sequence.addAnimation(QPauseAnimation(shift_delay_ms, self))
                    sequence.addAnimation(
                        self._animate_widget_geometry(
                            widget,
                            self._completed_record_geometry(
                                index_from_right,
                                completed_count_after,
                            ),
                            WORK_OVERLAY_RESTORE_SHIFT_MS,
                            QEasingCurve.Type.InOutQuad,
                        )
                    )
                    group.addAnimation(sequence)

                group.finished.connect(lambda: self._finish_completed_dismiss(item))
                self._transition_animation_group = group
                group.start()
            except Exception:
                self._end_transition()

        def _finish_completed_dismiss(self, item: Mapping[str, object]) -> None:
            _mark_item_dismissed(self._dismissed_instances, item)
            self._end_transition()

        def _start_completed_to_card_transition(
            self,
            item_id: str,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
        ) -> None:
            try:
                self._transition_in_progress = True
                self._transition_type = "completed_to_card"
                self._transition_item_id = item_id
                self._transition_started_at = time.monotonic()
                self._hide_transition_interactive_windows()

                circles, rects = self._transition_records_for_current_layout()
                clicked_record = self._record_for_item_kind(item_id, "completed")
                source_widget = (
                    self._record_visual_widget(clicked_record)
                    if clicked_record is not None
                    else None
                )
                if clicked_record is None or source_widget is None:
                    self._end_transition()
                    return

                staged_circles = list(circles)
                staged_circles.remove(clicked_record)
                staged_circles.append(clicked_record)
                self._transition_source_widget = source_widget
                self._transition_hidden_widget = source_widget
                effect = QGraphicsOpacityEffect(source_widget)
                effect.setOpacity(1.0)
                source_widget.setGraphicsEffect(effect)
                source_widget.show()
                source_widget.raise_()
                self._transition_source_effect = effect
                self._set_transition_height_for_counts(len(circles), len(rects))
                self._start_completed_to_card_phase1(
                    old_items,
                    new_items,
                    clicked_record,
                    staged_circles,
                    rects,
                )
            except Exception:
                self._end_transition()

        def _start_completed_to_card_phase1(
            self,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
            clicked_record: dict[str, Any],
            staged_circles: list[dict[str, Any]],
            rects: list[dict[str, Any]],
        ) -> None:
            try:
                duration_ms = max(
                    WORK_OVERLAY_RESTORE_FADE_OUT_MS,
                    WORK_OVERLAY_RESTORE_SHIFT_MS,
                )
                self._touch_transition_watchdog(duration_ms)
                phase1 = QParallelAnimationGroup(self)
                if self._transition_source_effect is not None:
                    phase1.addAnimation(
                        self._animate_effect_opacity(
                            self._transition_source_effect,
                            1.0,
                            0.0,
                            WORK_OVERLAY_RESTORE_FADE_OUT_MS,
                        )
                    )

                staged_count = len(staged_circles)
                for list_idx, record in enumerate(staged_circles[:-1]):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    index_from_right = staged_count - 1 - list_idx
                    phase1.addAnimation(
                        self._animate_widget_geometry(
                            widget,
                            self._completed_record_geometry(index_from_right, staged_count),
                            WORK_OVERLAY_RESTORE_SHIFT_MS,
                            QEasingCurve.Type.InOutQuad,
                        )
                    )
                if phase1.animationCount() == 0:
                    self._add_noop_animation(phase1, duration_ms)
                phase1.finished.connect(
                    lambda: self._start_completed_to_card_phase2(
                        old_items,
                        new_items,
                        clicked_record,
                        staged_circles,
                        rects,
                    )
                )
                self._transition_animation_group = phase1
                phase1.start()
            except Exception:
                self._end_transition()

        def _start_completed_to_card_phase2(
            self,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
            clicked_record: dict[str, Any],
            staged_circles: list[dict[str, Any]],
            rects: list[dict[str, Any]],
        ) -> None:
            try:
                source_widget = self._transition_source_widget
                if source_widget is None:
                    self._end_transition()
                    return
                duration_ms = WORK_OVERLAY_RESTORE_FADE_IN_MS
                self._touch_transition_watchdog(duration_ms)
                effect = self._transition_source_effect
                if effect is None:
                    effect = QGraphicsOpacityEffect(source_widget)
                    source_widget.setGraphicsEffect(effect)
                    self._transition_source_effect = effect
                effect.setOpacity(0.0)
                rightmost_rect = self._completed_record_geometry(0, len(staged_circles))
                source_widget.setGeometry(rightmost_rect)
                source_widget.show()
                source_widget.raise_()

                phase2 = QParallelAnimationGroup(self)
                phase2.addAnimation(
                    self._animate_effect_opacity(
                        effect,
                        0.0,
                        1.0,
                        duration_ms,
                    )
                )
                phase2.finished.connect(
                    lambda: self._start_completed_to_card_phase3(
                        old_items,
                        new_items,
                        clicked_record,
                        staged_circles,
                        rects,
                    )
                )
                self._transition_animation_group = phase2
                phase2.start()
            except Exception:
                self._end_transition()

        def _start_completed_to_card_phase3(
            self,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
            clicked_record: dict[str, Any],
            staged_circles: list[dict[str, Any]],
            rects: list[dict[str, Any]],
        ) -> None:
            try:
                duration_ms = WORK_OVERLAY_RESTORE_DESCEND_MS
                self._touch_transition_watchdog(duration_ms)
                phase3 = QParallelAnimationGroup(self)
                source_widget = self._transition_source_widget
                circle_count_after = max(0, len(staged_circles) - 1)
                target_card = self._card_record_geometry(0, circle_count_after)

                if source_widget is not None:
                    source_rect = source_widget.geometry()
                    source_widget.hide()
                    transition_card = self._build_transition_card_widget(
                        self._transition_item_payload(
                            str(clicked_record.get("item_id") or ""),
                            old_items,
                            new_items,
                        )
                    )
                    transition_card.setGeometry(source_rect)
                    transition_card.show()
                    transition_card.raise_()
                    self._transition_card_widget = transition_card
                    phase3.addAnimation(
                        self._animate_widget_geometry(
                            transition_card,
                            target_card,
                            duration_ms,
                            QEasingCurve.Type.OutBack,
                        )
                    )

                for index_from_top, record in enumerate(rects):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    phase3.addAnimation(
                        self._animate_widget_geometry(
                            widget,
                            self._card_record_geometry(index_from_top + 1, circle_count_after),
                            duration_ms,
                            QEasingCurve.Type.OutBack,
                        )
                    )

                for list_idx, record in enumerate(staged_circles[:-1]):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    index_from_right = circle_count_after - 1 - list_idx
                    phase3.addAnimation(
                        self._animate_widget_geometry(
                            widget,
                            self._completed_record_geometry(index_from_right, circle_count_after),
                            duration_ms,
                            QEasingCurve.Type.InOutQuad,
                        )
                    )

                self._set_transition_height_for_counts(
                    circle_count_after,
                    len(rects) + 1,
                    [target_card],
                )
                if phase3.animationCount() == 0:
                    self._add_noop_animation(phase3, duration_ms)
                phase3.finished.connect(self._end_transition)
                self._transition_animation_group = phase3
                phase3.start()
            except Exception:
                self._end_transition()

        def _build_transition_badge_widget(self, item: Mapping[str, object]) -> QWidget:
            badge = CompletedBadgeWidget(
                item,
                self._shell,
                animate_intro=False,
                theme_tokens=self._theme_tokens,
            )
            badge.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            badge.setMinimumSize(1, 1)
            badge.setMaximumSize(16777215, 16777215)
            badge.resize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )
            return badge

        def _card_yield_geometry_for_circle_path(
            self,
            widget: QWidget,
            circle_rect: QRect,
        ) -> QRect:
            card_rect = widget.geometry()
            target = _card_yield_rect_for_circle_path(
                (
                    float(card_rect.x()),
                    float(card_rect.y()),
                    float(card_rect.width()),
                    float(card_rect.height()),
                ),
                (
                    float(circle_rect.x()),
                    float(circle_rect.y()),
                    float(circle_rect.width()),
                    float(circle_rect.height()),
                ),
            )
            return self._card_geometry_for_slot(target)

        def _card_yield_delay_for_circle_path(
            self,
            widget: QWidget,
            source_circle: QRect,
            target_circle: QRect,
            duration_ms: int,
        ) -> int:
            card_rect = widget.geometry()
            return _card_yield_delay_ms(
                (
                    float(card_rect.x()),
                    float(card_rect.y()),
                    float(card_rect.width()),
                    float(card_rect.height()),
                ),
                (
                    float(source_circle.x()),
                    float(source_circle.y()),
                    float(source_circle.width()),
                    float(source_circle.height()),
                ),
                (
                    float(target_circle.x()),
                    float(target_circle.y()),
                    float(target_circle.width()),
                    float(target_circle.height()),
                ),
                duration_ms,
            )

        def _start_card_to_completed_transition(
            self,
            item_id: str,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
        ) -> None:
            try:
                self._transition_in_progress = True
                self._transition_type = "card_to_completed"
                self._transition_item_id = item_id
                self._transition_started_at = time.monotonic()
                self._hide_transition_interactive_windows()

                circles, rects = self._transition_records_for_current_layout()
                clicked_record = self._record_for_item_kind(item_id, "card")
                source_widget = (
                    self._record_visual_widget(clicked_record)
                    if clicked_record is not None
                    else None
                )
                if clicked_record is None or source_widget is None:
                    self._end_transition()
                    return

                clicked_idx = rects.index(clicked_record) if clicked_record in rects else -1
                next_rects = [record for record in rects if record is not clicked_record]
                next_circles = [*circles, clicked_record]
                circle_count_after = len(next_circles)
                source_rect = self._widget_shell_rect(source_widget)
                source_circle = self._qrect_from_rectf(
                    QRectF(
                        *_card_height_circle_rect_for_rect(
                            (
                                source_rect.x(),
                                source_rect.y(),
                                source_rect.width(),
                                source_rect.height(),
                            )
                        )
                    )
                )
                target_circle = self._completed_record_geometry(0, circle_count_after)
                self._set_transition_height_for_counts(
                    circle_count_after,
                    len(next_rects),
                    [source_circle, target_circle],
                )

                source_widget.hide()
                self._transition_hidden_widget = source_widget
                self._transition_source_widget = source_widget
                transition_card = self._build_transition_card_widget(
                    self._transition_item_payload(item_id, old_items, new_items)
                )
                transition_card.setGeometry(self._qrect_from_rectf(source_rect))
                transition_card.show()
                transition_card.raise_()
                self._transition_card_widget = transition_card
                transition_badge = self._build_transition_badge_widget(
                    self._transition_item_payload(item_id, old_items, new_items)
                )
                transition_badge.setGeometry(source_circle)
                badge_effect = QGraphicsOpacityEffect(transition_badge)
                badge_effect.setOpacity(0.0)
                transition_badge.setGraphicsEffect(badge_effect)
                transition_badge.show()
                transition_badge.raise_()
                self._transition_badge_widget = transition_badge

                self._start_card_to_completed_shape_phase(
                    circles,
                    rects,
                    clicked_record,
                    clicked_idx,
                    next_rects,
                    circle_count_after,
                    source_circle,
                    target_circle,
                    transition_card,
                    transition_badge,
                    badge_effect,
                )
            except Exception:
                self._end_transition()

        def _start_card_to_completed_shape_phase(
            self,
            circles: list[dict[str, Any]],
            rects: list[dict[str, Any]],
            clicked_record: dict[str, Any],
            clicked_idx: int,
            next_rects: list[dict[str, Any]],
            circle_count_after: int,
            source_circle: QRect,
            target_circle: QRect,
            transition_card: QWidget,
            transition_badge: QWidget,
            badge_effect: QGraphicsOpacityEffect,
        ) -> None:
            try:
                duration_ms = WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS
                self._touch_transition_watchdog(duration_ms)
                card_effect = QGraphicsOpacityEffect(transition_card)
                card_effect.setOpacity(1.0)
                transition_card.setGraphicsEffect(card_effect)

                phase = QParallelAnimationGroup(self)
                phase.addAnimation(
                    self._animate_widget_geometry(
                        transition_card,
                        source_circle,
                        WORK_OVERLAY_TRANSITION_SHRINK_MS,
                        QEasingCurve.Type.OutCubic,
                    )
                )
                phase.addAnimation(
                    self._animate_effect_opacity(
                        card_effect,
                        1.0,
                        0.0,
                        WORK_OVERLAY_TRANSITION_SHRINK_MS,
                    )
                )
                phase.addAnimation(
                    self._animate_effect_opacity(
                        badge_effect,
                        0.0,
                        1.0,
                        WORK_OVERLAY_TRANSITION_SHRINK_MS,
                    )
                )
                self._add_noop_animation(phase, duration_ms)
                phase.finished.connect(
                    lambda: self._start_card_to_completed_fly_phase(
                        circles,
                        rects,
                        clicked_record,
                        clicked_idx,
                        next_rects,
                        circle_count_after,
                        source_circle,
                        target_circle,
                        transition_card,
                        transition_badge,
                    )
                )
                self._transition_animation_group = phase
                phase.start()
            except Exception:
                self._end_transition()

        def _start_card_to_completed_fly_phase(
            self,
            circles: list[dict[str, Any]],
            rects: list[dict[str, Any]],
            clicked_record: dict[str, Any],
            clicked_idx: int,
            next_rects: list[dict[str, Any]],
            circle_count_after: int,
            source_circle: QRect,
            target_circle: QRect,
            transition_card: QWidget,
            transition_badge: QWidget,
        ) -> None:
            try:
                del clicked_record
                transition_card.hide()
                transition_badge.setGeometry(source_circle)
                transition_badge.show()
                transition_badge.raise_()
                effect = transition_badge.graphicsEffect()
                if isinstance(effect, QGraphicsOpacityEffect):
                    effect.setOpacity(1.0)

                duration_ms = WORK_OVERLAY_TRANSITION_MOVE_MS + WORK_OVERLAY_TRANSITION_SHIFT_MS
                self._touch_transition_watchdog(duration_ms)
                group = QParallelAnimationGroup(self)
                group.addAnimation(
                    self._animate_widget_geometry(
                        transition_badge,
                        target_circle,
                        duration_ms,
                        QEasingCurve.Type.InOutQuad,
                    )
                )

                for list_idx, record in enumerate(circles):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    index_from_right = circle_count_after - 1 - list_idx
                    group.addAnimation(
                        self._animate_widget_geometry(
                            widget,
                            self._completed_record_geometry(
                                index_from_right,
                                circle_count_after,
                            ),
                            duration_ms,
                            QEasingCurve.Type.InOutQuad,
                        )
                    )

                for index_from_top, record in enumerate(next_rects):
                    widget = self._record_visual_widget(record)
                    if widget is None:
                        continue
                    target = self._card_record_geometry(index_from_top, circle_count_after)
                    old_index = rects.index(record) if record in rects else index_from_top
                    if clicked_idx >= 0 and old_index < clicked_idx:
                        sequence = QSequentialAnimationGroup(self)
                        yield_rect = self._card_yield_geometry_for_circle_path(
                            widget,
                            target_circle,
                        )
                        yield_delay = self._card_yield_delay_for_circle_path(
                            widget,
                            source_circle,
                            target_circle,
                            duration_ms,
                        )
                        yield_ms = max(1, min(WORK_OVERLAY_TRANSITION_SHIFT_MS, duration_ms // 3))
                        settle_ms = max(1, min(WORK_OVERLAY_TRANSITION_SHIFT_MS, duration_ms // 3))
                        max_yield_start = max(0, duration_ms - yield_ms - settle_ms - 1)
                        yield_start = min(max_yield_start, max(0, yield_delay - yield_ms // 2))
                        hold_ms = max(1, duration_ms - yield_start - yield_ms - settle_ms)
                        if yield_start > 0:
                            sequence.addAnimation(QPauseAnimation(yield_start, self))
                        sequence.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                yield_rect,
                                yield_ms,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )
                        sequence.addAnimation(QPauseAnimation(hold_ms, self))
                        sequence.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                target,
                                settle_ms,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )
                        group.addAnimation(sequence)
                    else:
                        group.addAnimation(
                            self._animate_widget_geometry(
                                widget,
                                target,
                                duration_ms,
                                QEasingCurve.Type.InOutQuad,
                            )
                        )

                if group.animationCount() == 0:
                    self._add_noop_animation(group, duration_ms)
                group.finished.connect(self._end_transition)
                self._transition_animation_group = group
                group.start()
            except Exception:
                self._end_transition()

        def _start_transition(
            self,
            transition_type: str,
            item_id: str,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
        ) -> None:
            if self._transition_in_progress:
                return
            self._start_transition_watchdog(_transition_total_ms() + WORK_OVERLAY_RESTORE_DESCEND_MS)
            if transition_type == "card_to_completed":
                self._start_card_to_completed_transition(item_id, old_items, new_items)
                return
            if transition_type == "completed_to_card":
                self._start_completed_to_card_transition(item_id, old_items, new_items)
                return
            self._end_transition()

        def _end_transition(self) -> None:
            finished_transition_type = self._transition_type
            completed_ids = {
                _item_id(item)
                for item in self._raw_items
                if _item_id(item) and _item_is_completed(item)
            }
            self._settled_completed_intro_ids.update(completed_ids)
            self._transition_in_progress = False
            self._transition_type = ""
            self._transition_item_id = ""
            self._transition_started_at = 0.0
            self._transition_watchdog.stop()
            self._stop_transition_animation_group()
            if self._transition_source_widget is not None:
                if _transition_hides_source_before_effect_reset(finished_transition_type):
                    self._transition_source_widget.hide()
                self._transition_source_widget.setGraphicsEffect(None)
                if finished_transition_type not in {
                    "card_to_completed",
                    "completed_to_card",
                    "completed_dismiss",
                }:
                    self._transition_source_widget.show()
            self._transition_source_widget = None
            self._transition_source_effect = None
            if self._transition_card_widget is not None:
                self._transition_card_widget.setGraphicsEffect(None)
                self._transition_card_widget.setParent(None)
                self._transition_card_widget.deleteLater()
                self._transition_card_widget = None
            if self._transition_badge_widget is not None:
                self._transition_badge_widget.setGraphicsEffect(None)
                self._transition_badge_widget.setParent(None)
                self._transition_badge_widget.deleteLater()
                self._transition_badge_widget = None
            if self._transition_annihilation_widget is not None:
                try:
                    self._transition_annihilation_widget.close()
                    self._transition_annihilation_widget.deleteLater()
                except RuntimeError:
                    pass
                self._transition_annihilation_widget = None
            self._transition_hidden_widget = None
            self._transition_required_height = 0
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self.render_items(self._raw_items)

        def _sync_overlay_geometry(self) -> None:
            layout_width = max(WORK_OVERLAY_WIDTH, int(self._layout_width))
            content_height = max(
                1,
                self._transition_required_height,
                _overlay_items_required_height(
                    self._layout_items,
                    layout_width=layout_width,
                ),
            )
            screen = app.primaryScreen()
            available_geometry = screen.availableGeometry() if screen is not None else self.geometry()
            screen_geometry = screen.geometry() if screen is not None else available_geometry
            x = max(
                available_geometry.left(),
                available_geometry.right() - layout_width - WORK_OVERLAY_MARGIN,
            )
            y = _overlay_window_top_y(screen_geometry.top())
            max_height = max(1, available_geometry.bottom() - y - WORK_OVERLAY_MARGIN + 1)
            content_height = min(content_height, max_height)
            self.setGeometry(x, y, layout_width, content_height)
            self._shell.setMinimumSize(0, 0)
            self._shell.setMaximumSize(16777215, 16777215)
            self._shell.setGeometry(0, 0, layout_width, content_height)
            self._shell.move(0, 0)
            if not self.isVisible():
                self.show()
            self.raise_()
            QTimer.singleShot(0, self.reposition_interactive_windows)

        def render_items(self, items: Sequence[Mapping[str, object]]) -> None:
            if self._transition_in_progress:
                self._raw_items = list(items)
                return
            self._raw_items = list(items)
            ordered_items = _ordered_overlay_items(self._raw_items)
            visible_items = _visible_overlay_items(
                ordered_items,
                self._dismissed_instances,
                item_limit=self._item_limit,
            )
            self._sync_switch_pending(visible_items)
            if not visible_items:
                if self.isVisible():
                    now = time.time()
                    if self._empty_since <= 0.0:
                        self._empty_since = now
                        return
                    if (now - self._empty_since) < WORK_OVERLAY_EMPTY_GRACE_SECONDS:
                        return
                self._empty_since = 0.0
                self._last_payload_signature = "[]"
                self._last_structure_signature = ""
                self._layout_width = WORK_OVERLAY_WIDTH
                self._layout_items = []
                self._clear_shell()
                self.hide_overlay()
                self._previous_visible_items = []
                return
            self._empty_since = 0.0
            completed_count = sum(1 for item in visible_items if _item_is_completed(item))
            self._layout_width = max(
                WORK_OVERLAY_WIDTH,
                _completed_badge_row_width(completed_count),
            )
            visible_completed_ids = {
                _item_id(item)
                for item in visible_items
                if _item_id(item) and _item_is_completed(item)
            }
            self._settled_completed_intro_ids.intersection_update(visible_completed_ids)
            payload_signature = _overlay_payload_signature(
                visible_items,
                self._theme_tokens,
            )
            if payload_signature == self._last_payload_signature:
                return
            transition = _detect_transition(self._previous_visible_items, visible_items)
            if transition is not None:
                item_id = _detect_transition_item_id(self._previous_visible_items, visible_items)
                if item_id:
                    self._layout_width = _transition_layout_width(
                        self._previous_visible_items,
                        visible_items,
                    )
                    self._layout_items = list(self._previous_visible_items)
                    self._sync_overlay_geometry()
                    self._sync_item_widget_geometries(self._previous_visible_items)
                    self._start_transition(
                        transition,
                        item_id,
                        self._previous_visible_items,
                        visible_items,
                    )
                    self._previous_visible_items = list(visible_items)
                    return
            self._previous_visible_items = list(visible_items)
            self._last_payload_signature = payload_signature
            self._layout_items = list(visible_items)

            structure_signature = json.dumps(
                [
                    f"{self._item_identity(item, index)}:{self._item_widget_kind(item)}"
                    for index, item in enumerate(visible_items)
                ],
                ensure_ascii=False,
            )
            rebuild = structure_signature != self._last_structure_signature
            self._close_anchors.clear()
            self._workdir_anchors.clear()
            self._completed_check_anchors.clear()
            self._card_hover_anchors.clear()
            self._completed_hover_anchors.clear()
            if rebuild:
                self._last_structure_signature = structure_signature
                self._clear_shell()
                completed_items = [
                    item for item in visible_items if self._item_widget_kind(item) == "completed"
                ]
                active_items = [
                    item for item in visible_items if self._item_widget_kind(item) != "completed"
                ]
                if completed_items:
                    self._build_completed_row(completed_items)
                for item in active_items:
                    self._build_item_widget(item)
            else:
                for record, item in zip(self._item_widgets, visible_items):
                    self._update_item_widget(record, item)
            self._sync_overlay_geometry()
            self._sync_item_widget_geometries(visible_items)

        def reposition_interactive_windows(self) -> None:
            while len(self._close_windows) < len(self._close_anchors):
                self._close_windows.append(CloseButtonWindow(self.dismiss_item))
            while len(self._close_windows) > len(self._close_anchors):
                orphan = self._close_windows.pop()
                orphan.close()

            current_opacity = self.windowOpacity()
            for index, close_window in enumerate(self._close_windows):
                anchor, item, card_bg, pill_bg, accent = self._close_anchors[index]
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                close_window.configure(
                    item,
                    background=card_bg,
                    hover_background=pill_bg,
                    foreground="#A9B6C6",
                    hover_foreground=accent,
                    opacity=current_opacity,
                )
                close_window.move(anchor_top_left)
                close_window.show()
                close_window.raise_()

            for close_window in self._close_windows[len(self._close_anchors) :]:
                close_window.hide()

            while len(self._workdir_windows) < len(self._workdir_anchors):
                self._workdir_windows.append(WorkdirLinkWindow(self.switch_item))
            while len(self._workdir_windows) > len(self._workdir_anchors):
                orphan = self._workdir_windows.pop()
                orphan.close()

            for index, workdir_window in enumerate(self._workdir_windows):
                anchor, item = self._workdir_anchors[index]
                if not anchor.isVisible():
                    workdir_window.hide()
                    continue
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                pending = self._switch_pending_active_for_item(item)
                hotspot_pending = _workdir_link_pending_for_item(item, pending)
                workdir_window.configure(
                    item,
                    opacity=current_opacity,
                    pending=hotspot_pending,
                    pending_started_at=self._switch_pending_started_at if hotspot_pending else 0.0,
                )
                screen = app.primaryScreen()
                geometry = screen.availableGeometry() if screen is not None else self.geometry()
                workdir_window.setGeometry(
                    *_pending_workdir_window_rect(
                        anchor_top_left.x(),
                        anchor_top_left.y(),
                        anchor.width(),
                        anchor.height(),
                        pending=hotspot_pending,
                        screen_left=geometry.left(),
                    )
                )
                workdir_window.show()
                workdir_window.raise_()

            for workdir_window in self._workdir_windows[len(self._workdir_anchors) :]:
                workdir_window.hide()

            completed_palette = _completed_badge_palette(self._theme_tokens)
            while len(self._completed_check_windows) < len(self._completed_check_anchors):
                self._completed_check_windows.append(
                    ClickHotspotWindow(
                        self.dismiss_item,
                        circle=False,
                        hover_color=completed_palette["ring"],
                    )
                )
            while len(self._completed_check_windows) > len(self._completed_check_anchors):
                orphan = self._completed_check_windows.pop()
                orphan.close()

            for index, check_window in enumerate(self._completed_check_windows):
                anchor, item = self._completed_check_anchors[index]
                if not anchor.isVisible():
                    check_window.hide()
                    continue
                anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
                check_window.configure(
                    item,
                    opacity=current_opacity,
                    tooltip="关闭气泡",
                    hover_color=completed_palette["ring"],
                )
                check_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                check_window.show()
                check_window.raise_()

    overlay = OverlayWindow()
    state_watcher = QFileSystemWatcher()
    state_stale_timer = QTimer()
    state_stale_timer.setSingleShot(True)

    def watch_state_path() -> None:
        parent = str(path.parent)
        if parent and parent not in state_watcher.directories():
            try:
                state_watcher.addPath(parent)
            except RuntimeError:
                return
        file_path = str(path)
        if path.exists() and file_path not in state_watcher.files():
            try:
                state_watcher.addPath(file_path)
            except RuntimeError:
                return

    def schedule_stale_check() -> None:
        state_stale_timer.start(
            max(
                1000,
                int((max(0.1, float(stale_seconds)) + 0.25) * 1000),
            )
        )

    def refresh_state_from_watcher(*_args: object) -> None:
        watch_state_path()
        overlay.poll_state()
        watch_state_path()
        schedule_stale_check()

    state_watcher.fileChanged.connect(refresh_state_from_watcher)
    state_watcher.directoryChanged.connect(refresh_state_from_watcher)
    state_stale_timer.timeout.connect(refresh_state_from_watcher)
    watch_state_path()

    pointer_timer = QTimer()
    pointer_timer.timeout.connect(overlay.sync_pointer_state)
    pointer_timer.start(WORK_OVERLAY_POINTER_SYNC_MS)

    overlay.poll_state()
    schedule_stale_check()
    overlay.sync_pointer_state()
    app.exec()
    return 0
