"""Renderer CDP client lifecycle and transport helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from .config import UserConfig
from .core.connection_health import ConnectionHealth, PROBE_TIMEOUT_SECONDS
from .core.parser import ParsedSession
from .core.runtime_errors import RuntimeErrorEvent
from .platforms.cdp_probe import (
    cdp_port_from_env,
    install_new_document_script,
    list_targets,
    pick_page_target,
    remove_new_document_script,
    send_cdp_command,
)
from .platforms.codex_theme import CodexThemeProbe, CodexThemeSnapshot
from .renderer_catalog import (
    RENDERER_HUD_SCRIPT,  # noqa: F401 - compatibility export
    model_catalog_payload as _renderer_model_catalog_payload,  # noqa: F401 - compatibility export
    renderer_hud_script_with_model_catalog as _renderer_hud_script_with_model_catalog,
)
from .renderer_cdp import RendererTargetDiscovery as _RendererTargetDiscovery
from .renderer_cdp import _RendererBinding
from .renderer_payload_builder import (
    _renderer_theme_payload,
    _runtime_expression_params,
    payload_from_snapshot,
)
from .support_assets import support_qr_payload


DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
SLOW_RENDERER_UPDATE_LOG_MS = 250.0
ACTIVE_SESSION_BINDING_NAME = "codexUsageHudActiveSession"
SETTINGS_COMMAND_BINDING_NAME = "codexUsageHudSettingsCommand"
COMPOSER_ATTACHMENTS_BINDING_NAME = "codexUsageHudComposerAttachments"
LAYOUT_BINDING_NAME = "codexUsageHudLayout"
THEME_BINDING_NAME = "codexUsageHudTheme"
RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
_LOGGER = logging.getLogger("codex_usage_hud.ui.renderer_hud")
REMOVE_RENDERER_HUD_SCRIPT = (
    "(() => {"
    "let existed = false;"
    "try {"
    "const remove = window.__codexUsageHudRemove;"
    "existed = typeof remove === 'function' || !!document.getElementById('codex-usage-hud-root');"
    "if (typeof remove === 'function') remove();"
    "else {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "}"
    "} catch (_) {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "existed = true;"
    "}"
    "return existed;"
    "})()"
)


def renderer_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get(RENDERER_HUD_ENV)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in {"0", "false", "no", "off"}


class RendererHudClient:
    """Install and update the in-renderer HUD through a local CDP target."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
        target_cache_seconds: float = DEFAULT_RENDERER_TARGET_CACHE_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.target_cache_seconds = max(0.0, float(target_cache_seconds))
        self.enabled = renderer_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self.last_update_metrics: dict[str, object] = {}
        self.last_bootstrap_metrics: dict[str, object] = {}
        self.last_attach_metrics: dict[str, object] = {}
        self._target_id = ""
        self._script_identifier = ""
        self._websocket_url = ""
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._support_images_sent = False
        self._target_discovery = _RendererTargetDiscovery(
            port=self.port,
            timeout_seconds=self.timeout_seconds,
            list_targets_fn=lambda port, timeout: list_targets(port, timeout),
            pick_target_fn=lambda targets: pick_page_target(targets),
        )
        self._active_session_binding: _RendererBinding | None = None
        self._active_session_callback: Any = None
        self._settings_command_binding: _RendererBinding | None = None
        self._attachments_binding: _RendererBinding | None = None
        self._layout_binding: _RendererBinding | None = None
        self._theme_binding: _RendererBinding | None = None
        self._theme_callback: Any = None
        self._theme_bootstrap_target_id = ""
        # Theme changes are pushed by the renderer binding.  Keep the last
        # snapshot here so a normal HUD refresh never has to synchronously
        # walk the Codex DOM again.  That probe can block the renderer for
        # hundreds of milliseconds while Codex is busy.
        self._theme_snapshot: CodexThemeSnapshot | None = None
        self._theme_probe = CodexThemeProbe(
            port=self.port,
            timeout_seconds=max(0.08, min(self.timeout_seconds, 0.25)),
            cache_seconds=max(0.35, self.target_cache_seconds),
            failure_cooldown_seconds=4.0,
        )

    def set_active_session_callback(self, callback: Any) -> None:
        """Receive renderer active-session events over CDP instead of HTTP fetch."""
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        self._active_session_callback = callback if callable(callback) else None
        if callable(callback):
            self._active_session_binding = _RendererBinding(
                ACTIVE_SESSION_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
                disconnect_callback=self._handle_active_session_binding_disconnect,
            )
            self._active_session_binding.retry_same_target = True

    def _handle_active_session_binding_disconnect(self, reason: str) -> None:
        callback = self._active_session_callback
        if not callable(callback):
            return
        try:
            callback(
                {
                    "channelUnavailable": True,
                    "reason": str(reason or "renderer binding disconnected"),
                    "observedAt": int(time.time() * 1000),
                }
            )
        except Exception:
            return

    def set_settings_command_callback(self, callback: Any) -> None:
        """Receive renderer settings commands over CDP instead of HTTP fetch."""
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        if callable(callback):
            self._settings_command_binding = _RendererBinding(
                SETTINGS_COMMAND_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_attachments_callback(self, callback: Any) -> None:
        """Receive renderer composer-attachment events over CDP instead of HTTP fetch.

        The page CSP blocks in-page fetch to the local bridge, so this binding
        is the reliable channel for delivering attachment token estimates.
        """
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        if callable(callback):
            self._attachments_binding = _RendererBinding(
                COMPOSER_ATTACHMENTS_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_layout_callback(self, callback: Any) -> None:
        """Receive renderer HUD layout events (drag/resize/toggle) over CDP.

        The renderer JS reports panel geometry changes through a dedicated
        binding so the Python loop can emit ``renderer_layout_changed`` events
        without polling localStorage or waiting for the next refresh tick.
        """
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        if callable(callback):
            self._layout_binding = _RendererBinding(
                LAYOUT_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_theme_callback(self, callback: Any) -> None:
        """Receive live Codex renderer theme changes over CDP."""
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        self._theme_callback = callback if callable(callback) else None
        self._theme_bootstrap_target_id = ""
        if callable(callback):
            self._theme_binding = _RendererBinding(
                THEME_BINDING_NAME,
                self._handle_theme_binding_payload,
                timeout_seconds=self.timeout_seconds,
            )

    def _handle_theme_binding_payload(self, payload: dict[str, object]) -> None:
        callback = self._theme_callback
        if not callable(callback):
            return
        snapshot = CodexThemeSnapshot.from_probe_result(payload, source="cdp")
        if snapshot is None:
            return
        self._theme_snapshot = snapshot
        try:
            callback(_renderer_theme_payload(snapshot))
        except Exception:
            return

    def set_audit_callback(self, callback: Any) -> None:
        """Deprecated: request/response audit capture has been removed.

        Kept as a no-op so callers set up before wiring is torn down don't
        crash. Any callback passed here is discarded.
        """
        del callback

    def bootstrap_active_session(
        self,
        *,
        startup_payload: dict[str, object] | None = None,
    ) -> bool:
        """Install the renderer controller and ask it to report the selected session."""
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                stage = "script_install"
                self._install(websocket_url, target_id)
            if self._active_session_binding is not None:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
                wait_ready = getattr(self._active_session_binding, "wait_ready", None)
                if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                    raise RuntimeError("renderer active-session binding was not ready")
            active_expression = (
                "typeof window.__codexUsageHudReportActiveSession === 'function' && "
                "window.__codexUsageHudReportActiveSession('bootstrap')"
            )
            stage = "active_session_report"
            expression = active_expression
            if startup_payload:
                startup_json = json.dumps(startup_payload, ensure_ascii=False)
                expression = (
                    "(() => {"
                    "const startup = typeof window.__codexUsageHudUpdate === 'function' && "
                    f"window.__codexUsageHudUpdate({startup_json});"
                    f"const active = {active_expression};"
                    "return { startup: !!startup, active: active || {} };"
                    "})()"
                )
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
            value = result.get("result", {}).get("result", {}).get("value", False)
            active_value = (
                value.get("active", False)
                if startup_payload and isinstance(value, dict)
                else value
            )
            acknowledged = True if isinstance(active_value, dict) else bool(active_value)
            if not acknowledged:
                raise RuntimeError(
                    "renderer active session bootstrap did not acknowledge request"
                )
            self._deliver_bootstrap_active_session(active_value)
        except Exception as exc:
            self.last_bootstrap_metrics = {
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": stage,
                "startupBubble": bool(startup_payload),
            }
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.last_bootstrap_metrics = {
            "totalMs": (time.perf_counter() - started) * 1000.0,
            "failureStage": "",
            "startupBubble": bool(startup_payload),
        }
        self.last_status = "ok"
        self.last_error = ""
        return True

    def _deliver_bootstrap_active_session(self, value: object) -> None:
        """Synchronously publish the page-active session returned by bootstrap."""
        callback = self._active_session_callback
        if not callable(callback) or not isinstance(value, dict):
            return
        payload = dict(value)
        if not (
            str(payload.get("sessionId") or payload.get("session_id") or "").strip()
            or str(payload.get("title") or "").strip()
            or bool(payload.get("newSession") or payload.get("new_session"))
            or bool(payload.get("pendingSession") or payload.get("pending_session"))
        ):
            return
        try:
            callback(payload)
        except Exception:
            return

    def update(
        self,
        snapshot: ParsedSession,
        *,
        settings: UserConfig | None = None,
        active_display_mode: str = "renderer",
        settings_path: Path | str | None = None,
        settings_bridge_url: str = "",
        background_usage_bridge_url: str = "",
        background_usage_revision: int = 0,
        background_usage_notification: dict[str, object] | None = None,
        rest_reminder: dict[str, object] | None = None,
        settings_command_status: dict[str, object] | None = None,
        update_state: dict[str, object] | None = None,
        debug: bool = False,
        runtime_errors: list[RuntimeErrorEvent | dict[str, object]] | None = None,
        work_overlay_selectable_max: int = 6,
        desktop_overlay_dependency: dict[str, object] | None = None,
        provider_registry: dict[str, object] | None = None,
        app_provider: str = "",
        usage_insights: dict[str, object] | None = None,
        session_cleanup: dict[str, object] | None = None,
        connection_health: dict[str, object] | ConnectionHealth | None = None,
        request_rows_limit: int = 30,
    ) -> bool:
        started = time.perf_counter()
        support_images = [] if self._support_images_sent else support_qr_payload()
        theme_started = time.perf_counter()
        theme_snapshot = self._theme_snapshot
        if theme_snapshot is None:
            theme_snapshot = self._theme_probe.snapshot()
            self._theme_snapshot = theme_snapshot
        theme_probe_ms = (time.perf_counter() - theme_started) * 1000.0
        payload = payload_from_snapshot(
            snapshot,
            settings=settings,
            active_display_mode=active_display_mode,
            settings_path=settings_path,
            settings_bridge_url=settings_bridge_url,
            background_usage_bridge_url=background_usage_bridge_url,
            background_usage_revision=background_usage_revision,
            background_usage_notification=background_usage_notification,
            rest_reminder=rest_reminder,
            settings_command_status=settings_command_status,
            support_images=support_images,
            theme=_renderer_theme_payload(theme_snapshot),
            update_state=update_state,
            debug=debug,
            runtime_errors=runtime_errors,
            work_overlay_selectable_max=work_overlay_selectable_max,
            desktop_overlay_dependency=desktop_overlay_dependency,
            provider_registry=provider_registry,
            app_provider=app_provider,
            usage_insights=usage_insights,
            session_cleanup=session_cleanup,
            connection_health=connection_health,
            request_rows_limit=request_rows_limit,
        ).to_json()
        update_ok = self.update_payload(payload)
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "themeProbeMs": theme_probe_ms,
                "payloadBuildMs": (time.perf_counter() - theme_started) * 1000.0,
                "totalMs": (time.perf_counter() - started) * 1000.0,
            }
        )
        self.last_update_metrics = metrics
        if update_ok:
            if support_images:
                self._support_images_sent = True
            return True
        return False

    def show_startup(self, payload: dict[str, object]) -> bool:
        """Paint a startup-only payload before normal HUD domain updates begin."""
        return self.update_payload(payload)

    def update_payload(self, payload: dict[str, object]) -> bool:
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target_started = time.perf_counter()
            target = self._page_target()
            target_discovery_ms = (time.perf_counter() - target_started) * 1000.0
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                stage = "script_install"
                self._install(websocket_url, target_id)
            if self._theme_binding is not None:
                stage = "theme_binding"
                self._theme_binding.ensure(websocket_url, target_id)
            stage = "payload_apply"
            if not self._send_update(websocket_url, payload):
                raise RuntimeError("renderer update function did not acknowledge payload")
            if self._active_session_binding is not None:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
            if self._settings_command_binding is not None:
                stage = "settings_binding"
                self._settings_command_binding.ensure(websocket_url, target_id)
            if self._attachments_binding is not None:
                stage = "attachments_binding"
                self._attachments_binding.ensure(websocket_url, target_id)
            if self._layout_binding is not None:
                stage = "layout_binding"
                self._layout_binding.ensure(websocket_url, target_id)
            if self._theme_binding is not None and self._theme_bootstrap_target_id != target_id:
                try:
                    stage = "theme_bootstrap"
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(
                            "typeof window.__codexUsageHudReportTheme === 'function' "
                            "&& window.__codexUsageHudReportTheme('binding-ready')"
                        ),
                        self.timeout_seconds,
                    )
                    self._theme_bootstrap_target_id = target_id
                except Exception:
                    pass
        except Exception as exc:
            metrics = dict(self.last_update_metrics)
            metrics.update(
                {
                    "totalMs": (time.perf_counter() - started) * 1000.0,
                    "failureStage": stage,
                }
            )
            self.last_update_metrics = metrics
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "targetDiscoveryMs": target_discovery_ms,
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": "",
            }
        )
        self.last_update_metrics = metrics
        self.last_status = "ok"
        self.last_error = ""
        return True

    def probe_connection(self, *, timeout_seconds: float | None = None) -> bool:
        """Cheap CDP liveness check used by conditional heartbeat.

        Prefers the already-open active-session binding socket so healthy idle
        ticks avoid a fresh WebSocket handshake. Falls back to one ephemeral
        Runtime.evaluate against the current page target.
        """
        if not self.enabled:
            self.last_status = "disabled"
            return False
        timeout = max(
            0.05,
            float(
                PROBE_TIMEOUT_SECONDS
                if timeout_seconds is None
                else timeout_seconds
            ),
        )
        expression = (
            "typeof window.__codexUsageHudReportActiveSession === 'function' "
            "|| typeof window.__codexUsageHudUpdate === 'function'"
        )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            send_persistent = getattr(self._active_session_binding, "send_command", None)
            if callable(send_persistent):
                try:
                    if self._active_session_binding is not None:
                        self._active_session_binding.ensure(websocket_url, target_id)
                    result = send_persistent(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        timeout,
                    )
                except Exception:
                    result = send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        timeout,
                    )
            else:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    timeout,
                )
            value = result.get("result", {}).get("result", {}).get("value", False)
            ok = bool(value)
            self.last_status = "ok" if ok else "failed"
            self.last_error = "" if ok else "renderer probe expression returned false"
            return ok
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def report_active_session(self, reason: str = "self-heal") -> bool:
        """Force the injected controller to re-read and publish the active session.

        Used by session-follow self-heal after sticky new-session or binding loss.
        """
        if not self.enabled:
            self.last_status = "disabled"
            return False
        report_reason = str(reason or "self-heal").replace("\\", "\\\\").replace("'", "\\'")
        expression = (
            "typeof window.__codexUsageHudReportActiveSession === 'function' && "
            f"window.__codexUsageHudReportActiveSession('{report_reason}')"
        )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if self._active_session_binding is not None:
                self._active_session_binding.ensure(websocket_url, target_id)
            send_persistent = getattr(self._active_session_binding, "send_command", None)
            if callable(send_persistent):
                try:
                    result = send_persistent(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        self.timeout_seconds,
                    )
                except Exception:
                    result = send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(expression),
                        self.timeout_seconds,
                    )
            else:
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
            value = result.get("result", {}).get("result", {}).get("value", False)
            if isinstance(value, dict):
                self._deliver_bootstrap_active_session(value)
                self.last_status = "ok"
                self.last_error = ""
                return True
            ok = bool(value)
            self.last_status = "ok" if ok else "failed"
            self.last_error = "" if ok else "active session report was not acknowledged"
            return ok
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def rebind_active_session_channel(self) -> bool:
        """Restart the active-session CDP binding for the current page target."""
        if not self.enabled or self._active_session_binding is None:
            return False
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            # Force a fresh listener even for the same target id.
            self._active_session_binding.close(join_timeout=0.2)
            self._active_session_binding.ensure(websocket_url, target_id)
            wait_ready = getattr(self._active_session_binding, "wait_ready", None)
            if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                raise RuntimeError("active-session binding was not ready after rebind")
            self.last_status = "ok"
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def close(self) -> None:
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        self._active_session_callback = None
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        self._theme_callback = None
        self._theme_bootstrap_target_id = ""
        if not self.enabled:
            return
        try:
            for websocket_url in self._close_websocket_candidates():
                try:
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(
                            REMOVE_RENDERER_HUD_SCRIPT
                        ),
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
                if not self._script_identifier:
                    continue
                try:
                    remove_new_document_script(
                        websocket_url,
                        self._script_identifier,
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
        except Exception:
            return
        finally:
            self._clear_target_cache(clear_script=True)

    def _close_websocket_candidates(self) -> list[str]:
        urls: list[str] = []
        for websocket_url in (self._websocket_url, self._cached_websocket_url):
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        except Exception:
            pass
        return urls

    def _page_target(self, *, force: bool = False) -> dict[str, Any]:
        target = self._target_discovery.target(force=force)
        self._cached_target_id = str(
            target.get("id") or target.get("webSocketDebuggerUrl") or ""
        )
        self._cached_websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        self._target_cache_at = time.monotonic()
        return target

    def _clear_target_cache(self, *, clear_script: bool) -> None:
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._target_discovery.clear()
        if clear_script:
            self._target_id = ""
            self._websocket_url = ""
            self._script_identifier = ""
            self._theme_bootstrap_target_id = ""
            self._theme_snapshot = None

    def _install(self, websocket_url: str, target_id: str, *, force: bool = False) -> None:
        if target_id != self._target_id:
            self._theme_snapshot = None
        if force and self._script_identifier:
            try:
                remove_new_document_script(
                    websocket_url,
                    self._script_identifier,
                    self.timeout_seconds,
                )
            except Exception:
                pass
        self._script_identifier = install_new_document_script(
            websocket_url,
            _renderer_hud_script_with_model_catalog(),
            self.timeout_seconds,
        )
        self._target_id = target_id
        self._websocket_url = websocket_url
        self._support_images_sent = False

    def _send_update(self, websocket_url: str, payload: dict[str, object]) -> bool:
        payload_json = json.dumps(payload, ensure_ascii=False)
        expression = (
            "(() => {"
            "const started = performance.now();"
            "const ok = typeof window.__codexUsageHudUpdate === 'function' && "
            f"window.__codexUsageHudUpdate({payload_json});"
            "return { ok: !!ok, applyMs: performance.now() - started };"
            "})()"
        )
        started = time.perf_counter()
        transport = "ephemeral"
        persistent_ms: float | None = None
        persistent_fallback_reason = ""
        fallback_ms: float | None = None
        send_persistent = getattr(self._active_session_binding, "send_command", None)
        if callable(send_persistent):
            persistent_started = time.perf_counter()
            try:
                result = send_persistent(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                transport = "active-session-binding"
            except Exception as exc:
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                persistent_fallback_reason = f"{type(exc).__name__}: {exc}"
                fallback_started = time.perf_counter()
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
                fallback_ms = (time.perf_counter() - fallback_started) * 1000.0
        else:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        value = result.get("result", {}).get("result", {}).get("value", False)
        renderer_apply_ms: float | None = None
        ok: bool
        if isinstance(value, dict):
            ok = bool(value.get("ok", False))
            try:
                renderer_apply_ms = float(value.get("applyMs"))
            except (TypeError, ValueError):
                renderer_apply_ms = None
        else:
            ok = bool(value)
        domains_value = payload.get("payloadDomains")
        payload_domains = (
            sorted(str(key) for key in domains_value)
            if isinstance(domains_value, dict)
            else []
        )
        log_threshold = float(SLOW_RENDERER_UPDATE_LOG_MS)
        self.last_update_metrics = {
            "cdpMs": elapsed_ms,
            "rendererApplyMs": renderer_apply_ms,
            "payloadBytes": len(payload_json.encode("utf-8")),
            "payloadDomains": payload_domains,
            "transport": transport,
            "persistentMs": persistent_ms,
            "persistentFallbackReason": persistent_fallback_reason,
            "fallbackMs": fallback_ms,
            "attribution": (
                "hud_dom"
                if renderer_apply_ms is not None and renderer_apply_ms >= log_threshold
                else (
                    "codex_renderer_or_cdp"
                    if elapsed_ms >= log_threshold
                    else "normal"
                )
            ),
        }
        slow_session_switch = (
            "sessionSwitch" in payload_domains and elapsed_ms >= 150.0
        )
        if slow_session_switch or elapsed_ms >= log_threshold or (
            renderer_apply_ms is not None and renderer_apply_ms >= log_threshold
        ):
            _LOGGER.info(
                "renderer_update_timing attribution=%s transport=%s cdp_ms=%.1f persistent_ms=%s fallback_ms=%s fallback_reason=%s renderer_apply_ms=%s payload_bytes=%s domains=%s ok=%s",
                self.last_update_metrics["attribution"],
                transport,
                elapsed_ms,
                f"{persistent_ms:.1f}" if persistent_ms is not None else "-",
                f"{fallback_ms:.1f}" if fallback_ms is not None else "-",
                persistent_fallback_reason or "-",
                f"{renderer_apply_ms:.1f}" if renderer_apply_ms is not None else "-",
                self.last_update_metrics["payloadBytes"],
                ",".join(payload_domains),
                ok,
            )
        return ok


def wait_for_renderer(
    client: RendererHudClient,
    snapshot_factory: Any,
    *,
    timeout_seconds: float,
    progress_callback: Any = None,
) -> bool:
    """Attempt one renderer attach without startup polling or retrying."""
    del timeout_seconds
    if callable(progress_callback):
        try:
            progress_callback("reading_session")
        except Exception:
            pass
    started = time.perf_counter()
    snapshot_started = time.perf_counter()
    snapshot = snapshot_factory()
    snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
    if callable(progress_callback):
        try:
            progress_callback("showing_hud")
        except Exception:
            pass
    update_started = time.perf_counter()
    attached = bool(client.update(snapshot))
    update_ms = (time.perf_counter() - update_started) * 1000.0
    metrics = {
        "totalMs": (time.perf_counter() - started) * 1000.0,
        "snapshotBuildMs": snapshot_ms,
        "hudUpdateMs": update_ms,
        "update": dict(getattr(client, "last_update_metrics", {}) or {}),
    }
    client.last_attach_metrics = metrics
    if metrics["totalMs"] >= float(SLOW_RENDERER_UPDATE_LOG_MS):
        update_metrics = metrics["update"]
        attribution = (
            "python_snapshot"
            if snapshot_ms >= update_ms
            else str(update_metrics.get("attribution") or "hud_or_cdp")
        )
        _LOGGER.info(
            "renderer_attach_timing attribution=%s total_ms=%.1f snapshot_ms=%.1f hud_update_ms=%.1f cdp_ms=%s renderer_apply_ms=%s",
            attribution,
            metrics["totalMs"],
            snapshot_ms,
            update_ms,
            update_metrics.get("cdpMs", "-"),
            update_metrics.get("rendererApplyMs", "-"),
        )
    return attached


def remove_renderer_hud_from_pages(
    *,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
) -> int:
    """Best-effort cleanup for renderer HUD DOM left in any Codex page target."""
    removed = 0
    try:
        targets = list_targets(int(port or cdp_port_from_env()), timeout_seconds)
    except Exception:
        return 0
    for target in targets:
        if target.get("type") != "page":
            continue
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            continue
        try:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(
                    REMOVE_RENDERER_HUD_SCRIPT
                ),
                timeout_seconds,
            )
        except Exception:
            continue
        if bool(
            result.get("result", {})
            .get("result", {})
            .get("value", False)
        ):
            removed += 1
    return removed


__all__ = [
    "RendererHudClient",
    "remove_renderer_hud_from_pages",
    "wait_for_renderer",
]
