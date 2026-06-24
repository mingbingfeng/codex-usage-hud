"""PySide6 standalone HUD used between renderer injection and Tk fallback."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import json
import os
import sys
import time
from typing import Any

from .. import __version__
from ..config import (
    UserConfig,
    UserConfigStore,
    dismiss_warning_for_today,
    effective_display_mode,
    fetch_model_prices,
)
from ..core import ParsedSession
from ..platforms.cdp_probe import cdp_port_from_env, list_targets, pick_page_target
from ..platforms.codex_theme import CodexThemeProbe
from ..support_assets import support_qr_asset_paths
from ..updater import check_for_update, download_update_asset, format_update_info, launch_installer
from .renderer_hud import RendererHudPayload, _renderer_theme_payload, payload_from_snapshot
from .tk_hud import (
    CodexWindowLocator,
    HUD_UIA_ROI_DEMO_ENV,
    HudSettingsStore,
    WindowRect,
    _automatic_hud_geometry,
    _env_flag,
)
from .work_overlay_qt import work_overlay_max_items_for_screen_height

QT_HUD_TOP_WIDTH = 520
QT_HUD_REQUEST_WIDTH = 380
QT_HUD_TOP_COLLAPSED_HEIGHT = 36
QT_HUD_TOP_EXPANDED_HEIGHT = 390
QT_HUD_REQUEST_COLLAPSED_HEIGHT = 32
QT_HUD_REQUEST_EXPANDED_HEIGHT = 180
QT_HUD_MARGIN = 16
QT_HUD_ANIMATION_MS = 180
QT_HUD_INTERACTION_IDLE_MS = 240
QT_HUD_CLICK_PRIORITY_MS = 180
QT_HUD_CLICK_REFRESH_DELAY_MS = 50
QT_HUD_POINTER_PRIORITY_MS = 120
QT_HUD_POINTER_REFRESH_DELAY_MS = 75
QT_HUD_TOP_STACK_WIDTH = 420
QT_HUD_SAME_HWND_RECT_JITTER_PX = 48
QT_HUD_EVENT_DOCK_ENV = "CODEX_USAGE_HUD_EVENT_DOCK"
QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT = 38
QT_HUD_ACTIVITY_TRAIL_VISIBLE_ROWS = 4
QT_HUD_FOLLOW_MS = 120
QT_COLLAPSED_PROGRESS_MARQUEE_START_PAUSE_MS = 1500
QT_COLLAPSED_PROGRESS_MARQUEE_END_PAUSE_MS = 1500
QT_COLLAPSED_PROGRESS_MARQUEE_STEP_PX = 1
QT_COLLAPSED_PROGRESS_MARQUEE_INTERVAL_MS = 30
QT_COLLAPSED_PROGRESS_TAIL_PEEK_WIDTH = 40
QT_COLLAPSED_PROGRESS_RAIL_HEIGHT = 20
QT_RESIZE_EDGE_HIT_SIZE = 8
QT_THEME_DEFAULTS: dict[str, str] = {
    "surface": "#10161D",
    "panelSurface": "#141B24",
    "panelBorder": "#3A485A",
    "headerSurface": "#202833",
    "divider": "#273241",
    "text": "#DCE7F2",
    "muted": "#8D9AAD",
    "accent": "#F3D27A",
    "info": "#9CCBFF",
    "warning": "#FFB86B",
    "error": "#FF6B6B",
    "success": "#8FE3A1",
    "requestSurface": "#0B1016",
    "requestHeaderSurface": "#151D27",
    "requestPanelSurface": "#101821",
    "requestText": "#DCE7F2",
    "requestMuted": "#718095",
    "progressTrack": "#202832",
    "progressTrackBorder": "#3B4654",
    "progressTrackText": "#E9F1F8",
    "progressCache": "#5EA7FF",
    "progressDay": "#F3D27A",
    "progressWeek": "#B5DD92",
    "progressOverflow": "#FF875A",
}

try:  # pragma: no cover - exercised through QtHudWindow construction.
    from PySide6.QtCore import QAbstractAnimation, QEvent, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, Signal, Slot
    from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPaintEvent, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFrame,
        QGridLayout,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QLayout,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizeGrip,
        QSizePolicy,
        QStackedLayout,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    _QT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on optional GUI runtime.
    QApplication = None  # type: ignore[assignment]
    _QT_IMPORT_ERROR = exc


def _compact(value: object, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _metric_signature(metrics: Sequence[Mapping[str, object]]) -> str:
    return json.dumps(list(metrics), ensure_ascii=False, sort_keys=True)


if QApplication is not None:

    class _HudLabel(QLabel):
        def __init__(
            self,
            text: str = "",
            *,
            role: str = "body",
            wrap: bool = False,
        ) -> None:
            super().__init__(text)
            self.setObjectName(f"qtHudLabel-{role}")
            self.setMinimumWidth(0)
            self.setMinimumHeight(max(12, self.fontMetrics().height()))
            compressible_roles = {"body", "muted", "mono-blue", "activity-detail", "request"}
            horizontal_policy = (
                QSizePolicy.Policy.Ignored
                if role in compressible_roles
                else QSizePolicy.Policy.Minimum
            )
            self.setSizePolicy(horizontal_policy, QSizePolicy.Policy.Minimum)
            self.setWordWrap(wrap)
            self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            self._copy_text = ""
            self._elided_text = ""
            self._elided_expands_hint = role == "title"

        def set_elided_text(self, value: object, *, limit: int = 220) -> None:
            self._elided_text = _compact(value, limit)
            self._sync_elided_text()
            self.updateGeometry()

        def _sync_elided_text(self) -> None:
            metrics = self.fontMetrics()
            width = max(40, self.width() or super().sizeHint().width() or 120)
            self.setText(metrics.elidedText(self._elided_text, Qt.TextElideMode.ElideRight, width))
            self.setToolTip(self._elided_text)

        def resizeEvent(self, event: Any) -> None:  # noqa: N802
            if self._elided_text:
                self._sync_elided_text()
            super().resizeEvent(event)

        def sizeHint(self) -> QSize:  # noqa: N802
            hint = super().sizeHint()
            if self._elided_expands_hint and self._elided_text:
                width = self.fontMetrics().horizontalAdvance(self._elided_text) + 2
                hint.setWidth(max(hint.width(), width))
            return hint

        def set_copy_text(self, value: object, *, tooltip: str = "") -> None:
            text = str(value or "").strip()
            self._copy_text = text
            if text:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip(tooltip or f"点击复制\n{text}")
            else:
                self.unsetCursor()

        def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton and self._copy_text:
                QApplication.clipboard().setText(self._copy_text)
                event.accept()
                return
            super().mousePressEvent(event)


    class _HudSizeGrip(QSizeGrip):
        def __init__(self, parent: QWidget, on_resize_finished: Callable[[], None]) -> None:
            super().__init__(parent)
            self._on_resize_finished = on_resize_finished
            self._pressed = False

        def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            self._pressed = True
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            was_pressed = self._pressed
            self._pressed = False
            super().mouseReleaseEvent(event)
            if was_pressed:
                QTimer.singleShot(0, self._on_resize_finished)


    class _SettingsComboBox(QComboBox):
        def wheelEvent(self, event: object) -> None:  # noqa: N802
            if self.view().isVisible():
                super().wheelEvent(event)
                return
            event.ignore()


    class _ProgressRail(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setMinimumHeight(18)
            self.setMaximumHeight(20)
            self._metric: dict[str, object] = {}
            self._theme: dict[str, str] = dict(QT_THEME_DEFAULTS)

        def set_metric(self, metric: Mapping[str, object] | None) -> None:
            self._metric = dict(metric or {})
            self.setVisible(bool(self._metric))
            self.setToolTip(self._tooltip())
            self.update()

        def _tooltip(self) -> str:
            label = str(self._metric.get("label") or "")
            right_text = str(self._metric.get("rightText") or "")
            overflow_badge = str(self._metric.get("overflowBadge") or "")
            full_text = f"{label} / {right_text}" if right_text else label
            return f"{full_text} | {overflow_badge}" if overflow_badge else full_text

        def set_theme(self, tokens: Mapping[str, str]) -> None:
            self._theme.update({str(key): str(value) for key, value in tokens.items()})
            self.update()

        def preferred_width(self) -> int:
            font = QFont(self.font())
            font.setPointSize(max(8, font.pointSize()))
            font.setBold(True)
            metrics = QFontMetrics(font)
            label = str(self._metric.get("label") or "")
            right_text = str(self._metric.get("rightText") or "")
            overflow_badge = str(self._metric.get("overflowBadge") or "")
            right_width = max(42, metrics.horizontalAdvance(right_text) + 8) if right_text else 0
            text_gap = 8
            overflow_width = 0
            if overflow_badge:
                overflow_width = max(64, metrics.horizontalAdvance(overflow_badge) + 24)
            elif float(self._metric.get("overflowRatio") or 0.0) > 0.0:
                overflow_width = 24
            return metrics.horizontalAdvance(label) + right_width + text_gap + overflow_width + 20

        def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
            del event
            if not self._metric:
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(0, 1, 0, -1)
            radius = rect.height() // 2
            track = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            track.setColorAt(0.0, QColor(255, 255, 255, 10))
            track.setColorAt(1.0, QColor(self._theme.get("progressTrack", "#202832")))
            painter.setPen(QPen(QColor(self._theme.get("progressTrackBorder", "#3B4654")), 1))
            painter.setBrush(track)
            painter.drawRoundedRect(rect, radius, radius)

            tone = str(self._metric.get("tone") or "session")
            colors = {
                "cache": (self._theme.get("info", "#9CCBFF"), self._theme.get("progressCache", "#5EA7FF")),
                "session": (self._theme.get("info", "#9CCBFF"), self._theme.get("progressCache", "#5EA7FF")),
                "day": (self._theme.get("progressDay", "#F3D27A"), self._theme.get("progressDay", "#F3D27A")),
                "week": (self._theme.get("progressWeek", "#B5DD92"), self._theme.get("success", "#8FE3A1")),
                "error": (self._theme.get("warning", "#FFB86B"), self._theme.get("error", "#FF6B6B")),
            }
            start_color, end_color = colors.get(tone, ("#9CCBFF", "#5EA7FF"))
            fill = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
            fill.setColorAt(0.0, QColor(start_color))
            fill.setColorAt(1.0, QColor(end_color))
            ratio = max(0.0, min(1.0, float(self._metric.get("ratio") or 0.0)))
            fill_width = max(0, int(rect.width() * ratio))
            if fill_width > 0:
                fill_rect = QRect(rect.left(), rect.top(), fill_width, rect.height())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(fill_rect, radius, radius)
                gloss = QLinearGradient(fill_rect.left(), fill_rect.top(), fill_rect.left(), fill_rect.bottom())
                gloss.setColorAt(0.0, QColor(255, 255, 255, 66))
                gloss.setColorAt(0.45, QColor(255, 255, 255, 18))
                gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.setBrush(gloss)
                painter.drawRoundedRect(fill_rect, radius, radius)

            overflow_ratio = max(0.0, min(1.0, float(self._metric.get("overflowRatio") or 0.0)))
            overflow_badge = str(self._metric.get("overflowBadge") or "")
            if overflow_ratio > 0.0:
                painter.setPen(QPen(QColor(self._theme.get("progressOverflow", "#FF875A")), 1))
                overflow_left = rect.left() + int(rect.width() * (1.0 - overflow_ratio))
                overflow_rect = QRect(
                    overflow_left,
                    rect.top() + 4,
                    max(10, rect.right() - overflow_left - 5),
                    max(7, rect.height() - 8),
                )
                overflow = QLinearGradient(overflow_rect.left(), overflow_rect.top(), overflow_rect.right(), overflow_rect.top())
                overflow.setColorAt(0.0, QColor(255, 207, 170))
                overflow.setColorAt(0.65, QColor(self._theme.get("progressOverflow", "#FF875A")))
                overflow.setColorAt(1.0, QColor(self._theme.get("error", "#FF6B6B")))
                painter.setBrush(overflow)
                painter.drawRoundedRect(overflow_rect, overflow_rect.height() // 2, overflow_rect.height() // 2)
                anchor_size = min(16, max(10, rect.height() - 4))
                anchor_rect = QRect(
                    rect.right() - anchor_size - 4,
                    rect.center().y() - anchor_size // 2,
                    anchor_size,
                    anchor_size,
                )
                painter.setBrush(QColor(self._theme.get("progressOverflow", "#FF875A")))
                painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
                painter.drawEllipse(anchor_rect)

            text = str(self._metric.get("label") or "")
            right_text = str(self._metric.get("rightText") or "")
            painter.setPen(QColor(self._theme.get("progressTrackText", "#E9F1F8")))
            font = QFont(self.font())
            font.setPointSize(max(8, font.pointSize()))
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            badge_width = 0
            if overflow_badge:
                badge_width = min(max(64, metrics.horizontalAdvance(overflow_badge) + 24), max(72, rect.width() // 2))
                badge_rect = QRect(
                    rect.right() - badge_width - 8,
                    rect.top() + max(1, (rect.height() - 22) // 2),
                    badge_width,
                    min(22, rect.height() - 2),
                )
                painter.setPen(QPen(QColor(self._theme.get("progressOverflow", "#FF875A")), 1))
                painter.setBrush(QColor(255, 95, 92, 34))
                painter.drawRoundedRect(badge_rect, badge_rect.height() // 2, badge_rect.height() // 2)
                painter.setPen(QColor(255, 215, 202))
                dot_size = 6
                dot_rect = QRect(
                    badge_rect.left() + 9,
                    badge_rect.center().y() - dot_size // 2,
                    dot_size,
                    dot_size,
                )
                painter.setBrush(QColor(self._theme.get("progressOverflow", "#FF875A")))
                painter.drawEllipse(dot_rect)
                painter.drawText(
                    badge_rect.adjusted(20, 0, -8, 0),
                    Qt.AlignmentFlag.AlignVCenter,
                    metrics.elidedText(overflow_badge, Qt.TextElideMode.ElideRight, badge_width - 30),
                )

            painter.setPen(QColor(self._theme.get("progressTrackText", "#E9F1F8")))
            text_rect = rect.adjusted(10, 0, -(10 + badge_width), 0)
            right_width = min(max(42, metrics.horizontalAdvance(right_text) + 8), max(42, text_rect.width() // 2)) if right_text else 0
            left_text = metrics.elidedText(
                text,
                Qt.TextElideMode.ElideRight,
                max(30, text_rect.width() - right_width - 8),
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, left_text)
            if right_text:
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    metrics.elidedText(right_text, Qt.TextElideMode.ElideRight, right_width),
                )


    class _ActivityMarker(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setFixedSize(24, 38)
            self._top_line = QFrame(self)
            self._top_line.setObjectName("qtHudActivityMarkerLine")
            self._bottom_line = QFrame(self)
            self._bottom_line.setObjectName("qtHudActivityMarkerLine")
            self._dot = QFrame(self)
            self._dot.setObjectName("qtHudActivityMarkerDot")
            self._dot.setFixedSize(8, 8)
            self._sync_geometry()

        def resizeEvent(self, event: Any) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._sync_geometry()

        def set_state(self, index: int, total: int, active: bool) -> None:
            total = max(1, int(total))
            del index, total
            self._top_line.setVisible(False)
            self._bottom_line.setVisible(False)
            self._sync_geometry()
            self._dot.setProperty("active", "true" if active else "false")
            self._dot.style().unpolish(self._dot)
            self._dot.style().polish(self._dot)

        def _sync_geometry(self) -> None:
            center_x = self.width() // 2
            center_y = self.height() // 2
            dot_radius = self._dot.height() // 2
            line_gap = 1
            top_bottom = max(0, center_y - dot_radius - line_gap)
            bottom_top = min(self.height(), center_y + dot_radius + line_gap)
            self._top_line.setGeometry(center_x - 1, 0, 2, top_bottom)
            self._bottom_line.setGeometry(center_x - 1, bottom_top, 2, max(0, self.height() - bottom_top))
            self._dot.move(center_x - dot_radius, center_y - dot_radius)


    class _TopCollapsedProgressStrip(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setMinimumHeight(24)
            self.setMaximumHeight(28)
            self._base_widths = (72, 68, 76)
            self._column_gap = 7
            self._scroll_x = 0.0
            self._scroll_min_x = 0.0
            self._scroll_max_x = 0.0
            self._scroll_direction = -1
            self._scrolling_enabled = False
            self._layout_signature = ""
            self._metrics: list[dict[str, object]] = []
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._scroll_step)
            self._content = QWidget(self)
            self._content.setObjectName("qtHudTopCollapsedProgressContent")
            self._rails: list[_ProgressRail] = []
            for index in range(3):
                rail = _ProgressRail(self._content)
                rail.setGeometry(0, 0, self._base_widths[index], 24)
                self._rails.append(rail)

        @property
        def rails(self) -> list[_ProgressRail]:
            return self._rails

        @property
        def scrolling_enabled(self) -> bool:
            return self._scrolling_enabled

        def set_metrics(self, metrics: Sequence[Mapping[str, object]]) -> None:
            self._metrics = [dict(item) for item in metrics[:3]]
            for index, rail in enumerate(self._rails):
                rail.set_metric(self._metrics[index] if index < len(self._metrics) else None)
            self._layout_for_current_metrics()

        def set_theme(self, tokens: Mapping[str, str]) -> None:
            for rail in self._rails:
                rail.set_theme(tokens)

        def resizeEvent(self, event: Any) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._layout_for_current_metrics()

        def hideEvent(self, event: Any) -> None:  # noqa: N802
            super().hideEvent(event)
            self._stop_scrolling()

        def _visible_count(self) -> int:
            return sum(1 for rail in self._rails if bool(rail._metric))

        def _available_width(self) -> int:
            return max(1, int(self.width()))

        def _rail_height(self) -> int:
            return max(18, min(QT_COLLAPSED_PROGRESS_RAIL_HEIGHT, max(1, int(self.height()))))

        def _rail_y(self) -> int:
            return max(0, (int(self.height()) - self._rail_height()) // 2)

        def _preferred_widths(self) -> list[int]:
            return [
                max(self._base_widths[index], rail.preferred_width())
                for index, rail in enumerate(self._rails)
                if bool(rail._metric)
            ]

        def _should_scroll(self, widths: Sequence[int], available_width: int) -> bool:
            if len(widths) <= 1:
                return False
            gap = max(0, self._column_gap)
            required = sum(max(0, int(width)) for width in widths) + gap * (len(widths) - 1)
            if required <= max(1, int(available_width)) + 1:
                return False
            remaining_after_session = max(0, int(available_width) - int(widths[0]) - (gap * (len(widths) - 1)))
            tail_share = remaining_after_session / max(1, len(widths) - 1)
            return tail_share <= QT_COLLAPSED_PROGRESS_TAIL_PEEK_WIDTH

        def _layout_for_current_metrics(self) -> None:
            if self._visible_count() <= 1:
                self._layout_single_metric()
                return
            self._layout_multi_metric()

        def _layout_multi_metric(self) -> None:
            widths = self._preferred_widths()
            if len(widths) < 2:
                self._layout_single_metric()
                return
            available_width = self._available_width()
            gap_total = self._column_gap * max(0, len(widths) - 1)
            required_width = sum(widths) + gap_total
            should_scroll = self._should_scroll(widths, available_width)
            if should_scroll:
                column_widths = widths
                content_width = required_width
            else:
                tail_count = len(widths) - 1
                tail_width = max(1, (available_width - widths[0] - gap_total) // tail_count)
                column_widths = [widths[0]] + [tail_width] * tail_count
                content_width = available_width
            self._position_rails(column_widths)
            self._content.setGeometry(int(round(self._scroll_x)), 0, max(1, content_width), self.height())
            if not should_scroll:
                self._layout_signature = ""
                self._stop_scrolling()
                return
            signature = f"{','.join(str(width) for width in widths)}|{required_width}|{available_width}"
            if self._layout_signature != signature or not self._scrolling_enabled:
                self._layout_signature = signature
                self._start_scrolling(required_width, available_width)

        def _layout_single_metric(self) -> None:
            available_width = self._available_width()
            has_first_metric = bool(self._rails[0]._metric)
            first_width = max(self._base_widths[0], self._rails[0].preferred_width()) if has_first_metric else available_width
            self._rails[0].setGeometry(0, self._rail_y(), max(first_width, available_width), self._rail_height())
            self._rails[0].setVisible(has_first_metric)
            for rail in self._rails[1:]:
                rail.setVisible(False)
            self._content.setGeometry(0, 0, available_width, self.height())
            self._layout_signature = ""
            self._stop_scrolling()

        def _position_rails(self, widths: Sequence[int]) -> None:
            x = 0
            visible_index = 0
            for rail in self._rails:
                if not rail._metric:
                    rail.setVisible(False)
                    continue
                rail.setVisible(True)
                width = int(widths[visible_index])
                rail.setGeometry(x, self._rail_y(), width, self._rail_height())
                x += width + self._column_gap
                visible_index += 1

        def _set_content_offset(self, x: float) -> None:
            self._scroll_x = float(x)
            self._content.move(int(round(self._scroll_x)), 0)

        def _stop_scrolling(self) -> None:
            self._scrolling_enabled = False
            self._timer.stop()
            self._set_content_offset(0.0)

        def _start_scrolling(self, required_width: int, available_width: int) -> None:
            overflow = max(0, int(required_width) - int(available_width))
            if overflow <= 0 or not self.isVisible():
                self._stop_scrolling()
                return
            self._scrolling_enabled = True
            self._scroll_max_x = 0.0
            self._scroll_min_x = float(-overflow)
            self._scroll_direction = -1
            self._set_content_offset(self._scroll_max_x)
            self._timer.start(QT_COLLAPSED_PROGRESS_MARQUEE_START_PAUSE_MS)

        def _scroll_step(self) -> None:
            if not self.isVisible() or not self._scrolling_enabled:
                return
            next_x = self._scroll_x + (QT_COLLAPSED_PROGRESS_MARQUEE_STEP_PX * self._scroll_direction)
            if self._scroll_direction < 0 and next_x <= self._scroll_min_x:
                self._set_content_offset(self._scroll_min_x)
                self._scroll_direction = 1
                self._timer.start(QT_COLLAPSED_PROGRESS_MARQUEE_END_PAUSE_MS)
                return
            if self._scroll_direction > 0 and next_x >= self._scroll_max_x:
                self._set_content_offset(self._scroll_max_x)
                self._scroll_direction = -1
                self._timer.start(QT_COLLAPSED_PROGRESS_MARQUEE_START_PAUSE_MS)
                return
            self._set_content_offset(next_x)
            self._timer.start(QT_COLLAPSED_PROGRESS_MARQUEE_INTERVAL_MS)


    class _PanelWindow(QWidget):
        def __init__(
            self,
            *,
            target: str,
            width: int,
            collapsed_height: int,
            expanded_height: int,
            on_interaction: Callable[[], None],
            on_click_priority: Callable[[], None] | None = None,
            on_pointer_priority: Callable[[], None] | None = None,
            on_geometry_changed: Callable[[str, "_PanelWindow", str], None] | None = None,
            on_pin_toggle: Callable[[str], None] | None = None,
            grow_from_bottom: bool = False,
        ) -> None:
            super().__init__()
            self._target = str(target)
            self._collapsed_height = int(collapsed_height)
            self._expanded_height = int(expanded_height)
            self._grow_from_bottom = bool(grow_from_bottom)
            self._expanded = False
            self._drag_origin: QPoint | None = None
            self._drag_window_origin: QPoint | None = None
            self._dragging = False
            self._toggle_press_position: QPoint | None = None
            self._toggle_press_global: QPoint | None = None
            self._resize_edge = ""
            self._resize_origin: QPoint | None = None
            self._resize_start_geometry: QRect | None = None
            self._resizing = False
            self._manual_positioned = False
            self._collapsed_geometry: QRect | None = None
            self._on_interaction = on_interaction
            self._on_click_priority = on_click_priority or on_interaction
            self._on_pointer_priority = on_pointer_priority or on_interaction
            self._on_geometry_changed = on_geometry_changed
            self._on_pin_toggle = on_pin_toggle
            self._pin_buttons: list[QPushButton] = []
            self._animation: QPropertyAnimation | None = None
            self._stack = QStackedLayout()

            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            if not sys.platform.startswith("win"):
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setMouseTracking(True)
            self.setMinimumWidth(min(width, 320))
            self.resize(width, self._collapsed_height)
            self.setFixedHeight(self._collapsed_height)
            self._sync_window_opacity()

            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(0, 0, 0, 0)
            self.shell = QFrame()
            self.shell.setObjectName("qtHudShell")
            self.shell.setProperty("target", self._target)
            self.shell.setProperty("expanded", "false")
            self.shell.setFrameShape(QFrame.Shape.NoFrame)
            root_layout.addWidget(self.shell)
            shell_layout = QVBoxLayout(self.shell)
            shell_layout.setContentsMargins(10, 4, 10, 4)
            shell_layout.addLayout(self._stack)
            self._grip = _HudSizeGrip(self.shell, self._resize_finished)
            self._grip.setFixedSize(14, 14)
            self._sync_resize_grip_visibility()
            self._install_resize_cursor_tracking(self)

        def _sync_window_opacity(self) -> None:
            self.setWindowOpacity(1.0 if self._target == "top" and self._expanded else 0.96)

        def _sync_resize_grip_visibility(self) -> None:
            self._grip.setVisible(self._expanded)

        @property
        def expanded(self) -> bool:
            return self._expanded

        def set_pages(self, collapsed: QWidget, expanded: QWidget) -> None:
            self._stack.addWidget(collapsed)
            self._stack.addWidget(expanded)
            self._stack.setCurrentIndex(0)

        def _pin_button(self) -> QPushButton:
            button = QPushButton("📍")
            button.setObjectName("qtHudIconButton")
            button.clicked.connect(lambda _checked=False: self._emit_pin_toggle())
            self._pin_buttons.append(button)
            self.set_pinned(False)
            return button

        def _emit_pin_toggle(self) -> None:
            self._on_interaction()
            if self._on_pin_toggle is not None:
                self._on_pin_toggle(self._target)

        def set_pinned(self, pinned: bool) -> None:
            for button in self._pin_buttons:
                button.setText("📌" if pinned else "📍")
                button.setToolTip(
                    "取消钉住并自动跟随" if pinned else "钉住此 HUD 位置"
                )

        def toggle_expanded(self) -> None:
            self.set_expanded(not self._expanded)

        def set_expanded(self, expanded: bool) -> None:
            expanded = bool(expanded)
            if expanded == self._expanded and self.height() == self._target_height(expanded):
                return
            self._on_interaction()
            self._expanded = expanded
            self._sync_resize_grip_visibility()
            self.shell.setProperty("expanded", "true" if expanded else "false")
            self.shell.style().unpolish(self.shell)
            self.shell.style().polish(self.shell)
            self._sync_window_opacity()
            self._stack.setCurrentIndex(1 if expanded else 0)
            self.setMinimumHeight(1)
            self.setMaximumHeight(16777215)
            start = self.geometry()
            if expanded:
                self._collapsed_geometry = QRect(start)
            target_height = self._target_height(expanded)
            if self._grow_from_bottom:
                if expanded:
                    collapsed = self._collapsed_geometry or start
                    target_y = collapsed.y() - (target_height - self._collapsed_height)
                    target = QRect(collapsed.x(), target_y, start.width(), target_height)
                else:
                    collapsed = self._collapsed_geometry
                    if collapsed is not None:
                        target = QRect(start.x(), collapsed.y(), start.width(), self._collapsed_height)
                    else:
                        target = QRect(start.x(), start.bottom() - target_height + 1, start.width(), target_height)
            else:
                target = QRect(start.x(), start.y(), start.width(), target_height)
            if self._animation is not None:
                self._animation.stop()
            self._animation = QPropertyAnimation(self, b"geometry")
            self._animation.setDuration(QT_HUD_ANIMATION_MS)
            self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._animation.setStartValue(start)
            self._animation.setEndValue(target)
            self._after_page_switch(expanded)
            self._animation.finished.connect(lambda expanded=expanded: self._settle_height(expanded))
            self._animation.start()

        def _after_page_switch(self, expanded: bool) -> None:
            del expanded

        def _target_height(self, expanded: bool) -> int:
            return self._expanded_height if expanded else self._collapsed_height

        def _settle_height(self, expanded: bool) -> None:
            if expanded:
                self.setFixedHeight(self._expanded_height)
            else:
                self.setFixedHeight(self._collapsed_height)
                self._collapsed_geometry = None

        def geometry_interaction_active(self) -> bool:
            if self._drag_origin is not None or self._dragging or self._resizing:
                return True
            if bool(getattr(self._grip, "_pressed", False)):
                return True
            return (
                self._animation is not None
                and self._animation.state() == QAbstractAnimation.State.Running
            )

        def _resize_edge_at(self, position: QPoint) -> str:
            x = int(position.x())
            y = int(position.y())
            width = max(1, int(self.width()))
            height = max(1, int(self.height()))
            edge = QT_RESIZE_EDGE_HIT_SIZE
            if self._grow_from_bottom and self._expanded and 2 <= x < max(2, width - 2) and 0 <= y < edge:
                return "top"
            if not (2 <= y < max(2, height - 2)):
                return ""
            if 2 <= x < 2 + edge:
                return "left"
            if max(2, width - edge) <= x < max(2, width - 2):
                return "right"
            return ""

        def _minimum_expanded_height(self) -> int:
            return 240 if self._target == "top" else 120

        @staticmethod
        def _resize_cursor(edge: str) -> Qt.CursorShape:
            if edge in {"left", "right"}:
                return Qt.CursorShape.SizeHorCursor
            if edge == "top":
                return Qt.CursorShape.SizeVerCursor
            return Qt.CursorShape.ArrowCursor

        def _install_resize_cursor_tracking(self, widget: QWidget) -> None:
            if widget is not self and widget.window() is not self:
                return
            if not bool(widget.property("qtHudResizeCursorTracking")):
                widget.setProperty("qtHudResizeCursorTracking", True)
                widget.setMouseTracking(True)
                widget.installEventFilter(self)
            for child in widget.findChildren(QWidget):
                self._install_resize_cursor_tracking(child)

        def _sync_resize_cursor(self, position: QPoint) -> None:
            if self._resizing:
                return
            edge = self._resize_edge_at(position)
            self._apply_resize_cursor(edge)

        def _sync_resize_cursor_from_widget(self, widget: QWidget, position: QPoint) -> None:
            if self._resizing:
                return
            edge = self._resize_edge_at(widget.mapTo(self, position))
            self._apply_resize_cursor(edge, widget)

        def _apply_resize_cursor(self, edge: str, widget: QWidget | None = None) -> None:
            cursor = self._resize_cursor(edge)
            self.setCursor(cursor)
            if widget is None:
                return
            if edge:
                widget.setCursor(cursor)
                widget.setProperty("qtHudResizeCursorOverride", True)
            elif bool(widget.property("qtHudResizeCursorOverride")):
                widget.unsetCursor()
                widget.setProperty("qtHudResizeCursorOverride", False)

        def _clear_resize_cursor_override(self, widget: QWidget) -> None:
            if bool(widget.property("qtHudResizeCursorOverride")):
                widget.unsetCursor()
                widget.setProperty("qtHudResizeCursorOverride", False)

        def _start_edge_resize(self, edge: str, global_position: QPoint) -> None:
            self._on_interaction()
            if edge == "top":
                self.setMinimumHeight(1)
                self.setMaximumHeight(16777215)
            self._resize_edge = edge
            self._resize_origin = global_position
            self._resize_start_geometry = self.geometry()
            self._resizing = True
            self.setCursor(self._resize_cursor(edge))

        def _apply_edge_resize(self, global_position: QPoint) -> bool:
            if not self._resizing or self._resize_origin is None or self._resize_start_geometry is None:
                return False
            delta = global_position - self._resize_origin
            minimum_width = max(120, int(self.minimumWidth()))
            geometry = QRect(self._resize_start_geometry)
            if self._resize_edge == "left":
                new_width = max(minimum_width, self._resize_start_geometry.width() - delta.x())
                geometry.setX(self._resize_start_geometry.right() - new_width + 1)
                geometry.setWidth(new_width)
            elif self._resize_edge == "right":
                geometry.setWidth(max(minimum_width, self._resize_start_geometry.width() + delta.x()))
            elif self._resize_edge == "top":
                minimum_height = self._minimum_expanded_height() if self._expanded else self._collapsed_height
                new_height = max(minimum_height, self._resize_start_geometry.height() - delta.y())
                geometry.setY(self._resize_start_geometry.bottom() - new_height + 1)
                geometry.setHeight(new_height)
            else:
                return False
            self.setGeometry(geometry)
            return True

        def _finish_edge_resize(self, position: QPoint) -> None:
            was_top_resize = self._resize_edge == "top"
            self._resizing = False
            self._resize_edge = ""
            self._resize_origin = None
            self._resize_start_geometry = None
            if was_top_resize and self._expanded:
                self._expanded_height = max(self._minimum_expanded_height(), int(self.height()))
                self.setFixedHeight(self._expanded_height)
            self._emit_geometry_changed("resize")
            self._sync_resize_cursor(position)

        def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
            if isinstance(watched, QWidget):
                event_type = event.type()
                if event_type == QEvent.Type.ChildAdded:
                    self._install_resize_cursor_tracking(watched)
                elif event_type == QEvent.Type.Wheel:
                    self._on_pointer_priority()
                elif isinstance(event, QMouseEvent):
                    position = watched.mapTo(self, event.position().toPoint())
                    if event_type in {
                        QEvent.Type.MouseButtonPress,
                        QEvent.Type.MouseButtonRelease,
                    }:
                        self._on_click_priority()
                    elif event_type == QEvent.Type.MouseMove:
                        self._on_pointer_priority()
                    if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                        global_position = event.globalPosition().toPoint()
                        if self._should_toggle_from_click(position):
                            self._toggle_press_position = position
                            self._toggle_press_global = global_position
                        else:
                            self._toggle_press_position = None
                            self._toggle_press_global = None
                        edge = self._resize_edge_at(position)
                        if edge:
                            self._start_edge_resize(edge, global_position)
                            event.accept()
                            return True
                    if event_type == QEvent.Type.MouseMove:
                        if self._apply_edge_resize(event.globalPosition().toPoint()):
                            event.accept()
                            return True
                        self._sync_resize_cursor_from_widget(watched, event.position().toPoint())
                    if event_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and self._resizing:
                        self._finish_edge_resize(position)
                        event.accept()
                        return True
                    if event_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                        if self._finish_toggle_click(event.globalPosition().toPoint(), position):
                            event.accept()
                            return True
                if event_type == QEvent.Type.Leave and not self._resizing:
                    self._clear_resize_cursor_override(watched)
                    self.unsetCursor()
            return super().eventFilter(watched, event)

        def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                position = event.position().toPoint()
                edge = self._resize_edge_at(position)
                if edge:
                    self._start_edge_resize(edge, event.globalPosition().toPoint())
                    event.accept()
                    return
                global_position = event.globalPosition().toPoint()
                if self._should_toggle_from_click(position):
                    self._toggle_press_position = position
                    self._toggle_press_global = global_position
                else:
                    self._toggle_press_position = None
                    self._toggle_press_global = None
                if self._should_start_drag_from_click(position):
                    self._on_interaction()
                    self._drag_origin = global_position
                    self._drag_window_origin = self.pos()
                    self._dragging = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            if self._apply_edge_resize(event.globalPosition().toPoint()):
                event.accept()
                return
            if self._drag_origin is not None and self._drag_window_origin is not None:
                delta = event.globalPosition().toPoint() - self._drag_origin
                if abs(delta.x()) > 3 or abs(delta.y()) > 3:
                    self._dragging = True
                    self.move(self._drag_window_origin + delta)
            else:
                self._sync_resize_cursor(event.position().toPoint())
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                if self._resizing:
                    self._finish_edge_resize(event.position().toPoint())
                    event.accept()
                    return
                if self._dragging:
                    self._manual_positioned = True
                    self._emit_geometry_changed("move")
                if self._finish_toggle_click(event.globalPosition().toPoint(), event.position().toPoint()):
                    event.accept()
                    return
            super().mouseReleaseEvent(event)

        def _finish_toggle_click(self, release_global: QPoint, release_position: QPoint) -> bool:
            press_position = self._toggle_press_position
            press_global = self._toggle_press_global
            clicked = not self._dragging
            if press_global is not None:
                delta = release_global - press_global
                clicked = clicked and abs(delta.x()) <= 3 and abs(delta.y()) <= 3
            self._drag_origin = None
            self._drag_window_origin = None
            self._dragging = False
            self._toggle_press_position = None
            self._toggle_press_global = None
            if press_position is None:
                return False
            if clicked and self._should_toggle_from_click(press_position):
                self.toggle_expanded()
                return True
            return False

        def _panel_header_at(self, position: QPoint) -> QWidget | None:
            child = self.childAt(position)
            while child is not None and child is not self:
                if child.objectName() in {"qtHudPanelHeader", "qtHudRequestExpandedHeader"}:
                    return child
                child = child.parentWidget()
            return None

        def _toggle_target_at(self, position: QPoint) -> bool:
            child = self.childAt(position)
            while child is not None and child is not self:
                if isinstance(child, QPushButton):
                    return False
                child = child.parentWidget()
            return True

        def _should_start_drag_from_click(self, position: QPoint) -> bool:
            return self._toggle_target_at(position) and (
                not self._expanded or self._panel_header_at(position) is not None
            )

        def _should_toggle_from_click(self, position: QPoint) -> bool:
            return self._toggle_target_at(position) and (
                not self._expanded or self._panel_header_at(position) is not None
            )

        def leaveEvent(self, event: Any) -> None:  # noqa: N802
            if not self._resizing:
                self.unsetCursor()
            super().leaveEvent(event)

        def resizeEvent(self, event: Any) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._grip.move(
                max(0, self.shell.width() - self._grip.width() - 2),
                max(0, self.shell.height() - self._grip.height() - 2),
            )

        def _resize_finished(self) -> None:
            self._on_interaction()
            self._emit_geometry_changed("resize")

        def _emit_geometry_changed(self, reason: str) -> None:
            if self._on_geometry_changed is not None:
                self._on_geometry_changed(self._target, self, reason)


    class _TopPanel(_PanelWindow):
        def __init__(
            self,
            *,
            width: int = QT_HUD_TOP_WIDTH,
            expanded_height: int = QT_HUD_TOP_EXPANDED_HEIGHT,
            on_settings: Callable[[], None],
            on_update_action: Callable[[], None],
            on_dismiss_warnings: Callable[[], None],
            on_interaction: Callable[[], None],
            on_click_priority: Callable[[], None] | None = None,
            on_pointer_priority: Callable[[], None] | None = None,
            on_geometry_changed: Callable[[str, _PanelWindow, str], None] | None = None,
            on_pin_toggle: Callable[[str], None] | None = None,
        ) -> None:
            super().__init__(
                target="top",
                width=width,
                collapsed_height=QT_HUD_TOP_COLLAPSED_HEIGHT,
                expanded_height=expanded_height,
                on_interaction=on_interaction,
                on_click_priority=on_click_priority,
                on_pointer_priority=on_pointer_priority,
                on_geometry_changed=on_geometry_changed,
                on_pin_toggle=on_pin_toggle,
            )
            self._on_settings = on_settings
            self._on_update_action = on_update_action
            self._on_dismiss_warnings = on_dismiss_warnings
            self._collapsed_progress: list[_ProgressRail] = []
            self._collapsed_strip: _TopCollapsedProgressStrip | None = None
            self._budget_progress: list[_ProgressRail] = []
            self._heavy_rows: list[tuple[_HudLabel, _HudLabel]] = []
            self._activity_rows: list[tuple[_HudLabel, _ActivityMarker, _HudLabel, _HudLabel]] = []
            self._activity_signature = ""
            self._activity_trail: list[Mapping[str, object]] = []
            self._activity_visible_count = 4
            self._top_body: QFrame | None = None
            self._top_grid: QGridLayout | None = None
            self._top_left: QFrame | None = None
            self._top_right: QFrame | None = None
            self._top_layout_stacked: bool | None = None
            self._build()

        def _build(self) -> None:
            collapsed = QFrame()
            collapsed.setObjectName("qtHudTopCollapsed")
            collapsed_layout = QHBoxLayout(collapsed)
            collapsed_layout.setContentsMargins(0, 0, 0, 0)
            collapsed_layout.setSpacing(8)
            collapsed_layout.addWidget(self._pin_button())
            self._collapsed_strip = _TopCollapsedProgressStrip()
            collapsed_layout.addWidget(self._collapsed_strip, 1, Qt.AlignmentFlag.AlignVCenter)
            self._collapsed_progress = self._collapsed_strip.rails
            collapsed_settings = QPushButton("⚙")
            collapsed_settings.setObjectName("qtHudIconButton")
            collapsed_settings.setToolTip("设置")
            collapsed_settings.clicked.connect(self._on_settings)
            collapsed_layout.addWidget(collapsed_settings)

            expanded = QFrame()
            expanded.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            expanded_layout = QVBoxLayout(expanded)
            expanded_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            expanded_layout.setContentsMargins(0, 0, 0, 0)
            expanded_layout.setSpacing(8)

            header_frame = QFrame()
            header_frame.setObjectName("qtHudPanelHeader")
            header = QHBoxLayout(header_frame)
            header.setContentsMargins(6, 3, 6, 3)
            header.setSpacing(6)
            header.addWidget(self._pin_button())
            self.update_button = QPushButton("↓")
            self.update_button.setObjectName("qtHudIconButton")
            self.update_button.setVisible(False)
            self.update_button.clicked.connect(self._on_update_action)
            header.addWidget(self.update_button)
            self.title = _HudLabel("Codex Usage HUD", role="title")
            self.title.setMinimumHeight(24)
            self.title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            header.addWidget(self.title)
            self.session_meta = _HudLabel("", role="muted")
            self.session_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.session_meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.addWidget(self.session_meta, 1)
            self.cache_progress = _ProgressRail()
            self.cache_progress.setMinimumWidth(120)
            self.cache_progress.setMaximumWidth(170)
            self.cache_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header.addWidget(self.cache_progress)
            self.settings_button = QPushButton("⚙")
            self.settings_button.setObjectName("qtHudIconButton")
            self.settings_button.setToolTip("设置")
            self.settings_button.clicked.connect(self._on_settings)
            header.addWidget(self.settings_button)
            expanded_layout.addWidget(header_frame)

            self.warning_panel = QFrame()
            self.warning_panel.setObjectName("qtHudWarningPanel")
            warning_layout = QHBoxLayout(self.warning_panel)
            warning_layout.setContentsMargins(8, 5, 8, 5)
            warning_layout.setSpacing(8)
            warning_layout.addWidget(_HudLabel("●", role="warning-dot"))
            warning_layout.addWidget(_HudLabel("预警", role="warning-title"))
            self.warning = _HudLabel("", role="warning", wrap=True)
            warning_layout.addWidget(self.warning, 1)
            self.warning_close = QPushButton("×")
            self.warning_close.setObjectName("qtHudIconButton")
            self.warning_close.setToolTip("今天不再显示")
            self.warning_close.clicked.connect(self._on_dismiss_warnings)
            warning_layout.addWidget(self.warning_close)
            self.warning_panel.setVisible(False)
            expanded_layout.addWidget(self.warning_panel)

            body_scroll = QScrollArea()
            body_scroll.setWidgetResizable(True)
            body_scroll.setMinimumHeight(0)
            body_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            body_scroll.setFrameShape(QFrame.Shape.NoFrame)
            body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            body_scroll.setObjectName("qtHudTopBodyScroll")
            body = QFrame()
            body.setObjectName("qtHudTopBody")
            body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self._top_body = body
            body_layout = QVBoxLayout(body)
            body_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            body_layout.setContentsMargins(0, 0, 4, 0)
            body_layout.setSpacing(8)
            top_grid = QGridLayout()
            top_grid.setHorizontalSpacing(10)
            top_grid.setVerticalSpacing(8)
            left = QFrame()
            right = QFrame()
            for column in (left, right):
                column.setMinimumWidth(0)
                column.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            left_layout = QVBoxLayout(left)
            right_layout = QVBoxLayout(right)
            for column_layout in (left_layout, right_layout):
                column_layout.setContentsMargins(0, 0, 0, 0)
                column_layout.setSpacing(6)
            top_grid.addWidget(left, 0, 0)
            top_grid.addWidget(right, 0, 1)
            top_grid.setColumnStretch(0, 1)
            top_grid.setColumnStretch(1, 1)
            self._top_grid = top_grid
            self._top_left = left
            self._top_right = right
            body_layout.addLayout(top_grid)

            _session_card, session_body, session_actions = self._card(left_layout, "本会话用量")
            self.task_ordinal_session = self._chip(session_actions)
            self.session_rounds = self._chip(session_actions)
            stats = QGridLayout()
            stats.setHorizontalSpacing(8)
            self.session_cost = self._metric_box(stats, 0, 0, "会话金额", role="metric")
            self.session_tokens = self._metric_box(stats, 0, 1, "累计 tokens", role="metric-info")
            session_body.addLayout(stats)
            insight = QFrame()
            insight.setObjectName("qtHudInset")
            insight.setMinimumHeight(34)
            insight_layout = QHBoxLayout(insight)
            insight_layout.setContentsMargins(8, 6, 8, 6)
            insight_layout.setSpacing(8)
            insight_layout.addWidget(_HudLabel("会话构成", role="caption"))
            self.session_mix = _HudLabel("", role="mono-blue")
            self.session_mix.setMinimumWidth(82)
            self.session_average = _HudLabel("", role="mono-accent")
            self.session_average.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            insight_layout.addWidget(self.session_mix)
            insight_layout.addWidget(self.session_average, 1)
            session_body.addWidget(insight)
            self.session_composition = _HudLabel("", role="muted")
            session_body.addWidget(self.session_composition)
            token_grid = QGridLayout()
            token_grid.setHorizontalSpacing(6)
            token_grid.setVerticalSpacing(6)
            self.session_input_tokens = self._token_chip(token_grid, 0, 0, "输入")
            self.session_cached_tokens = self._token_chip(token_grid, 0, 1, "缓存")
            self.session_output_tokens = self._token_chip(token_grid, 1, 0, "输出")
            self.session_reasoning_tokens = self._token_chip(token_grid, 1, 1, "推理")
            session_body.addLayout(token_grid)

            _budget_card, budget_body, _budget_actions = self._card(left_layout, "额度进度")
            for _ in range(2):
                rail = _ProgressRail()
                budget_body.addWidget(rail)
                self._budget_progress.append(rail)

            heavy_card, heavy_body, heavy_actions = self._card(left_layout, "高消耗轮次")
            heavy_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.heavy_summary = self._chip(heavy_actions)
            for _ in range(3):
                row = QFrame()
                row.setObjectName("qtHudHeavyRow")
                row.setMinimumHeight(28)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(8, 5, 8, 5)
                row_layout.setSpacing(8)
                title = _HudLabel("", role="strong")
                detail = _HudLabel("", role="muted")
                row_layout.addWidget(title)
                row_layout.addWidget(detail, 1)
                heavy_body.addWidget(row)
                self._heavy_rows.append((title, detail))

            activity_card, activity_body, activity_actions = self._card(right_layout, "当前活动")
            activity_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.activity_state = self._chip(activity_actions, warning=True)
            self.task_ordinal_activity = self._chip(activity_actions)
            self.current_task_label = _HudLabel("当前需求", role="caption")
            self.current_task = self._inset_value(activity_body, self.current_task_label)
            self.executing_label = _HudLabel("正在执行", role="caption")
            self.executing = self._inset_value(activity_body, self.executing_label, role="mono-blue")
            metric_grid = QGridLayout()
            metric_grid.setHorizontalSpacing(6)
            metric_grid.setVerticalSpacing(0)
            self.activity_elapsed_label, self.activity_elapsed = self._activity_metric(metric_grid, 0)
            self.activity_gap_label, self.activity_gap = self._activity_metric(metric_grid, 1)
            self.activity_last_label, self.activity_last = self._activity_metric(metric_grid, 2)
            activity_body.addLayout(metric_grid)
            trail_head = QHBoxLayout()
            trail_head.setContentsMargins(0, 0, 0, 0)
            trail_head.setSpacing(5)
            trail_head.addWidget(_HudLabel("活动轨迹", role="caption"), 1)
            self.gap_chip = self._chip(trail_head)
            self.slow_chip = self._chip(trail_head)
            activity_body.addLayout(trail_head)
            activity_body.setSpacing(4)
            self.trail_scroll = QScrollArea()
            self.trail_scroll.setObjectName("qtHudActivityTrailScroll")
            self.trail_scroll.setWidgetResizable(False)
            self.trail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.trail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.trail_scroll.setMinimumHeight(
                QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT * QT_HUD_ACTIVITY_TRAIL_VISIBLE_ROWS + 6
            )
            self.trail_container = QFrame()
            self.trail_container.setObjectName("qtHudTimeline")
            self.trail_line = QFrame(self.trail_container)
            self.trail_line.setObjectName("qtHudActivityTrailLine")
            self.trail_line.lower()
            self.trail_layout = QVBoxLayout(self.trail_container)
            self.trail_layout.setContentsMargins(3, 3, 3, 3)
            self.trail_layout.setSpacing(0)
            for _ in range(QT_HUD_ACTIVITY_TRAIL_VISIBLE_ROWS):
                self._add_activity_row()
            self.trail_scroll.setWidget(self.trail_container)
            activity_body.addWidget(self.trail_scroll, 1)
            self.load_more = QPushButton("查看更多")
            self.load_more.setObjectName("qtHudSecondaryButton")
            self.load_more.clicked.connect(self._load_more_activity)
            activity_body.addWidget(self.load_more)
            activity_body.addStretch(0)

            body_scroll.setWidget(body)
            expanded_layout.addWidget(body_scroll, 1)
            self.set_pages(collapsed, expanded)
            self._sync_responsive_layout()

        def _after_page_switch(self, expanded: bool) -> None:
            if not expanded and self._collapsed_strip is not None:
                self._collapsed_strip._layout_for_current_metrics()

        def _card(self, parent: QVBoxLayout, title: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
            card = QFrame()
            card.setObjectName("qtHudTopCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 6, 8, 7)
            layout.setSpacing(6)
            head = QHBoxLayout()
            head.addWidget(_HudLabel(title, role="card-title"), 1)
            actions = QHBoxLayout()
            actions.setSpacing(5)
            head.addLayout(actions)
            layout.addLayout(head)
            body = QVBoxLayout()
            body.setSpacing(6)
            layout.addLayout(body)
            parent.addWidget(card)
            return card, body, actions

        def _chip(self, parent: QHBoxLayout, *, warning: bool = False) -> _HudLabel:
            label = _HudLabel("", role="chip-warning" if warning else "chip")
            label.setObjectName("qtHudChipWarning" if warning else "qtHudChip")
            label.setMinimumWidth(24)
            label.setFixedHeight(22)
            label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            label.setVisible(False)
            parent.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            return label

        def _metric_box(
            self,
            grid: QGridLayout,
            row: int,
            column: int,
            label: str,
            *,
            role: str = "strong",
        ) -> _HudLabel:
            box = QFrame()
            box.setObjectName("qtHudMetricBox")
            box.setMinimumHeight(50)
            layout = QVBoxLayout(box)
            layout.setContentsMargins(8, 5, 8, 5)
            layout.setSpacing(1)
            caption = _HudLabel(label, role="caption")
            value = _HudLabel("--", role=role)
            layout.addWidget(caption)
            layout.addWidget(value)
            grid.addWidget(box, row, column)
            return value

        def _token_chip(self, grid: QGridLayout, row: int, column: int, label: str) -> _HudLabel:
            box = QFrame()
            box.setObjectName("qtHudTokenChip")
            box.setMinimumHeight(24)
            layout = QHBoxLayout(box)
            layout.setContentsMargins(5, 3, 5, 3)
            layout.setSpacing(4)
            layout.addWidget(_HudLabel(label, role="caption"))
            value = _HudLabel("", role="strong")
            value.setMinimumWidth(52)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(value, 1)
            grid.addWidget(box, row, column)
            grid.setColumnStretch(column, 1)
            return value

        def _inset_value(
            self,
            parent: QVBoxLayout,
            label: _HudLabel,
            *,
            role: str = "body",
        ) -> _HudLabel:
            box = QFrame()
            box.setObjectName("qtHudInset")
            box.setMinimumHeight(44)
            layout = QVBoxLayout(box)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(2)
            layout.addWidget(label)
            value = _HudLabel("", role=role)
            layout.addWidget(value)
            parent.addWidget(box)
            return value

        def _activity_metric(
            self,
            grid: QGridLayout,
            column: int,
        ) -> tuple[_HudLabel, _HudLabel]:
            box = QFrame()
            box.setObjectName("qtHudInset")
            box.setMinimumHeight(42)
            layout = QVBoxLayout(box)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(1)
            label = _HudLabel("", role="caption")
            value = _HudLabel("", role="mono-accent")
            layout.addWidget(label)
            layout.addWidget(value)
            grid.addWidget(box, 0, column)
            grid.setColumnStretch(column, 1)
            return label, value

        def _add_activity_row(self) -> None:
            row = QFrame()
            row.setObjectName("qtHudActivityRow")
            row.setFixedHeight(QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            time_label = _HudLabel("", role="activity-time")
            time_label.setFixedWidth(52)
            marker = _ActivityMarker()
            text_box = QFrame()
            text_box.setObjectName("qtHudActivityTextBox")
            text_layout = QVBoxLayout(text_box)
            text_layout.setContentsMargins(8, 4, 0, 3)
            text_layout.setSpacing(0)
            title_label = _HudLabel("", role="activity-title")
            title_label.setFixedHeight(15)
            detail_label = _HudLabel("", role="activity-detail")
            detail_label.setFixedHeight(15)
            text_layout.addWidget(title_label)
            text_layout.addWidget(detail_label)
            row_layout.addWidget(time_label)
            row_layout.addWidget(marker)
            row_layout.addWidget(text_box, 1)
            marker.raise_()
            self.trail_layout.addWidget(row)
            self._activity_rows.append((time_label, marker, title_label, detail_label))

        def _ensure_activity_row_count(self, count: int) -> None:
            while len(self._activity_rows) < count:
                self._add_activity_row()

        def update_payload(self, payload: Mapping[str, object]) -> None:
            details = payload.get("topDetails") if isinstance(payload.get("topDetails"), Mapping) else {}
            progress = payload.get("topProgress") if isinstance(payload.get("topProgress"), Mapping) else {}
            copies = payload.get("topCopies") if isinstance(payload.get("topCopies"), Mapping) else {}

            self.title.set_elided_text(details.get("title"), limit=120)
            self.session_meta.set_elided_text(details.get("session"), limit=120)
            self.session_tokens.setText(str(details.get("sessionTokens") or "--"))
            self.session_cost.setText(str(details.get("sessionCost") or "--"))
            self.session_rounds.set_elided_text(details.get("sessionRounds"), limit=32)
            self.session_rounds.setVisible(bool(str(details.get("sessionRounds") or "").strip()))
            self.task_ordinal_session.set_elided_text(details.get("taskOrdinalSession"), limit=32)
            self.task_ordinal_session.setVisible(bool(str(details.get("taskOrdinalSession") or "").strip()))
            self.session_mix.set_elided_text(details.get("sessionMix"), limit=60)
            self.session_average.set_elided_text(details.get("sessionAverage"), limit=40)
            self.session_composition.set_elided_text(details.get("sessionComposition"), limit=120)
            self.session_input_tokens.setText(str(details.get("sessionInputTokens") or "--"))
            self.session_cached_tokens.setText(str(details.get("sessionCachedTokens") or "--"))
            self.session_output_tokens.setText(str(details.get("sessionOutputTokens") or "--"))
            self.session_reasoning_tokens.setText(str(details.get("sessionReasoningTokens") or "--"))
            self.heavy_summary.set_elided_text(details.get("heavyRoundsSummary"), limit=32)
            self.heavy_summary.setVisible(bool(str(details.get("heavyRoundsSummary") or "").strip()))
            self._update_heavy_rounds(details.get("heavyRounds"))
            self.activity_state.set_elided_text(details.get("activityState"), limit=30)
            self.activity_state.setVisible(bool(str(details.get("activityState") or "").strip()))
            self.task_ordinal_activity.set_elided_text(details.get("taskOrdinalActivity"), limit=32)
            self.task_ordinal_activity.setVisible(bool(str(details.get("taskOrdinalActivity") or "").strip()))
            self.current_task_label.setText(str(details.get("currentTaskLabel") or "当前需求"))
            self.executing_label.setText(str(details.get("executingLabel") or "正在执行"))
            self.current_task.set_elided_text(details.get("currentTask"), limit=130)
            self.current_task.set_copy_text(details.get("currentTask"))
            self.executing.set_elided_text(details.get("executing"), limit=130)
            self.executing.set_copy_text(details.get("executing"))
            self.activity_elapsed_label.setText(str(details.get("activityElapsedLabel") or "已运行"))
            self.activity_elapsed.setText(str(details.get("activityElapsed") or "--"))
            self.activity_gap_label.setText(str(details.get("activityGapLabel") or "当前等待"))
            self.activity_gap.set_elided_text(details.get("activityGap"), limit=40)
            self.activity_last_label.setText(str(details.get("activityLastLabel") or "需求轮次"))
            self.activity_last.set_elided_text(details.get("activityLast"), limit=44)
            self.slow_chip.set_elided_text(details.get("slow"), limit=48)
            self.slow_chip.setVisible(bool(str(details.get("slow") or "").strip()))
            self.slow_chip.set_copy_text(copies.get("slow"))
            self.gap_chip.set_elided_text(details.get("gap"), limit=48)
            self.gap_chip.setVisible(bool(str(details.get("gap") or "").strip()))
            self.gap_chip.set_copy_text(copies.get("gap"))
            warning = str(details.get("warnings") or "").strip()
            self.warning.setText(warning)
            self.warning_panel.setVisible(bool(warning))

            collapsed_metrics = [
                item for item in progress.get("collapsed", []) if isinstance(item, Mapping)
            ]
            if self._collapsed_strip is not None:
                self._collapsed_strip.set_metrics(collapsed_metrics)
            cache_metric = progress.get("cache")
            self.cache_progress.set_metric(cache_metric if isinstance(cache_metric, Mapping) else None)
            budget_metrics = [
                item for item in progress.get("budget", []) if isinstance(item, Mapping)
            ]
            for index, rail in enumerate(self._budget_progress):
                rail.set_metric(budget_metrics[index] if index < len(budget_metrics) else None)
            self._update_activity_trail(details.get("activityTrail"))
            update_state = payload.get("updateState")
            self._render_update_button(update_state if isinstance(update_state, Mapping) else {})
            self._sync_responsive_layout()
            self._sync_top_body_height()

        def hide_warning(self) -> None:
            self.warning.setText("")
            self.warning_panel.setVisible(False)

        def _render_update_button(self, state: Mapping[str, object]) -> None:
            visible = bool(state.get("visible"))
            self.update_button.setVisible(visible)
            if not visible:
                return
            icon = str(state.get("icon") or "")
            phase = str(state.get("phase") or "")
            glyph = "⇪" if icon == "install" else "↓"
            self.update_button.setText(glyph)
            self.update_button.setProperty("phase", phase)
            tooltip = str(state.get("title") or state.get("message") or "")
            self.update_button.setToolTip(tooltip)
            self.update_button.style().unpolish(self.update_button)
            self.update_button.style().polish(self.update_button)

        def _update_heavy_rounds(self, items: object) -> None:
            rounds = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
            placeholders = [
                ("暂无会话高消耗轮次", "会话出现 token 确认后展示 Top 3"),
                ("等待统计", "不会因新需求开始而清空"),
                ("保持占位", "新轮次超过历史 Top 3 后刷新"),
            ]
            for index, labels in enumerate(self._heavy_rows):
                title_label, detail_label = labels
                if index < len(rounds):
                    item = rounds[index]
                    title_label.set_elided_text(item.get("title"), limit=42)
                    detail_label.set_elided_text(item.get("detail"), limit=90)
                    copy_text = str(item.get("copyText") or item.get("tooltip") or "")
                    tooltip = str(item.get("tooltip") or copy_text)
                    title_label.parentWidget().setToolTip(tooltip)
                    title_label.set_copy_text(copy_text, tooltip="点击复制轮次内容")
                    detail_label.set_copy_text(copy_text, tooltip="点击复制轮次内容")
                else:
                    title, detail = placeholders[index]
                    title_label.setText(title)
                    detail_label.setText(detail)
                    title_label.parentWidget().setToolTip("")
                    title_label.set_copy_text("")
                    detail_label.set_copy_text("")

        def _update_activity_trail(self, items: object) -> None:
            trail = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
            signature = _metric_signature(trail)
            if signature != self._activity_signature:
                self._activity_visible_count = 4
                self._activity_signature = signature
                self._activity_trail = trail
            self._render_activity_trail()

        def _load_more_activity(self) -> None:
            self._activity_visible_count += 4
            self._render_activity_trail()

        def _render_activity_trail(self) -> None:
            trail = self._activity_trail
            if trail:
                visible_count = min(len(trail), max(4, self._activity_visible_count))
                visible_items = trail[:visible_count]
            else:
                visible_items = [
                    {"time": "--:--", "title": "暂无时间节点", "detail": "等待会话产生新活动"}
                ]
                visible_count = 1
            self._ensure_activity_row_count(visible_count)
            for index, labels in enumerate(self._activity_rows):
                time_label, marker, title_label, detail_label = labels
                if index < visible_count:
                    item = visible_items[index]
                    time_label.parentWidget().setVisible(True)
                    time_label.setText(str(item.get("time") or ""))
                    marker.set_state(index, visible_count, bool(item.get("active")))
                    title_label.set_elided_text(item.get("title") or "", limit=28)
                    detail_label.set_elided_text(item.get("detail") or "", limit=100)
                    tooltip = str(item.get("tooltip") or "  ".join(
                        str(item.get(key) or "") for key in ("time", "title", "detail")
                    ).strip())
                    detail_label.set_copy_text(tooltip, tooltip="点击复制轨迹详情")
                else:
                    time_label.parentWidget().setVisible(False)
                    detail_label.set_copy_text("")
            timeline_height = visible_count * QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT + 6
            self.trail_container.setMinimumHeight(timeline_height)
            self.trail_container.resize(max(1, self.trail_scroll.viewport().width()), timeline_height)
            self._sync_activity_trail_line(visible_count)
            has_more = bool(trail) and visible_count < len(trail)
            self.load_more.setEnabled(has_more)
            self.load_more.setText("查看更多" if has_more else "已显示全部")

        def resizeEvent(self, event: Any) -> None:  # noqa: N802
            super().resizeEvent(event)
            if hasattr(self, "trail_scroll") and hasattr(self, "trail_line"):
                self.trail_container.resize(
                    max(1, self.trail_scroll.viewport().width()),
                    max(1, self.trail_container.minimumHeight()),
                )
                self._sync_activity_trail_line(
                    max(1, min(len(self._activity_trail), max(4, self._activity_visible_count)))
                    if self._activity_trail
                    else 1
                )
            self._sync_responsive_layout()

        def _sync_activity_trail_line(self, visible_count: int) -> None:
            if not hasattr(self, "trail_line"):
                return
            if visible_count <= 1:
                self.trail_line.setVisible(False)
                return
            marker_x = 3 + 52 + 12
            first_center_y = 3 + (QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT // 2)
            last_center_y = 3 + ((visible_count - 1) * QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT) + (QT_HUD_ACTIVITY_TRAIL_ROW_HEIGHT // 2)
            self.trail_line.setVisible(True)
            self.trail_line.setGeometry(marker_x - 1, first_center_y, 2, max(1, last_center_y - first_center_y))
            self.trail_line.lower()

        def _sync_responsive_layout(self) -> None:
            width = max(1, self.width())
            if hasattr(self, "session_meta"):
                self.session_meta.setVisible(True)
            if hasattr(self, "cache_progress"):
                self.cache_progress.setVisible(True)
            grid = self._top_grid
            left = self._top_left
            right = self._top_right
            if grid is None or left is None or right is None:
                return
            stacked = width < QT_HUD_TOP_STACK_WIDTH
            if stacked == self._top_layout_stacked:
                return
            self._top_layout_stacked = stacked
            if stacked:
                grid.addWidget(left, 0, 0)
                grid.addWidget(right, 1, 0)
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 0)
                grid.setRowStretch(0, 0)
                grid.setRowStretch(1, 0)
                self._sync_top_body_height()
                return
            grid.addWidget(left, 0, 0)
            grid.addWidget(right, 0, 1)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setRowStretch(0, 1)
            grid.setRowStretch(1, 0)
            self._sync_top_body_height()

        def _sync_top_body_height(self) -> None:
            body = self._top_body
            if body is None:
                return
            body.adjustSize()
            body.setMinimumHeight(max(1, body.sizeHint().height()))

        def apply_theme(self, tokens: Mapping[str, str]) -> None:
            for rail in self.findChildren(_ProgressRail):
                rail.set_theme(tokens)


    class _RequestRow(QFrame):
        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("qtHudRequestRowFrame")
            self.setMinimumHeight(18)
            self.setMaximumHeight(24)
            self._started_at: datetime | None = None
            layout = QHBoxLayout(self)
            layout.setContentsMargins(4, 0, 4, 0)
            layout.setSpacing(0)
            self.prefix = _HudLabel("", role="request")
            self.prefix.setFixedWidth(104)
            self.prefix.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            self.time = _HudLabel("", role="request-time")
            self.time.setFixedWidth(62)
            self.time.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            self.suffix = _HudLabel("", role="request")
            layout.addWidget(self.prefix)
            layout.addWidget(self.time)
            layout.addWidget(self.suffix, 1)

        def set_detail(self, item: Mapping[str, object] | str, *, latest: bool) -> None:
            self.setProperty("latest", "true" if latest else "false")
            self._started_at = None
            if isinstance(item, Mapping):
                prefix = str(item.get("prefix") or "")
                time_text = str(item.get("time") or "")
                suffix = str(item.get("suffix") or "")
                fallback = str(item.get("text") or "").strip()
                if not prefix and not suffix:
                    prefix, time_text, suffix = fallback, "", ""
                tooltip = str(item.get("text") or f"{prefix}{time_text}{suffix}").strip()
                if latest and item.get("running") and item.get("startedAt"):
                    try:
                        self._started_at = datetime.fromisoformat(str(item.get("startedAt")))
                    except ValueError:
                        self._started_at = None
            else:
                prefix, time_text, suffix = str(item), "", ""
                tooltip = str(item)
            self.prefix.set_elided_text(prefix, limit=48)
            self.time.setText(time_text)
            self.suffix.set_elided_text(suffix, limit=120)
            self.setToolTip(tooltip)
            self.style().unpolish(self)
            self.style().polish(self)
            self.refresh_running_time()

        def refresh_running_time(self) -> None:
            if self._started_at is None:
                return
            now = datetime.now(self._started_at.tzinfo) if self._started_at.tzinfo else datetime.now()
            elapsed = max(0, int((now - self._started_at).total_seconds()))
            self.time.setText(f"{elapsed}s".rjust(8))


    class _RequestPanel(_PanelWindow):
        def __init__(
            self,
            *,
            width: int = QT_HUD_REQUEST_WIDTH,
            expanded_height: int = QT_HUD_REQUEST_EXPANDED_HEIGHT,
            on_interaction: Callable[[], None],
            on_click_priority: Callable[[], None] | None = None,
            on_pointer_priority: Callable[[], None] | None = None,
            on_geometry_changed: Callable[[str, _PanelWindow, str], None] | None = None,
            on_pin_toggle: Callable[[str], None] | None = None,
        ) -> None:
            super().__init__(
                target="request",
                width=width,
                collapsed_height=QT_HUD_REQUEST_COLLAPSED_HEIGHT,
                expanded_height=expanded_height,
                on_interaction=on_interaction,
                on_click_priority=on_click_priority,
                on_pointer_priority=on_pointer_priority,
                on_geometry_changed=on_geometry_changed,
                on_pin_toggle=on_pin_toggle,
                grow_from_bottom=True,
            )
            self._row_labels: list[_RequestRow] = []
            self._row_signature = ""
            self._build()

        def _build(self) -> None:
            collapsed = QFrame()
            collapsed.setObjectName("qtHudRequestCollapsed")
            collapsed_layout = QHBoxLayout(collapsed)
            collapsed_layout.setContentsMargins(0, 0, 0, 0)
            collapsed_layout.setSpacing(6)
            collapsed_layout.addWidget(self._pin_button())
            self.request_line = _HudLabel("等待请求...", role="strong")
            collapsed_layout.addWidget(self.request_line, 1)

            expanded = QFrame()
            expanded.setObjectName("qtHudRequestExpanded")
            expanded_layout = QVBoxLayout(expanded)
            expanded_layout.setContentsMargins(0, 0, 0, 0)
            expanded_layout.setSpacing(4)
            subhead = QFrame()
            subhead.setObjectName("qtHudRequestSubhead")
            subhead_layout = QHBoxLayout(subhead)
            subhead_layout.setContentsMargins(2, 0, 2, 0)
            subhead_layout.setSpacing(6)
            subhead_layout.addWidget(_HudLabel("轮次流水", role="caption"), 1)
            latest_label = _HudLabel("最新在上", role="caption")
            latest_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            subhead_layout.addWidget(latest_label)
            expanded_layout.addWidget(subhead)

            list_shell = QFrame()
            list_shell.setObjectName("qtHudRequestListShell")
            list_layout = QVBoxLayout(list_shell)
            list_layout.setContentsMargins(0, 0, 0, 0)
            self.rows_widget = QFrame()
            self.rows_layout = QVBoxLayout(self.rows_widget)
            self.rows_layout.setContentsMargins(4, 3, 2, 3)
            self.rows_layout.setSpacing(0)
            self.request_scroll = QScrollArea()
            self.request_scroll.setWidgetResizable(True)
            self.request_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.request_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.request_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.request_scroll.setObjectName("qtHudRequestScroll")
            self.request_scroll.setWidget(self.rows_widget)
            list_layout.addWidget(self.request_scroll)
            expanded_layout.addWidget(list_shell, 1)

            header = QFrame()
            header.setObjectName("qtHudRequestExpandedHeader")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(6, 3, 6, 3)
            header_layout.setSpacing(6)
            header_layout.addWidget(self._pin_button())
            self.request_title = _HudLabel("最近模型请求轮次", role="strong")
            header_layout.addWidget(self.request_title, 1)
            expanded_layout.addWidget(header)
            self.set_pages(collapsed, expanded)

        def update_payload(self, payload: Mapping[str, object]) -> None:
            status = str(payload.get("requestStatus") or "waiting")
            warning = bool(payload.get("warning"))
            state = "error" if status == "error" else "warning" if warning else ""
            for widget in (self.request_line, self.request_title):
                widget.setProperty("state", state)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            self.request_line.set_elided_text(payload.get("requestLine"), limit=160)
            self.request_title.set_elided_text(
                payload.get("requestLine") or "最近模型请求轮次",
                limit=170,
            )
            details = [
                item
                for item in payload.get("requestRowDetails", [])
                if isinstance(item, Mapping)
            ] if isinstance(payload.get("requestRowDetails"), list) else []
            fallback_rows = [
                str(item)
                for item in payload.get("requestRows", [])
                if str(item or "").strip()
            ] if isinstance(payload.get("requestRows"), list) else []
            rows: list[Mapping[str, object] | str] = details[:30] or fallback_rows[:30]
            if not rows:
                rows = ["本次请求(等待) $0.0000 ↑- ◎- ↓- ◇- ↻- ∑-"]
            signature = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            if signature == self._row_signature:
                self._refresh_running_rows()
                return
            self._row_signature = signature
            scroll_bar = self.request_scroll.verticalScrollBar()
            previous_top = scroll_bar.value()
            should_follow_head = previous_top <= 2
            self._ensure_row_count(max(1, len(rows)))
            visible_rows = rows[: len(self._row_labels)]
            for index, label in enumerate(self._row_labels):
                if index < len(visible_rows):
                    label.set_detail(visible_rows[index], latest=index == 0)
                    label.setVisible(True)
                else:
                    label.setVisible(False)
            if should_follow_head:
                scroll_bar.setValue(0)
            else:
                scroll_bar.setValue(previous_top)
            self._refresh_running_rows()

        def _ensure_row_count(self, count: int) -> None:
            while len(self._row_labels) < count:
                label = _RequestRow()
                self.rows_layout.addWidget(label)
                self._row_labels.append(label)
            while len(self._row_labels) > count:
                label = self._row_labels.pop()
                label.setParent(None)
                label.deleteLater()

        def _refresh_running_rows(self) -> None:
            for row in self._row_labels:
                row.refresh_running_time()

        def apply_theme(self, tokens: Mapping[str, str]) -> None:
            del tokens
            for row in self._row_labels:
                row.style().unpolish(row)
                row.style().polish(row)


    class _SettingsDialog(QDialog):
        def __init__(self, window: "_QtHudWindowImpl") -> None:
            super().__init__(window.top_window)
            self._window = window
            self._title_drag_origin: QPoint | None = None
            self._title_drag_window_origin: QPoint | None = None
            self.setWindowTitle("codex-usage-hud 设置")
            flags = Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
            if not sys.platform.startswith("win"):
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.setMinimumSize(760, 600)
            self.resize(780, 620)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            shell = QFrame()
            shell.setObjectName("qtHudSettingsDialog")
            shell_layout = QVBoxLayout(shell)
            shell_layout.setContentsMargins(0, 0, 0, 0)
            shell_layout.setSpacing(0)
            layout.addWidget(shell)

            header_frame = QFrame()
            header_frame.setObjectName("qtHudSettingsHead")
            header = QHBoxLayout(header_frame)
            header.setContentsMargins(12, 10, 12, 10)
            header.setSpacing(10)
            title = _HudLabel(f"codex-usage-hud v{__version__}", role="settings-title")
            header.addWidget(title, 1)
            close_top = QPushButton("×")
            close_top.setObjectName("qtHudIconButton")
            close_top.clicked.connect(self.close)
            header.addWidget(close_top)
            shell_layout.addWidget(header_frame)
            for drag_widget in (header_frame, title):
                drag_widget.installEventFilter(self)
                drag_widget.setCursor(Qt.CursorShape.SizeAllCursor)

            self.tabs = QTabWidget()
            self.tabs.setObjectName("qtHudSettingsTabs")
            self.tabs.addTab(self._build_settings_tab(), "设置")
            self.tabs.addTab(self._build_support_tab(), "请作者喝咖啡")
            self.tabs.addTab(self._build_about_tab(), "版本更新")
            self.tabs.currentChanged.connect(self._sync_action_visibility)
            shell_layout.addWidget(self.tabs, 1)

            footer_frame = QFrame()
            footer_frame.setObjectName("qtHudSettingsActions")
            footer = QHBoxLayout(footer_frame)
            footer.setContentsMargins(12, 10, 12, 10)
            footer.setSpacing(8)
            self.status = _HudLabel("设置将保存到本地配置文件", role="settings-status", wrap=True)
            footer.addWidget(self.status, 1)
            self.export_button = QPushButton("导出 JSON")
            self.save_button = QPushButton("保存")
            self.check_update_button = QPushButton("检查更新")
            self.install_update_button = QPushButton("安装更新")
            self.close_button = QPushButton("关闭")
            self.export_button.clicked.connect(self._export_json)
            self.save_button.clicked.connect(self._save_only)
            self.check_update_button.clicked.connect(self._check_update)
            self.install_update_button.clicked.connect(self._install_update)
            self.close_button.clicked.connect(self.close)
            self.save_button.setProperty("primary", "true")
            self.install_update_button.setProperty("primary", "true")
            self.close_button.setProperty("primary", "true")
            for button in (
                self.export_button,
                self.save_button,
                self.check_update_button,
                self.install_update_button,
                self.close_button,
            ):
                button.setObjectName("qtHudSettingsAction")
                footer.addWidget(button)
            shell_layout.addWidget(footer_frame)
            self.setStyleSheet(_qt_stylesheet(window._theme_tokens))
            self._sync_action_visibility()

        def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
            if isinstance(watched, QWidget):
                event_type = event.type()
                if isinstance(event, QMouseEvent):
                    if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                        self._title_drag_origin = event.globalPosition().toPoint()
                        self._title_drag_window_origin = self.pos()
                        event.accept()
                        return True
                    if event_type == QEvent.Type.MouseMove and self._title_drag_origin is not None and self._title_drag_window_origin is not None:
                        delta = event.globalPosition().toPoint() - self._title_drag_origin
                        self.move(self._title_drag_window_origin + delta)
                        event.accept()
                        return True
                    if event_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                        self._title_drag_origin = None
                        self._title_drag_window_origin = None
                        event.accept()
                        return True
            return super().eventFilter(watched, event)

        def _build_settings_tab(self) -> QWidget:
            outer = QWidget()
            outer.setObjectName("qtHudSettingsPage")
            outer_layout = QVBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            scroll = QScrollArea()
            scroll.setObjectName("qtHudSettingsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            body = QFrame()
            body.setObjectName("qtHudSettingsBody")
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 4, 0)
            body_layout.setSpacing(10)
            form = QGridLayout()
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(8)
            self.display_mode = _SettingsComboBox()
            self.display_mode.addItem("自动：Renderer -> Qt -> Tk", "auto")
            self.display_mode.addItem("Renderer 内嵌 HUD", "renderer")
            self.display_mode.addItem("Qt 独立窗口", "qt")
            self.display_mode.addItem("Tk 独立窗口", "tk")
            self._configured_display_mode = str(self._window.user_settings.display_mode)
            self._display_mode_touched = False
            index = max(0, self.display_mode.findData(self._window.active_display_mode))
            self.display_mode.setCurrentIndex(index)
            self.display_mode.currentIndexChanged.connect(self._on_display_mode_selected)
            self.daily_budget = QLineEdit(f"{self._window.user_settings.daily_budget_usd:g}")
            self.weekly_budget = QLineEdit(f"{self._window.user_settings.weekly_budget_usd:g}")
            self.daily_reset = QLineEdit(str(self._window.user_settings.daily_reset_time))
            self.weekly_reset = QLineEdit(str(self._window.user_settings.weekly_reset_time))
            self.weekday = _SettingsComboBox()
            for value, label in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]):
                self.weekday.addItem(label, value)
            self.weekday.setCurrentIndex(max(0, min(6, int(self._window.user_settings.weekly_reset_weekday))))
            self.work_overlay_max_items = _SettingsComboBox()
            for value in self._work_overlay_setting_values():
                label = f"{value} - 不启用" if value == 0 else str(value)
                self.work_overlay_max_items.addItem(label, value)
            overlay_index = max(
                0,
                self.work_overlay_max_items.findData(
                    self._work_overlay_setting_value(self._window.user_settings.work_overlay_max_items)
                ),
            )
            self.work_overlay_max_items.setCurrentIndex(overlay_index)
            self.pricing_url = QLineEdit(str(self._window.user_settings.pricing_url or ""))
            self.thresholds = QLineEdit(
                ",".join(f"{item:g}" for item in self._window.user_settings.budget_thresholds)
            )
            self.weekly_adjustment = QLineEdit(f"{self._window.user_settings.weekly_adjustment_usd:g}")
            weekly_reset_controls = QWidget()
            weekly_reset_layout = QHBoxLayout(weekly_reset_controls)
            weekly_reset_layout.setContentsMargins(0, 0, 0, 0)
            weekly_reset_layout.setSpacing(6)
            weekly_reset_layout.addWidget(self.weekday, 1)
            weekly_reset_layout.addWidget(self.weekly_reset, 1)

            pricing_controls = QWidget()
            pricing_layout = QHBoxLayout(pricing_controls)
            pricing_layout.setContentsMargins(0, 0, 0, 0)
            pricing_layout.setSpacing(8)
            pricing_layout.addWidget(self.pricing_url, 1)
            fetch_button = QPushButton("拉取")
            fetch_button.clicked.connect(self._fetch_prices)
            pricing_layout.addWidget(fetch_button)

            self._add_field(form, 0, 0, "日额度 USD", self.daily_budget)
            self._add_field(form, 0, 1, "周额度 USD", self.weekly_budget)
            self._add_field(form, 1, 0, "日额度重置时间", self.daily_reset)
            self._add_field(form, 1, 1, "周额度重置", weekly_reset_controls)
            self._add_field(form, 2, 0, "HUD 显示方案", self.display_mode)
            self._add_field(form, 2, 1, "会话气泡最大显示数（0 表示不启用）", self.work_overlay_max_items)
            self._add_field(form, 3, 0, "超额提醒阈值", self.thresholds)
            self._add_field(form, 3, 1, "本周补充已使用额度 USD", self.weekly_adjustment)
            self._add_field(form, 4, 0, "计费单价获取地址", pricing_controls, column_span=2)
            body_layout.addLayout(form)

            price_box = QFrame()
            price_box.setObjectName("qtHudMetricBox")
            price_layout = QVBoxLayout(price_box)
            price_layout.setContentsMargins(8, 6, 8, 8)
            price_layout.setSpacing(6)
            price_layout.addWidget(_HudLabel("模型单价（USD / 1M tokens）", role="caption"))
            self.price_table = QTableWidget(0, 5)
            self.price_table.setObjectName("qtHudPriceTable")
            self.price_table.setMinimumHeight(190)
            self.price_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.price_table.setHorizontalHeaderLabels(["模型", "输入", "缓存", "输出", "推理"])
            self.price_table.verticalHeader().setVisible(False)
            self.price_table.setAlternatingRowColors(True)
            self.price_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            header = self.price_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for index in range(1, 5):
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
            self._replace_price_rows(self._window.user_settings.model_prices)
            price_layout.addWidget(self.price_table, 1)
            add_model = QPushButton("添加模型")
            add_model.clicked.connect(lambda: self._append_price_row("future-model", {}))
            price_layout.addWidget(add_model, alignment=Qt.AlignmentFlag.AlignLeft)
            body_layout.addWidget(price_box, 1)

            foot = QFrame()
            foot_layout = QHBoxLayout(foot)
            foot_layout.setContentsMargins(0, 0, 0, 0)
            exit_button = QPushButton("退出 HUD")
            exit_button.clicked.connect(self._confirm_exit)
            foot_layout.addWidget(exit_button)
            path_label = _HudLabel(f"配置文件：{self._window.user_settings_store.path}", role="muted")
            path_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            foot_layout.addWidget(path_label, 1)
            body_layout.addWidget(foot)
            scroll.setWidget(body)
            outer_layout.addWidget(scroll)
            return outer

        def _build_support_tab(self) -> QWidget:
            tab = QWidget()
            tab.setObjectName("qtHudSettingsPage")
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
            layout.addWidget(
                _HudLabel(
                    "如果这个 HUD 帮你节省了排查 token 和费用的时间，可以扫码支持维护。",
                    role="muted",
                    wrap=True,
                )
            )
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for index, item in enumerate(support_qr_asset_paths()):
                card = QFrame()
                card.setObjectName("qtHudSupportQrCard")
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(10, 10, 10, 10)
                card_layout.setSpacing(8)
                title_row = QHBoxLayout()
                title_row.setContentsMargins(0, 0, 0, 0)
                title_row.setSpacing(8)
                title_row.addWidget(_HudLabel(str(item["label"]), role="strong"), 1)
                hint = _HudLabel(str(item["hint"]), role="caption")
                hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                title_row.addWidget(hint)
                card_layout.addLayout(title_row)
                image = QLabel()
                image.setObjectName("qtHudSupportQrImage")
                image.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image.setMinimumSize(260, 260)
                image.setMaximumSize(260, 360)
                pixmap = QPixmap(str(item["path"]))
                if not pixmap.isNull():
                    image.setPixmap(
                        pixmap.scaled(
                            260,
                            360,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                else:
                    image.setText("赞赏码资源未加载")
                card_layout.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter)
                grid.addWidget(card, index // 2, index % 2)
            layout.addLayout(grid)
            layout.addWidget(
                _HudLabel(
                    f"项目链接：{self._window.user_settings.support_url}\n"
                    f"当前配置文件：{self._window.user_settings_store.path}",
                    role="muted",
                    wrap=True,
                )
            )
            layout.addStretch(1)
            return tab

        def _build_about_tab(self) -> QWidget:
            tab = QWidget()
            tab.setObjectName("qtHudSettingsPage")
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            lines = [
                f"当前版本：v{__version__}",
                "更新源：GitHub Releases / mingbingfeng/codex-usage-hud",
                "Windows 安装包：codex-usage-hud-v*-windows-x64-setup.exe",
                "自动更新会下载最新版安装包并启动安装器；安装器会先关闭正在运行的 HUD，再替换本地文件。",
                f"当前配置文件：{self._window.user_settings_store.path}",
            ]
            layout.addWidget(_HudLabel("\n".join(lines), role="body", wrap=True))
            self.update_state_label = _HudLabel("", role="muted", wrap=True)
            layout.addWidget(self.update_state_label)
            self._refresh_update_state_label()
            layout.addStretch(1)
            return tab

        @Slot()
        @Slot(int)
        def _sync_action_visibility(self, *_args: object) -> None:
            active = self.tabs.currentIndex()
            is_settings = active == 0
            is_about = active == 2
            self.export_button.setVisible(is_settings)
            self.save_button.setVisible(is_settings)
            self.check_update_button.setVisible(is_about)
            self.install_update_button.setVisible(is_about)
            self.close_button.setVisible(not is_settings)
            if active == 1:
                self.status.setText("赞赏码资源来自本地打包文件")
            elif is_about:
                self.status.setText("可检查 GitHub Release 并启动 Windows 安装器")
                self._refresh_update_state_label()
            else:
                self.status.setText("设置将保存到本地配置文件")

        def _add_field(
            self,
            form: QGridLayout,
            row: int,
            column: int,
            label: str,
            widget: QWidget,
            *,
            column_span: int = 1,
        ) -> None:
            box = QFrame()
            box.setObjectName("qtHudMetricBox")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 6, 8, 6)
            box_layout.setSpacing(4)
            box_layout.addWidget(_HudLabel(label, role="caption"))
            box_layout.addWidget(widget)
            form.addWidget(box, row, column, 1, column_span)

        def _selected_mode(self) -> str:
            if not self._display_mode_touched:
                return str(self._configured_display_mode or self._window.user_settings.display_mode)
            return str(self.display_mode.currentData() or "auto")

        def _work_overlay_selectable_max(self) -> int:
            screen = QApplication.primaryScreen()
            height = screen.size().height() if screen is not None else 1080
            return work_overlay_max_items_for_screen_height(int(height))

        def _work_overlay_setting_value(self, count: object) -> int:
            try:
                value = int(count)
            except (TypeError, ValueError):
                value = 0
            return min(self._work_overlay_selectable_max(), max(0, value))

        def _work_overlay_setting_values(self) -> list[int]:
            return list(range(0, self._work_overlay_selectable_max() + 1))

        def _selected_work_overlay_max_items(self) -> int:
            data = self.work_overlay_max_items.currentData()
            if data is None:
                return self._work_overlay_setting_value(self.work_overlay_max_items.currentText().split(" ", 1)[0])
            return self._work_overlay_setting_value(data)

        def _append_price_row(self, model: str, values: Mapping[str, object]) -> None:
            row = self.price_table.rowCount()
            self.price_table.insertRow(row)
            keys = ["model", "input", "cached_input", "output", "reasoning"]
            for column, key in enumerate(keys):
                text = model if key == "model" else str(values.get(key) or 0)
                self.price_table.setItem(row, column, QTableWidgetItem(text))

        def _replace_price_rows(self, prices: Mapping[str, object]) -> None:
            self.price_table.setRowCount(0)
            for model, price in sorted(prices.items()):
                values = price.to_dict() if hasattr(price, "to_dict") else dict(price or {})
                self._append_price_row(str(model), values)
            if self.price_table.rowCount() > 4:
                self.price_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._sync_price_table_height()

        def _sync_price_table_height(self) -> None:
            header_height = self.price_table.horizontalHeader().height()
            row_height = self.price_table.verticalHeader().defaultSectionSize()
            visible_rows = min(max(1, self.price_table.rowCount()), 4)
            frame = self.price_table.frameWidth() * 2
            self.price_table.setFixedHeight(header_height + (row_height * visible_rows) + frame + 4)

        def _price_payload(self) -> dict[str, dict[str, object]]:
            payload: dict[str, dict[str, object]] = {}
            keys = ["input", "cached_input", "output", "reasoning"]
            for row in range(self.price_table.rowCount()):
                model_item = self.price_table.item(row, 0)
                model = model_item.text().strip() if model_item is not None else ""
                if not model:
                    continue
                values: dict[str, object] = {}
                for offset, key in enumerate(keys, start=1):
                    item = self.price_table.item(row, offset)
                    values[key] = item.text().strip() if item is not None else "0"
                payload[model] = values
            return payload

        def _save_config(self) -> UserConfig:
            current = self._window.user_settings_store.load()
            merged = current.to_dict()
            merged.update(
                {
                    "display_mode": self._selected_mode(),
                    "daily_budget_usd": self.daily_budget.text(),
                    "weekly_budget_usd": self.weekly_budget.text(),
                    "daily_reset_time": self.daily_reset.text(),
                    "weekly_reset_weekday": self.weekday.currentData(),
                    "weekly_reset_time": self.weekly_reset.text(),
                    "work_overlay_max_items": self._selected_work_overlay_max_items(),
                    "pricing_url": self.pricing_url.text(),
                    "budget_thresholds": self.thresholds.text(),
                    "weekly_adjustment_usd": self.weekly_adjustment.text(),
                    "model_prices": self._price_payload(),
                }
            )
            config = UserConfig.from_dict(merged)
            self._window.user_settings_store.save(config)
            self._window.user_settings = config
            return config

        @Slot()
        def _save_only(self) -> None:
            config = self._save_config()
            target = effective_display_mode(config.display_mode)
            self.status.setText(
                "已保存到本地配置；预算和价格会自动刷新。"
                if target == self._window.active_display_mode
                else f"已保存到本地配置；当前会话仍保持 Qt，{target} 方案会在下次切换或启动时生效。"
            )

        @Slot(int)
        def _on_display_mode_selected(self, _index: int) -> None:
            self._display_mode_touched = True
            selected_mode = self._selected_mode()
            target = effective_display_mode(selected_mode)
            if target == self._window.active_display_mode:
                self.status.setText(
                    "当前显示方案无需立即切换；点击保存后会写入新的启动偏好。"
                    if selected_mode != self._window.user_settings.display_mode
                    else "当前已经处于所选显示模式。"
                )
                return

            restart_codex = False
            if target == "renderer":
                debugger_available = self._window.renderer_debugger_available()
                message = (
                    "当前 Codex 已开启本地调试端口，HUD 可以直接切换到 Renderer 内嵌模式，无需重启 Codex。"
                    "\n\n是否现在应用？"
                    if debugger_available
                    else "当前 Codex 还不是调试/CDP 启动。要立即切换到 Renderer 内嵌模式，需要先以调试模式重启 Codex App。"
                    "\n\n是否现在重启并应用？"
                )
                title = "立即切换到 Renderer"
                restart_codex = not debugger_available
            elif target == "tk":
                title = "立即切换到 Tk"
                message = (
                    "准备切换到 Tk 独立窗口。HUD 会关闭当前 Qt 窗口，并打开新的 Tk 悬浮窗。"
                    "\n\n是否现在应用？"
                )
            else:
                title = "立即切换 HUD 显示方案"
                message = "是否现在应用新的 HUD 显示方案？"

            answer = QMessageBox.question(self, title, message)
            if answer != QMessageBox.StandardButton.Yes:
                self.status.setText("已保留方案选择，点击保存后会在下次切换或下次启动时生效。")
                return
            config = self._save_config()
            self.status.setText("正在应用新的 HUD 显示方案...")
            self._window.request_mode_switch(
                effective_display_mode(config.display_mode),
                restart_codex=restart_codex,
            )

        @Slot()
        def _fetch_prices(self) -> None:
            url = self.pricing_url.text().strip()
            try:
                fetched = fetch_model_prices(url)
                config = self._save_config().with_price_updates(fetched, pricing_url=url)
                self._window.user_settings_store.save(config)
            except (OSError, ValueError) as exc:
                self.status.setText(f"拉取失败：{exc}")
                return
            self._window.user_settings = config
            self._replace_price_rows(config.model_prices)
            self.status.setText(f"已拉取并保存 {len(fetched)} 个模型价格。")

        @Slot()
        def _export_json(self) -> None:
            try:
                config = self._save_config()
                payload = json.dumps({"user": config.to_dict()}, indent=2, ensure_ascii=False)
                QApplication.clipboard().setText(payload)
            except (OSError, ValueError) as exc:
                self.status.setText(f"导出失败：{exc}")
                return
            self.status.setText("设置 JSON 已复制到剪贴板。")

        @Slot()
        def _confirm_exit(self) -> None:
            answer = QMessageBox.question(
                self,
                "退出 HUD",
                "这会完全退出 HUD，并停止后台守护进程（如果当前正在运行）。\n\n是否继续？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.status.setText("已取消退出。")
                return
            self.status.setText("正在退出 HUD...")
            self._window.close("settings_exit")

        def _refresh_update_state_label(self) -> None:
            state_text = ""
            manager = self._window.update_manager
            if manager is not None and hasattr(manager, "status"):
                try:
                    state = manager.status()
                    state_text = getattr(state, "title", "") or getattr(state, "message", "")
                except Exception:
                    state_text = ""
            self.update_state_label.setText(state_text or "尚未检查更新。")

        @Slot()
        def _check_update(self) -> None:
            manager = self._window.update_manager
            if manager is not None and hasattr(manager, "request_check"):
                state = manager.request_check(auto_download=False)
                self.status.setText(getattr(state, "message", "") or "正在检查更新...")
                self._refresh_update_state_label()
                return
            info = check_for_update(current_version=__version__)
            self.status.setText(format_update_info(info))
            self.update_state_label.setText(format_update_info(info))

        @Slot()
        def _install_update(self) -> None:
            manager = self._window.update_manager
            if manager is not None and hasattr(manager, "request_install"):
                state = manager.request_install()
                self.status.setText(getattr(state, "message", "") or "正在准备安装更新...")
                self._refresh_update_state_label()
                return
            info = check_for_update(current_version=__version__)
            if info.error or not info.available:
                message = format_update_info(info)
                self.status.setText(message)
                self.update_state_label.setText(message)
                return
            try:
                installer = download_update_asset(info)
                launch_installer(installer)
            except Exception as exc:
                self.status.setText(f"安装更新失败：{exc}")
                return
            self.status.setText(f"已启动 {installer.name}，安装器会先关闭当前 HUD。")


    class _HeaderRoiDemoWidget(QWidget):
        def __init__(
            self,
            *,
            border: str = "#FF3030",
            fill: QColor | None = None,
        ) -> None:
            window_type = Qt.WindowType
            flags = (
                window_type.FramelessWindowHint
                | window_type.Tool
            )
            if not sys.platform.startswith("win"):
                flags |= window_type.WindowStaysOnTopHint
            transparent_input = getattr(window_type, "WindowTransparentForInput", 0)
            if transparent_input:
                flags |= transparent_input
            super().__init__(None, flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._border = QColor(border)
            self._fill = fill if fill is not None else QColor(255, 48, 48, 220)
            self._border_width = 6
            self.hide()

        def update_roi(self, rect: WindowRect | None) -> None:
            if rect is None or rect.width <= 0 or rect.height <= 0:
                self.hide()
                return
            self.setGeometry(QRect(rect.left, rect.top, rect.width, rect.height))
            self.show()
            self.raise_()
            self.update()

        def paintEvent(self, event: QPaintEvent) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border)
            pen.setWidth(self._border_width)
            painter.setPen(pen)
            painter.setBrush(self._fill)
            inset = max(1, self._border_width // 2)
            painter.drawRect(self.rect().adjusted(inset, inset, -inset - 1, -inset - 1))
            painter.end()


    class _DockEventEmitter(QObject):
        dock_event = Signal(str)

        def emit_event(self, reason: str) -> None:
            self.dock_event.emit(str(reason or "event"))


    class _QtHudWindowImpl:
        active_display_mode = "qt"

        def __init__(
            self,
            *,
            compact: bool = False,
            hide_until_attached: bool = False,
            tombstone_follow_ms: int = 500,
            user_settings_store: UserConfigStore | None = None,
            hud_settings_store: HudSettingsStore | None = None,
            update_manager: Any | None = None,
        ) -> None:
            del compact
            self.app = QApplication.instance() or QApplication(sys.argv[:1])
            self.app.setQuitOnLastWindowClosed(False)
            self.user_settings_store = user_settings_store or UserConfigStore()
            self.user_settings = self.user_settings_store.load()
            self.settings_store = hud_settings_store or HudSettingsStore()
            self.settings = self.settings_store.load()
            self.update_manager = update_manager
            self.hide_until_attached = bool(hide_until_attached)
            self.tombstone_follow_ms = max(50, int(tombstone_follow_ms))
            self._attached = False
            self._last_rect: WindowRect | None = None
            self._hud_hidden_by_follow = False
            self._header_roi_demo_enabled = _env_flag(
                HUD_UIA_ROI_DEMO_ENV,
                default=True,
            )
            self.locator = CodexWindowLocator()
            try:
                self.locator.set_dpi_aware()
            except Exception:
                pass
            self._event_dock: Any | None = None
            self._event_dock_started = False
            self._dock_event_emitter = _DockEventEmitter()
            self._dock_event_emitter.dock_event.connect(self._handle_dock_event)
            self.exit_reason = ""
            self._mode_switch_request = ""
            self._restart_codex_for_renderer = False
            self._latest_payload: RendererHudPayload | None = None
            self._last_snapshot: ParsedSession | None = None
            self._last_update_state: dict[str, object] = {}
            self._theme_tokens: dict[str, str] = dict(QT_THEME_DEFAULTS)
            self._theme_signature = ""
            self._theme_probe = CodexThemeProbe(
                timeout_seconds=0.08,
                cache_seconds=0.8,
                failure_cooldown_seconds=5.0,
            )
            self._interaction_block_until = 0.0
            self._click_priority_hold_until = 0.0
            self._pointer_priority_hold_until = 0.0
            self._session_manual_targets: set[str] = set()
            self._settings_dialog: _SettingsDialog | None = None
            self.top_window = _TopPanel(
                width=self._panel_width("top"),
                expanded_height=self._panel_expanded_height("top"),
                on_settings=self.open_settings,
                on_update_action=self._handle_update_action,
                on_dismiss_warnings=self._dismiss_warnings_today,
                on_interaction=self._mark_interaction,
                on_click_priority=self._mark_click_priority,
                on_pointer_priority=self._mark_pointer_priority,
                on_geometry_changed=self._remember_panel_geometry,
                on_pin_toggle=self.toggle_pin,
            )
            self.request_window = _RequestPanel(
                width=self._panel_width("request"),
                expanded_height=self._panel_expanded_height("request"),
                on_interaction=self._mark_interaction,
                on_click_priority=self._mark_click_priority,
                on_pointer_priority=self._mark_pointer_priority,
                on_geometry_changed=self._remember_panel_geometry,
                on_pin_toggle=self.toggle_pin,
            )
            self._sync_pin_buttons()
            self._header_roi_overlay: _HeaderRoiDemoWidget | None = (
                _HeaderRoiDemoWidget() if self._header_roi_demo_enabled else None
            )
            self._bottom_roi_overlay: _HeaderRoiDemoWidget | None = (
                _HeaderRoiDemoWidget(
                    border="#178BFF",
                    fill=QColor(23, 139, 255, 220),
                )
                if self._header_roi_demo_enabled
                else None
            )
            self._apply_theme_tokens(self._theme_tokens)
            self._event_dock_started = self._start_event_dock()
            self._place_windows()
            if self._should_show_hud():
                self.top_window.show()
                self.request_window.show()
            self._clock_timer = QTimer()
            self._clock_timer.timeout.connect(self._refresh_latest_payload)
            self._clock_timer.start(1000)
            self._follow_timer = QTimer()
            self._follow_timer.timeout.connect(self._follow_codex_window)
            if not self._owner_z_order_active():
                self._follow_timer.start(
                    max(50, min(QT_HUD_FOLLOW_MS, self.tombstone_follow_ms))
                )

        @property
        def mode_switch_request(self) -> str:
            return self._mode_switch_request

        @property
        def restart_codex_for_renderer(self) -> bool:
            return self._restart_codex_for_renderer

        def _start_event_dock(self) -> bool:
            if not sys.platform.startswith("win"):
                return False
            if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
                return False
            if not _env_flag(QT_HUD_EVENT_DOCK_ENV, default=True):
                return False
            try:
                from ..platforms.windows_event_dock import (
                    WindowsEventDockBridge,
                    event_dock_enabled_from_env,
                )
            except Exception:
                return False
            if not event_dock_enabled_from_env(default=True):
                return False
            try:
                self._event_dock = WindowsEventDockBridge(
                    on_event=self._dock_event_emitter.emit_event,
                    hud_hwnds=self._hud_hwnds,
                )
                return bool(self._event_dock.start())
            except Exception:
                self._event_dock = None
                return False

        def _owner_z_order_active(self) -> bool:
            return bool(
                self._event_dock_started
                and self._event_dock is not None
                and getattr(self._event_dock, "active", False)
            )

        def _handle_dock_event(self, _reason: str) -> None:
            if self._geometry_interaction_active():
                return
            self._follow_codex_window()

        def should_defer_background_work(self) -> bool:
            return self._manual_input_active()

        def should_refresh_snapshot(self) -> bool:
            return not self._manual_input_active()

        def refresh_delay_ms(self, normal_delay_ms: int) -> int:
            if self.hide_until_attached and not self._attached:
                return self.tombstone_follow_ms
            if self._click_priority_active():
                return QT_HUD_CLICK_REFRESH_DELAY_MS
            if self._pointer_priority_active():
                return QT_HUD_POINTER_REFRESH_DELAY_MS
            if time.monotonic() < self._interaction_block_until:
                return max(160, int(normal_delay_ms))
            return max(100, int(normal_delay_ms))

        def update_display(
            self,
            snapshot: ParsedSession,
            *,
            update_state: Any | None = None,
        ) -> None:
            if update_state is None and self.update_manager is not None and hasattr(self.update_manager, "status"):
                try:
                    update_state = self.update_manager.status()
                except Exception:
                    update_state = None
            update_payload = (
                update_state.to_dict()
                if hasattr(update_state, "to_dict")
                else dict(update_state or {})
                if isinstance(update_state, Mapping)
                else {}
            )
            reset_manual_geometry = self._reset_manual_geometry_for_session_switch(
                snapshot,
            )
            self._last_snapshot = snapshot
            self._last_update_state = dict(update_payload)
            self._latest_payload = self._payload_from_snapshot(snapshot, update_payload)
            self._follow_codex_window()
            if reset_manual_geometry and self._attached and self._last_rect is not None:
                self.attach_to_rect(self._last_rect)
            self._apply_payload(self._latest_payload.to_json())
            if self._should_show_hud() and not self.top_window.isVisible():
                self.top_window.show()
            if self._should_show_hud() and not self.request_window.isVisible():
                self.request_window.show()

        def _payload_from_snapshot(
            self,
            snapshot: ParsedSession,
            update_payload: Mapping[str, object] | None = None,
        ) -> RendererHudPayload:
            theme_snapshot = self._theme_probe.snapshot()
            return payload_from_snapshot(
                snapshot,
                settings=self.user_settings,
                active_display_mode=self.active_display_mode,
                settings_path=self.user_settings_store.path,
                theme=_renderer_theme_payload(theme_snapshot),
                update_state=dict(update_payload or {}),
            )

        def run(self) -> None:
            if self._should_show_hud() and not self.top_window.isVisible():
                self.top_window.show()
            if self._should_show_hud() and not self.request_window.isVisible():
                self.request_window.show()
            self.app.exec()

        def close(self, reason: str = "") -> None:
            if reason and not self.exit_reason:
                self.exit_reason = reason
            if self._event_dock is not None:
                try:
                    self._event_dock.stop()
                except Exception:
                    pass
                self._event_dock = None
                self._event_dock_started = False
            for timer in (getattr(self, "_clock_timer", None), getattr(self, "_follow_timer", None)):
                if timer is not None:
                    timer.stop()
            if self._settings_dialog is not None:
                self._settings_dialog.close()
                self._settings_dialog.deleteLater()
                self._settings_dialog = None
            if self._header_roi_overlay is not None:
                self._header_roi_overlay.hide()
                self._header_roi_overlay.close()
                self._header_roi_overlay.deleteLater()
                self._header_roi_overlay = None
            if self._bottom_roi_overlay is not None:
                self._bottom_roi_overlay.hide()
                self._bottom_roi_overlay.close()
                self._bottom_roi_overlay.deleteLater()
                self._bottom_roi_overlay = None
            for window in (self.top_window, self.request_window):
                window.hide()
                window.close()
                window.deleteLater()
            self.app.processEvents()
            self.app.quit()

        def open_settings(self) -> None:
            self._mark_interaction()
            if self._settings_dialog is None:
                self._settings_dialog = _SettingsDialog(self)
            if not self._settings_dialog.isVisible():
                self._center_settings_dialog()
            self._settings_dialog.show()
            if self._event_dock is not None and self._last_rect is not None and self._last_rect.hwnd:
                try:
                    self._event_dock.bind_to_owner(int(self._last_rect.hwnd), self._hud_hwnds())
                except Exception:
                    pass
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()

        def _center_settings_dialog(self) -> None:
            if self._settings_dialog is None:
                return
            target_rect = self._settings_center_rect()
            width = max(1, self._settings_dialog.frameGeometry().width())
            height = max(1, self._settings_dialog.frameGeometry().height())
            x = target_rect.x() + max(0, (target_rect.width() - width) // 2)
            y = target_rect.y() + max(0, (target_rect.height() - height) // 2)
            self._settings_dialog.move(x, y)

        def _settings_center_rect(self) -> QRect:
            try:
                rect = self.locator.find()
            except Exception:
                rect = None
            if rect is not None and rect.width > 0 and rect.height > 0:
                return QRect(rect.left, rect.top, rect.width, rect.height)
            screen = self.app.screenAt(self.top_window.pos()) or self.app.primaryScreen()
            if screen is None:
                return QRect(0, 0, 1, 1)
            return screen.availableGeometry()

        @staticmethod
        def renderer_debugger_available(timeout_seconds: float = 0.35) -> bool:
            try:
                target = pick_page_target(list_targets(cdp_port_from_env(), timeout_seconds))
            except Exception:
                return False
            return bool(target.get("webSocketDebuggerUrl"))

        def request_mode_switch(self, target: str, *, restart_codex: bool = False) -> None:
            self._mode_switch_request = target
            self._restart_codex_for_renderer = bool(restart_codex and target == "renderer")
            self.close("display_mode_switch")

        def _handle_update_action(self) -> None:
            self._mark_interaction()
            manager = self.update_manager
            if manager is None or not hasattr(manager, "handle_click"):
                return
            try:
                state = manager.handle_click()
            except Exception:
                return
            payload = state.to_dict() if hasattr(state, "to_dict") else dict(state or {}) if isinstance(state, Mapping) else {}
            self.top_window._render_update_button(payload)
            if self._settings_dialog is not None:
                message = str(payload.get("message") or payload.get("title") or "")
                if message:
                    self._settings_dialog.status.setText(message)
                self._settings_dialog._refresh_update_state_label()

        def _dismiss_warnings_today(self) -> None:
            self._mark_interaction()
            try:
                dismiss_warning_for_today(self.user_settings_store.path)
            except OSError:
                return
            if self._last_snapshot is not None:
                self._latest_payload = self._payload_from_snapshot(
                    self._last_snapshot,
                    self._last_update_state,
                )
            self.top_window.hide_warning()

        def _apply_payload(self, payload: Mapping[str, object]) -> None:
            self._apply_payload_theme(payload)
            self.top_window.update_payload(payload)
            self.request_window.update_payload(payload)

        def _apply_payload_theme(self, payload: Mapping[str, object]) -> None:
            theme = payload.get("theme") if isinstance(payload.get("theme"), Mapping) else {}
            tokens = theme.get("tokens") if isinstance(theme, Mapping) and isinstance(theme.get("tokens"), Mapping) else {}
            if not isinstance(tokens, Mapping):
                return
            next_tokens = dict(QT_THEME_DEFAULTS)
            next_tokens.update({str(key): str(value) for key, value in tokens.items()})
            self._apply_theme_tokens(next_tokens)

        def _apply_theme_tokens(self, tokens: Mapping[str, str]) -> None:
            normalized = dict(QT_THEME_DEFAULTS)
            normalized.update({str(key): str(value) for key, value in tokens.items()})
            signature = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if signature == self._theme_signature:
                return
            self._theme_signature = signature
            self._theme_tokens = normalized
            stylesheet = _qt_stylesheet(normalized)
            self.top_window.setStyleSheet(stylesheet)
            self.request_window.setStyleSheet(stylesheet)
            self.top_window.apply_theme(normalized)
            self.request_window.apply_theme(normalized)
            if self._settings_dialog is not None:
                self._settings_dialog.setStyleSheet(stylesheet)

        def _refresh_latest_payload(self) -> None:
            if not self._owner_z_order_active():
                self._follow_codex_window()
            if self._latest_payload is not None:
                self._apply_payload(self._latest_payload.to_json())
            if self._should_show_hud():
                if not self.top_window.isVisible():
                    self.top_window.show()
                if not self.request_window.isVisible():
                    self.request_window.show()

        def _mark_interaction(self) -> None:
            self._interaction_block_until = time.monotonic() + (
                QT_HUD_INTERACTION_IDLE_MS / 1000.0
            )

        def _mark_click_priority(self) -> None:
            self._click_priority_hold_until = max(
                self._click_priority_hold_until,
                time.monotonic() + (QT_HUD_CLICK_PRIORITY_MS / 1000.0),
            )
            self._mark_interaction()

        def _mark_pointer_priority(self) -> None:
            self._pointer_priority_hold_until = max(
                self._pointer_priority_hold_until,
                time.monotonic() + (QT_HUD_POINTER_PRIORITY_MS / 1000.0),
            )
            self._mark_interaction()

        def _click_priority_active(self) -> bool:
            return time.monotonic() < self._click_priority_hold_until

        def _pointer_priority_active(self) -> bool:
            return time.monotonic() < self._pointer_priority_hold_until

        def _manual_input_active(self) -> bool:
            return self._click_priority_active() or self._pointer_priority_active()

        def _placement(self, target: str) -> Any:
            return self.settings.top if target == "top" else self.settings.request

        def _panel_for_target(self, target: str) -> _PanelWindow:
            return self.top_window if target == "top" else self.request_window

        def _use_saved_panel_geometry(self, target: str) -> bool:
            placement = self._placement(target)
            return bool(placement.pinned or target in self._session_manual_targets)

        def _sync_pin_buttons(self) -> None:
            self.top_window.set_pinned(bool(self.settings.top.pinned))
            self.request_window.set_pinned(bool(self.settings.request.pinned))

        def _capture_panel_geometry(self, target: str, panel: _PanelWindow) -> None:
            placement = self._placement(target)
            placement.absolute_x = int(panel.x())
            placement.absolute_y = int(panel.y())
            placement.width = max(120, int(panel.width()))
            placement.height = max(1, int(panel.height()))
            if panel.expanded:
                placement.height = max(panel._minimum_expanded_height(), int(panel.height()))

        def _clear_session_manual_geometry(self, target: str) -> None:
            self._session_manual_targets.discard(target)
            self._panel_for_target(target)._manual_positioned = False

        @staticmethod
        def _snapshot_session_id(snapshot: ParsedSession | None) -> str:
            if snapshot is None:
                return ""
            return str(getattr(snapshot, "session_id", "") or "").strip()

        def _reset_manual_geometry_for_session_switch(
            self,
            next_snapshot: ParsedSession,
        ) -> bool:
            previous_session_id = self._snapshot_session_id(self._last_snapshot)
            next_session_id = self._snapshot_session_id(next_snapshot)
            if (
                not previous_session_id
                or not next_session_id
                or previous_session_id == next_session_id
            ):
                return False
            changed = False
            for target in ("top", "request"):
                if target not in self._session_manual_targets:
                    continue
                placement = self._placement(target)
                if placement.pinned:
                    continue
                placement.clear_geometry()
                self._clear_session_manual_geometry(target)
                changed = True
            return changed

        def toggle_pin(self, target: str) -> None:
            self._mark_click_priority()
            placement = self._placement(target)
            panel = self._panel_for_target(target)
            if placement.pinned:
                placement.pinned = False
                placement.clear_geometry()
                self._clear_session_manual_geometry(target)
                self.settings_store.save(self.settings)
                self._sync_pin_buttons()
                if self._attached and self._last_rect is not None:
                    self.attach_to_rect(self._last_rect)
                else:
                    self._place_windows()
                return
            placement.clear_geometry()
            placement.pinned = True
            self._capture_panel_geometry(target, panel)
            panel._manual_positioned = True
            self.settings_store.save(self.settings)
            self._sync_pin_buttons()

        def _panel_width(self, target: str) -> int:
            default = QT_HUD_TOP_WIDTH if target == "top" else QT_HUD_REQUEST_WIDTH
            placement = self._placement(target)
            width = placement.width if self._use_saved_panel_geometry(target) else None
            return max(120, int(width or default))

        def _panel_expanded_height(self, target: str) -> int:
            default = (
                QT_HUD_TOP_EXPANDED_HEIGHT
                if target == "top"
                else QT_HUD_REQUEST_EXPANDED_HEIGHT
            )
            minimum = 240 if target == "top" else 120
            placement = self._placement(target)
            height = placement.height if self._use_saved_panel_geometry(target) else None
            return max(minimum, int(height or default))

        def _attached_panel_geometry(
            self,
            target: str,
            rect: WindowRect,
            expanded: bool,
        ) -> tuple[int, int, int, int]:
            panel = self.top_window if target == "top" else self.request_window
            height = panel._target_height(expanded)
            x, y, width, height = _automatic_hud_geometry(
                self.locator,
                target,
                rect,
                height,
                expanded=expanded,
                use_roi=True,
            )
            placement = self._placement(target)
            if placement.has_pinned_position():
                x = int(placement.absolute_x or x)
                y = int(placement.absolute_y or y)
                width = max(120, int(placement.width or width))
                return x, y, width, height
            elif target in self._session_manual_targets:
                x = int(panel.x())
                y = int(panel.y())
                width = max(120, int(placement.width or panel.width() or width))
                return x, y, width, height
            min_x = rect.left + QT_HUD_MARGIN
            max_x = max(min_x, rect.right - width - QT_HUD_MARGIN)
            min_y = rect.top + QT_HUD_MARGIN
            max_y = max(min_y, rect.bottom - height - QT_HUD_MARGIN)
            x = max(min_x, min(x, max_x))
            y = max(min_y, min(y, max_y))
            return x, y, width, height

        def _remember_panel_geometry(
            self,
            target: str,
            panel: _PanelWindow,
            reason: str,
        ) -> None:
            placement = self._placement(target)
            self._session_manual_targets.add(target)
            panel._manual_positioned = True
            placement.absolute_x = int(panel.x())
            placement.absolute_y = int(panel.y())
            placement.width = max(120, int(panel.width()))
            placement.relative_x = None
            placement.relative_y = None
            placement.relative_bottom = None
            placement.relative_x_ratio = None
            placement.relative_y_ratio = None
            placement.relative_bottom_ratio = None
            placement.anchor_x_ratio = None
            placement.anchor_y_ratio = None
            placement.anchor_source = None
            placement.width_ratio = None
            placement.collapsed_width_locked = False
            if panel.expanded or reason == "resize":
                placement.height = max(1, int(panel.height()))
                if panel.expanded:
                    placement.height = max(panel._minimum_expanded_height(), int(panel.height()))
            self.settings_store.save(self.settings)

        def _place_windows(self) -> None:
            try:
                rect = self.locator.find()
            except Exception:
                rect = None
            if rect is not None and not getattr(rect, "minimized", False):
                self.attach_to_rect(rect)
                if not self._owner_z_order_active():
                    try:
                        active = self.locator.is_active(rect, self._hud_hwnds())
                    except Exception:
                        active = True
                    if not active:
                        self._hide_for_follow()
                return

            screen = self.app.primaryScreen()
            geometry = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
            top_saved = self.settings.top.has_pinned_position()
            request_saved = self.settings.request.has_pinned_position()
            if top_saved:
                self.top_window.move(
                    int(self.settings.top.absolute_x or 0),
                    int(self.settings.top.absolute_y or 0),
                )
                self.top_window._manual_positioned = True
            if request_saved:
                self.request_window.move(
                    int(self.settings.request.absolute_x or 0),
                    int(self.settings.request.absolute_y or 0),
                )
                self.request_window._manual_positioned = True
            if not top_saved:
                top_x = geometry.left() + max(0, (geometry.width() - self.top_window.width()) // 2)
                top_y = geometry.top() + QT_HUD_MARGIN
                self.top_window.move(top_x, top_y)
                self.top_window._manual_positioned = False
            if not request_saved:
                req_x = geometry.right() - self.request_window.width() - QT_HUD_MARGIN
                req_y = geometry.bottom() - self.request_window.height() - QT_HUD_MARGIN
                self.request_window.move(max(geometry.left(), req_x), max(geometry.top(), req_y))
                self.request_window._manual_positioned = False
            if self.hide_until_attached and not top_saved and not request_saved:
                self._hide_for_follow()

        def _should_show_hud(self) -> bool:
            return (not self.hide_until_attached or self._attached) and not self._hud_hidden_by_follow

        def _geometry_interaction_active(self) -> bool:
            return (
                time.monotonic() < self._interaction_block_until
                or self._manual_input_active()
                or self._hud_window_active()
                or self.top_window.geometry_interaction_active()
                or self.request_window.geometry_interaction_active()
            )

        def _hud_window_active(self) -> bool:
            active_window = self.app.activeWindow()
            windows: list[QWidget] = [self.top_window, self.request_window]
            if self._settings_dialog is not None:
                windows.append(self._settings_dialog)
            if any(active_window is window for window in windows):
                return True
            cursor = QCursor.pos()
            for window in windows:
                if window.isVisible() and window.geometry().contains(cursor):
                    return True
            return False

        def _follow_codex_window(self) -> bool:
            if self._geometry_interaction_active():
                return self._attached
            try:
                rect = self.locator.find()
            except Exception:
                rect = None
            if rect is None:
                if self.hide_until_attached:
                    self._hide_for_follow()
                    self._attached = False
                    self._last_rect = None
                else:
                    self._enter_free_mode()
                return False
            if getattr(rect, "minimized", False):
                self._hide_for_follow()
                self._attached = False
                return False
            if not self._owner_z_order_active():
                try:
                    active = self.locator.is_active(rect, self._hud_hwnds())
                except Exception:
                    active = True
                if not active:
                    self._attached = True
                    self._last_rect = rect
                    self._hide_for_follow()
                    return False
            self.attach_to_rect(rect)
            return True

        def _hud_hwnds(self) -> set[int]:
            hwnds: set[int] = set()
            windows: list[QWidget] = [self.top_window, self.request_window]
            if self._header_roi_overlay is not None:
                windows.append(self._header_roi_overlay)
            if self._bottom_roi_overlay is not None:
                windows.append(self._bottom_roi_overlay)
            if self._settings_dialog is not None:
                windows.append(self._settings_dialog)
            for window in windows:
                try:
                    hwnd = int(window.winId())
                except Exception:
                    continue
                if hwnd:
                    hwnds.add(hwnd)
            return hwnds

        def _hide_for_follow(self) -> None:
            self.top_window.hide()
            self.request_window.hide()
            self._hide_header_roi_demo()
            self._hide_bottom_roi_demo()
            self._hud_hidden_by_follow = True

        def _enter_free_mode(self) -> None:
            self._attached = False
            self._last_rect = None
            self._hide_header_roi_demo()
            self._hide_bottom_roi_demo()
            self._hud_hidden_by_follow = False
            if self._should_show_hud():
                if not self.top_window.isVisible():
                    self.top_window.show()
                if not self.request_window.isVisible():
                    self.request_window.show()

        @staticmethod
        def _same_hwnd_rect_jitter(previous: WindowRect | None, current: WindowRect) -> bool:
            if previous is None:
                return False
            if not previous.hwnd or previous.hwnd != current.hwnd:
                return False
            if previous.width != current.width or previous.height != current.height:
                return False
            max_delta = max(
                abs(int(previous.left) - int(current.left)),
                abs(int(previous.top) - int(current.top)),
                abs(int(previous.right) - int(current.right)),
                abs(int(previous.bottom) - int(current.bottom)),
            )
            return 0 < max_delta <= QT_HUD_SAME_HWND_RECT_JITTER_PX

        def attach_to_rect(self, rect: WindowRect) -> None:
            self._attached = True
            self._last_rect = rect
            if self._event_dock is not None and getattr(rect, "hwnd", 0):
                try:
                    self._event_dock.bind_to_owner(int(rect.hwnd), self._hud_hwnds())
                except Exception:
                    pass
            for target, panel in (("top", self.top_window), ("request", self.request_window)):
                if not panel._manual_positioned:
                    x, y, width, _height = self._attached_panel_geometry(
                        target,
                        rect,
                        panel.expanded,
                    )
                    self._apply_panel_geometry(panel, x, y, width)
            self._hud_hidden_by_follow = False
            if self._should_show_hud():
                if not self.top_window.isVisible():
                    self.top_window.show()
                if not self.request_window.isVisible():
                    self.request_window.show()
            self._sync_header_roi_demo(rect)
            self._sync_bottom_roi_demo(rect)

        def _apply_panel_geometry(
            self,
            panel: "_PanelWindow",
            x: int,
            y: int,
            width: int,
        ) -> None:
            height = int(panel.height())
            panel.resize(int(width), height)
            panel.move(int(x), int(y))
            if self._event_dock is None:
                return
            try:
                hwnd = int(panel.winId())
            except Exception:
                return
            try:
                self._event_dock.set_hud_geometry(hwnd, int(x), int(y), int(width), height)
            except Exception:
                return

        def _sync_header_roi_demo(self, rect: WindowRect | None) -> None:
            overlay = self._header_roi_overlay
            if overlay is None:
                return
            if rect is None or getattr(rect, "minimized", False):
                overlay.update_roi(None)
                return
            try:
                roi = self.locator.header_roi_geometry(rect)
            except Exception:
                roi = None
            overlay.update_roi(roi)

        def _hide_header_roi_demo(self) -> None:
            overlay = self._header_roi_overlay
            if overlay is None:
                return
            overlay.update_roi(None)

        def _sync_bottom_roi_demo(self, rect: WindowRect | None) -> None:
            overlay = self._bottom_roi_overlay
            if overlay is None:
                return
            if rect is None or getattr(rect, "minimized", False):
                overlay.update_roi(None)
                return
            try:
                roi = self.locator.bottom_roi_geometry(rect)
            except Exception:
                roi = None
            overlay.update_roi(roi)

        def _hide_bottom_roi_demo(self) -> None:
            overlay = self._bottom_roi_overlay
            if overlay is None:
                return
            overlay.update_roi(None)


def _qt_hex_rgb(value: object, fallback: str = "#000000") -> tuple[int, int, int]:
    text = str(value or fallback).strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        text = str(fallback).strip().lstrip("#")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        fallback_text = str(fallback).strip().lstrip("#")
        return (
            int(fallback_text[0:2], 16),
            int(fallback_text[2:4], 16),
            int(fallback_text[4:6], 16),
        )


def _qt_rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        *(max(0, min(255, int(channel))) for channel in rgb)
    )


def _qt_mix_hex(left: object, right: object, ratio: float) -> str:
    ratio = max(0.0, min(1.0, float(ratio)))
    inverse = 1.0 - ratio
    left_rgb = _qt_hex_rgb(left)
    right_rgb = _qt_hex_rgb(right)
    return _qt_rgb_hex(
        (
            round((left_rgb[0] * inverse) + (right_rgb[0] * ratio)),
            round((left_rgb[1] * inverse) + (right_rgb[1] * ratio)),
            round((left_rgb[2] * inverse) + (right_rgb[2] * ratio)),
        )
    )


def _qt_rgba(value: object, alpha: int) -> str:
    red, green, blue = _qt_hex_rgb(value)
    return f"rgba({red}, {green}, {blue}, {max(0, min(255, int(alpha)))})"


def _qt_luma(value: object) -> float:
    channels = []
    for channel in _qt_hex_rgb(value):
        normalized = channel / 255.0
        if normalized <= 0.03928:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return (channels[0] * 0.2126) + (channels[1] * 0.7152) + (channels[2] * 0.0722)


def _qt_contrast(left: object, right: object) -> float:
    left_luma = _qt_luma(left)
    right_luma = _qt_luma(right)
    lighter = max(left_luma, right_luma)
    darker = min(left_luma, right_luma)
    return (lighter + 0.05) / (darker + 0.05)


def _qt_readable_text(background: object, primary: object, secondary: object) -> str:
    if _qt_contrast(background, primary) >= _qt_contrast(background, secondary):
        return str(primary)
    return str(secondary)


def _qt_stylesheet(tokens: Mapping[str, str] | None = None) -> str:
    theme = dict(QT_THEME_DEFAULTS)
    theme.update({str(key): str(value) for key, value in dict(tokens or {}).items()})
    is_light = _qt_luma(theme["surface"]) > _qt_luma(theme["text"])
    themed = {
        "shellBackground": _qt_rgba(theme["surface"], 236),
        "panelHeaderBackground": _qt_rgba(theme["headerSurface"], 232),
        "panelHairline": _qt_rgba(theme["panelBorder"], 210),
        "cardBackground": _qt_mix_hex(
            theme["panelSurface"],
            theme["text"],
            0.025 if is_light else 0.045,
        ),
        "cardBorder": _qt_mix_hex(
            theme["panelBorder"],
            theme["text"],
            0.04 if is_light else 0.08,
        ),
        "warningPanelBackground": _qt_mix_hex(
            theme["surface"],
            theme["warning"],
            0.14 if is_light else 0.26,
        ),
        "warningChipBackground": _qt_mix_hex(
            theme["surface"],
            theme["warning"],
            0.20 if is_light else 0.34,
        ),
        "activityTrailBackground": _qt_rgba(theme["requestPanelSurface"], 220),
        "activityBorder": _qt_rgba(theme["panelBorder"], 190),
        "activityLine": _qt_mix_hex(theme["requestMuted"], theme["requestText"], 0.18),
        "activityDotBorder": theme["requestPanelSurface"],
        "chipBackground": _qt_mix_hex(
            theme["headerSurface"],
            theme["text"],
            0.03 if is_light else 0.06,
        ),
        "chipWarningBackground": _qt_mix_hex(
            theme["surface"],
            theme["warning"],
            0.18 if is_light else 0.30,
        ),
        "buttonBackground": _qt_mix_hex(
            theme["headerSurface"],
            theme["text"],
            0.035 if is_light else 0.06,
        ),
        "buttonHoverBackground": _qt_mix_hex(
            theme["headerSurface"],
            theme["progressCache"],
            0.08 if is_light else 0.18,
        ),
        "settingsActionBackground": _qt_mix_hex(
            theme["headerSurface"],
            theme["text"],
            0.08 if is_light else 0.14,
        ),
        "primaryActionText": _qt_readable_text(
            theme["accent"],
            theme["surface"],
            theme["text"],
        ),
        "inputSelectionBackground": _qt_mix_hex(theme["info"], theme["accent"], 0.35),
        "settingsStatus": _qt_mix_hex(theme["muted"], theme["text"], 0.22),
        "settingsChromeBackground": _qt_mix_hex(
            theme["surface"],
            theme["headerSurface"],
            0.58 if is_light else 0.75,
        ),
        "settingsControlBackground": _qt_mix_hex(
            theme["surface"],
            theme["text"],
            0.025 if is_light else 0.06,
        ),
        "settingsControlHover": _qt_mix_hex(
            theme["surface"],
            theme["progressCache"],
            0.10 if is_light else 0.18,
        ),
        "settingsPopupBackground": _qt_mix_hex(
            theme["surface"],
            theme["text"],
            0.04 if is_light else 0.10,
        ),
        "settingsPopupSelection": _qt_mix_hex(
            theme["surface"],
            theme["accent"],
            0.16 if is_light else 0.26,
        ),
        "scrollbarBackground": _qt_rgba(theme["text"], 18 if is_light else 10),
    }
    css = """
    QWidget {
        font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
        font-size: 12px;
        color: #DCE7F2;
    }
    QFrame#qtHudShell {
        background: rgba(16, 22, 29, 236);
        border: 1px solid #2C3745;
        border-radius: 8px;
    }
    QFrame#qtHudShell[target="top"][expanded="true"] {
        background: #10161D;
    }
    QFrame#qtHudShell[target="request"] {
        background: #111820;
    }
    QFrame#qtHudShell[target="request"][expanded="true"] {
        background: #0B1016;
    }
    QFrame#qtHudSettingsDialog {
        background: #10161D;
        border: 0;
        border-radius: 0;
    }
    QFrame#qtHudSettingsHead,
    QFrame#qtHudSettingsActions {
        background: __QT_HUD_SETTINGS_CHROME_BACKGROUND__;
        border: 0;
    }
    QFrame#qtHudSettingsHead {
        border-top-left-radius: 0;
        border-top-right-radius: 0;
    }
    QFrame#qtHudSettingsActions {
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
    }
    QFrame#qtHudPanelHeader,
    QFrame#qtHudRequestExpandedHeader {
        background: rgba(24, 33, 43, 220);
        border: 1px solid rgba(255, 255, 255, 20);
        border-radius: 6px;
    }
    QFrame#qtHudWarningPanel {
        background: rgba(127, 62, 58, 170);
        border: 1px solid #FF875A;
        border-radius: 7px;
    }
    QFrame#qtHudTopCard,
    QFrame#qtHudMetricBox,
    QFrame#qtHudInset,
    QFrame#qtHudTokenChip,
    QFrame#qtHudHeavyRow,
    QFrame#qtHudSupportQrCard {
        background: rgba(255, 255, 255, 18);
        border: 1px solid rgba(255, 255, 255, 24);
        border-radius: 7px;
        padding: 4px;
    }
    QLabel#qtHudSupportQrImage {
        background: #FFFFFF;
        border-radius: 6px;
    }
    QFrame#qtHudRequestCollapsed,
    QFrame#qtHudRequestExpanded,
    QFrame#qtHudRequestSubhead,
    QFrame#qtHudRequestListShell,
    QScrollArea#qtHudRequestScroll,
    QScrollArea#qtHudRequestScroll > QWidget,
    QScrollArea#qtHudRequestScroll QWidget {
        background: #101821;
    }
    QFrame#qtHudRequestListShell {
        border: 1px solid rgba(39, 50, 65, 210);
        border-radius: 4px;
    }
    QFrame#qtHudRequestRowFrame {
        background: transparent;
        border: 0;
    }
    QFrame#qtHudRequestRowFrame[latest="true"] QLabel#qtHudLabel-request,
    QFrame#qtHudRequestRowFrame[latest="true"] QLabel#qtHudLabel-request-time {
        color: #F3D27A;
    }
    QFrame#qtHudRequestCollapsed QLabel#qtHudLabel-strong,
    QFrame#qtHudRequestExpanded QLabel#qtHudLabel-strong,
    QFrame#qtHudRequestExpanded QLabel#qtHudLabel-request {
        color: #4D6075;
    }
    QFrame#qtHudRequestExpanded QLabel#qtHudLabel-caption,
    QFrame#qtHudRequestExpanded QLabel#qtHudLabel-muted {
        color: #718095;
    }
    QScrollArea#qtHudActivityTrailScroll {
        background: rgba(16, 24, 33, 190);
        border: 1px solid rgba(255, 255, 255, 18);
        border-radius: 6px;
        padding: 0;
    }
    QScrollArea#qtHudActivityTrailScroll > QWidget,
    QScrollArea#qtHudActivityTrailScroll QWidget,
    QFrame#qtHudTimeline,
    QFrame#qtHudActivityRow,
    QFrame#qtHudActivityTextBox {
        background: transparent;
        border: 0;
        padding: 0;
    }
    QFrame#qtHudActivityTrailLine,
    QScrollArea#qtHudActivityTrailScroll QFrame#qtHudActivityTrailLine,
    QFrame#qtHudActivityMarkerLine,
    QScrollArea#qtHudActivityTrailScroll QFrame#qtHudActivityMarkerLine {
        background: __QT_HUD_ACTIVITY_LINE__;
        border: 0;
        padding: 0;
    }
    QFrame#qtHudActivityMarkerDot,
    QScrollArea#qtHudActivityTrailScroll QFrame#qtHudActivityMarkerDot {
        background: #5EA7FF;
        border: 1px solid #101821;
        border-radius: 4px;
        padding: 0;
    }
    QFrame#qtHudActivityMarkerDot[active="true"],
    QScrollArea#qtHudActivityTrailScroll QFrame#qtHudActivityMarkerDot[active="true"] {
        background: #F3D27A;
    }
    QLabel#qtHudLabel-title {
        font-size: 15px;
        font-weight: 600;
        color: #F2F6FA;
    }
    QLabel#qtHudLabel-settings-title {
        font-size: 13px;
        font-weight: 700;
        color: #F2F6FA;
    }
    QLabel#qtHudLabel-settings-status {
        color: #A9BCD2;
        font-size: 11px;
    }
    QLabel#qtHudLabel-card-title {
        font-size: 12px;
        font-weight: 600;
        color: #F2F6FA;
    }
    QLabel#qtHudLabel-strong {
        font-weight: 600;
        color: #F2F6FA;
    }
    QLabel#qtHudLabel-metric {
        font-size: 20px;
        font-weight: 700;
        color: #F2F6FA;
    }
    QLabel#qtHudLabel-metric-info {
        font-size: 20px;
        font-weight: 700;
        color: #9CCBFF;
    }
    QLabel#qtHudLabel-chip,
    QLabel#qtHudLabel-chip-warning,
    QLabel#qtHudChip,
    QLabel#qtHudChipWarning {
        background: rgba(28, 38, 50, 230);
        border: 1px solid #334254;
        border-radius: 6px;
        padding: 2px 7px;
        font-weight: 600;
        color: #DCE7F2;
    }
    QLabel#qtHudLabel-chip-warning,
    QLabel#qtHudChipWarning {
        color: #FFD6B0;
        border-color: #FF875A;
        background: rgba(91, 49, 44, 200);
    }
    QLabel#qtHudLabel-handle {
        color: #8D9AAD;
        font-weight: 700;
    }
    QLabel#qtHudLabel-mono-blue {
        font-family: Consolas, "Cascadia Mono", monospace;
        color: #9CCBFF;
        font-weight: 600;
    }
    QLabel#qtHudLabel-mono-accent {
        font-family: Consolas, "Cascadia Mono", monospace;
        color: #F3D27A;
        font-weight: 600;
    }
    QLabel#qtHudLabel-request,
    QLabel#qtHudLabel-request-time {
        font-family: Consolas, "Cascadia Mono", monospace;
        color: #DCE7F2;
        font-size: 11px;
        line-height: 145%;
    }
    QLabel#qtHudLabel-request-time {
        color: #9CCBFF;
        font-weight: 600;
    }
    QLabel#qtHudLabel-activity-time {
        font-family: Consolas, "Cascadia Mono", monospace;
        color: #8D9AAD;
        font-size: 9px;
    }
    QLabel#qtHudLabel-activity-title {
        color: #DCE7F2;
        font-size: 9px;
        font-weight: 800;
    }
    QLabel#qtHudLabel-activity-detail {
        color: #8D9AAD;
        font-size: 9px;
    }
    QLabel#qtHudLabel-muted, QLabel#qtHudLabel-caption {
        color: #8D9AAD;
    }
    QLabel#qtHudLabel-warning {
        color: #FFD6B0;
        background: transparent;
        border: 0;
        padding: 0;
    }
    QLabel#qtHudLabel-warning-dot,
    QLabel#qtHudLabel-warning-title {
        color: #FFD6B0;
        font-weight: 700;
    }
    QLabel#qtHudLabel-strong[state="warning"],
    QLabel#qtHudLabel-title[state="warning"] {
        color: #F3D27A;
    }
    QLabel#qtHudLabel-strong[state="error"],
    QLabel#qtHudLabel-title[state="error"] {
        color: #FF7A7A;
    }
    QPushButton {
        color: #DCE7F2;
        background: #1C2632;
        border: 1px solid #334254;
        border-radius: 6px;
        padding: 5px 10px;
    }
    QPushButton:hover {
        border-color: #5EA7FF;
        background: #223044;
    }
    QPushButton#qtHudIconButton {
        min-width: 24px;
        max-width: 28px;
        padding: 3px;
        font-weight: 700;
    }
    QPushButton#qtHudIconButton[phase="ready"] {
        color: #B5DD92;
        border-color: #B5DD92;
    }
    QPushButton#qtHudIconButton[phase="paused"],
    QPushButton#qtHudIconButton[phase="error"] {
        color: #FFD6B0;
        border-color: #FF875A;
    }
    QPushButton#qtHudSettingsAction {
        border: 0;
        border-radius: 5px;
        background: #2E3846;
        min-height: 28px;
        padding: 4px 9px;
    }
    QPushButton#qtHudSettingsAction[primary="true"] {
        color: __QT_HUD_PRIMARY_ACTION_TEXT__;
        background: #F3D27A;
        font-weight: 700;
    }
    QComboBox {
        color: #DCE7F2;
        background: __QT_HUD_SETTINGS_CONTROL_BACKGROUND__;
        border: 1px solid #334254;
        border-radius: 6px;
        padding: 5px;
    }
    QComboBox:hover {
        background: __QT_HUD_SETTINGS_CONTROL_HOVER__;
    }
    QComboBox QAbstractItemView,
    QComboBox QListView {
        color: #DCE7F2;
        background: __QT_HUD_SETTINGS_POPUP_BACKGROUND__;
        border: 1px solid #334254;
        selection-color: #DCE7F2;
        selection-background-color: __QT_HUD_SETTINGS_POPUP_SELECTION__;
        outline: 0;
    }
    QComboBox QAbstractItemView::item,
    QComboBox QListView::item {
        color: #DCE7F2;
        background: __QT_HUD_SETTINGS_POPUP_BACKGROUND__;
        min-height: 22px;
        padding: 4px 6px;
    }
    QComboBox QAbstractItemView::item:selected,
    QComboBox QListView::item:selected {
        color: #DCE7F2;
        background: __QT_HUD_SETTINGS_POPUP_SELECTION__;
    }
    QLineEdit {
        color: #DCE7F2;
        background: __QT_HUD_SETTINGS_CONTROL_BACKGROUND__;
        border: 1px solid #334254;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #2E6DA8;
    }
    QLineEdit:focus {
        border-color: #5EA7FF;
    }
    QDialog {
        background: #10161D;
    }
    QScrollArea,
    QScrollArea QWidget#qtHudTopBody,
    QWidget#qtHudSettingsPage,
    QFrame#qtHudSettingsBody,
    QScrollArea#qtHudSettingsScroll,
    QScrollArea#qtHudSettingsScroll QWidget {
        background: transparent;
    }
    QScrollArea#qtHudSettingsScroll > QWidget,
    QWidget#qtHudSettingsPage,
    QFrame#qtHudSettingsBody {
        background: #10161D;
    }
    QTabWidget::pane {
        border: 0;
        border-top: 1px solid #202833;
        border-bottom: 1px solid #202833;
        background: #10161D;
        padding: 12px;
    }
    QTabBar {
        background: #10161D;
    }
    QTabBar::tab {
        color: #A9BCD2;
        background: transparent;
        border: 0;
        border-radius: 5px;
        padding: 5px 9px;
        margin: 8px 0 8px 6px;
    }
    QTabBar::tab:selected {
        color: #F3D27A;
        background: #202833;
    }
    QTableWidget {
        color: #DCE7F2;
        background: #111820;
        alternate-background-color: #151E28;
        gridline-color: #2C3745;
        border: 1px solid #2C3745;
        border-radius: 6px;
    }
    QHeaderView::section {
        color: #9AA8BA;
        background: #17202A;
        border: 0;
        border-right: 1px solid #2C3745;
        padding: 5px;
        font-weight: 600;
    }
    QScrollBar:vertical {
        background: rgba(255, 255, 255, 10);
        width: 8px;
        margin: 0;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #3B4654;
        min-height: 24px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical:hover {
        background: #5EA7FF;
    }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0;
        border: 0;
    }
    QScrollBar:horizontal {
        height: 0;
        background: transparent;
    }
    """
    replacements = {
        "__QT_HUD_PRIMARY_ACTION_TEXT__": themed["primaryActionText"],
        "__QT_HUD_ACTIVITY_LINE__": themed["activityLine"],
        "__QT_HUD_SETTINGS_CHROME_BACKGROUND__": themed["settingsChromeBackground"],
        "__QT_HUD_SETTINGS_CONTROL_BACKGROUND__": themed["settingsControlBackground"],
        "__QT_HUD_SETTINGS_CONTROL_HOVER__": themed["settingsControlHover"],
        "__QT_HUD_SETTINGS_POPUP_BACKGROUND__": themed["settingsPopupBackground"],
        "__QT_HUD_SETTINGS_POPUP_SELECTION__": themed["settingsPopupSelection"],
        "rgba(16, 22, 29, 236)": themed["shellBackground"],
        "rgba(24, 33, 43, 220)": themed["panelHeaderBackground"],
        "rgba(255, 255, 255, 20)": themed["panelHairline"],
        "rgba(127, 62, 58, 170)": themed["warningPanelBackground"],
        "rgba(255, 255, 255, 18)": themed["cardBackground"],
        "rgba(255, 255, 255, 24)": themed["cardBorder"],
        "rgba(39, 50, 65, 210)": themed["panelHairline"],
        "rgba(16, 24, 33, 190)": themed["activityTrailBackground"],
        "rgba(28, 38, 50, 230)": themed["chipBackground"],
        "rgba(91, 49, 44, 200)": themed["chipWarningBackground"],
        "rgba(255, 255, 255, 10)": themed["scrollbarBackground"],
        "#10161D": theme["surface"],
        "#10161d": theme["surface"],
        "#141B24": theme["panelSurface"],
        "#2C3745": theme["panelBorder"],
        "#3A485A": theme["panelBorder"],
        "#202833": theme["headerSurface"],
        "#273241": theme["divider"],
        "#DCE7F2": theme["text"],
        "#F2F6FA": theme["text"],
        "#E9F1F8": theme["progressTrackText"],
        "#8D9AAD": theme["muted"],
        "#9AA8BA": theme["muted"],
        "#A9BCD2": themed["settingsStatus"],
        "#F3D27A": theme["accent"],
        "#9CCBFF": theme["info"],
        "#5EA7FF": theme["progressCache"],
        "#FFB86B": theme["warning"],
        "#FFD6B0": theme["warning"],
        "#FF875A": theme["progressOverflow"],
        "#FF7A7A": theme["error"],
        "#FF6B6B": theme["error"],
        "#B5DD92": theme["success"],
        "#4D6075": theme["requestText"],
        "#718095": theme["requestMuted"],
        "#3B4654": theme["progressTrackBorder"],
        "#202832": theme["progressTrack"],
        "#1C2632": themed["buttonBackground"],
        "#111820": theme["requestSurface"],
        "#0B1016": theme["requestSurface"],
        "#101821": theme["requestPanelSurface"],
        "#151D27": theme["requestHeaderSurface"],
        "#151E28": theme["requestHeaderSurface"],
        "#17202A": theme["requestHeaderSurface"],
        "#1D2A38": theme["headerSurface"],
        "#334254": theme["panelBorder"],
        "#223044": themed["buttonHoverBackground"],
        "#2E3846": themed["settingsActionBackground"],
        "#2E6DA8": themed["inputSelectionBackground"],
        "#FFFFFF": "#FFFFFF",
    }
    for source, target in replacements.items():
        css = css.replace(source, str(target))
    return css


class QtHudWindow:
    """Lazy public wrapper so importing the package does not require Qt to start."""

    def __init__(
        self,
        *,
        compact: bool = False,
        hide_until_attached: bool = False,
        tombstone_follow_ms: int = 500,
        user_settings_store: UserConfigStore | None = None,
        hud_settings_store: HudSettingsStore | None = None,
        update_manager: Any | None = None,
    ) -> None:
        if QApplication is None:
            raise RuntimeError(f"PySide6 is required for Qt HUD: {_QT_IMPORT_ERROR}")
        self._impl = _QtHudWindowImpl(
            compact=compact,
            hide_until_attached=hide_until_attached,
            tombstone_follow_ms=tombstone_follow_ms,
            user_settings_store=user_settings_store,
            hud_settings_store=hud_settings_store,
            update_manager=update_manager,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)


__all__ = ["QtHudWindow"]
