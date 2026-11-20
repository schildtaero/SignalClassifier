import json
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest

from signalclassifier.drones import Bridge, DroneStore, capabilities, decode_payload, example_payload


class DroneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DroneStore(Path(self.temp.name) / "db.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_position_and_height_references_are_distinct(self):
        payload = example_payload()
        payload["drone"]["altitude_pressure_m"] = 350
        payload["operator"] = {"lat": 49.4, "lon": 11.0, "altitude_geodetic_m": 320}
        result = self.store.ingest(payload)
        self.assertEqual(result["drone"]["altitude_geodetic_m"], 400)
        self.assertEqual(result["drone"]["altitude_pressure_m"], 350)
        self.assertNotIn("height_agl_m", result["drone"])
        self.assertEqual(result["operator"]["lat"], 49.4)
        self.assertEqual(result["home"]["location_type"], "takeoff")
        self.assertEqual(len(DroneStore(self.store.db_path).tracks()), 1)
        self.assertFalse(result["position_stale"])
        self.assertEqual(result["authentication"], "not_verified")

    def test_identification_update_does_not_refresh_position(self):
        from datetime import datetime, timezone
        payload = example_payload()
        original = time.time() - 60
        payload["observed_at"] = datetime.fromtimestamp(original, timezone.utc).isoformat()
        self.store.ingest(payload)
        self.store.ingest({"id": payload["id"], "protocol": payload["protocol"], "source": payload["source"],
                           "observed_at": example_payload()["observed_at"], "basic_id": "SERIAL"})
        track = self.store.tracks()[0]
        self.assertFalse(track["stale"])
        self.assertTrue(track["position_stale"])
        self.assertGreater(track["position_age_seconds"], 59)

    def test_older_packet_does_not_replace_position(self):
        from datetime import datetime, timezone
        payload = example_payload()
        self.store.ingest(payload)
        payload["observed_at"] = datetime.fromtimestamp(time.time() - 60, timezone.utc).isoformat()
        payload["drone"]["lat"] = 0.5
        result = self.store.ingest(payload)
        self.assertEqual(result["drone"]["lat"], 49.45)
        self.assertTrue(result["out_of_order"])

    def test_invalid_packets_rejected(self):
        for mutate in (
            lambda p: p.update(observed_at="2026-01-01"),
            lambda p: p.update(source=""),
            lambda p: p["drone"].update(lat=91),
            lambda p: p["drone"].update(lon=float("nan")),
            lambda p: p["drone"].pop("lon"),
        ):
            payload = example_payload()
            mutate(payload)
            with self.assertRaises(ValueError):
                self.store.ingest(payload)
        self.assertEqual(self.store.tracks(), [])

    @staticmethod
    def location(height_reference=1):
        raw = bytearray(25)
        raw[0] = 0x12
        raw[1] = 0x20 | (height_reference << 2)
        raw[2] = 42
        raw[3] = 40
        raw[4] = 2
        struct.pack_into("<iiHHHH", raw, 5, 494500000, 110800000, 2700, 2800, 2100, 0)
        struct.pack_into("<H", raw, 21, 1234)
        return bytes(raw)

    def test_decode_location_matches_wire_units_and_sentinels(self):
        result = decode_payload(self.location())
        self.assertEqual(result["drone"]["lat"], 49.45)
        self.assertEqual(result["drone"]["lon"], 11.08)
        self.assertEqual(result["drone"]["altitude_pressure_m"], 350)
        self.assertEqual(result["drone"]["altitude_geodetic_m"], 400)
        self.assertEqual(result["drone"]["height_agl_m"], 50)
        self.assertEqual(result["drone"]["speed_mps"], 10)
        self.assertEqual(result["transmitted_seconds_after_hour"], 123.4)
        other = decode_payload(self.location(0))
        self.assertNotIn("height_agl_m", other["drone"])
        self.assertEqual(other["drone"]["height_above_takeoff_m"], 50)
        unknown = decode_payload(bytes([0x12]) + bytes(24))
        self.assertIsNone(unknown["drone"]["lat"])
        self.assertIsNone(unknown["drone"]["altitude_geodetic_m"])

    def test_decode_pack_and_system_takeoff_are_not_operator(self):
        basic = bytes([2, 0x12]) + b"SERIAL-123".ljust(20, b"\0") + bytes(3)
        system = bytearray(25)
        system[0] = 0x42
        struct.pack_into("<ii", system, 2, 494490000, 110790000)
        struct.pack_into("<H", system, 18, 2640)
        packed = bytes([0xF2, 25, 3]) + basic + self.location() + bytes(system)
        result = decode_payload(packed)
        self.assertEqual(result["basic_id"], "SERIAL-123")
        self.assertIn("home", result)
        self.assertNotIn("operator", result)
        self.assertEqual(result["home"]["altitude_geodetic_m"], 320)
        payload = {"id": "radio-mac", "source": "test", "observed_at": example_payload()["observed_at"], "payload_hex": packed.hex()}
        self.assertEqual(self.store.ingest(payload)["basic_id"], "SERIAL-123")
        with self.assertRaises(ValueError):
            decode_payload(packed[:-1])
        with self.assertRaises(ValueError):
            decode_payload(bytes([0x11]) + bytes(24))

    def test_independent_reference_c_encoder_fixtures(self):
        # Generated by upstream encodeLocationMessage/encodeSystemMessage at
        # a92bd5ffe113edd076c159a06799152aed3b1fb0, not our Python packer.
        location = decode_payload(bytes.fromhex("12275a4cf8a078791d80ac9a068c0af00a34080000d2040000"))
        drone = location["drone"]
        self.assertEqual(drone["lat"], 49.45)
        self.assertEqual(drone["lon"], 11.08)
        self.assertEqual(drone["heading_deg"], 270)
        self.assertEqual(drone["speed_mps"], 120.75)
        self.assertEqual(drone["vertical_speed_mps"], -4)
        self.assertEqual(drone["altitude_pressure_m"], 350)
        self.assertEqual(drone["altitude_geodetic_m"], 400)
        self.assertEqual(drone["height_agl_m"], 50)
        system = decode_payload(bytes.fromhex("420180d7711d80778e060100000000000000500a0000000000"))
        self.assertNotIn("home", system)
        self.assertEqual(system["operator"]["location_type"], "live_gnss")
        self.assertEqual(system["operator"]["altitude_geodetic_m"], 320)

    def test_identical_ids_from_distinct_sources_are_not_silently_merged(self):
        payload = example_payload()
        self.store.ingest(payload)
        payload["source"] = "other-scanner"
        self.store.ingest(payload)
        self.assertEqual(len(self.store.tracks()), 2)

    def test_udp_bridge_handles_invalid_datagram_then_valid_packet(self):
        bridge = Bridge(self.store)
        try:
            with self.assertRaises(ValueError):
                bridge.start({"host": "0.0.0.0"})
            status = bridge.start({"port": 0})
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"not json", (status["host"], status["port"]))
                sender.sendto(json.dumps(example_payload()).encode(), (status["host"], status["port"]))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and bridge.status()["accepted"] < 1:
                time.sleep(.02)
            self.assertEqual(bridge.status()["accepted"], 1)
            self.assertEqual(bridge.status()["rejected"], 1)
            self.assertEqual(len(self.store.tracks()), 1)
        finally:
            self.assertFalse(bridge.stop()["running"])

    def test_capabilities_dont_claim_universal_rf_decoding(self):
        self.assertGreaterEqual(len(capabilities()), 6)
        for row in capabilities():
            self.assertTrue(all(key in row for key in ("protocol", "status", "hardware", "position", "video", "notes")))


if __name__ == "__main__":
    unittest.main()
