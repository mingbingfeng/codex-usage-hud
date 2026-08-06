from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from codex_usage_hud.core.pricing_snapshots import PricingSnapshotLedger
from codex_usage_hud.core.calculator import UsageCalculator
from codex_usage_hud.core.parser import CostEstimator, JsonlSessionParser


class PricingSnapshotLedgerTests(unittest.TestCase):
    @staticmethod
    def _records() -> list[dict[str, object]]:
        occurred_at = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        return [
            {
                "type": "session_meta",
                "timestamp": "2026-08-02T11:59:00Z",
                "_dt": datetime(2026, 8, 2, 11, 59, tzinfo=timezone.utc),
                "_line": 1,
                "payload": {"id": "session-priced", "model_provider": "custom"},
            },
            {
                "type": "turn_context",
                "timestamp": "2026-08-02T11:59:30Z",
                "_dt": datetime(2026, 8, 2, 11, 59, 30, tzinfo=timezone.utc),
                "_line": 2,
                "payload": {"model": "gpt-test"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-08-02T12:00:00Z",
                "_dt": occurred_at,
                "_line": 3,
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1_000_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 0,
                        }
                    },
                },
            },
        ]

    @staticmethod
    def _estimator(ledger: PricingSnapshotLedger, input_price: float) -> CostEstimator:
        return CostEstimator(
            UsageCalculator(
                {
                    "custom/gpt-test": {
                        "model": "gpt-test",
                        "provider": "custom",
                        "input": input_price,
                        "cached_input": input_price,
                        "cache_write": 0,
                        "output": 1,
                        "reasoning": 1,
                    }
                },
                pricing_versions=[
                    {
                        "version_id": f"price-{input_price}",
                        "model": "gpt-test",
                        "provider": "custom",
                        "input": input_price,
                        "cached_input": input_price,
                        "cache_write": 0,
                        "output": 1,
                        "reasoning": 1,
                        "effective_at": "2026-08-01T00:00:00Z",
                        "created_at": "2026-08-01T00:00:00Z",
                        "created_by": "user_edit",
                        "source": "manual",
                    }
                ],
            ),
            pricing_ledger=ledger,
        )

    def test_jsonl_event_keeps_first_snapshot_across_price_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = PricingSnapshotLedger(Path(temp_dir) / "pricing.sqlite3")
            first_parser = JsonlSessionParser(
                cost_estimator=self._estimator(ledger, 1.0)
            )
            first = first_parser.usage_events(self._records())
            self.assertEqual(first[0].cost_usd, 1.0)
            self.assertEqual(first[0].price_status, "versioned")

            second_estimator = self._estimator(ledger, 2.0)
            second_parser = JsonlSessionParser(cost_estimator=second_estimator)
            frozen = second_parser.usage_events(self._records())
            self.assertEqual(frozen[0].cost_usd, 1.0)
            self.assertEqual(frozen[0].price_snapshot["version_id"], "price-1.0")

            preview = ledger.preview_recalculation(
                second_estimator.recalculate_snapshot,
                provider="custom",
                model="gpt-test",
            )
            self.assertEqual(preview.previous_total_usd, 1.0)
            self.assertEqual(preview.next_total_usd, 2.0)
            ledger.apply_recalculation(preview)
            recalculated = second_parser.usage_events(self._records())
            self.assertEqual(recalculated[0].cost_usd, 2.0)

    def test_session_snapshot_writes_share_one_database_transaction(self) -> None:
        class CountingLedger(PricingSnapshotLedger):
            def __init__(self, path: Path) -> None:
                self.connection_count = 0
                super().__init__(path)

            def _connect(self):
                self.connection_count += 1
                return super()._connect()

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = CountingLedger(Path(temp_dir) / "pricing.sqlite3")
            ledger.connection_count = 0
            records = self._records()
            records.append(
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-02T12:01:00Z",
                    "_dt": datetime(2026, 8, 2, 12, 1, tzinfo=timezone.utc),
                    "_line": 4,
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 2_000_000,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                            }
                        },
                    },
                }
            )
            parser = JsonlSessionParser(cost_estimator=self._estimator(ledger, 1.0))

            events = parser.usage_events(records)

            self.assertEqual(len(events), 2)
            self.assertEqual(ledger.connection_count, 1)

    def test_first_snapshot_is_immutable_until_explicit_recalculation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = PricingSnapshotLedger(Path(temp_dir) / "pricing.sqlite3")
            occurred_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            event_key = ledger.event_key("session-1", 12, occurred_at)
            first = ledger.record_if_absent(
                event_key=event_key,
                session_id="session-1",
                event_line=12,
                occurred_at=occurred_at,
                provider="custom",
                model="gpt-test",
                base_url="",
                input_tokens=1_000_000,
                cached_input_tokens=0,
                cache_write_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cost_usd=1.0,
                status="versioned",
                price_snapshot={"versionId": "old"},
            )
            second = ledger.record_if_absent(
                event_key=event_key,
                session_id="session-1",
                event_line=12,
                occurred_at=occurred_at,
                provider="custom",
                model="gpt-test",
                base_url="",
                input_tokens=1_000_000,
                cached_input_tokens=0,
                cache_write_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cost_usd=2.0,
                status="versioned",
                price_snapshot={"versionId": "new"},
            )
            self.assertEqual(first.cost_usd, 1.0)
            self.assertEqual(second.cost_usd, 1.0)
            self.assertEqual(second.price_snapshot, {"version_id": "old"})
            self.assertIsNone(second.original_cost_usd)
            self.assertIsNone(second.original_price_snapshot)

            preview = ledger.preview_recalculation(
                lambda _row: (2.0, "versioned", {"versionId": "new"}),
                provider="custom",
                model="gpt-test",
            )
            self.assertEqual(preview.matched_count, 1)
            self.assertEqual(preview.changed_count, 1)
            self.assertEqual(ledger.get(event_key).cost_usd, 1.0)  # type: ignore[union-attr]

            result = ledger.apply_recalculation(preview)
            updated = ledger.get(event_key)
            self.assertEqual(result["changedCount"], 1)
            self.assertEqual(updated.cost_usd, 2.0)  # type: ignore[union-attr]
            self.assertEqual(updated.original_cost_usd, 1.0)  # type: ignore[union-attr]
            self.assertEqual(
                updated.original_price_snapshot,  # type: ignore[union-attr]
                {"version_id": "old"},
            )
            self.assertTrue(updated.recalculated_at)  # type: ignore[union-attr]

            second_preview = ledger.preview_recalculation(
                lambda _row: (3.0, "versioned", {"version_id": "newer"}),
                provider="custom",
                model="gpt-test",
            )
            ledger.apply_recalculation(second_preview)
            updated_again = ledger.get(event_key)
            self.assertEqual(updated_again.original_cost_usd, 1.0)  # type: ignore[union-attr]
            self.assertEqual(
                updated_again.original_price_snapshot,  # type: ignore[union-attr]
                {"version_id": "old"},
            )

    def test_snapshot_cache_is_bounded_and_queries_are_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = PricingSnapshotLedger(
                Path(temp_dir) / "pricing.sqlite3",
                max_cache_entries=2,
            )
            for line in range(1, 5):
                occurred_at = datetime(2026, 8, line, tzinfo=timezone.utc)
                ledger.record_if_absent(
                    event_key=ledger.event_key("session", line, occurred_at),
                    session_id="session",
                    event_line=line,
                    occurred_at=occurred_at,
                    provider="custom",
                    model="gpt-test",
                    base_url="",
                    input_tokens=1,
                    cached_input_tokens=0,
                    cache_write_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    cost_usd=float(line),
                    status="versioned",
                    price_snapshot={"version_id": f"v{line}"},
                )

            self.assertEqual(len(ledger._snapshot_cache), 2)
            ledger._snapshot_cache.clear()
            key = ledger.event_key(
                "session", 1, datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
            self.assertEqual(ledger.get(key).cost_usd, 1.0)  # type: ignore[union-attr]
            self.assertEqual(list(ledger._snapshot_cache), [key])

    def test_schema_v1_migration_compacts_snapshots_and_delays_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pricing.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE usage_price_snapshots (
                        event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                        event_line INTEGER NOT NULL, occurred_at TEXT NOT NULL,
                        provider TEXT NOT NULL, model TEXT NOT NULL,
                        base_url TEXT NOT NULL, input_tokens INTEGER NOT NULL,
                        cached_input_tokens INTEGER NOT NULL,
                        cache_write_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL,
                        reasoning_tokens INTEGER NOT NULL, cost_usd REAL,
                        status TEXT NOT NULL, price_snapshot_json TEXT NOT NULL,
                        original_cost_usd REAL,
                        original_price_snapshot_json TEXT NOT NULL,
                        created_at TEXT NOT NULL, recalculated_at TEXT NOT NULL
                    );
                    CREATE INDEX idx_usage_price_snapshots_scope
                        ON usage_price_snapshots(provider, model, occurred_at);
                    """
                )
                connection.execute(
                    "INSERT INTO usage_price_snapshots VALUES("
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "event",
                        "session",
                        1,
                        "2026-08-01T00:00:00Z",
                        "custom",
                        "gpt-test",
                        "",
                        1,
                        0,
                        0,
                        0,
                        0,
                        1.0,
                        "versioned",
                        json.dumps({"version_id": "v1"}),
                        1.0,
                        json.dumps({"version_id": "v1"}),
                        "2026-08-01T00:00:01Z",
                        "",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            ledger = PricingSnapshotLedger(path)
            stored = ledger.get("event")
            self.assertEqual(stored.price_snapshot, {"version_id": "v1"})  # type: ignore[union-attr]
            self.assertIsNone(stored.original_cost_usd)  # type: ignore[union-attr]
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(usage_price_snapshots)"
                    )
                }
            finally:
                connection.close()
            self.assertIn("version_id", columns)
            self.assertNotIn("price_snapshot_json", columns)

    def test_failed_batch_rolls_back_database_and_memory_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = PricingSnapshotLedger(Path(temp_dir) / "pricing.sqlite3")
            occurred_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            event_key = ledger.event_key("session", 1, occurred_at)

            with self.assertRaisesRegex(RuntimeError, "cancel batch"):
                with ledger.batch():
                    ledger.record_if_absent(
                        event_key=event_key,
                        session_id="session",
                        event_line=1,
                        occurred_at=occurred_at,
                        provider="custom",
                        model="gpt-test",
                        base_url="",
                        input_tokens=1,
                        cached_input_tokens=0,
                        cache_write_tokens=0,
                        output_tokens=0,
                        reasoning_tokens=0,
                        cost_usd=1.0,
                        status="versioned",
                        price_snapshot={"version_id": "test"},
                    )
                    raise RuntimeError("cancel batch")

            self.assertIsNone(ledger.get(event_key))
            connection = ledger._connect()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM usage_price_snapshots"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 0)

    def test_tail_preview_does_not_persist_price_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            key: value
                            for key, value in record.items()
                            if key not in {"_dt", "_line"}
                        },
                        ensure_ascii=False,
                    )
                    for record in self._records()
                )
                + "\n",
                encoding="utf-8",
            )
            ledger = PricingSnapshotLedger(Path(temp_dir) / "pricing.sqlite3")
            estimator = self._estimator(ledger, 1.0)
            parser = JsonlSessionParser(cost_estimator=estimator)
            parser.parse_file_tail_preview(path, session_id="session-priced")

            preview = ledger.preview_recalculation(
                estimator.recalculate_snapshot,
                provider="custom",
                model="gpt-test",
            )

        self.assertEqual(preview.matched_count, 0)

    def test_recalculation_scope_and_unavailable_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = PricingSnapshotLedger(Path(temp_dir) / "pricing.sqlite3")
            for line, provider in ((1, "a"), (2, "b")):
                occurred_at = datetime(2026, 8, line, tzinfo=timezone.utc)
                ledger.record_if_absent(
                    event_key=ledger.event_key("session", line, occurred_at),
                    session_id="session",
                    event_line=line,
                    occurred_at=occurred_at,
                    provider=provider,
                    model="gpt-test",
                    base_url="",
                    input_tokens=1,
                    cached_input_tokens=0,
                    cache_write_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    cost_usd=1.0,
                    status="versioned",
                    price_snapshot={"versionId": "old"},
                )
            preview = ledger.preview_recalculation(
                lambda _row: (None, "unavailable", None),
                provider="b",
            )
            self.assertEqual(preview.matched_count, 1)
            self.assertEqual(preview.changed_count, 1)
            self.assertEqual(preview.unavailable_count, 1)


if __name__ == "__main__":
    unittest.main()
