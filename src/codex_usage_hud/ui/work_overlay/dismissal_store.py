"""Persistent dismissal state for desktop work-overlay items."""

from __future__ import annotations

import time
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Callable

from ...config import read_json_object, write_json_object

WORK_OVERLAY_DISMISSAL_FILENAME = "overlay-dismissals.json"
WORK_OVERLAY_DISMISSAL_SCHEMA_VERSION = 1
WORK_OVERLAY_DISMISSAL_MAX_ENTRIES = 512


class WorkOverlayDismissalStore(MutableMapping[str, str]):
    """A bounded, best-effort mapping that survives helper process restarts."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] | None = None,
        max_entries: int = WORK_OVERLAY_DISMISSAL_MAX_ENTRIES,
    ) -> None:
        self.path = Path(path)
        self._clock = clock or time.time
        self._max_entries = max(1, int(max_entries))
        self._items: dict[str, str] = {}
        self._dismissed_at: dict[str, float] = {}
        self._load()

    def __getitem__(self, key: str) -> str:
        return self._items[key]

    def __setitem__(self, key: str, value: str) -> None:
        item_id = str(key).strip()
        if not item_id:
            raise KeyError("dismissal item id must not be empty")
        self._items[item_id] = str(value)
        self._dismissed_at[item_id] = float(self._clock())
        self._prune()
        self._persist()

    def __delitem__(self, key: str) -> None:
        del self._items[key]
        self._dismissed_at.pop(key, None)
        self._persist()

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def _load(self) -> None:
        raw = read_json_object(self.path)
        records = raw.get("items")
        if not isinstance(records, dict):
            return
        for raw_id, raw_record in records.items():
            item_id = str(raw_id).strip()
            if not item_id or not isinstance(raw_record, dict):
                continue
            key = str(raw_record.get("key") or "")
            if not key:
                continue
            try:
                dismissed_at = float(raw_record.get("dismissedAt") or 0.0)
            except (TypeError, ValueError):
                dismissed_at = 0.0
            self._items[item_id] = key
            self._dismissed_at[item_id] = dismissed_at
        self._prune()

    def _prune(self) -> None:
        overflow = len(self._items) - self._max_entries
        if overflow <= 0:
            return
        oldest = sorted(
            self._items,
            key=lambda item_id: self._dismissed_at.get(item_id, 0.0),
        )[:overflow]
        for item_id in oldest:
            self._items.pop(item_id, None)
            self._dismissed_at.pop(item_id, None)

    def _persist(self) -> None:
        payload = {
            "schemaVersion": WORK_OVERLAY_DISMISSAL_SCHEMA_VERSION,
            "items": {
                item_id: {
                    "key": self._items[item_id],
                    "dismissedAt": self._dismissed_at.get(item_id, 0.0),
                }
                for item_id in sorted(self._items)
            },
        }
        try:
            write_json_object(self.path, payload)
        except OSError:
            # Dismissal is still effective for this process when the sidecar
            # cannot be written (locked/read-only runtime directory, etc.).
            return


__all__ = [
    "WORK_OVERLAY_DISMISSAL_FILENAME",
    "WORK_OVERLAY_DISMISSAL_MAX_ENTRIES",
    "WorkOverlayDismissalStore",
]
