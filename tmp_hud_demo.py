import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QSequentialAnimationGroup,
    QTimer,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QPushButton,
    QWidget,
)

# --- 布局参数配置 ---
RIGHT_MARGIN = 550
CIRCLE_SIZE = 60
RECT_W = 200
RECT_H = 60
SPACING = 15
CIRCLE_Y = 50
LOG_PATH = Path(__file__).with_name("tmp_hud_demo_animation.log")
SAMPLE_INTERVAL_MS = 50


class Bubble(QPushButton):
    def __init__(self, text, is_circle, parent=None):
        super().__init__(text, parent)
        self.debug_id = text
        self.is_circle = is_circle
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(1.0)
        self.setGraphicsEffect(self.effect)
        self.update_style()
        self.clicked.connect(self.on_click)

    def update_style(self):
        if self.is_circle:
            self.setStyleSheet(
                f"background-color: #007acc; color: white; border-radius: {CIRCLE_SIZE // 2}px; font-weight: bold; border: none;"
            )
        else:
            self.setStyleSheet(
                "background-color: #2b2b2b; color: #cccccc; border-radius: 8px; border: 1px solid #555; text-align: center;"
            )

    def on_click(self):
        self.parent().handle_bubble_click(self)


class HUDDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HUD 交互：全状态动态槽位补位测试")
        self.resize(600, 600)
        self.setStyleSheet("background-color: #1e1e1e;")

        self.debug_log_path = LOG_PATH
        self._debug_started_at = time.perf_counter()
        self._debug_sample_label = ""
        self._debug_sample_timer = QTimer(self)
        self._debug_sample_timer.timeout.connect(self._log_sample)
        self.debug_log_path.write_text(
            "HUDDemo animation trace\n"
            "Columns: elapsed | event | details | circles | rects\n",
            encoding="utf-8",
        )

        self.is_animating = False
        self.animation_timeout_ms = 2000
        self.animation_watchdog = QTimer(self)
        self.animation_watchdog.setSingleShot(True)
        self.animation_watchdog.timeout.connect(self._handle_animation_timeout)
        self.group1 = None
        self.group2 = None
        self.group3 = None
        self.group_up = None
        self.circles = []
        self.rects = []

        for i in range(2):
            bubble = Bubble(f"完成 {i + 1}", True, self)
            bubble.debug_id = f"C{i + 1}"
            self.circles.append(bubble)

        for i in range(3):
            bubble = Bubble(f"历史任务 {i + 1}", False, self)
            bubble.debug_id = f"R{i + 1}"
            self.rects.append(bubble)

        self.layout_initial()
        self._log_state("init")

    def get_circle_rect(self, index_from_right):
        x = RIGHT_MARGIN - CIRCLE_SIZE - index_from_right * (CIRCLE_SIZE + SPACING)
        return self._validate_target_rect(
            QRect(x, CIRCLE_Y, CIRCLE_SIZE, CIRCLE_SIZE),
            f"circle slot {index_from_right}",
        )

    def get_rect_start_y(self, circle_count):
        if circle_count == 0:
            return CIRCLE_Y
        return CIRCLE_Y + CIRCLE_SIZE + SPACING

    def get_rect_rect(self, index_from_top, circle_count):
        x = RIGHT_MARGIN - RECT_W
        y = self.get_rect_start_y(circle_count) + index_from_top * (RECT_H + SPACING)
        return self._validate_target_rect(
            QRect(x, y, RECT_W, RECT_H),
            f"rect slot {index_from_top} with {circle_count} circles",
        )

    def _validate_target_rect(self, rect, context):
        if rect.width() <= 0 or rect.height() <= 0:
            raise ValueError(f"Invalid target rect for {context}: {rect}")
        return QRect(rect)

    def _rect_text(self, rect):
        return f"({rect.x()},{rect.y()},{rect.width()},{rect.height()})"

    def _bubble_text(self, bubble):
        rect = bubble.geometry()
        shape = "C" if bubble.is_circle else "R"
        return (
            f"{bubble.debug_id}:{shape}:{bubble.text()}"
            f"@{self._rect_text(rect)}:op={bubble.effect.opacity():.2f}"
        )

    def _ids_text(self, bubbles):
        return "[" + ",".join(bubble.debug_id for bubble in bubbles) + "]"

    def _log(self, event, **fields):
        elapsed_ms = (time.perf_counter() - self._debug_started_at) * 1000.0
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{elapsed_ms:8.1f}ms | {event}"
        if details:
            line += f" | {details}"
        print(line)
        with self.debug_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _log_state(self, event, **fields):
        self._log(
            event,
            **fields,
            circles=self._ids_text(self.circles),
            rects=self._ids_text(self.rects),
            circle_state=";".join(self._bubble_text(bubble) for bubble in self.circles),
            rect_state=";".join(self._bubble_text(bubble) for bubble in self.rects),
        )

    def _log_sample(self):
        if self.is_animating:
            self._log_state(f"sample.{self._debug_sample_label}")

    def _start_sampling(self, label):
        self._debug_sample_label = label
        if not self._debug_sample_timer.isActive():
            self._debug_sample_timer.start(SAMPLE_INTERVAL_MS)

    def _stop_sampling(self):
        self._debug_sample_timer.stop()
        self._debug_sample_label = ""

    def _target_map_text(self, pairs):
        return ";".join(
            f"{bubble.debug_id}->{self._rect_text(rect)}" for bubble, rect in pairs
        )

    def _start_animation_guard(self, label):
        self.is_animating = True
        self.animation_watchdog.start(self.animation_timeout_ms)
        self._start_sampling(label)
        print(f"动画开始: {label}")
        self._log_state("animation.start", label=label)

    def _touch_animation_guard(self):
        if self.is_animating:
            self.animation_watchdog.start(self.animation_timeout_ms)

    def _stop_animation_object(self, attr_name):
        animation = getattr(self, attr_name, None)
        if animation is None:
            return
        try:
            if animation.state() != QAbstractAnimation.Stopped:
                animation.stop()
        except RuntimeError:
            pass
        setattr(self, attr_name, None)

    def _force_reset_animation_state(self, reason):
        print(f"警告：{reason}，强制重置动画状态")
        self._log_state("animation.force_reset", reason=reason)
        self.animation_watchdog.stop()
        self._stop_sampling()
        for attr_name in ("group1", "group2", "group3", "group_up"):
            self._stop_animation_object(attr_name)
        self.is_animating = False

    def _handle_animation_timeout(self):
        if self.is_animating:
            self._force_reset_animation_state(
                f"动画超过 {self.animation_timeout_ms}ms 未结束"
            )

    def layout_initial(self):
        for i, bubble in enumerate(reversed(self.circles)):
            bubble.setGeometry(self.get_circle_rect(i))
        for i, bubble in enumerate(self.rects):
            bubble.setGeometry(self.get_rect_rect(i, len(self.circles)))

    def handle_bubble_click(self, bubble):
        if self.is_animating:
            self._force_reset_animation_state("检测到动画卡死或重复点击")

        self._log_state(
            "click",
            clicked=bubble.debug_id,
            shape="circle" if bubble.is_circle else "rect",
        )
        self._start_animation_guard(
            "circle_to_rect" if bubble.is_circle else "rect_to_circle"
        )

        try:
            if bubble.is_circle:
                self.animate_circle_to_rect(bubble)
            else:
                self.animate_rect_to_circle(bubble)
        except Exception as exc:
            print(f"动画执行出错: {exc}")
            self._force_reset_animation_state("动画入口异常")

    def animate_circle_to_rect(self, clicked_bubble):
        new_circles = self.circles.copy()
        new_circles.remove(clicked_bubble)
        new_circles.append(clicked_bubble)

        self.group1 = QParallelAnimationGroup(self)
        phase1_targets = []

        anim_fade_out = QPropertyAnimation(clicked_bubble.effect, b"opacity")
        anim_fade_out.setEndValue(0.0)
        anim_fade_out.setDuration(200)
        self.group1.addAnimation(anim_fade_out)

        for list_idx, bubble in enumerate(new_circles[:-1]):
            idx_from_right = len(new_circles) - 1 - list_idx
            target_rect = self.get_circle_rect(idx_from_right)
            phase1_targets.append((bubble, target_rect))
            anim_shift = QPropertyAnimation(bubble, b"geometry")
            anim_shift.setEndValue(target_rect)
            anim_shift.setDuration(300)
            anim_shift.setEasingCurve(QEasingCurve.InOutQuad)
            self.group1.addAnimation(anim_shift)

        self._log_state(
            "circle_to_rect.phase1.start",
            clicked=clicked_bubble.debug_id,
            staged_circles=self._ids_text(new_circles),
            targets=self._target_map_text(phase1_targets),
        )
        self.group1.finished.connect(
            lambda: self._circle_to_rect_phase1_finished(clicked_bubble, new_circles)
        )
        self.group1.start()

    def _circle_to_rect_phase1_finished(self, clicked_bubble, new_circles):
        self._log_state(
            "circle_to_rect.phase1.finished",
            clicked=clicked_bubble.debug_id,
            staged_circles=self._ids_text(new_circles),
        )
        self._circle_to_rect_phase2(clicked_bubble, new_circles)

    def _circle_to_rect_phase2(self, clicked_bubble, new_circles):
        try:
            self._touch_animation_guard()
            rightmost_rect = self.get_circle_rect(0)
            clicked_bubble.setGeometry(rightmost_rect)
            self.group2 = QPropertyAnimation(clicked_bubble.effect, b"opacity", self)
            self.group2.setEndValue(1.0)
            self.group2.setDuration(150)

            self._log_state(
                "circle_to_rect.phase2.start",
                clicked=clicked_bubble.debug_id,
                silent_reset=self._rect_text(rightmost_rect),
            )
            self.group2.finished.connect(
                lambda: self._circle_to_rect_phase2_finished(
                    clicked_bubble, new_circles
                )
            )
            self.group2.start()
        except Exception as exc:
            print(f"circle_to_rect phase2 出错: {exc}")
            self._force_reset_animation_state("circle_to_rect phase2 异常")

    def _circle_to_rect_phase2_finished(self, clicked_bubble, new_circles):
        self._log_state(
            "circle_to_rect.phase2.finished",
            clicked=clicked_bubble.debug_id,
            staged_circles=self._ids_text(new_circles),
        )
        self._circle_to_rect_phase3(clicked_bubble, new_circles)

    def _circle_to_rect_phase3(self, clicked_bubble, new_circles):
        try:
            self._touch_animation_guard()
            clicked_bubble.is_circle = False
            clicked_bubble.setText("恢复运行中")
            clicked_bubble.update_style()

            self.group3 = QParallelAnimationGroup(self)
            circle_count_after = len(new_circles) - 1
            phase3_targets = []

            anim_descend = QPropertyAnimation(clicked_bubble, b"geometry")
            target_rect = self.get_rect_rect(0, circle_count_after)
            phase3_targets.append((clicked_bubble, target_rect))
            anim_descend.setEndValue(target_rect)
            anim_descend.setDuration(400)
            anim_descend.setEasingCurve(QEasingCurve.OutBack)
            self.group3.addAnimation(anim_descend)

            for i, bubble in enumerate(self.rects):
                anim_rect_down = QPropertyAnimation(bubble, b"geometry")
                target_rect = self.get_rect_rect(i + 1, circle_count_after)
                phase3_targets.append((bubble, target_rect))
                anim_rect_down.setEndValue(target_rect)
                anim_rect_down.setDuration(400)
                anim_rect_down.setEasingCurve(QEasingCurve.OutBack)
                self.group3.addAnimation(anim_rect_down)

            for list_idx, bubble in enumerate(new_circles[:-1]):
                idx_from_right = circle_count_after - 1 - list_idx
                target_rect = self.get_circle_rect(idx_from_right)
                phase3_targets.append((bubble, target_rect))
                anim_circle_right = QPropertyAnimation(bubble, b"geometry")
                anim_circle_right.setEndValue(target_rect)
                anim_circle_right.setDuration(400)
                anim_circle_right.setEasingCurve(QEasingCurve.InOutQuad)
                self.group3.addAnimation(anim_circle_right)

            self._log_state(
                "circle_to_rect.phase3.start",
                clicked=clicked_bubble.debug_id,
                circle_count_after=circle_count_after,
                targets=self._target_map_text(phase3_targets),
            )
            self.group3.finished.connect(
                lambda: self._circle_to_rect_phase3_finished(
                    clicked_bubble, new_circles
                )
            )
            self.group3.start()
        except Exception as exc:
            print(f"circle_to_rect phase3 出错: {exc}")
            self._force_reset_animation_state("circle_to_rect phase3 异常")

    def _circle_to_rect_phase3_finished(self, clicked_bubble, new_circles):
        self._log_state(
            "circle_to_rect.phase3.finished",
            clicked=clicked_bubble.debug_id,
            next_circles=self._ids_text(new_circles[:-1]),
            next_rects=self._ids_text([clicked_bubble] + self.rects),
        )
        self.finalize_state(new_circles[:-1], [clicked_bubble] + self.rects)

    def animate_rect_to_circle(self, clicked_bubble):
        clicked_idx = self.rects.index(clicked_bubble)

        new_rects = self.rects.copy()
        new_rects.remove(clicked_bubble)
        new_circles = self.circles + [clicked_bubble]
        circle_count_after = len(new_circles)

        clicked_bubble.is_circle = True
        clicked_bubble.setText("新完成")
        clicked_bubble.update_style()

        self.group_up = QParallelAnimationGroup(self)
        self._touch_animation_guard()
        group_up_targets = []

        anim_fly = QPropertyAnimation(clicked_bubble, b"geometry")
        target_rect = self.get_circle_rect(0)
        group_up_targets.append((clicked_bubble, target_rect))
        anim_fly.setEndValue(target_rect)
        anim_fly.setDuration(500)
        anim_fly.setEasingCurve(QEasingCurve.InOutQuad)
        self.group_up.addAnimation(anim_fly)

        for list_idx, bubble in enumerate(self.circles):
            idx_from_right = len(new_circles) - 1 - list_idx
            anim_c_shift = QPropertyAnimation(bubble, b"geometry")
            target_rect = self.get_circle_rect(idx_from_right)
            group_up_targets.append((bubble, target_rect))
            anim_c_shift.setEndValue(target_rect)
            anim_c_shift.setDuration(500)
            anim_c_shift.setEasingCurve(QEasingCurve.InOutQuad)
            self.group_up.addAnimation(anim_c_shift)

        for i, bubble in enumerate(new_rects):
            target_rect = self.get_rect_rect(i, circle_count_after)

            if self.rects.index(bubble) < clicked_idx:
                seq = QSequentialAnimationGroup(self)

                anim_yield = QPropertyAnimation(bubble, b"geometry")
                yield_rect = bubble.geometry()
                yield_rect.translate(-80, 0)
                anim_yield.setEndValue(yield_rect)
                anim_yield.setDuration(250)
                group_up_targets.append((bubble, yield_rect))

                anim_settle = QPropertyAnimation(bubble, b"geometry")
                anim_settle.setEndValue(target_rect)
                anim_settle.setDuration(250)
                group_up_targets.append((bubble, target_rect))

                seq.addAnimation(anim_yield)
                seq.addAnimation(anim_settle)
                self.group_up.addAnimation(seq)
            else:
                anim_move = QPropertyAnimation(bubble, b"geometry")
                anim_move.setEndValue(target_rect)
                anim_move.setDuration(500)
                anim_move.setEasingCurve(QEasingCurve.InOutQuad)
                group_up_targets.append((bubble, target_rect))
                self.group_up.addAnimation(anim_move)

        self._log_state(
            "rect_to_circle.start",
            clicked=clicked_bubble.debug_id,
            clicked_idx=clicked_idx,
            next_circles=self._ids_text(new_circles),
            next_rects=self._ids_text(new_rects),
            targets=self._target_map_text(group_up_targets),
        )
        self.group_up.finished.connect(
            lambda: self._rect_to_circle_finished(new_circles, new_rects)
        )
        self.group_up.start()

    def _rect_to_circle_finished(self, new_circles, new_rects):
        self._log_state(
            "rect_to_circle.finished",
            next_circles=self._ids_text(new_circles),
            next_rects=self._ids_text(new_rects),
        )
        self.finalize_state(new_circles, new_rects)

    def finalize_state(self, new_circles, new_rects):
        self.circles = new_circles
        self.rects = new_rects
        self.animation_watchdog.stop()
        self._stop_sampling()
        self.is_animating = False
        self._log_state("animation.finalize")
        print(f"动画结束：当前圆形数量 {len(self.circles)}，方形数量 {len(self.rects)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = HUDDemo()
    window.show()
    sys.exit(app.exec())
