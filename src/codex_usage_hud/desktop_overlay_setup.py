"""Optional desktop-overlay dependency and helper process setup."""

from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from .config import DEFAULT_WORK_OVERLAY_MAX_ITEMS
from .instance_lock import process_exists as _process_exists
from .runtime_paths import hud_runtime_dir


ACTIVE_WORK_ITEM_LIMIT = DEFAULT_WORK_OVERLAY_MAX_ITEMS
FORCE_DESKTOP_OVERLAY_MISSING_ENV = "CODEX_USAGE_HUD_FORCE_DESKTOP_OVERLAY_MISSING"
WORK_OVERLAY_STALE_SECONDS = 20.0
WORK_OVERLAY_ALPHA = 0.88
WORK_OVERLAY_HOVER_ALPHA = 0.22
WORK_OVERLAY_HEADER_TITLE_LIMIT = 28
WORK_OVERLAY_TOP_OFFSET = 56
WORK_OVERLAY_MARGIN = 16
WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT = 160
DESKTOP_OVERLAY_PACKAGE = "PySide6"
DESKTOP_OVERLAY_PIP_SPEC = "PySide6>=6.8"

_DESKTOP_OVERLAY_INSTALL_PROCESS: subprocess.Popen[Any] | None = None
_FORCE_DESKTOP_OVERLAY_MISSING = False


def _eprint(message: str) -> None:
    try:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
    except Exception:
        pass

def _work_overlay_helper_qt() -> Any:
    from .ui.work_overlay_qt import run_work_overlay_helper_qt

    return run_work_overlay_helper_qt


def _work_overlay_max_items_for_screen_height(screen_height: int) -> int:
    available_height = max(
        1,
        int(screen_height) - WORK_OVERLAY_TOP_OFFSET - (WORK_OVERLAY_MARGIN * 2),
    )
    return max(1, available_height // WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT)


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _force_desktop_overlay_missing() -> bool:
    return bool(_FORCE_DESKTOP_OVERLAY_MISSING)


def _set_force_desktop_overlay_missing(enabled: bool) -> None:
    global _FORCE_DESKTOP_OVERLAY_MISSING
    _FORCE_DESKTOP_OVERLAY_MISSING = bool(enabled)


def _init_force_desktop_overlay_missing_from_env() -> None:
    _set_force_desktop_overlay_missing(
        _env_flag_enabled(FORCE_DESKTOP_OVERLAY_MISSING_ENV)
    )


def _pyside6_runtime_available(*, honor_force: bool = True) -> bool:
    if honor_force and _force_desktop_overlay_missing():
        return False
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec(DESKTOP_OVERLAY_PACKAGE) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _pyside6_version() -> str:
    try:
        return importlib_metadata.version(DESKTOP_OVERLAY_PACKAGE)
    except importlib_metadata.PackageNotFoundError:
        return ""
    except Exception:
        return ""


def _desktop_overlay_install_running() -> bool:
    global _DESKTOP_OVERLAY_INSTALL_PROCESS
    process = _DESKTOP_OVERLAY_INSTALL_PROCESS
    if process is None:
        return False
    if process.poll() is None:
        return True
    _DESKTOP_OVERLAY_INSTALL_PROCESS = None
    # Install finished: if the package is now present, stop simulating missing.
    if _pyside6_runtime_available(honor_force=False):
        _set_force_desktop_overlay_missing(False)
    return False


def _desktop_overlay_can_install() -> bool:
    return bool(sys.executable) and not bool(getattr(sys, "frozen", False))


def _desktop_overlay_dependency_status() -> dict[str, object]:
    real_installed = _pyside6_runtime_available(honor_force=False)
    # Forced-missing simulation reports not installed until install/enable clears it.
    installed = real_installed and not _force_desktop_overlay_missing()
    version = _pyside6_version() if real_installed else ""
    can_install = _desktop_overlay_can_install()
    installing = _desktop_overlay_install_running()
    requires_restart = bool(getattr(sys, "frozen", False)) and not installed
    install_command = f"{Path(sys.executable).name} -m pip install \"{DESKTOP_OVERLAY_PIP_SPEC}\""
    return {
        "package": DESKTOP_OVERLAY_PACKAGE,
        "installed": installed,
        "version": version if installed else "",
        "canInstall": can_install,
        "installing": installing,
        "requiresRestart": requires_restart,
        "canEnableNow": not requires_restart,
        "installCommand": install_command,
        "forcedMissing": _force_desktop_overlay_missing(),
        "realInstalled": real_installed,
    }


def _start_desktop_overlay_install() -> bool:
    global _DESKTOP_OVERLAY_INSTALL_PROCESS
    if _desktop_overlay_install_running():
        return True
    if not _desktop_overlay_can_install():
        return False
    # Simulated missing + real package present: clear force immediately so the next
    # status poll shows installed without a redundant pip install.
    if _force_desktop_overlay_missing() and _pyside6_runtime_available(honor_force=False):
        _set_force_desktop_overlay_missing(False)
        return True
    try:
        _DESKTOP_OVERLAY_INSTALL_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", DESKTOP_OVERLAY_PIP_SPEC],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _DESKTOP_OVERLAY_INSTALL_PROCESS = None
        return False
    return True


def _work_overlay_owner_pid(path: Path) -> int | None:
    match = re.match(r"work-overlay-(\d+)-\d+\.json$", path.name, re.IGNORECASE)
    if not match:
        return None
    try:
        pid = int(match.group(1))
    except ValueError:
        return None
    return pid if pid > 0 else None


def cleanup_stale_work_overlay_files() -> None:
    runtime = hud_runtime_dir()
    try:
        files = list(runtime.glob("work-overlay-*.json"))
    except OSError:
        return
    now = time.time()
    for path in files:
        owner_pid = _work_overlay_owner_pid(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        stale = (now - float(mtime)) > WORK_OVERLAY_STALE_SECONDS
        owner_alive = _process_exists(owner_pid) if owner_pid is not None else False
        if owner_pid is not None and owner_alive and not stale:
            continue
        if owner_pid is None and not stale:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def run_work_overlay_helper(state_file: str | Path) -> int:
    state_arg = str(state_file or "").strip()
    if not state_arg:
        return 1
    try:
        return _work_overlay_helper_qt()(
            state_arg,
            process_exists=_process_exists,
            owner_pid_from_path=_work_overlay_owner_pid,
            item_limit=ACTIVE_WORK_ITEM_LIMIT,
            stale_seconds=WORK_OVERLAY_STALE_SECONDS,
            overlay_alpha=WORK_OVERLAY_ALPHA,
            hover_alpha=WORK_OVERLAY_HOVER_ALPHA,
            header_title_limit=WORK_OVERLAY_HEADER_TITLE_LIMIT,
        )
    except RuntimeError as exc:
        _eprint(str(exc))
        return 1
