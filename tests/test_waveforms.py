"""Numeric and independent wire-format checks; no transmitter or RF hardware."""
import binascii
import hashlib
import unittest
from unittest import mock

import numpy as np

from signalclassifier import waveforms as w


def unstuff_ax25(bits):
    """Test receiver: remove HDLC zeros and deserialize LSB-first octets."""
    result, ones, position = [], 0, 0
    while position < len(bits):
        bit = int(bits[position])
        result.append(bit)
        ones = ones + 1 if bit else 0
        position += 1
        if ones == 5:
            if position >= len(bits) or bits[position] != 0:
                raise AssertionError("Invalid HDLC stuffing")
            position += 1
            ones = 0
    if len(result) % 8:
        raise AssertionError("Incomplete AX.25 octet")
    return bytes(sum(result[start + k] << k for k in range(8))
                 for start in range(0, len(result), 8))


def x25_library_oracle(data):
    # Independent C stdlib CRC implementation, with explicit bit reflection.
    reflect = lambda x: int(f"{x:08b}"[::-1], 2)
    crc = binascii.crc_hqx(bytes(reflect(byte) for byte in data), 0xffff)
    reflected = int(f"{crc:016b}"[::-1], 2)
    return reflected ^ 0xffff


class WaveformTests(unittest.TestCase):
    def test_catalogue_contract_defaults_and_variations(self):
        entries = w.catalogue()
        expected = {"am", "nfm", "wfm", "usb", "lsb", "ook", "4ask",
                    "2fsk", "4fsk", "8fsk", "16fsk", "msk", "gmsk",
                    "bpsk", "qpsk", "8psk", "16psk", "dbpsk", "dqpsk",
                    "16qam", "64qam", "256qam", "ofdm", "chirp", "cw", "noise",
                    "ax25-afsk1200", "pocsag512", "pocsag1200", "pocsag2400"}
        self.assertEqual({entry["id"] for entry in entries}, expected)
        for entry in entries:
            with self.subTest(mode=entry["id"]):
                spec = w.normalize_waveform_spec({"mode_id": entry["id"]})
                self.assertEqual(set(spec), {"mode_id", "sample_rate", "parameters"})
                self.assertEqual(spec["sample_rate"], 1_000_000)
                self.assertEqual(set(entry["facets"]), {"waveform_family", "modulation", "protocol", "service", "emitter"})
                self.assertEqual(w.estimate_bandwidth(spec), entry["estimated_bandwidth_hz"])
                for variation in entry["default_variations"]:
                    w.normalize_waveform_spec({"mode_id": entry["id"], "parameters": variation})
        entries[0]["parameters"]["samples"]["default"] = 1
        entries[0]["facets"]["protocol"] = "secret mutation"
        self.assertEqual(w.catalogue()[0]["parameters"]["samples"]["default"], 65_536)
        self.assertIsNone(w.catalogue()[0]["facets"]["protocol"])

    def test_all_defaults_have_finite_peak_limited_iq_and_provenance(self):
        for entry in w.catalogue():
            with self.subTest(mode=entry["id"]):
                result = w.generate_waveform({"mode_id": entry["id"]}, payload_seed=17, waveform_seed=41)
                iq = result["iq"]
                self.assertEqual(iq.dtype, np.complex64)
                self.assertEqual(iq.ndim, 1)
                limit = w.MAX_PERIOD_SAMPLES if entry["implementation_kind"] == "protocol_encoder" else w.MAX_NUMERIC_SAMPLES
                self.assertLessEqual(len(iq), limit)
                self.assertTrue(np.isfinite(iq).all())
                self.assertLessEqual(float(np.max(np.abs(iq))), .8)
                self.assertGreater(float(np.mean(np.abs(iq) ** 2)), 0)
                self.assertEqual(result["normalization"]["sample_count"], len(iq))
                self.assertEqual(result["normalization"]["duration_seconds"], len(iq) / result["sample_rate"])
                self.assertEqual(result["provenance"]["source_kind"], "generated")
                self.assertEqual(len(result["provenance"]["payload_sha256"]), 64)
                self.assertIsNone(result["facets"]["service"])
                self.assertIsNone(result["facets"]["emitter"])

    def test_strict_input_validation_and_cross_field_guards(self):
        invalid = [None, {}, {"mode_id": "LoRa"}, {"mode_id": "am", "center_frequency_hz": 100e6},
                   {"mode_id": "am", "sample_rate": True}, {"mode_id": "am", "sample_rate": float("nan")},
                   {"mode_id": "am", "sample_rate": 520_833},
                   {"mode_id": "am", "parameters": {"token": "ignored?"}},
                   {"mode_id": "am", "parameters": {"samples": 300.5}},
                   {"mode_id": "bpsk", "parameters": {"symbols": 32768, "samples_per_symbol": 256}},
                   {"mode_id": "16fsk", "parameters": {"samples_per_symbol": 4, "modulation_index": 2}},
                   {"mode_id": "ofdm", "parameters": {"fft_size": 96}},
                   {"mode_id": "ofdm", "parameters": {"active_carriers": 65}},
                   {"mode_id": "ofdm", "parameters": {"cyclic_prefix": 128}},
                   {"mode_id": "cw", "parameters": {"offset_hz": 450_000}},
                   {"mode_id": "noise", "sample_rate": 10e6, "parameters": {"samples": 256, "bandwidth_hz": 1000}},
                   {"mode_id": "pocsag512", "sample_rate": 10e6}]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                w.normalize_waveform_spec(spec)
        for seed in (True, -1, 2 ** 32, 1.5):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                w.generate_waveform({"mode_id": "cw"}, payload_seed=seed, waveform_seed=1)
        with mock.patch.object(w.np, "arange", side_effect=AssertionError("allocated before validation")):
            with self.assertRaises(ValueError):
                w.generate_waveform({"mode_id": "pocsag512", "sample_rate": 10e6}, payload_seed=1, waveform_seed=2)

    def test_seed_replay_payload_phase_separation_and_no_global_rng_mutation(self):
        np.random.seed(99)
        expected = np.random.random(4)
        np.random.seed(99)
        spec = {"mode_id": "qpsk", "parameters": {"symbols": 128}}
        a = w.generate_waveform(spec, payload_seed=3, waveform_seed=4)
        b = w.generate_waveform(spec, payload_seed=3, waveform_seed=4)
        c = w.generate_waveform(spec, payload_seed=3, waveform_seed=5)
        d = w.generate_waveform(spec, payload_seed=6, waveform_seed=4)
        np.testing.assert_array_equal(a["iq"], b["iq"])
        self.assertEqual(a["provenance"], b["provenance"])
        self.assertEqual(a["provenance"]["payload_sha256"], c["provenance"]["payload_sha256"])
        self.assertNotEqual(a["provenance"]["payload_sha256"], d["provenance"]["payload_sha256"])
        np.testing.assert_allclose(np.abs(a["iq"]), np.abs(c["iq"]), atol=2e-7)
        self.assertFalse(np.array_equal(a["iq"], c["iq"]))
        np.testing.assert_array_equal(np.random.random(4), expected)

    def test_analog_envelope_frequency_and_ssb_sideband(self):
        am = w.generate_waveform({"mode_id": "am", "parameters": {"message_kind": "tone", "modulation_index": .6}}, payload_seed=1, waveform_seed=2)["iq"]
        envelope = np.abs(am)
        # Positive carrier envelope, intended modulation depth from extrema.
        depth = (envelope.max() - envelope.min()) / (envelope.max() + envelope.min())
        self.assertAlmostEqual(float(depth), .6, delta=.005)
        fm = w.generate_waveform({"mode_id": "nfm", "parameters": {"message_kind": "tone", "deviation_hz": 3000}}, payload_seed=1, waveform_seed=2)["iq"]
        np.testing.assert_allclose(np.abs(fm), np.abs(fm)[0], atol=2e-7)
        freq = np.angle(fm[1:] * fm[:-1].conj()) * 1e6 / (2 * np.pi)
        self.assertAlmostEqual(float(np.max(np.abs(freq))), 3000, delta=2)
        for mode, sign in (("usb", 1), ("lsb", -1)):
            iq = w.generate_waveform({"mode_id": mode}, payload_seed=13, waveform_seed=2)["iq"]
            power = np.abs(np.fft.fft(iq)) ** 2
            f = np.fft.fftfreq(len(iq))
            wanted = power[f * sign > 0].sum()
            unwanted = power[f * sign < 0].sum()
            self.assertGreater(wanted / max(unwanted, 1e-20), 1e9)

    def test_cpfsk_spacing_and_msk_index(self):
        for mode, order, sps, h in (("4fsk", 4, 16, .75), ("msk", 2, 16, .5)):
            parameters = {"symbols": 256, "samples_per_symbol": sps}
            if mode == "4fsk":
                parameters["modulation_index"] = h
            result = w.generate_waveform({"mode_id": mode, "parameters": parameters}, payload_seed=7, waveform_seed=3)
            phase = np.angle(result["iq"][1:] * result["iq"][:-1].conj())
            middle = phase[np.arange(256) * sps + sps // 2]
            expected_levels = np.arange(-order + 1, order, 2) * np.pi * h / sps
            np.testing.assert_allclose(np.unique(np.round(middle, 5)), np.round(expected_levels, 5), atol=1e-5)
            np.testing.assert_allclose(np.abs(result["iq"]), .799999, atol=2e-7)

    def test_gaussian_shaping_reduces_msk_high_frequency_energy(self):
        a = w.generate_waveform({"mode_id": "msk"}, payload_seed=9, waveform_seed=3)["iq"]
        b = w.generate_waveform({"mode_id": "gmsk"}, payload_seed=9, waveform_seed=3)["iq"]
        f = np.abs(np.fft.fftfreq(len(a)))
        outside = f > .15
        leakage = lambda x: float((np.abs(np.fft.fft(x)) ** 2)[outside].sum() / (np.abs(np.fft.fft(x)) ** 2).sum())
        self.assertLess(leakage(b), leakage(a))

    def test_gray_qam_and_psk_constellations_and_rrc_singularities(self):
        for order in (16, 64, 256):
            points = w._constellation(np.arange(order), f"{order}qam")
            self.assertEqual(len(np.unique(points)), order)
            self.assertAlmostEqual(float(np.mean(np.abs(points) ** 2)), 1)
            side = int(np.sqrt(order))
            # Source-bit indices at adjacent physical I levels differ by one bit.
            physical_order = np.argsort(points[:side].real)
            self.assertTrue(all(int(a ^ b).bit_count() == 1 for a, b in zip(physical_order[:-1], physical_order[1:])))
        self.assertEqual(len(np.unique(np.round(w._constellation(np.arange(16), "16psk"), 8))), 16)
        for beta in (0, .25, .5, 1):
            taps = w._rrc(8, beta, 8)
            self.assertTrue(np.isfinite(taps).all())
            self.assertAlmostEqual(float(np.sum(taps ** 2)), 1)
            np.testing.assert_array_equal(taps, taps[::-1])

    def test_ofdm_carriers_prefix_and_separate_facets(self):
        result = w.generate_waveform({"mode_id": "ofdm", "parameters": {"symbols": 8}}, payload_seed=3, waveform_seed=7)
        blocks = result["iq"].reshape(8, 160)
        np.testing.assert_array_equal(blocks[:, :32], blocks[:, -32:])
        grid = np.fft.fft(blocks[:, 32:], axis=1)
        active = np.r_[np.arange(1, 33), np.arange(96, 128)]
        inactive = np.setdiff1d(np.arange(128), active)
        self.assertGreater(float(np.mean(np.abs(grid[:, active]) ** 2)), 1)
        self.assertLess(float(np.max(np.abs(grid[:, inactive]))), 1e-5)
        self.assertEqual(result["facets"]["waveform_family"], "OFDM")
        self.assertEqual(result["facets"]["modulation"], "16QAM")
        self.assertIsNone(result["facets"]["protocol"])

    def test_chirp_is_not_lora_noise_is_bandlimited_and_cw_has_expected_offset(self):
        chirp = w.generate_waveform({"mode_id": "chirp", "parameters": {"chirps": 1}}, payload_seed=3, waveform_seed=7)
        freq = np.angle(chirp["iq"][1:] * chirp["iq"][:-1].conj()) * 1e6 / (2 * np.pi)
        self.assertLess(freq[0], -62_000)
        self.assertGreater(freq[-1], 62_000)
        self.assertIsNone(chirp["facets"]["protocol"])
        noise = w.generate_waveform({"mode_id": "noise", "parameters": {"bandwidth_hz": 50_000}}, payload_seed=3, waveform_seed=7)["iq"]
        spectrum = np.abs(np.fft.fft(noise)) ** 2
        outside = np.abs(np.fft.fftfreq(len(noise), 1e-6)) > 25_000
        self.assertLess(float(spectrum[outside].sum() / spectrum.sum()), 1e-12)
        cw = w.generate_waveform({"mode_id": "cw", "parameters": {"offset_hz": 12_345}}, payload_seed=3, waveform_seed=7)["iq"]
        freq = np.angle(cw[1:] * cw[:-1].conj()) * 1e6 / (2 * np.pi)
        np.testing.assert_allclose(freq, 12_345, atol=.02)

    def test_ax25_known_crc_vectors_and_wire_frame(self):
        # CRC-16/X-25 catalogue check value, and fixed AX.25 UI addresses.
        self.assertEqual(w._crc_x25(b"123456789"), 0x906e)
        payload = b"\xff\xff\xf8HELLO"
        bits, frame = w._ax25_bits(payload, 2, 1)
        self.assertEqual(frame[:16].hex(), "82a0a4a64040609c60868298986103f0")
        decoded = unstuff_ax25(bits[16:-8])
        self.assertEqual(decoded, frame)
        self.assertEqual(int.from_bytes(frame[-2:], "little"), x25_library_oracle(frame[:-2]))
        self.assertEqual(x25_library_oracle(frame), 0x0f47)
        self.assertGreater(len(bits), len(frame) * 8 + 24)

    def test_ax25_generated_fm_iq_can_be_demodulated_to_valid_payload(self):
        result = w.generate_waveform({"mode_id": "ax25-afsk1200", "parameters": {"payload_bytes": 8, "preamble_flags": 2, "postamble_flags": 1}}, payload_seed=8, waveform_seed=99)
        iq = result["iq"]
        # Independent simple test receiver: FM discriminator, audio matched tones,
        # NRZI, HDLC unstuffing, then the standard-library CRC oracle above.
        audio = np.r_[0., np.angle(iq[1:] * iq[:-1].conj()) * 1e6 / (2 * np.pi * 2500)]
        count = result["provenance"]["protocol_validation"]["bit_count"]
        states = []
        for i in range(count):
            a, b = int(np.ceil(i * 1e6 / 1200)), int(np.ceil((i + 1) * 1e6 / 1200))
            segment = audio[a:b]
            t = np.arange(len(segment)) / 1e6
            energies = [abs(np.dot(segment, np.exp(-2j * np.pi * hz * t))) for hz in (2200, 1200)]
            states.append(int(energies[1] > energies[0]))
        bits = [int(state == previous) for state, previous in zip(states, [0] + states[:-1])]
        self.assertEqual(bits[:16], [0, 1, 1, 1, 1, 1, 1, 0] * 2)
        frame = unstuff_ax25(bits[16:-8])
        self.assertEqual(x25_library_oracle(frame), 0x0f47)
        self.assertEqual(hashlib.sha256(frame[16:-2]).hexdigest(), result["provenance"]["payload_sha256"])
        self.assertEqual(result["facets"]["protocol"], "AX.25")
        self.assertIn("not_independent_rx_decode", result["provenance"]["protocol_validation"]["scope"])

    def test_pocsag_known_idle_bch_parity_and_batch_boundaries(self):
        # Published POCSAG idle codeword is a fixed independent BCH/parity vector.
        self.assertEqual(w._pocsag_codeword(0x7a89c197 >> 11), 0x7a89c197)
        self.assertEqual(w._pocsag_codeword(0), 0)
        payload = b"TEST 123"
        bits = w._pocsag_bits(payload, 123_463)  # address frame7, message crosses batch
        self.assertEqual(bits[:576].tolist(), [1, 0] * 288)
        self.assertEqual((len(bits) - 576) % 544, 0)
        stream = bits[576:].reshape(-1, 544)
        for batch in stream:
            words = np.packbits(batch).tobytes()
            self.assertEqual(words[:4].hex(), "7cd215d8")
            for start in range(4, len(words), 4):
                word = int.from_bytes(words[start:start + 4], "big")
                self.assertEqual(word.bit_count() % 2, 0)
                # Polynomial long division, distinct expression from encoder loop.
                dividend = word >> 1
                while dividend.bit_length() > 10:
                    dividend ^= 0x769 << (dividend.bit_length() - 11)
                self.assertEqual(dividend, 0)
        first_words = np.packbits(stream[0]).tobytes()
        self.assertEqual(int.from_bytes(first_words[4:8], "big"), 0x7a89c197)
        address = int.from_bytes(first_words[4 + 14 * 4:4 + 15 * 4], "big")
        self.assertEqual(address >> 11, ((123_463 >> 3) << 2) | 3)

    def test_pocsag_sampled_fsk_recovers_full_burst_and_payload(self):
        result = w.generate_waveform({"mode_id": "pocsag2400", "parameters": {"payload_bytes": 8}}, payload_seed=15, waveform_seed=2)
        iq = result["iq"]
        baud = 2400
        count = result["provenance"]["protocol_validation"]["bit_count"]
        middle = (np.arange(count) + .5) * 1e6 / baud
        middle = middle.astype(int)
        delta = np.angle(iq[middle] * iq[middle - 1].conj())
        bits = (delta < 0).astype(np.uint8)
        self.assertEqual(bits[:576].tolist(), [1, 0] * 288)
        raw = np.packbits(bits[576:]).tobytes()
        self.assertEqual(raw[:4].hex(), "7cd215d8")
        self.assertEqual((len(bits) - 576) % 544, 0)
        message = []
        for start in range(8, len(raw), 4):  # skip sync/address for capcode frame0
            word = int.from_bytes(raw[start:start + 4], "big")
            if word == 0x7a89c197:
                break
            self.assertTrue(word & 0x80000000)
            message.extend((word >> bit) & 1 for bit in range(30, 10, -1))
        payload = bytes(sum(message[i + k] << k for k in range(7)) for i in range(0, 56, 7))
        self.assertEqual(hashlib.sha256(payload).hexdigest(), result["provenance"]["payload_sha256"])
        self.assertEqual(len(iq), int(np.ceil(1120 * 1e6 / 2400)))
        self.assertGreater(len(iq), w.MAX_NUMERIC_SAMPLES)


if __name__ == "__main__":
    unittest.main()
