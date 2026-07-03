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
import math
import os
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

_LOGGER = logging.getLogger("codex_usage_hud.pre_send_estimator")
_LOGGER.addHandler(logging.NullHandler())

_DEFAULT_DEBOUNCE_SECONDS = 0.8
_TIKTOKEN_ENCODING = "cl100k_base"
_PADDING_TOKENS = 50
# 附件按文件名解析时，递归扫描 project_roots 的上限（防止大仓库遍历卡顿）。
_ATTACHMENT_SCAN_MAX_ENTRIES = 20000
_ATTACHMENT_SCAN_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".vscode"}
)
_SKILL_SCAN_MAX_ENTRIES = 5000
_MARKDOWN_LINK_TARGET_RE = re.compile(r"^\s*!?\[[^\]]+\]\(([^)]+)\)\s*$")


# ── 公开的数据类型 ─────────────────────────────────────────────


@dataclass(frozen=True)
class AttachmentEstimate:
    """输入框附件（图片 / 粘贴文件 / @引用 / 技能）的一次估算结果。"""

    image_tokens: int = 0  # 所有图片附件合计（按视觉分块公式近似）
    file_tokens: int = 0  # 粘贴文件 + @引用文件（读盘 tiktoken 或按名下限估计）
    mention_tokens: int = 0  # 无法定位到磁盘的 @引用/技能（按名字面量下限估计）
    image_count: int = 0
    file_count: int = 0
    mention_count: int = 0
    # 细分来源，用于 tiktoken 浮窗逐项展示；旧字段仍保留为聚合值。
    file_attachment_tokens: int = 0
    file_attachment_count: int = 0
    reference_file_tokens: int = 0
    reference_file_count: int = 0
    skill_tokens: int = 0
    skill_count: int = 0
    # 分类别的「未定位」计数：仅在真正读不到文件内容时+1，用来区分「图片按分块估算」
    # 引发的 approximate 与「文件按名字面量估算」，避免文件行错误地标 ≈ 部分未定位。
    file_attachment_unresolved: int = 0
    reference_file_unresolved: int = 0
    skill_unresolved: int = 0
    # 是否含有无法精确定位的项（磁盘未命中 / 图片分块近似），用于 UI 标注「≈」。
    approximate: bool = False

    @property
    def total_tokens(self) -> int:
        return max(0, self.image_tokens + self.file_tokens + self.mention_tokens + self.skill_tokens)

    @property
    def has_any(self) -> bool:
        return self.image_count + self.file_count + self.mention_count + self.skill_count > 0


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
    # ── 计价相关（发送端 token 全部按 input 价，缓存命中部分按 cached 价）──
    input_price_per_token: float = 0.0  # 未命中缓存的 input 单价（USD/token）
    cached_price_per_token: float = 0.0  # 命中缓存的 input 单价（USD/token）
    cache_hit_rate: float = 0.0  # 会话上下文的缓存命中率 0..1（来自上次真实请求）
    model_name: str = ""
    has_prices: bool = False
    # ── 会话上下文来源：True 表示取自上一次真实请求的实测输入（已含系统提示/工具/
    #    历史/协议开销，因此不再另加 C/D/F）；False 表示会话首条消息的冷启动估算。──
    confirmed_context: bool = False
    confirmed_cached_tokens: int = 0  # 实测命中缓存的输入 token
    confirmed_uncached_tokens: int = 0  # 实测未命中的输入 token
    # ── 输入框附件（图片 / 粘贴文件 / @引用 / 技能），发送前已确定，按 input 价计入。──
    attachments: AttachmentEstimate = field(default_factory=AttachmentEstimate)

    def short_label(self) -> str:
        """返回一行 UI 标签，例如 ``预估基础: ~12.5k Ts (Cache友好)``。"""
        if self.error:
            return f"估价异常: {self.error}"
        label = "Cache友好" if self.total_tokens < 50_000 else "大量上下文"
        return f"预估基础: ~{self._human(self.total_tokens)} Ts ({label})"

    def _cost(self, tokens: int, *, cached: bool = False) -> float | None:
        if not self.has_prices:
            return None
        price = self.cached_price_per_token if cached else self.input_price_per_token
        return max(0, int(tokens)) * price

    def breakdown_rows(self, *, live_input_tokens: int | None = None) -> list[dict[str, object]]:
        """返回悬浮明细面板所需的行数据（含 token、计价类型与金额）。

        - 名称使用用户可读的中文，不再暴露 A/B/C/D/F 代号。
        - ``会话上下文`` 按缓存命中率拆成「命中」「未命中」两行，分别计价。
        - ``live_input_tokens`` 若提供则覆盖「输入框内容」（浏览器侧实时值）。

        每行结构::

            {"label": str, "note": str, "tokens": int, "display": str,
             "cost": float|None, "cached": bool, "kind": str}

        ``kind`` 取值 ``input``（未命中价）或 ``cached``（命中价），供浏览器侧
        在打字时用对应单价实时重算「输入框内容」金额。
        """
        a = self.input_text_tokens if live_input_tokens is None else max(0, int(live_input_tokens))
        rows: list[dict[str, object]] = []
        rows.append(self._row("输入框内容", a, cached=False))

        if self.confirmed_context:
            # 会话上下文取自上一次真实请求：直接用实测的命中/未命中拆分，
            # 且不再叠加 C/D/F（它们已包含在这次实测输入里）。
            cached_hist = max(0, int(self.confirmed_cached_tokens))
            uncached_hist = max(0, int(self.confirmed_uncached_tokens))
            total_hist = cached_hist + uncached_hist
            rate = (cached_hist / total_hist) if total_hist > 0 else 0.0
            if cached_hist > 0:
                rows.append(
                    self._row(
                        "会话上下文·命中缓存",
                        cached_hist,
                        cached=True,
                        note=f"命中 {rate * 100:.0f}%",
                    )
                )
            rows.append(
                self._row(
                    "会话上下文·未命中" if cached_hist > 0 else "会话上下文",
                    uncached_hist,
                    cached=False,
                )
            )
            self._append_attachment_rows(rows)
            return rows

        # 冷启动：无真实请求，用估算的历史 + 项目规则/工具/协议开销。
        history = max(0, int(self.session_history_tokens))
        rate = min(1.0, max(0.0, float(self.cache_hit_rate)))
        cached_hist = int(round(history * rate))
        uncached_hist = history - cached_hist
        if cached_hist > 0:
            rows.append(
                self._row(
                    "会话上下文·命中缓存",
                    cached_hist,
                    cached=True,
                    note=f"命中 {rate * 100:.0f}%",
                )
            )
        if uncached_hist > 0 or history > 0:
            rows.append(
                self._row(
                    "会话上下文·未命中" if cached_hist > 0 else "会话上下文",
                    uncached_hist,
                    cached=False,
                )
            )
        rows.append(self._row("项目规则", self.context_files_tokens, cached=False))
        rows.append(self._row("工具定义", self.mcp_schema_tokens, cached=False))
        rows.append(self._row("协议开销", self.padding_tokens, cached=False))
        self._append_attachment_rows(rows)
        return rows

    def _append_attachment_rows(self, rows: list[dict[str, object]]) -> None:
        """把输入框附件（图片 / 引用文件 / 技能）追加为明细行。

        附件是本次发送的新内容，首次上传必然未命中缓存，按 input 价计。
        无法精确定位的项以 ``≈`` 前缀标注（图片分块近似 / 磁盘未命中）。
        """
        att = self.attachments
        if att.image_count > 0:
            rows.append(
                self._row(
                    f"图片附件×{att.image_count}",
                    att.image_tokens,
                    cached=False,
                    note="≈ 视觉估算",
                )
            )
        classified_file_count = att.file_attachment_count + att.reference_file_count
        classified_file_tokens = att.file_attachment_tokens + att.reference_file_tokens
        if att.file_attachment_count > 0:
            rows.append(
                self._row(
                    f"文件附件×{att.file_attachment_count}",
                    att.file_attachment_tokens,
                    cached=False,
                    note="≈ 部分未定位" if att.file_attachment_unresolved > 0 else "",
                )
            )
        if att.reference_file_count > 0:
            rows.append(
                self._row(
                    f"@引用文件×{att.reference_file_count}",
                    att.reference_file_tokens,
                    cached=False,
                    note="≈ 部分未定位" if att.reference_file_unresolved > 0 else "",
                )
            )
        unclassified_file_count = max(0, att.file_count - classified_file_count)
        unclassified_file_tokens = max(0, att.file_tokens - classified_file_tokens)
        if unclassified_file_count > 0:
            rows.append(
                self._row(
                    f"引用文件×{unclassified_file_count}",
                    unclassified_file_tokens,
                    cached=False,
                    note="≈ 部分未定位" if (att.file_attachment_unresolved + att.reference_file_unresolved) > 0 else "",
                )
            )
        if att.skill_count > 0:
            rows.append(
                self._row(
                    f"$技能×{att.skill_count}",
                    att.skill_tokens,
                    cached=False,
                    note="≈ 按名估算" if att.skill_unresolved > 0 else "",
                )
            )
        if att.mention_count > 0:
            rows.append(
                self._row(
                    f"@引用/名称×{att.mention_count}",
                    att.mention_tokens,
                    cached=False,
                    note="≈ 按名估算",
                )
            )

    def _row(
        self,
        label: str,
        tokens: int,
        *,
        cached: bool,
        note: str = "",
    ) -> dict[str, object]:
        tokens = max(0, int(tokens))
        return {
            "label": label,
            "note": note,
            "tokens": tokens,
            "display": self._human(tokens),
            "cost": self._cost(tokens, cached=cached),
            "cached": cached,
            "kind": "cached" if cached else "input",
        }

    def total_cost(self, *, live_input_tokens: int | None = None) -> float | None:
        """按缓存拆算返回合计预估金额（USD），无单价时返回 None。"""
        if not self.has_prices:
            return None
        return sum(float(row["cost"] or 0.0) for row in self.breakdown_rows(
            live_input_tokens=live_input_tokens
        ))

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
        return replace(
            self,
            total_tokens=base_without_history + history + self.attachments.total_tokens,
            session_history_tokens=history,
        )

    def with_confirmed_context(
        self,
        *,
        cached_tokens: int,
        uncached_tokens: int,
    ) -> "BaseEstimate":
        """用上一次真实请求的实测输入作为会话上下文（含缓存拆分）。

        ``last_input`` 已经包含了系统提示、工具定义、完整历史与协议开销，因此
        这里把 C/D/F 归零，避免与实测输入重复计费。合计 token = 输入框 + 实测输入。
        """
        cached = max(0, int(cached_tokens))
        uncached = max(0, int(uncached_tokens))
        context_total = cached + uncached
        return replace(
            self,
            total_tokens=self.input_text_tokens + context_total + self.attachments.total_tokens,
            session_history_tokens=context_total,
            context_files_tokens=0,
            mcp_schema_tokens=0,
            padding_tokens=0,
            confirmed_context=True,
            confirmed_cached_tokens=cached,
            confirmed_uncached_tokens=uncached,
        )

    def with_attachments(self, attachments: AttachmentEstimate) -> "BaseEstimate":
        """附加输入框附件估算，并把附件 token 计入合计。"""
        att = attachments or AttachmentEstimate()
        # 剔除旧附件 token，避免重复叠加（total_tokens 已含 self.attachments）。
        base_total = max(0, int(self.total_tokens) - self.attachments.total_tokens)
        return replace(
            self,
            attachments=att,
            total_tokens=base_total + att.total_tokens,
        )

    def with_pricing(
        self,
        *,
        input_price_per_token: float,
        cached_price_per_token: float,
        cache_hit_rate: float,
        model_name: str,
    ) -> "BaseEstimate":
        """附加计价信息（单价、缓存命中率、模型名），返回新的估算快照。"""
        return replace(
            self,
            input_price_per_token=max(0.0, float(input_price_per_token)),
            cached_price_per_token=max(0.0, float(cached_price_per_token)),
            cache_hit_rate=min(1.0, max(0.0, float(cache_hit_rate))),
            model_name=str(model_name or ""),
            has_prices=True,
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


def _estimate_image_tokens(width: int, height: int) -> int:
    """按 GPT 视觉模型的分块公式近似图片 token 数。

    注：实际受 detail 参数/模型影响，这里取 high-detail 模式的下限。
    """
    w = max(0, int(width or 0))
    h = max(0, int(height or 0))
    if w <= 0 or h <= 0:
        return 0
    w = max(1, w)
    h = max(1, h)
    # 先缩放到短边 ≤ 768，保留比例
    scale = 768.0 / min(w, h) if min(w, h) > 768 else 1.0
    sw = int(round(w * scale))
    sh = int(round(h * scale))
    # 再按 512×512 切块，每块约 85 token + base 85
    tiles_x = int(math.ceil(sw / 512.0))
    tiles_y = int(math.ceil(sh / 512.0))
    return max(85, 85 + (tiles_x * tiles_y - 1) * 170)


def _read_text_token_count(path: Path, encode_fn: Callable[[str], int]) -> int | None:
    try:
        return encode_fn(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def _attachment_path_text(name: str) -> str:
    """Extract the usable path/name from composer chip text.

    Codex @file chips may serialize as Markdown links when copied, e.g.
    ``[base.py](E:/Project/.../base.py)``. The actual prompt content is the
    target file, so use the link destination for disk lookup.
    """
    text = name.strip()
    match = _MARKDOWN_LINK_TARGET_RE.match(text)
    if not match:
        return text
    target = match.group(1).strip()
    if (target.startswith("<") and target.endswith(">")) or (
        target.startswith('"') and target.endswith('"')
    ):
        target = target[1:-1].strip()
    return target or text


def _find_attachment_file(
    name: str,
    roots: Sequence[Path],
    encode_fn: Callable[[str], int],
    max_entries: int = _ATTACHMENT_SCAN_MAX_ENTRIES,
    skip_dirs: frozenset[str] = _ATTACHMENT_SCAN_SKIP_DIRS,
) -> tuple[int, bool]:
    """在 roots 下按文件名搜索附件，返回 (token_count, found)。

    找到第一个名字完全匹配的文件就读盘 tiktoken；搜不到则给一个下限估计。
    只在 Python 侧后台线程调用，带遍历上限防止大仓库卡顿。
    """
    normalized = _attachment_path_text(name)
    if not normalized:
        return 0, True
    # 先看是否是完整路径
    maybe_path = Path(normalized)
    if maybe_path.is_absolute() and maybe_path.is_file():
        tokens = _read_text_token_count(maybe_path, encode_fn)
        if tokens is not None:
            return tokens, True
    relative_parts = [part for part in normalized.replace("\\", "/").split("/") if part not in ("", ".")]
    # 再扫描根目录
    scanned = 0
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if relative_parts:
            candidate = root_path.joinpath(*relative_parts)
            if candidate.is_file():
                tokens = _read_text_token_count(candidate, encode_fn)
                if tokens is not None:
                    return tokens, True
        for dirpath, dirnames, filenames in os.walk(root_path):
            # 跳过大仓库常见的排除目录，避免遍历过深。
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            basename = Path(normalized).name
            if basename in filenames:
                candidate = Path(dirpath) / basename
                tokens = _read_text_token_count(candidate, encode_fn)
                if tokens is not None:
                    return tokens, True
            scanned += len(filenames)
            if scanned >= max_entries:
                return int(math.ceil(len(normalized) / 4.0)), False
    # 搜不到时，按名字长度给一个保守下限。
    return int(math.ceil(len(normalized) / 4.0)), False


def _default_skill_roots() -> list[Path]:
    """Return local Codex/agent skill roots that may hold SKILL.md files."""
    home = Path.home()
    roots = [
        home / ".codex" / "skills",
        home / ".agents" / "skills",
        home / ".codex" / "plugins" / "cache",
    ]
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        roots.insert(0, Path(env_home).expanduser() / "skills")
    return roots


def _normalize_skill_name(value: str) -> str:
    text = value.strip()
    while text.startswith("$"):
        text = text[1:].strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1].strip()
    return text.strip()


def _find_skill_markdown(
    name: str,
    roots: Sequence[Path],
    encode_fn: Callable[[str], int],
    max_entries: int = _SKILL_SCAN_MAX_ENTRIES,
) -> tuple[int, bool]:
    """Locate a referenced $skill and count its SKILL.md content."""
    normalized = _normalize_skill_name(name)
    if not normalized:
        return 0, True
    lower = normalized.lower()
    scanned = 0
    for root in roots:
        root_path = Path(root).expanduser()
        if not root_path.exists():
            continue
        direct = root_path / normalized / "SKILL.md"
        if direct.is_file():
            tokens = _read_text_token_count(direct, encode_fn)
            if tokens is not None:
                return tokens, True
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in _ATTACHMENT_SCAN_SKIP_DIRS]
            if "SKILL.md" not in filenames:
                scanned += len(filenames)
                if scanned >= max_entries:
                    return int(math.ceil(len(normalized) / 4.0)), False
                continue
            skill_path = Path(dirpath) / "SKILL.md"
            folder_name = Path(dirpath).name.lower()
            if folder_name == lower:
                tokens = _read_text_token_count(skill_path, encode_fn)
                if tokens is not None:
                    return tokens, True
            try:
                head = skill_path.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                head = ""
            if f"name: {normalized}" in head or f"name: \"{normalized}\"" in head:
                tokens = _read_text_token_count(skill_path, encode_fn)
                if tokens is not None:
                    return tokens, True
            scanned += len(filenames)
            if scanned >= max_entries:
                return int(math.ceil(len(normalized) / 4.0)), False
    return int(math.ceil(len(normalized) / 4.0)), False


def _resolve_file_entry(
    entry: Any,
    roots: Sequence[Path],
    encode_fn: Callable[[str], int],
) -> tuple[int, bool]:
    """把一个文件附件条目转为 (token_count, found)。

    条目可能是纯文件名字符串（旧格式），也可能是 JS 侧从 React fiber 取到的
    ``{"name": ..., "path": <绝对路径 或 项目根相对路径>}``。有绝对路径时直接读盘 tiktoken，
    相对路径则依次拼到每个 project_root 试读，这样桌面/仓库外的引用文件、以及
    ProseMirror atMention 上报的相对路径 (如 ``Moon.Core/Entity/Entity.cs``) 都能拿到
    真实 token，而不必按文件名瞎猜下限。
    """
    if isinstance(entry, dict):
        path_text = str(entry.get("path") or "").strip()
        if path_text:
            normalized = _attachment_path_text(path_text)
            candidate = Path(normalized).expanduser()
            # 绝对路径直接读
            if candidate.is_absolute():
                if candidate.is_file():
                    tokens = _read_text_token_count(candidate, encode_fn)
                    if tokens is not None:
                        return tokens, True
            else:
                # 相对路径：mention chip 只暴露相对项目根的 path，把它拼到 project_root 上。
                for root in roots:
                    joined = (root / candidate).resolve()
                    if joined.is_file():
                        tokens = _read_text_token_count(joined, encode_fn)
                        if tokens is not None:
                            return tokens, True
        name = str(entry.get("name") or entry.get("path") or "")
    else:
        name = str(entry or "")
    return _find_attachment_file(name, roots, encode_fn)


def estimate_attachments(
    *,
    images: Sequence[dict[str, object]] | None,
    files: Sequence[str | dict[str, object]] | None,
    mentions: Sequence[str | dict[str, object]] | None,
    project_roots: Sequence[str],
    encode_fn: Callable[[str], int],
    skills: Sequence[str] | None = None,
    skill_roots: Sequence[str] | None = None,
) -> AttachmentEstimate:
    """把 JS 采集的附件信息转为 token 估算。

    返回的 ``approximate`` 标记是否含有无法精确定位的项（图片分块近似 /
    文件搜不到按名字面量估计），供 UI 展示「≈」前缀。
    """
    image_tokens = 0
    image_count = 0
    file_tokens = 0
    file_count = 0
    mention_tokens = 0
    mention_count = 0
    file_attachment_tokens = 0
    file_attachment_count = 0
    file_attachment_unresolved = 0
    reference_file_tokens = 0
    reference_file_count = 0
    reference_file_unresolved = 0
    skill_tokens = 0
    skill_count = 0
    skill_unresolved = 0
    approximate = False

    if images:
        for img in images:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if w > 0 and h > 0:
                image_tokens += _estimate_image_tokens(w, h)
                image_count += 1
                approximate = True  # 图片分块本质是近似，只要有图就标 ≈

    roots = [Path(r).expanduser().resolve() for r in project_roots if r]
    if files:
        for entry in files:
            tokens, found = _resolve_file_entry(entry, roots, encode_fn)
            file_tokens += tokens
            file_count += 1
            file_attachment_tokens += tokens
            file_attachment_count += 1
            if not found:
                approximate = True
                file_attachment_unresolved += 1

    if mentions:
        for entry in mentions:
            # mention 现在也可能是 {"name": "@Entity.cs", "path": "E:/..."}（JS 从 fiber 抓
            # 到 resourcePath 时会带上）；有绝对路径就直接读盘，回退到文件名搜项目根。
            if isinstance(entry, dict):
                raw_name = str(entry.get("name") or "").strip()
                path_text = str(entry.get("path") or "").strip()
            else:
                raw_name = str(entry or "").strip()
                path_text = ""
            stripped = raw_name.lstrip("@").strip()
            if not stripped:
                continue
            if path_text:
                tokens, found = _resolve_file_entry(
                    {"name": stripped, "path": path_text}, roots, encode_fn
                )
            else:
                tokens, found = _find_attachment_file(stripped, roots, encode_fn)
            if found:
                file_tokens += tokens
                file_count += 1
                reference_file_tokens += tokens
                reference_file_count += 1
            else:
                mention_tokens += tokens
                mention_count += 1
                approximate = True
                reference_file_unresolved += 1

    resolved_skill_roots = [
        Path(r).expanduser().resolve()
        for r in (skill_roots if skill_roots is not None else _default_skill_roots())
        if r
    ]
    if skills:
        for text in skills:
            stripped = _normalize_skill_name(str(text or ""))
            if not stripped:
                continue
            tokens, found = _find_skill_markdown(stripped, resolved_skill_roots, encode_fn)
            skill_tokens += tokens
            skill_count += 1
            if not found:
                approximate = True
                skill_unresolved += 1

    return AttachmentEstimate(
        image_tokens=image_tokens,
        image_count=image_count,
        file_tokens=file_tokens,
        file_count=file_count,
        mention_tokens=mention_tokens,
        mention_count=mention_count,
        file_attachment_tokens=file_attachment_tokens,
        file_attachment_count=file_attachment_count,
        reference_file_tokens=reference_file_tokens,
        reference_file_count=reference_file_count,
        skill_tokens=skill_tokens,
        skill_count=skill_count,
        file_attachment_unresolved=file_attachment_unresolved,
        reference_file_unresolved=reference_file_unresolved,
        skill_unresolved=skill_unresolved,
        approximate=approximate,
    )


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
    # 返回 JS 采集的输入框附件：{"images":[{width,height,name}], "files":[name], "mentions":[text]}
    attachments_getter: Callable[[], dict[str, Any]] | None = None
    update_callback: Callable[[BaseEstimate], None] | None = None
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
        self._immediate_pending = threading.Event()
        self._encode, self._encoding_label = _tiktoken_or_fallback()
        self._context_cache: str | None = None
        self._context_cache_lock = threading.Lock()
        self._attachments: dict[str, Any] = {}
        self._attachments_lock = threading.Lock()

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
        self._immediate_pending.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def latest(self) -> BaseEstimate:
        """返回最新估算快照，永不阻塞。"""
        with self._lock:
            return self._latest

    def invalidate(self, *, immediate: bool = False) -> None:
        """标记估算失效，触发后台线程下次 tick 时重算（外部防抖入口）。"""
        if self._thread is None or not self._thread.is_alive():
            return
        if immediate:
            self._immediate_pending.set()
        self._pending.set()

    # ── 后台线程 ───────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._pending.wait(timeout=self.debounce_seconds)
            if self._stop_event.is_set():
                break
            immediate = self._immediate_pending.is_set()
            if immediate:
                self._immediate_pending.clear()
                self._pending.clear()
                self._recompute()
                continue
            self._pending.clear()
            # 再等一个防抖期，让打字积累
            if self._pending.wait(timeout=self.debounce_seconds):
                if self._immediate_pending.is_set():
                    continue
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

            # G ── 输入框附件（图片 / 粘贴文件 / @引用 / 技能）
            attachments = self._resolved_attachments(encode)

            total = (
                a_tokens + b_tokens + c_tokens + d_tokens + f_tokens
                + attachments.total_tokens
            )
            estimate = BaseEstimate(
                total_tokens=total,
                input_text_tokens=a_tokens,
                session_history_tokens=b_tokens,
                context_files_tokens=c_tokens,
                mcp_schema_tokens=d_tokens,
                padding_tokens=f_tokens,
                encoding_used=self._encoding_label,
                attachments=attachments,
            )
            with self._lock:
                self._latest = estimate
            if self.update_callback is not None:
                try:
                    self.update_callback(estimate)
                except Exception:
                    _LOGGER.debug("presend_update_callback_failed", exc_info=True)

            _LOGGER.debug(
                "presend_estimate total=%s a=%s b=%s c=%s d=%s f=%s att=%s encoding=%s",
                total,
                a_tokens,
                b_tokens,
                c_tokens,
                d_tokens,
                f_tokens,
                attachments.total_tokens,
                self._encoding_label,
            )
        except Exception as exc:
            _LOGGER.warning("presend_estimate_failed error=%s", exc, exc_info=True)
            with self._lock:
                self._latest = BaseEstimate(error=str(exc))

    def _resolved_attachments(self, encode: Callable[[str], int]) -> AttachmentEstimate:
        """读取 JS 采集的附件并估算 token（后台线程调用，可读盘）。"""
        raw: dict[str, Any]
        if self.attachments_getter is not None:
            try:
                raw = self.attachments_getter() or {}
            except Exception:
                return AttachmentEstimate()
        else:
            with self._attachments_lock:
                raw = dict(self._attachments)
        if not raw:
            return AttachmentEstimate()
        return estimate_attachments(
            images=raw.get("images"),
            files=raw.get("files"),
            mentions=raw.get("mentions"),
            skills=raw.get("skills"),
            project_roots=self.project_roots,
            encode_fn=encode,
        )

    def set_attachments(self, payload: dict[str, Any] | None) -> None:
        """外部（bridge 回调）设置最新输入框附件，并触发重算。

        只在附件签名变化时触发 invalidate，避免重复扫盘。
        """
        images = list((payload or {}).get("images") or [])
        files = list((payload or {}).get("files") or [])
        mentions = list((payload or {}).get("mentions") or [])
        skills = list((payload or {}).get("skills") or [])
        normalized = {"images": images, "files": files, "mentions": mentions, "skills": skills}
        with self._attachments_lock:
            if normalized == self._attachments:
                return
            self._attachments = normalized
        self.invalidate(immediate=True)

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
    "AttachmentEstimate",
    "BaseEstimate",
    "PreSendEstimator",
    "base_estimate_from_snapshot",
    "estimate_attachments",
]
