"""Qt-free work-overlay geometry, transition, and hit-test functions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .constants import (
    WORK_OVERLAY_POINTER_SYNC_MS,
    WORK_OVERLAY_HOTSPOT_HIT_ALPHA,
    WORK_OVERLAY_HOTSPOT_HOVER_ALPHA,
    WORK_OVERLAY_WIDTH,
    WORK_OVERLAY_MARGIN,
    WORK_OVERLAY_TOP_OFFSET,
    WORK_OVERLAY_CLOSE_SIZE,
    WORK_OVERLAY_TEXT_WRAP_WIDTH,
    WORK_OVERLAY_BODY_MAX_LINES,
    WORK_OVERLAY_CARD_X_PADDING,
    WORK_OVERLAY_CARD_Y_PADDING,
    WORK_OVERLAY_CARD_SPACING,
    WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SPACING,
    WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS,
    WORK_OVERLAY_STACK_SPACING,
    WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
    WORK_OVERLAY_TRANSITION_SHRINK_MS,
    WORK_OVERLAY_TRANSITION_PAUSE_MS,
    WORK_OVERLAY_TRANSITION_MOVE_MS,
    WORK_OVERLAY_TRANSITION_SHIFT_MS,
    WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED,
    WORK_OVERLAY_RESTORE_FADE_OUT_MS,
    WORK_OVERLAY_RESTORE_SHIFT_MS,
    WORK_OVERLAY_RESTORE_FADE_IN_MS,
    WORK_OVERLAY_RESTORE_DESCEND_MS,
    WORK_OVERLAY_COMPLETED_ANNIHILATION_MS,
    WORK_OVERLAY_COMPLETED_ANNIHILATION_MARGIN,
    WORK_OVERLAY_TRANSITION_CLEARANCE_PX,
    WORK_OVERLAY_SHIMMER_TIMER_MS,
    WORK_OVERLAY_SHIMMER_STEP_PX,
    WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX,
    WORK_OVERLAY_SHIMMER_HIGHLIGHT,
    WORK_OVERLAY_SHIMMER_PEAK_ALPHA,
    WORK_OVERLAY_SWITCH_PENDING_SLOW_SECONDS,
    WORK_OVERLAY_SWITCH_PENDING_TIMEOUT_SECONDS,
    WORK_OVERLAY_SWITCH_PENDING_TIMER_MS,
    WORK_OVERLAY_SWITCH_PENDING_MIN_WIDTH,
    WORK_OVERLAY_COMPLETED_PENDING_LAUNCH_SECONDS,
    WORK_OVERLAY_COMPLETED_PENDING_FINISH_SECONDS,
    WORK_OVERLAY_EMPTY_GRACE_SECONDS,
    WORK_OVERLAY_STATE_READ_FAILURE_GRACE_SECONDS,
    WORK_OVERLAY_STATE_READ_RETRY_MS,
    WORK_OVERLAY_HELPER_HEARTBEAT_MS,
    WORK_OVERLAY_ELAPSED_TEXT_TIMER_MS,
    DEFAULT_WORK_OVERLAY_THEME,
)
from .model import (
    _clamp01,
    _item_id,
    _item_is_completed,
    _item_kind,
)

OverlayRect = tuple[float, float, float, float]


def _is_left_side(side: str) -> bool:
    return str(side or "right").strip().lower() == "left"


def _mirror_rect_x(
    rect: OverlayRect,
    *,
    layout_width: int,
    side: str,
) -> OverlayRect:
    if not _is_left_side(side):
        return rect
    return (
        float(layout_width) - rect[0] - rect[2],
        rect[1],
        rect[2],
        rect[3],
    )

def work_overlay_max_items_for_screen_height(screen_height: int) -> int:
    available_height = max(
        1,
        int(screen_height) - WORK_OVERLAY_TOP_OFFSET - (WORK_OVERLAY_MARGIN * 2),
    )
    return max(1, available_height // WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT)


def _pending_workdir_window_rect(
    anchor_x: int,
    anchor_y: int,
    anchor_width: int,
    anchor_height: int,
    *,
    pending: bool,
    screen_left: int = 0,
    side: str = "right",
) -> tuple[int, int, int, int]:
    width = max(1, int(anchor_width))
    height = max(1, int(anchor_height))
    x = int(anchor_x)
    y = int(anchor_y)
    if not pending:
        return x, y, width, height
    right = x + width
    width = max(width, WORK_OVERLAY_SWITCH_PENDING_MIN_WIDTH)
    x = (
        max(int(screen_left), x)
        if _is_left_side(side)
        else max(int(screen_left), right - width)
    )
    return x, y, width, height

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
    changes = _transition_changes(old_items, new_items)
    return changes[0][1] if changes else None

def _transition_changes(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
) -> list[tuple[str, str]]:
    old_by_id = {_item_id(item): item for item in old_items if _item_id(item)}
    changes: list[tuple[str, str]] = []
    for item in new_items:
        item_id = _item_id(item)
        if not item_id or item_id not in old_by_id:
            continue
        old_kind = _item_kind(old_by_id[item_id])
        new_kind = _item_kind(item)
        if old_kind == "card" and new_kind == "completed":
            changes.append((item_id, "card_to_completed"))
        elif old_kind == "completed" and new_kind == "card":
            changes.append((item_id, "completed_to_card"))
    return changes

def _detect_transition_item_id(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
) -> str:
    changes = _transition_changes(old_items, new_items)
    return changes[0][0] if changes else ""

def _defer_other_transition_items(
    old_items: Sequence[Mapping[str, object]],
    new_items: Sequence[Mapping[str, object]],
    transition_item_id: str,
) -> list[Mapping[str, object]]:
    """Keep later shape changes at their displayed state until their turn."""
    old_by_id = {_item_id(item): item for item in old_items if _item_id(item)}
    deferred_ids = {
        item_id
        for item_id, _transition_type in _transition_changes(old_items, new_items)
        if item_id != transition_item_id
    }
    deferred_items: list[Mapping[str, object]] = []
    for item in new_items:
        item_id = _item_id(item)
        deferred_items.append(
            dict(old_by_id[item_id]) if item_id in deferred_ids else item
        )
    return deferred_items

def _completed_badge_slot_rects(
    items: Sequence[Mapping[str, object]],
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
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
            side=side,
        )
    return rects

def _completed_slot_rect(
    index_from_right: int,
    completed_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> OverlayRect:
    completed_count = max(0, int(completed_count))
    if completed_count <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    index_from_right = max(0, min(completed_count - 1, int(index_from_right)))
    index_from_left = completed_count - 1 - index_from_right
    row_width = _completed_badge_row_width(completed_count)
    start_x = max(0, int(layout_width) - row_width)
    rect = (
        float(
            start_x
            + index_from_left
            * (WORK_OVERLAY_COMPLETED_BADGE_SIZE + WORK_OVERLAY_COMPLETED_BADGE_SPACING)
        ),
        0.0,
        float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
        float(WORK_OVERLAY_COMPLETED_BADGE_SIZE),
    )
    return _mirror_rect_x(rect, layout_width=layout_width, side=side)

def _card_slot_rect(
    index_from_top: int,
    completed_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> OverlayRect:
    row_top = 0
    if int(completed_count) > 0:
        row_top += WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT + WORK_OVERLAY_STACK_SPACING
    index_from_top = max(0, int(index_from_top))
    card_height = WORK_OVERLAY_TRANSITION_CARD_HEIGHT
    row_top += index_from_top * (card_height + WORK_OVERLAY_STACK_SPACING)
    x = 0 if _is_left_side(side) else max(0, int(layout_width) - WORK_OVERLAY_WIDTH)
    return (
        float(x),
        float(row_top),
        float(WORK_OVERLAY_WIDTH),
        float(card_height),
    )

def _overlay_required_height_for_counts(
    completed_count: int,
    card_count: int,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> int:
    bottoms: list[float] = [1.0]
    completed_count = max(0, int(completed_count))
    card_count = max(0, int(card_count))
    if completed_count > 0:
        bottoms.append(float(WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT))
    if card_count > 0:
        last_card = _card_slot_rect(
            card_count - 1,
            completed_count,
            layout_width=layout_width,
            side=side,
        )
        bottoms.append(last_card[1] + last_card[3])
    return max(1, int(math.ceil(max(bottoms))))

def _overlay_window_top_y(screen_top: int) -> int:
    return int(screen_top) + WORK_OVERLAY_TOP_OFFSET


def _overlay_window_x(
    screen_left: int,
    screen_right: int,
    layout_width: int,
    margin: int = WORK_OVERLAY_MARGIN,
    side: str = "right",
) -> int:
    if _is_left_side(side):
        return int(screen_left) + int(margin)
    return max(
        int(screen_left),
        int(screen_right) - int(layout_width) - int(margin),
    )

def _find_item_rect(
    items: Sequence[Mapping[str, object]],
    item_id: str,
    kind: str,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> OverlayRect:
    if not item_id:
        return (0.0, 0.0, 0.0, 0.0)
    completed_items = [item for item in items if _item_is_completed(item)]
    active_items = [item for item in items if not _item_is_completed(item)]
    if kind == "completed":
        return _completed_badge_slot_rects(
            items,
            layout_width=layout_width,
            side=side,
        ).get(
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
        side=side,
    )

def _find_item_position(
    items: Sequence[Mapping[str, object]],
    item_id: str,
    kind: str,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> tuple[int, int]:
    rect = _find_item_rect(
        items,
        item_id,
        kind,
        layout_width=layout_width,
        side=side,
    )
    return int(rect[0]), int(rect[1])

def _remembered_card_rect_for_layout(
    rect: OverlayRect,
    *,
    layout_width: int = WORK_OVERLAY_WIDTH,
    side: str = "right",
) -> OverlayRect:
    x = (
        0
        if _is_left_side(side)
        else max(0, int(layout_width) - int(rect[2]))
    )
    return (
        float(x),
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
    return _edge_circle_rect_for_rect(rect, side="right")


def _edge_circle_rect_for_rect(
    rect: OverlayRect,
    *,
    side: str = "right",
) -> OverlayRect:
    size = float(WORK_OVERLAY_COMPLETED_BADGE_SIZE)
    center_y = rect[1] + rect[3] / 2.0
    top = max(0.0, center_y - size / 2.0)
    left = rect[0] if _is_left_side(side) else rect[0] + rect[2] - size
    return (
        left,
        top,
        size,
        size,
    )

def _card_height_circle_rect_for_rect(
    rect: OverlayRect,
    *,
    side: str = "right",
) -> OverlayRect:
    diameter = max(1.0, float(rect[3]))
    left = rect[0] if _is_left_side(side) else float(rect[0]) + rect[2] - diameter
    return (
        left,
        float(rect[1]),
        diameter,
        diameter,
    )

def _card_yield_rect_for_circle_path(
    card_rect: OverlayRect,
    circle_rect: OverlayRect,
    *,
    side: str = "right",
) -> OverlayRect:
    if _is_left_side(side):
        target_left = float(circle_rect[0]) + float(circle_rect[2]) + WORK_OVERLAY_COMPLETED_BADGE_SPACING
        offset_x = max(0.0, target_left - float(card_rect[0]))
    else:
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

def _transition_required_height(
    transition_type: str,
    source_rect: OverlayRect,
    target_rect: OverlayRect,
    *,
    side: str = "right",
) -> int:
    rects = [
        source_rect,
        target_rect,
        _transition_rect_for_progress(
            transition_type, source_rect, target_rect, 0.0, side=side
        ),
        _transition_rect_for_progress(
            transition_type, source_rect, target_rect, 0.35, side=side
        ),
        _transition_rect_for_progress(
            transition_type, source_rect, target_rect, 0.75, side=side
        ),
        _transition_rect_for_progress(
            transition_type, source_rect, target_rect, 1.0, side=side
        ),
    ]
    return max(1, int(math.ceil(max(rect[1] + rect[3] for rect in rects))))

def _transition_rect_for_progress(
    transition_type: str,
    source_rect: OverlayRect,
    target_rect: OverlayRect,
    progress: float,
    *,
    side: str = "right",
) -> OverlayRect:
    progress = _clamp01(progress)
    total_ms = float(_transition_total_ms())
    shrink_end = WORK_OVERLAY_TRANSITION_SHRINK_MS / total_ms
    pause_end = (WORK_OVERLAY_TRANSITION_SHRINK_MS + WORK_OVERLAY_TRANSITION_PAUSE_MS) / total_ms

    if transition_type == "card_to_completed":
        source_circle = _edge_circle_rect_for_rect(source_rect, side=side)
        if progress <= shrink_end:
            return _lerp_rect(source_rect, source_circle, _ease_out_cubic(progress / shrink_end))
        if progress <= pause_end:
            return source_circle
        move_progress = (progress - pause_end) / max(0.001, 1.0 - pause_end)
        return _lerp_rect(source_circle, target_rect, _ease_in_out_cubic(move_progress))

    if transition_type == "completed_to_card":
        target_circle = _edge_circle_rect_for_rect(target_rect, side=side)
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
    side: str = "right",
) -> dict[str, tuple[OverlayRect, OverlayRect]]:
    old_rects = _completed_badge_slot_rects(
        old_items,
        layout_width=layout_width,
        side=side,
    )
    new_rects = _completed_badge_slot_rects(
        new_items,
        layout_width=layout_width,
        side=side,
    )
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
    side: str = "right",
) -> dict[str, tuple[OverlayRect, OverlayRect, OverlayRect]]:
    old_rects = _completed_badge_slot_rects(
        old_items,
        layout_width=layout_width,
        side=side,
    )
    staged_rects = _completed_badge_slot_rects(
        _completed_restore_staged_items(old_items, item_id),
        layout_width=layout_width,
        side=side,
    )
    new_rects = _completed_badge_slot_rects(
        new_items,
        layout_width=layout_width,
        side=side,
    )
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
    side: str = "right",
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
        rect = _find_item_rect(
            items,
            item_id,
            "card",
            layout_width=layout_width,
            side=side,
        )
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

def _transition_hides_source_before_effect_reset(transition_type: str) -> bool:
    return transition_type == "completed_dismiss"

__all__ = [
    "OverlayRect",
    "work_overlay_max_items_for_screen_height",
    "_pending_workdir_window_rect",
    "_ease_out_cubic",
    "_ease_in_out_cubic",
    "_transition_total_ms",
    "_detect_transition",
    "_transition_changes",
    "_detect_transition_item_id",
    "_defer_other_transition_items",
    "_completed_badge_slot_rects",
    "_completed_slot_rect",
    "_card_slot_rect",
    "_overlay_required_height_for_counts",
    "_overlay_window_top_y",
    "_overlay_window_x",
    "_find_item_rect",
    "_find_item_position",
    "_remembered_card_rect_for_layout",
    "_rect_center",
    "_rect_from_center",
    "_lerp",
    "_lerp_rect",
    "_circle_rect_at_rect_center",
    "_right_edge_circle_rect_for_rect",
    "_card_height_circle_rect_for_rect",
    "_card_yield_rect_for_circle_path",
    "_card_yield_delay_ms",
    "_energy_ring_rect_for_completed_rect",
    "_completed_pending_particle_state",
    "_completed_pending_launch_progress",
    "_completed_pending_launch_scale",
    "_completed_pending_finish_progress",
    "_completed_pending_caption_opacity",
    "_transition_required_height",
    "_transition_rect_for_progress",
    "_transition_slot_shift_progress",
    "_transition_clearance_offset",
    "_completed_badge_slot_moves",
    "_completed_restore_staged_items",
    "_completed_badge_restore_slot_moves",
    "_completed_badge_row_width",
    "_transition_layout_width",
    "_overlay_items_required_height",
    "_point_in_rect",
    "_point_in_inscribed_circle",
    "_overlay_hover_hit_test",
    "_transition_hides_source_before_effect_reset",
]
