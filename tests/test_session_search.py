from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from codex_usage_hud.core import session_search as session_search_module
from codex_usage_hud.core.session_search import SessionSearchIndex, parse_rollout


def test_rollout_search_reads_late_content_beyond_two_megabytes(tmp_path: Path) -> None:
    rollout = tmp_path / "large.jsonl"
    late_marker = "late-content-marker"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "x" * (3 * 1024 * 1024),
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"text": late_marker}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "search.sqlite")
    index.upsert("session", (rollout,))

    assert index.search(late_marker)["matches"]


def test_search_works_without_fts5(tmp_path: Path) -> None:
    rollout = tmp_path / "fallback.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "fallback-marker"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "fallback.sqlite")
    index._fts_available = False
    index.upsert("session", (rollout,))

    assert index.search("fallback-marker")["matches"]


def test_read_tool_paths_are_not_reported_as_modified_files(tmp_path: Path) -> None:
    rollout = tmp_path / "read.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "read_file",
                    "arguments": json.dumps(
                        {"path": "E:/Project/codex-usage-hud/src/active_session.py"}
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _user, _assistant, _tool, changed_paths = parse_rollout((rollout,))

    assert not changed_paths


def test_loaded_search_uses_resident_postings_without_reopening_sqlite(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "resident.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "find renderer_assets active_session",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "resident.sqlite")
    index.upsert("session", (rollout,))

    with patch.object(index, "_connect", side_effect=AssertionError("disk query")):
        result = index.search("active_session")

    assert result["memoryLoaded"] is True
    assert result["matches"][0]["sessionId"] == "session"


def test_search_terms_support_fuzzy_path_fragments(tmp_path: Path) -> None:
    rollout = tmp_path / "path.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "arguments": "*** Update File: E:/Project/codex-usage-hud/src/codex_usage_hud/renderer_assets/active_session.py",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    index = SessionSearchIndex(tmp_path / "path.sqlite")
    index.upsert("session", (rollout,))

    result = index.search("renderer active_sess")
    assert result["matches"]
    assert "file" in result["matches"][0]["kinds"]


def _write_rollout(path: Path, message: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": message},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_snapshot_restores_resident_index_on_cold_restart(tmp_path: Path) -> None:
    rollout = tmp_path / "snap.jsonl"
    _write_rollout(rollout, "snapshot-cold-restart-marker")

    index = SessionSearchIndex(tmp_path / "snap.sqlite")
    index.upsert("session", (rollout,))
    assert index._snapshot_path().exists()

    fresh = SessionSearchIndex(tmp_path / "snap.sqlite")
    with patch.object(
        session_search_module,
        "_memory_fields",
        side_effect=AssertionError("re-tokenised on cold restart"),
    ):
        loaded = fresh.load()

    assert loaded["memoryLoaded"] is True
    assert fresh.search("snapshot-cold-restart-marker")["matches"]


def test_clear_index_removes_database_snapshot_and_journal_artifacts(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "clear.jsonl"
    _write_rollout(rollout, "clear-index-marker")
    database = tmp_path / "clear.sqlite"
    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))
    # Exercise the companion-file accounting even though SQLite normally
    # removes these files after a short transaction.
    for suffix in ("-wal", "-shm", "-journal", ".tmp"):
        database.with_name(database.name + suffix).write_bytes(b"temporary")
    snapshot_tmp = index._snapshot_path().with_name(
        index._snapshot_path().name + ".tmp"
    )
    snapshot_tmp.write_bytes(b"temporary")

    assert index.disk_usage_bytes() > 0
    result = index.clear_index()

    assert result["removedFiles"] >= 2
    assert index.disk_usage_bytes() == 0
    assert index._documents == {}
    assert index._postings == {}
    assert index.memory_loaded is False
    assert all(not path.exists() for path in index.index_artifact_paths())


def test_snapshot_reconciles_with_newer_sqlite_rows(tmp_path: Path) -> None:
    rollout = tmp_path / "reconcile.jsonl"
    _write_rollout(rollout, "older-content-marker")
    database = tmp_path / "reconcile.sqlite"

    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))

    # Simulate a post-snapshot write by bumping indexed_at and the text.
    import sqlite3 as sqlite3_module

    connection = sqlite3_module.connect(database)
    connection.execute(
        "UPDATE session_search_documents SET user_text = ?, indexed_at = indexed_at + 10 "
        "WHERE session_id = ?",
        ("newer-content-marker", "session"),
    )
    connection.commit()
    connection.close()

    fresh = SessionSearchIndex(database)
    fresh.load()
    assert not fresh.search("older-content-marker")["matches"]
    assert fresh.search("newer-content-marker")["matches"]


def test_snapshot_reconcile_preserves_field_kinds(tmp_path: Path) -> None:
    """Rebuilding a stale doc from SQLite must not shift its text fields.

    ``_document_from_row`` reads a fixed column order that includes
    ``fingerprints`` at index 1; the text fields start at index 2. A regression
    that reads user_text from index 1 would store the fingerprint JSON as user
    text and drop tool_text entirely, so each marker must land in its own kind.
    """
    rollout = tmp_path / "kinds.jsonl"
    user_marker = "reconcile-user-marker"
    assistant_marker = "reconcile-assistant-marker"
    tool_marker = "reconcile-tool-marker"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": user_marker},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "agent_message", "content": [{"text": assistant_marker}]},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "bash", "command": tool_marker},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "kinds.sqlite"

    index = SessionSearchIndex(database)
    index.upsert("session", (rollout,))

    import sqlite3 as sqlite3_module

    connection = sqlite3_module.connect(database)
    connection.execute(
        "UPDATE session_search_documents SET indexed_at = indexed_at + 10 WHERE session_id = ?",
        ("session",),
    )
    connection.commit()
    connection.close()

    fresh = SessionSearchIndex(database)
    fresh.load()

    def kinds_of(marker: str) -> list[str]:
        matches = fresh.search(marker)["matches"]
        return matches[0]["kinds"] if matches else []

    assert "user" in kinds_of(user_marker)
    assert "assistant" in kinds_of(assistant_marker)
    # Tool text is the field a column-shift bug would silently drop.
    assert "tool" in kinds_of(tool_marker)


def test_two_character_cjk_query_matches(tmp_path: Path) -> None:
    rollout = tmp_path / "cjk.jsonl"
    _write_rollout(rollout, "今天我们讨论会话管理的性能优化问题")

    index = SessionSearchIndex(tmp_path / "cjk.sqlite")
    index.upsert("session", (rollout,))

    assert index.search("会话")["matches"]
    assert index.search("性能")["matches"]


def test_large_batch_parses_in_worker_processes(tmp_path: Path) -> None:
    entries = []
    for number in range(30):
        rollout = tmp_path / f"parallel-{number}.jsonl"
        _write_rollout(rollout, f"parallel-worker-marker-{number}")
        entries.append((f"session-{number}", (rollout,), "", "", "", ""))

    index = SessionSearchIndex(tmp_path / "parallel.sqlite")
    assert session_search_module._process_pool_allowed()
    processed = index.sync_batches(entries, total=len(entries), batch_size=8)

    assert processed == 30
    assert index.count() == 30
    assert index.search("parallel-worker-marker-17")["matches"][0]["sessionId"] == "session-17"


def test_ensure_scan_index_covers_stamp_scans_and_reports_repairs(tmp_path: Path) -> None:
    # The document table stores multi-megabyte text columns, so a two-column
    # full scan walks every overflow page of a multi-GB database (~8.5 s
    # measured). The covering index keeps the warm job's stamp scans off that
    # path, and must be idempotent so the background finalize can call it on
    # every completion.
    import sqlite3

    rollout = tmp_path / "covered.jsonl"
    _write_rollout(rollout, "covering-index-marker")

    index = SessionSearchIndex(tmp_path / "covered.sqlite")
    index.upsert("session", (rollout,))

    assert index.ensure_scan_index() is True
    assert index.ensure_scan_index() is True  # idempotent

    connection = sqlite3.connect(index.path)
    try:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert session_search_module._STAMPS_INDEX in names
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT session_id, indexed_at "
            "FROM session_search_documents"
        ).fetchall()
        assert any("COVERING INDEX" in str(row) for row in plan)
    finally:
        connection.close()

    # A clean cold load of a matching snapshot reports nothing reconciled.
    result = index.load()
    assert result["memoryLoaded"] is True
    assert result["reconciled"] == 0
