"""HUD user interface helpers."""

from __future__ import annotations

from typing import Any

__all__ = ["QtHudWindow", "TokenHudWindow"]


def __getattr__(name: str) -> Any:
    if name == "QtHudWindow":
        from .qt_hud import QtHudWindow

        return QtHudWindow
    if name == "TokenHudWindow":
        from .tk_hud import TokenHudWindow

        return TokenHudWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
