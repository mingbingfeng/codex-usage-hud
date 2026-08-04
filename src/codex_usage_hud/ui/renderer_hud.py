"""Explicit compatibility exports for the Renderer HUD public API."""

from ..renderer_client import (
    RendererHudClient,
    remove_renderer_hud_from_pages,
    wait_for_renderer,
)
from ..renderer_payload_builder import (
    payload_from_snapshot,
    session_switch_payload_from_snapshot,
)

__all__ = [
    "RendererHudClient",
    "payload_from_snapshot",
    "remove_renderer_hud_from_pages",
    "session_switch_payload_from_snapshot",
    "wait_for_renderer",
]
