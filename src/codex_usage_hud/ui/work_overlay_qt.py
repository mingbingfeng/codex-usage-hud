"""PySide6 desktop overlay used by the standalone work-bubble helper."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

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


def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


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


def _item_signature(item: Mapping[str, object]) -> str:
    return json.dumps(
        {
            "id": item.get("id"),
            "status": item.get("status"),
            "statusText": item.get("statusText"),
            "lastText": item.get("lastText"),
            "current": item.get("current"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _visible_overlay_items(
    items: Sequence[Mapping[str, object]],
    dismissed_signatures: dict[str, str],
    *,
    item_limit: int,
) -> list[Mapping[str, object]]:
    visible: list[Mapping[str, object]] = []
    live_ids: set[str] = set()
    for item in items[:item_limit]:
        item_id = str(item.get("id") or "")
        if item_id:
            live_ids.add(item_id)
        signature = _item_signature(item)
        if item_id and dismissed_signatures.get(item_id) == signature:
            continue
        if item_id and item_id in dismissed_signatures:
            dismissed_signatures.pop(item_id, None)
        visible.append(item)
    for item_id in list(dismissed_signatures):
        if item_id not in live_ids:
            dismissed_signatures.pop(item_id, None)
    return visible


def _color_for(status: str) -> tuple[str, str, str, str]:
    if status == "waiting_user":
        return "#FFB86B", "#1D1610", "#10161D", "#263241"
    if status == "tool":
        return "#9CCBFF", "#0D1722", "#10161D", "#263241"
    if status == "recent":
        return "#8FE3A1", "#0E1B14", "#0E1B14", "#2F9F55"
    return "#F3D27A", "#1C190F", "#10161D", "#263241"


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
        from PySide6.QtCore import QPoint, Qt, QTimer
        from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
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
            self._dismissed_signatures: dict[str, str] = {}
            self._last_signature = ""
            self._raw_items: list[Mapping[str, object]] = []
            self._close_windows: list[CloseButtonWindow] = []
            self._close_anchors: list[tuple[QWidget, Mapping[str, object], str, str, str]] = []
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
                self._dismissed_signatures[item_id] = _item_signature(item)
            self._last_signature = ""
            self.render_items(self._raw_items)

        def hide_overlay(self) -> None:
            self.hide()
            for close_window in self._close_windows:
                close_window.hide()

        def shutdown(self) -> None:
            self.hide_overlay()
            for close_window in self._close_windows:
                close_window.close()
            self._close_windows.clear()
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
                target = hover_alpha if (inside_overlay or inside_close) else overlay_alpha
            if abs(self.windowOpacity() - target) < 0.01:
                return
            self.setWindowOpacity(target)
            for close_window in self._close_windows:
                close_window.setWindowOpacity(target)

        def render_items(self, items: Sequence[Mapping[str, object]]) -> None:
            self._raw_items = list(items)
            visible_items = _visible_overlay_items(
                self._raw_items,
                self._dismissed_signatures,
                item_limit=item_limit,
            )
            signature = json.dumps(visible_items, ensure_ascii=False, sort_keys=True)
            if signature == self._last_signature:
                return
            self._last_signature = signature
            self._close_anchors.clear()

            shell_layout = self._shell.layout()
            while shell_layout.count():
                item = shell_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            if not visible_items:
                self.hide_overlay()
                return

            self._shell.setFixedWidth(WORK_OVERLAY_WIDTH)
            for item in visible_items:
                status = str(item.get("status") or "")
                accent, pill_bg, card_bg, border_color = _color_for(status)
                elapsed_text = str(item.get("elapsedText") or "").strip() or "已处理 --"
                header_text = _work_overlay_header_text(
                    str(item.get("startedAt") or ""),
                    elapsed_text,
                    str(item.get("title") or "Codex 工作"),
                    title_limit=header_title_limit,
                )

                card = QFrame(self._shell)
                card.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                card.setStyleSheet(
                    "QFrame {"
                    f"background-color: {card_bg};"
                    f"border: 1px solid {border_color};"
                    "border-radius: 10px;"
                    "}"
                )
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

                header = QLabel(header_text, card)
                header.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                header.setWordWrap(False)
                header.setTextFormat(text_format.PlainText)
                header.setAlignment(alignment.AlignVCenter | alignment.AlignLeft)
                header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
                header.setStyleSheet(
                    "QLabel {"
                    f"color: {accent if status == 'recent' else '#A9B6C6'};"
                    "border: none;"
                    "background: transparent;"
                    "}"
                )
                head_layout.addWidget(header, 1)

                close_anchor = QWidget(card)
                close_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                close_anchor.setFixedSize(WORK_OVERLAY_CLOSE_SIZE, WORK_OVERLAY_CLOSE_SIZE)
                head_layout.addWidget(close_anchor, 0)
                self._close_anchors.append((close_anchor, dict(item), card_bg, pill_bg, accent))
                card_layout.addLayout(head_layout)

                body_text = str(item.get("lastText") or item.get("detail") or "").strip()
                detail = QLabel(body_text, card)
                detail.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                detail.setWordWrap(True)
                detail.setTextFormat(text_format.PlainText)
                detail.setAlignment(alignment.AlignTop | alignment.AlignLeft)
                detail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
                detail.setFont(QFont("Microsoft YaHei UI", 8))
                detail.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
                detail.setMinimumHeight(self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH))
                detail.setStyleSheet(
                    "QLabel {"
                    "color: #B8C6D8;"
                    "border: none;"
                    "background: transparent;"
                    "}"
                )
                card_layout.addWidget(detail)

                status_text = str(item.get("statusText") or item.get("statusLabel") or "").strip()
                if status_text:
                    status_label = QLabel(status_text, card)
                    status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
                    status_label.setWordWrap(True)
                    status_label.setTextFormat(text_format.PlainText)
                    status_label.setAlignment(alignment.AlignTop | alignment.AlignLeft)
                    status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
                    status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
                    status_label.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
                    status_label.setMinimumHeight(
                        self._wrapped_label_height(status_label, WORK_OVERLAY_TEXT_WRAP_WIDTH)
                    )
                    status_label.setStyleSheet(
                        "QLabel {"
                        f"color: {accent if status == 'recent' else '#8492A6'};"
                        "border: none;"
                        "background: transparent;"
                        "}"
                    )
                    card_layout.addWidget(status_label)

                shell_layout.addWidget(card)

            self._shell.layout().activate()
            self.layout().activate()
            content_height = max(
                1,
                self.layout().totalHeightForWidth(WORK_OVERLAY_WIDTH),
                self.sizeHint().height(),
            )
            screen = app.primaryScreen()
            geometry = screen.availableGeometry() if screen is not None else self.geometry()
            x = max(geometry.left(), geometry.right() - WORK_OVERLAY_WIDTH - WORK_OVERLAY_MARGIN)
            max_y = max(geometry.top(), geometry.bottom() - content_height - WORK_OVERLAY_MARGIN)
            y = min(geometry.top() + WORK_OVERLAY_TOP_OFFSET, max_y)
            self.setGeometry(x, y, WORK_OVERLAY_WIDTH, content_height)
            self.show()
            self.raise_()
            app.processEvents()
            self._shell.layout().activate()
            self.layout().activate()
            final_height = max(
                content_height,
                self.layout().totalHeightForWidth(WORK_OVERLAY_WIDTH),
                self.sizeHint().height(),
            )
            if final_height != content_height:
                max_y = max(geometry.top(), geometry.bottom() - final_height - WORK_OVERLAY_MARGIN)
                y = min(geometry.top() + WORK_OVERLAY_TOP_OFFSET, max_y)
                self.setGeometry(x, y, WORK_OVERLAY_WIDTH, final_height)
            QTimer.singleShot(0, self.reposition_close_windows)

        def reposition_close_windows(self) -> None:
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
