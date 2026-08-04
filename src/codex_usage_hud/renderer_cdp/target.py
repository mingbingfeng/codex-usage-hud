"""Subscribed CDP page-target discovery."""

from __future__ import annotations

import threading
from typing import Any, Callable

from ..platforms.cdp_probe import list_targets, pick_page_target


class RendererTargetDiscovery:
    """Keep the selected CDP page target as subscribed runtime state."""

    def __init__(
        self,
        *,
        port: int,
        timeout_seconds: float,
        list_targets_fn: Callable[[int, float], list[dict[str, Any]]] = list_targets,
        pick_target_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] = pick_page_target,
    ) -> None:
        self.port = int(port)
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._lock = threading.Lock()
        self._target: dict[str, Any] | None = None
        self._disconnected_reason = ""
        self._list_targets = list_targets_fn
        self._pick_target = pick_target_fn

    def target(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            disconnected_reason = self._disconnected_reason
            cached = dict(self._target or {}) if self._target is not None else None
        if disconnected_reason:
            raise RuntimeError(
                f"CDP target discovery disconnected: {disconnected_reason}"
            )
        if cached is not None and not force:
            return cached
        selected = dict(
            self._pick_target(self._list_targets(self.port, self.timeout_seconds))
        )
        with self._lock:
            self._target = dict(selected)
        return selected

    def mark_disconnected(self, reason: object = "") -> None:
        text = str(reason or "").strip() or "CDP websocket closed"
        with self._lock:
            self._disconnected_reason = text

    def clear(self) -> None:
        with self._lock:
            self._target = None


__all__ = ["RendererTargetDiscovery"]
