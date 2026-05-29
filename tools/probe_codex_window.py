"""Print live Codex window, title bar, input box, and HUD dock coordinates.

Run from the repository root:

    python tools/probe_codex_window.py
    python tools/probe_codex_window.py --watch
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.windows_tracker import (  # noqa: E402
    CodexWindowTracker,
    DockSnapshot,
    PhysicalRect,
    window_tracker_log_path,
)

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"


def _enable_ansi_on_windows() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


def _rect_line(label: str, rect: PhysicalRect | None) -> str:
    if rect is None:
        return f"{label:<8} {RED}n/a{RESET}"
    x, y, width, height = rect.as_xywh()
    return (
        f"{label:<8} "
        f"x={CYAN}{x:>5}{RESET}  "
        f"y={CYAN}{y:>5}{RESET}  "
        f"width={GREEN}{width:>5}{RESET}  "
        f"height={GREEN}{height:>4}{RESET}  "
        f"rect=({rect.left}, {rect.top}, {rect.right}, {rect.bottom})"
    )


def _print_snapshot(snapshot: DockSnapshot) -> None:
    print(f"{BOLD}Codex 实时窗口定位探测{RESET}")
    status_color = GREEN if snapshot.visible else YELLOW
    if snapshot.status in {"not_found", "unsupported"}:
        status_color = RED
    print(
        f"状态     {status_color}{snapshot.status}{RESET}  "
        f"HWND={snapshot.hwnd or 'n/a'}  source={snapshot.source}"
    )
    print(f"日志     {window_tracker_log_path()}")
    if snapshot.reason:
        print(f"原因     {YELLOW}{snapshot.reason}{RESET}")
    print(_rect_line("主窗口", snapshot.window_rect))
    print(_rect_line("标题栏", snapshot.title_bar))
    print(_rect_line("输入框", snapshot.input_box))
    if snapshot.dock is None:
        print(f"HUD吸附  {RED}n/a{RESET}")
    else:
        x, y, width = snapshot.dock
        print(
            f"HUD吸附  X={CYAN}{x}{RESET}  "
            f"Y={CYAN}{y}{RESET}  "
            f"可用宽度={GREEN}{width}{RESET}"
        )
    print(f"{DIM}{time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Codex title bar and chat input physical coordinates.",
    )
    parser.add_argument(
        "--target",
        choices=["input", "title"],
        default="input",
        help="Which HUD dock coordinate to print. Default: input.",
    )
    parser.add_argument(
        "--hud-height",
        type=int,
        default=32,
        help="HUD height used when calculating the Y coordinate. Default: 32.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep printing live coordinates.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.25,
        help="Watch interval in seconds. Default: 0.25.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _enable_ansi_on_windows()
    args = build_parser().parse_args(argv)
    tracker = CodexWindowTracker(blocking_uia=True)
    tracker.set_dpi_aware()

    while True:
        snapshot = tracker.get_dock_snapshot(
            target=args.target,
            hud_height=args.hud_height,
        )
        _print_snapshot(snapshot)
        if not args.watch:
            return 0 if snapshot.visible else 2
        print()
        time.sleep(max(0.05, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
