"""Small local Codex App Server client used for Provider session transfers.

Codex's supported fork operation is the safest way to preserve a transcript
while changing the model provider.  This module deliberately owns only the
JSON-RPC transport and response validation; inventory safety and optional
source deletion remain in ``SessionCleanupManager``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
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
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.executable = str(executable or _codex_executable()).strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.process_factory = process_factory
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
