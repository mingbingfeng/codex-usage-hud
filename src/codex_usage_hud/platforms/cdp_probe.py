"""Read-only Chrome DevTools Protocol probe for the Codex renderer DOM.

The probe intentionally uses only the Python standard library.  It talks to a
local Codex remote-debugging port when one is already available and otherwise
returns ``None`` quickly so callers can fall back to native window tracking.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import socket
import struct
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_CDP_PORT = 9229
DEFAULT_CDP_CACHE_SECONDS = 0.20
DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS = 2.0
DEFAULT_CDP_TIMEOUT_SECONDS = 0.45
CDP_PORT_ENV = "CODEX_USAGE_HUD_CDP_PORT"
CDP_DOM_ENV = "CODEX_USAGE_HUD_CDP_DOM"


@dataclass(frozen=True)
class CdpRect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)


@dataclass(frozen=True)
class CdpDomSnapshot:
    session_id: str
    title: str
    device_pixel_ratio: float
    header_rect: CdpRect | None = None
    title_rect: CdpRect | None = None
    composer_rect: CdpRect | None = None
    app_error: str = ""


DOM_PROBE_SCRIPT = r"""
(() => {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (node) => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const rectFor = (node) => {
    if (!visible(node)) return null;
    const rect = node.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    };
  };
  const rowHref = (row) => row?.getAttribute?.("href") || row?.querySelector?.("a")?.getAttribute?.("href") || "";
  const locationThreadId = () => {
    const source = `${location.pathname}${location.search}${location.hash}`;
    const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
      || source.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[/?#]|$)/)
      || source.match(/\/([A-Za-z0-9_-]{24,})(?:[/?#]|$)/);
    return match ? decodeURIComponent(match[1]) : "";
  };
  const threadRows = Array.from(document.querySelectorAll("[data-app-action-sidebar-thread-id]"));
  const refFromRow = (row) => {
    const href = rowHref(row);
    const idMatch = href.match(/(?:session|conversation|thread)[=/:-]([A-Za-z0-9_.-]+)/i) || href.match(/([A-Za-z0-9_-]{8,})$/);
    const sessionId = row.getAttribute("data-app-action-sidebar-thread-id")
      || (idMatch && idMatch[1])
      || row.getAttribute("data-session-id")
      || row.getAttribute("data-testid")
      || "";
    const titleNode = row.querySelector("[data-thread-title], .truncate.select-none, .truncate.text-base");
    const rawTitle = titleNode?.textContent || (titleNode ? "" : (row.textContent || ""));
    const title = normalize(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
    return { sessionId, title };
  };
  const currentRow = (row) => {
    if (row.getAttribute("data-app-action-sidebar-thread-active") === "true") return true;
    if (row.getAttribute("aria-current") === "page" || row.getAttribute("aria-current") === "true") return true;
    const href = rowHref(row);
    if (href) {
      try {
        const url = new URL(href, location.href);
        if (url.href === location.href || url.pathname === location.pathname) return true;
      } catch (_) {
        if (location.href.includes(href)) return true;
      }
    }
    const ref = refFromRow(row);
    return !!ref.sessionId && location.href.includes(ref.sessionId);
  };
  const activeRow = threadRows.find(currentRow) || null;
  const activeRef = activeRow ? refFromRow(activeRow) : { sessionId: locationThreadId(), title: "" };
  const compact = (value, limit = 220) => {
    const text = normalize(value);
    return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 3))}...`;
  };
  const appErrorText = () => {
    const selectors = [
      "[role='alert']",
      "[role='status']",
      "[aria-live]",
      "[data-testid*='toast' i]",
      "[data-testid*='notification' i]",
      "[data-testid*='error' i]",
      "[class*='toast' i]",
      "[class*='notification' i]",
      "[class*='error' i]",
      "[class*='danger' i]",
      "[class*='destructive' i]",
      "[class*='alert' i]",
    ].join(", ");
    const errorPattern = /(exceeded retry limit|too many requests|\b429\b|\brate limit(?:ed)?\b|\b5\d\d\b|network error|request failed|failed to fetch|server error|internal server error|service unavailable|temporarily unavailable|something went wrong|unexpected error|error occurred)/i;
    const candidates = Array.from(document.querySelectorAll(selectors))
      .filter((node) => visible(node) && !node.closest("#codex-usage-hud-root"))
      .map((node, index) => {
        const text = normalize([
          node.getAttribute("aria-label"),
          node.getAttribute("title"),
          node.textContent,
        ].filter(Boolean).join(" "));
        if (!text || !errorPattern.test(text)) return null;
        const rect = node.getBoundingClientRect();
        const className = String(node.className || "");
        const role = String(node.getAttribute("role") || "");
        let score = 1000 - index;
        if (role === "alert") score += 160;
        if (role === "status") score += 90;
        if (node.hasAttribute("aria-live")) score += 90;
        if (/toast|notification|snackbar|alert/i.test(className)) score += 80;
        if (/error|danger|destructive/i.test(className)) score += 70;
        if (rect.top <= 160 || rect.bottom >= innerHeight - 220) score += 30;
        if (text.length <= 180) score += 20;
        if (/\b429\b|too many requests|exceeded retry limit|rate limit/i.test(text)) score += 180;
        return { text: compact(text), score };
      })
      .filter(Boolean)
      .sort((left, right) => right.score - left.score);
    return candidates[0]?.text || "";
  };

  const scoreHeader = (node) => {
    const rect = node.getBoundingClientRect();
    const text = normalize(node.textContent);
    let score = 0;
    if (node.tagName === "HEADER") score += 80;
    if (node.classList.contains("app-header-tint")) score += 35;
    if (node.matches?.("[data-testid='app-shell-header-context-menu-surface']")) score += 140;
    if (node.closest?.("header.app-header-tint")) score += 120;
    if (String(node.className || "").includes("top-toolbar-sm")) score += 110;
    if (rect.top > 20) score += 95;
    if (rect.top <= 4) score -= 140;
    if (rect.width > 300) score += 25;
    if (rect.height >= 34 && rect.height <= 80) score += 30;
    if (/File\s*Edit\s*View\s*Window\s*Help/i.test(text) || text === "FileEditViewWindowHelp") score -= 300;
    if (text && !/File\s*Edit\s*View\s*Window\s*Help/i.test(text)) score += Math.min(20, text.length);
    return score;
  };
  const shellSurface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
  const shellHeader = shellSurface?.closest?.("header.app-header-tint, header, .app-header-tint");
  const header = visible(shellHeader) ? shellHeader : Array.from(document.querySelectorAll([
    "header.app-header-tint",
    "[data-testid='app-shell-header']",
    "[data-testid='app-shell-header-context-menu-surface']",
    ".app-header-tint",
  ].join(", "))).filter(visible)
    .map((node, index) => ({ node, index, score: scoreHeader(node) }))
    .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;
  const headerRect = rectFor(header);
  const titleText = normalize(activeRef.title);
  const titleScope = header || document;
  const titleCandidates = Array.from(titleScope.querySelectorAll([
    ".app-header-tint [data-thread-title]",
    ".app-header-tint h1",
    ".app-header-tint h2",
    "[data-testid='app-shell-header'] [data-thread-title]",
    "[data-testid='app-shell-header'] h1",
    "[data-testid='app-shell-header'] h2",
    "[data-testid='app-shell-header-context-menu-surface'] [data-thread-title]",
    "[data-testid='app-shell-header-context-menu-surface'] h1",
    "[data-testid='app-shell-header-context-menu-surface'] h2",
    "[data-thread-title]",
    "h1",
    "h2",
  ].join(", "))).filter(visible);
  const title = titleCandidates.find((node) => {
    const text = normalize(node.textContent);
    return titleText ? (text === titleText || titleText.startsWith(text) || text.startsWith(titleText)) : text.length >= 3;
  }) || titleCandidates[0] || null;
  const titleRect = rectFor(title);
  const resolvedTitle = titleText || normalize(title?.textContent || "").slice(0, 160);

  const composerClasses = [
    "relative",
    "z-10",
    "flex",
    "flex-col",
    "mx-auto",
    "w-full",
    "max-w-(--thread-content-max-width)",
    "px-toolbar",
  ];
  const hasAllClasses = (node, classes) => {
    const set = new Set(String(node?.className || "").split(/\s+/).filter(Boolean));
    return classes.every((name) => set.has(name));
  };
  const candidates = new Set();
  Array.from(document.querySelectorAll("div")).forEach((node) => {
    if (hasAllClasses(node, composerClasses) && visible(node)) candidates.add(node);
  });
  Array.from(document.querySelectorAll(".composer-footer")).filter(visible).forEach((footer) => {
    candidates.add(footer);
    let node = footer.parentElement;
    for (let depth = 0; node instanceof HTMLElement && depth < 6; depth += 1, node = node.parentElement) {
      if (visible(node)) candidates.add(node);
    }
  });
  Array.from(document.querySelectorAll("textarea, [contenteditable='true']")).filter(visible).forEach((input) => {
    let node = input.parentElement;
    for (let depth = 0; node instanceof HTMLElement && depth < 6; depth += 1, node = node.parentElement) {
      if (visible(node)) candidates.add(node);
    }
  });
  const scoreComposer = (node) => {
    const rect = node.getBoundingClientRect();
    let score = 0;
    if (rect.bottom > innerHeight * 0.55) score += 80;
    if (node.matches?.(".composer-footer")) score += 6;
    if (node.querySelector?.(".composer-footer")) score += 30;
    if (node.querySelector?.("textarea, [contenteditable='true']")) score += 45;
    score += Math.min(20, Array.from(node.querySelectorAll?.("button, [role='button']") || []).filter(visible).length * 2);
    score += Math.min(20, rect.width / 80);
    score -= Math.max(0, (innerHeight * 0.45 - rect.top) / 10);
    return score;
  };
  const composer = Array.from(candidates)
    .map((node, index) => ({ node, index, score: scoreComposer(node) }))
    .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;

  return {
    sessionId: activeRef.sessionId || "",
    title: resolvedTitle || "",
    devicePixelRatio: window.devicePixelRatio || 1,
    headerRect,
    titleRect,
    composerRect: rectFor(composer),
    appError: appErrorText(),
  };
})()
"""


def cdp_enabled_from_env() -> bool:
    value = os.environ.get(CDP_DOM_ENV)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def cdp_port_from_env(default: int = DEFAULT_CDP_PORT) -> int:
    raw = os.environ.get(CDP_PORT_ENV, "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        return default
    return port if 0 < port < 65536 else default


class CodexCdpProbe:
    """Small cached read-only probe for Codex DOM state over local CDP."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_CDP_TIMEOUT_SECONDS,
        cache_seconds: float = DEFAULT_CDP_CACHE_SECONDS,
        failure_cooldown_seconds: float = DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.failure_cooldown_seconds = max(0.1, float(failure_cooldown_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self._cache: CdpDomSnapshot | None = None
        self._cache_at = 0.0
        self._failure_until = 0.0

    def snapshot(self, *, force: bool = False) -> CdpDomSnapshot | None:
        if not self.enabled:
            self.last_status = "disabled"
            return None
        now = time.monotonic()
        if not force and self._cache is not None and now - self._cache_at <= self.cache_seconds:
            self.last_status = "cache"
            return self._cache
        if not force and now < self._failure_until:
            self.last_status = "cooldown"
            return None

        try:
            targets = list_targets(self.port, self.timeout_seconds)
            target = pick_page_target(targets)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            result = evaluate_script(websocket_url, DOM_PROBE_SCRIPT, self.timeout_seconds)
            snapshot = snapshot_from_evaluate_result(result)
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._failure_until = now + self.failure_cooldown_seconds
            return None

        if snapshot is None:
            self.last_status = "empty"
            self.last_error = "Runtime.evaluate returned no DOM snapshot"
            self._failure_until = now + self.failure_cooldown_seconds
            return None
        self._cache = snapshot
        self._cache_at = time.monotonic()
        self._failure_until = 0.0
        self.last_status = "ok"
        self.last_error = ""
        return snapshot


def list_targets(port: int, timeout_seconds: float) -> list[dict[str, Any]]:
    opener = build_opener(ProxyHandler({}))
    errors: list[Exception] = []
    for host in ("127.0.0.1", "[::1]"):
        url = f"http://{host}:{port}/json"
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(512 * 1024).decode("utf-8", "replace")
            data = json.loads(payload)
            return data if isinstance(data, list) else []
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[-1]
    return []


def pick_page_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [
        target
        for target in targets
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
    ]
    codex_pages = [
        target
        for target in pages
        if (
            "codex" in f"{target.get('title') or ''} {target.get('url') or ''}".lower()
            or str(target.get("url") or "").startswith("app://")
        )
    ]
    main_pages = [
        target
        for target in codex_pages
        if not _is_hotkey_window_target(target)
    ]
    ranked_pages = sorted(
        main_pages,
        key=_page_target_rank,
        reverse=True,
    )
    if ranked_pages:
        return ranked_pages[0]
    if codex_pages:
        raise RuntimeError("No main Codex CDP page target found")
    raise RuntimeError("No Codex CDP page target found")


def _page_target_rank(target: dict[str, Any]) -> tuple[int, int]:
    title = str(target.get("title") or "").strip().lower()
    url = str(target.get("url") or "").strip().lower()
    score = 0
    if "codex" in title:
        score += 80
    if url.startswith("app://"):
        score += 60
    if url.startswith("app://-/index.html"):
        score += 120
    if _is_hotkey_window_target(target):
        score -= 160
    if "hotkey" in title:
        score -= 80
    # Prefer the main app surface over transient helper pages when scores tie.
    return score, -len(url)


def _is_hotkey_window_target(target: dict[str, Any]) -> bool:
    url = str(target.get("url") or "").strip().lower()
    title = str(target.get("title") or "").strip().lower()
    return (
        "initialroute=%2fhotkey-window" in url
        or "initialroute=/hotkey-window" in url
        or "hotkey" in title
    )


def evaluate_script(websocket_url: str, script: str, timeout_seconds: float) -> dict[str, Any]:
    return send_cdp_command(
        websocket_url,
        "Runtime.evaluate",
        runtime_evaluate_params(script),
        timeout_seconds,
    )


def runtime_evaluate_params(script: str, *, return_by_value: bool = True) -> dict[str, Any]:
    return {
        "expression": script,
        "returnByValue": return_by_value,
        "allowUnsafeEvalBlockedByCSP": True,
    }


def install_new_document_script(
    websocket_url: str,
    script: str,
    timeout_seconds: float,
) -> str:
    """Install and immediately evaluate a renderer script in one CDP session."""
    results = send_cdp_commands(
        websocket_url,
        [
            ("Page.enable", {}),
            ("Page.addScriptToEvaluateOnNewDocument", {"source": script}),
            ("Runtime.evaluate", runtime_evaluate_params(script)),
        ],
        timeout_seconds,
    )
    identifier = (
        results.get(2, {})
        .get("result", {})
        .get("identifier", "")
    )
    return str(identifier or "")


def remove_new_document_script(
    websocket_url: str,
    identifier: str,
    timeout_seconds: float,
) -> None:
    if not identifier:
        return
    send_cdp_command(
        websocket_url,
        "Page.removeScriptToEvaluateOnNewDocument",
        {"identifier": identifier},
        timeout_seconds,
    )


def send_cdp_command(
    websocket_url: str,
    method: str,
    params: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return send_cdp_commands(
        websocket_url,
        [(method, params)],
        timeout_seconds,
    )[1]


def send_cdp_commands(
    websocket_url: str,
    commands: list[tuple[str, dict[str, Any]]],
    timeout_seconds: float,
) -> dict[int, dict[str, Any]]:
    if not commands:
        return {}
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws":
        raise RuntimeError("Only local ws:// CDP endpoints are supported")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        _websocket_handshake(sock, host, port, path)
        pending: set[int] = set()
        for index, (method, params) in enumerate(commands, start=1):
            pending.add(index)
            command = {
                "id": index,
                "method": method,
                "params": params,
            }
            _send_text_frame(sock, json.dumps(command, separators=(",", ":")))
        deadline = time.monotonic() + timeout_seconds
        results: dict[int, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            message = _receive_text_message(sock)
            payload = json.loads(message)
            command_id = payload.get("id")
            if command_id in pending:
                results[int(command_id)] = payload
                pending.remove(int(command_id))
                if not pending:
                    return results
    raise TimeoutError("Timed out waiting for CDP command response")


def snapshot_from_evaluate_result(result: dict[str, Any]) -> CdpDomSnapshot | None:
    value = (
        result.get("result", {})
        .get("result", {})
        .get("value")
    )
    if not isinstance(value, dict):
        return None
    dpr = _positive_float(value.get("devicePixelRatio")) or 1.0
    return CdpDomSnapshot(
        session_id=str(value.get("sessionId") or "").strip(),
        title=str(value.get("title") or "").strip(),
        device_pixel_ratio=dpr,
        header_rect=_rect_from_value(value.get("headerRect")),
        title_rect=_rect_from_value(value.get("titleRect")),
        composer_rect=_rect_from_value(value.get("composerRect")),
        app_error=str(value.get("appError") or "").strip(),
    )


def _rect_from_value(value: Any) -> CdpRect | None:
    if not isinstance(value, dict):
        return None
    left = _finite_float(value.get("left"))
    top = _finite_float(value.get("top"))
    right = _finite_float(value.get("right"))
    bottom = _finite_float(value.get("bottom"))
    width = _finite_float(value.get("width"))
    height = _finite_float(value.get("height"))
    if left is None or top is None:
        return None
    if right is None and width is not None:
        right = left + width
    if bottom is None and height is not None:
        bottom = top + height
    if right is None or bottom is None:
        return None
    rect = CdpRect(left=left, top=top, right=right, bottom=bottom)
    return rect if rect.width > 0 and rect.height > 0 else None


def _positive_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _websocket_handshake(sock: socket.socket, host: str, port: int, path: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Origin: http://{host}:{port}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 8192:
        chunk = sock.recv(1024)
        if not chunk:
            break
        response += chunk
    first_line = response.split(b"\r\n", 1)[0]
    if b" 101 " not in first_line:
        raise RuntimeError("CDP websocket handshake failed")


def _send_text_frame(sock: socket.socket, payload: str) -> None:
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _receive_text_message(sock: socket.socket) -> str:
    parts: list[bytes] = []
    while True:
        first, second = _read_exact(sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x8:
            raise RuntimeError("CDP websocket closed")
        if opcode == 0x9:
            _send_pong_frame(sock, payload)
            continue
        if opcode in {0x1, 0x0}:
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")


def _send_pong_frame(sock: socket.socket, payload: bytes) -> None:
    header = bytearray([0x8A])
    length = len(payload)
    if length >= 126:
        payload = payload[:125]
        length = len(payload)
    header.append(0x80 | length)
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("Unexpected EOF from CDP websocket")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "CDP_DOM_ENV",
    "CDP_PORT_ENV",
    "CdpDomSnapshot",
    "CdpRect",
    "CodexCdpProbe",
    "DOM_PROBE_SCRIPT",
    "DEFAULT_CDP_PORT",
    "install_new_document_script",
    "pick_page_target",
    "remove_new_document_script",
    "runtime_evaluate_params",
    "send_cdp_command",
    "send_cdp_commands",
    "snapshot_from_evaluate_result",
]
