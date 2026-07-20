"""Local HTTP bridge for renderer-injected settings UI."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Thread
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .config import UserConfig, UserConfigStore, fetch_model_prices

DEFAULT_SETTINGS_BRIDGE_PORT = 57322


class SettingsBridgeServer:
    """Tiny localhost JSON API used by the renderer settings panel."""

    def __init__(
        self,
        store: UserConfigStore,
        host: str = "127.0.0.1",
        port: int = DEFAULT_SETTINGS_BRIDGE_PORT,
        restart_callback: Callable[[], None] | None = None,
        command_callback: Callable[[dict[str, Any]], None] | None = None,
        active_session_callback: Callable[[dict[str, Any]], None] | None = None,
        attachments_callback: Callable[[dict[str, Any]], None] | None = None,
        background_usage_query_callback: (
            Callable[[dict[str, str]], dict[str, object]] | None
        ) = None,
        background_usage_detail_callback: (
            Callable[[str], dict[str, object] | None] | None
        ) = None,
        background_usage_confirm_callback: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.host = host
        self.port = max(0, int(port))
        self.restart_callback = restart_callback
        self.command_callback = command_callback
        self.active_session_callback = active_session_callback
        self.attachments_callback = attachments_callback
        self.background_usage_query_callback = background_usage_query_callback
        self.background_usage_detail_callback = background_usage_detail_callback
        self.background_usage_confirm_callback = background_usage_confirm_callback
        self.background_usage_access_token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self.url = ""

    def start(self) -> str:
        if self._server is not None:
            return self.url
        handler = self._handler_type()
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError:
            if self.port <= 0:
                raise
            self._server = ThreadingHTTPServer((self.host, 0), handler)
        port = int(self._server.server_address[1])
        self.url = f"http://{self.host}:{port}"
        self._thread = Thread(
            target=self._server.serve_forever,
            name="codex-usage-hud-settings-bridge",
            daemon=True,
        )
        self._thread.start()
        return self.url

    @property
    def background_usage_url(self) -> str:
        if not self.url:
            return ""
        return (
            f"{self.url}/background-usage?access_token="
            f"{quote(self.background_usage_access_token, safe='')}"
        )

    def close(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        store = self.store
        restart_callback = self.restart_callback
        command_callback = self.command_callback
        active_session_callback = self.active_session_callback
        attachments_callback = self.attachments_callback
        background_usage_query_callback = self.background_usage_query_callback
        background_usage_detail_callback = self.background_usage_detail_callback
        background_usage_confirm_callback = self.background_usage_confirm_callback
        background_usage_access_token = self.background_usage_access_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "codex-usage-hud-settings"

            def log_message(self, format: str, *args: object) -> None:
                del format, args
                return

            def do_OPTIONS(self) -> None:
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/settings":
                    self._send_config(store.load(), "ok", "settings loaded")
                    return
                if parsed.path == "/background-usage":
                    self._background_usage_query(parsed)
                    return
                if parsed.path == "/background-usage/detail":
                    self._background_usage_detail(parsed)
                    return
                if parsed.path != "/settings":
                    self._send_json({"status": "failed", "message": "not found"}, 404)
                    return

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/settings":
                    self._save_settings()
                    return
                if path == "/prices/fetch":
                    self._fetch_prices()
                    return
                if path == "/restart":
                    self._request_restart()
                    return
                if path == "/command":
                    self._receive_command()
                    return
                if path == "/active-session":
                    self._receive_active_session()
                    return
                if path == "/composer-attachments":
                    self._receive_attachments()
                    return
                if path == "/background-usage/confirm":
                    self._background_usage_confirm(urlparse(self.path))
                    return
                self._send_json({"status": "failed", "message": "not found"}, 404)

            def _background_usage_authorized(self, parsed: Any) -> bool:
                supplied = str(
                    parse_qs(str(parsed.query or "")).get("access_token", [""])[0]
                )
                if secrets.compare_digest(supplied, background_usage_access_token):
                    return True
                self._send_json(
                    {"status": "failed", "message": "background usage access denied"},
                    403,
                )
                return False

            def _background_usage_query(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_query_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                query = parse_qs(str(parsed.query or ""))
                filters = {
                    "range_key": str(query.get("range", ["today"])[0]),
                    "feature": str(query.get("feature", [""])[0]),
                    "model": str(query.get("model", [""])[0]),
                    "event_id": str(query.get("eventId", [""])[0]),
                }
                try:
                    payload = background_usage_query_callback(filters)
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage query failed: {exc}",
                        },
                        500,
                    )
                    return
                self._send_json({"status": "ok", "backgroundUsage": payload})

            def _background_usage_detail(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_detail_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                query = parse_qs(str(parsed.query or ""))
                event_id = str(query.get("eventId", [""])[0]).strip()
                try:
                    payload = background_usage_detail_callback(event_id)
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage detail failed: {exc}",
                        },
                        500,
                    )
                    return
                if payload is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage event not found"},
                        404,
                    )
                    return
                self._send_json({"status": "ok", "backgroundUsageDetail": payload})

            def _background_usage_confirm(self, parsed: Any) -> None:
                if not self._background_usage_authorized(parsed):
                    return
                if background_usage_confirm_callback is None:
                    self._send_json(
                        {"status": "failed", "message": "background usage is unavailable"},
                        503,
                    )
                    return
                event_id = str(self._read_json().get("eventId") or "").strip()
                try:
                    changed = bool(background_usage_confirm_callback(event_id))
                except Exception as exc:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": f"background usage confirmation failed: {exc}",
                        },
                        500,
                    )
                    return
                self._send_json(
                    {
                        "status": "ok",
                        "message": "background usage confirmed" if changed else "unchanged",
                        "changed": changed,
                    }
                )

            def _save_settings(self) -> None:
                body = self._read_json()
                payload = body.get("settings", body)
                current = store.load()
                merged = current.to_dict()
                if isinstance(payload, dict):
                    merged.update(payload)
                config = UserConfig.from_dict(merged)
                try:
                    store.save(config)
                except OSError as exc:
                    self._send_json(
                        {"status": "failed", "message": f"save failed: {exc}"},
                        500,
                    )
                    return
                self._send_config(config, "ok", "settings saved")

            def _fetch_prices(self) -> None:
                body = self._read_json()
                current = store.load()
                provider = str(body.get("provider") or "").strip().lower()
                provider_url = (
                    current.provider_settings.get(provider).pricing_url
                    if provider and provider in current.provider_settings
                    else ""
                )
                url = str(body.get("url") or provider_url or current.pricing_url or "").strip()
                try:
                    prices = fetch_model_prices(url)
                    config = current.with_price_updates(
                        prices,
                        pricing_url=url,
                        provider=provider or None,
                    )
                    store.save(config)
                except (OSError, ValueError) as exc:
                    self._send_json(
                        {"status": "failed", "message": str(exc)},
                        400,
                    )
                    return
                self._send_config(
                    config,
                    "ok",
                    f"fetched {len(prices)} model price entries",
                    extra={"fetched": len(prices)},
                )

            def _request_restart(self) -> None:
                if restart_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "restart is not available for this HUD session",
                        },
                        503,
                    )
                    return
                try:
                    restart_callback()
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"restart failed: {exc}"},
                        500,
                    )
                    return
                self._send_json(
                    {
                        "status": "ok",
                        "message": "HUD restart requested; daemon mode will relaunch it shortly",
                    }
                )

            def _receive_command(self) -> None:
                if command_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "renderer command callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                if not body:
                    self._send_json(
                        {"status": "failed", "message": "empty command"},
                        400,
                    )
                    return
                try:
                    command_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"command failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "command accepted"})

            def _receive_active_session(self) -> None:
                if active_session_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "active session callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                if not body:
                    self._send_json(
                        {"status": "failed", "message": "empty active session payload"},
                        400,
                    )
                    return
                try:
                    active_session_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"active session failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "active session accepted"})

            def _receive_attachments(self) -> None:
                if attachments_callback is None:
                    self._send_json(
                        {
                            "status": "failed",
                            "message": "attachments callback is not available",
                        },
                        503,
                    )
                    return
                body = self._read_json()
                # 空附件也需要上报（用户清空了输入框附件），故不拒绝空 body。
                try:
                    attachments_callback(body)
                except Exception as exc:
                    self._send_json(
                        {"status": "failed", "message": f"attachments failed: {exc}"},
                        500,
                    )
                    return
                self._send_json({"status": "ok", "message": "attachments accepted"})

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    value = json.loads(raw)
                except (OSError, json.JSONDecodeError):
                    return {}
                return value if isinstance(value, dict) else {}

            def _send_config(
                self,
                config: UserConfig,
                status: str,
                message: str,
                *,
                extra: dict[str, object] | None = None,
            ) -> None:
                payload: dict[str, object] = {
                    "status": status,
                    "message": message,
                    "settings": config.to_dict(),
                    "settingsPath": str(store.path),
                }
                if extra:
                    payload.update(extra)
                self._send_json(payload)

            def _send_json(self, payload: dict[str, object], status_code: int = 200) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type,X-Codex-Usage-Hud-Token",
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(data)

        return Handler


__all__ = ["DEFAULT_SETTINGS_BRIDGE_PORT", "SettingsBridgeServer"]
