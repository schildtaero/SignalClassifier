"""Status counts must not decode archived experiment/model payloads."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

import numpy as np

from signalclassifier.app import create_app
from signalclassifier.db import Store
from signalclassifier.sigmf import write_recording


class StoreCountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "library.sqlite3")

    def test_count_never_decodes_payloads_even_if_they_are_invalid_json(self):
        with self.store.connect() as conn:
            conn.executemany("INSERT INTO records(kind,id,payload) VALUES(?,?,?)", [
                ("experiment", "broken", "not-json"),
                ("experiment", "private-large", '{"private":"' + "x" * 10000 + '"}'),
                ("model", "broken-model", "also-not-json"),
            ])
        with patch("signalclassifier.db.json.loads", side_effect=AssertionError("count decoded a payload")):
            self.assertEqual(self.store.count("experiment"), 2)
            self.assertEqual(self.store.count("model"), 1)
            self.assertEqual(self.store.count("missing"), 0)

    def test_counts_follow_inserts_updates_batch_writes_and_deletions(self):
        self.assertEqual(self.store.count("experiment"), 0)
        self.store.put("experiment", "first", {"status": "queued"})
        self.store.put("experiment", "first", {"status": "completed"})
        self.assertEqual(self.store.count("experiment"), 1)
        self.store.put_many("experiment", [{"id": "second"}, {"id": "third"}])
        self.store.put("model", "independent", {})
        self.assertEqual(self.store.count("experiment"), 3)
        self.assertEqual(self.store.count("model"), 1)
        self.store.delete("experiment", "first")
        self.assertEqual(self.store.count("experiment"), 2)
        self.store.delete_many("experiment", ["second", "third"])
        self.assertEqual(self.store.count("experiment"), 0)
        self.assertEqual(self.store.count("model"), 1)

    def test_kind_is_a_bound_sql_value(self):
        self.store.put("experiment", "one", {})
        self.store.put("model", "two", {})
        self.assertEqual(self.store.count("experiment' OR 1=1 --"), 0)
        self.assertEqual(self.store.count("experiment"), 1)
        self.assertEqual(self.store.count("model"), 1)


class StatusPollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # No external GPU query, receiver or background model work is needed.
        telemetry = patch("signalclassifier.app.ResourceTelemetry")
        mocked = telemetry.start()
        mocked.return_value.snapshot.return_value = {"cached": True}
        self.addCleanup(telemetry.stop)
        hardware = patch("signalclassifier.app.hardware_info", return_value={"cpu": "fixture"})
        hardware.start()
        self.addCleanup(hardware.stop)
        self.app = create_app(self.root)
        self.runtime = self.app.state.runtime
        self.runtime.analysis.close()
        self.addCleanup(self.runtime.close)
        self.status_endpoint = next(route.endpoint for route in self.app.routes
                                    if getattr(route, "path", None) == "/api/status")

    def prohibit_large_catalogue_reads(self):
        original = self.runtime.store.all

        def guarded(kind):
            if kind in ("experiment", "model"):
                raise AssertionError(f"Status loaded the entire {kind} catalogue")
            return original(kind)

        return patch.object(self.runtime.store, "all", side_effect=guarded)

    def status(self):
        # Exercise the registered synchronous handler directly: this is a
        # catalogue-access regression, independent of HTTP/socket permissions.
        # The existing application suite covers the ASGI transport separately.
        value = self.status_endpoint()
        self.assertIsInstance(value, dict)
        return value

    def test_status_counts_experiments_models_and_both_label_sources(self):
        for identifier, extra in (("db-labelled", {}), ("unlabelled", {}),
                                  ("metadata-labelled", {"signalclassifier:label": "FM",
                                                         "signalclassifier:label_verified": True})):
            write_recording(self.root / "recordings" / identifier, np.zeros(256, np.complex64),
                            25000, 100e6, extra=extra)
        self.runtime.store.put("label", "db-labelled", {"label": "AM", "verified": True})
        self.runtime.store.put("experiment", "good-run", {"status": "completed"})
        self.runtime.store.put("model", "good-model", {})
        # Neither malformed nor very large archived payloads need decoding for
        # a dashboard count. The endpoint must still return its other fields.
        with self.runtime.store.connect() as conn:
            conn.executemany("INSERT INTO records(kind,id,payload) VALUES(?,?,?)", [
                ("experiment", "broken-run", "invalid archived json"),
                ("model", "broken-model", "invalid model json"),
            ])
        with self.prohibit_large_catalogue_reads():
            value = self.status()
        self.assertEqual(value["counts"], {"recordings": 3, "labels": 2, "experiments": 2, "models": 2})
        self.assertEqual(value["hardware"], {"cpu": "fixture"})
        self.assertIsNone(value["active_experiment"])
        self.assertIn("receiver", value)

    def test_status_counts_refresh_after_put_update_and_delete(self):
        with self.prohibit_large_catalogue_reads():
            self.assertEqual(self.status()["counts"]["experiments"], 0)
            self.runtime.store.put("experiment", "run", {"status": "completed"})
            self.runtime.store.put("model", "model", {})
            self.assertEqual(self.status()["counts"], {"recordings": 0, "labels": 0, "experiments": 1, "models": 1})
            self.runtime.store.put("experiment", "run", {"status": "failed"})
            self.assertEqual(self.status()["counts"]["experiments"], 1)
            self.runtime.store.delete("experiment", "run")
            self.runtime.store.delete("model", "model")
            self.assertEqual(self.status()["counts"], {"recordings": 0, "labels": 0, "experiments": 0, "models": 0})

    def test_active_experiment_is_still_read_by_identifier(self):
        self.runtime.store.put("experiment", "active", {"status": "running", "progress": .25})
        self.runtime.store.put("active_model", "main", {"model_id": "chosen"})
        self.runtime.active_experiment = "active"
        with self.prohibit_large_catalogue_reads(), patch.object(
                self.runtime.store, "get", wraps=self.runtime.store.get) as get:
            value = self.status()
        self.assertIn(call("experiment", "active"), get.call_args_list)
        self.assertEqual(value["active_experiment"], {"id": "active", "status": "running", "progress": .25})
        self.assertEqual(value["active_model"]["model_id"], "chosen")
        self.assertEqual(value["counts"]["experiments"], 1)


if __name__ == "__main__":
    unittest.main()
