"""Active-session tracking and session-path resolution helpers."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .base import BasePlatform


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
    if not sessions_root.exists():
        return None

    try:
        matches = sorted(
            sessions_root.rglob(f"*{session_id}*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return matches[0] if matches else None


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
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Begin best-effort platform tracking for the selected Codex conversation."""
        if not self.enabled or self._proc is not None or self._thread is not None:
            return

        self._stop_event.clear()
        if self.platform.supports_active_title_polling():
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
            return

        command = self.platform.build_active_title_command(self.poll_ms)
        if not command:
            self.enabled = False
            self.latest_source = "activity"
            return

        kwargs: dict[str, Any] = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self._proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except OSError:
            self.enabled = False
            self.latest_source = "activity"
            return

        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop the background title tracker process if one is running."""
        self._stop_event.set()
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
            thread.join(timeout=0.5)

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

    def path_from_thread_id(self, thread_id: str) -> Path | None:
        """Resolve a known thread id to a local rollout JSONL path."""
        if not thread_id:
            return None
        path = find_session_file(thread_id, self.sessions_root)
        if path is not None:
            return path
        if not self.state_db.exists():
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
            return None

        if row is None:
            return None
        return self._normalize_rollout_path(str(row[0] or ""))

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        for line in proc.stdout:
            if self._stop_event.is_set():
                return
            title = line.rstrip("\r\n")
            if not title.startswith("TITLE\t"):
                continue
            title = title[6:].strip()
            if not title:
                continue
            with self._lock:
                self.latest_title = title
                if title != self._mapped_title:
                    self.latest_path = None

    def _poll_loop(self) -> None:
        """Poll an in-process platform title probe without spawning PowerShell."""
        delay = max(250, self.poll_ms) / 1000.0
        last = ""
        while not self._stop_event.is_set():
            try:
                title = self.platform.get_active_conversation_title() or ""
            except Exception:
                title = ""
            title = title.strip()
            if title and title != last:
                last = title
                with self._lock:
                    self.latest_title = title
                    if title != self._mapped_title:
                        self.latest_path = None
            self._stop_event.wait(delay)

    def _normalize_rollout_path(self, path_text: str) -> Path | None:
        text = path_text[4:] if path_text.startswith("\\\\?\\") else path_text
        path = Path(text)
        return path if path.exists() else None


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

    def resolve(self) -> tuple[Path | None, str]:
        """Resolve the current session path plus a short source label."""
        if self.session_file is not None:
            self.selection_source = "pinned:file"
            return self.session_file, self.selection_source

        if self.session_id:
            self.selection_source = "pinned:id"
            return find_session_file(self.session_id, self.sessions_root), self.selection_source

        active_path = (
            self.active_session_tracker.current_path()
            if self.active_session_tracker is not None
            else None
        )
        if active_path is not None:
            self.auto_session_file = active_path
            self.selection_source = self.active_session_tracker.latest_source
            return active_path, self.selection_source

        if self.active_session_tracker is not None and self.active_session_tracker.enabled:
            self.selection_source = self.active_session_tracker.latest_source

        latest = self.platform.detect_active_session(self.sessions_root)
        if latest is None:
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
        if (
            latest_mtime > current_mtime
            and current_idle >= self.auto_switch_idle_seconds
        ):
            self.auto_session_file = latest
            self.selection_source = "activity"
        return self.auto_session_file, self.selection_source
