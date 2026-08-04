"""Incremental command and acknowledgement sidecar I/O for the overlay.

This owner understands the JSONL channel and versioned overlay contracts only.
It does not route actions, wake the runtime, supervise a helper, or touch Qt.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import overlay_ipc


CommandParser = Callable[[Mapping[str, object]], dict[str, object]]


@dataclass(slots=True)
class OverlayCommandReader:
    """Read complete, unseen command rows from an append-only JSONL sidecar."""

    offset: int = 0
    seen_request_ids: set[str] = field(default_factory=set)

    def read(
        self,
        command_path: Path,
        *,
        parse_command: CommandParser | None = None,
    ) -> list[dict[str, object]]:
        path = Path(command_path)
        try:
            stat = path.stat()
        except OSError:
            self.offset = 0
            return []
        if stat.st_size <= 0:
            self.offset = 0
            return []
        if stat.st_size < self.offset:
            self.offset = 0
        elif stat.st_size == self.offset:
            return []

        parser = parse_command or overlay_ipc.parse_command
        commands: list[dict[str, object]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self.offset)
                while True:
                    line_start = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        self.offset = line_start
                        break
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        try:
                            command = parser(payload)
                        except overlay_ipc.OverlayContractError:
                            continue
                        request_id = str(command.get("requestId") or "")
                        if request_id and request_id in self.seen_request_ids:
                            continue
                        if request_id:
                            self.seen_request_ids.add(request_id)
                        commands.append(command)
                    self.offset = handle.tell()
        except OSError:
            return []
        return commands

    def reset(self) -> None:
        self.offset = 0
        self.seen_request_ids.clear()


def append_acknowledgement(
    state_path: Path,
    command: Mapping[str, object],
    *,
    producer_instance_id: str,
    status: str,
    result: Mapping[str, object] | None = None,
    error: Mapping[str, object] | None = None,
) -> bool:
    """Append one validated v1 acknowledgement to the state sidecar."""

    request_id = str(command.get("requestId") or "")
    action = str(command.get("action") or "")
    if not request_id or not action:
        return False
    try:
        ack = overlay_ipc.ack_message(
            requestId=request_id,
            action=action,
            status=status,
            producerInstanceId=producer_instance_id,
            result=dict(result or {}),
            error=dict(error or {}),
        )
        path = overlay_ipc.ack_path(Path(state_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(ack, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    except (OSError, overlay_ipc.OverlayContractError):
        return False
    return True


__all__ = ["OverlayCommandReader", "append_acknowledgement"]
