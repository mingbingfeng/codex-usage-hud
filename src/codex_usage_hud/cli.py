"""Command-line interface for codex-usage-hud."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import logging
import os
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path

from .core import JsonlSessionParser, ParsedSession, SseRequestStateMachine, UsageSummary
from .daemon import (
    CodexDaemonManager,
    DEFAULT_DAEMON_POLL_MS,
    ProcessListenerError,
    configure_daemon_logging,
    hide_console_window,
)
from .platforms import ActiveSessionTracker, SessionPathResolver, get_current_platform
from .platforms.base import BasePlatform
from .ui import TokenHudWindow
from .ui.renderer_hud import RendererHudClient, wait_for_renderer

DEFAULT_POLL_MS = 500
DEFAULT_SQLITE_LOG = "logs_2.sqlite"
DEFAULT_STATE_DB = "state_5.sqlite"
DEFAULT_SESSION_INDEX = "session_index.jsonl"
DEFAULT_DAILY_BUDGET_USD = 100.0
DEFAULT_WEEKLY_BUDGET_USD = 400.0
DEFAULT_BUDGET_THRESHOLDS = "0.5,0.8,0.9,1.0"
DEFAULT_ACTIVE_SESSION_POLL_MS = 500
DEFAULT_AUTO_SWITCH_IDLE_SECONDS = 30.0
HUD_LOCK_FILENAME = "codex_usage_hud.pid"
HUD_MUTEX_NAME = "Local\\codex_usage_hud_single_instance"
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
DAEMON_RESTART_REQUESTED = 10
RENDERER_HUD_UNAVAILABLE = 20
RENDERER_INITIAL_TIMEOUT_SECONDS = 0.7
DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS = 2.0
_LOGGER = logging.getLogger("codex_usage_hud.cli")


class HudAlreadyRunningError(RuntimeError):
    """Raised when another HUD instance owns the local runtime lock."""


def configure_stdout() -> None:
    """Prefer UTF-8 console output when the interpreter supports it."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _eprint(message: str) -> None:
    """Print to stderr when a console stream exists."""
    try:
        stream = sys.stderr
        if stream is not None and hasattr(stream, "write"):
            print(message, file=stream)
    except Exception:
        pass


def hud_runtime_dir() -> Path:
    """Return the per-user directory for lightweight HUD runtime files."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("XDG_STATE_HOME")
            or Path.home() / ".local" / "state"
        )
    return base / "codex-usage-hud"


def hud_lock_path() -> Path:
    """Return the pid-file lock path used by the interactive HUD."""
    return hud_runtime_dir() / HUD_LOCK_FILENAME


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(
                    wintypes.HANDLE(handle),
                    ctypes.byref(exit_code),
                ):
                    return False
                return int(exit_code.value or 0) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_process(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


class HudInstanceLock:
    """Cross-process lock to keep invisible duplicate HUDs from piling up."""

    def __init__(self, path: Path | None = None, mutex_name: str | None = None) -> None:
        self.path = path or hud_lock_path()
        self.mutex_name = mutex_name or HUD_MUTEX_NAME
        self._owned = False
        self._mutex_handle: int | None = None

    def acquire(self) -> None:
        self._mutex_handle = self._acquire_native_mutex()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_pid = _read_pid(self.path)
        if existing_pid is not None and _process_exists(existing_pid):
            self._release_native_mutex()
            raise HudAlreadyRunningError(
                f"codex-usage-hud is already running as PID {existing_pid}. "
                "Run `python -m codex_usage_hud --stop` to close it."
            )
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            self._release_native_mutex()
            raise HudAlreadyRunningError(
                "codex-usage-hud lock already exists. "
                "Run `python -m codex_usage_hud --stop` to clear it."
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self._owned = True

    def _acquire_native_mutex(self) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.CreateMutexW(None, True, self.mutex_name)
            error = ctypes.get_last_error()
            if not handle:
                raise OSError(error, "CreateMutexW failed")
            if error == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
                raise HudAlreadyRunningError(
                    "codex-usage-hud is already running "
                    f"(mutex {self.mutex_name!r} exists). "
                    "Run `python -m codex_usage_hud --stop` to close it."
                )
            return int(handle)
        except HudAlreadyRunningError:
            raise
        except Exception:
            return None

    def _release_native_mutex(self) -> None:
        if not self._mutex_handle or not sys.platform.startswith("win"):
            self._mutex_handle = None
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = wintypes.HANDLE(self._mutex_handle)
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        self._mutex_handle = None

    def release(self) -> None:
        if not self._owned:
            self._release_native_mutex()
            return
        if _read_pid(self.path) == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._owned = False
        self._release_native_mutex()

    def __enter__(self) -> "HudInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.release()


def stop_running_hud(path: Path | None = None) -> str:
    """Stop the HUD instance recorded in the local pid-file lock."""
    lock_path = path or hud_lock_path()
    pid = _read_pid(lock_path)
    if pid is None:
        try:
            lock_path.unlink()
        except OSError:
            pass
        return "No running codex-usage-hud instance was recorded."
    if not _process_exists(pid):
        try:
            lock_path.unlink()
        except OSError:
            pass
        return f"Removed stale codex-usage-hud lock for PID {pid}."
    if not _terminate_process(pid):
        return f"Unable to stop codex-usage-hud PID {pid}."
    for _ in range(20):
        if not _process_exists(pid):
            try:
                lock_path.unlink()
            except OSError:
                pass
            return f"Stopped codex-usage-hud PID {pid}."
        import time

        time.sleep(0.1)
    return f"Sent stop signal to codex-usage-hud PID {pid}."


def _format_money(value: float | None) -> str:
    return f"${float(value or 0.0):,.6f}"


def _format_tokens(value: int | None) -> str:
    amount = int(value or 0)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}k"
    return f"{amount}"


def _current_task_tokens(snapshot: ParsedSession) -> int:
    request = snapshot.request
    if request.total_tokens:
        return int(request.total_tokens)
    if request.input_tokens is not None or request.output_tokens is not None:
        return int(request.input_tokens or 0) + int(request.output_tokens or 0)
    if snapshot.estimate.total_tokens:
        return int(snapshot.estimate.total_tokens)
    return int(snapshot.confirmed.last_total or 0)


def _current_session_cost(snapshot: ParsedSession) -> float:
    confirmed_cost = float(snapshot.confirmed.cumulative_cost_usd or 0.0)
    pending_cost = 0.0
    if snapshot.request.status == "running" and snapshot.request.cost_usd is not None:
        pending_cost = float(snapshot.request.cost_usd)
    return round(confirmed_cost + pending_cost, 6)


def _merge_usage(target: UsageSummary, addition: UsageSummary) -> None:
    target.tokens += addition.tokens
    target.input_tokens += addition.input_tokens
    target.cached_tokens += addition.cached_tokens
    target.output_tokens += addition.output_tokens
    target.reasoning_tokens += addition.reasoning_tokens
    target.cost_usd = round(target.cost_usd + addition.cost_usd, 6)


def usage_before_today_in_week(
    week_total: UsageSummary,
    today_total: UsageSummary,
    day_start: datetime,
    week_start: datetime,
) -> UsageSummary:
    """Return the part of the weekly window that happened before this daily window."""
    if day_start <= week_start:
        return UsageSummary()
    return UsageSummary(
        tokens=max(0, week_total.tokens - today_total.tokens),
        input_tokens=max(0, week_total.input_tokens - today_total.input_tokens),
        cached_tokens=max(0, week_total.cached_tokens - today_total.cached_tokens),
        output_tokens=max(0, week_total.output_tokens - today_total.output_tokens),
        reasoning_tokens=max(0, week_total.reasoning_tokens - today_total.reasoning_tokens),
        cost_usd=round(max(0.0, week_total.cost_usd - today_total.cost_usd), 6),
    )


@dataclass
class _UsageCacheEntry:
    mtime: float | None
    file_size: int | None
    day_start: datetime
    week_start: datetime
    summary_day: UsageSummary
    summary_week: UsageSummary


class UsageSummaryCache:
    """Cache rolling day/week usage summaries per JSONL session file."""

    def __init__(self, parser: JsonlSessionParser) -> None:
        self._parser = parser
        self._entries: dict[Path, _UsageCacheEntry] = {}

    def summarize(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> tuple[UsageSummary, UsageSummary]:
        day_total = UsageSummary()
        week_total = UsageSummary()

        if not sessions_root.exists():
            return day_total, week_total

        seen_paths: set[Path] = set()
        for path in sessions_root.rglob("*.jsonl"):
            seen_paths.add(path)
            summary_day, summary_week = self._summaries_for_file(
                path, day_start, week_start
            )
            _merge_usage(day_total, summary_day)
            _merge_usage(week_total, summary_week)

        for cached_path in list(self._entries):
            if cached_path not in seen_paths:
                del self._entries[cached_path]

        return day_total, week_total

    def _summaries_for_file(
        self,
        path: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> tuple[UsageSummary, UsageSummary]:
        try:
            stat = path.stat()
        except OSError:
            return UsageSummary(), UsageSummary()

        entry = self._entries.get(path)
        if (
            entry is not None
            and entry.mtime == stat.st_mtime
            and entry.file_size == stat.st_size
            and entry.day_start == day_start
            and entry.week_start == week_start
        ):
            return entry.summary_day, entry.summary_week

        try:
            records = self._parser.load_records_lenient(path)
        except OSError:
            return UsageSummary(), UsageSummary()

        events = self._parser.usage_events(records)
        summary_day = self._parser.summarize_usage_events(events, day_start)
        summary_week = self._parser.summarize_usage_events(events, week_start)
        self._entries[path] = _UsageCacheEntry(
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            day_start=day_start,
            week_start=week_start,
            summary_day=summary_day,
            summary_week=summary_week,
        )
        return summary_day, summary_week


@dataclass
class RuntimeContext:
    platform: BasePlatform
    sessions_root: Path
    session_file: Path | None
    sqlite_log_path: Path | None
    state_db_path: Path
    session_index_path: Path
    poll_ms: int
    daily_budget_usd: float
    weekly_budget_usd: float
    budget_thresholds: list[float]
    parser: JsonlSessionParser
    sse_tracker: SseRequestStateMachine | None
    active_session_tracker: ActiveSessionTracker | None
    session_resolver: SessionPathResolver
    usage_cache: UsageSummaryCache

    def close(self) -> None:
        """Release any background helpers created for the runtime context."""
        if self.active_session_tracker is not None:
            self.active_session_tracker.close()


def _candidate_data_dirs(platform: BasePlatform | None = None) -> list[Path]:
    platform = platform or get_current_platform()
    candidates = [platform.get_codex_data_dir(), Path.home() / ".codex"]
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def _discover_path(
    platform: BasePlatform,
    explicit_path: str | None,
    relative_name: str,
) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()

    for root in _candidate_data_dirs(platform):
        candidate = root / relative_name
        if candidate.exists():
            return candidate
    return _candidate_data_dirs(platform)[0] / relative_name


def _discover_sessions_root(platform: BasePlatform, explicit_root: str | None) -> Path:
    if explicit_root:
        return Path(explicit_root).expanduser()

    for root in _candidate_data_dirs(platform):
        candidate = root / "sessions"
        if candidate.exists():
            return candidate
    return _candidate_data_dirs(platform)[0] / "sessions"


def parse_thresholds(value: str) -> list[float]:
    """Parse comma-separated budget warning thresholds."""
    thresholds: list[float] = []
    for part in value.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            amount = float(text)
        except ValueError:
            continue
        if amount > 1:
            amount /= 100.0
        if amount > 0:
            thresholds.append(amount)
    return sorted(set(thresholds)) or [0.5, 0.8, 0.9, 1.0]


def current_budget_windows() -> tuple[datetime, datetime]:
    """Return original HUD budget windows: daily 10:00 and weekly Thursday 10:00."""
    now = datetime.now().astimezone()
    day_start = datetime.combine(
        now.date(), datetime_time(hour=10), tzinfo=now.tzinfo
    )
    if now < day_start:
        day_start -= timedelta(days=1)

    days_since_thursday = (now.weekday() - 3) % 7
    week_date = now.date() - timedelta(days=days_since_thursday)
    week_start = datetime.combine(
        week_date, datetime_time(hour=10), tzinfo=now.tzinfo
    )
    if now < week_start:
        week_start -= timedelta(days=7)
    return day_start, week_start


def budget_warnings(
    day_cost: float,
    week_cost: float,
    daily_limit_usd: float,
    weekly_limit_usd: float,
    thresholds: Sequence[float],
) -> list[str]:
    """Build original-style budget threshold warnings."""
    messages: list[str] = []
    for label, used, limit in [
        ("日", day_cost, daily_limit_usd),
        ("周", week_cost, weekly_limit_usd),
    ]:
        if limit <= 0:
            continue
        ratio = used / limit
        crossed = [item for item in thresholds if ratio >= item]
        if not crossed:
            continue
        percent = int(crossed[-1] * 100)
        messages.append(
            f"{label}额度已用 {used:.2f}/{limit:.0f} USD ({ratio:.0%})，超过 {percent}% 阈值"
        )
    return messages


def build_runtime_context(args: argparse.Namespace) -> RuntimeContext:
    platform = get_current_platform()
    parser = JsonlSessionParser(estimate_enabled=True)
    sessions_root = _discover_sessions_root(platform, args.sessions_root)
    sqlite_log_path = _discover_path(platform, args.sse_db, DEFAULT_SQLITE_LOG)
    state_db_path = _discover_path(platform, args.state_db, DEFAULT_STATE_DB)
    session_index_path = _discover_path(platform, None, DEFAULT_SESSION_INDEX)
    active_session_tracker = ActiveSessionTracker(
        platform=platform,
        state_db=state_db_path,
        sessions_root=sessions_root,
        session_index_path=session_index_path,
        poll_ms=args.active_session_poll_ms,
        enabled=(
            not args.no_follow_active_session
            and not args.session_id
            and not args.session_file
        ),
    )
    active_session_tracker.start()
    session_resolver = SessionPathResolver(
        platform=platform,
        sessions_root=sessions_root,
        session_id=args.session_id,
        session_file=Path(args.session_file).expanduser() if args.session_file else None,
        active_session_tracker=active_session_tracker,
        auto_switch_idle_seconds=args.auto_switch_idle_seconds,
    )
    sse_tracker = (
        None
        if args.no_sse
        else SseRequestStateMachine(db_path=sqlite_log_path)
    )
    return RuntimeContext(
        platform=platform,
        sessions_root=sessions_root,
        session_file=Path(args.session_file).expanduser() if args.session_file else None,
        sqlite_log_path=sqlite_log_path,
        state_db_path=state_db_path,
        session_index_path=session_index_path,
        poll_ms=max(100, int(args.poll_ms)),
        daily_budget_usd=max(0.0, float(args.daily_budget_usd)),
        weekly_budget_usd=max(0.0, float(args.weekly_budget_usd)),
        budget_thresholds=parse_thresholds(args.budget_thresholds),
        parser=parser,
        sse_tracker=sse_tracker,
        active_session_tracker=active_session_tracker,
        session_resolver=session_resolver,
        usage_cache=UsageSummaryCache(parser),
    )


def build_snapshot(context: RuntimeContext) -> ParsedSession:
    session_path, selection_source = context.session_resolver.resolve()

    if session_path is None:
        if context.session_resolver.session_id:
            snapshot = ParsedSession(
                status="missing",
                error=(
                    f"Session id not found under {context.sessions_root}: "
                    f"{context.session_resolver.session_id}"
                ),
            )
        elif context.session_resolver.session_file is not None:
            snapshot = ParsedSession(
                status="missing",
                error=f"Session file not found: {context.session_resolver.session_file}",
            )
        elif context.sessions_root.exists():
            snapshot = ParsedSession(
                status="waiting",
                error=f"No local Codex session JSONL found under {context.sessions_root}",
            )
        else:
            snapshot = ParsedSession(
                status="missing",
                error=f"Sessions directory not found: {context.sessions_root}",
            )
    else:
        snapshot = context.parser.parse_file(
            session_path,
            sse_tracker=context.sse_tracker,
        )
    snapshot.selection_source = selection_source
    if context.active_session_tracker is not None and session_path is not None:
        snapshot.session_title = context.active_session_tracker.title_for_session(
            session_path,
            snapshot.session_id,
        )

    day_start, week_start = current_budget_windows()
    today_total, week_total = context.usage_cache.summarize(
        context.sessions_root,
        day_start,
        week_start,
    )
    snapshot.today_tokens = today_total.tokens
    snapshot.today_cost_usd = today_total.cost_usd
    snapshot.week_tokens = week_total.tokens
    snapshot.week_cost_usd = week_total.cost_usd
    prior_week_total = usage_before_today_in_week(
        week_total,
        today_total,
        day_start,
        week_start,
    )
    snapshot.week_before_today_tokens = prior_week_total.tokens
    snapshot.week_before_today_cost_usd = prior_week_total.cost_usd
    snapshot.daily_limit_usd = context.daily_budget_usd
    snapshot.weekly_limit_usd = context.weekly_budget_usd
    snapshot.day_start = day_start
    snapshot.week_start = week_start
    snapshot.budget_warnings = budget_warnings(
        today_total.cost_usd,
        week_total.cost_usd,
        context.daily_budget_usd,
        context.weekly_budget_usd,
        context.budget_thresholds,
    )
    snapshot.budget_error = "" if context.sessions_root.exists() else snapshot.error
    return snapshot


def snapshot_to_text(snapshot: ParsedSession, compact: bool = False) -> str:
    """Render a ParsedSession as CLI-friendly text."""
    model_name = snapshot.request.model or "n/a"
    task_tokens = _current_task_tokens(snapshot)
    session_cost = _current_session_cost(snapshot)

    if compact:
        return (
            f"session={snapshot.session_id} status={snapshot.status} source={snapshot.selection_source} model={model_name} "
            f"task_tokens={_format_tokens(task_tokens)} session_cost={_format_money(session_cost)} "
            f"today={_format_tokens(snapshot.today_tokens)}/{_format_money(snapshot.today_cost_usd)} "
            f"week={_format_tokens(snapshot.week_tokens)}/{_format_money(snapshot.week_cost_usd)}"
        )

    lines = [
        f"Session: {snapshot.session_id}",
        f"Status: {snapshot.status}",
        f"Source: {snapshot.selection_source}",
        f"Model: {model_name}",
        f"Current Task: {_format_tokens(task_tokens)} tokens",
        f"Current Session Cost: {_format_money(session_cost)}",
        (
            "Today: "
            f"{_format_tokens(snapshot.today_tokens)} tokens | "
            f"{_format_money(snapshot.today_cost_usd)} / "
            f"{_format_money(snapshot.daily_limit_usd)}"
        ),
        (
            "This Week: "
            f"{_format_tokens(snapshot.week_tokens)} tokens | "
            f"{_format_money(snapshot.week_cost_usd)} / "
            f"{_format_money(snapshot.weekly_limit_usd)}"
        ),
        (
            "This Week Breakdown: "
            f"before today reset {_format_money(snapshot.week_before_today_cost_usd)} + "
            f"today {_format_money(snapshot.today_cost_usd)}"
        ),
        f"Activity: {snapshot.activity.kind} | {snapshot.activity.detail or 'n/a'}",
        f"Path: {snapshot.session_path or 'n/a'}",
    ]
    if snapshot.budget_warnings:
        lines.append("Budget Warnings: " + " | ".join(snapshot.budget_warnings))
    if snapshot.error:
        lines.append(f"Error: {snapshot.error}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI parser."""
    parser = argparse.ArgumentParser(
        prog="codex-hud",
        description="Local-first Codex usage HUD from local JSONL and SQLite logs.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print the current local snapshot and exit without opening the HUD.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the currently running HUD instance recorded by the local pid lock.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=(
            "Run as a hidden Windows daemon: wait for Codex, show the HUD, "
            "and exit when Codex closes."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Use compact output mode for CLI snapshots and the Tkinter HUD.",
    )
    parser.set_defaults(renderer_hud=True)
    parser.add_argument(
        "--renderer-hud",
        dest="renderer_hud",
        action="store_true",
        help=(
            "Prefer the renderer-injected HUD when Codex exposes a local CDP "
            "target, falling back to the Tk HUD otherwise. Enabled by default."
        ),
    )
    parser.add_argument(
        "--tk-hud",
        "--no-renderer-hud",
        dest="renderer_hud",
        action="store_false",
        help="Force the legacy Tk HUD and skip renderer injection.",
    )
    parser.add_argument(
        "--session-file",
        help="Optional exact session JSONL file to monitor.",
    )
    parser.add_argument(
        "--session-id",
        help="Optional session id to pin instead of following the active Codex conversation.",
    )
    parser.add_argument(
        "--sessions-root",
        help="Optional override for the root directory containing Codex session JSONL files.",
    )
    parser.add_argument(
        "--sse-db",
        help="Optional override for the Codex SQLite OTel log database path.",
    )
    parser.add_argument(
        "--state-db",
        help="Optional override for the Codex state SQLite path used for active-session mapping.",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_MS,
        help=f"HUD refresh interval in milliseconds. Default: {DEFAULT_POLL_MS}.",
    )
    parser.add_argument(
        "--active-session-poll-ms",
        type=int,
        default=DEFAULT_ACTIVE_SESSION_POLL_MS,
        help=(
            "Polling interval for tracking the currently selected Codex conversation. "
            f"Default: {DEFAULT_ACTIVE_SESSION_POLL_MS}."
        ),
    )
    parser.add_argument(
        "--daemon-poll-ms",
        type=int,
        default=DEFAULT_DAEMON_POLL_MS,
        help=(
            "Windows daemon process polling interval in milliseconds. "
            f"Default: {DEFAULT_DAEMON_POLL_MS}; values above 500 are clamped."
        ),
    )
    parser.add_argument(
        "--auto-switch-idle-seconds",
        type=float,
        default=DEFAULT_AUTO_SWITCH_IDLE_SECONDS,
        help=(
            "When no conversation is selected explicitly, only switch to a newer "
            "mtime-based session after the current session has been idle this many "
            f"seconds. Default: {DEFAULT_AUTO_SWITCH_IDLE_SECONDS:g}."
        ),
    )
    parser.add_argument(
        "--no-follow-active-session",
        action="store_true",
        help="Disable best-effort tracking of the currently selected Codex conversation.",
    )
    parser.add_argument(
        "--no-sse",
        action="store_true",
        help="Disable SQLite SSE tracking and use JSONL-only fallback parsing.",
    )
    parser.add_argument(
        "--daily-budget-usd",
        type=float,
        default=DEFAULT_DAILY_BUDGET_USD,
        help=f"Daily reminder budget in USD. Default: {DEFAULT_DAILY_BUDGET_USD:g}.",
    )
    parser.add_argument(
        "--weekly-budget-usd",
        type=float,
        default=DEFAULT_WEEKLY_BUDGET_USD,
        help=f"Weekly reminder budget in USD. Default: {DEFAULT_WEEKLY_BUDGET_USD:g}.",
    )
    parser.add_argument(
        "--budget-thresholds",
        default=DEFAULT_BUDGET_THRESHOLDS,
        help=(
            "Comma-separated budget warning thresholds. "
            f"Default: {DEFAULT_BUDGET_THRESHOLDS}."
        ),
    )
    return parser


def run_once_snapshot(args: argparse.Namespace) -> int:
    """Print one local usage snapshot and exit."""
    context = build_runtime_context(args)
    try:
        if context.active_session_tracker is not None:
            context.active_session_tracker.wait_for_title()
        print(snapshot_to_text(build_snapshot(context), compact=args.compact))
        return 0
    finally:
        context.close()


def run_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
) -> int:
    """Run one HUD session, preferring renderer injection with Tk fallback."""
    if getattr(args, "renderer_hud", False):
        renderer_exit = run_renderer_hud_session(
            args,
            lock_already_held=lock_already_held,
            daemon_manager=daemon_manager,
        )
        if renderer_exit != RENDERER_HUD_UNAVAILABLE:
            return renderer_exit
        _LOGGER.info("renderer_hud_unavailable falling_back=tk")
    return run_tk_hud_session(
        args,
        lock_already_held=lock_already_held,
        hide_until_attached=hide_until_attached,
        daemon_manager=daemon_manager,
    )


def run_renderer_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
) -> int:
    """Run the in-renderer HUD over CDP, or report that it is unavailable."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            client = RendererHudClient()

            def snapshot_or_error() -> ParsedSession:
                try:
                    return build_snapshot(context)
                except Exception as exc:
                    return ParsedSession(status="error", error=str(exc))

            try:
                initial_timeout = (
                    DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS
                    if daemon_manager is not None
                    else RENDERER_INITIAL_TIMEOUT_SECONDS
                )
                if not wait_for_renderer(
                    client,
                    snapshot_or_error,
                    timeout_seconds=initial_timeout,
                ):
                    _LOGGER.info(
                        "renderer_hud_initial_connect_failed status=%s error=%s",
                        client.last_status,
                        client.last_error,
                    )
                    return RENDERER_HUD_UNAVAILABLE

                failures = 0
                while True:
                    started = time.monotonic()
                    if daemon_manager is not None:
                        try:
                            if not daemon_manager.codex_is_running():
                                _LOGGER.info("daemon_codex_exited")
                                return DAEMON_RESTART_REQUESTED
                        except ProcessListenerError as exc:
                            _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                            return RENDERER_HUD_UNAVAILABLE
                    if client.update(snapshot_or_error()):
                        failures = 0
                    else:
                        failures += 1
                        _LOGGER.info(
                            "renderer_hud_update_failed failures=%s status=%s error=%s",
                            failures,
                            client.last_status,
                            client.last_error,
                        )
                        if failures >= 6:
                            return RENDERER_HUD_UNAVAILABLE
                    elapsed = time.monotonic() - started
                    delay = max(0.1, (context.poll_ms / 1000.0) - elapsed)
                    time.sleep(delay)
            except KeyboardInterrupt:
                return 130
            finally:
                client.close()
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def run_tk_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
) -> int:
    """Run one Tk HUD session with optional daemon process supervision."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            try:
                try:
                    window = TokenHudWindow(
                        compact=args.compact,
                        hide_until_attached=hide_until_attached,
                        tombstone_follow_ms=(
                            100 if daemon_manager is not None else 500
                        ),
                    )
                except Exception as exc:
                    _eprint(f"codex-usage-hud: unable to open Tkinter HUD: {exc}")
                    return 1

                def refresh() -> None:
                    if window.should_refresh_snapshot():
                        try:
                            snapshot = build_snapshot(context)
                        except Exception as exc:
                            snapshot = ParsedSession(status="error", error=str(exc))
                        window.update_display(snapshot)
                    try:
                        window.root.after(window.refresh_delay_ms(context.poll_ms), refresh)
                    except Exception:
                        return

                def daemon_watchdog() -> None:
                    if daemon_manager is None:
                        return
                    try:
                        if not daemon_manager.codex_is_running():
                            _LOGGER.info("daemon_codex_exited")
                            window.close("daemon_codex_exited")
                            return
                    except ProcessListenerError as exc:
                        _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                        return
                    try:
                        window.root.after(daemon_manager.poll_ms, daemon_watchdog)
                    except Exception:
                        return

                refresh()
                if daemon_manager is not None:
                    window.root.after(daemon_manager.poll_ms, daemon_watchdog)
                window.run()
                if daemon_manager is not None and window.exit_reason == "daemon_codex_exited":
                    return DAEMON_RESTART_REQUESTED
                return 0
            finally:
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def run_daemon(args: argparse.Namespace) -> int:
    """Run the hidden Windows daemon manager, falling back when unsupported."""
    configure_daemon_logging()
    hide_console_window()
    manager = CodexDaemonManager(poll_ms=args.daemon_poll_ms)
    try:
        with HudInstanceLock():
            while True:
                try:
                    manager.wait_for_codex()
                except KeyboardInterrupt:
                    return 130
                except ProcessListenerError as exc:
                    _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                    return run_hud_session(
                        args,
                        lock_already_held=True,
                        hide_until_attached=False,
                    )
                exit_code = run_hud_session(
                    args,
                    lock_already_held=True,
                    hide_until_attached=True,
                    daemon_manager=manager,
                )
                if exit_code == DAEMON_RESTART_REQUESTED:
                    _LOGGER.info("daemon_restarting_wait_for_codex")
                    continue
                return exit_code
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entry point."""
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.stop:
        print(stop_running_hud())
        return 0
    if args.daemon and args.once:
        parser.error("--daemon cannot be combined with --once")

    if args.once:
        return run_once_snapshot(args)

    if args.daemon:
        return run_daemon(args)

    return run_hud_session(args)


if __name__ == "__main__":
    raise SystemExit(main())
