"""Bounded, deterministic baseband test signals; no device or RF transmission.

Modulation, waveform and protocol are independent label facets.  A generated
protocol frame is encoder evidence, never proof that a receiver decoded it.
Digital sample rates are exact: symbol_rate = sample_rate / samples_per_symbol.
"""
from __future__ import annotations

import copy
import hashlib
import math
from numbers import Real

import numpy as np
from scipy import signal

ENCODER_VERSION = "signalclassifier-waveforms-v1"
MIN_SAMPLE_RATE = 520_834
MAX_SAMPLE_RATE = 10_000_000
MAX_NUMERIC_SAMPLES = 262_144
MAX_PERIOD_SAMPLES = 4_194_304


def _number(default, low, high, unit=None, integer=False):
    value = {"type": "integer" if integer else "number", "default": default,
             "min": low, "max": high}
    if unit:
        value["unit"] = unit
    return value


def _choice(default, values):
    return {"type": "string", "default": default, "choices": list(values)}


def _facets(modulation=None, waveform_family="single_carrier", protocol=None):
    return {"waveform_family": waveform_family, "modulation": modulation,
            "protocol": protocol, "service": None, "emitter": None}


_ANALOG = {"samples": _number(65_536, 256, MAX_NUMERIC_SAMPLES, "samples", True),
           "audio_bandwidth_hz": _number(3000, 100, 50_000, "Hz"),
           "message_kind": _choice("multitone", ("multitone", "tone", "filtered_noise"))}
_DIGITAL = {"symbols": _number(1024, 16, 32_768, "symbols", True),
            "samples_per_symbol": _number(8, 4, 256, "samples/symbol", True)}
_SHAPED = {**_DIGITAL, "rolloff": _number(.35, 0, 1),
           "filter_span_symbols": _number(8, 4, 32, "symbols", True)}
_CATALOGUE = []


def _entry(identifier, name, facets, parameters, variations=(), bandwidth=0,
           kind="native_numeric"):
    _CATALOGUE.append({"id": identifier, "name": name, "facets": facets,
                       "implementation_kind": kind, "parameters": copy.deepcopy(parameters),
                       "default_variations": list(variations),
                       "estimated_bandwidth_hz": bandwidth})


_entry("am", "AM mit Träger", _facets("AM"),
       {**_ANALOG, "modulation_index": _number(.6, .05, .95)},
       ({"modulation_index": .3}, {"modulation_index": .8}), 6000)
for _id, _name, _deviation, _band in (("nfm", "Schmalband-FM", 2500, 2500),
                                     ("wfm", "Breitband-FM", 75_000, 15_000)):
    _entry(_id, _name, _facets("FM"),
           {**_ANALOG, "audio_bandwidth_hz": _number(_band, 100, 50_000, "Hz"),
            "deviation_hz": _number(_deviation, 100, 150_000, "Hz")},
           ({"deviation_hz": _deviation * .7}, {"deviation_hz": _deviation}),
           2 * (_deviation + _band))
for _id in ("usb", "lsb"):
    _entry(_id, _id.upper() + " Einseitenband", _facets(_id.upper()), _ANALOG,
           ({"audio_bandwidth_hz": 1500}, {"audio_bandwidth_hz": 3000}), 3000)
for _id, _name, _mod in (("ook", "On-off keying / 2-ASK", "OOK"),
                         ("4ask", "Vierstufige ASK", "4ASK")):
    _entry(_id, _name, _facets(_mod),
           {**_DIGITAL, "edge_smoothing_symbols": _number(.1, 0, .5, "symbols")},
           ({"samples_per_symbol": 8}, {"samples_per_symbol": 16}), 250_000)
for _order in (2, 4, 8, 16):
    _entry(f"{_order}fsk", f"{_order}-FSK, kontinuierliche Phase", _facets(f"{_order}FSK"),
           {**_DIGITAL, "samples_per_symbol": _number(max(8, 2 * _order), 4, 256, "samples/symbol", True),
            "modulation_index": _number(.5, .1, 2)},
           ({"modulation_index": .5}, {"modulation_index": 1}),
           1_000_000 / 8 * (2 + (_order - 1) * .5))
_entry("msk", "Minimum shift keying", _facets("MSK"), _DIGITAL,
       ({"samples_per_symbol": 8}, {"samples_per_symbol": 16}), 187_500)
_entry("gmsk", "Gaussian minimum shift keying", _facets("GMSK"),
       {**_DIGITAL, "bt": _number(.3, .15, 1)},
       ({"bt": .3}, {"bt": .5}), 187_500)
for _id, _order, _differential in (("bpsk", 2, False), ("qpsk", 4, False),
                                    ("8psk", 8, False), ("16psk", 16, False),
                                    ("dbpsk", 2, True), ("dqpsk", 4, True)):
    _entry(_id, _id.upper() + " mit RRC-Pulsformung", _facets(_id.upper()), _SHAPED,
           ({"rolloff": .2}, {"rolloff": .5}), 168_750)
for _order in (16, 64, 256):
    _entry(f"{_order}qam", f"{_order}-QAM, quadratisch / Gray", _facets(f"{_order}QAM"),
           _SHAPED, ({"rolloff": .2}, {"rolloff": .5}), 168_750)
_entry("ofdm", "Generisches OFDM (kein WLAN-/DVB-Rahmen)", _facets("16QAM", "OFDM"),
       {"symbols": _number(128, 4, 1024, "OFDM symbols", True),
        "fft_size": _number(128, 32, 1024, "samples", True),
        "active_carriers": _number(64, 4, 1000, "carriers", True),
        "cyclic_prefix": _number(32, 0, 256, "samples", True),
        "subcarrier_modulation": _choice("16QAM", ("BPSK", "QPSK", "16QAM", "64QAM"))},
       ({"subcarrier_modulation": "QPSK"}, {"subcarrier_modulation": "16QAM"}), 500_000)
_entry("chirp", "Generischer linearer Chirp (kein LoRa)", _facets(None, "chirp"),
       {"samples": _number(65_536, 256, MAX_NUMERIC_SAMPLES, "samples", True),
        "bandwidth_hz": _number(125_000, 100, 2_000_000, "Hz"),
        "direction": _choice("up", ("up", "down")),
        "chirps": _number(8, 1, 128, "chirps", True)},
       ({"direction": "up"}, {"direction": "down"}), 125_000)
_entry("cw", "Unmodulierter Träger", _facets("CW"),
       {"samples": _number(65_536, 256, MAX_NUMERIC_SAMPLES, "samples", True),
        "offset_hz": _number(0, -2_000_000, 2_000_000, "Hz")},
       ({"offset_hz": 0}, {"offset_hz": 10_000}), 0)
_entry("noise", "Komplexes bandbegrenztes Rauschen", _facets("NOISE", None),
       {"samples": _number(65_536, 256, MAX_NUMERIC_SAMPLES, "samples", True),
        "bandwidth_hz": _number(200_000, 1000, 8_000_000, "Hz")},
       ({"bandwidth_hz": 50_000}, {"bandwidth_hz": 200_000}), 200_000)
_entry("ax25-afsk1200", "AX.25 UI / Bell 202 AFSK 1200", _facets("FM", protocol="AX.25"),
       {"payload_bytes": _number(16, 1, 128, "bytes", True),
        "preamble_flags": _number(4, 1, 64, "flags", True),
        "postamble_flags": _number(2, 1, 16, "flags", True),
        "transport": _choice("nfm", ("nfm", "audio")),
        "fm_deviation_hz": _number(2500, 500, 10_000, "Hz")},
       ({"payload_bytes": 8}, {"payload_bytes": 16}), 9400, "protocol_encoder")
for _baud in (512, 1200, 2400):
    _entry(f"pocsag{_baud}", f"POCSAG {_baud} / alphanumerisch", _facets("2FSK", protocol="POCSAG"),
           {"payload_bytes": _number(16, 1, 80, "7-bit characters", True),
            "capcode": _number(123_456, 0, 2_097_151, "address", True),
            "deviation_hz": _number(4500, 1000, 10_000, "Hz"),
            "polarity": _choice("normal", ("normal", "inverted"))},
           ({"payload_bytes": 8}, {"payload_bytes": 16}), 2 * (4500 + _baud), "protocol_encoder")

_BY_ID = {entry["id"]: entry for entry in _CATALOGUE}


def catalogue():
    """Return fresh JSON-compatible descriptions; callers cannot mutate defaults."""
    result = copy.deepcopy(_CATALOGUE)
    for entry in result:
        entry["estimated_bandwidth_hz"] = estimate_bandwidth({"mode_id": entry["id"]})
    return result


def _finite_number(value):
    return not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value)


def normalize_waveform_spec(spec):
    """Strictly validate parameters and physical/size constraints before allocation."""
    if not isinstance(spec, dict) or set(spec) - {"mode_id", "sample_rate", "parameters"}:
        raise ValueError("Waveform spec permits only mode_id, sample_rate and parameters.")
    identifier = spec.get("mode_id")
    if not isinstance(identifier, str) or identifier not in _BY_ID:
        raise ValueError("Unknown native waveform mode_id.")
    sample_rate = spec.get("sample_rate", 1_000_000)
    if not _finite_number(sample_rate) or not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError(f"sample_rate must be {MIN_SAMPLE_RATE}..{MAX_SAMPLE_RATE} Hz.")
    supplied = spec.get("parameters", {})
    schema = _BY_ID[identifier]["parameters"]
    if not isinstance(supplied, dict) or set(supplied) - set(schema):
        raise ValueError("Unknown or invalid waveform parameters.")
    values = {}
    for key, rule in schema.items():
        value = supplied.get(key, rule["default"])
        if rule["type"] == "string":
            if not isinstance(value, str) or value not in rule["choices"]:
                raise ValueError(f"Invalid {key}; choose one of {rule['choices']}.")
        elif (not _finite_number(value) or not rule["min"] <= value <= rule["max"]
              or (rule["type"] == "integer" and int(value) != value)):
            raise ValueError(f"Invalid {key}; required range {rule['min']}..{rule['max']}.")
        elif rule["type"] == "integer":
            value = int(value)
        else:
            value = float(value)
        values[key] = value
    normalized = {"mode_id": identifier, "sample_rate": float(sample_rate), "parameters": values}
    _validate_physical(normalized)
    return normalized


def _bandwidth(spec):
    mode, p, fs = spec["mode_id"], spec["parameters"], spec["sample_rate"]
    if mode == "am":
        return 2 * p["audio_bandwidth_hz"]
    if mode in ("nfm", "wfm"):
        return 2 * (p["audio_bandwidth_hz"] + p["deviation_hz"])
    if mode in ("usb", "lsb"):
        return p["audio_bandwidth_hz"]
    if mode in ("chirp", "noise"):
        return p["bandwidth_hz"]
    if mode == "cw":
        return 0.
    if mode == "ofdm":
        return fs * p["active_carriers"] / p["fft_size"]
    if mode == "ax25-afsk1200":
        return 2 * (2200 + p["fm_deviation_hz"]) if p["transport"] == "nfm" else 4400.
    if mode.startswith("pocsag"):
        return 2 * (p["deviation_hz"] + int(mode[6:]))
    rate = fs / p["samples_per_symbol"]
    if mode.endswith("fsk"):
        order = int(mode[:-3])
        return rate * (2 + (order - 1) * p["modulation_index"])
    if mode in ("msk", "gmsk"):
        return 1.5 * rate
    if "rolloff" in p:
        return rate * (1 + p["rolloff"])
    return 2 * rate


def estimate_bandwidth(spec):
    """Cheap design bandwidth in Hz, not a measured spectral occupancy or SNR."""
    return float(_bandwidth(normalize_waveform_spec(spec)))


def _protocol_bits(spec):
    """A conservative count bound is checked before building sampled waveforms."""
    mode, p = spec["mode_id"], spec["parameters"]
    if mode == "ax25-afsk1200":
        # 16 address/control/PID bytes + FCS, worst-case HDLC stuffing.
        frame_bits = (18 + p["payload_bytes"]) * 8
        return p["preamble_flags"] * 8 + p["postamble_flags"] * 8 + frame_bits + frame_bits // 5
    messages = math.ceil((p["payload_bytes"] * 7) / 20)
    batches = math.ceil(((p["capcode"] & 7) * 2 + 1 + messages) / 16)
    return 576 + batches * (32 + 16 * 32)


def _validate_physical(spec):
    mode, p, fs = spec["mode_id"], spec["parameters"], spec["sample_rate"]
    if mode == "ofdm":
        fft = p["fft_size"]
        if fft & (fft - 1) or p["active_carriers"] % 2 or p["active_carriers"] > fft - 2:
            raise ValueError("OFDM requires a power-of-two FFT and an even active_carriers <= fft_size-2.")
        if p["cyclic_prefix"] > fft // 2:
            raise ValueError("OFDM cyclic_prefix must be <= fft_size/2.")
        count = p["symbols"] * (fft + p["cyclic_prefix"])
    elif "samples_per_symbol" in p:
        count = p["symbols"] * p["samples_per_symbol"]
    elif "samples" in p:
        count = p["samples"]
    else:
        baud = 1200 if mode == "ax25-afsk1200" else int(mode[6:])
        count = math.ceil(_protocol_bits(spec) * fs / baud)
    limit = MAX_PERIOD_SAMPLES if _BY_ID[mode]["implementation_kind"] == "protocol_encoder" else MAX_NUMERIC_SAMPLES
    if count > limit:
        raise ValueError(f"Waveform period exceeds {limit} samples; reduce size/sample rate.")
    if _bandwidth(spec) >= fs * .9:
        raise ValueError("Estimated occupied bandwidth needs a 10% sample-rate guard; increase sample rate or reduce bandwidth.")
    if mode == "cw" and abs(p["offset_hz"]) >= fs * .45:
        raise ValueError("CW offset exceeds the guarded Nyquist range.")
    if mode == "chirp" and p["samples"] // p["chirps"] < 16:
        raise ValueError("Each chirp needs at least 16 samples.")
    if mode == "noise" and p["bandwidth_hz"] * p["samples"] / fs < 8:
        raise ValueError("Noise period needs at least eight occupied Fourier bins.")


def _seed(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or not 0 <= value <= 2 ** 32 - 1:
        raise ValueError(f"{name} must be an integer in 0..2**32-1.")
    return int(value)


def _rrc(sps, beta, span):
    # Odd symmetric root-raised-cosine FIR, with both removable singularities.
    half = span * sps // 2
    t = np.arange(-half, half + 1, dtype=float) / sps
    if beta == 0:
        taps = np.sinc(t)
    else:
        taps = np.empty_like(t)
        zero = np.abs(t) < 1e-12
        singular = np.abs(np.abs(t) - 1 / (4 * beta)) < 1e-10
        other = ~(zero | singular)
        taps[zero] = 1 + beta * (4 / np.pi - 1)
        taps[singular] = beta / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                                                  + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta)))
        u = t[other]
        taps[other] = (np.sin(np.pi * u * (1 - beta)) + 4 * beta * u * np.cos(np.pi * u * (1 + beta))) / (np.pi * u * (1 - (4 * beta * u) ** 2))
    return taps / np.sqrt(np.sum(taps ** 2))


def _circular_filter(x, taps):
    half = len(taps) // 2
    return signal.fftconvolve(np.pad(x, (half, half), mode="wrap"), taps, mode="valid")


def _pulse_shape(symbols, parameters):
    sps = parameters["samples_per_symbol"]
    impulses = np.zeros(len(symbols) * sps, dtype=np.complex128)
    impulses[::sps] = symbols
    return _circular_filter(impulses, _rrc(sps, parameters["rolloff"], parameters["filter_span_symbols"]))


def _gray_decode(values):
    result = np.array(values, dtype=np.int64, copy=True)
    shifted = result >> 1
    while np.any(shifted):
        result ^= shifted
        shifted >>= 1
    return result


def _constellation(indices, mode):
    if mode.endswith("qam") or mode.endswith("QAM"):
        order = int(mode[:-3])
        side = math.isqrt(order)
        # Adjacent amplitudes differ by one Gray-coded source bit.
        i = 2 * _gray_decode(indices % side) - side + 1
        q = 2 * _gray_decode(indices // side) - side + 1
        return (i + 1j * q) / np.sqrt(2 * (order - 1) / 3)
    order = {"bpsk": 2, "qpsk": 4, "8psk": 8, "16psk": 16,
             "BPSK": 2, "QPSK": 4}[mode]
    return np.exp(2j * np.pi * _gray_decode(indices) / order)


def _crc_x25(data):
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xffff


def _bits_lsb(data):
    return [(byte >> bit) & 1 for byte in data for bit in range(8)]


def _ax25_bits(payload, preamble_flags=4, postamble_flags=2):
    # Fixed laboratory UI addresses, no external recipient or user identifier.
    def address(call, last):
        return bytes([ord(c) << 1 for c in call.ljust(6)]) + bytes([0x60 | int(last)])
    frame = address("APRS", False) + address("N0CALL", True) + bytes([0x03, 0xf0]) + payload
    fcs = _crc_x25(frame)
    frame += bytes([fcs & 255, fcs >> 8])
    stuffed, ones = [], 0
    for bit in _bits_lsb(frame):
        stuffed.append(bit)
        ones = ones + 1 if bit else 0
        if ones == 5:
            stuffed.append(0)
            ones = 0
    flag = _bits_lsb(b"\x7e")
    return np.array(flag * preamble_flags + stuffed + flag * postamble_flags, dtype=np.uint8), frame


def _pocsag_codeword(data):
    """Encode 21 data bits with BCH(31,21), then an even overall parity bit."""
    remainder = int(data) << 10
    for bit in range(30, 9, -1):
        if remainder & (1 << bit):
            remainder ^= 0x769 << (bit - 10)
    word = ((int(data) << 10) | remainder) << 1
    return word | (word.bit_count() & 1)


def _pocsag_bits(payload, capcode):
    # Alphanumeric characters are sent least-significant bit first; codewords
    # themselves are serialized MSB first. Function bits 11 select alphanumeric.
    message = [(byte >> bit) & 1 for byte in payload for bit in range(7)]
    words = []
    for start in range(0, len(message), 20):
        chunk = message[start:start + 20]
        value = 0
        for bit in chunk + [0] * (20 - len(chunk)):
            value = (value << 1) | bit
        words.append(_pocsag_codeword((1 << 20) | value))
    address = _pocsag_codeword(((capcode >> 3) << 2) | 3)
    stream = [0x7a89c197] * ((capcode & 7) * 2) + [address] + words
    stream += [0x7a89c197] * ((-len(stream)) % 16)
    bits = [1, 0] * 288
    for start in range(0, len(stream), 16):
        for word in [0x7cd215d8] + stream[start:start + 16]:
            bits.extend((word >> bit) & 1 for bit in range(31, -1, -1))
    return np.asarray(bits, dtype=np.uint8)


def _sample_bits(bits, sample_rate, baud):
    count = math.ceil(len(bits) * sample_rate / baud)
    # Floating point boundary error cannot select a symbol past the final bit.
    indices = np.minimum((np.arange(count) * baud / sample_rate).astype(np.int64), len(bits) - 1)
    return bits[indices]


def _message(rng, n, fs, bandwidth, kind):
    t = np.arange(n) / fs
    if kind in ("tone", "multitone"):
        count = 1 if kind == "tone" else 5
        freq = rng.uniform(.05, .95, count) * bandwidth
        amplitude = rng.uniform(.3, 1, count)
        phases = rng.uniform(-np.pi, np.pi, count)
        result = sum(a * np.sin(2 * np.pi * f * t + phase) for f, a, phase in zip(freq, amplitude, phases))
    else:
        raw = rng.normal(size=n)
        # A causal lowpass is fine for fixtures; its initial transient remains
        # in provenance as part of this exact generated waveform.
        result = signal.sosfilt(signal.butter(6, bandwidth, fs=fs, output="sos"), raw)
    return result / max(np.max(np.abs(result)), 1e-12)


def generate_waveform(spec, *, payload_seed, waveform_seed):
    """Generate finite peak-limited complex64 IQ; never truncate a protocol frame.

    Numeric fixtures are not claims of compliance with any broadcast protocol.
    AX.25 is a laboratory UI frame with reflected CRC, HDLC stuffing and NRZI.
    POCSAG is an alphanumeric burst with preamble, sync and complete batches.
    """
    spec = normalize_waveform_spec(spec)
    payload_seed = _seed(payload_seed, "payload_seed")
    waveform_seed = _seed(waveform_seed, "waveform_seed")
    rng = np.random.default_rng(payload_seed)
    physical = np.random.default_rng(waveform_seed)
    mode, p, fs = spec["mode_id"], spec["parameters"], spec["sample_rate"]
    facets = copy.deepcopy(_BY_ID[mode]["facets"])
    payload = b""
    protocol = {"status": "not_applicable", "checks": [], "scope": "numeric_waveform_only"}
    phase = physical.uniform(-np.pi, np.pi)
    if mode in ("am", "nfm", "wfm", "usb", "lsb"):
        m = _message(rng, p["samples"], fs, p["audio_bandwidth_hz"], p["message_kind"])
        payload = np.asarray(m, dtype="<f8").tobytes()
        if mode == "am":
            iq = 1 + p["modulation_index"] * m
        elif mode in ("nfm", "wfm"):
            iq = np.exp(2j * np.pi * p["deviation_hz"] / fs * np.cumsum(m))
        else:
            iq = signal.hilbert(m)
            if mode == "lsb":
                iq = iq.conjugate()
    elif mode in ("ook", "4ask"):
        order = 2 if mode == "ook" else 4
        indices = rng.integers(0, order, p["symbols"], dtype=np.uint8)
        # Both keyed states remain observable even for a short unlucky payload.
        indices[:2] = [0, order - 1]
        payload = indices.tobytes()
        iq = np.repeat(indices / (order - 1), p["samples_per_symbol"])
        width = round(p["edge_smoothing_symbols"] * p["samples_per_symbol"])
        if width:
            taps = signal.windows.hann(2 * width + 3)[1:-1]
            iq = _circular_filter(iq, taps / sum(taps))
    elif mode.endswith("fsk") or mode in ("msk", "gmsk"):
        order = int(mode[:-3]) if mode.endswith("fsk") else 2
        indices = rng.integers(0, order, p["symbols"], dtype=np.uint8)
        payload = indices.tobytes()
        levels = np.repeat(2 * indices.astype(float) - order + 1, p["samples_per_symbol"])
        if mode == "gmsk":
            sps = p["samples_per_symbol"]
            t = np.arange(-4 * sps, 4 * sps + 1) / sps
            taps = np.exp(-2 * (np.pi * p["bt"] * t) ** 2 / np.log(2))
            levels = _circular_filter(levels, taps / taps.sum())
        h = p.get("modulation_index", .5)
        iq = np.exp(1j * np.pi * h / p["samples_per_symbol"] * np.cumsum(levels))
    elif mode in ("bpsk", "qpsk", "8psk", "16psk", "dbpsk", "dqpsk", "16qam", "64qam", "256qam"):
        mapping_mode = {"dbpsk": "bpsk", "dqpsk": "qpsk"}.get(mode, mode)
        order = int(mode[:-3]) if mode.endswith("qam") else {"bpsk": 2, "qpsk": 4, "8psk": 8, "16psk": 16}[mapping_mode]
        indices = rng.integers(0, order, p["symbols"], dtype=np.uint16)
        payload = indices.astype("<u2").tobytes()
        symbols = _constellation(indices, mapping_mode)
        if mode in ("dbpsk", "dqpsk"):
            symbols = np.cumprod(symbols)
        iq = _pulse_shape(symbols, p)
    elif mode == "ofdm":
        order = {"BPSK": 2, "QPSK": 4, "16QAM": 16, "64QAM": 64}[p["subcarrier_modulation"]]
        indices = rng.integers(0, order, (p["symbols"], p["active_carriers"]), dtype=np.uint16)
        payload = indices.astype("<u2").tobytes()
        symbols = _constellation(indices, p["subcarrier_modulation"])
        grid = np.zeros((p["symbols"], p["fft_size"]), dtype=np.complex128)
        half = p["active_carriers"] // 2
        grid[:, 1:half + 1] = symbols[:, :half]
        grid[:, -half:] = symbols[:, half:]
        time = np.fft.ifft(grid, axis=1)
        cp = p["cyclic_prefix"]
        iq = (np.concatenate((time[:, -cp:], time), axis=1) if cp else time).reshape(-1)
        facets["modulation"] = p["subcarrier_modulation"]
    elif mode == "chirp":
        lengths = np.full(p["chirps"], p["samples"] // p["chirps"], dtype=int)
        lengths[:p["samples"] % p["chirps"]] += 1
        sign = 1 if p["direction"] == "up" else -1
        iq = np.concatenate([np.exp(2j * np.pi * sign * p["bandwidth_hz"] / fs *
                                   (-.5 * np.arange(n) + np.arange(n) ** 2 / (2 * n))) for n in lengths])
        payload = b"linear-chirp"
    elif mode == "cw":
        iq = np.exp(2j * np.pi * p["offset_hz"] / fs * np.arange(p["samples"]))
        payload = b"unmodulated-carrier"
    elif mode == "noise":
        count = p["samples"]
        raw = rng.normal(size=count) + 1j * rng.normal(size=count)
        payload = np.asarray(raw, dtype="<c16").tobytes()
        spectrum = np.fft.fft(raw)
        spectrum[np.abs(np.fft.fftfreq(count, 1 / fs)) > p["bandwidth_hz"] / 2] = 0
        iq = np.fft.ifft(spectrum)
    elif mode == "ax25-afsk1200":
        payload = bytes(rng.integers(32, 127, p["payload_bytes"], dtype=np.uint8))
        bits, frame = _ax25_bits(payload, p["preamble_flags"], p["postamble_flags"])
        if _crc_x25(frame) != 0x0f47:
            raise ValueError("AX.25 encoder failed its FCS residue check.")
        # NRZI: a zero changes tone, a one leaves it unchanged.
        states = np.cumsum(1 - bits.astype(np.int64)) % 2
        sampled = _sample_bits(states, fs, 1200)
        audio = np.sin(2 * np.pi / fs * np.cumsum(np.where(sampled, 1200., 2200.)))
        iq = np.exp(2j * np.pi * p["fm_deviation_hz"] / fs * np.cumsum(audio)) if p["transport"] == "nfm" else audio.astype(complex)
        facets["modulation"] = "FM" if p["transport"] == "nfm" else "AFSK"
        protocol = {"status": "encoder_checks_passed", "checks": ["crc16_x25", "hdlc_bit_stuffing", "nrzi", "bell202_tones"],
                    "scope": "ax25_ui_lab_frame_not_independent_rx_decode", "transport": p["transport"],
                    "frame_bytes": len(frame), "bit_count": len(bits), "baud": 1200}
    else:
        payload = bytes(rng.integers(32, 127, p["payload_bytes"], dtype=np.uint8))
        bits = _pocsag_bits(payload, p["capcode"])
        baud = int(mode[6:])
        sampled = _sample_bits(bits, fs, baud)
        polarity = 1 if p["polarity"] == "normal" else -1
        # POCSAG normal polarity: binary one is the lower RF frequency.
        iq = np.exp(2j * np.pi * p["deviation_hz"] / fs * np.cumsum(polarity * (1 - 2 * sampled.astype(float))))
        protocol = {"status": "encoder_checks_passed", "checks": ["bch31_21", "even_parity", "576bit_preamble", "complete_batches"],
                    "scope": "pocsag_alphanumeric_function3_not_independent_rx_decode", "baud": baud,
                    "bit_count": len(bits), "function": 3}
    iq = np.asarray(iq, dtype=np.complex128) * np.exp(1j * phase)
    limit = MAX_PERIOD_SAMPLES if _BY_ID[mode]["implementation_kind"] == "protocol_encoder" else MAX_NUMERIC_SAMPLES
    if not 1 <= len(iq) <= limit or not np.all(np.isfinite(iq)):
        raise ValueError("Generated waveform is invalid or exceeds its sample budget.")
    peak = float(np.max(np.abs(iq)))
    if peak <= 1e-15:
        raise ValueError("Generated waveform has no energy; change size or payload seed.")
    iq = np.asarray(iq * (.799999 / peak), dtype=np.complex64)
    return {"iq": iq, "sample_rate": fs, "facets": facets, "parameters": copy.deepcopy(p),
            "provenance": {"encoder_version": ENCODER_VERSION, "source_kind": "generated",
                           "payload_seed": payload_seed, "waveform_seed": waveform_seed,
                           "payload_sha256": hashlib.sha256(payload).hexdigest(), "protocol_validation": protocol},
            "estimated_bandwidth_hz": float(_bandwidth(spec)),
            "normalization": {"kind": "peak", "target_peak": .8, "measured_peak": float(np.max(np.abs(iq))),
                              "mean_power": float(np.mean(np.abs(iq.astype(np.complex128)) ** 2)),
                              "sample_count": len(iq), "duration_seconds": len(iq) / fs,
                              "power_unit": "relative_digital_not_dBm", "bandwidth_scope": "design_estimate_not_measured"}}
