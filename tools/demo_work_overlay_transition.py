"""Interactive demo for the real PySide6 work-overlay bubble transitions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_usage_hud.ui import work_overlay_qt  # noqa: E402


DEFAULT_SCALE = 6.0
DEFAULT_SPEED = 1.0
SAMPLE_INTERVAL_MS = 60
DEFAULT_LOG_PATH = ROOT / "tmp" / "work_overlay_transition_demo.log"
TRANSITION_DURATION_NAMES = (
    "WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS",
    "WORK_OVERLAY_TRANSITION_SHRINK_MS",
    "WORK_OVERLAY_TRANSITION_PAUSE_MS",
    "WORK_OVERLAY_TRANSITION_MOVE_MS",
    "WORK_OVERLAY_TRANSITION_SHIFT_MS",
    "WORK_OVERLAY_RESTORE_FADE_OUT_MS",
    "WORK_OVERLAY_RESTORE_SHIFT_MS",
    "WORK_OVERLAY_RESTORE_FADE_IN_MS",
    "WORK_OVERLAY_RESTORE_DESCEND_MS",
)
BASE_TRANSITION_DURATIONS = {
    name: int(getattr(work_overlay_qt, name))
    for name in TRANSITION_DURATION_NAMES
    if hasattr(work_overlay_qt, name)
}


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=offset_seconds)).isoformat()


def _item(
    item_id: str,
    *,
    status: str,
    title: str,
    status_text: str,
    elapsed_text: str,
    workdir: str,
    updated_offset: int = 0,
) -> dict[str, object]:
    return {
        "id": item_id,
        "sessionId": item_id,
        "targetTitle": title,
        "title": title,
        "status": status,
        "statusText": status_text,
        "elapsedText": elapsed_text,
        "startedAt": _now_iso(-320 + updated_offset),
        "taskStartedAt": _now_iso(-320 + updated_offset),
        "updatedAt": _now_iso(updated_offset),
        "lastText": (
            "这是完成态气泡动画 demo。状态由独立控制窗口写入真实 overlay state，"
            "用于手动观察方形变圆、圆形恢复方形、让位和回位。"
        ),
        "workdir": workdir,
        "workdirName": Path(workdir).name,
        "tokensText": "2.4k",
        "costText": "$0.03",
        "cacheHitText": "91%",
        "current": item_id in {"R1", "demo-main"},
    }


def _write_state(
    state_path: Path,
    *,
    items: Sequence[Mapping[str, object]],
    close: bool = False,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ownerPid": os.getpid(),
        "updatedAt": time.time(),
        "close": bool(close),
        "itemLimit": 8,
        "items": [dict(item) for item in items],
    }
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(state_path)


def _apply_transition_scale(scale: float) -> None:
    scale = max(0.1, float(scale))
    for name, base_value in BASE_TRANSITION_DURATIONS.items():
        setattr(work_overlay_qt, name, max(1, int(round(base_value * scale))))


def _effective_transition_scale(args: argparse.Namespace) -> float:
    scale = max(0.1, float(args.scale))
    speed = max(0.1, float(args.speed))
    return max(0.1, scale / speed)


def _updated_key(item: Mapping[str, object]) -> str:
    return str(item.get("updatedAt") or item.get("taskStartedAt") or item.get("startedAt") or "")


def _item_id(item: Mapping[str, object]) -> str:
    return str(item.get("id") or "").strip()


def _is_completed(item: Mapping[str, object]) -> bool:
    return str(item.get("status") or "") == "recent"


def _circles(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return sorted([item for item in items if _is_completed(item)], key=_updated_key)


def _rects(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [item for item in items if not _is_completed(item)]


def _ids_text(items: Sequence[Mapping[str, object]]) -> str:
    return "[" + ",".join(_item_id(item) for item in items) + "]"


def _rect_text(rect: Any) -> str:
    return f"({rect.x()},{rect.y()},{rect.width()},{rect.height()})"


def _overlay_rect_text(rect: tuple[float, float, float, float]) -> str:
    return (
        f"({int(round(rect[0]))},{int(round(rect[1]))},"
        f"{int(round(rect[2]))},{int(round(rect[3]))})"
    )


def _target_map_text(pairs: Sequence[tuple[str, tuple[float, float, float, float]]]) -> str:
    return ";".join(f"{item_id}->{_overlay_rect_text(rect)}" for item_id, rect in pairs)


def _initial_items() -> list[dict[str, object]]:
    workdir = str(ROOT)
    return [
        _item(
            "C1",
            status="recent",
            title="完成 1",
            status_text="已完成",
            elapsed_text="已处理 3m02s",
            workdir=workdir,
            updated_offset=-50,
        ),
        _item(
            "C2",
            status="recent",
            title="完成 2",
            status_text="已完成",
            elapsed_text="已处理 1m26s",
            workdir=workdir,
            updated_offset=-40,
        ),
        _item(
            "R1",
            status="tool",
            title="历史任务 1",
            status_text="正在运行测试",
            elapsed_text="已处理 2m18s",
            workdir=workdir,
            updated_offset=-30,
        ),
        _item(
            "R2",
            status="tool",
            title="历史任务 2",
            status_text="正在整理变更摘要",
            elapsed_text="已处理 48s",
            workdir=workdir,
            updated_offset=-20,
        ),
        _item(
            "R3",
            status="thinking",
            title="历史任务 3",
            status_text="正在思考",
            elapsed_text="已处理 14s",
            workdir=workdir,
            updated_offset=-10,
        ),
    ]


def _clone_items(items: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [dict(item) for item in items]


def _toggle_items(
    items: Sequence[Mapping[str, object]],
    item_id: str,
) -> tuple[list[dict[str, object]], bool]:
    next_items = _clone_items(items)
    was_circle = False
    for item in next_items:
        if _item_id(item) != item_id:
            continue
        was_circle = _is_completed(item)
        if was_circle:
            item.update(
                {
                    "status": "tool",
                    "statusText": "恢复运行中",
                    "elapsedText": "已处理 1m42s",
                    "updatedAt": _now_iso(0),
                }
            )
        else:
            item.update(
                {
                    "status": "recent",
                    "statusText": "已完成",
                    "elapsedText": "已处理 3m02s",
                    "updatedAt": _now_iso(0),
                }
            )
        break
    return next_items, was_circle


def _timeline(
    state_path: Path,
    *,
    scale: float,
    loop: bool,
) -> None:
    items = _initial_items()
    base_pause = max(1.0, scale * 0.55)
    transition_pause = max(2.0, scale * 0.95)

    while True:
        print("demo: 1/5 initial mixed cards/circles")
        _write_state(state_path, items=items)
        time.sleep(base_pause)

        print("demo: 2/5 R1 card -> completed circle")
        items, _ = _toggle_items(items, "R1")
        _write_state(state_path, items=items)
        time.sleep(transition_pause)

        print("demo: 3/5 R2 card -> completed circle")
        items, _ = _toggle_items(items, "R2")
        _write_state(state_path, items=items)
        time.sleep(transition_pause)

        print("demo: 4/5 C1 circle -> running card")
        items, _ = _toggle_items(items, "C1")
        _write_state(state_path, items=items)
        time.sleep(transition_pause)

        print("demo: 5/5 stable final state")
        _write_state(state_path, items=items)
        time.sleep(base_pause)

        if not loop:
            print("demo: finished; closing overlay")
            _write_state(state_path, items=items, close=True)
            return


def _run_auto_demo(args: argparse.Namespace) -> int:
    state_path = args.state_file.expanduser().resolve()
    _write_state(state_path, items=[])
    thread = threading.Thread(
        target=_timeline,
        kwargs={
            "state_path": state_path,
            "scale": args.effective_scale,
            "loop": not args.once,
        },
        daemon=True,
    )
    thread.start()
    print(f"demo: state file {state_path}")
    print(
        "demo: transition "
        f"scale={args.scale:g}x speed={args.speed:g}x effective={args.effective_scale:g}x"
    )
    print("demo: press Ctrl+C in this terminal to stop")
    try:
        return work_overlay_qt.run_work_overlay_helper_qt(
            state_path,
            process_exists=lambda pid: pid == os.getpid(),
            owner_pid_from_path=lambda _path: os.getpid(),
            item_limit=8,
            stale_seconds=3600.0,
            overlay_alpha=0.94,
            hover_alpha=0.62,
            header_title_limit=28,
        )
    except KeyboardInterrupt:
        _write_state(state_path, items=[], close=True)
        return 0


def _run_interactive_demo(args: argparse.Namespace) -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QDoubleSpinBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSlider,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication.instance() or QApplication([Path(sys.argv[0]).name or "overlay-demo"])
    state_path = args.state_file.expanduser().resolve()
    log_path = args.log_file.expanduser().resolve()

    class DemoHotspotWindow(QWidget):
        def __init__(self, callback: Any) -> None:
            flags = (
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            super().__init__(None, flags)
            self._callback = callback
            self._item_id = ""
            self._circle = False
            self._hover = False
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        def configure(self, item_id: str, *, circle: bool, tooltip: str) -> None:
            self._item_id = item_id
            self._circle = circle
            self.setToolTip(tooltip)
            self.update()

        def enterEvent(self, event: Any) -> None:
            self._hover = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event: Any) -> None:
            self._hover = False
            self.update()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == Qt.MouseButton.LeftButton and self._item_id:
                self._callback(self._item_id)
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def paintEvent(self, event: Any) -> None:
            del event
            if not self._hover:
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            fill = QColor("#F3D27A")
            fill.setAlpha(28)
            border = QColor("#F3D27A")
            border.setAlpha(110)
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.4))
            rect = self.rect().adjusted(2, 2, -2, -2)
            if self._circle:
                painter.drawEllipse(rect)
            else:
                painter.drawRoundedRect(rect, 10, 10)

    class InteractiveWorkOverlayDemo(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.items = _initial_items()
            self._log_items: list[dict[str, object]] | None = None
            self._next_items_after_animation: list[dict[str, object]] | None = None
            self._animation_label = ""
            self._animation_item_id = ""
            self._animation_started_at = 0.0
            self._animation_seen_active = False
            self._hotspots: list[DemoHotspotWindow] = []
            self._started_at = time.perf_counter()
            self._speed = max(0.1, float(args.speed))

            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "HUDDemo animation trace\n"
                "Columns: elapsed | event | details | circles | rects\n",
                encoding="utf-8",
            )
            _write_state(state_path, items=self.items)

            self.setWindowTitle("Work Overlay 真实动画交互 Demo")
            self.setMinimumWidth(430)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)
            layout.addWidget(QLabel("点击真实气泡或下方按钮：方形 <-> 圆形。"))
            layout.addWidget(QLabel(f"日志：{log_path}"))
            self._status_label = QLabel("")
            layout.addWidget(self._status_label)

            speed_row = QHBoxLayout()
            speed_row.addWidget(QLabel("速度"))
            self._speed_slider = QSlider(Qt.Orientation.Horizontal)
            self._speed_slider.setRange(25, 400)
            self._speed_slider.setSingleStep(25)
            self._speed_slider.setPageStep(50)
            self._speed_slider.setValue(int(round(self._speed * 100)))
            self._speed_spin = QDoubleSpinBox()
            self._speed_spin.setRange(0.25, 4.0)
            self._speed_spin.setDecimals(2)
            self._speed_spin.setSingleStep(0.25)
            self._speed_spin.setSuffix("x")
            self._speed_spin.setValue(min(4.0, max(0.25, self._speed)))
            self._speed_detail_label = QLabel("")
            speed_row.addWidget(self._speed_slider, 1)
            speed_row.addWidget(self._speed_spin)
            speed_row.addWidget(self._speed_detail_label)
            layout.addLayout(speed_row)
            self._speed_slider.valueChanged.connect(self._set_speed_from_slider)
            self._speed_spin.valueChanged.connect(self._set_speed)
            self._apply_speed(log_change=False)

            button_row = QHBoxLayout()
            self._buttons: dict[str, QPushButton] = {}
            for item_id in ("C1", "C2", "R1", "R2", "R3"):
                button = QPushButton(item_id)
                button.clicked.connect(lambda checked=False, item_id=item_id: self.toggle_item(item_id))
                button_row.addWidget(button)
                self._buttons[item_id] = button
            layout.addLayout(button_row)

            reset_button = QPushButton("重置初始状态")
            reset_button.clicked.connect(self.reset_state)
            close_button = QPushButton("关闭演示")
            close_button.clicked.connect(self.close_demo)
            action_row = QHBoxLayout()
            action_row.addWidget(reset_button)
            action_row.addWidget(close_button)
            layout.addLayout(action_row)

            self._sync_timer = QTimer(self)
            self._sync_timer.timeout.connect(self.sync_hotspots)
            self._sync_timer.start(80)

            self._sample_timer = QTimer(self)
            self._sample_timer.timeout.connect(self._log_sample)

            self._finish_timer = QTimer(self)
            self._finish_timer.timeout.connect(self._maybe_finish_animation)
            self._finish_timer.start(40)

            self._log_state("init")
            self._update_status()

        def _set_speed_from_slider(self, value: int) -> None:
            self._set_speed(max(0.25, min(4.0, float(value) / 100.0)))

        def _set_speed(self, speed: float) -> None:
            self._speed = max(0.25, min(4.0, float(speed)))
            self._apply_speed(log_change=True)

        def _apply_speed(self, *, log_change: bool) -> None:
            effective_scale = max(0.1, float(args.scale) / self._speed)
            args.speed = self._speed
            args.effective_scale = effective_scale
            _apply_transition_scale(effective_scale)

            slider_value = int(round(self._speed * 100))
            if self._speed_slider.value() != slider_value:
                self._speed_slider.blockSignals(True)
                self._speed_slider.setValue(slider_value)
                self._speed_slider.blockSignals(False)
            if abs(self._speed_spin.value() - self._speed) > 0.001:
                self._speed_spin.blockSignals(True)
                self._speed_spin.setValue(self._speed)
                self._speed_spin.blockSignals(False)
            self._speed_detail_label.setText(f"effective {effective_scale:g}x")
            if log_change:
                self._log_state(
                    "speed.change",
                    speed=f"{self._speed:g}x",
                    effective_scale=f"{effective_scale:g}x",
                )

        def close_demo(self) -> None:
            _write_state(state_path, items=self.items, close=True)
            self._hide_hotspots()
            app.quit()

        def closeEvent(self, event: Any) -> None:
            self.close_demo()
            super().closeEvent(event)

        def reset_state(self) -> None:
            if self._is_animating():
                self._log_state("animation.force_reset", reason="动画运行中，忽略重置")
                return
            self.items = _initial_items()
            self._log_items = None
            _write_state(state_path, items=self.items)
            self._log_state("reset")
            self._update_status()

        def _overlay_window(self) -> QWidget | None:
            for widget in QApplication.topLevelWidgets():
                if hasattr(widget, "_item_widgets") and hasattr(widget, "_shell"):
                    return widget
            return None

        def _is_animating(self) -> bool:
            overlay = self._overlay_window()
            return bool(self._animation_label or getattr(overlay, "_transition_in_progress", False))

        def _active_log_items(self) -> list[dict[str, object]]:
            return self._log_items if self._log_items is not None else self.items

        def _overlay_record_state(self) -> dict[str, tuple[str, Any, float]]:
            overlay = self._overlay_window()
            if overlay is None:
                return {}
            records: dict[str, tuple[str, Any, float]] = {}
            for record in getattr(overlay, "_item_widgets", []):
                kind = str(record.get("kind") or "")
                item_id = str(record.get("item_id") or "")
                widget = record.get("badge") if kind == "completed" else record.get("card")
                if not item_id or widget is None:
                    continue
                try:
                    top_left = widget.mapTo(getattr(overlay, "_shell"), QPoint(0, 0))
                    rect = widget.geometry()
                    effect = widget.graphicsEffect()
                    opacity = float(effect.opacity()) if effect is not None else 1.0
                    records[item_id] = (
                        "C" if kind == "completed" else "R",
                        type(rect)(top_left.x(), top_left.y(), rect.width(), rect.height()),
                        opacity,
                    )
                except RuntimeError:
                    continue

            transition_card = getattr(overlay, "_transition_card_widget", None)
            transition_item_id = str(getattr(overlay, "_transition_item_id", "") or "")
            if transition_card is not None and transition_item_id:
                try:
                    if transition_card.isVisible():
                        top_left = transition_card.mapTo(getattr(overlay, "_shell"), QPoint(0, 0))
                        rect = transition_card.geometry()
                        effect = transition_card.graphicsEffect()
                        opacity = float(effect.opacity()) if effect is not None else 1.0
                        records[transition_item_id] = (
                            "R",
                            type(rect)(top_left.x(), top_left.y(), rect.width(), rect.height()),
                            opacity,
                        )
                except RuntimeError:
                    pass

            transition_badge = getattr(overlay, "_transition_badge_widget", None)
            if transition_badge is not None and transition_item_id:
                try:
                    if transition_badge.isVisible():
                        top_left = transition_badge.mapTo(getattr(overlay, "_shell"), QPoint(0, 0))
                        rect = transition_badge.geometry()
                        effect = transition_badge.graphicsEffect()
                        opacity = float(effect.opacity()) if effect is not None else 1.0
                        records[transition_item_id] = (
                            "C",
                            type(rect)(top_left.x(), top_left.y(), rect.width(), rect.height()),
                            opacity,
                        )
                except RuntimeError:
                    pass

            return records

        def _overlay_geometry_text(self) -> str:
            overlay = self._overlay_window()
            if overlay is None:
                return "none"
            try:
                rect = overlay.geometry()
                shell = getattr(overlay, "_shell")
                shell_rect = shell.geometry()
                shell_top_left = shell.mapToGlobal(QPoint(0, 0))
                shell_global = (
                    f"({shell_top_left.x()},{shell_top_left.y()},"
                    f"{shell.width()},{shell.height()})"
                )
                first_bubble = "none"
                for record in getattr(overlay, "_item_widgets", []):
                    kind = str(record.get("kind") or "")
                    item_id = str(record.get("item_id") or "")
                    widget = record.get("badge") if kind == "completed" else record.get("card")
                    if not item_id or widget is None or not widget.isVisible():
                        continue
                    top_left = widget.mapToGlobal(QPoint(0, 0))
                    shape = "C" if kind == "completed" else "R"
                    first_bubble = (
                        f"{item_id}:{shape}@({top_left.x()},{top_left.y()},"
                        f"{widget.width()},{widget.height()})"
                    )
                    break
            except RuntimeError:
                return "deleted"
            return (
                f"overlay={_rect_text(rect)},shell={_rect_text(shell_rect)},"
                f"shell_global={shell_global},first_global={first_bubble}"
            )

        def _bubble_text(
            self,
            item: Mapping[str, object],
            records: dict[str, tuple[str, Any, float]],
        ) -> str:
            item_id = _item_id(item)
            fallback_shape = "C" if _is_completed(item) else "R"
            shape, rect, opacity = records.get(item_id, (fallback_shape, None, 1.0))
            if rect is None:
                slot_kind = "completed" if _is_completed(item) else "card"
                slot = work_overlay_qt._find_item_rect(
                    self._active_log_items(),
                    item_id,
                    slot_kind,
                    layout_width=work_overlay_qt.WORK_OVERLAY_WIDTH,
                )
                rect_text = _overlay_rect_text(slot)
            else:
                rect_text = _rect_text(rect)
            return f"{item_id}:{shape}:{item.get('title', item_id)}@{rect_text}:op={opacity:.2f}"

        def _log(self, event: str, **fields: object) -> None:
            elapsed_ms = (time.perf_counter() - self._started_at) * 1000.0
            details = " ".join(f"{key}={value}" for key, value in fields.items())
            line = f"{elapsed_ms:8.1f}ms | {event}"
            if details:
                line += f" | {details}"
            print(line, flush=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

        def _log_state(self, event: str, **fields: object) -> None:
            items = self._active_log_items()
            records = self._overlay_record_state()
            circles = _circles(items)
            rects = _rects(items)
            self._log(
                event,
                **fields,
                overlay=self._overlay_geometry_text(),
                circles=_ids_text(circles),
                rects=_ids_text(rects),
                circle_state=";".join(self._bubble_text(item, records) for item in circles),
                rect_state=";".join(self._bubble_text(item, records) for item in rects),
            )

        def _log_sample(self) -> None:
            if self._animation_label:
                self._log_state(f"sample.{self._animation_label}")

        def _start_sampling(self, label: str) -> None:
            self._animation_label = label
            if not self._sample_timer.isActive():
                self._sample_timer.start(SAMPLE_INTERVAL_MS)

        def _stop_sampling(self) -> None:
            self._sample_timer.stop()

        def _update_status(self) -> None:
            circles = _ids_text(_circles(self.items))
            rects = _ids_text(_rects(self.items))
            self._status_label.setText(f"当前圆形 {circles}；当前方形 {rects}")
            for item_id, button in self._buttons.items():
                item = next((item for item in self.items if _item_id(item) == item_id), None)
                if item is None:
                    continue
                button.setText(f"{item_id} {'圆' if _is_completed(item) else '方'}")
                button.setEnabled(not self._is_animating())

        def _log_rect_to_circle_start(
            self,
            item_id: str,
            old_items: list[dict[str, object]],
            new_items: list[dict[str, object]],
        ) -> None:
            layout_width = work_overlay_qt._transition_layout_width(old_items, new_items)
            targets: list[tuple[str, tuple[float, float, float, float]]] = [
                (
                    item_id,
                    work_overlay_qt._find_item_rect(
                        new_items,
                        item_id,
                        "completed",
                        layout_width=layout_width,
                    ),
                )
            ]
            moves = work_overlay_qt._completed_badge_slot_moves(
                old_items,
                new_items,
                layout_width=layout_width,
            )
            for move_id, (_start, target) in moves.items():
                if move_id != item_id:
                    targets.append((move_id, target))
            for item in _rects(new_items):
                rect_id = _item_id(item)
                if rect_id != item_id:
                    targets.append(
                        (
                            rect_id,
                            work_overlay_qt._find_item_rect(
                                new_items,
                                rect_id,
                                "card",
                                layout_width=layout_width,
                            ),
                        )
                    )
            clicked_idx = next(
                (index for index, item in enumerate(_rects(old_items)) if _item_id(item) == item_id),
                -1,
            )
            self._log_state(
                "rect_to_circle.start",
                clicked=item_id,
                clicked_idx=clicked_idx,
                next_circles=_ids_text(_circles(new_items)),
                next_rects=_ids_text(_rects(new_items)),
                targets=_target_map_text(targets),
            )

        def _log_circle_to_rect_phase1_start(
            self,
            item_id: str,
            old_items: list[dict[str, object]],
            new_items: list[dict[str, object]],
        ) -> None:
            layout_width = work_overlay_qt._transition_layout_width(old_items, new_items)
            staged_items = work_overlay_qt._completed_restore_staged_items(old_items, item_id)
            moves = work_overlay_qt._completed_badge_restore_slot_moves(
                old_items,
                new_items,
                item_id,
                layout_width=layout_width,
            )
            targets = [
                (move_id, staged)
                for move_id, (_start, staged, _target) in moves.items()
                if move_id != item_id
            ]
            self._log_state(
                "circle_to_rect.phase1.start",
                clicked=item_id,
                staged_circles=_ids_text(_circles(staged_items)),
                targets=_target_map_text(targets),
            )

        def _schedule_circle_to_rect_logs(
            self,
            item_id: str,
            old_items: list[dict[str, object]],
            new_items: list[dict[str, object]],
        ) -> None:
            phase1_ms = max(
                work_overlay_qt.WORK_OVERLAY_RESTORE_FADE_OUT_MS,
                work_overlay_qt.WORK_OVERLAY_RESTORE_SHIFT_MS,
            )
            phase2_ms = work_overlay_qt.WORK_OVERLAY_RESTORE_FADE_IN_MS

            def phase1_finished() -> None:
                if self._animation_item_id != item_id:
                    return
                staged_items = work_overlay_qt._completed_restore_staged_items(old_items, item_id)
                self._log_state(
                    "circle_to_rect.phase1.finished",
                    clicked=item_id,
                    staged_circles=_ids_text(_circles(staged_items)),
                )
                layout_width = work_overlay_qt._transition_layout_width(old_items, new_items)
                rightmost = work_overlay_qt._find_item_rect(
                    staged_items,
                    item_id,
                    "completed",
                    layout_width=layout_width,
                )
                self._log_state(
                    "circle_to_rect.phase2.start",
                    clicked=item_id,
                    silent_reset=_overlay_rect_text(rightmost),
                )

            def phase2_finished() -> None:
                if self._animation_item_id != item_id:
                    return
                staged_items = work_overlay_qt._completed_restore_staged_items(old_items, item_id)
                self._log_state(
                    "circle_to_rect.phase2.finished",
                    clicked=item_id,
                    staged_circles=_ids_text(_circles(staged_items)),
                )
                self._log_circle_to_rect_phase3_start(item_id, old_items, new_items)

            QTimer.singleShot(phase1_ms + 80, phase1_finished)
            QTimer.singleShot(phase1_ms + phase2_ms + 120, phase2_finished)

        def _log_circle_to_rect_phase3_start(
            self,
            item_id: str,
            old_items: list[dict[str, object]],
            new_items: list[dict[str, object]],
        ) -> None:
            layout_width = work_overlay_qt._transition_layout_width(old_items, new_items)
            targets: list[tuple[str, tuple[float, float, float, float]]] = [
                (
                    item_id,
                    work_overlay_qt._find_item_rect(
                        new_items,
                        item_id,
                        "card",
                        layout_width=layout_width,
                    ),
                )
            ]
            for item in _rects(old_items):
                rect_id = _item_id(item)
                targets.append(
                    (
                        rect_id,
                        work_overlay_qt._find_item_rect(
                            new_items,
                            rect_id,
                            "card",
                            layout_width=layout_width,
                        ),
                    )
                )
            moves = work_overlay_qt._completed_badge_restore_slot_moves(
                old_items,
                new_items,
                item_id,
                layout_width=layout_width,
            )
            for move_id, (_start, _staged, target) in moves.items():
                if move_id != item_id:
                    targets.append((move_id, target))
            self._log_state(
                "circle_to_rect.phase3.start",
                clicked=item_id,
                circle_count_after=len(_circles(new_items)),
                targets=_target_map_text(targets),
            )

        def toggle_item(self, item_id: str) -> None:
            if self._is_animating():
                self._log_state("animation.force_reset", reason="检测到动画运行中，忽略重复点击")
                return
            old_items = _clone_items(self.items)
            new_items, was_circle = _toggle_items(old_items, item_id)
            if old_items == new_items:
                return

            label = "circle_to_rect" if was_circle else "rect_to_circle"
            self._log_items = old_items
            self._next_items_after_animation = new_items
            self._animation_item_id = item_id
            self._animation_started_at = time.perf_counter()
            self._animation_seen_active = False
            self._log_state("click", clicked=item_id, shape="circle" if was_circle else "rect")
            self._start_sampling(label)
            self._log_state("animation.start", label=label)
            if was_circle:
                self._log_circle_to_rect_phase1_start(item_id, old_items, new_items)
                self._schedule_circle_to_rect_logs(item_id, old_items, new_items)
            else:
                self._log_rect_to_circle_start(item_id, old_items, new_items)

            self.items = new_items
            _write_state(state_path, items=self.items)
            self._hide_hotspots()
            self._update_status()

        def _maybe_finish_animation(self) -> None:
            if not self._animation_label:
                self._update_status()
                return
            overlay = self._overlay_window()
            in_progress = bool(getattr(overlay, "_transition_in_progress", False))
            if in_progress:
                self._animation_seen_active = True
                return
            elapsed = time.perf_counter() - self._animation_started_at
            if not self._animation_seen_active and elapsed < 0.6:
                return

            label = self._animation_label
            item_id = self._animation_item_id
            next_items = self._next_items_after_animation or self.items
            if label == "circle_to_rect":
                self._log_state(
                    "circle_to_rect.phase3.finished",
                    clicked=item_id,
                    next_circles=_ids_text(_circles(next_items)),
                    next_rects=_ids_text(_rects(next_items)),
                )
            else:
                self._log_state(
                    "rect_to_circle.finished",
                    next_circles=_ids_text(_circles(next_items)),
                    next_rects=_ids_text(_rects(next_items)),
                )
            self._log_items = None
            self._next_items_after_animation = None
            self._animation_label = ""
            self._animation_item_id = ""
            self._animation_seen_active = False
            self._stop_sampling()
            self._log_state("animation.finalize")
            self._update_status()

        def _hide_hotspots(self) -> None:
            for hotspot in self._hotspots:
                hotspot.hide()

        def sync_hotspots(self) -> None:
            overlay = self._overlay_window()
            if overlay is None or self._is_animating():
                self._hide_hotspots()
                return
            records = getattr(overlay, "_item_widgets", [])
            while len(self._hotspots) < len(records):
                self._hotspots.append(DemoHotspotWindow(self.toggle_item))
            for index, record in enumerate(records):
                hotspot = self._hotspots[index]
                kind = str(record.get("kind") or "")
                item_id = str(record.get("item_id") or "")
                widget = record.get("badge") if kind == "completed" else record.get("card")
                if not item_id or widget is None:
                    hotspot.hide()
                    continue
                try:
                    if not widget.isVisible():
                        hotspot.hide()
                        continue
                    top_left = widget.mapToGlobal(QPoint(0, 0))
                    hotspot.configure(
                        item_id,
                        circle=kind == "completed",
                        tooltip=f"点击切换 {item_id} 方形/圆形",
                    )
                    hotspot.setGeometry(
                        top_left.x(),
                        top_left.y(),
                        max(1, widget.width()),
                        max(1, widget.height()),
                    )
                    hotspot.show()
                    hotspot.raise_()
                except RuntimeError:
                    hotspot.hide()
            for hotspot in self._hotspots[len(records) :]:
                hotspot.hide()

    controller = InteractiveWorkOverlayDemo()
    controller.show()
    print(f"demo: state file {state_path}")
    print(f"demo: log file {log_path}")
    print(
        "demo: transition "
        f"scale={args.scale:g}x speed={args.speed:g}x effective={args.effective_scale:g}x"
    )
    print("demo: click real bubbles or the controller buttons; Ctrl+C stops")

    try:
        return work_overlay_qt.run_work_overlay_helper_qt(
            state_path,
            process_exists=lambda pid: pid == os.getpid(),
            owner_pid_from_path=lambda _path: os.getpid(),
            item_limit=8,
            stale_seconds=3600.0,
            overlay_alpha=0.94,
            hover_alpha=0.62,
            header_title_limit=28,
        )
    except KeyboardInterrupt:
        _write_state(state_path, items=[], close=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_SCALE,
        help=f"Transition time multiplier. Default: {DEFAULT_SCALE:g}.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_SPEED,
        help=(
            "Initial UI speed multiplier. Higher is faster; "
            f"default: {DEFAULT_SPEED:g}."
        ),
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run the old automatic timeline instead of the interactive click demo.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="With --auto, run the sequence once and close the overlay.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(tempfile.gettempdir()) / "codex-work-overlay-transition-demo.json",
        help="State file used by the demo overlay.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Interactive trace log file.",
    )
    args = parser.parse_args(argv)

    args.effective_scale = _effective_transition_scale(args)
    _apply_transition_scale(args.effective_scale)
    if args.auto:
        return _run_auto_demo(args)
    return _run_interactive_demo(args)


if __name__ == "__main__":
    raise SystemExit(main())
