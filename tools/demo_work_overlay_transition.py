"""Slow-motion demo for the work overlay completed-bubble transition."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_usage_hud.ui import work_overlay_qt  # noqa: E402


DEFAULT_SCALE = 10.0


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
            "这是完成态气泡动画 demo。当前状态由脚本写入 overlay state，"
            "用于观察方形变圆、直线升顶、已有圆形让位和逆向恢复。"
        ),
        "workdir": workdir,
        "workdirName": Path(workdir).name,
        "tokensText": "2.4k",
        "costText": "$0.03",
        "cacheHitText": "91%",
        "current": item_id == "demo-main",
    }


def _write_state(
    state_path: Path,
    *,
    items: list[Mapping[str, object]],
    close: bool = False,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ownerPid": os.getpid(),
        "updatedAt": time.time(),
        "close": bool(close),
        "itemLimit": 6,
        "items": list(items),
    }
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(state_path)


def _apply_transition_scale(scale: float) -> None:
    scale = max(0.1, float(scale))
    work_overlay_qt.WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS = int(
        work_overlay_qt.WORK_OVERLAY_COMPLETED_BADGE_ANIMATION_MS * scale
    )
    work_overlay_qt.WORK_OVERLAY_TRANSITION_SHRINK_MS = int(
        work_overlay_qt.WORK_OVERLAY_TRANSITION_SHRINK_MS * scale
    )
    work_overlay_qt.WORK_OVERLAY_TRANSITION_PAUSE_MS = int(
        work_overlay_qt.WORK_OVERLAY_TRANSITION_PAUSE_MS * scale
    )
    work_overlay_qt.WORK_OVERLAY_TRANSITION_MOVE_MS = int(
        work_overlay_qt.WORK_OVERLAY_TRANSITION_MOVE_MS * scale
    )
    work_overlay_qt.WORK_OVERLAY_TRANSITION_SHIFT_MS = int(
        work_overlay_qt.WORK_OVERLAY_TRANSITION_SHIFT_MS * scale
    )


def _timeline(
    state_path: Path,
    *,
    scale: float,
    loop: bool,
) -> None:
    workdir = str(ROOT)
    main_running = _item(
        "demo-main",
        status="tool",
        title="修复完成态气泡过渡",
        status_text="正在运行测试",
        elapsed_text="已处理 2m18s",
        workdir=workdir,
        updated_offset=-40,
    )
    second_running = _item(
        "demo-second",
        status="tool",
        title="生成发布说明草案",
        status_text="正在整理变更摘要",
        elapsed_text="已处理 48s",
        workdir=workdir,
        updated_offset=-20,
    )
    third_running = _item(
        "demo-third",
        status="thinking",
        title="排查窗口置顶策略",
        status_text="正在思考",
        elapsed_text="已处理 14s",
        workdir=workdir,
        updated_offset=-10,
    )

    base_pause = max(1.0, scale * 0.55)
    transition_pause = max(2.0, scale * 0.95)

    while True:
        print("demo: 1/5 initial running cards")
        _write_state(state_path, items=[main_running, second_running, third_running])
        time.sleep(base_pause)

        print("demo: 2/5 first card -> completed circle, then straight up")
        main_completed = dict(main_running)
        main_completed.update(
            {
                "status": "recent",
                "statusText": "已完成",
                "elapsedText": "已处理 3m02s",
                "updatedAt": _now_iso(-30),
            }
        )
        _write_state(state_path, items=[main_completed, second_running, third_running])
        time.sleep(transition_pause)

        print("demo: 3/5 second card -> completed circle, existing circle moves left")
        second_completed = dict(second_running)
        second_completed.update(
            {
                "status": "recent",
                "statusText": "已完成",
                "elapsedText": "已处理 1m26s",
                "updatedAt": _now_iso(-10),
            }
        )
        _write_state(state_path, items=[main_completed, second_completed, third_running])
        time.sleep(transition_pause)

        print("demo: 4/5 second circle -> running card, remaining circle moves right")
        second_restored = dict(second_completed)
        second_restored.update(
            {
                "status": "tool",
                "statusText": "继续运行",
                "elapsedText": "已处理 1m42s",
                "updatedAt": _now_iso(0),
            }
        )
        _write_state(state_path, items=[main_completed, second_restored, third_running])
        time.sleep(transition_pause)

        print("demo: 5/5 stable final state")
        _write_state(state_path, items=[main_completed, second_restored, third_running])
        time.sleep(base_pause)

        if not loop:
            print("demo: finished; closing overlay")
            _write_state(state_path, items=[main_completed, second_restored, third_running], close=True)
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_SCALE,
        help="Transition time multiplier. Default: 10.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the sequence once and close the overlay.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(tempfile.gettempdir()) / "codex-work-overlay-transition-demo.json",
        help="State file used by the demo overlay.",
    )
    args = parser.parse_args(argv)

    _apply_transition_scale(args.scale)
    state_path = args.state_file.expanduser().resolve()
    _write_state(state_path, items=[])

    thread = threading.Thread(
        target=_timeline,
        kwargs={
            "state_path": state_path,
            "scale": args.scale,
            "loop": not args.once,
        },
        daemon=True,
    )
    thread.start()

    print(f"demo: state file {state_path}")
    print(f"demo: transition scale {args.scale:g}x")
    print("demo: press Ctrl+C in this terminal to stop")
    try:
        return work_overlay_qt.run_work_overlay_helper_qt(
            state_path,
            process_exists=lambda pid: pid == os.getpid(),
            owner_pid_from_path=lambda _path: os.getpid(),
            item_limit=6,
            stale_seconds=3600.0,
            overlay_alpha=0.94,
            hover_alpha=0.62,
            header_title_limit=28,
        )
    except KeyboardInterrupt:
        _write_state(state_path, items=[], close=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
