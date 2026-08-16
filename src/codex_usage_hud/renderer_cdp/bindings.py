"""Persistent Runtime.addBinding event and command transport."""

from __future__ import annotations

import json
import queue
import socket
import threading
from typing import Any

from .connection import connect_websocket, receive_message, send_command


_CALLBACK_STOP = object()


class _BindingDisconnect:
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


class _RendererBinding:
    """Receive events from the renderer over a CDP ``Runtime.addBinding`` channel.

    The renderer page runs under a strict CSP whose ``connect-src`` does not
    allow ``http://127.0.0.1``, so in-page ``fetch``/XHR to the local settings
    bridge is blocked. A CDP binding is the reliable push channel: the page
    calls ``window[binding_name](json)`` and we receive it as
    ``Runtime.bindingCalled``. Used for both active-session and composer
    attachment events.
    """

    def __init__(
        self,
        binding_name: str,
        callback: Any,
        *,
        timeout_seconds: float,
        disconnect_callback: Any = None,
        retry_same_target: bool = False,
    ) -> None:
        self.binding_name = str(binding_name or "").strip()
        self.callback = callback
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.disconnect_callback = disconnect_callback
        self.retry_same_target = bool(retry_same_target)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback_thread: threading.Thread | None = None
        self._callback_queue: queue.Queue[object] | None = None
        self._sock: socket.socket | None = None
        self._websocket_url = ""
        self._target_id = ""
        self._disconnected_target_id = ""
        self._next_command_id = 100
        self._pending_responses: dict[
            int, tuple[threading.Event, dict[str, object]]
        ] = {}

    def ensure(self, websocket_url: str, target_id: str) -> None:
        """Start or restart the binding listener for the current page target.

        Subscription setup is intentionally asynchronous. A renderer payload
        update must not serially wait for every optional CDP binding to finish
        its websocket handshake. The active-session bootstrap is the sole
        caller that explicitly waits for its critical binding.
        """
        if not self.binding_name or not callable(self.callback) or not websocket_url:
            return
        with self._lock:
            thread = self._thread
            disconnected_target_id = self._disconnected_target_id
            if (
                thread is not None
                and thread.is_alive()
                and websocket_url == self._websocket_url
                and target_id == self._target_id
            ):
                return
            if (
                disconnected_target_id
                and disconnected_target_id == target_id
                and websocket_url == self._websocket_url
                and not self.retry_same_target
            ):
                # Do not turn an auxiliary binding disconnect into a retry
                # loop. A real target transition supplies a new target id and
                # creates a fresh binding explicitly.
                return
        self.close(join_timeout=0.3)
        with self._lock:
            stop_event = threading.Event()
            callback_queue: queue.Queue[object] = queue.Queue()
            callback = self.callback
            disconnect_callback = self.disconnect_callback
            self._stop_event = stop_event
            self._ready_event = threading.Event()
            self._callback_queue = callback_queue
            self._websocket_url = websocket_url
            self._target_id = target_id
            self._disconnected_target_id = ""
            self._callback_thread = threading.Thread(
                target=self._run_callback_worker,
                args=(callback_queue, stop_event, callback, disconnect_callback),
                name="codex-hud-binding-callback",
                daemon=True,
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(websocket_url, stop_event, callback_queue, target_id),
                name="codex-hud-active-session-cdp",
                daemon=True,
            )
            callback_thread = self._callback_thread
            thread = self._thread
            callback_thread.start()
            thread.start()

    def wait_ready(self, timeout_seconds: float | None = None) -> bool:
        """Wait for this binding only when its first event is correctness-critical."""
        with self._lock:
            ready_event = self._ready_event
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        return bool(ready_event.wait(max(0.0, float(timeout))))

    def send_command(
        self,
        websocket_url: str,
        method: str,
        params: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Send a CDP command over the already-subscribed binding socket."""
        response_ready = threading.Event()
        response: dict[str, object] = {}
        with self._lock:
            sock = self._sock
            if (
                sock is None
                or websocket_url != self._websocket_url
                or not self._ready_event.is_set()
            ):
                raise RuntimeError("renderer binding command channel is not ready")
            command_id = self._next_command_id
            self._next_command_id += 1
            self._pending_responses[command_id] = (response_ready, response)
        try:
            with self._send_lock:
                self._send_command(sock, command_id, method, params)
        except Exception:
            with self._lock:
                self._pending_responses.pop(command_id, None)
            raise
        if not response_ready.wait(max(0.05, float(timeout_seconds))):
            with self._lock:
                self._pending_responses.pop(command_id, None)
            raise TimeoutError("timed out waiting for persistent CDP response")
        with self._lock:
            self._pending_responses.pop(command_id, None)
        error = response.get("error")
        if error:
            raise RuntimeError(str(error))
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("persistent CDP response was invalid")
        return payload

    def close(self, *, join_timeout: float = 1.0) -> None:
        with self._lock:
            stop_event = self._stop_event
            sock = self._sock
            thread = self._thread
            callback_thread = self._callback_thread
            callback_queue = self._callback_queue
            self._sock = None
            self._thread = None
            self._callback_thread = None
            self._callback_queue = None
            self._websocket_url = ""
            self._target_id = ""
            self._disconnected_target_id = ""
        stop_event.set()
        if callback_queue is not None:
            callback_queue.put(_CALLBACK_STOP)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=max(0.0, float(join_timeout)))
        if (
            callback_thread is not None
            and callback_thread is not threading.current_thread()
            and callback_thread.is_alive()
        ):
            callback_thread.join(timeout=max(0.0, float(join_timeout)))

    def _run(
        self,
        websocket_url: str,
        stop_event: threading.Event | None = None,
        callback_queue: queue.Queue[object] | None = None,
        target_id: str = "",
    ) -> None:
        sock: socket.socket | None = None
        disconnect_reason = ""
        stop_event = stop_event or self._stop_event
        callback_queue = callback_queue or self._callback_queue
        try:
            sock = self._connect(websocket_url)
            with self._lock:
                if stop_event.is_set():
                    return
                self._sock = sock
            self._send_command(sock, 1, "Runtime.enable", {})
            # A page can retain a Runtime binding after the owning CDP
            # websocket dies.  Remove the old registration before adding the
            # same name again so a restarted HUD does not leave a callable
            # JavaScript function pointed at a dead listener.
            self._send_command(
                sock,
                2,
                "Runtime.removeBinding",
                {"name": self.binding_name},
            )
            self._send_command(
                sock,
                3,
                "Runtime.addBinding",
                {"name": self.binding_name},
            )
            pending = {1, 2, 3}
            while not stop_event.is_set():
                try:
                    message = receive_message(sock)
                except socket.timeout:
                    continue
                payload = json.loads(message)
                command_id = payload.get("id")
                if command_id in pending:
                    pending.remove(int(command_id))
                    if not pending:
                        self._ready_event.set()
                    continue
                try:
                    response_id = int(command_id)
                except (TypeError, ValueError):
                    response_id = 0
                with self._lock:
                    pending_response = self._pending_responses.get(response_id)
                if pending_response is not None:
                    response_ready, response = pending_response
                    response["payload"] = payload
                    response_ready.set()
                    continue
                if payload.get("method") != "Runtime.bindingCalled":
                    continue
                params = payload.get("params") or {}
                if str(params.get("name") or "") != self.binding_name:
                    continue
                self._handle_binding_payload(
                    str(params.get("payload") or ""),
                    callback_queue,
                )
        except Exception as exc:
            disconnect_reason = f"{self.binding_name} binding closed: {type(exc).__name__}"
            self._ready_event.set()
            return
        finally:
            stopped = stop_event.is_set()
            if callback_queue is not None:
                callback_queue.put(
                    _BindingDisconnect(disconnect_reason)
                    if disconnect_reason and not stopped
                    else _CALLBACK_STOP
                )
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            with self._lock:
                if self._sock is sock:
                    self._sock = None
                if (
                    disconnect_reason
                    and not stopped
                    and target_id == self._target_id
                    and websocket_url == self._websocket_url
                ):
                    self._disconnected_target_id = target_id
                pending_responses = list(self._pending_responses.values())
            for response_ready, response in pending_responses:
                response["error"] = disconnect_reason or "renderer binding closed"
                response_ready.set()
            if disconnect_reason and not stopped and callback_queue is None:
                try:
                    if callable(self.disconnect_callback):
                        self.disconnect_callback(disconnect_reason)
                except Exception:
                    pass

    def _connect(self, websocket_url: str) -> socket.socket:
        return connect_websocket(websocket_url, self.timeout_seconds)

    @staticmethod
    def _send_command(
        sock: socket.socket,
        command_id: int,
        method: str,
        params: dict[str, object],
    ) -> None:
        send_command(sock, command_id, method, params)

    def _handle_binding_payload(
        self,
        raw_payload: str,
        callback_queue: queue.Queue[object] | None = None,
    ) -> None:
        if callback_queue is None:
            callback_queue = self._callback_queue
        try:
            value = json.loads(raw_payload)
        except json.JSONDecodeError:
            return
        if not isinstance(value, dict):
            return
        if callback_queue is None:
            try:
                self.callback(value)
            except Exception:
                return
            return
        callback_queue.put(value)

    @staticmethod
    def _run_callback_worker(
        callback_queue: queue.Queue[object],
        stop_event: threading.Event,
        callback: Any,
        disconnect_callback: Any = None,
    ) -> None:
        while True:
            value = callback_queue.get()
            try:
                if value is _CALLBACK_STOP:
                    return
                if isinstance(value, _BindingDisconnect):
                    if not stop_event.is_set() and callable(disconnect_callback):
                        try:
                            disconnect_callback(value.reason)
                        except Exception:
                            pass
                    return
                if stop_event.is_set():
                    continue
                callback(value)
            except Exception:
                continue
            finally:
                callback_queue.task_done()


__all__ = ["_RendererBinding"]
