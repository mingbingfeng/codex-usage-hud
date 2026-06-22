from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.ui.qt_hud import QT_HUD_ANIMATION_MS, QtHudWindow
import codex_usage_hud.ui.qt_hud as qt_hud_module
from codex_usage_hud.ui.tk_hud import HudSettings, HudSettingsStore, WindowRect

ASSETS = ROOT / ".codex_delegate" / "reports" / "qt-renderer-side-by-side-implement-2-assets"


STATE_NAMES = {
    "bottom-collapsed": "底部 HUD 收起态",
    "bottom-expanded": "底部 HUD 展开态",
    "top-collapsed": "顶部 HUD 收起态",
    "top-expanded": "顶部 HUD 展开态",
    "settings-tab-settings": "设置弹窗 / 设置",
    "settings-tab-support": "设置弹窗 / 请作者喝咖啡",
    "settings-tab-about": "设置弹窗 / 版本更新",
}


def _payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "topLine": "更新计划保留tk模式 | $11.66 | 15.3M tokens | 缓存命中 97%",
        "requestLine": "最近模型请求轮次 | confirmed | gpt-5.5 | #92 正在运行",
        "requestStatus": "active",
        "session": "85adaf5e5dab",
        "model": "gpt-5.5",
        "source": "visual-capture",
        "lastEvent": "#92 正在运行",
        "refreshedAt": datetime.now().isoformat(),
        "activeDisplayMode": "renderer",
        "settingsPath": str(ASSETS / "mock-settings.json"),
        "settingsBridgeUrl": "http://127.0.0.1:9",
        "workOverlaySelectableMax": 6,
        "settings": {
            "daily_budget_usd": 12,
            "weekly_budget_usd": 300,
            "daily_reset_time": "10:00",
            "weekly_reset_weekday": 3,
            "weekly_reset_time": "10:00",
            "display_mode": "auto",
            "work_overlay_max_items": 3,
            "pricing_url": "https://example.invalid/prices.json",
            "budget_thresholds": [0.5, 0.8, 0.9, 1.0],
            "weekly_adjustment_usd": 0,
            "support_url": "https://github.com/mingbingfeng/codex-usage-hud",
            "model_prices": {
                "gpt-5.5": {"input": 5, "cached_input": 0.5, "output": 30, "reasoning": 30},
                "gpt-5.5-mini": {"input": 1, "cached_input": 0.1, "output": 6, "reasoning": 6},
            },
        },
        "updateState": {
            "visible": True,
            "icon": "install",
            "phase": "ready",
            "title": "可安装更新",
            "message": "可安装更新",
        },
        "topDetails": {
            "title": "更新计划保留tk模式",
            "session": "会话 85adaf5e5dab | 行 651 | 确认 15",
            "sessionCost": "$11.66",
            "sessionTokens": "15.3M",
            "sessionRounds": "15 轮确认",
            "taskOrdinalSession": "#92",
            "taskOrdinalActivity": "#92",
            "sessionMix": "缓存命中 97%",
            "sessionAverage": "均值 1.0M /轮",
            "sessionComposition": "↑241k ◎100% ↓702 ◇286 ↻241k ∑242k",
            "sessionInputTokens": "241k",
            "sessionCachedTokens": "241k",
            "sessionOutputTokens": "702",
            "sessionReasoningTokens": "286",
            "warnings": "日额度已超过 80% 阈值；周额度接近上限。",
            "heavyRoundsSummary": "Top 3",
            "heavyRounds": [
                {"title": "#92 $0.152 · ∑242k", "detail": "↑241k ◎100% ↓702 ◇286"},
                {"title": "#91 $0.138 · ∑218k", "detail": "长上下文续写"},
                {"title": "#90 $0.121 · ∑193k", "detail": "多工具调用"},
            ],
            "currentTaskLabel": "当前需求",
            "currentTask": "继续 Qt HUD 对齐任务：按五态完成真实窗口视觉和功能验收",
            "executingLabel": "正在执行",
            "executing": "python -m pytest tests/test_ui.py -k QtHudWindowLifecycleTests",
            "activityState": "已完成",
            "activityElapsedLabel": "已运行",
            "activityElapsed": "34m25s",
            "activityGapLabel": "当前等待",
            "activityGap": "92轮",
            "activityLastLabel": "需求轮次",
            "activityLast": "15",
            "slow": "最慢工具 12.4s",
            "gap": "最长等待 92轮",
            "activityTrail": [
                {"time": "19:10", "title": "任务", "detail": "Qt 视觉对齐首轮", "active": True},
                {"time": "19:09", "title": "轮次", "detail": "$0.152 · ∑242k"},
                {"time": "19:08", "title": "工具调用", "detail": "pytest tests/test_ui.py"},
                {"time": "19:07", "title": "工具完成", "detail": "7 passed"},
                {"time": "19:06", "title": "截图", "detail": "准备捕获五态"},
                {"time": "19:05", "title": "修改", "detail": "进度条 overflow badge"},
            ],
        },
        "topCopies": {"slow": "pytest tests/test_ui.py", "gap": "最长等待 92轮"},
        "topProgress": {
            "collapsed": [
                {"label": "本会话 15.3M", "rightText": "$11.66", "ratio": 0.72, "tone": "session"},
                {"label": "今日 $10.99/$12", "rightText": "92%", "overflowBadge": "超 4%", "overflowRatio": 0.04, "ratio": 1.0, "tone": "day"},
                {"label": "本周 $296.6/$300", "rightText": "99%", "overflowBadge": "临界", "overflowRatio": 0.02, "ratio": 0.99, "tone": "week"},
            ],
            "cache": {"label": "缓存命中 97%", "rightText": "15.3M", "ratio": 0.97, "tone": "cache"},
            "budget": [
                {"label": "今日 $10.99/$12", "rightText": "92%", "overflowBadge": "超出 4%", "overflowRatio": 0.04, "ratio": 1.0, "tone": "day"},
                {"label": "本周 $296.6/$300", "rightText": "99%", "overflowBadge": "临界", "overflowRatio": 0.02, "ratio": 0.99, "tone": "week"},
            ],
        },
        "requestRowDetails": [],
    }
    rows: list[dict[str, object]] = []
    started = (datetime.now() - timedelta(seconds=11)).isoformat()
    for index in range(30):
        rows.append(
            {
                "text": f"#{92 - index} $0.{152 - index:03d} 19:{10 - index % 10:02d}:17 ↑241k ◎100% ↓702 ◇286 ↻241k ∑242k",
                "prefix": f"#{92 - index} $0.{152 - index:03d} ",
                "time": "19:10:17" if index else "      11s",
                "suffix": " ↑241k ◎100% ↓702 ◇286 ↻241k ∑242k",
                "running": index == 0,
                "startedAt": started,
            }
        )
    payload["requestRowDetails"] = rows
    payload["requestRows"] = [str(row["text"]) for row in rows]
    return payload


def _settle(app: QApplication, panel: object) -> None:
    app.processEvents()
    animation = getattr(panel, "_animation", None)
    if animation is not None:
        animation.setCurrentTime(QT_HUD_ANIMATION_MS)
    app.processEvents()


def _save_grab(widget: object, path: Path) -> tuple[int, int]:
    pixmap = widget.grab()
    pixmap.save(str(path))
    return pixmap.width(), pixmap.height()


def capture_qt(app: QApplication, payload: dict[str, object]) -> dict[str, object]:
    validation: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as temp_dir:
        user_store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
        user_store.save(UserConfig.defaults())
        hud_store = HudSettingsStore(Path(temp_dir) / "geometry_settings.json")
        hud_store.save(HudSettings.empty())
        with patch.object(qt_hud_module.CodexWindowLocator, "find", return_value=None):
            window = QtHudWindow(hide_until_attached=True, user_settings_store=user_store, hud_settings_store=hud_store)
        try:
            window.attach_to_rect(WindowRect(left=100, top=120, right=1100, bottom=920))
            window.top_window.update_payload(payload)
            window.request_window.update_payload(payload)
            window.top_window.show()
            window.request_window.show()
            app.processEvents()

            window.top_window.set_expanded(False)
            window.request_window.set_expanded(False)
            _settle(app, window.top_window)
            _settle(app, window.request_window)
            validation["bottom-collapsed"] = _save_grab(window.request_window, ASSETS / "qt-bottom-collapsed.png")
            validation["top-collapsed"] = _save_grab(window.top_window, ASSETS / "qt-top-collapsed.png")

            window.request_window.set_expanded(True)
            _settle(app, window.request_window)
            validation["bottom-expanded"] = _save_grab(window.request_window, ASSETS / "qt-bottom-expanded.png")

            window.top_window.set_expanded(True)
            _settle(app, window.top_window)
            validation["top-expanded"] = _save_grab(window.top_window, ASSETS / "qt-top-expanded.png")

            window.open_settings()
            dialog = window._settings_dialog
            if dialog is None:
                raise RuntimeError("settings dialog was not created")
            dialog.show()
            app.processEvents()
            for tab_index, name in enumerate(("settings", "support", "about")):
                dialog.tabs.setCurrentIndex(tab_index)
                dialog._sync_action_visibility()
                app.processEvents()
                validation[f"settings-tab-{name}"] = _save_grab(dialog, ASSETS / f"qt-settings-tab-{name}.png")
            validation["settings_tabs"] = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        finally:
            impl = getattr(window, "_impl", window)
            timer = getattr(impl, "_clock_timer", None)
            if timer is not None:
                timer.stop()
            window.close("capture")
            app.processEvents()
    return validation



def _draw_panel_base(width: int, height: int, path: Path, *, title: str, subtitle: str = "") -> tuple[int, int]:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(58, 72, 90, 160))
    painter.setBrush(QColor(16, 22, 29, 238))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, 7, 7)
    painter.setPen(QColor("#E8EEF7"))
    painter.drawText(QRect(10, 0, max(1, width - 20), height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
    if subtitle:
        painter.setPen(QColor("#8492A6"))
        painter.drawText(QRect(10, 18, max(1, width - 20), max(1, height - 22)), Qt.AlignmentFlag.AlignLeft, subtitle)
    painter.end()
    image.save(str(path))
    return image.width(), image.height()


def _draw_progress(painter: QPainter, x: int, y: int, width: int, label: str, ratio: float, color: QColor, badge: str = "") -> None:
    painter.setPen(QColor("#3B4149"))
    painter.setBrush(QColor("#262C33"))
    painter.drawRoundedRect(x, y, width, 18, 9, 9)
    fill_width = max(1, int(width * max(0.0, min(1.0, ratio))))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(x, y, fill_width, 18, 9, 9)
    if badge:
        painter.setBrush(QColor("#7F3E3A"))
        painter.setPen(QColor("#FF875A"))
        painter.drawRoundedRect(x + width - 58, y + 2, 52, 14, 7, 7)
        painter.setPen(QColor("#FFD7CA"))
        painter.drawText(QRect(x + width - 56, y + 2, 48, 14), Qt.AlignmentFlag.AlignCenter, badge)
    painter.setPen(QColor("#E8EEF7"))
    painter.drawText(QRect(x + 8, y, width - 16, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)


def _draw_renderer_top_expanded(payload: dict[str, object], path: Path) -> tuple[int, int]:
    width, height = 673, 390
    details = dict(payload.get("topDetails") or {})
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(58, 72, 90, 160))
    painter.setBrush(QColor(16, 22, 29, 238))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, 7, 7)
    painter.setPen(QColor("#E8EEF7"))
    painter.drawText(QRect(10, 6, 360, 24), Qt.AlignmentFlag.AlignVCenter, str(details.get("title") or ""))
    painter.setPen(QColor("#8492A6"))
    painter.drawText(QRect(370, 6, 240, 24), Qt.AlignmentFlag.AlignVCenter, str(details.get("session") or ""))
    painter.setPen(QColor("#273241"))
    painter.drawLine(0, 36, width, 36)
    painter.setPen(QColor("#FFB86B"))
    painter.setBrush(QColor(255, 184, 107, 32))
    painter.drawRoundedRect(9, 43, width - 18, 24, 7, 7)
    painter.drawText(QRect(18, 43, width - 36, 24), Qt.AlignmentFlag.AlignVCenter, str(details.get("warnings") or ""))
    left_x, right_x, y = 9, 344, 76
    card_w = 318
    for x, title in ((left_x, "本会话用量"), (right_x, "当前活动")):
        painter.setPen(QColor("#273241"))
        painter.setBrush(QColor(20, 27, 36, 232))
        painter.drawRoundedRect(x, y, card_w, 256, 8, 8)
        painter.setPen(QColor("#8492A6"))
        painter.drawText(QRect(x + 10, y + 8, card_w - 20, 18), Qt.AlignmentFlag.AlignVCenter, title)
    painter.setPen(QColor("#F3D27A"))
    painter.drawText(QRect(left_x + 12, y + 36, 120, 30), Qt.AlignmentFlag.AlignVCenter, str(details.get("sessionCost") or ""))
    painter.setPen(QColor("#9CCBFF"))
    painter.drawText(QRect(left_x + 150, y + 36, 120, 30), Qt.AlignmentFlag.AlignVCenter, str(details.get("sessionTokens") or ""))
    _draw_progress(painter, left_x + 12, y + 91, card_w - 24, "缓存命中 97%", 0.97, QColor("#5EA7FF"))
    _draw_progress(painter, left_x + 12, y + 121, card_w - 24, "今日 $10.99/$12", 1.0, QColor("#F3D27A"), "超出")
    _draw_progress(painter, left_x + 12, y + 151, card_w - 24, "本周 $296.6/$300", 0.99, QColor("#B5DD92"), "临界")
    painter.setPen(QColor("#DCE7F2"))
    painter.drawText(QRect(left_x + 12, y + 190, card_w - 24, 18), Qt.AlignmentFlag.AlignVCenter, "高消耗轮次")
    for index, row in enumerate(details.get("heavyRounds") or []):
        item = dict(row)
        painter.setPen(QColor("#9CCBFF" if index == 0 else "#8492A6"))
        painter.drawText(QRect(left_x + 12, y + 214 + index * 18, card_w - 24, 16), Qt.AlignmentFlag.AlignVCenter, str(item.get("title") or ""))
    painter.setPen(QColor("#DCE7F2"))
    painter.drawText(QRect(right_x + 12, y + 34, card_w - 24, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(details.get("currentTask") or ""))
    painter.setPen(QColor("#9CCBFF"))
    painter.drawText(QRect(right_x + 12, y + 82, card_w - 24, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(details.get("executing") or ""))
    painter.setPen(QColor("#F3D27A"))
    painter.drawText(QRect(right_x + 12, y + 126, 90, 20), Qt.AlignmentFlag.AlignVCenter, str(details.get("activityElapsed") or ""))
    painter.drawText(QRect(right_x + 112, y + 126, 90, 20), Qt.AlignmentFlag.AlignVCenter, str(details.get("activityGap") or ""))
    painter.drawText(QRect(right_x + 212, y + 126, 70, 20), Qt.AlignmentFlag.AlignVCenter, str(details.get("activityLast") or ""))
    painter.setPen(QColor("#8492A6"))
    painter.drawText(QRect(right_x + 12, y + 158, card_w - 24, 18), Qt.AlignmentFlag.AlignVCenter, "活动轨迹")
    for index, row in enumerate(details.get("activityTrail") or []):
        item = dict(row)
        row_y = y + 184 + index * 22
        painter.setPen(QColor("#F3D27A" if item.get("active") else "#3A485A"))
        painter.setBrush(QColor("#F3D27A" if item.get("active") else "#3A485A"))
        painter.drawEllipse(right_x + 14, row_y + 5, 7, 7)
        painter.setPen(QColor("#8492A6"))
        painter.drawText(QRect(right_x + 30, row_y, 40, 18), Qt.AlignmentFlag.AlignVCenter, str(item.get("time") or ""))
        painter.setPen(QColor("#DCE7F2"))
        painter.drawText(QRect(right_x + 76, row_y, 80, 18), Qt.AlignmentFlag.AlignVCenter, str(item.get("title") or ""))
        painter.setPen(QColor("#8492A6"))
        painter.drawText(QRect(right_x + 158, row_y, 145, 18), Qt.AlignmentFlag.AlignVCenter, str(item.get("detail") or ""))
    painter.end()
    image.save(str(path))
    return image.width(), image.height()


def _draw_renderer_request_expanded(payload: dict[str, object], path: Path) -> tuple[int, int]:
    width, height = 355, 180
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(58, 72, 90, 160))
    painter.setBrush(QColor(11, 16, 22, 235))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, 7, 7)
    painter.setPen(QColor("#718095"))
    painter.drawText(QRect(10, 6, 110, 16), Qt.AlignmentFlag.AlignVCenter, "轮次流水")
    painter.drawText(QRect(width - 90, 6, 80, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "最新在上")
    rows = list(payload.get("requestRowDetails") or [])[:6]
    for index, row in enumerate(rows):
        item = dict(row)
        y = 27 + index * 18
        if item.get("running"):
            painter.setPen(QColor(156, 203, 255, 92))
            painter.setBrush(QColor(156, 203, 255, 30))
            painter.drawRoundedRect(7, y - 1, width - 18, 17, 5, 5)
        painter.setPen(QColor("#DCE7F2" if item.get("running") else "#8D9AAD"))
        painter.drawText(QRect(11, y, width - 28, 16), Qt.AlignmentFlag.AlignVCenter, str(item.get("text") or ""))
    painter.setPen(QColor("#273241"))
    painter.drawLine(0, height - 31, width, height - 31)
    painter.setPen(QColor("#E8EEF7"))
    painter.drawText(QRect(28, height - 30, width - 38, 30), Qt.AlignmentFlag.AlignVCenter, str(payload.get("requestLine") or ""))
    painter.end()
    image.save(str(path))
    return image.width(), image.height()


def _draw_renderer_settings(tab: str, path: Path) -> tuple[int, int]:
    width, height = 780, 580
    labels = {"settings": "设置", "support": "请作者喝咖啡", "about": "版本更新"}
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0B1016"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#3A485A"))
    painter.setBrush(QColor("#141B24"))
    painter.drawRoundedRect(0, 0, width - 1, height - 1, 14, 14)
    painter.setPen(QColor("#E8EEF7"))
    painter.drawText(QRect(22, 14, 400, 30), Qt.AlignmentFlag.AlignVCenter, "codex-usage-hud vvisual")
    painter.setPen(QColor("#273241"))
    painter.drawLine(0, 58, width, 58)
    x = 20
    for key, label in labels.items():
        active = key == tab
        painter.setPen(QColor("#F3D27A" if active else "#3A485A"))
        painter.setBrush(QColor("#202833" if active else "#10161D"))
        painter.drawRoundedRect(x, 74, 150, 32, 8, 8)
        painter.setPen(QColor("#F3D27A" if active else "#DCE7F2"))
        painter.drawText(QRect(x, 74, 150, 32), Qt.AlignmentFlag.AlignCenter, label)
        x += 162
    painter.setPen(QColor("#273241"))
    painter.setBrush(QColor("#10161D"))
    painter.drawRoundedRect(20, 122, width - 40, 370, 10, 10)
    painter.setPen(QColor("#DCE7F2"))
    if tab == "settings":
        lines = ["显示方案：auto / renderer / qt / tk", "每日预算：12 USD", "每周预算：300 USD", "模型价格：gpt-5.5 / gpt-5.5-mini", "Work overlay 最大条目：3"]
    elif tab == "support":
        lines = ["请作者喝咖啡", "支持链接与二维码区域", "配置文件路径与项目链接"]
    else:
        lines = ["版本更新", "检查 GitHub Release", "安装更新按钮", "当前状态：可安装更新"]
    for index, line in enumerate(lines):
        painter.drawText(QRect(42, 148 + index * 42, width - 84, 24), Qt.AlignmentFlag.AlignVCenter, line)
    painter.setPen(QColor("#273241"))
    painter.drawLine(0, 512, width, 512)
    painter.setPen(QColor("#8492A6"))
    painter.drawText(QRect(22, 526, 420, 28), Qt.AlignmentFlag.AlignVCenter, "设置将保存到本地配置文件")
    painter.setPen(QColor("#F3D27A"))
    painter.drawText(QRect(width - 190, 526, 160, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "主操作")
    painter.end()
    image.save(str(path))
    return image.width(), image.height()


def capture_renderer(app: QApplication, payload: dict[str, object]) -> dict[str, object]:
    del app
    validation: dict[str, object] = {
        "method": "回退：Qt WebEngine 在当前环境无法生成 Renderer DOM，改用 Renderer 源码尺寸与样式语义绘制静态参考图",
        "webengine_blocker": "Qt WebEngine runJavaScript 返回空值且未创建 [data-panel]，同时 Chromium 报 GPU shared context failure",
    }
    validation["bottom-collapsed"] = _draw_panel_base(355, 32, ASSETS / "renderer-bottom-collapsed.png", title=str(payload.get("requestLine") or ""))
    validation["bottom-expanded"] = _draw_renderer_request_expanded(payload, ASSETS / "renderer-bottom-expanded.png")
    validation["top-collapsed"] = _draw_panel_base(673, 36, ASSETS / "renderer-top-collapsed.png", title=str(payload.get("topLine") or ""))
    validation["top-expanded"] = _draw_renderer_top_expanded(payload, ASSETS / "renderer-top-expanded.png")
    for tab in ("settings", "support", "about"):
        validation[f"settings-tab-{tab}"] = _draw_renderer_settings(tab, ASSETS / f"renderer-settings-tab-{tab}.png")
    return validation


def _load_pixmap(path: Path) -> QPixmap:
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise RuntimeError(f"cannot load image: {path}")
    return pixmap


def _make_side_by_side(name: str, renderer_path: Path, qt_path: Path, output_path: Path) -> tuple[int, int]:
    renderer = _load_pixmap(renderer_path)
    qt = _load_pixmap(qt_path)
    label_height = 36
    gap = 12
    width = renderer.width() + qt.width() + gap
    height = max(renderer.height(), qt.height()) + label_height
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0B1016"))
    painter = QPainter(image)
    painter.setPen(QColor("#DCE7F2"))
    painter.drawText(QRect(0, 0, renderer.width(), label_height), Qt.AlignmentFlag.AlignCenter, f"Renderer - {STATE_NAMES[name]}")
    painter.drawText(QRect(renderer.width() + gap, 0, qt.width(), label_height), Qt.AlignmentFlag.AlignCenter, f"Qt - {STATE_NAMES[name]}")
    painter.drawPixmap(0, label_height, renderer)
    painter.drawPixmap(renderer.width() + gap, label_height, qt)
    painter.end()
    image.save(str(output_path))
    return image.width(), image.height()


def _make_settings_contact_sheet() -> tuple[int, int]:
    rows = []
    for tab in ("settings", "support", "about"):
        side_path = ASSETS / f"side-by-side-settings-tab-{tab}.png"
        rows.append(_load_pixmap(side_path))
    gap = 12
    width = max(pixmap.width() for pixmap in rows)
    height = sum(pixmap.height() for pixmap in rows) + gap * (len(rows) - 1)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0B1016"))
    painter = QPainter(image)
    y = 0
    for pixmap in rows:
        painter.drawPixmap(0, y, pixmap)
        y += pixmap.height() + gap
    painter.end()
    output = ASSETS / "side-by-side-settings-all-tabs.png"
    image.save(str(output))
    return image.width(), image.height()


def create_comparisons() -> dict[str, tuple[int, int]]:
    outputs: dict[str, tuple[int, int]] = {}
    for name in ("bottom-collapsed", "bottom-expanded", "top-collapsed", "top-expanded"):
        outputs[name] = _make_side_by_side(
            name,
            ASSETS / f"renderer-{name}.png",
            ASSETS / f"qt-{name}.png",
            ASSETS / f"side-by-side-{name}.png",
        )
    for tab in ("settings", "support", "about"):
        key = f"settings-tab-{tab}"
        outputs[key] = _make_side_by_side(
            key,
            ASSETS / f"renderer-settings-tab-{tab}.png",
            ASSETS / f"qt-settings-tab-{tab}.png",
            ASSETS / f"side-by-side-settings-tab-{tab}.png",
        )
    outputs["settings-all-tabs"] = _make_settings_contact_sheet()
    return outputs


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(["qt-renderer-visual-capture"])
    app.setQuitOnLastWindowClosed(False)
    payload = _payload()
    qt_validation = capture_qt(app, payload)
    renderer_validation = capture_renderer(app, payload)
    comparison_validation = create_comparisons()
    validation = {
        "payload": "same in-process payload for Qt and Renderer captures",
        "renderer_reference_method": renderer_validation.pop("method"),
        "qt": qt_validation,
        "renderer": renderer_validation,
        "side_by_side": comparison_validation,
    }
    (ASSETS / "visual-validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote Renderer/Qt screenshots to {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
