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
WORK_OVERLAY_SHIMMER_TIMER_MS = 30
WORK_OVERLAY_SHIMMER_STEP_PX = 3.5
WORK_OVERLAY_SHIMMER_BAND_WIDTH_PX = 58
WORK_OVERLAY_SHIMMER_HIGHLIGHT = "#FFFFFF"
WORK_OVERLAY_SHIMMER_PEAK_ALPHA = 245
WORK_OVERLAY_EMPTY_GRACE_SECONDS = 0.8


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


def _color_for(status: str) -> tuple[str, str, str, str]:
    if status == "error":
        return "#FF6B6B", "#2A1013", "#1A1012", "#A63A3A"
    if status == "waiting_user":
        return "#FFB86B", "#1D1610", "#10161D", "#263241"
    if status == "tool":
        return "#9CCBFF", "#0D1722", "#10161D", "#263241"
    if status == "recent":
        return "#8FE3A1", "#0E1B14", "#0E1B14", "#2F9F55"
    return "#F3D27A", "#1C190F", "#10161D", "#263241"


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

    class CompletedBadgeWidget(QWidget):
        """Animated green circular summary for finished work."""

        def __init__(self, item: Mapping[str, object], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._item: Mapping[str, object] = dict(item)
            self._started_at = time.monotonic()
            self._progress = 0.0
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._advance)
            self.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            self.setAttribute(widget_attrs.WA_TranslucentBackground, True)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.setFixedSize(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE,
                WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
            )
            self._timer.start(16)

        def set_item(self, item: Mapping[str, object]) -> None:
            self._item = dict(item)
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
            gradient.setColorAt(0.0, QColor("#49E07D"))
            gradient.setColorAt(0.5, QColor("#1FA85A"))
            gradient.setColorAt(1.0, QColor("#0A5B35"))
            painter.setBrush(gradient)
            painter.setPen(QPen(QColor("#93F0AF"), 1.4))
            painter.drawRoundedRect(rect, radius, radius)

            if eased < 0.24:
                return

            content_alpha = int(255 * min(1.0, (eased - 0.24) / 0.42))
            painter.save()
            painter.setOpacity(content_alpha / 255.0)
            center = QPointF(end_rect.center())

            outer_pen = QPen(QColor("#B9F7C9"), 2.0)
            painter.setPen(outer_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(end_rect.adjusted(4.0, 4.0, -4.0, -4.0))

            dashed_pen = QPen(QColor(218, 255, 229, 145), 1.1)
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
                color=QColor("#E9FFF0"),
                bottom=False,
            )
            if workdir:
                self._draw_arc_text(
                    painter,
                    workdir,
                    center=center,
                    radius=73.0,
                    start_degrees=145.0,
                    end_degrees=35.0,
                    font=QFont("Microsoft YaHei UI", 9),
                    color=QColor("#BFF8D1"),
                    bottom=True,
                )

            check_font = QFont("Segoe UI Symbol", 38, QFont.Weight.Bold)
            painter.setFont(check_font)
            painter.setPen(QColor("#F8FFF9"))
            painter.drawText(
                QRectF(center.x() - 34.0, center.y() - 58.0, 68.0, 56.0),
                alignment.AlignCenter,
                "✓",
            )

            elapsed = str(self._item.get("elapsedText") or "已处理 --").strip()
            painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#F4FFF7"))
            painter.drawText(
                QRectF(center.x() - 54.0, center.y() - 2.0, 108.0, 18.0),
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
            y = center.y() + 22.0
            for index, (label, value) in enumerate(stats):
                box = QRectF(start_x + index * (box_width + spacing), y, box_width, box_height)
                painter.setPen(QPen(QColor(221, 255, 230, 105), 0.8))
                painter.setBrush(QColor(4, 57, 31, 108))
                painter.drawRoundedRect(box, 6.0, 6.0)
                painter.setPen(QColor("#EFFFF2"))
                painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
                painter.drawText(box.adjusted(1.0, 1.0, -1.0, -11.0), alignment.AlignCenter, value)
                painter.setPen(QColor("#A7EFC0"))
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
            total_width = sum(widths)
            degrees_per_px = span / max(1.0, total_width)
            direction = 1.0 if end_degrees >= start_degrees else -1.0
            current = start_degrees + direction * ((span - total_width * degrees_per_px) / 2.0)
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
                current += direction * width * degrees_per_px
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
            self._command_path = _work_overlay_command_path(path)
            self._item_limit = normalize_work_overlay_max_items(item_limit, item_limit)
            self._close_windows: list[CloseButtonWindow] = []
            self._workdir_windows: list[WorkdirLinkWindow] = []
            self._close_anchors: list[tuple[QWidget, Mapping[str, object], str, str, str]] = []
            self._workdir_anchors: list[tuple[QWidget, Mapping[str, object]]] = []
            self._item_widgets: list[dict[str, Any]] = []
            self._empty_since = 0.0
            self._layout_width = WORK_OVERLAY_WIDTH
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

        def shutdown(self) -> None:
            self.hide_overlay()
            for close_window in self._close_windows:
                close_window.close()
            self._close_windows.clear()
            for workdir_window in self._workdir_windows:
                workdir_window.close()
            self._workdir_windows.clear()
            self.close()
            app.quit()

        def poll_state(self) -> None:
            state = read_state()
            if state is None:
                self.shutdown()
                return
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
                inside_overlay = self.frameGeometry().contains(cursor_pos)
                inside_close = any(
                    window.isVisible() and window.frameGeometry().contains(cursor_pos)
                    for window in self._close_windows
                )
                inside_workdir = any(
                    window.isVisible() and window.frameGeometry().contains(cursor_pos)
                    for window in self._workdir_windows
                )
                target = (
                    hover_alpha
                    if (inside_overlay or inside_close or inside_workdir)
                    else overlay_alpha
                )
            if abs(self.windowOpacity() - target) < 0.01:
                return
            self.setWindowOpacity(target)
            for close_window in self._close_windows:
                close_window.setWindowOpacity(target)
            for workdir_window in self._workdir_windows:
                workdir_window.setWindowOpacity(target)

        @staticmethod
        def _item_identity(item: Mapping[str, object], index: int) -> str:
            item_id = str(item.get("id") or "").strip()
            return item_id or f"overlay-index-{index}"

        @staticmethod
        def _item_widget_kind(item: Mapping[str, object]) -> str:
            return "completed" if _item_is_completed(item) else "card"

        def _clear_shell(self) -> None:
            self._close_anchors.clear()
            self._workdir_anchors.clear()
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
            badge = CompletedBadgeWidget(item, parent)
            close_anchor = QWidget(badge)
            close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
            close_anchor.move(
                WORK_OVERLAY_COMPLETED_BADGE_SIZE - WORK_OVERLAY_CLOSE_SIZE - 5,
                5,
            )
            close_anchor.show()
            row_layout.addWidget(badge, 0, alignment.AlignRight)
            record = {
                "kind": "completed",
                "badge": badge,
                "close_anchor": close_anchor,
            }
            self._item_widgets.append(record)
            self._update_completed_badge(record, item)

        def _update_completed_badge(
            self,
            record: dict[str, Any],
            item: Mapping[str, object],
        ) -> None:
            badge = record["badge"]
            badge.set_item(item)
            self._close_anchors.append(
                (
                    record["close_anchor"],
                    dict(item),
                    "#0E1B14",
                    "#163B24",
                    "#B9F7C9",
                )
            )

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

            card_layout.addWidget(footer_container)
            self._shell.layout().addWidget(card, 0, alignment.AlignRight)

            record = {
                "kind": "card",
                "card": card,
                "header": header,
                "detail": detail,
                "footer_container": footer_container,
                "status_label": status_label,
                "workdir_label": workdir_label,
                "close_anchor": close_anchor,
            }
            self._item_widgets.append(record)
            self._update_item_card(record, item)

        def _update_item_card(self, record: dict[str, Any], item: Mapping[str, object]) -> None:
            status = str(item.get("status") or "")
            accent, pill_bg, card_bg, border_color = _color_for(status)
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
                f"color: {accent if status == 'recent' else '#A9B6C6'};"
                "border: none;"
                "background: transparent;"
                "}"
            )

            body_text = str(item.get("lastText") or item.get("detail") or "").strip()
            detail = record["detail"]
            detail.setText(body_text)
            detail.setMinimumHeight(self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH))
            detail.setStyleSheet(
                "QLabel {"
                "color: #B8C6D8;"
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
                status_text_color = accent if status in {"recent", "error"} else "#8492A6"
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
                    f"color: {'#9CCBFF' if workdir_clickable else '#5E6A78'};"
                    "border: none;"
                    "background: transparent;"
                    "}"
                )
                workdir_label.setVisible(True)
            else:
                workdir_label.setText("")
                workdir_label.setToolTip("")
                workdir_label.setVisible(False)

            self._close_anchors.append(
                (record["close_anchor"], dict(item), card_bg, pill_bg, accent)
            )
            if workdir_clickable:
                self._workdir_anchors.append((record["workdir_label"], dict(item)))

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
            QTimer.singleShot(0, self.reposition_interactive_windows)

        def render_items(self, items: Sequence[Mapping[str, object]]) -> None:
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
                return
            self._empty_since = 0.0
            completed_count = sum(1 for item in visible_items if _item_is_completed(item))
            self._layout_width = max(
                WORK_OVERLAY_WIDTH,
                _completed_badge_row_width(completed_count),
            )
            payload_signature = json.dumps(visible_items, ensure_ascii=False, sort_keys=True)
            if payload_signature == self._last_payload_signature:
                return
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
