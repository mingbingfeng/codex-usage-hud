"""Centered PySide6 rest-reminder dialog.

Shown when the rest reminder fires and PySide6 is available. Falls back to the
renderer toast when this module cannot import or create a Qt application.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

_LOGGER = logging.getLogger("codex_usage_hud.rest_reminder_qt")
_LOGGER.addHandler(logging.NullHandler())


def show_rest_reminder_dialog(
    *,
    message: str,
    can_postpone: bool,
    postpone_minutes: int,
    on_ack: Callable[[], None] | None = None,
    on_postpone: Callable[[], None] | None = None,
) -> object:
    """Show a non-modal centered rest dialog. Returns the dialog widget."""
    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QFont, QGuiApplication
        from PySide6.QtWidgets import (
            QApplication,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("PySide6 is required for rest reminder dialogs.") from exc

    app = QApplication.instance()
    if app is None:
        app = QApplication([_path_name()])
        app.setQuitOnLastWindowClosed(False)

    class RestReminderDialog(QWidget):
        def __init__(self) -> None:
            flags = (
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            super().__init__(None, flags)
            self.setObjectName("codexRestReminderDialog")
            self.setFixedWidth(420)
            self.setStyleSheet(
                """
                QWidget#codexRestReminderDialog {
                    background: #141B24;
                    color: #DCE7F2;
                    border: 1px solid #3A4A5C;
                    border-radius: 14px;
                }
                QLabel#codexRestReminderTitle {
                    color: #F3D27A;
                    font-size: 15px;
                    font-weight: 700;
                }
                QLabel#codexRestReminderBody {
                    color: #DCE7F2;
                    font-size: 13px;
                }
                QLabel#codexRestReminderHint {
                    color: #8492A6;
                    font-size: 11px;
                }
                QPushButton {
                    background: #1E2834;
                    color: #DCE7F2;
                    border: 1px solid #3A4A5C;
                    border-radius: 8px;
                    padding: 8px 14px;
                    min-width: 96px;
                }
                QPushButton[primary="true"] {
                    background: #F3D27A;
                    color: #1A1408;
                    border: 1px solid #F3D27A;
                    font-weight: 700;
                }
                QPushButton:hover {
                    border-color: #F3D27A;
                }
                """
            )
            root = QVBoxLayout(self)
            root.setContentsMargins(18, 16, 18, 16)
            root.setSpacing(12)

            title = QLabel("☕ 休息一下")
            title.setObjectName("codexRestReminderTitle")
            title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold))
            root.addWidget(title)

            body = QLabel(str(message or "该休息一下了。"))
            body.setObjectName("codexRestReminderBody")
            body.setWordWrap(True)
            body.setFont(QFont("Microsoft YaHei UI", 10))
            root.addWidget(body)

            hint = QLabel("轻柔提醒，不会打断或锁定你的工作。")
            hint.setObjectName("codexRestReminderHint")
            hint.setWordWrap(True)
            root.addWidget(hint)

            actions = QHBoxLayout()
            actions.setContentsMargins(0, 4, 0, 0)
            actions.setSpacing(10)
            actions.addStretch(1)

            if can_postpone:
                postpone_btn = QPushButton(f"延后 {int(postpone_minutes)} 分钟")
                postpone_btn.clicked.connect(self._handle_postpone)
                actions.addWidget(postpone_btn)

            ack_btn = QPushButton("知道了")
            ack_btn.setProperty("primary", "true")
            ack_btn.clicked.connect(self._handle_ack)
            actions.addWidget(ack_btn)
            root.addLayout(actions)

            self._closed = False
            self._center_on_screen()
            # Auto-dismiss after 60s so a forgotten dialog does not stick forever.
            QTimer.singleShot(60_000, self._handle_ack)

        def _center_on_screen(self) -> None:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            self.adjustSize()
            frame = self.frameGeometry()
            frame.moveCenter(geo.center())
            self.move(frame.topLeft())

        def _handle_ack(self) -> None:
            if self._closed:
                return
            self._closed = True
            try:
                if on_ack is not None:
                    on_ack()
            finally:
                self.hide()
                self.close()

        def _handle_postpone(self) -> None:
            if self._closed:
                return
            self._closed = True
            try:
                if on_postpone is not None:
                    on_postpone()
            finally:
                self.hide()
                self.close()

        def closeEvent(self, event) -> None:  # noqa: N802
            if not self._closed:
                self._closed = True
                if on_ack is not None:
                    try:
                        on_ack()
                    except Exception:
                        pass
            super().closeEvent(event)

    dialog = RestReminderDialog()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if not hasattr(app, "_codex_rest_reminder_dialogs"):
        app._codex_rest_reminder_dialogs = []  # type: ignore[attr-defined]
    app._codex_rest_reminder_dialogs.append(dialog)  # type: ignore[attr-defined]

    def _cleanup() -> None:
        dialogs = getattr(app, "_codex_rest_reminder_dialogs", [])
        try:
            dialogs.remove(dialog)
        except ValueError:
            pass

    dialog.destroyed.connect(_cleanup)
    app.processEvents()
    return dialog


def _path_name() -> str:
    return str(getattr(sys, "argv", ["codex-usage-hud"])[0] or "codex-usage-hud")
