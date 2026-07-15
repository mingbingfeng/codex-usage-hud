"""Cross-platform helpers for locating Codex data."""

from .active_session import (
    ActiveSessionTracker,
    SessionPathResolver,
    find_session_file,
    is_new_session_source,
    is_pending_session_source,
)
from .base import get_current_platform
from .session_switch import (
    CdpSessionSwitchBackend,
    SessionSwitchController,
    SessionSwitchRequest,
    SessionSwitchResult,
    WindowsSearchSessionSwitchBackend,
)
from .windows_tracker import (
    CodexWindowTracker,
    DockSnapshot,
    PhysicalRect,
    window_tracker_log_path,
)

__all__ = [
    "ActiveSessionTracker",
    "CodexWindowTracker",
    "CdpSessionSwitchBackend",
    "DockSnapshot",
    "PhysicalRect",
    "SessionSwitchController",
    "SessionSwitchRequest",
    "SessionSwitchResult",
    "SessionPathResolver",
    "WindowsSearchSessionSwitchBackend",
    "find_session_file",
    "get_current_platform",
    "is_new_session_source",
    "is_pending_session_source",
    "window_tracker_log_path",
]
