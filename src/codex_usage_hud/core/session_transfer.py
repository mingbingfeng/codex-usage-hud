"""Small local Codex App Server client used for Provider session transfers.

Codex's supported fork operation is the safest way to preserve a transcript
while changing the model provider.  This module deliberately owns only the
JSON-RPC transport and response validation; inventory safety and optional
source deletion remain in ``SessionCleanupManager``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
from threading import Event, Lock, Thread
import time
from typing import Any
import uuid


_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_TIMEOUT_SECONDS = 30.0
# App Server's ThreadSource enum accepts `custom`; an arbitrary HUD label would
# be rejected by newer native servers even though the field is optional.
_THREAD_SOURCE = "custom"
# A fork acknowledgement alone is not durable evidence that a target session
# will be shown by the provider's session list.  The short retry window handles
# the App Server's asynchronous state-db/index writer without turning HUD
# refreshes into a background polling loop.
_TARGET_VISIBILITY_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.25)
# ``thread/list`` defaults to interactive sources when omitted.  A fork made by
# this App Server can be classified as ``appServer`` on newer Codex versions,
# so explicitly include every current source kind when checking persistence.
_THREAD_LIST_SOURCE_KINDS = (
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
)
_MAX_TARGET_LIST_PAGES = 64


class SessionTransferError(RuntimeError):
    """Raised when Codex cannot create a forked session safely."""


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _provider_id(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _PROVIDER_ID_PATTERN.fullmatch(candidate) else ""


def _codex_executable() -> str:
    """Resolve a native Codex executable without retaining any credentials."""
    configured = str(os.environ.get("CODEX_EXECUTABLE") or "").strip()
    if configured:
        return configured
    if os.name == "nt":
        npm_shim = shutil.which("codex.cmd") or shutil.which("codex")
        if npm_shim:
            package_root = (
                Path(npm_shim).parent
                / "node_modules"
                / "@openai"
                / "codex"
                / "node_modules"
                / "@openai"
            )
            try:
                native_candidates = sorted(
                    package_root.glob(
                        "codex-win32-*/vendor/*/bin/codex.exe"
                    )
                )
            except OSError:
                native_candidates = []
            for candidate in native_candidates:
                if candidate.is_file():
                    return str(candidate)
        native = shutil.which("codex.exe")
        if native:
            return str(native)
    executable = shutil.which("codex")
    return str(executable or "")


def _codex_command(executable: str) -> list[str]:
    """Build a subprocess argv for both native binaries and npm shims."""
    path = str(executable or "").strip()
    if not path:
        raise SessionTransferError("未找到可用的 Codex CLI。")
    suffix = Path(path).suffix.casefold()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("ComSpec") or os.environ.get("COMSPEC") or "cmd.exe"
        # The command is assembled from the resolved executable and fixed
        # arguments; it is not influenced by renderer input.
        return [str(comspec), "/d", "/s", "/c", f'"{path}" app-server --stdio']
    return [path, "app-server", "--stdio"]


def _codex_environment(codex_home: str | Path | None = None) -> dict[str, str]:
    """Return the App Server environment bound to the selected Codex home."""
    environment = os.environ.copy()
    normalized_home = str(codex_home or "").strip()
    if normalized_home:
        environment["CODEX_HOME"] = normalized_home
    return environment


def _error_message(value: object) -> str:
    if isinstance(value, Mapping):
        for key in ("message", "error", "detail"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    text = " ".join(str(value or "").split())
    return text or "Codex App Server 请求失败。"


class CodexAppServerClient:
    """One short-lived JSON-lines App Server connection.

    The App Server emits asynchronous notifications while a request is in
    flight.  A reader thread therefore queues every line and ``request`` waits
    for its matching JSON-RPC id instead of assuming the next line is the
    response.
    """

    def __init__(
        self,
        *,
        executable: str | None = None,
        codex_home: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        process_factory: Callable[..., Any] = subprocess.Popen,
        target_visibility_retry_delays: Sequence[float] = _TARGET_VISIBILITY_RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.executable = str(executable or _codex_executable()).strip()
        self.codex_home = str(codex_home or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.process_factory = process_factory
        self.target_visibility_retry_delays = tuple(
            max(0.0, float(delay))
            for delay in target_visibility_retry_delays
        ) or (0.0,)
        self._sleep = sleep
        self._process: Any | None = None
        self._messages: queue.Queue[object] = queue.Queue()
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=24)
        self._write_lock = Lock()
        self._request_id = 0
        self._closed = False
        self._eof = Event()

    def __enter__(self) -> "CodexAppServerClient":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def stderr_tail(self) -> str:
        return " ".join(" ".join(line.split()) for line in self._stderr_tail).strip()

    def start(self) -> None:
        if self._process is not None:
            return
        argv = _codex_command(self.executable)
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self._process = self.process_factory(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=_codex_environment(self.codex_home),
            )
        except OSError as exc:
            raise SessionTransferError(
                f"无法启动 Codex App Server：{type(exc).__name__}。"
            ) from exc
        stdout = getattr(self._process, "stdout", None)
        stderr = getattr(self._process, "stderr", None)
        if stdout is None or getattr(self._process, "stdin", None) is None:
            self.close()
            raise SessionTransferError("Codex App Server 未提供可用的 stdio 通道。")
        self._stdout_thread = Thread(
            target=self._read_stdout,
            args=(stdout,),
            name="codex-usage-hud-app-server-stdout",
            daemon=True,
        )
        self._stdout_thread.start()
        if stderr is not None:
            self._stderr_thread = Thread(
                target=self._read_stderr,
                args=(stderr,),
                name="codex-usage-hud-app-server-stderr",
                daemon=True,
            )
            self._stderr_thread.start()
        try:
            result = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codex-usage-hud",
                        "title": "Codex Usage HUD session transfer",
                        "version": "0.1",
                    },
                    "capabilities": {"experimentalApi": False},
                },
            )
            if not isinstance(result, Mapping):
                raise SessionTransferError("Codex App Server 初始化返回了无效结果。")
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        try:
            stdin = getattr(process, "stdin", None)
            if stdin is not None:
                stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=1.5)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=1.5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        self._eof.set()

    def notify(self, method: str, params: Mapping[str, object] | None = None) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "method": str(method),
                "params": dict(params or {}),
            }
        )

    def request(self, method: str, params: Mapping[str, object]) -> object:
        if self._process is None or self._closed:
            raise SessionTransferError("Codex App Server 连接未启动。")
        self._request_id += 1
        request_id = self._request_id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method),
                "params": dict(params),
            }
        )
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SessionTransferError(
                    f"Codex App Server 请求超时：{method}。"
                )
            try:
                message = self._messages.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self._eof.is_set():
                    detail = self.stderr_tail
                    suffix = f"（{detail[:180]}）" if detail else ""
                    raise SessionTransferError(
                        f"Codex App Server 连接已关闭{suffix}。"
                    )
                continue
            if not isinstance(message, Mapping):
                continue
            if message.get("__eof__"):
                detail = self.stderr_tail
                suffix = f"（{detail[:180]}）" if detail else ""
                raise SessionTransferError(f"Codex App Server 连接已关闭{suffix}。")
            if message.get("__protocol_error__"):
                raise SessionTransferError("Codex App Server 返回了无效 JSON。")
            if message.get("id") != request_id:
                continue
            if message.get("error") is not None:
                raise SessionTransferError(_error_message(message.get("error")))
            return message.get("result")

    def fork(
        self,
        session_id: str,
        target_provider: str,
        *,
        cwd: str = "",
    ) -> str:
        source_id = _canonical_uuid(session_id)
        provider = _provider_id(target_provider)
        if not source_id:
            raise SessionTransferError("源会话标识无效。")
        if not provider:
            raise SessionTransferError("目标 Provider 标识无效。")
        params: dict[str, object] = {
            "threadId": source_id,
            "modelProvider": provider,
            "threadSource": _THREAD_SOURCE,
            "ephemeral": False,
        }
        normalized_cwd = str(cwd or "").strip()
        if normalized_cwd:
            try:
                if Path(normalized_cwd).is_absolute():
                    params["cwd"] = normalized_cwd
            except (OSError, ValueError):
                pass
        result = self.request("thread/fork", params)
        if not isinstance(result, Mapping):
            raise SessionTransferError("Codex fork 返回了无效结果。")
        thread = result.get("thread")
        thread_payload = thread if isinstance(thread, Mapping) else {}
        new_id = _canonical_uuid(
            thread_payload.get("id")
            or result.get("threadId")
            or result.get("id")
        )
        if not new_id or new_id == source_id:
            raise SessionTransferError("Codex fork 未返回新的会话标识。")
        returned_provider = _provider_id(
            result.get("modelProvider")
            or thread_payload.get("modelProvider")
            or thread_payload.get("model_provider")
        )
        if returned_provider and returned_provider != provider:
            raise SessionTransferError(
                f"Codex fork 的目标 Provider 不匹配：{returned_provider}。"
            )
        return new_id

    def verify_persistent_thread(
        self,
        session_id: str,
        target_provider: str,
    ) -> bool:
        """Confirm a fork is durable and listed under its target Provider.

        ``thread/fork`` returning an id only proves creation was accepted.  A
        transfer is ready only after ``thread/read`` can read its durable local
        rollout, ``thread/list`` finds that id under the selected Provider from
        the state database that backs Codex's session list, and ``thread/resume``
        can rehydrate it with that Provider without sending a user turn.
        """
        thread_id = _canonical_uuid(session_id)
        provider = _provider_id(target_provider)
        if not thread_id:
            raise SessionTransferError("目标会话标识无效。")
        if not provider:
            raise SessionTransferError("目标 Provider 标识无效。")
        last_error = ""
        for index, delay in enumerate(self.target_visibility_retry_delays):
            if index and delay:
                self._sleep(delay)
            try:
                thread_payload = self._verify_persistent_thread_read(thread_id, provider)
                if self._target_is_visible_in_provider_list(thread_id, provider):
                    self._verify_target_resume(thread_id, provider, thread_payload)
                    return True
                last_error = "Codex 目标会话尚未出现在目标 Provider 的会话列表中。"
            except SessionTransferError as exc:
                last_error = str(exc) or type(exc).__name__
        raise SessionTransferError(
            last_error or "Codex 目标会话尚未通过持久化和列表可见性验证。"
        )

    def _verify_persistent_thread_read(
        self,
        thread_id: str,
        provider: str,
    ) -> Mapping[str, object]:
        result = self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
        )
        if not isinstance(result, Mapping):
            raise SessionTransferError("Codex 目标会话读取返回了无效结果。")
        thread = result.get("thread")
        thread_payload = thread if isinstance(thread, Mapping) else {}
        returned_id = _canonical_uuid(thread_payload.get("id"))
        if returned_id != thread_id:
            raise SessionTransferError("Codex 目标会话尚不可读取。")
        returned_provider = _provider_id(
            thread_payload.get("modelProvider")
            or thread_payload.get("model_provider")
        )
        if returned_provider != provider:
            raise SessionTransferError(
                f"Codex 目标会话的 Provider 不匹配：{returned_provider or 'unknown'}。"
            )
        if thread_payload.get("ephemeral") is not False:
            raise SessionTransferError("Codex 目标会话尚未持久化。")
        rollout_path = str(thread_payload.get("path") or "").strip()
        try:
            persisted = bool(rollout_path) and Path(rollout_path).is_file()
        except (OSError, ValueError):
            persisted = False
        if not persisted:
            raise SessionTransferError("Codex 目标会话的本地记录尚不可用。")
        return thread_payload

    def _verify_target_resume(
        self,
        thread_id: str,
        provider: str,
        thread_payload: Mapping[str, object],
    ) -> None:
        """Prove that the target can be resumed under its selected Provider.

        ``thread/read`` and ``thread/list`` establish durable visibility, but
        they do not prove that Codex will rehydrate the conversation with the
        target Provider. ``thread/resume`` performs that local App Server
        validation without submitting a user turn or contacting a model.
        """
        params: dict[str, object] = {
            "threadId": thread_id,
            "modelProvider": provider,
        }
        cwd = str(thread_payload.get("cwd") or "").strip()
        try:
            if cwd and Path(cwd).is_absolute():
                params["cwd"] = cwd
        except (OSError, ValueError):
            pass
        result = self.request("thread/resume", params)
        if not isinstance(result, Mapping):
            raise SessionTransferError("Codex 目标会话续聊校验返回了无效结果。")
        resumed = result.get("thread")
        resumed_thread = resumed if isinstance(resumed, Mapping) else {}
        resumed_id = _canonical_uuid(resumed_thread.get("id"))
        if resumed_id != thread_id:
            raise SessionTransferError("Codex 目标会话无法以目标 Provider 续聊。")
        resumed_provider = _provider_id(
            resumed_thread.get("modelProvider")
            or resumed_thread.get("model_provider")
        )
        if resumed_provider != provider:
            raise SessionTransferError(
                f"Codex 目标会话续聊 Provider 不匹配：{resumed_provider or 'unknown'}。"
            )
        if resumed_thread.get("ephemeral") is True:
            raise SessionTransferError("Codex 目标会话续聊仍是临时会话。")

    def _target_is_visible_in_provider_list(
        self,
        thread_id: str,
        provider: str,
    ) -> bool:
        """Return whether the target is indexed by the filtered session list."""
        params: dict[str, object] = {
            "modelProviders": [provider],
            "sourceKinds": list(_THREAD_LIST_SOURCE_KINDS),
            "limit": 100,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            # This intentionally checks the persisted session-list index,
            # not an in-process JSONL repair that could mask a missing DB
            # registration just before a source deletion.
            "useStateDbOnly": True,
        }
        seen_cursors: set[str] = set()
        for _page in range(_MAX_TARGET_LIST_PAGES):
            result = self.request("thread/list", params)
            if not isinstance(result, Mapping):
                raise SessionTransferError(
                    "Codex 目标 Provider 会话列表返回了无效结果。"
                )
            threads = result.get("data")
            if not isinstance(threads, Sequence) or isinstance(
                threads, (str, bytes, bytearray)
            ):
                raise SessionTransferError(
                    "Codex 目标 Provider 会话列表返回了无效数据。"
                )
            for candidate in threads:
                if not isinstance(candidate, Mapping):
                    continue
                candidate_id = _canonical_uuid(candidate.get("id"))
                candidate_provider = _provider_id(
                    candidate.get("modelProvider") or candidate.get("model_provider")
                )
                if candidate_id == thread_id and candidate_provider == provider:
                    return True
            next_cursor = str(result.get("nextCursor") or "").strip()
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            params["cursor"] = next_cursor
        return False

    def _write(self, payload: Mapping[str, object]) -> None:
        process = self._process
        stdin = getattr(process, "stdin", None) if process is not None else None
        if stdin is None or self._closed:
            raise SessionTransferError("Codex App Server 写入通道不可用。")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                stdin.write(line)
                stdin.flush()
        except (OSError, ValueError) as exc:
            raise SessionTransferError("Codex App Server 请求写入失败。") from exc

    def _read_stdout(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except (TypeError, ValueError):
                    self._messages.put({"__protocol_error__": line[:240]})
                    continue
                self._messages.put(message)
        finally:
            self._eof.set()
            self._messages.put({"__eof__": True})

    def _read_stderr(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                text = str(raw_line or "").strip()
                if text:
                    self._stderr_tail.append(text)
        except Exception:
            return


__all__ = [
    "CodexAppServerClient",
    "SessionTransferError",
]
