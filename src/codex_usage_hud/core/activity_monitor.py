"""运行中隐性行为监听──检测 Codex 在 ReAct 循环里“偷读”了哪些文件。

发送前无法预测 Codex 会读取哪些源码文件（变量 E）。因此这里不做数字
预测，而是做“行为感知”：解析 Codex 的工具调用事件，提取出当前正在被
AI 读取的文件名，供 HUD 亮起黄色警告灯。

设计取舍
--------
Codex 每次工具调用都会写入本地 session JSONL（``response_item`` →
``function_call``）。项目已有 ``FileChangeWatcher`` 会在该文件变更时唤醒
渲染循环，因此**默认零新增线程**：直接从已解析的 ``ParsedSession`` 中提取
读取行为即可，成本几乎为零。

``detect_reading_activity()``
    从 ``ParsedSession`` 提取当前读取状态──推荐路径，无额外 I/O。
``CodexActivityMonitor``
    可选的独立 tail 监听器，用于不走渲染快照的场景。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("codex_usage_hud.activity_monitor")
_LOGGER.addHandler(logging.NullHandler())

# 会触发“深度读取”警告的工具名（Codex / MCP 文件工具）。
_READ_TOOL_NAMES = frozenset(
    {
        "read_file",
        "read",
        "view",
        "view_file",
        "open_file",
        "cat",
        "read_text_file",
        "view_codebase",
        "codebase_search",
        "grep",
        "glob",
        "list_dir",
        "search_files",
    }
)

# 从命令行式的 shell 工具参数中提取文件名，例如 ``cat src/ScanClient.cs``。
_SHELL_READ_PATTERN = re.compile(
    r"\b(?:cat|less|head|tail|bat|type|Get-Content)\s+([^\s;|&><]+)"
)


@dataclass(frozen=True)
class ReadingActivity:
    """一次“AI 正在读取文件”行为快照。"""

    active: bool = False
    tool_name: str = ""
    file_name: str = ""  # 展示用短名（basename）
    file_path: str = ""  # 原始路径
    detail: str = ""

    def warning_label(self) -> str:
        """返回滚动警告文案，例如 ``⚡ AI 正在深度读取: ScanClient.cs...``。"""
        if not self.active or not self.file_name:
            return ""
        return f"⚡ AI 正在深度读取: {self.file_name}..."


def _extract_file_from_arguments(tool_name: str, arguments: Any) -> tuple[str, str]:
    """从工具调用参数里解析 ``(file_path, detail)``。

    参数通常是 JSON 字符串，例如 ``{"path": "src/ScanClient.cs"}``；
    也可能是 shell 命令 ``{"command": "cat src/ScanClient.cs"}``。
    """
    if arguments is None:
        return "", ""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)

    # 优先尝试结构化 JSON
    payload: Any = None
    if isinstance(arguments, Mapping):
        payload = arguments
    else:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = None

    if isinstance(payload, Mapping):
        for key in ("path", "file", "file_path", "filename", "target_file", "abs_path"):
            value = payload.get(key)
            if value:
                return str(value), str(value)
        # shell 风格：{"command": [...]} 或 {"command": "cat x"}
        command = payload.get("command") or payload.get("cmd")
        if isinstance(command, (list, tuple)):
            command = " ".join(str(part) for part in command)
        if command:
            match = _SHELL_READ_PATTERN.search(str(command))
            if match:
                return match.group(1), str(command)
            # grep/glob 等：取最后一个非选项参数
            tokens = [t for t in str(command).split() if not t.startswith("-")]
            if len(tokens) >= 2:
                return tokens[-1], str(command)

    # 回退：直接在原始文本中搜命令模式
    match = _SHELL_READ_PATTERN.search(raw)
    if match:
        return match.group(1), raw
    return "", raw[:140]


def _is_read_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip().lower()
    if name in _READ_TOOL_NAMES:
        return True
    # MCP 工具通常带命名空间前缀，如 ``filesystem.read_file``
    tail = name.rsplit(".", 1)[-1].rsplit("__", 1)[-1]
    return tail in _READ_TOOL_NAMES


def detect_reading_activity(snapshot: Any) -> ReadingActivity:
    """从 ``ParsedSession`` 提取当前的“AI 读取文件”状态。

    这是**推荐入口**：复用已解析快照，无额外磁盘 I/O。

    判定逻辑：
    - 任务仍在运行（未结束/未中止）；
    - 最新活动是一次 ``function_call`` 且工具属于读取类。
    """
    # 任务已结束──熄灯，交棒给结算面板。
    if getattr(snapshot, "task_completed_at", None) is not None:
        return ReadingActivity(active=False)
    if getattr(snapshot, "task_aborted_at", None) is not None:
        return ReadingActivity(active=False)

    activity = getattr(snapshot, "activity", None)
    if activity is None or getattr(activity, "kind", "") != "tool call":
        return ReadingActivity(active=False)

    # activity.detail 形如 ``read_file {"path": "src/ScanClient.cs"}``
    detail = str(getattr(activity, "detail", "") or "")
    tool_name, _, arguments = detail.partition(" ")
    if not _is_read_tool(tool_name):
        return ReadingActivity(active=False)

    file_path, detail_text = _extract_file_from_arguments(tool_name, arguments.strip())
    file_name = Path(file_path).name if file_path else ""
    return ReadingActivity(
        active=bool(file_name),
        tool_name=tool_name,
        file_name=file_name,
        file_path=file_path,
        detail=detail_text or detail,
    )


class CodexActivityMonitor:
    """可选的独立 tail 监听器，用于不经渲染快照的低频轮询场景。

    大多数情况下应优先使用 :func:`detect_reading_activity`──它复用渲染
    循环已经解析好的快照，成本更低。仅当需要独立于渲染节奏、以更高频率
    捕捉读取行为时才启用本类。
    """

    def __init__(
        self,
        session_path_getter: Any,
        *,
        poll_seconds: float = 0.5,
        tail_bytes: int = 16 * 1024,
    ) -> None:
        self._session_path_getter = session_path_getter
        self._poll_seconds = max(0.1, float(poll_seconds))
        self._tail_bytes = max(1024, int(tail_bytes))
        self._lock = threading.Lock()
        self._latest = ReadingActivity(active=False)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hud-activity-monitor",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def latest(self) -> ReadingActivity:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                path = self._session_path_getter()
            except Exception:
                path = None
            if not path:
                continue
            activity = self._scan_tail(Path(path))
            with self._lock:
                self._latest = activity

    def _scan_tail(self, path: Path) -> ReadingActivity:
        """只读文件尾部，避免全量解析。"""
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - self._tail_bytes))
                chunk = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ReadingActivity(active=False)

        # 从尾部向前找最后一个 function_call
        for line in reversed(chunk.splitlines()):
            line = line.strip()
            if not line or '"function_call"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, Mapping) else None
            if not isinstance(payload, Mapping) or payload.get("type") != "function_call":
                continue
            tool_name = str(payload.get("name") or "")
            if not _is_read_tool(tool_name):
                return ReadingActivity(active=False)
            file_path, detail = _extract_file_from_arguments(
                tool_name, payload.get("arguments")
            )
            file_name = Path(file_path).name if file_path else ""
            return ReadingActivity(
                active=bool(file_name),
                tool_name=tool_name,
                file_name=file_name,
                file_path=file_path,
                detail=detail,
            )
        return ReadingActivity(active=False)


__all__ = [
    "CodexActivityMonitor",
    "ReadingActivity",
    "detect_reading_activity",
]