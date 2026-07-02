"""估算发送前静态底价的模块──在你按下回车之前就能看到的底价。

该模块在后台线程中运行，使用 ``tiktoken`` 实时估算“当前会话 + 输入框 +
静态上下文 + MCP 工具 Schema + 协议底噪”的合计 Token 数，并通过防抖
（Debounce）避免在用户打字时频繁刷新。

核心类
------
PreSendEstimator
    后台防抖估算器。调用 ``.latest()`` 获取最新快照，永远不阻塞。
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_LOGGER = logging.getLogger("codex_usage_hud.pre_send_estimator")
_LOGGER.addHandler(logging.NullHandler())

_DEFAULT_DEBOUNCE_SECONDS = 0.8
_TIKTOKEN_ENCODING = "cl100k_base"
_PADDING_TOKENS = 50


# ── 公开的数据类型 ─────────────────────────────────────────────


@dataclass(frozen=True)
class BaseEstimate:
    """一次静态底价估算的快照结果。"""

    total_tokens: int = 0
    input_text_tokens: int = 0  # A: 当前输入文本
    session_history_tokens: int = 0  # B: 会话历史
    context_files_tokens: int = 0  # C: AGENTS.md / context.md
    mcp_schema_tokens: int = 0  # D: MCP 工具 Schema
    padding_tokens: int = 0  # F: 协议底噪
    encoding_used: str = "heuristic"  # "tiktoken" | "heuristic"
    error: str = ""

    def short_label(self) -> str:
        """返回一行 UI 标签，例如 ``预估基础: ~12.5k Ts (Cache友好)``。"""
        if self.error:
            return f"估价异常: {self.error}"
        label = "Cache友好" if self.total_tokens < 50_000 else "大量上下文"
        return f"预估基础: ~{self._human(self.total_tokens)} Ts ({label})"

    def breakdown_rows(self, *, live_input_tokens: int | None = None) -> list[dict[str, object]]:
        """返回悬浮明细面板所需的 A/B/C/D/F 行数据。

        ``live_input_tokens`` 若提供则覆盖 A（当前输入）──通常由浏览器侧实时
        计算后回填；Python 侧默认 A=0（拿不到输入框文本）。
        """
        a = self.input_text_tokens if live_input_tokens is None else max(0, int(live_input_tokens))
        rows = [
            ("A", "当前输入", a),
            ("B", "会话历史", self.session_history_tokens),
            ("C", "静态约束", self.context_files_tokens),
            ("D", "MCP 工具", self.mcp_schema_tokens),
            ("F", "协议底噪", self.padding_tokens),
        ]
        return [
            {"key": key, "label": label, "tokens": int(value), "display": self._human(value)}
            for key, label, value in rows
        ]

    def with_session_history(self, history_tokens: int) -> "BaseEstimate":
        """返回补入会话历史 B 后的新估算（B 取自已确认的上下文 Token）。

        ``PreSendEstimator`` 在后台只负责计算 C+D+F（依赖文件、变动缓慢），
        而 B（会话历史）应直接取自解析快照里 API 已确认的输入 Token，
        既精确又无需重新分词。此方法在推送快照时合成最终底价。
        """
        history = max(0, int(history_tokens or 0))
        base_without_history = (
            self.input_text_tokens
            + self.context_files_tokens
            + self.mcp_schema_tokens
            + self.padding_tokens
        )
        return BaseEstimate(
            total_tokens=base_without_history + history,
            input_text_tokens=self.input_text_tokens,
            session_history_tokens=history,
            context_files_tokens=self.context_files_tokens,
            mcp_schema_tokens=self.mcp_schema_tokens,
            padding_tokens=self.padding_tokens,
            encoding_used=self.encoding_used,
            error=self.error,
        )

    @staticmethod
    def _human(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)


# ── Token 编码器（优先 tiktoken，回退到启发式）────────────────


def _tiktoken_or_fallback() -> tuple[Callable[[str], int], str]:
    """返回 ``(encode_fn, label)``。

    ``encode_fn`` 接受文本并返回 token 数量；若 tiktoken 不可用则
    回退到项目内置的 ``estimate_tokens`` 启发式函数。
    """
    try:
        import tiktoken
    except ImportError:
        from .calculator import estimate_tokens

        return estimate_tokens, "heuristic"

    try:
        enc = tiktoken.get_encoding(_TIKTOKEN_ENCODING)
    except Exception:
        from .calculator import estimate_tokens

        return estimate_tokens, "heuristic"

    def _count(text: str) -> int:
        return len(enc.encode(text or "", disallowed_special=()))

    return _count, "tiktoken"


# ── 上下文文件扫描器 ───────────────────────────────────────────


def _scan_context_files(roots: Sequence[Path], filenames: Sequence[str]) -> str:
    """在 ``roots`` 下查找 ``filenames``（例如 ``AGENTS.md``）并拼接。"""
    parts: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            for child in root.iterdir():
                if child.is_file() and child.name in filenames and child not in seen:
                    seen.add(child)
                    try:
                        parts.append(child.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
        except OSError:
            pass
        # 也检查递归子目录下的 .claude/ / .cursor/ 等
        for sub in (root / ".claude", root / ".cursor", root / ".codex"):
            if sub.is_dir():
                for fname in filenames:
                    candidate = sub / fname
                    if candidate.is_file() and candidate not in seen:
                        seen.add(candidate)
                        try:
                            parts.append(candidate.read_text(encoding="utf-8", errors="replace"))
                        except OSError:
                            pass
    return "\n".join(parts)


# ── 核心估算器 ─────────────────────────────────────────────────


@dataclass
class PreSendEstimator:
    """后台防抖 Token 底价估算器。

    典型用法::

        estimator = PreSendEstimator(project_roots=["/path/to/project"])
        estimator.start()  # 启动后台线程
        ...
        est = estimator.latest()   # 永不阻塞
        print(est.short_label())   # "预估基础: ~12.5k Ts (Cache友好)"
        estimator.close()
    """

    project_roots: list[str] = field(default_factory=list)
    # ── 以下字段在外部 ticking 时设置 ──
    input_text_getter: Callable[[], str] | None = None
    session_history_getter: Callable[[], str] | None = None
    mcp_schema_getter: Callable[[], str] | None = None
    # ── 上下文文件名列表（相对于 project_roots 查找） ──
    context_filenames: tuple[str, ...] = ("AGENTS.md", "context.md", "CLAUDE.md", ".cursorrules")
    # ── 防抖参数 ──
    debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: BaseEstimate = BaseEstimate()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending = threading.Event()
        self._encode, self._encoding_label = _tiktoken_or_fallback()
        self._context_cache: str | None = None
        self._context_cache_lock = threading.Lock()

    # ── 公共 API ───────────────────────────────────────────────

    def start(self) -> None:
        """启动后台防抖线程（守护线程）。"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hud-presend-estimator",
            daemon=True,
        )
        self._thread.start()
        _LOGGER.info("presend_estimator_started encoding=%s", self._encoding_label)

    def close(self) -> None:
        """停止后台线程。"""
        self._stop_event.set()
        self._pending.set()  # 解除等待
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def latest(self) -> BaseEstimate:
        """返回最新估算快照，永不阻塞。"""
        with self._lock:
            return self._latest

    def invalidate(self) -> None:
        """标记估算失效，触发后台线程下次 tick 时重算（外部防抖入口）。"""
        if self._thread is None or not self._thread.is_alive():
            return
        self._pending.set()

    # ── 后台线程 ───────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._pending.wait(timeout=self.debounce_seconds)
            if self._stop_event.is_set():
                break
            self._pending.clear()
            # 再等一个防抖期，让打字积累
            if self._pending.wait(timeout=self.debounce_seconds):
                # 在此期间收到了新的 invalidate，重新计时
                self._pending.clear()
                continue
            self._recompute()

    def _recompute(self) -> None:
        """执行一次完整估算（在后台线程中调用）。"""
        try:
            encode = self._encode
            # A ── 当前输入文本
            input_text = self.input_text_getter() if self.input_text_getter else ""
            a_tokens = encode(input_text)

            # B ── 会话历史
            history_text = self.session_history_getter() if self.session_history_getter else ""
            b_tokens = encode(history_text) if history_text else 0

            # C ── 静态上下文文件
            c_text = self._resolved_context_text()
            c_tokens = encode(c_text) if c_text else 0

            # D ── MCP 工具 Schema
            mcp_text = self.mcp_schema_getter() if self.mcp_schema_getter else ""
            d_tokens = encode(mcp_text) if mcp_text else 0

            # F ── 协议底噪
            f_tokens = _PADDING_TOKENS

            total = a_tokens + b_tokens + c_tokens + d_tokens + f_tokens
            estimate = BaseEstimate(
                total_tokens=total,
                input_text_tokens=a_tokens,
                session_history_tokens=b_tokens,
                context_files_tokens=c_tokens,
                mcp_schema_tokens=d_tokens,
                padding_tokens=f_tokens,
                encoding_used=self._encoding_label,
            )
            with self._lock:
                self._latest = estimate

            _LOGGER.debug(
                "presend_estimate total=%s a=%s b=%s c=%s d=%s f=%s encoding=%s",
                total,
                a_tokens,
                b_tokens,
                c_tokens,
                d_tokens,
                f_tokens,
                self._encoding_label,
            )
        except Exception as exc:
            _LOGGER.warning("presend_estimate_failed error=%s", exc, exc_info=True)
            with self._lock:
                self._latest = BaseEstimate(error=str(exc))

    def _resolved_context_text(self) -> str:
        """返回已缓存的上下文文件文本，带读锁。"""
        with self._context_cache_lock:
            if self._context_cache is not None:
                return self._context_cache
        # 初始扫描
        roots = [Path(p).expanduser().resolve() for p in self.project_roots if p]
        text = _scan_context_files(roots, self.context_filenames)
        with self._context_cache_lock:
            self._context_cache = text
        return text

    def refresh_context_cache(self) -> None:
        """外部触发时刷新上下文文件缓存（例如 CI 文件变更后）。"""
        with self._context_cache_lock:
            self._context_cache = None
        self.invalidate()

    def set_project_roots(self, roots: Sequence[str]) -> None:
        """更新扫描根目录（例如切换会话后 cwd 变化），并触发重算。

        仅在根目录集合真正变化时清缓存，避免每 tick 重复扫盘。
        """
        next_roots = [str(r) for r in roots if r]
        if next_roots == self.project_roots:
            return
        self.project_roots = next_roots
        self.refresh_context_cache()


# ── 为现有 ParsedSession 提供的基础数据封装 ────────────────────


def base_estimate_from_snapshot(
    snapshot: Any,
    estimator: PreSendEstimator | None,
) -> BaseEstimate:
    """从 ``ParsedSession`` 和估算器中提取估算结果。

    如果 estimator 为 None 或 snapshot 没有 ``estimate_base`` 字段，
    返回空估算。
    """
    if estimator is not None:
        return estimator.latest()
    if hasattr(snapshot, "estimate_base") and snapshot.estimate_base:
        return snapshot.estimate_base
    return BaseEstimate()


__all__ = [
    "BaseEstimate",
    "PreSendEstimator",
    "base_estimate_from_snapshot",
]