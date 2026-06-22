"""Active-session tracking and session-path resolution helpers."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import BasePlatform

_LOGGER = logging.getLogger("codex_usage_hud.active_session")
_LOGGER.addHandler(logging.NullHandler())
_THREAD_PATH_NEGATIVE_CACHE_SECONDS = 2.0


def _session_search_roots(sessions_root: Path) -> tuple[Path, ...]:
    roots = [sessions_root]
    if sessions_root.name == "sessions":
        roots.append(sessions_root.parent / "archived_sessions")
    ordered: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        ordered.append(root)
    return tuple(ordered)


def compact_text(value: Any, limit: int = 28) -> str:
    """Collapse whitespace and trim text for short status labels."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def find_session_file(session_id: str, sessions_root: Path) -> Path | None:
    """Find a session JSONL by id substring or direct path."""
    direct = Path(session_id)
    if direct.exists():
        return direct
    search_roots = [root for root in _session_search_roots(sessions_root) if root.exists()]
    if not search_roots:
        return None

    matches: list[Path] = []
    for root in search_roots:
        try:
            matches.extend(root.rglob(f"*{session_id}*.jsonl"))
        except OSError:
            continue
    try:
        matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        matches = sorted(
            [item for item in matches if item.exists()],
            reverse=True,
        )
    return matches[0] if matches else None


class RealtimeSessionWatcher:
    """Stream active Codex conversation titles with event-first fallback paths."""

    def __init__(
        self,
        platform: BasePlatform,
        poll_ms: int,
        on_title: Callable[[str, str, float], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.platform = platform
        self.poll_ms = max(250, int(poll_ms))
        self.on_title = on_title
        self.stop_event = stop_event or threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self.primary_thread: threading.Thread | None = None
        self._threads: list[threading.Thread] = []
        self._last_emitted = ""
        self._last_event_at = 0.0
        self._emit_lock = threading.Lock()

    def start(self) -> bool:
        """Start the best available realtime watcher."""
        if self.primary_thread is not None or self.process is not None:
            return True

        self.stop_event.clear()
        if self.platform.supports_active_title_events():
            self.primary_thread = self._start_thread(
                self._event_loop,
                "codex-hud-active-events",
            )
            if self.platform.supports_active_title_polling():
                self._start_thread(
                    self._poll_backstop_loop,
                    "codex-hud-active-poll-backstop",
                )
            return True

        if self.platform.supports_active_title_polling():
            self.primary_thread = self._start_thread(
                self._poll_loop,
                "codex-hud-active-poll",
            )
            return True

        return self._start_command_sidecar()

    def close(self) -> None:
        """Stop any background watcher threads or sidecar process."""
        self.stop_event.set()
        proc = self.process
        self.process = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

        current = threading.current_thread()
        for thread in list(self._threads):
            if thread is not current and thread.is_alive():
                thread.join(timeout=2.0)
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        self.primary_thread = None if not self._threads else self.primary_thread

    def _start_thread(
        self,
        target: Callable[[], None],
        name: str,
    ) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()
        return thread

    def _start_command_sidecar(self) -> bool:
        command = self.platform.build_active_title_command(self.poll_ms)
        if not command:
            return False

        kwargs: dict[str, Any] = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except OSError:
            self.process = None
            return False

        self.primary_thread = self._start_thread(
            self._read_loop,
            "codex-hud-active-sidecar",
        )
        return True

    def _event_loop(self) -> None:
        try:
            started = self.platform.watch_active_conversation_title(
                self.stop_event,
                lambda title: self._emit(title, "event"),
            )
        except Exception as exc:
            _LOGGER.debug("active_session_event_stream_failed error=%s", exc)
            started = False
        if started or self.stop_event.is_set():
            return
        if self.platform.supports_active_title_polling():
            self._poll_loop()

    def _poll_backstop_loop(self) -> None:
        backstop_seconds = max(1.0, (self.poll_ms / 1000.0) * 2.0)
        if self.stop_event.wait(backstop_seconds):
            return
        delay = max(0.5, self.poll_ms / 1000.0)
        last_probe_at = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - max(self._last_event_at, last_probe_at) >= backstop_seconds:
                self._poll_once("poll-backstop")
                last_probe_at = time.monotonic()
            self.stop_event.wait(delay)

    def _poll_loop(self) -> None:
        delay = max(250, self.poll_ms) / 1000.0
        while not self.stop_event.is_set():
            self._poll_once("poll")
            self.stop_event.wait(delay)

    def _poll_once(self, source: str) -> None:
        try:
            title = self.platform.get_active_conversation_title() or ""
        except Exception as exc:
            _LOGGER.debug("active_session_poll_failed error=%s", exc)
            title = ""
        self._emit(title, source)

    def _read_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return

        for line in proc.stdout:
            if self.stop_event.is_set():
                return
            title = line.rstrip("\r\n")
            if not title.startswith("TITLE\t"):
                continue
            self._emit(title[6:], "sidecar")

    def _emit(self, title: str, source: str) -> None:
        text = title.strip()
        if not text:
            return
        detected_at = time.monotonic()
        with self._emit_lock:
            if text == self._last_emitted:
                return
            self._last_emitted = text
            if source == "event":
                self._last_event_at = detected_at
        self.on_title(text, source, detected_at)


class ActiveSessionTracker:
    """Track the currently selected Codex conversation and map it to a JSONL path."""

    def __init__(
        self,
        platform: BasePlatform,
        state_db: Path,
        sessions_root: Path,
        session_index_path: Path,
        poll_ms: int,
        enabled: bool,
    ) -> None:
        self.platform = platform
        self.state_db = state_db
        self.sessions_root = sessions_root
        self.session_index_path = session_index_path
        self.poll_ms = max(250, int(poll_ms))
        self.enabled = bool(enabled)
        self.latest_title = ""
        self.latest_path: Path | None = None
        self.latest_source = "ui-waiting" if self.enabled else "activity"
        self._mapped_title = ""
        self._title_cache_key: tuple[str, str] | None = None
        self._title_cache_value = ""
        self._thread_path_cache: dict[str, tuple[Path | None, float]] = {}
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._watcher: RealtimeSessionWatcher | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.latest_response_ms = 0.0
        self.latest_event_source = ""

    def start(self) -> None:
        """Begin best-effort platform tracking for the selected Codex conversation."""
        if not self.enabled or self._watcher is not None:
            return

        self._stop_event.clear()
        watcher = RealtimeSessionWatcher(
            self.platform,
            self.poll_ms,
            self._handle_title_candidate,
            stop_event=self._stop_event,
        )
        if not watcher.start():
            self.enabled = False
            self.latest_source = "activity"
            return
        self._watcher = watcher
        self._proc = watcher.process
        self._thread = watcher.primary_thread

    def close(self) -> None:
        """Stop the background title tracker process if one is running."""
        self._stop_event.set()
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.close()
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def wait_for_title(self, timeout_ms: int = 1200) -> None:
        """Give the background title probe a short chance to produce an initial title."""
        if not self.enabled:
            return
        deadline = time.time() + (max(0, timeout_ms) / 1000.0)
        while time.time() < deadline:
            with self._lock:
                if self.latest_title:
                    return
            if self._proc is not None and self._proc.poll() is not None:
                return
            time.sleep(0.05)

    def current_path(self) -> Path | None:
        """Resolve the latest observed Codex conversation title to a JSONL path."""
        if not self.enabled:
            return None
        ref = self.platform.get_active_conversation_ref()
        if ref is not None:
            session_id, title = ref
            path = self.path_from_thread_id(session_id) if session_id else None
            if path is None and title:
                path = self.path_for_title(title)
            if path is not None or title or session_id:
                with self._lock:
                    self.latest_title = title or self.latest_title
                    self.latest_path = path
                    self._mapped_title = title or self._mapped_title
                    self.latest_source = (
                        f"cdp:{compact_text(title or session_id)}"
                        if path is not None
                        else "cdp-unmatched"
                    )
                    self.latest_event_source = "cdp"
                return path
        with self._lock:
            title = self.latest_title
        if not title:
            self.latest_source = "ui-waiting"
            return None
        if title == self._mapped_title and self.latest_path is not None:
            return self.latest_path

        path = self.path_for_title(title)
        self._mapped_title = title
        self.latest_path = path
        self.latest_source = (
            f"ui:{compact_text(title)}" if path is not None else "ui-unmatched"
        )
        return path

    def path_for_title(self, title: str) -> Path | None:
        """Map a visible Codex conversation title to a session JSONL path."""
        path = self.path_from_session_index(title)
        if path is not None:
            return path
        if not self.state_db.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                row = con.execute(
                    """
                    select rollout_path
                    from threads
                    where title = ?
                    order by archived asc, updated_at_ms desc, updated_at desc
                    limit 1
                    """,
                    (title,),
                ).fetchone()
                if row is None:
                    row = con.execute(
                        """
                        select rollout_path, title
                        from threads
                        where length(title) >= 3
                          and ? like title || '%'
                        order by archived asc, length(title) desc, updated_at_ms desc, updated_at desc
                        limit 1
                        """,
                        (title,),
                    ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return None

        if row is None:
            return None
        path_text = str(row["rollout_path"] or "")
        return self._normalize_rollout_path(path_text)

    def title_for_session(
        self,
        session_path: Path | None,
        session_id: str = "",
    ) -> str:
        """Resolve the display title for the session currently shown in the HUD."""
        session_key = (session_id or "", self._path_key(session_path))
        with self._lock:
            latest_title = self.latest_title.strip()
            latest_path = self.latest_path
        if latest_title and self._same_path(latest_path, session_path):
            self._title_cache_key = session_key
            self._title_cache_value = latest_title
            return latest_title
        if self._title_cache_key == session_key:
            return self._title_cache_value

        title = ""
        if session_id:
            title = self.title_from_session_index_id(session_id)
            if not title:
                title = self.title_from_thread_id(session_id)
        if not title and session_path is not None:
            title = self.title_from_rollout_path(session_path)
        self._title_cache_key = session_key
        self._title_cache_value = title
        return title

    def path_from_session_index(self, title: str) -> Path | None:
        """Map a session title via ``session_index.jsonl`` before hitting SQLite."""
        if not self.session_index_path.exists():
            return None

        best_id = ""
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = str(item.get("thread_name") or "")
                    if name == title or title.startswith(name) or name.startswith(title):
                        best_id = str(item.get("id") or best_id)
        except OSError:
            return None

        if not best_id:
            return None
        return find_session_file(best_id, self.sessions_root)

    def title_from_session_index_id(self, session_id: str) -> str:
        """Look up the visible thread title for a known session id."""
        if not session_id or not self.session_index_path.exists():
            return ""
        best_title = ""
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(item.get("id") or "") != session_id:
                        continue
                    title = str(item.get("thread_name") or "").strip()
                    if title:
                        best_title = title
        except OSError:
            return ""
        return best_title

    def path_from_thread_id(self, thread_id: str) -> Path | None:
        """Resolve a known thread id to a local rollout JSONL path."""
        if not thread_id:
            return None
        now = time.monotonic()
        cached = self._thread_path_cache.get(thread_id)
        if cached is not None:
            cached_path, cached_at = cached
            if cached_path is not None and cached_path.exists():
                return cached_path
            if (
                cached_path is None
                and now - cached_at <= _THREAD_PATH_NEGATIVE_CACHE_SECONDS
            ):
                return None
        path = find_session_file(thread_id, self.sessions_root)
        if path is not None:
            self._thread_path_cache[thread_id] = (path, now)
            return path
        if not self.state_db.exists():
            self._thread_path_cache[thread_id] = (None, now)
            return None
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "select rollout_path from threads where id = ? limit 1",
                    (thread_id,),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            self._thread_path_cache[thread_id] = (None, now)
            return None

        if row is None:
            self._thread_path_cache[thread_id] = (None, now)
            return None
        path = self._normalize_rollout_path(str(row[0] or ""))
        self._thread_path_cache[thread_id] = (path, now)
        return path

    def title_from_thread_id(self, thread_id: str) -> str:
        """Resolve a known thread id to its visible title via the state database."""
        if not thread_id or not self.state_db.exists():
            return ""
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    """
                    select title
                    from threads
                    where id = ?
                    order by archived asc, updated_at_ms desc, updated_at desc
                    limit 1
                    """,
                    (thread_id,),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        return str(row[0] or "").strip()

    def title_from_rollout_path(self, session_path: Path) -> str:
        """Resolve a visible title using the rollout path stored in the state database."""
        if not self.state_db.exists():
            return ""
        raw_path = str(session_path)
        prefixed_path = raw_path if raw_path.startswith("\\\\?\\") else f"\\\\?\\{raw_path}"
        try:
            con = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    """
                    select title
                    from threads
                    where rollout_path in (?, ?)
                    order by archived asc, updated_at_ms desc, updated_at desc
                    limit 1
                    """,
                    (raw_path, prefixed_path),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        return str(row[0] or "").strip()

    def _handle_title_candidate(
        self,
        title: str,
        source: str = "event",
        detected_at: float | None = None,
    ) -> None:
        title = title.strip()
        if not title:
            return
        detected = detected_at if detected_at is not None else time.monotonic()
        previous_title = ""
        previous_path: Path | None = None
        with self._lock:
            previous_title = self.latest_title
            previous_path = self.latest_path

        path = self.path_for_title(title)
        if path is None and self.platform.supports_active_title_polling():
            fallback_title = (self.platform.get_active_conversation_title() or "").strip()
            if fallback_title and fallback_title != title:
                fallback_path = self.path_for_title(fallback_title)
                if fallback_path is not None:
                    title = fallback_title
                    path = fallback_path
                    source = f"{source}+poll-confirm"
        response_ms = (time.monotonic() - detected) * 1000.0
        with self._lock:
            self.latest_title = title
            self.latest_path = path
            self._mapped_title = title
            self.latest_source = (
                f"ui:{compact_text(title)}" if path is not None else "ui-unmatched"
            )
            self.latest_response_ms = response_ms
            self.latest_event_source = source

        if title != previous_title or path != previous_path:
            _LOGGER.info(
                "ACTIVE_SESSION_SWITCH source=%s matched=%s response_ms=%.1f title=%r",
                source,
                path is not None,
                response_ms,
                compact_text(title, 80),
            )

    def _normalize_rollout_path(self, path_text: str) -> Path | None:
        text = path_text[4:] if path_text.startswith("\\\\?\\") else path_text
        path = Path(text)
        return path if path.exists() else None

    @staticmethod
    def _path_key(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    @classmethod
    def _same_path(cls, left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return False
        return cls._path_key(left) == cls._path_key(right)


class SessionPathResolver:
    """Choose which session JSONL the HUD should follow."""

    def __init__(
        self,
        platform: BasePlatform,
        sessions_root: Path,
        session_id: str | None = None,
        session_file: Path | None = None,
        active_session_tracker: ActiveSessionTracker | None = None,
        auto_switch_idle_seconds: float = 30.0,
    ) -> None:
        self.platform = platform
        self.sessions_root = sessions_root
        self.session_id = session_id
        self.session_file = session_file
        self.active_session_tracker = active_session_tracker
        self.auto_switch_idle_seconds = max(0.0, float(auto_switch_idle_seconds))
        self.auto_session_file: Path | None = None
        self.selection_source = (
            "pinned" if (self.session_id or self.session_file) else "activity"
        )

    @staticmethod
    def _has_unresolved_tracker_selection(source: str) -> bool:
        return source.startswith("ui-unmatched") or source.startswith("cdp-unmatched")

    def resolve(self) -> tuple[Path | None, str]:
        """Resolve the current session path plus a short source label."""
        if self.session_file is not None:
            self.selection_source = "pinned:file"
            return self.session_file, self.selection_source

        if self.session_id:
            self.selection_source = "pinned:id"
            return find_session_file(self.session_id, self.sessions_root), self.selection_source

        tracker_source = ""
        active_path = None
        if self.active_session_tracker is not None:
            active_path = self.active_session_tracker.current_path()
            tracker_source = self.active_session_tracker.latest_source
        if active_path is not None:
            self.auto_session_file = active_path
            self.selection_source = tracker_source
            return active_path, self.selection_source

        if self.active_session_tracker is not None and self.active_session_tracker.enabled:
            self.selection_source = tracker_source

        latest = self.platform.detect_active_session(self.sessions_root)
        if latest is None:
            if self.auto_session_file is not None and not self.auto_session_file.exists():
                self.auto_session_file = None
                self.selection_source = tracker_source or "activity"
            return self.auto_session_file, self.selection_source

        if self.auto_session_file is None or not self.auto_session_file.exists():
            self.auto_session_file = latest
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        if latest == self.auto_session_file:
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        try:
            current_mtime = self.auto_session_file.stat().st_mtime
            latest_mtime = latest.stat().st_mtime
        except OSError:
            self.auto_session_file = latest
            self.selection_source = "activity"
            return self.auto_session_file, self.selection_source

        current_idle = time.time() - current_mtime
        tracker_requested_switch = self._has_unresolved_tracker_selection(tracker_source)
        if (
            latest_mtime > current_mtime
            and (
                tracker_requested_switch
                or current_idle >= self.auto_switch_idle_seconds
            )
        ):
            self.auto_session_file = latest
            self.selection_source = (
                f"{tracker_source}+activity"
                if tracker_requested_switch
                else "activity"
            )
        return self.auto_session_file, self.selection_source
