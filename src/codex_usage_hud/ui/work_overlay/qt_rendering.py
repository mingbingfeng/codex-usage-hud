"""Qt card, completed-badge, and layout rendering owner."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    WORK_OVERLAY_BODY_MAX_LINES,
    WORK_OVERLAY_CARD_SPACING,
    WORK_OVERLAY_CARD_X_PADDING,
    WORK_OVERLAY_CARD_Y_PADDING,
    WORK_OVERLAY_CLOSE_SIZE,
    WORK_OVERLAY_COMPLETED_BADGE_ROW_HEIGHT,
    WORK_OVERLAY_COMPLETED_BADGE_SIZE,
    WORK_OVERLAY_EMPTY_GRACE_SECONDS,
    WORK_OVERLAY_FEED_SPINNER_ENABLED,
    WORK_OVERLAY_FEED_SPINNER_TIMER_MS,
    WORK_OVERLAY_MARGIN,
    WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED,
    WORK_OVERLAY_TEXT_WRAP_WIDTH,
    WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
    WORK_OVERLAY_WIDTH,
    WORK_OVERLAY_WORKDIR_FOOTER_WIDTH,
)
from .geometry import (
    OverlayRect,
    _card_slot_rect,
    _completed_badge_row_width,
    _completed_slot_rect,
    _defer_other_transition_items,
    _detect_transition,
    _detect_transition_item_id,
    _find_item_rect,
    _overlay_items_required_height,
    _overlay_window_top_y,
    _overlay_window_x,
    _transition_layout_width,
)
from .model import (
    _compact_work_text,
    _item_id,
    _item_is_background_usage,
    _item_is_completed,
    _item_is_rest_reminder,
    _item_is_system_action,
    _item_is_system_notice,
    _matched_overlay_item_records,
    _overlay_activity_step_texts,
    _overlay_activity_steps,
    _overlay_activity_tooltip,
    _overlay_feed_active,
    _overlay_feed_rows,
    _overlay_feed_spinner_frame,
    _overlay_footer_selection,
    _overlay_step_elapsed_seconds,
    _normalized_rest_reminder,
    _normalized_system_action,
    _normalized_system_notice,
    _ordered_overlay_items,
    _rest_reminder_overlay_item,
    _system_action_overlay_item,
    _system_notice_overlay_item,
    _visible_overlay_items,
    _work_overlay_header_text,
    _work_overlay_item_with_live_elapsed_text,
    _work_overlay_live_elapsed_text,
    _workdir_clickable_for_item,
    _workdir_display_name,
    _workdir_external_link_for_item,
    _workdir_footer_display_name,
)
from .qt_visuals import (
    CardSwitchPendingOverlayWidget,
    CompletedBadgeWidget,
    ShimmerTextLabel,
)
from .theme import _color_for, _overlay_payload_signature, _round_badge_palette, _theme_mix

widget_attrs = Qt.WidgetAttribute
focus_policy = Qt.FocusPolicy
alignment = Qt.AlignmentFlag
text_format = Qt.TextFormat
window_type = Qt.WindowType


class OverlayRenderingMixin:
    def _wrapped_label_height(self, label: QLabel, width: int) -> int:
        return max(label.sizeHint().height(), label.heightForWidth(width), label.minimumSizeHint().height())

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
        self._system_action_anchors.clear()
        self._rest_action_anchors.clear()
        self._card_hover_anchors.clear()
        self._completed_hover_anchors.clear()
        feed_row_anchors = getattr(self, "_feed_row_anchors", None)
        if isinstance(feed_row_anchors, list):
            feed_row_anchors.clear()
        self._item_widgets.clear()
        self.circles.clear()
        self.rects.clear()
        for child in list(self._shell.findChildren(QWidget)):
            if child.parent() is self._shell:
                child.deleteLater()

    def _sync_live_elapsed_timer(
        self,
        items: Sequence[Mapping[str, object]],
    ) -> None:
        feed_active = any(_overlay_feed_active(item) for item in items)
        if feed_active or any(
            _work_overlay_live_elapsed_text(item) is not None for item in items
        ):
            if not self._elapsed_text_timer.isActive():
                self._elapsed_text_timer.start()
        else:
            self._elapsed_text_timer.stop()
        spinner_timer = getattr(self, "_feed_spinner_timer", None)
        if isinstance(spinner_timer, QTimer):
            if feed_active and WORK_OVERLAY_FEED_SPINNER_ENABLED:
                if not spinner_timer.isActive():
                    spinner_timer.start()
            else:
                spinner_timer.stop()

    def _activity_step_display_text(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
        *,
        now: float | None = None,
    ) -> tuple[str, str]:
        """Show the newest parsed action as soon as the payload refreshes.

        The footer picks its row by priority (running > 等确认 > 失败 > 思考
        标题 > 末条). Running rows carry the local elapsed seconds that share
        the 1s elapsed tick, and thinking rows carry the rotating spinner;
        the full step list stays available in the tooltip.
        """
        del record, now
        texts = _overlay_activity_step_texts(item)
        if not texts:
            return "", ""
        kind, text = _overlay_footer_selection(item)
        if not text:
            return "", ""
        if kind == "running":
            steps = _overlay_activity_steps(item)
            running = [
                step
                for step in steps
                if str(step.get("status") or "").strip().lower() == "running"
            ]
            elapsed = (
                _overlay_step_elapsed_seconds(
                    running[-1],
                    datetime.now().astimezone(),
                )
                if running
                else 0
            )
            spinner = self._feed_spinner_glyph()
            prefix = f"{spinner} " if spinner else ""
            text = f"{prefix}{text} · {elapsed}s"
        elif kind == "thinking":
            spinner = self._feed_spinner_glyph()
            prefix = f"{spinner} " if spinner else ""
            text = f"{prefix}{text}"
        return text, _overlay_activity_tooltip(item)

    def _feed_spinner_glyph(self) -> str:
        if not WORK_OVERLAY_FEED_SPINNER_ENABLED:
            return ""
        return _overlay_feed_spinner_frame(getattr(self, "_feed_spinner_tick", 0))

    def _footer_elide_width(self, label: QLabel) -> int:
        # Reserve room for the local-seconds suffix and the workdir leaf that
        # share the footer row; commands keep head and tail via middle elision.
        return max(60, WORK_OVERLAY_TEXT_WRAP_WIDTH - 96)

    def _footer_display_text(self, label: QLabel, text: str) -> str:
        return QFontMetrics(label.font()).elidedText(
            text,
            Qt.TextElideMode.ElideMiddle,
            self._footer_elide_width(label),
        )

    def _feed_rows_for_item(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
    ) -> list[dict[str, str]]:
        """Build width-elided feed rows, or the single peek row when reviewing."""
        if str(record.get("kind") or "") != "card":
            return []
        detail = record.get("detail")
        if not isinstance(detail, QLabel):
            return []
        if not _overlay_feed_active(item):
            peek_ids = getattr(self, "_feed_peek_item_ids", None)
            if isinstance(peek_ids, set):
                peek_ids.discard(_item_id(item))
            return []
        item_id = _item_id(item)
        peek_ids = getattr(self, "_feed_peek_item_ids", None)
        if (
            isinstance(peek_ids, set)
            and item_id
            and item_id in peek_ids
            and str(item.get("lastText") or "").strip()
        ):
            return [
                {
                    "kind": "peek",
                    "text": "",
                    "tooltip": "回看输出（临时） · 点击返回活动",
                    "action": "resume_feed",
                    "command": "",
                }
            ]
        rows = _overlay_feed_rows(
            item,
            spinner_frame=self._feed_spinner_glyph(),
            now=datetime.now().astimezone(),
        )
        if not rows:
            return []
        metrics = QFontMetrics(detail.font())
        width = WORK_OVERLAY_TEXT_WRAP_WIDTH
        for row in rows:
            text = str(row.get("text") or "")
            if row.get("kind") == "cmd" and row.get("command"):
                command = str(row["command"])
                row["text"] = metrics.elidedText(
                    f"$ {command}",
                    Qt.TextElideMode.ElideMiddle,
                    width,
                )
            elif text:
                row["text"] = metrics.elidedText(
                    text,
                    Qt.TextElideMode.ElideRight,
                    width,
                )
        return rows

    def _refresh_feed_body(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
    ) -> None:
        """Timer-path refresh of the feed body text (seconds and spinner)."""
        detail = record.get("detail")
        if not isinstance(detail, QLabel):
            return
        rows = self._feed_rows_for_item(record, item)
        if not rows or str(rows[0].get("action") or "") == "resume_feed":
            return
        text = "\n".join(str(row.get("text") or "") for row in rows)
        if text == detail.text():
            return
        detail.setText(text)
        detail.setToolTip(
            "\n".join(
                str(row.get("tooltip") or "") for row in rows if row.get("tooltip")
            )
        )

    def _sync_feed_anchor_geometry(self, record: dict[str, Any]) -> None:
        """Position one invisible click target over each visible feed row."""
        rows = record.get("feed_rows_meta") or []
        anchors = record.get("feed_anchors") or []
        detail = record.get("detail")
        if not isinstance(detail, QLabel) or not anchors:
            return
        count = max(1, len(rows))
        row_height = max(1, detail.height() // count) if rows else 0
        for index, anchor in enumerate(anchors):
            if index >= len(rows):
                anchor.hide()
                continue
            anchor.setGeometry(
                detail.x(),
                detail.y() + index * row_height,
                detail.width(),
                row_height,
            )
            anchor.show()

    def _tick_feed_spinner(self) -> None:
        """Rotate the feed spinner glyph; only runs while a feed is active."""
        if not WORK_OVERLAY_FEED_SPINNER_ENABLED:
            return
        self._feed_spinner_tick = int(getattr(self, "_feed_spinner_tick", 0)) + 1
        for record in self._item_widgets:
            if record.get("kind") != "card":
                continue
            item = record.get("item")
            if not isinstance(item, Mapping):
                continue
            if not _overlay_feed_active(item):
                continue
            self._refresh_feed_body(record, item)
            self._refresh_activity_step_label(record, item)

    @staticmethod
    def _workdir_footer_width(label: QLabel, text: str) -> int:
        """Reserve only the visible provider/workdir width, up to its cap."""
        natural_width = QFontMetrics(label.font()).horizontalAdvance(text) + 4
        return max(1, min(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH, natural_width))

    def _refresh_activity_step_label(
        self,
        record: dict[str, Any],
        item: Mapping[str, object],
        *,
        now: float | None = None,
    ) -> None:
        label = record.get("status_label")
        if not isinstance(label, ShimmerTextLabel):
            return
        if (
            _item_is_system_action(item)
            or _item_is_system_notice(item)
            or _item_is_background_usage(item)
            or _item_is_rest_reminder(item)
            or str(item.get("status") or "").strip() == "draft"
        ):
            return
        text, tooltip = self._activity_step_display_text(record, item, now=now)
        if not text:
            return
        label.setText(self._footer_display_text(label, text))
        label.setToolTip(tooltip or text)

    def _refresh_live_elapsed_text(self) -> None:
        now = time.monotonic()
        for record in self._item_widgets:
            if record.get("kind") != "card":
                continue
            item = record.get("item")
            if not isinstance(item, Mapping):
                continue
            updated_item = _work_overlay_item_with_live_elapsed_text(item)
            elapsed_text = str(updated_item.get("elapsedText") or "")
            if elapsed_text != str(item.get("elapsedText") or ""):
                record["item"] = updated_item
                header = record.get("header")
                if isinstance(header, QLabel):
                    header.setText(
                        _work_overlay_header_text(
                            str(item.get("startedAt") or ""),
                            elapsed_text,
                            str(item.get("title") or "Codex 工作"),
                            title_limit=self._header_title_limit,
                        )
                    )
                item = updated_item
            self._refresh_activity_step_label(record, item, now=now)
            self._refresh_feed_body(record, item)

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
        if workdir_text and _workdir_external_link_for_item(item):
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

        header_meta = QLabel("", card)
        header_meta.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        header_meta.setWordWrap(False)
        header_meta.setTextFormat(text_format.PlainText)
        header_meta.setAlignment(alignment.AlignVCenter | alignment.AlignRight)
        header_meta.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header_meta.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.DemiBold))
        header_meta.setVisible(False)
        head_layout.addWidget(header_meta, 0)

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

        rest_hint = QLabel("", card)
        rest_hint.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_hint.setWordWrap(False)
        rest_hint.setTextFormat(text_format.PlainText)
        rest_hint.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        rest_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        rest_hint.setFont(QFont("Microsoft YaHei UI", 7))
        rest_hint.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
        rest_hint.setFixedHeight(rest_hint.fontMetrics().height() + 4)
        rest_hint.setVisible(False)
        card_layout.addWidget(rest_hint)

        footer_container = QWidget(card)
        footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        footer_layout = QHBoxLayout(footer_container)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
        status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        status_label.setSingleLine(True)
        status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        status_label.setMinimumWidth(1)
        footer_layout.addWidget(status_label, 1, alignment.AlignVCenter)

        rest_actions_row = QWidget(card)
        rest_actions_row.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_row_layout = QHBoxLayout(rest_actions_row)
        rest_row_layout.setContentsMargins(0, 0, 0, 0)
        rest_row_layout.setSpacing(4)
        rest_actions_row.setFixedHeight(24)

        secondary_action_label = QLabel("", rest_actions_row)
        secondary_action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        secondary_action_label.setTextFormat(text_format.PlainText)
        secondary_action_label.setAlignment(alignment.AlignCenter)
        secondary_action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
        secondary_action_label.setFixedHeight(24)
        secondary_action_label.setVisible(False)
        rest_row_layout.addWidget(secondary_action_label, 0)

        extra_rest_action_labels: list[QLabel] = []
        for _ in range(4):
            action_label = QLabel("", rest_actions_row)
            action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            action_label.setTextFormat(text_format.PlainText)
            action_label.setAlignment(alignment.AlignCenter)
            action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
            action_label.setFixedHeight(24)
            action_label.setVisible(False)
            rest_row_layout.addWidget(action_label, 0)
            extra_rest_action_labels.append(action_label)

        primary_action_label = QLabel("", rest_actions_row)
        primary_action_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        primary_action_label.setTextFormat(text_format.PlainText)
        primary_action_label.setAlignment(alignment.AlignCenter)
        primary_action_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        primary_action_label.setFixedHeight(24)
        primary_action_label.setVisible(False)
        rest_row_layout.addWidget(primary_action_label, 0)

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
        workdir_label.setMinimumWidth(0)
        workdir_label.setMaximumWidth(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH)
        workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
        workdir_label.setFixedWidth(1)
        workdir_label.setStyleSheet(
            "QLabel {"
            "color: #5E6A78;"
            "border: none;"
            "background: transparent;"
            "}"
        )
        footer_height = max(
            18,
            status_label.fontMetrics().height() + 4,
            workdir_label.fontMetrics().height() + 4,
        )
        footer_container.setFixedHeight(footer_height)
        status_label.setFixedHeight(footer_height)
        workdir_label.setFixedHeight(footer_height)
        footer_layout.addWidget(workdir_label, 0, alignment.AlignVCenter)
        footer_layout.addWidget(round_badge, 0, alignment.AlignVCenter)

        card_layout.addWidget(footer_container)
        card_layout.addWidget(rest_actions_row)
        switch_overlay = CardSwitchPendingOverlayWidget(card)

        # Invisible per-row click targets over the activity feed body. Their
        # geometry tracks the detail label rows and is refreshed in the
        # interactive-window reposition pass after the layout settles.
        feed_anchors: list[QWidget] = []
        for _ in range(WORK_OVERLAY_BODY_MAX_LINES):
            feed_anchor = QWidget(card)
            feed_anchor.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
            feed_anchor.hide()
            feed_anchors.append(feed_anchor)

        record = {
            "kind": "card",
            "item_id": _item_id(item),
            "card": card,
            "header": header,
            "header_meta": header_meta,
            "detail": detail,
            "rest_hint": rest_hint,
            "footer_container": footer_container,
            "rest_actions_row": rest_actions_row,
            "rest_actions_layout": rest_row_layout,
            "status_label": status_label,
            "secondary_action_label": secondary_action_label,
            "primary_action_label": primary_action_label,
            "rest_action_labels": [
                secondary_action_label,
                primary_action_label,
                *extra_rest_action_labels,
            ],
            "round_badge": round_badge,
            "workdir_label": workdir_label,
            "switch_overlay": switch_overlay,
            "close_anchor": close_anchor,
            "feed_anchors": feed_anchors,
            "feed_rows_meta": [],
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
        item = _work_overlay_item_with_live_elapsed_text(item)
        record["item"] = dict(item)
        status = str(item.get("status") or "")
        system_action = _item_is_system_action(item)
        system_notice = _item_is_system_notice(item)
        background_usage = _item_is_background_usage(item)
        rest_reminder = _item_is_rest_reminder(item)
        # Renderer selection is a visual hint only; ordering is owned by the
        # payload projection and must not be inferred from this flag.
        current = bool(item.get("current")) and not (
            system_action or system_notice or background_usage or rest_reminder
        )
        accent, pill_bg, card_bg, border_color = _color_for(
            status,
            self._theme_tokens,
        )
        current_border_color = (
            _theme_mix(border_color, accent, 0.78, fallback=border_color)
            if current
            else border_color
        )
        feed_rows: list[dict[str, str]] = []
        peeking = False
        if not (
            system_action
            or system_notice
            or background_usage
            or rest_reminder
            or status == "draft"
        ):
            feed_rows = self._feed_rows_for_item(record, item)
            peeking = bool(feed_rows) and str(
                feed_rows[0].get("action") or ""
            ) == "resume_feed"
        theme = self._theme_tokens
        elapsed_text = (
            ""
            if system_action or system_notice or background_usage or rest_reminder
            else str(item.get("elapsedText") or "").strip() or "已处理 --"
        )
        header_text = (
            str(item.get("title") or "Codex HUD")
            if system_notice
            else (
                str(item.get("title") or "休息提醒")
                if rest_reminder
                else (
                    str(item.get("statusLabel") or "Codex App 后台任务：未知后台任务")
                    if background_usage
                    else _work_overlay_header_text(
                        str(item.get("startedAt") or ""),
                        elapsed_text,
                        str(item.get("title") or "Codex 工作"),
                        title_limit=self._header_title_limit,
                    )
                )
            )
        )
        if rest_reminder and str(item.get("headerElapsed") or "").strip():
            header_text = " · ".join(
                part
                for part in (
                    header_text,
                    str(item.get("headerElapsed") or "").strip(),
                )
                if part
            )

        card = record["card"]
        if peeking:
            # 回看输出：dashed accent border marks the temporary output view.
            card.setStyleSheet(
                "QFrame {"
                f"background-color: {card_bg};"
                f"border: 1px dashed {accent};"
                f"border-left: 1px dashed {accent};"
                "border-radius: 10px;"
                "}"
            )
        else:
            card.setStyleSheet(
                "QFrame {"
                f"background-color: {card_bg};"
                f"border: 1px solid {border_color};"
                f"border-left: {2 if current else 1}px solid {current_border_color};"
                "border-radius: 10px;"
                "}"
            )

        header = record["header"]
        header.setText(header_text)
        header.setStyleSheet(
            "QLabel {"
            f"color: {accent if status in {'recent', 'background_usage'} else _theme_mix(theme['text'], theme['muted'], 0.36, fallback=theme['text'])};"
            "border: none;"
            "background: transparent;"
            "}"
        )

        close_anchor = record["close_anchor"]
        close_anchor.setVisible(not rest_reminder)

        header_meta = record.get("header_meta")
        header_meta_text = (
            str(item.get("headerMeta") or "").strip() if rest_reminder else ""
        )
        if isinstance(header_meta, QLabel) and header_meta_text:
            header_meta.setText(header_meta_text)
            header_meta.setStyleSheet(
                "QLabel {"
                f"color: {theme['muted']};"
                "border: none;"
                "background: transparent;"
                "padding: 0;"
                "}"
            )
            header_meta.adjustSize()
            header_meta.setFixedWidth(header_meta.sizeHint().width())
            header_meta.setVisible(True)
        elif isinstance(header_meta, QLabel):
            header_meta.setText("")
            header_meta.setVisible(False)

        round_badge = record["round_badge"]
        round_index = max(0, int(item.get("roundIndex") or 0))
        badge_visible = not rest_reminder and status != "recent" and round_index > 0
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

        if background_usage:
            feature_text = str(item.get("title") or "未知后台任务").strip()
            model_text = str(item.get("modelName") or "").strip()
            progress_text = str(item.get("progress") or item.get("detail") or "").strip()
            secondary = " · ".join(
                part for part in (model_text, progress_text) if part
            )
            body_text = "\n".join(
                part for part in (feature_text, secondary) if part
            )
        else:
            body_text = str(item.get("lastText") or item.get("detail") or "").strip()
        detail = record["detail"]
        if feed_rows and not peeking:
            # Live activity feed: pre-elided single lines so each row keeps a
            # stable click band without touching the body layout constants.
            detail_text = "\n".join(str(row.get("text") or "") for row in feed_rows)
            detail.setToolTip(
                "\n".join(
                    str(row.get("tooltip") or "")
                    for row in feed_rows
                    if row.get("tooltip")
                )
            )
        else:
            detail_text = self._multiline_elided_text(
                body_text,
                font=detail.font(),
                width=WORK_OVERLAY_TEXT_WRAP_WIDTH,
            )
            if peeking:
                detail.setToolTip("回看输出（临时） · 点击返回活动")
            else:
                detail.setToolTip(
                    body_text if detail_text and detail_text != body_text else ""
                )
        detail.setText(detail_text)
        detail.setFixedHeight(self._wrapped_label_height(detail, WORK_OVERLAY_TEXT_WRAP_WIDTH))
        detail.setStyleSheet(
            "QLabel {"
            f"color: {theme['requestText']};"
            "border: none;"
            "background: transparent;"
            "}"
        )
        record["feed_rows_meta"] = list(feed_rows)
        self._sync_feed_anchor_geometry(record)

        rest_hint = record["rest_hint"]
        rest_hint_text = (
            str(item.get("restHint") or "").strip() if rest_reminder else ""
        )
        rest_hint.setText(rest_hint_text)
        rest_hint.setToolTip("")
        rest_hint.setStyleSheet(
            "QLabel {"
            f"color: {theme['muted']};"
            "border: none;"
            "background: transparent;"
            "}"
        )
        rest_hint.setVisible(bool(rest_hint_text))

        workdir_text = _workdir_display_name(item)
        workdir_footer_text = _workdir_footer_display_name(item, limit=96)
        full_workdir = str(item.get("workdir") or "").strip()
        workdir_clickable = _workdir_clickable_for_item(item)
        status_text = (
            " · ".join(
                part
                for part in (
                    (
                        f"{str(item.get('tokensText') or '').strip()} tokens"
                        if str(item.get("tokensText") or "").strip()
                        else ""
                    ),
                    str(item.get("costText") or "").strip(),
                )
                if part
            )
            if background_usage
            else str(item.get("statusText") or item.get("statusLabel") or "").strip()
        )
        activity_text, activity_tooltip = self._activity_step_display_text(record, item)
        footer_kind = _overlay_footer_selection(item)[0]
        if activity_text and not (
            system_action
            or system_notice
            or background_usage
            or rest_reminder
            or status == "draft"
        ):
            status_text = activity_text
        footer_container = record["footer_container"]
        footer_container.setVisible(bool(status_text or workdir_footer_text or rest_reminder))

        status_label = record["status_label"]
        if status_text and not rest_reminder:
            if footer_kind == "wait" or status == "waiting_user":
                status_text_color = _color_for("waiting_user", self._theme_tokens)[0]
            elif footer_kind == "failed":
                status_text_color = _color_for("error", self._theme_tokens)[0]
            elif status in {"recent", "error", "background_usage"}:
                status_text_color = accent
            else:
                status_text_color = theme["muted"]
            footer_status_text = _compact_work_text(status_text, 240)
            status_label.setText(self._footer_display_text(status_label, footer_status_text))
            status_label.setToolTip(activity_tooltip or status_text)
            status_label.setBaseColor(status_text_color)
            status_label.setShimmerEnabled(
                not system_notice
                and not rest_reminder
                and status not in {"recent", "background_usage", "draft"}
            )
            status_label.setVisible(True)
        else:
            status_label.setText("")
            status_label.setToolTip("")
            status_label.setShimmerEnabled(False)
            status_label.setVisible(False)

        rest_actions = (
            [dict(action) for action in item.get("restActions", []) if isinstance(action, Mapping)]
            if rest_reminder
            else []
        )
        action_labels = [
            label for label in record.get("rest_action_labels", [])
            if isinstance(label, QLabel)
        ]
        # Keep every rest action in one compact horizontal bar. Secondary actions
        # stay on the left; the highlighted primary action is pushed to the right.
        rest_actions_row = record.get("rest_actions_row")
        rest_actions_layout = record.get("rest_actions_layout")
        if isinstance(rest_actions_row, QWidget):
            rest_actions_row.setVisible(bool(rest_actions))
        secondary_labels: list[QLabel] = []
        primary_label: QLabel | None = None
        for index, label in enumerate(action_labels):
            action = rest_actions[index] if index < len(rest_actions) else None
            if action is None:
                label.setText("")
                label.setVisible(False)
                continue
            primary = bool(action.get("primary"))
            label.setText(str(action.get("label") or ""))
            label.setMinimumWidth(36)
            label.setMaximumWidth(92)
            label.setStyleSheet(
                "QLabel {"
                f"color: {theme['surface'] if primary else theme['text']};"
                f"background-color: {accent if primary else pill_bg};"
                f"border: 1px solid {accent if primary else border_color};"
                "border-radius: 7px;"
                "padding: 0 5px;"
                "}"
            )
            label.adjustSize()
            label.setFixedHeight(24)
            label.setFixedWidth(max(32, min(88, label.sizeHint().width() + 4)))
            label.setVisible(True)
            if primary:
                primary_label = label
            else:
                secondary_labels.append(label)
        if isinstance(rest_actions_layout, QHBoxLayout):
            while rest_actions_layout.count():
                rest_actions_layout.takeAt(0)
            for label in secondary_labels:
                rest_actions_layout.addWidget(label, 0)
            if primary_label is not None:
                rest_actions_layout.addStretch(1)
                rest_actions_layout.addWidget(primary_label, 0)

        workdir_label = record["workdir_label"]
        if workdir_footer_text and not rest_reminder:
            footer_width = self._workdir_footer_width(
                workdir_label,
                workdir_footer_text,
            )
            workdir_label.setFixedWidth(footer_width)
            workdir_label.setText(
                QFontMetrics(workdir_label.font()).elidedText(
                    workdir_footer_text,
                    Qt.TextElideMode.ElideLeft,
                    max(1, footer_width - 4),
                )
            )
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
            workdir_label.setFixedWidth(1)
            workdir_label.setVisible(False)

        if collect_anchors:
            self._card_hover_anchors.append(card)
            if system_action:
                self._system_action_anchors.append((status_label, dict(item)))
            elif rest_reminder:
                for index, action in enumerate(rest_actions):
                    if index >= len(action_labels):
                        continue
                    label = action_labels[index]
                    action_item = dict(item)
                    action_item["action"] = str(action.get("action") or "")
                    action_item["actionLabel"] = str(action.get("label") or "")
                    if "minutes" in action:
                        action_item["minutes"] = action.get("minutes")
                    self._rest_action_anchors.append((label, action_item))
            elif not system_notice:
                self._close_anchors.append(
                    (record["close_anchor"], dict(item), card_bg, pill_bg, accent)
                )
            if (
                not system_action
                and not system_notice
                and _workdir_external_link_for_item(item)
            ):
                self._workdir_anchors.append((record["workdir_label"], dict(item)))
            feed_rows_meta = record.get("feed_rows_meta") or []
            feed_row_anchors = getattr(self, "_feed_row_anchors", None)
            if (
                feed_rows_meta
                and isinstance(feed_row_anchors, list)
                and not (
                    system_action
                    or system_notice
                    or background_usage
                    or rest_reminder
                    or status == "draft"
                )
            ):
                feed_item_id = _item_id(item)
                feed_anchors = record.get("feed_anchors") or []
                for index, row in enumerate(feed_rows_meta):
                    if index >= len(feed_anchors):
                        break
                    feed_row_anchors.append(
                        (
                            feed_anchors[index],
                            {
                                "id": feed_item_id,
                                "feedAction": str(row.get("action") or ""),
                                "command": str(row.get("command") or ""),
                            },
                        )
                    )
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

        rest_hint = QLabel("", card)
        rest_hint.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        rest_hint.setWordWrap(False)
        rest_hint.setTextFormat(text_format.PlainText)
        rest_hint.setAlignment(alignment.AlignTop | alignment.AlignLeft)
        rest_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        rest_hint.setFont(QFont("Microsoft YaHei UI", 7))
        rest_hint.setFixedWidth(WORK_OVERLAY_TEXT_WRAP_WIDTH)
        rest_hint.setFixedHeight(rest_hint.fontMetrics().height() + 4)
        rest_hint.setVisible(False)
        card_layout.addWidget(rest_hint)

        footer_container = QWidget(card)
        footer_container.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        footer_layout = QHBoxLayout(footer_container)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        status_label = ShimmerTextLabel("", footer_container, base_color="#8492A6")
        status_label.setAttribute(widget_attrs.WA_TransparentForMouseEvents, True)
        status_label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        status_label.setSingleLine(True)
        status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        status_label.setMinimumWidth(1)
        footer_layout.addWidget(status_label, 1, alignment.AlignVCenter)

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
        workdir_label.setMinimumWidth(0)
        workdir_label.setMaximumWidth(WORK_OVERLAY_WORKDIR_FOOTER_WIDTH)
        workdir_label.setFont(QFont("Microsoft YaHei UI", 7))
        workdir_label.setFixedWidth(1)
        workdir_label.setStyleSheet(
            "QLabel {"
            "color: #5E6A78;"
            "border: none;"
            "background: transparent;"
            "}"
        )
        footer_height = max(
            18,
            status_label.fontMetrics().height() + 4,
            workdir_label.fontMetrics().height() + 4,
        )
        footer_container.setFixedHeight(footer_height)
        status_label.setFixedHeight(footer_height)
        workdir_label.setFixedHeight(footer_height)
        footer_layout.addWidget(workdir_label, 0, alignment.AlignVCenter)
        footer_layout.addWidget(round_badge, 0, alignment.AlignVCenter)
        card_layout.addWidget(footer_container)

        record = {
            "kind": "card",
            "item_id": _item_id(item),
            "card": card,
            "header": header,
            "detail": detail,
            "rest_hint": rest_hint,
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
        self.circles = []
        self.rects = []
        for record, item in _matched_overlay_item_records(
            self._item_widgets,
            visible_items,
        ):
            kind = self._item_widget_kind(item)
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
                side=self._side,
            )
        )

    def _card_record_geometry(
        self,
        item_id: str,
        visible_items: Sequence[Mapping[str, object]],
    ) -> QRect:
        rect = _find_item_rect(
            visible_items,
            item_id,
            "card",
            layout_width=self._layout_width,
            side=self._side,
        )
        return QRect(
            int(round(rect[0])),
            int(round(rect[1])),
            max(1, int(round(rect[2]))),
            max(1, int(round(rect[3]))),
        )

    def _card_slot_record_geometry(
        self,
        index_from_top: int,
        completed_count: int,
    ) -> QRect:
        return self._card_geometry_for_slot(
            _card_slot_rect(
                index_from_top,
                completed_count,
                layout_width=self._layout_width,
                side=self._side,
            )
        )

    def _card_geometry_for_slot(self, slot_rect: OverlayRect) -> QRect:
        return QRect(
            int(round(slot_rect[0])),
            int(round(slot_rect[1])),
            WORK_OVERLAY_WIDTH,
            WORK_OVERLAY_TRANSITION_CARD_HEIGHT,
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

        for record in self.rects:
            widget = self._record_visual_widget(record)
            if widget is None:
                continue
            widget.setGeometry(
                self._card_record_geometry(
                    str(record.get("item_id") or ""),
                    visible_items,
                )
            )
            switch_overlay = record.get("switch_overlay")
            if isinstance(switch_overlay, CardSwitchPendingOverlayWidget):
                switch_overlay.setGeometry(widget.rect())
                if switch_overlay.isVisible():
                    switch_overlay.raise_()
            widget.show()

    def _sync_overlay_geometry(self) -> None:
        layout_width = max(WORK_OVERLAY_WIDTH, int(self._layout_width))
        content_height = max(
            1,
            self._transition_required_height,
            _overlay_items_required_height(
                self._layout_items,
                layout_width=layout_width,
                side=self._side,
            ),
        )
        screen = self._qt_app.primaryScreen()
        available_geometry = screen.availableGeometry() if screen is not None else self.geometry()
        screen_geometry = screen.geometry() if screen is not None else available_geometry
        x = _overlay_window_x(
            available_geometry.left(),
            available_geometry.right(),
            layout_width,
            WORK_OVERLAY_MARGIN,
            self._side,
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
        QTimer.singleShot(0, self._reposition_interactive_windows_if_settled)
        # QWidget visibility and geometry can lag one event turn behind the
        # rebuilt shell on Windows. Retry once after the layout reaches the
        # native window system so current anchors always get their HWNDs.
        QTimer.singleShot(48, self._reposition_interactive_windows_if_settled)

    @staticmethod
    def _system_notice_structure_change_only(
        old_items: Sequence[Mapping[str, object]],
        new_items: Sequence[Mapping[str, object]],
    ) -> bool:
        old_without_notice = [
            item for item in old_items if not _item_is_system_notice(item)
        ]
        new_without_notice = [
            item for item in new_items if not _item_is_system_notice(item)
        ]
        if [
            (
                str(item.get("id") or ""),
                "completed" if _item_is_completed(item) else "card",
            )
            for item in old_without_notice
        ] != [
            (
                str(item.get("id") or ""),
                "completed" if _item_is_completed(item) else "card",
            )
            for item in new_without_notice
        ]:
            return False
        old_notices = [item for item in old_items if _item_is_system_notice(item)]
        new_notices = [item for item in new_items if _item_is_system_notice(item)]
        if len(old_notices) > 1 or len(new_notices) > 1:
            return False
        if bool(old_notices) == bool(new_notices):
            return False
        return True

    def _remove_system_notice_record(self, item_id: str) -> None:
        record = self._record_for_item_kind(item_id, "card")
        if record is None:
            return
        try:
            self._item_widgets.remove(record)
        except ValueError:
            return
        widget = record.get("card")
        if not isinstance(widget, QWidget):
            return
        widget.hide()
        widget.deleteLater()

    def render_items(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        system_action: Mapping[str, object] | None = None,
        system_notice: Mapping[str, object] | None = None,
        rest_reminder: Mapping[str, object] | None = None,
    ) -> None:
        if system_action is not None:
            self._system_action = _normalized_system_action(system_action)
        if system_notice is not None:
            self._system_notice = _normalized_system_notice(system_notice)
        if rest_reminder is not None:
            self._rest_reminder = _normalized_rest_reminder(rest_reminder)
        if self._rest_reminder is not None:
            if not self._rest_countdown_timer.isActive():
                self._rest_countdown_timer.start()
        else:
            self._rest_countdown_timer.stop()
        if self._transition_in_progress:
            self._raw_items = list(items)
            self._sync_live_elapsed_timer(self._raw_items)
            return
        self._raw_items = list(items)
        ordered_items = _ordered_overlay_items(self._raw_items)
        visible_items = _visible_overlay_items(
            ordered_items,
            self._dismissed_instances,
            item_limit=self._item_limit,
        )
        if self._system_action is not None:
            visible_items = [
                _system_action_overlay_item(self._system_action),
                *[
                    item
                    for item in visible_items
                    if not _item_is_system_action(item)
                ],
            ]
        if self._system_notice is not None:
            visible_items = [
                _system_notice_overlay_item(self._system_notice),
                *[
                    item
                    for item in visible_items
                    if not _item_is_system_notice(item)
                ],
            ]
        if self._rest_reminder is not None:
            rest_item = _rest_reminder_overlay_item(self._rest_reminder)
            if rest_item:
                visible_items = [
                    rest_item,
                    *[item for item in visible_items if not _item_is_rest_reminder(item)],
                ]
        self._sync_switch_pending(visible_items)
        self._sync_live_elapsed_timer(visible_items)
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
        transition = (
            _detect_transition(self._previous_visible_items, visible_items)
            if WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED
            else None
        )
        if transition is not None:
            item_id = _detect_transition_item_id(self._previous_visible_items, visible_items)
            if item_id:
                previous_items = list(self._previous_visible_items)
                transition_items = _defer_other_transition_items(
                    previous_items,
                    visible_items,
                    item_id,
                )
                self._layout_width = _transition_layout_width(
                    previous_items,
                    transition_items,
                )
                self._layout_items = list(previous_items)
                self._sync_overlay_geometry()
                self._sync_item_widget_geometries(previous_items)
                # A transition may fail synchronously while constructing its
                # temporary card. Commit the next visual state first so its
                # cleanup render cannot re-enter the same transition.
                self._previous_visible_items = list(transition_items)
                self._start_transition(
                    transition,
                    item_id,
                    previous_items,
                    transition_items,
                )
                return
        previous_visible_items = list(self._previous_visible_items)
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
        record_items = _matched_overlay_item_records(
            self._item_widgets,
            visible_items,
        )
        if not rebuild and len(record_items) != len(visible_items):
            rebuild = True
        if rebuild and self._system_notice_structure_change_only(
            previous_visible_items,
            visible_items,
        ):
            old_notice = next(
                (
                    item
                    for item in previous_visible_items
                    if _item_is_system_notice(item)
                ),
                None,
            )
            new_notice = next(
                (item for item in visible_items if _item_is_system_notice(item)),
                None,
            )
            if old_notice is not None and new_notice is None:
                self._remove_system_notice_record(_item_id(old_notice))
            elif old_notice is None and new_notice is not None:
                self._build_item_widget(new_notice)
            self._last_structure_signature = structure_signature
            rebuild = False
            record_items = _matched_overlay_item_records(
                self._item_widgets,
                visible_items,
            )
        self._close_anchors.clear()
        self._workdir_anchors.clear()
        self._completed_check_anchors.clear()
        self._system_action_anchors.clear()
        self._rest_action_anchors.clear()
        self._card_hover_anchors.clear()
        self._completed_hover_anchors.clear()
        self._feed_row_anchors.clear()
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
            for record, item in record_items:
                self._update_item_widget(record, item)
        self._sync_overlay_geometry()
        self._sync_item_widget_geometries(visible_items)

__all__ = [
    'OverlayRenderingMixin',
]
