from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_usage_hud.core.session_materializer import (
    SessionMaterializationError,
    materialize_forked_rollout,
)


SOURCE_ID = "10000000-0000-4000-8000-000000000001"
TARGET_ID = "10000000-0000-4000-8000-000000000002"
ROOT_ID = "10000000-0000-4000-8000-000000000003"


def _write_rollout(path: Path, records: list[dict[str, object]]) -> int:
    encoded = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    encoded_bytes = encoded.encode("utf-8")
    path.write_bytes(encoded_bytes)
    return len(encoded_bytes)


def _meta(session_id: str, **payload: object) -> dict[str, object]:
    return {
        "type": "session_meta",
        "payload": {"session_id": session_id, "id": session_id, **payload},
        "ordinal": 0,
    }


def _event(kind: str) -> dict[str, object]:
    return {"type": "event_msg", "payload": {"kind": kind}, "ordinal": 99}


def _identity_event(thread_id: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "payload": {
            "thread_id": thread_id,
            "item": {
                "parent_thread_id": thread_id,
                "id": thread_id,
            },
        },
        "ordinal": 99,
    }


def test_materialize_flattens_source_and_target_and_rewrites_ordinals(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    source_size = _write_rollout(
        source,
        [_meta(SOURCE_ID, model_provider="openai-custom"), _event("source")],
    )
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                model_provider="jianzhile",
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": source_size},
                forked_from_id=SOURCE_ID,
            ),
            _event("target"),
        ],
    )

    materialize_forked_rollout(
        target_id=TARGET_ID,
        source_id=SOURCE_ID,
        target_path=target,
        rollout_paths={SOURCE_ID: source, TARGET_ID: target},
    )

    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "session_meta",
        "event_msg",
        "event_msg",
    ]
    assert [record["payload"]["kind"] for record in records[1:]] == ["source", "target"]
    assert [record["ordinal"] for record in records] == [0, 1, 2]
    assert records[0]["payload"]["id"] == TARGET_ID
    assert "history_base" not in records[0]["payload"]
    assert "forked_from_id" not in records[0]["payload"]


def test_materialize_rebinds_event_thread_id_to_target_without_rewriting_item_ids(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    source_size = _write_rollout(
        source,
        [_meta(SOURCE_ID), _identity_event(SOURCE_ID)],
    )
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": source_size},
            ),
        ],
    )

    materialize_forked_rollout(
        target_id=TARGET_ID,
        source_id=SOURCE_ID,
        target_path=target,
        rollout_paths={SOURCE_ID: source, TARGET_ID: target},
    )

    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    payload = records[1]["payload"]
    assert payload["thread_id"] == TARGET_ID
    assert payload["item"]["parent_thread_id"] == TARGET_ID
    assert payload["item"]["id"] == SOURCE_ID


def test_materialize_resolves_nested_paginated_lineage(tmp_path: Path) -> None:
    root = tmp_path / "root.jsonl"
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    root_size = _write_rollout(root, [_meta(ROOT_ID), _event("root")])
    source_size = _write_rollout(
        source,
        [
            _meta(
                SOURCE_ID,
                history_mode="paginated",
                history_base={"thread_id": ROOT_ID, "end_byte_offset": root_size},
            ),
            _event("source"),
        ],
    )
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": source_size},
            ),
            _event("target"),
        ],
    )

    materialize_forked_rollout(
        target_id=TARGET_ID,
        source_id=SOURCE_ID,
        target_path=target,
        rollout_paths={ROOT_ID: root, SOURCE_ID: source, TARGET_ID: target},
    )

    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert [record["payload"].get("kind") for record in records[1:]] == [
        "root",
        "source",
        "target",
    ]


def test_materialize_rejects_missing_source_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": 10},
            ),
            _event("target"),
        ],
    )
    before = target.read_bytes()

    with pytest.raises(SessionMaterializationError, match="源 rollout 不存在"):
        materialize_forked_rollout(
            target_id=TARGET_ID,
            source_id=SOURCE_ID,
            target_path=target,
            rollout_paths={TARGET_ID: target},
        )

    assert target.read_bytes() == before


def test_materialize_rejects_non_line_boundary_without_touching_target(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    source_size = _write_rollout(source, [_meta(SOURCE_ID), _event("source")])
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": source_size - 1},
            ),
            _event("target"),
        ],
    )
    before = target.read_bytes()

    with pytest.raises(SessionMaterializationError, match="boundary"):
        materialize_forked_rollout(
            target_id=TARGET_ID,
            source_id=SOURCE_ID,
            target_path=target,
            rollout_paths={SOURCE_ID: source, TARGET_ID: target},
        )

    assert target.read_bytes() == before


def test_materialize_reports_atomic_replace_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    source_size = _write_rollout(source, [_meta(SOURCE_ID), _event("source")])
    _write_rollout(
        target,
        [
            _meta(
                TARGET_ID,
                history_mode="paginated",
                history_base={"thread_id": SOURCE_ID, "end_byte_offset": source_size},
            ),
            _event("target"),
        ],
    )

    def fail_replace(_temporary: str, _target: str) -> None:
        raise PermissionError("file is locked")

    monkeypatch.setattr("codex_usage_hud.core.session_materializer.os.replace", fail_replace)

    with pytest.raises(SessionMaterializationError, match="PermissionError: file is locked"):
        materialize_forked_rollout(
            target_id=TARGET_ID,
            source_id=SOURCE_ID,
            target_path=target,
            rollout_paths={SOURCE_ID: source, TARGET_ID: target},
        )
