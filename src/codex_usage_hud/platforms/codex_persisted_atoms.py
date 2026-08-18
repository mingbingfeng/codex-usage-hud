"""Small, scoped bridge for Codex Desktop persisted-atom updates.

The Desktop sidebar keeps a client-thread binding outside Codex's rollout and
state-db stores.  A migration must clear an exact source binding through the
running Desktop process itself; replacing its state file while Desktop owns it
can be overwritten by a later renderer persistence flush.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json

from .cdp_probe import (
    cdp_enabled_from_env,
    cdp_port_from_env,
    list_targets,
    pick_page_target,
    runtime_evaluate_params,
    send_cdp_command,
)


PERSISTED_THREAD_BINDINGS_KEY = "client-thread-bindings-v1"
_DEFAULT_TIMEOUT_SECONDS = 3.0


class CodexDesktopBindingCleanupError(RuntimeError):
    """Raised when Desktop cannot prove a source binding was cleaned."""

    def __init__(
        self,
        message: str,
        *,
        source_binding_detected: bool = False,
    ) -> None:
        super().__init__(message)
        self.source_binding_detected = source_binding_detected


def _normalise_ids(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value or "").strip().casefold()
            for value in values
            if str(value or "").strip()
        )
    )


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(str(item).strip() for item in value if str(item or "").strip())


def _persisted_atom_script(
    source_ids: Sequence[str],
    *,
    commit: bool,
    timeout_ms: int,
) -> str:
    """Return a self-contained Desktop-owned exact binding operation.

    The app exposes no direct persisted-atom API on ``window``.  Its own
    renderer uses the message bridge below, so this waits for a real atom sync,
    updates only exact values in the one known binding map, then re-syncs to
    prove the source ids are gone.  It deliberately never touches prompt
    history or any other atom.
    """
    payload = json.dumps(list(_normalise_ids(source_ids)), ensure_ascii=False)
    phase = "commit" if commit else "prepare"
    return f"""
(async () => {{
  const sourceIds = new Set({payload});
  const bindingKey = {json.dumps(PERSISTED_THREAD_BINDINGS_KEY)};
  const timeoutMs = {max(250, int(timeout_ms))};
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== "function") {{
    return {{ ok: false, phase: {json.dumps(phase)}, reason: "desktop-bridge-unavailable" }};
  }}
  const sync = () => new Promise((resolve, reject) => {{
    let timer = 0;
    const finish = (callback, value) => {{
      if (timer) window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      callback(value);
    }};
    const onMessage = (event) => {{
      const message = event?.data;
      if (message?.type !== "persisted-atom-sync") return;
      finish(resolve, message);
    }};
    timer = window.setTimeout(() => finish(reject, new Error("persisted-atom-sync-timeout")), timeoutMs);
    window.addEventListener("message", onMessage);
    Promise.resolve(bridge.sendMessageFromView({{ type: "persisted-atom-sync-request" }}))
      .catch((error) => finish(reject, error));
  }});
  const bindingsFrom = (snapshot) => {{
    const state = snapshot?.state;
    const bindings = state && typeof state === "object" ? state[bindingKey] : null;
    return bindings && typeof bindings === "object" && !Array.isArray(bindings) ? bindings : {{}};
  }};
  const matching = (bindings) => Object.entries(bindings)
    .filter(([, value]) => typeof value === "string" && sourceIds.has(value.trim().toLowerCase()))
    .map(([key]) => key);
  const before = await sync();
  const bindings = bindingsFrom(before);
  const matchingBindingKeys = matching(bindings);
  const canWrite = before?.canWritePrimaryWindowTabPersistence === true;
  if (!{str(bool(commit)).lower()}) {{
    return {{
      ok: matchingBindingKeys.length === 0 || canWrite,
      phase: {json.dumps(phase)},
      canWrite,
      matchingBindingKeys,
      remainingBindingKeys: matchingBindingKeys,
    }};
  }}
  if (matchingBindingKeys.length && !canWrite) {{
    return {{
      ok: false,
      phase: {json.dumps(phase)},
      reason: "desktop-persistence-not-writable",
      canWrite,
      matchingBindingKeys,
      remainingBindingKeys: matchingBindingKeys,
    }};
  }}
  if (matchingBindingKeys.length) {{
    const next = Object.fromEntries(Object.entries(bindings)
      .filter(([, value]) => !(typeof value === "string" && sourceIds.has(value.trim().toLowerCase()))));
    await Promise.resolve(bridge.sendMessageFromView({{
      type: "persisted-atom-update",
      key: bindingKey,
      value: next,
      deleted: false,
    }}));
  }}
  const after = await sync();
  const remainingBindingKeys = matching(bindingsFrom(after));
  return {{
    ok: remainingBindingKeys.length === 0,
    phase: {json.dumps(phase)},
    canWrite,
    matchingBindingKeys,
    removedBindingKeys: matchingBindingKeys,
    remainingBindingKeys,
  }};
}})()
"""


@dataclass(frozen=True)
class DesktopBindingCleanupReport:
    """The minimal audit result required by the transfer transaction."""

    removed_binding_keys: tuple[str, ...]
    remaining_binding_keys: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return not self.remaining_binding_keys


@dataclass(frozen=True)
class DesktopBindingCleanupPlan:
    """A prepared Desktop binding cleanup, committed after local deletion."""

    cleaner: "CodexDesktopBindingCleaner"
    source_ids: tuple[str, ...]

    def commit(self) -> DesktopBindingCleanupReport:
        return self.cleaner._commit(self.source_ids)


class CodexDesktopBindingCleaner:
    """Use the live Desktop's persisted-atom protocol through local CDP."""

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
        self.timeout_seconds = max(0.4, float(timeout_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self._target_lister = target_lister
        self._target_picker = target_picker
        self._command_sender = command_sender

    def prepare_source_binding_cleanup(
        self,
        source_ids: Sequence[str],
    ) -> DesktopBindingCleanupPlan:
        """Check Desktop's writable atom state before the source is deleted."""
        normalized = _normalise_ids(source_ids)
        if not normalized:
            return DesktopBindingCleanupPlan(self, normalized)
        value = self._evaluate(normalized, commit=False)
        if value.get("ok") is not True:
            raise CodexDesktopBindingCleanupError(
                self._error(value, "Desktop 源会话绑定预检失败。"),
                source_binding_detected=bool(
                    _string_list(value.get("matchingBindingKeys"))
                ),
            )
        return DesktopBindingCleanupPlan(self, normalized)

    def _commit(self, source_ids: Sequence[str]) -> DesktopBindingCleanupReport:
        normalized = _normalise_ids(source_ids)
        if not normalized:
            return DesktopBindingCleanupReport((), ())
        value = self._evaluate(normalized, commit=True)
        remaining = _string_list(value.get("remainingBindingKeys"))
        if value.get("ok") is not True or remaining:
            raise CodexDesktopBindingCleanupError(
                self._error(value, "Desktop 源会话绑定清理未验证。")
            )
        return DesktopBindingCleanupReport(
            removed_binding_keys=_string_list(value.get("removedBindingKeys")),
            remaining_binding_keys=remaining,
        )

    def _evaluate(
        self,
        source_ids: Sequence[str],
        *,
        commit: bool,
    ) -> Mapping[str, object]:
        if not self.enabled:
            raise CodexDesktopBindingCleanupError("Codex Desktop CDP 当前不可用。")
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
                    _persisted_atom_script(
                        source_ids,
                        commit=commit,
                        timeout_ms=max(
                            250,
                            int(self.timeout_seconds * 1000) - 250,
                        ),
                    ),
                    await_promise=True,
                ),
                self.timeout_seconds,
            )
        except CodexDesktopBindingCleanupError:
            raise
        except Exception as exc:
            raise CodexDesktopBindingCleanupError(
                f"Codex Desktop 持久化绑定通道不可用：{type(exc).__name__}。"
            ) from exc
        value = (
            result.get("result", {}).get("result", {}).get("value")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(value, Mapping):
            raise CodexDesktopBindingCleanupError(
                "Codex Desktop 持久化绑定通道返回了无效结果。"
            )
        return value

    @staticmethod
    def _error(value: Mapping[str, object], fallback: str) -> str:
        reason = str(value.get("reason") or "").strip()
        if reason == "desktop-persistence-not-writable":
            return "Codex Desktop 当前不允许写入会话侧栏绑定。"
        if reason == "desktop-bridge-unavailable":
            return "Codex Desktop 持久化绑定通道不可用。"
        return reason or fallback


__all__ = [
    "CodexDesktopBindingCleanupError",
    "CodexDesktopBindingCleaner",
    "DesktopBindingCleanupPlan",
    "DesktopBindingCleanupReport",
    "PERSISTED_THREAD_BINDINGS_KEY",
]
