from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from signalclassifier.analysis_runtime import SignalStore


class SignalStoreSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "signals.sqlite3"

    @staticmethod
    def signal(identifier, session, updated):
        return {"id": identifier, "session_id": session, "last_seen": updated, "analysis_at": updated,
                "analysis_settings": {"enabled": True}, "source_context": {"source_epoch": session},
                "decision": {"schema_version": 1, "label": "AM", "status": "candidate", "conclusions": {},
                             "stages": [{"id": "quality"}], "elapsed_ms": 1}}

    def old_database(self):
        values = [self.signal("a", "session-a", "2026-11-22T00:00:01Z"),
                  self.signal("b", "session-b", "2026-11-22T00:00:02Z")]
        encoded = {value["id"]: json.dumps(value) for value in values}
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE signals(id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.executemany("INSERT INTO signals VALUES(?,?,?)", [(value["id"], value["analysis_at"], encoded[value["id"]]) for value in values])
            conn.execute("INSERT INTO signals VALUES(?,?,?)", ("damaged", "2026-11-22T00:00:03Z", "not-json"))
        return encoded

    def test_old_three_column_database_is_backfilled_once_without_changing_payloads(self):
        encoded = self.old_database()
        store = SignalStore(self.path)
        with store.connect() as conn:
            self.assertIn("session_id", {row[1] for row in conn.execute("PRAGMA table_info(signals)")})
            rows = conn.execute("SELECT id,session_id,payload FROM signals ORDER BY id").fetchall()
            self.assertEqual(rows[0], ("a", "session-a", encoded["a"]))
            self.assertEqual(rows[1], ("b", "session-b", encoded["b"]))
            self.assertIsNone(rows[2][1])
            plan = conn.execute("EXPLAIN QUERY PLAN SELECT payload FROM signals WHERE session_id=? ORDER BY updated_at DESC,id LIMIT ?", ("session-a", 2)).fetchall()
            self.assertTrue(any("signals_session_updated_id" in row[3] for row in plan))
        self.assertEqual([row["id"] for row in store.list(session_id="session-a")], ["a"])
        # Opening a migrated database must not re-read every historical JSON.
        with mock.patch("signalclassifier.analysis_runtime.json.loads", side_effect=AssertionError("backfill repeated")):
            SignalStore(self.path)

    def test_session_filter_and_limit_run_before_any_payload_deserialization(self):
        store = SignalStore(self.path)
        for i in range(4):
            store.save(self.signal(f"a-{i}", "wanted", f"2026-11-22T00:00:0{i}Z"))
        # A newer malformed row in another session would break the former
        # read-all-then-filter implementation before it reached the target rows.
        with store.connect() as conn:
            conn.execute("INSERT INTO signals(id,updated_at,payload,session_id) VALUES(?,?,?,?)",
                         ("unrelated", "2026-11-23T00:00:00Z", "not-json", "other"))
        statements = []
        original_connect = store.connect
        original_loads = json.loads
        @contextmanager
        def traced():
            with original_connect() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        with mock.patch.object(store, "connect", side_effect=traced), \
             mock.patch("signalclassifier.analysis_runtime.json.loads", wraps=original_loads) as loads:
            values = store.list(2, "wanted")
        self.assertEqual([row["id"] for row in values], ["a-3", "a-2"])
        self.assertEqual(loads.call_count, 2)
        selects = [statement for statement in statements if statement.startswith("SELECT payload FROM signals")]
        self.assertEqual(len(selects), 1)
        self.assertIn("WHERE session_id='wanted'", selects[0])
        self.assertTrue(selects[0].endswith("LIMIT 2"))

    def test_save_upsert_updates_indexed_session_and_preserves_compact_history_retention(self):
        store = SignalStore(self.path)
        store.save(self.signal("same", "before", "2026-11-22T00:00:01Z"), "obs-1", max_history=2)
        store.save(self.signal("same", "after", "2026-11-22T00:00:02Z"), "obs-2", max_history=2)
        store.save(self.signal("same", "after", "2026-11-22T00:00:03Z"), "obs-3", max_history=2)
        self.assertEqual(store.list(session_id="before"), [])
        compact = store.list(session_id="after", compact=True)
        self.assertEqual(len(compact), 1)
        self.assertNotIn("analysis_settings", compact[0])
        self.assertNotIn("source_context", compact[0])
        self.assertNotIn("stages", compact[0]["decision"])
        self.assertEqual([row["analysis_at"] for row in store.history("same")],
                         ["2026-11-22T00:00:03Z", "2026-11-22T00:00:02Z"])
        self.assertEqual(store.stats()["history_pruned"], 1)
        self.assertEqual(store.get("same")["session_id"], "after")

    def test_empty_and_no_session_filters_remain_distinct(self):
        store = SignalStore(self.path)
        store.save(self.signal("empty", "", "2026-11-22T00:00:01Z"))
        store.save(self.signal("other", "other", "2026-11-22T00:00:02Z"))
        self.assertEqual([row["id"] for row in store.list(session_id="")], ["empty"])
        self.assertEqual([row["id"] for row in store.list()], ["other", "empty"])


if __name__ == "__main__":
    unittest.main()
