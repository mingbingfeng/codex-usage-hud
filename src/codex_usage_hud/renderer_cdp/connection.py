"""Low-level local websocket operations used by the Renderer CDP binding."""

from __future__ import annotations

import json
import socket
from urllib.parse import urlparse

from ..platforms.cdp_probe import (
    _receive_text_message,
    _send_text_frame,
    _websocket_handshake,
)


def connect_websocket(websocket_url: str, timeout_seconds: float) -> socket.socket:
    """Open and handshake one local CDP websocket."""
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws":
        raise RuntimeError("Only local ws:// CDP endpoints are supported")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    sock = socket.create_connection((host, port), timeout=timeout_seconds)
    sock.settimeout(0.25)
    _websocket_handshake(sock, host, port, path)
    return sock


def send_command(
    sock: socket.socket,
    command_id: int,
    method: str,
    params: dict[str, object],
) -> None:
    """Send one JSON CDP command over an established websocket."""
    _send_text_frame(
        sock,
        json.dumps(
            {"id": command_id, "method": method, "params": params},
            separators=(",", ":"),
        ),
    )


def receive_message(sock: socket.socket) -> str:
    """Receive one decoded websocket text message."""
    return _receive_text_message(sock)


__all__ = ["connect_websocket", "receive_message", "send_command"]
