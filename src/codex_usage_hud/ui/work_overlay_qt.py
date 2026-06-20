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

WORK_OVERLAY_POINTER_SYNC_MS = 60
WORK_OVERLAY_POLL_MS = 160
WORK_OVERLAY_WIDTH = 430
WORK_OVERLAY_MARGIN = 16
WORK_OVERLAY_TOP_OFFSET = 56
WORK_OVERLAY_CLOSE_SIZE = 22
WORK_OVERLAY_TEXT_WRAP_WIDTH = WORK_OVERLAY_WIDTH - 28
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
WORK_OVERLAY_TRANSITION_CLEARANCE_PX = (
    WORK_OVERLAY_COMPLETED_BADGE_SIZE + WORK_OVERLAY_COMPLETED_BADGE_SPACING
)
WORK_OVERLAY_SHIMMER_TIMER_MS = 30
WORK_OVERLAY_SHIMMER_STEP_PX = 3.5
WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX = 58
WORK_OVERLAY_SHIMMER_HIGHLIGHT = "#FFFFFF"
WORK_OVERLAY_SHIMMER_PEAK_ALPHA = 245
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


def _workdir_leaf(value: object) -> str:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return ""
    parts = [part for part in text.replace("/", "\\").split("\\") if part]
    return parts[-1] if parts else text


def _item_is_completed(item: Mapping[str, object]) -> bool:
    return str(item.get("status") or "") == "recent"


OverlayRect = tuple[float, float, float, float]


def _item_id(item: Mapping[str, object]) -> str:
    return str(item.get("id") or "").strip()


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
    row_width = _completed_badge_row_width(len(completed_items))
    start_x = max(0, int(layout_width) - row_width)
    rects: dict[str, OverlayRect] = {}
    for index, item in enumerate(completed_items):
        item_id = _item_id(item)
        if not item_id:
            continue
        rects[item_id] = (
            float(
                start_x
                + index
                * (WORK_OVERLAY_COMPLETED_BADGE_SIZE + WORK_OVERLAY_COMPLETED_BADGE_SPACING)
            ),
            0.0,
            float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
            float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
        )
    return rects


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
    row_top = 0
    if completed_items:
        row_top += WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT + WORK_OVERLAY_STACK_SPACING
    row_top += active_index * (WORK_OVERLAY_TRANSITION_CARD_HEIGHT + WORK_OVERLAY_STACK_SPACING)
    x = max(0, int(layout_width) - WORK_OVERLAY_WIDTH)
    return (
        float(x),
        float(row_top),
        float(WORK_OVERLAY_WIDTH),
        float(WORK_OVERLAY_TRANSITION_CARD_HEIGHT),
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
    success = _theme_mix(theme["success"], theme["accent"], 0.12, fallback=theme["success"])
    fill_start = _theme_mix(theme["surface"], success, 0.16, fallback=success)
    fill_mid = _theme_mix(success, theme["accent"], 0.16, fallback=success)
    fill_end = _theme_mix(
        theme["requestPanelSurface"],
        success,
        0.62,
        fallback=theme["requestPanelSurface"],
    )
    border = _theme_mix(theme["panelBorder"], success, 0.68, fallback=theme["panelBorder"])
    primary_ink = _theme_emphasis_ink(fill_mid, fallback=theme["text"])
    secondary_ink = _theme_mix(primary_ink, theme["accent"], 0.22, fallback=primary_ink)
    elapsed_ink = _theme_mix(primary_ink, success, 0.10, fallback=primary_ink)
    ring = _theme_mix(theme["accent"], success, 0.34, fallback=theme["accent"])
    dashed_ring = _theme_mix(theme["accent"], primary_ink, 0.22, fallback=theme["accent"])
    stat_box_fill = _theme_mix(
        theme["requestPanelSurface"],
        success,
        0.22,
        fallback=theme["requestPanelSurface"],
    )
    stat_box_border = _theme_mix(
        theme["panelBorder"],
        success,
        0.46,
        fallback=theme["panelBorder"],
    )
    stat_value = _theme_emphasis_ink(stat_box_fill, fallback=primary_ink)
    stat_label = _theme_mix(stat_value, success, 0.20, fallback=stat_value)
    return {
        "fillStart": fill_start,
        "fillMid": fill_mid,
        "fillEnd": fill_end,
        "border": border,
        "ring": ring,
        "dashedRing": dashed_ring,
        "titleText": primary_ink,
        "workdirText": secondary_ink,
        "checkText": primary_ink,
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
        from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
        from PySide6.QtGui import (
            QColor,
            QCursor,
            QFont,
            QFontMetrics,
            QLinearGradient,
            QPainter,
            QPainterPath,
            QPen,
            QTextLayout,
            QTextOption,
        )
        from PySide6.QtWidgets import (
            QApplication,
            QFrame,
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
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def configure(self, item: Mapping[str, object], *, opacity: float) -> None:
            self._item = dict(item)
            self.setWindowOpacity(opacity)
            tooltip = str(
                item.get("targetTitle") or item.get("title") or item.get("workdir") or ""
            ).strip()
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
            if self._hover:
                fill = QColor(156, 203, 255, 18)
            painter.setBrush(fill)
            painter.drawRoundedRect(self.rect(), 4, 4)

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

        def configure(self, item: Mapping[str, object], *, opacity: float, tooltip: str = "") -> None:
            self._item = dict(item)
            self.setWindowOpacity(opacity)
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

        def sizeHint(self) -> QSize:
            return QSize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )

        def _advance(self) -> None:
            elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
            self._progress = min(1.0, elapsed_ms / WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS)
            if self._progress >= 1.0:
                self._timer.stop()
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)

            eased = 1.0 - pow(1.0 - max(0.0, min(1.0, self._progress)), 3)
            palette = _completed_badge_palette(self._theme_tokens)
            final_size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
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
            workdir = str(self._item.get("workdirName") or "").strip()
            if not workdir:
                workdir = _workdir_leaf(self._item.get("workdir"))
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
                stat_border.setAlpha(168)
                painter.setPen(QPen(stat_border, 0.8))
                stat_fill = QColor(palette["statBoxFill"])
                stat_fill.setAlpha(136)
                painter.setBrush(stat_fill)
                painter.drawRoundedRect(box, 6.0, 6.0)
                painter.setPen(QColor(palette["statValue"]))
                painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
                painter.drawText(box.adjusted(1.0, 1.0, -1.0, -11.0), alignment.AlignCenter, value)
                painter.setPen(QColor(palette["statLabel"]))
                painter.setFont(QFont("Microsoft YaHei UI", 5))
                painter.drawText(box.adjusted(1.0, 13.0, -1.0, -1.0), alignment.AlignCenter, label)

            painter.restore()

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

    class TransitionOverlay(QWidget):
        def __init__(
            self,
            parent: QWidget | None = None,
            *,
            theme_tokens: Mapping[str, object] | None = None,
        ) -> None:
            super().__init__(parent)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self._progress = 0.0
            self._transition_type = ""
            self._source_rect = QRectF()
            self._target_rect = QRectF()
            self._item: Mapping[str, object] = {}
            self._theme_tokens = _resolved_overlay_theme(theme_tokens)

        def set_theme_tokens(
            self,
            theme_tokens: Mapping[str, object] | None,
        ) -> None:
            self._theme_tokens = _resolved_overlay_theme(theme_tokens)
            self.update()

        def set_transition(
            self,
            transition_type: str,
            source_rect: QRectF,
            target_rect: QRectF,
            item: Mapping[str, object],
        ) -> None:
            self._transition_type = transition_type
            self._source_rect = QRectF(source_rect)
            self._target_rect = QRectF(target_rect)
            self._item = dict(item)
            self._progress = 0.0
            self.update()

        def set_progress(self, progress: float) -> None:
            self._progress = max(0.0, min(1.0, progress))
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            if self._transition_type not in ("card_to_completed", "completed_to_card"):
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            source_rect = (
                self._source_rect.x(),
                self._source_rect.y(),
                self._source_rect.width(),
                self._source_rect.height(),
            )
            target_rect = (
                self._target_rect.x(),
                self._target_rect.y(),
                self._target_rect.width(),
                self._target_rect.height(),
            )
            current = _transition_rect_for_progress(
                self._transition_type,
                source_rect,
                target_rect,
                self._progress,
            )
            palette = _transition_palette(self._transition_type, self._theme_tokens)
            current_rect = QRectF(*current)
            radius = min(current_rect.width(), current_rect.height()) / 2
            gradient = QLinearGradient(current_rect.topLeft(), current_rect.bottomRight())
            gradient.setColorAt(0.0, QColor(palette["fillStart"]))
            gradient.setColorAt(0.5, QColor(palette["fillMid"]))
            gradient.setColorAt(1.0, QColor(palette["fillEnd"]))
            pen_color = QColor(palette["border"])
            painter.setBrush(gradient)
            painter.setPen(QPen(pen_color, 1.4))
            painter.drawRoundedRect(current_rect, radius, radius)

            if self._transition_type == "card_to_completed" and self._progress < 0.24:
                title = _compact_work_text(self._item.get("title") or "Codex 工作", 42)
                elapsed = _compact_work_text(self._item.get("elapsedText") or "已处理 --", 24)
                painter.save()
                painter.setOpacity(1.0 - self._progress / 0.24)
                painter.setPen(QColor(palette["titleText"]))
                painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
                painter.drawText(
                    current_rect.adjusted(12.0, 10.0, -12.0, -62.0),
                    alignment.AlignLeft | alignment.AlignVCenter,
                    title,
                )
                painter.setPen(QColor(palette["subtitleText"]))
                painter.setFont(QFont("Microsoft YaHei UI", 8))
                painter.drawText(
                    current_rect.adjusted(12.0, 42.0, -12.0, -18.0),
                    alignment.AlignLeft | alignment.AlignVCenter,
                    elapsed,
                )
                painter.restore()

            if self._transition_type == "completed_to_card" and self._progress > 0.76:
                title = _compact_work_text(self._item.get("title") or "Codex 工作", 42)
                elapsed = _compact_work_text(self._item.get("elapsedText") or "已处理 --", 24)
                painter.save()
                painter.setOpacity(min(1.0, (self._progress - 0.76) / 0.24))
                painter.setPen(QColor(palette["titleText"]))
                painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
                painter.drawText(
                    current_rect.adjusted(12.0, 10.0, -12.0, -62.0),
                    alignment.AlignLeft | alignment.AlignVCenter,
                    title,
                )
                painter.setPen(QColor(palette["subtitleText"]))
                painter.setFont(QFont("Microsoft YaHei UI", 8))
                painter.drawText(
                    current_rect.adjusted(12.0, 42.0, -12.0, -18.0),
                    alignment.AlignLeft | alignment.AlignVCenter,
                    elapsed,
                )
                painter.restore()

            check_visible = (
                self._transition_type == "card_to_completed"
                and self._progress >= 0.24
            ) or (
                self._transition_type == "completed_to_card"
                and self._progress <= 0.76
            )
            if check_visible:
                check_font = QFont("Segoe UI Symbol", 24, QFont.Weight.Bold)
                painter.setFont(check_font)
                painter.setPen(QColor(palette["markText"]))
                mark = "↻" if self._transition_type == "completed_to_card" else "✓"
                painter.drawText(current_rect, Qt.AlignmentFlag.AlignCenter, mark)

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
            self._empty_since = 0.0
            self._state_read_failed_at = 0.0
            self._layout_width = WORK_OVERLAY_WIDTH
            self._transition_in_progress = False
            self._transition_type = ""
            self._transition_item_id = ""
            self._transition_started_at = 0.0
            self._transition_widget: TransitionOverlay | None = None
            self._transition_hidden_widget: QWidget | None = None
            self._completed_badge_moves: list[tuple[QWidget, int, int]] = []
            self._card_clearance_moves: list[tuple[QWidget, int]] = []
            self._completed_card_memory_rects: dict[str, OverlayRect] = {}
            self._settled_completed_intro_ids: set[str] = set()
            self._transition_timer = QTimer(self)
            self._transition_timer.timeout.connect(self._update_transition)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setAttribute(widget_attrs.WA_ShowWithoutActivating, True)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(focus_policy.NoFocus)
            self.setWindowOpacity(overlay_alpha)

            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)

            self._shell = QWidget(self)
            self._shell.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            shell_layout = QVBoxLayout(self._shell)
            shell_layout.setContentsMargins(0, 0, 0, 0)
            shell_layout.setSpacing(8)
            root_layout.addWidget(self._shell)

        def _wrapped_label_height(self, label: QLabel, width: int) -> int:
            return max(label.sizeHint().height(), label.heightForWidth(width), label.minimumSizeHint().height())

        def dismiss_item(self, item: Mapping[str, object]) -> None:
            item_id = str(item.get("id") or "")
            if item_id:
                self._dismissed_instances[item_id] = _item_dismiss_key(item)
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self.render_items(self._raw_items)

        def switch_item(self, item: Mapping[str, object]) -> None:
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
                "requestedAt": time.time(),
                "current": bool(item.get("current")),
            }
            try:
                self._command_path.parent.mkdir(parents=True, exist_ok=True)
                with self._command_path.open("a", encoding="utf-8") as handle:
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
            if self._transition_widget is not None:
                self._transition_widget.set_theme_tokens(self._theme_tokens)
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
            shell_layout = self._shell.layout()
            while shell_layout.count():
                item = shell_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

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
            row = QWidget(self._shell)
            row.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(WORK_OVERLAY_COMPLETED_BADGE_SPACING)
            row_layout.addStretch(1)
            self._shell.layout().addWidget(row)
            for item in completed_items:
                self._build_completed_badge(item, row, row_layout)

        def _build_completed_badge(
            self,
            item: Mapping[str, object],
            parent: QWidget,
            row_layout: QHBoxLayout,
        ) -> None:
            item_id = _item_id(item)
            animate_intro = item_id not in self._settled_completed_intro_ids
            badge = CompletedBadgeWidget(
                item,
                parent,
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
            row_layout.addWidget(badge, 0, alignment.AlignRight)
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
            badge.set_theme_tokens(self._theme_tokens)
            badge.set_item(item)
            self._completed_hover_anchors.append(record["hover_anchor"])
            session_id = str(item.get("sessionId") or item.get("id") or "").strip()
            target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
            workdir_text = str(item.get("workdirName") or "").strip()
            if not workdir_text:
                workdir_text = _workdir_leaf(item.get("workdir"))
            if workdir_text and (session_id or target_title):
                self._workdir_anchors.append((record["workdir_anchor"], dict(item)))
            self._completed_check_anchors.append((record["check_anchor"], dict(item)))

        def _build_item_card(self, item: Mapping[str, object]) -> None:
            card = QFrame(self._shell)
            card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            card.setFixedWidth(WORK_OVERLAY_WIDTH)
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
            self._shell.layout().addWidget(card, 0, alignment.AlignRight)

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
            self._item_widgets.append(record)
            self._update_item_card(record, item)

        def _update_item_card(self, record: dict[str, Any], item: Mapping[str, object]) -> None:
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
            detail.setText(body_text)
            detail.setMinimumHeight(self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH))
            detail.setStyleSheet(
                "QLabel {"
                f"color: {theme['requestText']};"
                "border: none;"
                "background: transparent;"
                "}"
            )

            workdir_text = str(item.get("workdir") or "").strip()
            session_id = str(item.get("sessionId") or item.get("id") or "").strip()
            target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
            workdir_clickable = bool(workdir_text and (session_id or target_title))
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
                workdir_label.setToolTip(workdir_text)
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

            self._card_hover_anchors.append(card)
            self._close_anchors.append(
                (record["close_anchor"], dict(item), card_bg, pill_bg, accent)
            )
            if workdir_clickable:
                self._workdir_anchors.append((record["workdir_label"], dict(item)))

        def _widget_shell_rect(self, widget: QWidget) -> QRectF:
            top_left = widget.mapTo(self._shell, QPoint(0, 0))
            return QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(max(1, widget.width())),
                float(max(1, widget.height())),
            )

        def _record_widget_for_kind(
            self,
            item_id: str,
            kind: str,
        ) -> QWidget | None:
            for record in self._item_widgets:
                if record.get("kind") == kind and str(record.get("item_id") or "") == item_id:
                    widget = record.get("card") if kind == "card" else record.get("badge")
                    if isinstance(widget, QWidget):
                        return widget
            return None

        def _prepare_completed_badge_moves(
            self,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
            source_rect: QRectF,
            target_rect: QRectF,
        ) -> None:
            moves = _completed_badge_slot_moves(
                old_items,
                new_items,
                layout_width=self._layout_width,
            )
            self._completed_badge_moves = []
            for record in self._item_widgets:
                if record.get("kind") != "completed":
                    continue
                item_id = str(record.get("item_id") or "")
                if item_id == self._transition_item_id or item_id not in moves:
                    continue
                widget = record.get("badge")
                if not isinstance(widget, QWidget):
                    continue
                start_slot_rect, target_slot_rect = moves[item_id]
                start_x = int(round(start_slot_rect[0]))
                target_x = int(round(target_slot_rect[0]))
                widget.move(start_x, widget.y())
                self._completed_badge_moves.append((widget, start_x, target_x))
            self._card_clearance_moves = []
            source_circle = QRectF(
                *_right_edge_circle_rect_for_rect(
                    (
                        source_rect.x(),
                        source_rect.y(),
                        source_rect.width(),
                        source_rect.height(),
                    )
                )
            )
            path_top = min(source_circle.top(), target_rect.top())
            path_bottom = max(source_circle.bottom(), target_rect.bottom())
            path_left = min(source_circle.left(), target_rect.left())
            path_right = max(source_circle.right(), target_rect.right())
            for record in self._item_widgets:
                if record.get("kind") != "card":
                    continue
                item_id = str(record.get("item_id") or "")
                if item_id == self._transition_item_id:
                    continue
                widget = record.get("card")
                if not isinstance(widget, QWidget):
                    continue
                rect = self._widget_shell_rect(widget)
                overlaps_path = (
                    rect.right() > path_left
                    and rect.left() < path_right
                    and rect.bottom() > path_top
                    and rect.top() < path_bottom
                )
                if not overlaps_path:
                    continue
                self._card_clearance_moves.append((widget, widget.x()))

        def _remembered_card_rect(self, item_id: str) -> OverlayRect | None:
            rect = self._completed_card_memory_rects.get(item_id)
            if rect is None:
                return None
            return _remembered_card_rect_for_layout(rect, layout_width=self._layout_width)

        def _hide_transition_interactive_windows(self) -> None:
            for window in [*self._close_windows, *self._completed_check_windows]:
                window.hide()

        def _start_transition(
            self,
            transition_type: str,
            item_id: str,
            old_items: list[Mapping[str, object]],
            new_items: list[Mapping[str, object]],
        ) -> None:
            self._transition_in_progress = True
            self._transition_type = transition_type
            self._transition_item_id = item_id
            self._transition_started_at = time.monotonic()
            source_kind = "card" if transition_type == "card_to_completed" else "completed"
            target_kind = "completed" if transition_type == "card_to_completed" else "card"
            source_widget = self._record_widget_for_kind(item_id, source_kind)
            source_rect = (
                self._widget_shell_rect(source_widget)
                if source_widget is not None
                else QRectF(
                    *_find_item_rect(
                        old_items,
                        item_id,
                        source_kind,
                        layout_width=self._layout_width,
                    )
                )
            )
            target_rect = QRectF(
                *_find_item_rect(
                    new_items,
                    item_id,
                    target_kind,
                    layout_width=self._layout_width,
                )
            )
            if transition_type == "card_to_completed":
                self._completed_card_memory_rects[item_id] = (
                    source_rect.x(),
                    source_rect.y(),
                    source_rect.width(),
                    source_rect.height(),
                )
            elif transition_type == "completed_to_card":
                remembered_rect = self._remembered_card_rect(item_id)
                if remembered_rect is not None:
                    target_rect = QRectF(*remembered_rect)
            required_height = _transition_required_height(
                transition_type,
                (
                    source_rect.x(),
                    source_rect.y(),
                    source_rect.width(),
                    source_rect.height(),
                ),
                (
                    target_rect.x(),
                    target_rect.y(),
                    target_rect.width(),
                    target_rect.height(),
                ),
            )
            self._shell.setMinimumHeight(max(self._shell.minimumHeight(), required_height))
            self._sync_overlay_geometry()
            item = {}
            for it in old_items:
                if _item_id(it) == item_id:
                    item = it
                    break
            if not item:
                for it in new_items:
                    if _item_id(it) == item_id:
                        item = it
                        break
            if self._transition_widget is None:
                self._transition_widget = TransitionOverlay(
                    self._shell,
                    theme_tokens=self._theme_tokens,
                )
            else:
                self._transition_widget.set_theme_tokens(self._theme_tokens)
            self._transition_widget.setGeometry(self._shell.rect())
            self._transition_widget.show()
            self._transition_widget.raise_()
            self._transition_widget.set_transition(
                transition_type,
                source_rect,
                target_rect,
                item,
            )
            self._transition_hidden_widget = None
            if source_widget is not None:
                self._transition_hidden_widget = source_widget
                source_widget.hide()
            self._hide_transition_interactive_windows()
            self._prepare_completed_badge_moves(old_items, new_items, source_rect, target_rect)
            self._transition_timer.start(16)

        def _update_transition(self) -> None:
            if not self._transition_in_progress:
                return
            elapsed_ms = int(max(0.0, (time.monotonic() - self._transition_started_at) * 1000.0))
            total_duration = _transition_total_ms()
            progress = min(1.0, elapsed_ms / max(1, total_duration))
            if self._transition_widget is not None:
                self._transition_widget.set_progress(progress)
            self._update_completed_badge_moves(progress)
            if progress >= 1.0:
                self._end_transition()

        def _update_completed_badge_moves(self, progress: float) -> None:
            shift_progress = _transition_slot_shift_progress(self._transition_type, progress)
            for widget, start_x, target_x in self._completed_badge_moves:
                widget.move(int(round(_lerp(start_x, target_x, shift_progress))), widget.y())
            clearance_offset = _transition_clearance_offset(self._transition_type, progress)
            for widget, start_x in self._card_clearance_moves:
                widget.move(int(round(start_x + clearance_offset)), widget.y())

        def _end_transition(self) -> None:
            finished_transition_type = self._transition_type
            finished_item_id = self._transition_item_id
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
            self._completed_badge_moves.clear()
            self._card_clearance_moves.clear()
            self._transition_timer.stop()
            if self._transition_widget is not None:
                self._transition_widget.setParent(None)
                self._transition_widget.deleteLater()
                self._transition_widget = None
            self._transition_hidden_widget = None
            self._shell.setMinimumHeight(0)
            if finished_transition_type == "completed_to_card":
                self._completed_card_memory_rects.pop(finished_item_id, None)
            self._last_payload_signature = ""
            self._last_structure_signature = ""
            self.render_items(self._raw_items)

        def _sync_overlay_geometry(self) -> None:
            layout_width = max(WORK_OVERLAY_WIDTH, int(self._layout_width))
            self._shell.setFixedWidth(layout_width)
            self._shell.layout().activate()
            self.layout().activate()
            content_height = max(
                1,
                self.layout().totalHeightForWidth(layout_width),
                self.sizeHint().height(),
            )
            screen = app.primaryScreen()
            geometry = screen.availableGeometry() if screen is not None else self.geometry()
            x = max(geometry.left(), geometry.right() - layout_width - WORK_OVERLAY_MARGIN)
            max_y = max(geometry.top(), geometry.bottom() - content_height - WORK_OVERLAY_MARGIN)
            y = min(geometry.top() + WORK_OVERLAY_TOP_OFFSET, max_y)
            self.setGeometry(x, y, layout_width, content_height)
            if not self.isVisible():
                self.show()
            self.raise_()
            self._shell.layout().activate()
            self.layout().activate()
            final_height = max(
                content_height,
                self.layout().totalHeightForWidth(layout_width),
                self.sizeHint().height(),
            )
            if final_height != content_height:
                max_y = max(geometry.top(), geometry.bottom() - final_height - WORK_OVERLAY_MARGIN)
                y = min(geometry.top() + WORK_OVERLAY_TOP_OFFSET, max_y)
                self.setGeometry(x, y, layout_width, final_height)
            if self._transition_widget is not None:
                self._transition_widget.setGeometry(self._shell.rect())
                self._transition_widget.show()
                self._transition_widget.raise_()
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
                    self._sync_overlay_geometry()
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
                workdir_window.configure(item, opacity=current_opacity)
                workdir_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                workdir_window.show()
                workdir_window.raise_()

            for workdir_window in self._workdir_windows[len(self._workdir_anchors) :]:
                workdir_window.hide()

            while len(self._completed_check_windows) < len(self._completed_check_anchors):
                self._completed_check_windows.append(
                    ClickHotspotWindow(
                        self.dismiss_item,
                        circle=False,
                        hover_color="#B9F7C9",
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
                check_window.configure(item, opacity=current_opacity, tooltip="关闭气泡")
                check_window.setGeometry(
                    anchor_top_left.x(),
                    anchor_top_left.y(),
                    max(1, anchor.width()),
                    max(1, anchor.height()),
                )
                check_window.show()
                check_window.raise_()

    overlay = OverlayWindow()
    poll_timer = QTimer()
    poll_timer.timeout.connect(overlay.poll_state)
    poll_timer.start(WORK_OVERLAY_POLL_MS)

    pointer_timer = QTimer()
    pointer_timer.timeout.connect(overlay.sync_pointer_state)
    pointer_timer.start(WORK_OVERLAY_POINTER_SYNC_MS)

    overlay.poll_state()
    overlay.sync_pointer_state()
    app.exec()
    return 0
