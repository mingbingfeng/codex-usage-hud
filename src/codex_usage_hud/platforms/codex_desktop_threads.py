"""Desktop-owned archive/delete lifecycle for migrated Codex threads.

Codex Desktop keeps a local thread catalog in addition to the CLI rollout and
state-db files.  Deleting those files from another process can therefore leave
an unresumable sidebar entry.  This adapter sends the same App Server requests
through the already-running Desktop process and requires the corresponding
Desktop notifications before reporting a source as deleted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import uuid

from .cdp_probe import (
    cdp_enabled_from_env,
    cdp_port_from_env,
    list_targets,
    pick_page_target,
    runtime_evaluate_params,
    send_cdp_command,
)


_DEFAULT_TIMEOUT_SECONDS = 8.0


class CodexDesktopThreadLifecycleError(RuntimeError):
    """Raised when Desktop cannot prove a thread lifecycle transition."""


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _desktop_thread_lifecycle_script(
    thread_id: str,
    cwd: str,
    *,
    timeout_ms: int,
) -> str:
    """Build one Desktop-owned archive-then-delete transaction.

    ``mcp-request`` is the renderer-to-main-process route used by Desktop's
    own App Server client.  The script deliberately waits for
    ``thread/archived`` and ``thread/deleted`` notifications, because those
    are what make Desktop update its separate local thread catalog.
    """
    payload = json.dumps(
        {
            "threadId": thread_id,
            "cwd": str(cwd or "").strip() or "/",
        },
        ensure_ascii=False,
    )
    return f"""
(async () => {{
  const input = {payload};
  const timeoutMs = {max(500, int(timeout_ms))};
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== "function") {{
    return {{ ok: false, threadId: input.threadId, error: "desktop-bridge-unavailable" }};
  }}
  const notifications = {{ archived: false, deleted: false }};
  const pending = new Map();
  const messageText = (value) => {{
    if (typeof value === "string") return value;
    if (value && typeof value === "object") {{
      return String(value.message || value.detail || value.error || "");
    }}
    return "";
  }};
  const onMessage = (event) => {{
    const data = event?.data;
    if (data?.hostId !== "local") return;
    if (data.type === "mcp-notification") {{
      const threadId = data.params?.threadId;
      if (threadId !== input.threadId) return;
      if (data.method === "thread/archived") notifications.archived = true;
      if (data.method === "thread/deleted") notifications.deleted = true;
      return;
    }}
    if (data.type !== "mcp-response") return;
    const id = data.message?.id;
    const waiter = pending.get(id);
    if (!waiter) return;
    pending.delete(id);
    window.clearTimeout(waiter.timer);
    const error = messageText(data.message?.error);
    waiter.resolve({{ ok: !error, error }});
  }};
  window.addEventListener("message", onMessage);
  const waitForNotification = async (name) => {{
    const deadline = Date.now() + timeoutMs;
    while (!notifications[name] && Date.now() < deadline) {{
      await new Promise((resolve) => window.setTimeout(resolve, 25));
    }}
    return notifications[name];
  }};
  const request = (method, params) => new Promise((resolve) => {{
    const id = `hud-desktop-thread-${{method.replace(/[^a-z]/g, "-")}}-${{crypto.randomUUID()}}`;
    const timer = window.setTimeout(() => {{
      if (pending.delete(id)) resolve({{ ok: false, error: "desktop-response-timeout" }});
    }}, timeoutMs);
    pending.set(id, {{ resolve, timer }});
    Promise.resolve(bridge.sendMessageFromView({{
      type: "mcp-request",
      hostId: "local",
      request: {{ id, method, params }},
      priority: "critical",
      source: "hud-session-migration",
      timeoutMs,
      expiresAtMs: Date.now() + timeoutMs,
    }})).catch((error) => {{
      const waiter = pending.get(id);
      if (!waiter) return;
      pending.delete(id);
      window.clearTimeout(waiter.timer);
      resolve({{ ok: false, error: `desktop-send-failed: ${{String(error)}}` }});
    }});
  }});
  try {{
    // This is the companion registration sent by Desktop's archive command.
    // It lets the same App Server connection apply its normal archive side
    // effects, while explicitly avoiding any worktree cleanup.
    await Promise.resolve(bridge.sendMessageFromView({{
      type: "archive-thread",
      hostId: "local",
      conversationId: input.threadId,
      cwd: input.cwd,
      cleanupWorktree: false,
      replacementOwnerThreadId: null,
      replacementOwnerCwd: null,
    }}));
    const archive = await request("thread/archive", {{ threadId: input.threadId }});
    if (!archive.ok) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: false,
        deleted: false,
        archiveNotification: notifications.archived,
        deleteNotification: notifications.deleted,
        error: archive.error || "desktop-archive-failed",
      }};
    }}
    if (!await waitForNotification("archived")) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: false,
        archiveNotification: false,
        deleteNotification: notifications.deleted,
        error: "desktop-archive-notification-timeout",
      }};
    }}
    const deletion = await request("thread/delete", {{ threadId: input.threadId }});
    if (!deletion.ok) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: false,
        archiveNotification: true,
        deleteNotification: notifications.deleted,
        error: deletion.error || "desktop-delete-failed",
      }};
    }}
    if (!await waitForNotification("deleted")) {{
      return {{
        ok: false,
        threadId: input.threadId,
        archived: true,
        deleted: true,
        archiveNotification: true,
        deleteNotification: false,
        error: "desktop-delete-notification-timeout",
      }};
    }}
    return {{
      ok: true,
      threadId: input.threadId,
      archived: true,
      deleted: true,
      archiveNotification: true,
      deleteNotification: true,
      error: "",
    }};
  }} finally {{
    for (const waiter of pending.values()) {{
      window.clearTimeout(waiter.timer);
      waiter.resolve({{ ok: false, error: "desktop-lifecycle-cancelled" }});
    }}
    pending.clear();
    window.removeEventListener("message", onMessage);
  }}
}})()
"""


@dataclass(frozen=True)
class DesktopThreadLifecycleReport:
    """Verified result for one source thread."""

    thread_id: str
    archived: bool
    deleted: bool
    archive_notification: bool
    delete_notification: bool
    error: str = ""

    @property
    def verified(self) -> bool:
        return (
            self.archived
            and self.deleted
            and self.archive_notification
            and self.delete_notification
            and not self.error
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "threadId": self.thread_id,
            "archived": self.archived,
            "deleted": self.deleted,
            "archiveNotification": self.archive_notification,
            "deleteNotification": self.delete_notification,
            "verified": self.verified,
            "error": self.error,
        }


class CodexDesktopThreadLifecycle:
    """Invoke the running Desktop's official thread lifecycle through CDP."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        enabled: bool | None = None,
        target_lister: Callable[[int, float], list[dict[str, object]]] = list_targets,
        target_picker: Callable[
            [list[dict[str, object]]], dict[str, object]
        ] = pick_page_target,
        command_sender: Callable[
            [str, str, Mapping[str, object], float], dict[str, object]
        ] = send_cdp_command,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self._target_lister = target_lister
        self._target_picker = target_picker
        self._command_sender = command_sender

    def archive_then_delete(
        self,
        thread_id: str,
        *,
        cwd: str = "",
    ) -> DesktopThreadLifecycleReport:
        normalized_id = _canonical_uuid(thread_id)
        if not normalized_id:
            raise CodexDesktopThreadLifecycleError("Codex Desktop 源会话标识无效。")
        if not self.enabled:
            raise CodexDesktopThreadLifecycleError("Codex Desktop CDP 当前不可用。")
        try:
            target = self._target_picker(
                self._target_lister(self.port, self.timeout_seconds)
            )
            websocket_url = str(target.get("webSocketDebuggerUrl") or "").strip()
            if not websocket_url:
                raise RuntimeError("Codex Desktop 没有可用的 CDP 页面目标。")
            result = self._command_sender(
                websocket_url,
                "Runtime.evaluate",
                runtime_evaluate_params(
                    _desktop_thread_lifecycle_script(
                        normalized_id,
                        cwd,
                        timeout_ms=max(500, int(self.timeout_seconds * 1000) - 250),
                    ),
                    await_promise=True,
                ),
                # The renderer waits independently for archive response,
                # archive notification, delete response, and delete
                # notification.  Leave enough CDP time for the full bounded
                # lifecycle instead of cutting it off after its first phase.
                self.timeout_seconds * 4 + 0.5,
            )
        except CodexDesktopThreadLifecycleError:
            raise
        except Exception as exc:
            raise CodexDesktopThreadLifecycleError(
                f"Codex Desktop 归档/删除通道不可用：{type(exc).__name__}。"
            ) from exc
        value = (
            result.get("result", {}).get("result", {}).get("value")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(value, Mapping):
            raise CodexDesktopThreadLifecycleError(
                "Codex Desktop 归档/删除通道返回了无效结果。"
            )
        reported_id = _canonical_uuid(value.get("threadId"))
        if reported_id != normalized_id:
            raise CodexDesktopThreadLifecycleError(
                "Codex Desktop 归档/删除通道返回了不匹配的会话标识。"
            )
        return DesktopThreadLifecycleReport(
            thread_id=reported_id,
            archived=bool(value.get("archived")),
            deleted=bool(value.get("deleted")),
            archive_notification=bool(value.get("archiveNotification")),
            delete_notification=bool(value.get("deleteNotification")),
            error=str(value.get("error") or "").strip(),
        )


__all__ = [
    "CodexDesktopThreadLifecycle",
    "CodexDesktopThreadLifecycleError",
    "DesktopThreadLifecycleReport",
]
