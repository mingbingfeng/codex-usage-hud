"""Persistent, local-first search index for Codex session rollouts.

The index intentionally lives outside Codex's own databases. It stores only
searchable text and rollout fingerprints, never session UUIDs in a renderer
payload. Queries use a resident postings index; the optional legacy FTS5 table
is retained only for compatibility with older on-disk indexes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import re
import sqlite3
import sys
from threading import RLock
import time
from typing import Callable


# Rollouts can be several megabytes (especially after long tool outputs). Keep
# a generous safety ceiling while avoiding an unbounded copy of a corrupt log.
MAX_FIELD_CHARS = 16 * 1024 * 1024
MAX_DOCUMENT_CHARS = 64 * 1024 * 1024
MAX_MATCHES = 100_000

_FTS_TABLE = "session_search_fts"
_DOC_TABLE = "session_search_documents"

_USER_MESSAGE_TYPES = {"user_message", "user_message_item"}
_ASSISTANT_MESSAGE_TYPES = {"agent_message", "assistant_message"}
_ITEM_PAYLOAD_TYPES = {"item_started", "item_updated", "item_completed"}
_TOOL_CALL_TYPES = {
    "function_call",
    "custom_tool_call",
    "function_call_output",
    "custom_tool_call_output",
}
_READ_TOOL_HINTS = {
    "cat",
    "find",
    "glob",
    "grep",
    "list",
    "ls",
    "read",
    "search",
    "stat",
    "tree",
}
_CHANGE_PAYLOAD_TYPES = {
    "file_change",
    "filechange",
    "file_edit",
    "fileedit",
    "apply_patch",
    "patch",
}
_TEXT_KEYS = (
    "text",
    "message",
    "content",
    "output",
    "summary",
    "summary_text",
    "reasoning",
    "command",
    "cmd",
    "arguments",
    "input",
    "stdout",
    "stderr",
    "result",
    "description",
)
# A single record can carry megabytes of tool output. Keys listed here keep
# only a head/tail window once they exceed ``_BULK_TEXT_THRESHOLD`` so one
# record cannot dominate a session's extraction cost. Command-like keys are
# deliberately excluded: they are small and carry the highest search value.
_BULK_TEXT_KEYS = frozenset(
    {
        "aggregated_output",
        "content",
        "formatted_output",
        "message",
        "output",
        "reasoning",
        "result",
        "stderr",
        "stdout",
        "summary",
        "summary_text",
        "text",
    }
)
_BULK_TEXT_THRESHOLD = 65_536
_BULK_TEXT_HEAD = 16_384
_BULK_TEXT_TAIL = 4_096
# Ceiling for the text extracted from one rollout record.
_RECORD_TEXT_BUDGET = 131_072
# Shared ceiling on the total text extracted for one session family across all
# fields (user + assistant + tool). Without this, three independent 16 MB
# buckets can retain up to 48 MB per session and inflate the resident index,
# which slows both the first build and cold restart. The validated prototype
# used 8 MB with no recall regression (100% vs the un-budgeted extractor).
_PER_SESSION_BUDGET = 8 * 1024 * 1024
_PATH_KEYS = {
    "path",
    "file",
    "file_path",
    "filepath",
    "filename",
    "target_file",
    "targetfile",
    "abs_path",
    "abspath",
}
_PATCH_TARGET_RE = re.compile(
    r"\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*([^\r\n]+?)"
    r"(?:\\r?\\n|\\n|\r?\n|$)",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|(?:\\\\)|(?:/)|(?:\.\.?[\\/])|(?:[A-Za-z0-9_.-]+[\\/]))"
    r"[^\s\"'`<>|{}\[\],;()]+"
)
_WRITE_COMMAND_RE = re.compile(
    r"(?i)(?:\b(?:sed|perl)\b[^\r\n]*\s-i(?:\s|$)|"
    r"\b(?:tee|set-content|out-file|add-content)\b|>>|(?:^|\s)>\s*)"
)
_WHITESPACE_RE = re.compile(r"\s+")
_INDEX_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", re.UNICODE)
_SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?i)(\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*)([\"']?)[^\s,;\"']+"
    ),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;\"']+"),
)
# CJK runs are single tokens, so a two-character Chinese query can never hit a
# 3-gram posting. Emitting bigrams for CJK tokens keeps short Chinese queries
# on the posting-intersection path instead of a full substring scan.
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]")
# Resident-memory snapshot: pickle of plain built-ins only (no classes), so
# loading can never execute code. ``stamps`` carries the SQLite ``indexed_at``
# value observed at write time; on load, documents whose stamp no longer
# matches SQLite are rebuilt from their rows.
_SNAPSHOT_VERSION = 4
_SNAPSHOT_SUFFIX = ".memory"
_SNAPSHOT_MIN_INTERVAL = 300.0
# Entries below this count are parsed inline; larger batches (full builds)
# use a process pool when the runtime allows it.
_PARALLEL_PARSE_MIN_ENTRIES = 24
_PARALLEL_PARSE_WORKERS = 8


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Parsed searchable fields for one root session family."""

    session_id: str
    user_text: str
    assistant_text: str
    tool_text: str
    changed_paths: tuple[str, ...]
    title: str = ""
    workdir: str = ""
    model_provider: str = ""
    client_kind: str = ""

    @property
    def searchable(self) -> str:
        values = (
            self.title,
            self.workdir,
            self.model_provider,
            self.client_kind,
            self.user_text,
            self.assistant_text,
            self.tool_text,
            " ".join(self.changed_paths),
        )
        return _bounded_text("\n".join(value for value in values if value))


def normalise_workdir(value: object) -> str:
    """Return a stable path spelling for matching and opaque workdir IDs."""

    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    # Preserve a Windows drive while removing duplicate separators and ``/.``.
    prefix = ""
    if re.match(r"^[A-Za-z]:/", text):
        prefix, text = text[:3], text[3:]
    elif text.startswith("//"):
        prefix, text = "//", text[2:]
    elif text.startswith("/"):
        prefix, text = "/", text.lstrip("/")
    try:
        parts = [part for part in PurePosixPath(text).parts if part not in {"", "."}]
        normalised = "/".join(parts)
    except (TypeError, ValueError):
        normalised = text
    if prefix:
        normalised = prefix + normalised
    return normalised.rstrip("/") or prefix.rstrip("/")


def workdir_identity(value: object) -> str:
    """Return a renderer-safe opaque identity for one absolute workdir."""

    normalised = normalise_workdir(value).casefold()
    if not normalised:
        return ""
    return hashlib.sha256(normalised.encode("utf-8", errors="replace")).hexdigest()[:20]


def _bounded_text(value: object, limit: int = MAX_FIELD_CHARS) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(r"\1<redacted>", text)
    return text[: max(0, int(limit))]


def _window_text(value: str) -> str:
    """Return a bounded head/tail window for one oversized text value."""

    if len(value) <= _BULK_TEXT_THRESHOLD:
        return value
    return f"{value[:_BULK_TEXT_HEAD]} {value[-_BULK_TEXT_TAIL:]}"


class _TextBucket:
    """Append-only text collector with O(1) length accounting.

    Accumulating with ``len(" ".join(parts))`` on every append made
    extraction quadratic in the size of a session: 8_000 appends of 4 KB
    cost ~12s for only 32 MB of data.
    """

    __slots__ = ("parts", "_size")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self._size = 0

    def add(self, value: object, *, limit: int = MAX_FIELD_CHARS) -> bool:
        if self._size >= limit:
            return False
        if isinstance(value, str):
            text = value
        elif isinstance(value, (int, float, bool)):
            text = str(value)
        else:
            return False
        room = limit - self._size
        if len(text) > room:
            text = text[:room]
        if not text:
            return False
        self.parts.append(text)
        self._size += len(text) + 1
        return True

    def value(self) -> str:
        return _bounded_text(" ".join(self.parts))


def _collect_text(
    value: object,
    out: list[str],
    budget: list[int],
    depth: int = 0,
) -> None:
    """Collect raw text from one payload, bounded by ``budget[0]`` characters.

    Values are kept verbatim; whitespace collapsing and secret redaction
    happen once per field in :func:`_bounded_text` instead of on every leaf.
    """

    if depth > 6 or budget[0] <= 0:
        return
    if isinstance(value, str):
        text = _window_text(value)
        if len(text) > budget[0]:
            text = text[: budget[0]]
        if text:
            out.append(text)
            budget[0] -= len(text)
        return
    if isinstance(value, bool):
        out.append("true" if value else "false")
        return
    if isinstance(value, (int, float)):
        out.append(str(value))
        return
    if isinstance(value, Mapping):
        for key in _TEXT_KEYS:
            if key not in value:
                continue
            nested = value[key]
            if (
                isinstance(nested, str)
                and key in _BULK_TEXT_KEYS
                and len(nested) > _BULK_TEXT_THRESHOLD
            ):
                nested = f"{nested[:_BULK_TEXT_HEAD]} {nested[-_BULK_TEXT_TAIL:]}"
            _collect_text(nested, out, budget, depth + 1)
            if budget[0] <= 0:
                return
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_text(item, out, budget, depth + 1)
            if budget[0] <= 0:
                return


def _path_from_value(value: object) -> Iterable[str]:
    if isinstance(value, str):
        text = _clean_path_candidate(value)
        if text:
            yield text
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _path_from_value(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _path_from_value(nested)


def _looks_like_path(value: str) -> bool:
    text = _clean_path_candidate(value)
    if not text or len(text) > 2048:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if any(char in text for char in "\r\n\t"):
        return False
    return (
        "/" in text
        or "\\" in text
        or bool(re.match(r"^[A-Za-z]:$", text))
        or bool(re.search(r"\.[A-Za-z0-9]{1,12}$", text))
    )


def _clean_path_candidate(value: object) -> str:
    text = str(value or "").strip().strip("'\"")
    # JSON-encoded patch blocks contain literal ``\\n`` separators. Keep the
    # path before the separator and discard the rest of the protocol marker.
    text = re.split(r"(?:\\r?\\n|\\n|\r?\n)", text, maxsplit=1)[0]
    return text.rstrip(".,:;\"'")


def _extract_paths(payload: Mapping[str, object], raw_line: str) -> set[str]:
    paths: set[str] = set()

    def visit(value: object, key: str = "") -> None:
        key_name = key.casefold().replace("-", "_")
        if key_name in _PATH_KEYS:
            for candidate in _path_from_value(value):
                if _looks_like_path(candidate):
                    paths.add(candidate)
            return
        if isinstance(value, Mapping):
            for nested_key, nested in value.items():
                visit(nested, str(nested_key))
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested, key)

    visit(payload)
    if "***" in raw_line:
        for match in _PATCH_TARGET_RE.finditer(raw_line):
            candidate = _clean_path_candidate(match.group(1))
            if candidate:
                paths.add(candidate)
    for match in _PATH_TOKEN_RE.finditer(raw_line):
        candidate = _clean_path_candidate(match.group(0))
        if _looks_like_path(candidate):
            paths.add(candidate)
    return paths


def _has_change_evidence(
    payload: Mapping[str, object], payload_type: str, raw_line: str
) -> bool:
    """Return whether a tool record plausibly changed a file.

    Read-only tools still contribute to ``tool_text`` but should not make a
    session look like it modified every file it inspected. Explicit patch
    blocks and write/edit tool names are treated as authoritative evidence.
    """

    # Every patch header starts with ``***``; the substring probe keeps the
    # regex off the hot path for the overwhelming majority of records.
    if (
        "***" in raw_line
        and _PATCH_TARGET_RE.search(raw_line)
        and payload_type not in {
            "user_message",
            "agent_message",
            "message",
        }
    ):
        return True
    if payload_type.casefold().replace("-", "_") in _CHANGE_PAYLOAD_TYPES:
        return True
    if any(
        str(key or "").casefold().replace("-", "_") in {"changes", "edits", "patch", "diff"}
        for key in payload
    ):
        return True
    command = str(payload.get("command") or payload.get("cmd") or "")
    if command and _WRITE_COMMAND_RE.search(command):
        return True
    name = str(payload.get("name") or payload.get("tool_name") or "").casefold()
    if not name:
        return False
    if any(hint in name for hint in _READ_TOOL_HINTS):
        return False
    return any(
        hint in name
        for hint in ("apply", "edit", "write", "create", "delete", "replace", "patch", "move")
    )


def _path_variants(value: object) -> tuple[str, ...]:
    raw = str(value or "").strip().strip("'\"")
    normalised = normalise_workdir(raw)
    if not normalised:
        return ()
    variants = [raw, normalised]
    parts = [part for part in normalised.split("/") if part]
    if parts:
        variants.append(parts[-1])
    if len(parts) > 1:
        variants.append("/".join(parts[-2:]))
    return tuple(dict.fromkeys(item for item in variants if item))


def parse_rollout(paths: Sequence[Path]) -> tuple[str, str, str, tuple[str, ...]]:
    """Read rollout JSONL files and extract bounded searchable fields."""

    user = _TextBucket()
    assistant = _TextBucket()
    tools = _TextBucket()
    changed: set[str] = set()
    total = 0
    # A single shared budget across all three fields, so one session family
    # cannot retain more than ``_PER_SESSION_BUDGET`` characters. Without it,
    # three independent 16 MB buckets would inflate the resident index and
    # slow both the first build and cold restart.
    budget = _PER_SESSION_BUDGET
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    if total >= MAX_DOCUMENT_CHARS or budget <= 0:
                        break
                    total += len(raw_line)
                    try:
                        record = json.loads(raw_line)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    record_type = str(record.get("type") or "").casefold()
                    payload_type = str(payload.get("type") or "").casefold()
                    # Text is collected only when a branch will consume it.
                    # Metadata, context and token-count records are skipped
                    # outright instead of being walked for nothing.
                    if payload_type in _USER_MESSAGE_TYPES or (
                        record_type == "event_msg" and payload_type == "user_message"
                    ):
                        target, source = user, payload
                    elif payload_type in _ASSISTANT_MESSAGE_TYPES or (
                        record_type == "event_msg" and payload_type == "agent_message"
                    ):
                        target, source = assistant, payload
                    elif payload_type == "message":
                        role = str(payload.get("role") or "").casefold()
                        target = user if role in {"user", "human"} else assistant
                        source = payload
                    elif payload_type in _TOOL_CALL_TYPES:
                        target, source = tools, payload
                    elif payload_type in _CHANGE_PAYLOAD_TYPES:
                        target, source = tools, payload
                    elif payload_type in {"reasoning", "analysis"}:
                        target, source = assistant, payload
                    elif payload_type in _ITEM_PAYLOAD_TYPES:
                        item = payload.get("item")
                        target = tools
                        source = item if isinstance(item, Mapping) else None
                        if isinstance(item, Mapping) and _has_change_evidence(
                            item, str(item.get("type") or ""), raw_line
                        ):
                            changed.update(_extract_paths(item, raw_line))
                    else:
                        target, source = None, None

                    if target is not None and source is not None:
                        remaining = max(0, min(_RECORD_TEXT_BUDGET, budget))
                        parts: list[str] = []
                        _collect_text(source, parts, [remaining])
                        if parts:
                            text = " ".join(parts)
                            target.add(text)
                            budget -= min(len(text), budget)

                    if _has_change_evidence(payload, payload_type, raw_line):
                        changed.update(_extract_paths(payload, raw_line))
                if total >= MAX_DOCUMENT_CHARS:
                    break
        except (OSError, UnicodeError):
            continue
    return (
        user.value(),
        assistant.value(),
        tools.value(),
        tuple(sorted({variant for path in changed for variant in _path_variants(path)})),
    )


def rollout_fingerprints(paths: Sequence[Path]) -> tuple[tuple[str, int, int], ...]:
    values: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            values.append((str(path), -1, -1))
        else:
            values.append((str(path), int(stat.st_size), int(stat.st_mtime_ns)))
    return tuple(values)


def search_terms(query: object) -> tuple[str, ...]:
    """Split a query into path-aware, case-insensitive terms."""

    text = str(query or "").strip().casefold().replace("\\", "/")
    return tuple(
        dict.fromkeys(
            token
            for token in _INDEX_TOKEN_RE.findall(text)
            if token
        )
    )


def _index_tokens(value: object) -> tuple[str, ...]:
    """Return bounded unique tokens used by the in-memory search index.

    Tokens are casefolded individually rather than casefolding the whole
    field first: the tokeniser splits on both ``\\`` and ``/``, so a global
    ``replace("\\\\", "/")`` is a no-op, and folding only the matched runs
    avoids allocating a full copy of a multi-megabyte tool-output field.
    """

    text = str(value or "")
    tokens: dict[str, None] = {}
    for match in _INDEX_TOKEN_RE.finditer(text):
        token = match.group(0)
        if len(token) > 1024:
            # A single unbroken pasted blob is not useful as one searchable
            # token and would otherwise retain a very large duplicate string.
            values = (token[:512].casefold(), token[-512:].casefold())
        else:
            values = (token.casefold(),)
        for value_item in values:
            if value_item:
                tokens.setdefault(value_item, None)
        if len(tokens) >= 200_000:
            break
    return tuple(tokens)


def _token_grams(token: str) -> tuple[str, ...]:
    length = len(token)
    if length < 2 or length > 1024:
        return ()
    grams: dict[str, None] = {}
    # Pure-ASCII tokens are the overwhelming majority (code identifiers and
    # English words); a cheap ``isascii`` check keeps the CJK regex off the
    # hot path entirely for them.
    if not token.isascii() and _CJK_RUN_RE.search(token):
        # CJK runs tokenize as one long token, so a run of ``n`` characters
        # would otherwise emit ~2n grams (trigrams *and* bigrams). Emitting
        # only bigrams keeps two-character Chinese queries on the posting
        # fast path, while longer Chinese queries are resolved by intersecting
        # consecutive bigrams (then verified against ``search_text``) at a
        # fraction of the index size.
        for index in range(length - 1):
            grams.setdefault(token[index : index + 2], None)
    elif length >= 3:
        for index in range(length - 2):
            grams.setdefault(token[index : index + 3], None)
    return tuple(grams)


def _memory_field(
    name: str,
    value: object,
) -> tuple[str, tuple[str, ...], frozenset[str], str]:
    tokens = _index_tokens(value)
    grams = frozenset(
        gram
        for token in tokens
        for gram in _token_grams(token)
    )
    return name, tokens, grams, "\x00".join(tokens)


def _memory_fields(
    document: SearchDocument,
) -> tuple[tuple[str, tuple[str, ...], frozenset[str], str], ...]:
    return (
        _memory_field("user", document.user_text),
        _memory_field("assistant", document.assistant_text),
        _memory_field("tool", document.tool_text),
        _memory_field("file", " ".join(document.changed_paths)),
        _memory_field(
            "metadata",
            " ".join(
                (
                    document.title,
                    document.workdir,
                    document.model_provider,
                    document.client_kind,
                )
            ),
        ),
    )


def _parse_fields(
    user_text: str,
    assistant_text: str,
    tool_text: str,
    changed_paths: tuple[str, ...],
) -> tuple[tuple[str, tuple[str, ...], frozenset[str], str], ...]:
    """Compute the four large memory fields (all but ``metadata``).

    Tokenisation plus gram generation dominates a first build (~60s on the
    real corpus) and is embarrassingly parallel per session, so it runs inside
    the worker process that already holds the extracted text. The caller adds
    the tiny ``metadata`` field in the main process, where title/workdir are
    known.
    """
    return (
        _memory_field("user", user_text),
        _memory_field("assistant", assistant_text),
        _memory_field("tool", tool_text),
        _memory_field("file", " ".join(changed_paths)),
    )


def _process_pool_allowed() -> bool:
    """Return whether worker processes are safe in this runtime.

    Frozen (PyInstaller) builds re-execute the executable on ``spawn``
    unless the entry point calls :func:`multiprocessing.freeze_support`,
    which this application does not; those builds parse inline instead.
    """

    if os.environ.get("CODEX_HUD_SEARCH_NO_PROCESSES"):
        return False
    return not getattr(sys, "frozen", False)


def _parse_entry_job(
    job: tuple[str, Sequence[str]],
) -> tuple[str, str, str, str, tuple[str, ...], tuple]:
    """Parse one session's rollouts inside a worker process.

    The worker also tokenises the four large fields (user/assistant/tool/file)
    here, so the main process only merges postings instead of re-tokenising
    hundreds of megabytes single-threaded.
    """

    session_id, path_strings = job
    try:
        user_text, assistant_text, tool_text, changed_paths = parse_rollout(
            [Path(item) for item in path_strings]
        )
        fields = _parse_fields(user_text, assistant_text, tool_text, changed_paths)
    except Exception:
        # Never let one malformed rollout sink the whole pool: a missing
        # session simply degrades to empty fields rather than an error.
        user_text = assistant_text = tool_text = ""
        changed_paths = ()
        fields = ()
    return session_id, user_text, assistant_text, tool_text, changed_paths, fields


def _parse_entries_parallel(
    jobs: list[tuple[str, list[str]]],
    *,
    cancelled: Callable[[], bool] | None,
) -> dict[str, tuple[str, str, str, tuple[str, ...], tuple]]:
    """Parse many stale rollouts in a process pool; empty dict on any failure."""

    if not jobs:
        return {}
    try:
        workers = max(2, min(_PARALLEL_PARSE_WORKERS, os.cpu_count() or 2))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results: dict[str, tuple[str, str, str, tuple[str, ...], tuple]] = {}
            # A moderate chunksize amortises IPC by shipping several jobs per
            # message, unlike submitting one task per file.
            chunksize = max(1, len(jobs) // (workers * 4))
            for session_id, user_text, assistant_text, tool_text, changed, fields in pool.map(
                _parse_entry_job, jobs, chunksize=chunksize
            ):
                if callable(cancelled) and cancelled():
                    return {}
                results[str(session_id)] = (
                    user_text,
                    assistant_text,
                    tool_text,
                    changed,
                    fields,
                )
            return results
    except (OSError, ImportError, RuntimeError, ValueError):
        return {}


@dataclass(frozen=True, slots=True)
class _MemoryDocument:
    session_id: str
    fields: tuple[tuple[str, tuple[str, ...], frozenset[str], str], ...]
    search_text: str
    token_set: frozenset[str]


class SessionSearchIndex:
    """Persistent snapshot plus an Everything-style resident query index.

    SQLite is only the durable snapshot. Once loaded, queries use postings held
    in memory and do not open SQLite, parse JSONL, or scan the document table.
    Rollout parsing is intentionally kept in ``sync_batches`` so it can run in
    a cancellable background worker without blocking the session inventory.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        use_fts: bool = False,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self._fts_available: bool | None = True if use_fts else False
        self._lock = RLock()
        self._memory_loaded = False
        self._documents: dict[str, _MemoryDocument] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._snapshot_last_write = 0.0

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_DOC_TABLE} ("
            "session_id TEXT PRIMARY KEY, fingerprints TEXT NOT NULL, "
            "user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, "
            "tool_text TEXT NOT NULL, changed_paths TEXT NOT NULL, "
            "title TEXT NOT NULL, workdir TEXT NOT NULL, "
            "model_provider TEXT NOT NULL, client_kind TEXT NOT NULL, "
            "searchable TEXT NOT NULL, indexed_at REAL NOT NULL)"
        )
        if self._fts_available:
            try:
                connection.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5("
                    "session_id UNINDEXED, content, tokenize='trigram')"
                )
                self._fts_available = True
            except sqlite3.Error:
                self._fts_available = False
        connection.commit()
        return connection

    @staticmethod
    def _encode_fingerprints(value: Sequence[tuple[str, int, int]]) -> str:
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode_fingerprints(value: object) -> tuple[tuple[str, int, int], ...]:
        try:
            payload = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        if not isinstance(payload, list):
            return ()
        result: list[tuple[str, int, int]] = []
        for item in payload:
            if isinstance(item, list) and len(item) == 3:
                try:
                    result.append((str(item[0]), int(item[1]), int(item[2])))
                except (TypeError, ValueError):
                    continue
        return tuple(result)

    @staticmethod
    def _document_from_row(row: Sequence[object]) -> SearchDocument:
        # Callers select a fixed column order:
        #   session_id, fingerprints, user_text, assistant_text, tool_text,
        #   changed_paths, title, workdir, model_provider, client_kind, ...
        # ``fingerprints`` sits at index 1, so the text fields start at index 2.
        try:
            changed_payload = json.loads(str(row[5] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            changed_payload = []
        changed_paths = (
            tuple(str(item) for item in changed_payload if str(item or "").strip())
            if isinstance(changed_payload, list)
            else ()
        )
        return SearchDocument(
            session_id=str(row[0] or ""),
            user_text=str(row[2] or ""),
            assistant_text=str(row[3] or ""),
            tool_text=str(row[4] or ""),
            changed_paths=changed_paths,
            title=str(row[6] or ""),
            workdir=str(row[7] or ""),
            model_provider=str(row[8] or ""),
            client_kind=str(row[9] or ""),
        )

    def _add_memory_document_locked(
        self,
        document: SearchDocument,
        precomputed_fields: tuple | None = None,
    ) -> None:
        canonical = str(document.session_id or "").strip()
        if not canonical:
            return
        self._remove_memory_document_locked(canonical)
        if precomputed_fields:
            # Four large fields were tokenised in a worker process; only the
            # tiny metadata field (title/workdir/provider/client_kind) is left
            # for the main process, where those values are known.
            metadata_field = _memory_field(
                "metadata",
                " ".join(
                    (
                        document.title,
                        document.workdir,
                        document.model_provider,
                        document.client_kind,
                    )
                ),
            )
            fields = tuple(precomputed_fields) + (metadata_field,)
        else:
            fields = _memory_fields(document)
        memory_document = _MemoryDocument(
            canonical,
            fields,
            "\x01".join(field[3] for field in fields),
            frozenset(
                token
                for _field_name, tokens, _grams, _field_text in fields
                for token in tokens
            ),
        )
        self._documents[canonical] = memory_document
        for _field_name, _tokens, grams, _field_text in memory_document.fields:
            for gram in grams:
                # Postings are keyed directly by gram (no prefix); the dict
                # holds only gram postings, so formatting a ``g:`` prefix on
                # every membership just allocates a throwaway string.
                self._postings[gram].add(canonical)
                # Short terms need no separate index: the resident token set
                # is cheap to check and avoids a large low-selectivity posting.

    def _remove_memory_document_locked(self, session_id: str) -> None:
        existing = self._documents.pop(str(session_id or ""), None)
        if existing is None:
            return
        canonical = existing.session_id
        for _field_name, _tokens, grams, _field_text in existing.fields:
            for gram in grams:
                posting = self._postings.get(gram)
                if posting is not None:
                    posting.discard(canonical)
                    if not posting:
                        self._postings.pop(gram, None)

    def _apply_memory_documents(
        self,
        documents: Sequence[SearchDocument],
        *,
        precomputed: Mapping[str, tuple] | None = None,
    ) -> None:
        with self._lock:
            for document in documents:
                fields = precomputed.get(document.session_id) if precomputed else None
                self._add_memory_document_locked(document, precomputed_fields=fields)
            self._memory_loaded = True

    def _snapshot_path(self) -> Path:
        return self.path.with_name(self.path.name + _SNAPSHOT_SUFFIX)

    def _write_snapshot(
        self,
        connection: sqlite3.Connection | None = None,
        *,
        force: bool = True,
    ) -> bool:
        """Persist the resident index so cold restarts skip tokenisation.

        The payload contains only built-in types (dicts, tuples, frozensets of
        plain strings), so unpickling it can never execute code. Writes go to
        a temporary file and are swapped in atomically; a crash between the
        SQLite commit and this write only costs a re-parse of the affected
        documents on the next load, never correctness.
        """

        now = float(self.clock())
        if not force and (now - self._snapshot_last_write) < _SNAPSHOT_MIN_INTERVAL:
            return False
        own = connection is None
        conn = connection or self._connect()
        try:
            with self._lock:
                if not self._memory_loaded or not self._documents:
                    return False
                stamps = {
                    str(row[0]): float(row[1] or 0.0)
                    for row in conn.execute(
                        f"SELECT session_id, indexed_at FROM {_DOC_TABLE}"
                    ).fetchall()
                }
                if not stamps:
                    return False
                # Store only the minimal per-field data (name, tokens, grams).
                # ``field_text``, ``search_text`` and ``token_set`` are all
                # derivable from the token tuples and are rebuilt on load, so
                # persisting them would triple the snapshot size for nothing.
                documents_payload = {
                    session_id: tuple(
                        (name, tokens, grams)
                        for name, tokens, grams, _field_text in document.fields
                    )
                    for session_id, document in self._documents.items()
                }
                postings_payload = {
                    key: set(value) for key, value in self._postings.items()
                }
            target = self._snapshot_path()
            temporary = target.with_name(target.name + ".tmp")
            try:
                with open(temporary, "wb") as handle:
                    pickle.dump(
                        {
                            "version": _SNAPSHOT_VERSION,
                            "stamps": stamps,
                            "documents": documents_payload,
                            "postings": postings_payload,
                        },
                        handle,
                        protocol=pickle.HIGHEST_PROTOCOL,
                    )
                os.replace(temporary, target)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                return False
            self._snapshot_last_write = now
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            if own:
                conn.close()

    def _load_snapshot_locked(self) -> bool:
        """Load resident state from the snapshot, reconciled with SQLite.

        Documents whose ``indexed_at`` stamp no longer matches SQLite (or that
        are missing from it) are rebuilt from their rows, so a stale snapshot
        degrades to partial re-indexing rather than wrong results.
        """

        try:
            with open(self._snapshot_path(), "rb") as handle:
                payload = pickle.load(handle)
        except (
            OSError,
            EOFError,
            pickle.UnpicklingError,
            AttributeError,
            ImportError,
            IndexError,
            TypeError,
            ValueError,
            RuntimeError,
        ):
            return False
        if not isinstance(payload, dict) or payload.get("version") != _SNAPSHOT_VERSION:
            return False
        documents = payload.get("documents")
        stamps = payload.get("stamps")
        postings = payload.get("postings")
        if not isinstance(documents, dict) or not isinstance(stamps, dict) or not isinstance(postings, dict):
            return False
        fresh: dict[str, float] = {}
        for key, value in stamps.items():
            try:
                fresh[str(key)] = float(value or 0.0)
            except (TypeError, ValueError):
                continue
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT session_id, indexed_at FROM {_DOC_TABLE}"
            ).fetchall()
        except sqlite3.Error:
            return False
        finally:
            connection.close()
        current: dict[str, float] = {}
        for row in rows:
            try:
                current[str(row[0])] = float(row[1] or 0.0)
            except (TypeError, ValueError):
                current[str(row[0])] = 0.0

        self._documents.clear()
        self._postings.clear()
        for session_id, entry in documents.items():
            canonical = str(session_id or "").strip()
            if not canonical or not isinstance(entry, tuple):
                continue
            fields: list[tuple[str, tuple[str, ...], frozenset[str], str]] = []
            token_accumulator: list[str] = []
            for field_entry in entry:
                if not isinstance(field_entry, tuple) or len(field_entry) != 3:
                    continue
                name, tokens, grams = field_entry
                if not isinstance(name, str) or not isinstance(tokens, (tuple, list)):
                    continue
                token_tuple = tuple(tokens)
                gram_set = (
                    frozenset(grams)
                    if isinstance(grams, (frozenset, set, list, tuple))
                    else frozenset()
                )
                fields.append((name, token_tuple, gram_set, "\x00".join(token_tuple)))
                token_accumulator.extend(token_tuple)
            if not fields:
                continue
            self._documents[canonical] = _MemoryDocument(
                canonical,
                tuple(fields),
                "\x01".join(field[3] for field in fields),
                frozenset(token_accumulator),
            )
        for key, value in postings.items():
            if isinstance(value, set):
                self._postings[str(key)] = value
            elif isinstance(value, (frozenset, list, tuple)):
                self._postings[str(key)] = set(value)

        # Reconcile: drop docs that vanished or changed since the snapshot.
        stale_ids = [sid for sid in current if fresh.get(sid) != current[sid]]
        removed_ids = [sid for sid in fresh if sid not in current]
        for session_id in removed_ids:
            self._remove_memory_document_locked(session_id)
        for session_id in self._documents:
            if session_id not in fresh:
                stale_ids.append(session_id)
        if not stale_ids:
            return True
        connection = self._connect()
        try:
            placeholders = ",".join("?" for _ in stale_ids)
            rows = connection.execute(
                f"SELECT session_id, fingerprints, user_text, assistant_text, "
                "tool_text, changed_paths, title, workdir, model_provider, "
                f"client_kind, searchable, indexed_at FROM {_DOC_TABLE} "
                f"WHERE session_id IN ({placeholders})",
                stale_ids,
            ).fetchall()
        except sqlite3.Error:
            return False
        finally:
            connection.close()
        for row in rows:
            self._add_memory_document_locked(self._document_from_row(row))
        return True

    def _load_memory(self) -> int:
        with self._lock:
            if self._memory_loaded:
                return len(self._documents)
            if self._load_snapshot_locked():
                self._memory_loaded = True
                return len(self._documents)
            connection = self._connect()
            documents: list[SearchDocument] = []
            try:
                rows = connection.execute(
                    f"SELECT session_id, fingerprints, user_text, assistant_text, "
                    "tool_text, changed_paths, title, workdir, model_provider, "
                    f"client_kind, searchable, indexed_at FROM {_DOC_TABLE}"
                ).fetchall()
                for row in rows:
                    documents.append(self._document_from_row(row))
            finally:
                connection.close()
            self._documents.clear()
            self._postings.clear()
            for document in documents:
                self._add_memory_document_locked(document)
            self._memory_loaded = True
            return len(self._documents)

    def load(self) -> dict[str, object]:
        """Load the durable snapshot into resident memory."""

        count = self._load_memory()
        return {
            "indexed": count,
            "indexAvailable": True,
            "memoryLoaded": True,
        }

    @property
    def memory_loaded(self) -> bool:
        with self._lock:
            return self._memory_loaded

    def _upsert_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        paths: Sequence[Path],
        *,
        title: str = "",
        workdir: str = "",
        model_provider: str = "",
        client_kind: str = "",
        parsed: tuple[str, str, str, tuple[str, ...]] | None = None,
    ) -> tuple[bool, SearchDocument | None]:
        canonical = str(session_id or "").strip()
        if not canonical:
            return False, None
        fingerprints = rollout_fingerprints(paths)
        encoded = self._encode_fingerprints(fingerprints)
        existing = connection.execute(
            f"SELECT fingerprints, user_text, assistant_text, tool_text, changed_paths, searchable, "
            "title, workdir, model_provider, client_kind "
            f"FROM {_DOC_TABLE} WHERE session_id = ?",
            (canonical,),
        ).fetchone()
        fts_missing = bool(
            self._fts_available
            and connection.execute(
                f"SELECT 1 FROM {_FTS_TABLE} WHERE session_id = ? LIMIT 1",
                (canonical,),
            ).fetchone()
            is None
        )
        old_searchable = ""
        if existing and self._decode_fingerprints(existing[0]) == fingerprints:
            (
                user_text,
                assistant_text,
                tool_text,
                changed_json,
                old_searchable,
                old_title,
                old_workdir,
                old_provider,
                old_client_kind,
            ) = existing[1:]
            if (
                not fts_missing
                and str(old_title or "") == str(title or "").strip()
                and str(old_workdir or "") == str(workdir or "").strip()
                and str(old_provider or "") == str(model_provider or "").strip()
                and str(old_client_kind or "") == str(client_kind or "").strip()
            ):
                return True, None
            try:
                loaded_paths = json.loads(changed_json or "[]")
                changed_paths = tuple(loaded_paths) if isinstance(loaded_paths, list) else ()
            except (TypeError, ValueError, json.JSONDecodeError):
                changed_paths = ()
        else:
            if parsed is not None:
                user_text, assistant_text, tool_text, changed_paths = parsed[:4]
            else:
                user_text, assistant_text, tool_text, changed_paths = parse_rollout(paths)
            changed_json = json.dumps(
                list(changed_paths), ensure_ascii=False, separators=(",", ":")
            )
        document = SearchDocument(
            session_id=canonical,
            user_text=user_text,
            assistant_text=assistant_text,
            tool_text=tool_text,
            changed_paths=tuple(str(item) for item in changed_paths),
            title=str(title or "").strip(),
            workdir=str(workdir or "").strip(),
            model_provider=str(model_provider or "").strip(),
            client_kind=str(client_kind or "").strip(),
        )
        searchable = document.searchable if self._fts_available else ""
        connection.execute(
            f"INSERT INTO {_DOC_TABLE} (session_id, fingerprints, user_text, assistant_text, "
            "tool_text, changed_paths, title, workdir, model_provider, client_kind, searchable, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET fingerprints=excluded.fingerprints, "
            "user_text=excluded.user_text, assistant_text=excluded.assistant_text, "
            "tool_text=excluded.tool_text, changed_paths=excluded.changed_paths, "
            "title=excluded.title, workdir=excluded.workdir, model_provider=excluded.model_provider, "
            "client_kind=excluded.client_kind, searchable=excluded.searchable, indexed_at=excluded.indexed_at",
            (
                canonical,
                encoded,
                document.user_text,
                document.assistant_text,
                document.tool_text,
                changed_json,
                document.title,
                document.workdir,
                document.model_provider,
                document.client_kind,
                searchable,
                float(self.clock()),
            ),
        )
        if self._fts_available and (
            fts_missing or not existing or str(old_searchable or "") != searchable
        ):
            connection.execute(f"DELETE FROM {_FTS_TABLE} WHERE session_id = ?", (canonical,))
            connection.execute(
                f"INSERT INTO {_FTS_TABLE}(session_id, content) VALUES (?, ?)",
                (canonical, searchable),
            )
        return True, document

    def upsert(
        self,
        session_id: str,
        paths: Sequence[Path],
        *,
        title: str = "",
        workdir: str = "",
        model_provider: str = "",
        client_kind: str = "",
    ) -> bool:
        self._load_memory()
        connection = self._connect()
        try:
            result, document = self._upsert_connection(
                connection,
                session_id,
                paths,
                title=title,
                workdir=workdir,
                model_provider=model_provider,
                client_kind=client_kind,
            )
            connection.commit()
            if document is not None:
                self._apply_memory_documents((document,))
                self._write_snapshot(connection, force=False)
            return result
        finally:
            connection.close()

    def sync(
        self,
        entries: Iterable[tuple[str, Sequence[Path], str, str, str, str]],
    ) -> int:
        """Upsert many documents in one SQLite transaction."""
        values = list(entries)
        return self.sync_batches(
            values,
            total=len(values),
            batch_size=max(1, len(values)),
        )

    def _prefetch_parses(
        self,
        connection: sqlite3.Connection,
        values: list[tuple[str, Sequence[Path], str, str, str, str]],
        *,
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, tuple[str, str, str, tuple[str, ...], tuple]]:
        """Parse stale rollouts in parallel before the upsert loop.

        Only entries whose fingerprints no longer match the snapshot are sent
        to the pool, so incremental jobs (a handful of files) stay inline and
        never pay process startup. Returns an empty dict when the pool is
        unavailable or cancelled; the upsert loop then parses inline.
        """

        if len(values) < _PARALLEL_PARSE_MIN_ENTRIES or not _process_pool_allowed():
            return {}
        stale: list[tuple[str, list[str]]] = []
        seen: set[str] = set()
        for entry in values:
            canonical = str(entry[0] or "").strip()
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            fingerprints = rollout_fingerprints(entry[1])
            row = connection.execute(
                f"SELECT fingerprints FROM {_DOC_TABLE} WHERE session_id = ?",
                (canonical,),
            ).fetchone()
            if row is None or self._decode_fingerprints(row[0]) != fingerprints:
                stale.append((canonical, [str(item) for item in entry[1]]))
        return _parse_entries_parallel(stale, cancelled=cancelled)

    def sync_batches(
        self,
        entries: Iterable[tuple[str, Sequence[Path], str, str, str, str]],
        *,
        total: int | None = None,
        batch_size: int = 24,
        progress_callback: Callable[[int, int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """Update the snapshot in cancellable, resident-memory batches."""

        self._load_memory()
        values = list(entries)
        connection = self._connect()
        processed = 0
        count = 0
        pending: list[SearchDocument] = []
        precomputed: dict[str, tuple] = {}
        last_reported = 0
        safe_batch_size = max(1, int(batch_size))
        expected_total = max(0, int(total)) if total is not None else 0
        mutated = 0

        def commit_batch() -> None:
            nonlocal last_reported
            if pending:
                connection.commit()
                self._apply_memory_documents(tuple(pending), precomputed=precomputed)
                pending.clear()
                precomputed.clear()
            elif processed:
                connection.commit()
            if callable(progress_callback) and processed != last_reported:
                progress_callback(
                    processed,
                    expected_total,
                    self.count(connection=connection),
                )
                last_reported = processed

        try:
            parsed_cache = self._prefetch_parses(
                connection, values, cancelled=cancelled
            )
            for session_id, paths, title, workdir, provider, client_kind in values:
                if callable(cancelled) and cancelled():
                    break
                canonical = str(session_id or "").strip()
                parsed = parsed_cache.get(canonical)
                result, document = self._upsert_connection(
                    connection,
                    session_id,
                    paths,
                    title=title,
                    workdir=workdir,
                    model_provider=provider,
                    client_kind=client_kind,
                    parsed=parsed,
                )
                processed += 1
                count += int(result)
                if document is not None:
                    pending.append(document)
                    # The worker pre-tokenised the four large fields; carry
                    # them through so the main process skips re-tokenisation.
                    if parsed is not None and len(parsed) > 4 and parsed[4]:
                        precomputed[document.session_id] = parsed[4]
                    mutated += 1
                if processed % safe_batch_size == 0:
                    commit_batch()
            commit_batch()
            if mutated or not self._snapshot_path().exists():
                self._write_snapshot(
                    connection,
                    force=len(values) >= _PARALLEL_PARSE_MIN_ENTRIES,
                )
            return count
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def remove_missing(self, session_ids: Iterable[str]) -> None:
        keep = {str(item or "").strip() for item in session_ids if str(item or "").strip()}
        connection = self._connect()
        try:
            rows = connection.execute(f"SELECT session_id FROM {_DOC_TABLE}").fetchall()
            stale = [str(row[0]) for row in rows if str(row[0]) not in keep]
            if stale:
                placeholders = ",".join("?" for _ in stale)
                connection.execute(f"DELETE FROM {_DOC_TABLE} WHERE session_id IN ({placeholders})", stale)
                if self._fts_available:
                    connection.execute(f"DELETE FROM {_FTS_TABLE} WHERE session_id IN ({placeholders})", stale)
            connection.commit()
            with self._lock:
                for session_id in stale:
                    self._remove_memory_document_locked(session_id)
            if stale:
                self._write_snapshot(connection, force=False)
        finally:
            connection.close()

    def search(
        self,
        query: str,
        *,
        limit: int = MAX_MATCHES,
        session_ids: Iterable[str] | None = None,
        load: bool = True,
    ) -> dict[str, object]:
        """Search resident postings in milliseconds after the snapshot is loaded."""

        if load and not self.memory_loaded:
            self._load_memory()
        with self._lock:
            allowed = (
                {str(item or "").strip() for item in session_ids if str(item or "").strip()}
                if session_ids is not None
                else set(self._documents)
            )
            terms = search_terms(query)
            if not self._memory_loaded:
                return {
                    "query": str(query or ""),
                    "matches": [],
                    "indexed": 0,
                    "indexAvailable": True,
                    "memoryLoaded": False,
                }
            if not terms:
                return {
                    "query": str(query or ""),
                    "matches": [],
                    "indexed": len(self._documents),
                    "indexAvailable": True,
                    "memoryLoaded": True,
                }
            candidates = set(allowed)
            for term in terms:
                grams = _token_grams(term)
                if not grams:
                    continue
                current: set[str] | None = None
                for gram in grams:
                    posting = self._postings.get(gram, set())
                    current = set(posting) if current is None else current & posting
                    if not current:
                        break
                candidates &= current or set()
                if not candidates:
                    break

            weights = {"file": 12, "user": 10, "assistant": 8, "tool": 5, "metadata": 3}
            matches: list[dict[str, object]] = []
            for session_id in candidates:
                document = self._documents.get(session_id)
                if document is None:
                    continue
                # Exact-token membership is a sufficient (and much cheaper)
                # verification than a substring scan over ``search_text``;
                # only fall back to the scan when some term is not an exact
                # token of the document.
                token_set = document.token_set
                if not all(term in token_set for term in terms):
                    if not all(term in document.search_text for term in terms):
                        continue
                matched_kinds: set[str] = set()
                score = 0.0
                all_terms_match = True
                for term in terms:
                    term_matched = False
                    best_term_score = 0.0
                    for field_name, tokens, _grams, field_text in document.fields:
                        field_matched = term in field_text
                        if not field_matched:
                            continue
                        term_matched = True
                        matched_kinds.add(field_name)
                        field_score = weights[field_name]
                        if term in tokens:
                            field_score *= 2.0
                        elif any(token.startswith(term) for token in tokens):
                            field_score *= 1.4
                        best_term_score = max(best_term_score, field_score)
                    if not term_matched:
                        all_terms_match = False
                        break
                    score += best_term_score
                if not all_terms_match:
                    continue
                matches.append(
                    {
                        "sessionId": session_id,
                        "kinds": [
                            name
                            for name in ("user", "assistant", "tool", "file", "metadata")
                            if name in matched_kinds
                        ],
                        "score": round(score, 3),
                    }
                )
            matches.sort(
                key=lambda item: (-float(item.get("score") or 0), str(item.get("sessionId") or ""))
            )
            return {
                "query": str(query or ""),
                "matches": matches[: max(1, int(limit))],
                "indexed": len(self._documents),
                "indexAvailable": True,
                "memoryLoaded": True,
            }

    def count(self, *, connection: sqlite3.Connection | None = None) -> int:
        own = connection is None
        if connection is None and self.memory_loaded:
            with self._lock:
                return len(self._documents)
        conn = connection or self._connect()
        try:
            return int(conn.execute(f"SELECT count(*) FROM {_DOC_TABLE}").fetchone()[0])
        finally:
            if own:
                conn.close()

    def status(self) -> dict[str, object]:
        try:
            if self.memory_loaded:
                count = self.count()
            else:
                connection = self._connect()
                try:
                    count = self.count(connection=connection)
                finally:
                    connection.close()
        except (OSError, sqlite3.Error):
            return {"available": False, "indexed": 0, "memoryLoaded": False}
        return {
            "available": True,
            "indexed": count,
            "memoryLoaded": self.memory_loaded,
        }


__all__ = [
    "MAX_DOCUMENT_CHARS",
    "MAX_FIELD_CHARS",
    "SearchDocument",
    "SessionSearchIndex",
    "normalise_workdir",
    "parse_rollout",
    "rollout_fingerprints",
    "search_terms",
    "workdir_identity",
]
