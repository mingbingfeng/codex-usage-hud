import json
from pathlib import Path

from codex_usage_hud import overlay_ipc
from codex_usage_hud.overlay_command_channel import (
    OverlayCommandReader,
    append_acknowledgement,
)


def test_reader_preserves_partial_tail_and_deduplicates_request_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "work-overlay-commands.jsonl"
    reader = OverlayCommandReader()
    command = overlay_ipc.command_message(
        action="activateSession",
        requestId="request-1",
        producerInstanceId="helper-1",
    )
    encoded = json.dumps(command, ensure_ascii=False)
    split = len(encoded) // 2
    path.write_text(encoded[:split], encoding="utf-8")

    assert reader.read(path) == []
    assert reader.offset == 0

    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded[split:] + "\n" + encoded + "\n")

    assert reader.read(path) == [command]
    assert reader.read(path) == []


def test_reader_restarts_at_zero_after_sidecar_truncation(tmp_path: Path) -> None:
    path = tmp_path / "work-overlay-commands.jsonl"
    reader = OverlayCommandReader()
    first = overlay_ipc.command_message(action="first", requestId="first")
    second = overlay_ipc.command_message(action="s", requestId="s")
    path.write_text(
        json.dumps(first, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert reader.read(path) == [first]

    path.write_text(
        json.dumps(second, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert reader.read(path) == [second]


def test_append_acknowledgement_preserves_contract_and_rejects_invalid_status(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "work-overlay.json"
    command = overlay_ipc.command_message(
        action="activateSession",
        requestId="request-1",
        producerInstanceId="helper-1",
    )

    assert append_acknowledgement(
        state_path,
        command,
        producer_instance_id="producer-1",
        status="completed",
        result={"backend": "cdp"},
    )
    ack_path = overlay_ipc.ack_path(state_path)
    ack = overlay_ipc.parse_ack(json.loads(ack_path.read_text(encoding="utf-8")))
    assert ack["requestId"] == "request-1"
    assert ack["result"] == {"backend": "cdp"}
    assert not append_acknowledgement(
        state_path,
        command,
        producer_instance_id="producer-1",
        status="invalid",
    )
