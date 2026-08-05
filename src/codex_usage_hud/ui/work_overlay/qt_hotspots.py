from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import (
    QPoint,
    QPointF,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from .constants import (
    WORK_OVERLAY_CLOSE_SIZE,
    WORK_OVERLAY_HOTSPOT_HIT_ALPHA,
    WORK_OVERLAY_SWITCH_PENDING_SLOW_SECONDS,
)
from .model import (
    _clamp01,
    _interactive_hotspot_opacity,
    _item_is_background_usage,
    _workdir_link_hover_visible_for_item,
    _workdir_link_opacity_for_item,
)

widget_attrs = Qt.WidgetAttribute
focus_policy = Qt.FocusPolicy
mouse_buttons = Qt.MouseButton
alignment = Qt.AlignmentFlag
window_type = Qt.WindowType


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
        self._base_opacity = 1.0
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
        self.setToolTip(
            "确认并关闭后台用量提醒"
            if _item_is_background_usage(item)
            else "关闭气泡"
        )
        self.set_overlay_opacity(opacity)
        self.update()

    def set_overlay_opacity(self, opacity: float) -> None:
        self._base_opacity = _clamp01(opacity)
        if not self._hover:
            self.setWindowOpacity(
                _interactive_hotspot_opacity(self._base_opacity, False)
            )

    def enterEvent(self, event: object) -> None:
        self._hover = True
        self.setWindowOpacity(
            _interactive_hotspot_opacity(self._base_opacity, True)
        )
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.setWindowOpacity(
            _interactive_hotspot_opacity(self._base_opacity, False)
        )
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
        painter.drawText(
            self.rect(),
            alignment.AlignCenter,
            "✓" if _item_is_background_usage(self._item) else "×",
        )

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
        self._base_opacity = 1.0
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
        self.set_overlay_opacity(opacity)
        tooltip = str(
            item.get("targetTitle") or item.get("title") or item.get("workdir") or ""
        ).strip()
        if _item_is_background_usage(item):
            tooltip = "查看后台用量记录"
        if self._pending:
            tooltip = (tooltip + "\n" if tooltip else "") + "正在前往会话..."
        self.setToolTip(tooltip)
        self.update()

    def set_overlay_opacity(self, opacity: float) -> None:
        self._base_opacity = _clamp01(opacity)
        if not self._hover:
            self.setWindowOpacity(
                _workdir_link_opacity_for_item(
                    self._item,
                    self._base_opacity,
                    False,
                )
            )

    def enterEvent(self, event: object) -> None:
        self._hover = True
        self.setWindowOpacity(
            _workdir_link_opacity_for_item(
                self._item,
                self._base_opacity,
                True,
            )
        )
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.setWindowOpacity(
            _workdir_link_opacity_for_item(
                self._item,
                self._base_opacity,
                False,
            )
        )
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
        # Keep the post-opacity alpha non-zero so Windows layered hit-testing
        # does not drop the hotspot while the parent bubble is at 0.22 opacity.
        fill = QColor(255, 255, 255, WORK_OVERLAY_HOTSPOT_HIT_ALPHA)
        if self._hover and _workdir_link_hover_visible_for_item(self._item):
            fill = QColor(156, 203, 255, 48)
        if self._pending:
            fill = QColor(156, 203, 255, 42)
        painter.setBrush(fill)
        painter.drawRoundedRect(self.rect(), 4, 4)
        if self._hover and _workdir_link_hover_visible_for_item(self._item):
            underline = QPen(QColor(231, 242, 255, 235), 1.6)
            underline.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(underline)
            underline_y = max(1.0, float(self.height()) - 2.0)
            painter.drawLine(
                QPointF(3.0, underline_y),
                QPointF(max(3.0, float(self.width()) - 3.0), underline_y),
            )
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
        self._base_opacity = 1.0
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
        self.set_overlay_opacity(opacity)
        self.setToolTip(tooltip)
        if hover_color is not None:
            self._hover_color = QColor(hover_color)
        self.update()

    def set_overlay_opacity(self, opacity: float) -> None:
        self._base_opacity = _clamp01(opacity)
        if not self._hover:
            self.setWindowOpacity(
                _interactive_hotspot_opacity(
                    self._base_opacity,
                    False,
                    invisible_hit_surface=True,
                )
            )

    def enterEvent(self, event: object) -> None:
        self._hover = True
        self.setWindowOpacity(
            _interactive_hotspot_opacity(
                self._base_opacity,
                True,
                invisible_hit_surface=True,
            )
        )
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.setWindowOpacity(
            _interactive_hotspot_opacity(
                self._base_opacity,
                False,
                invisible_hit_surface=True,
            )
        )
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
        fill = QColor(255, 255, 255, WORK_OVERLAY_HOTSPOT_HIT_ALPHA)
        if self._hover:
            fill = QColor(self._hover_color)
            fill.setAlpha(56)
            outline = QColor(self._hover_color)
            outline.setAlpha(235)
            painter.setPen(QPen(outline, 1.8))
        painter.setBrush(fill)
        target_rect = self.rect().adjusted(1, 1, -1, -1)
        if self._circle:
            painter.drawEllipse(target_rect)
        else:
            painter.drawRoundedRect(target_rect, 6, 6)

__all__ = [
    'CloseButtonWindow',
    'WorkdirLinkWindow',
    'ClickHotspotWindow'
]
