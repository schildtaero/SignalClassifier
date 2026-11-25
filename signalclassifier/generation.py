"""Bounded, explicit Pluto -> cable -> receiver dataset campaigns.

Generator references, submitted TX settings and measured RX evidence are kept
separate. Planning never opens a device. A stopped/restarted process never
automatically resumes cyclic transmission.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import queue
import shutil
import threading
import time
import uuid

import numpy as np
from scipy import signal

from . import onair, waveforms
from .db import utcnow
from .receivers import _IQQueue, _Pluto, _RSP1A, validate_config
from .sigmf import SigMFWriter
from .txhardware import PlutoTxAdapter, validate_tx_config

MAX_CASES = 4096
MAX_PERIOD_SAMPLES = 4_194_304


class _ReaderLeaseError(RuntimeError):
    reason = "receiver_unresponsive"
_IMPAIRMENTS = {
    "snr_db": (None, -30, 80), "frequency_offset_hz": (0., -1e6, 1e6),
    "sample_clock_ppm": (0., -1000, 1000), "jitter_rms_samples": (0., 0, 2),
    "iq_gain_db": (0., -12, 12), "iq_phase_deg": (0., -45, 45),
    "dc_offset": (0., 0, .5), "clip_level": (0., 0, 2),
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{name}: {'Ganzzahl' if integer else 'Zahl'} zwischen {low:g} und {high:g} erforderlich.")
    return int(value) if integer else float(value)


def _object(value, name, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"{name}: unbekannte Felder oder kein JSON-Objekt.")
    return dict(value)


def catalogue():
    native = [{**entry, "available": True, "sample_rate": 1e6, "reason": None,
               "provider": "native"} for entry in waveforms.catalogue()]
    external = [{**entry, "provider": "onair"} for entry in onair.catalogue()]
    path = Path(__file__).with_name("data") / "onair-modes.json"
    coverage = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"modes": []}
    return {"schemes": native + external, "engines": {"onair": onair.capabilities()},
            "onair_coverage": coverage,
            "limits": {"max_cases": MAX_CASES, "max_period_samples": MAX_PERIOD_SAMPLES,
                       "max_experiment_classes": 64},
            "warnings": ["Ein OnAir-Katalogeintrag ist kein Nachweis eines Sendegenerators. Scanner und Werkzeuge sind keine eigenen Funkprotokolle.",
                         "Externe OnAir-Testsignale können feste Nutzdaten und quantisierte IQ-Ausschnitte enthalten; kein Nachweis vollständiger Protokollkonformität."]}


def normalize_config(config):
    c = _object(config, "Generator-Konfiguration", {
        "mode_ids", "frequencies_hz", "variants_per_mode", "payload_repetitions", "duration_seconds", "seed", "label_facet",
        "transmitter", "receiver", "settle_seconds", "rx_preroll_seconds", "max_bytes", "parameter_overrides", "impairments", "continue_on_error", "transport"})
    c.setdefault("transport", "cable")
    if c["transport"] not in {"cable", "over_the_air"}:
        raise ValueError("Messaufbau muss cable oder over_the_air sein.")
    ids = c.get("mode_ids", [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 512 or any(not isinstance(x, str) or not x or len(x) > 160 for x in ids):
        raise ValueError("1–512 gültige Generatorverfahren auswählen.")
    c["mode_ids"] = list(dict.fromkeys(ids))
    frequencies = c.get("frequencies_hz", [433.92e6])
    if not isinstance(frequencies, list) or not 1 <= len(frequencies) <= 32:
        raise ValueError("1–32 Mittenfrequenzen erforderlich.")
    c["frequencies_hz"] = list(dict.fromkeys(_number(x, "Frequenz / Hz", 325e6, 3.8e9) for x in frequencies))
    for key, default, low, high, integer in (
        ("variants_per_mode", 3, 1, 32, True), ("payload_repetitions", 3, 1, 100, True),
        ("duration_seconds", 1., .5, 30, False), ("seed", 42, 0, 2**31-1, True),
        ("settle_seconds", .1, .05, 5, False), ("rx_preroll_seconds", .2, .05, 5, False),
        ("max_bytes", 8*1024**3, 1024*1024, 64*1024**3, True)):
        c[key] = _number(c.get(key, default), key, low, high, integer)
    c.setdefault("label_facet", "scheme")
    if c["label_facet"] not in {"scheme", "modulation", "protocol"}:
        raise ValueError("Labelziel muss scheme, modulation oder protocol sein.")
    c.setdefault("continue_on_error", False)
    if type(c["continue_on_error"]) is not bool:
        raise ValueError("continue_on_error muss boolesch sein.")
    tx = _object(c.get("transmitter", {}), "Sender", {"uri", "tx_gain_db", "sample_rate", "rf_bandwidth_hz", "attenuation_offsets_db"})
    tx.setdefault("uri", "ip:192.168.2.1")
    if not isinstance(tx["uri"], str) or not 1 <= len(tx["uri"]) <= 500 or any(ord(x) < 32 for x in tx["uri"]):
        raise ValueError("Ungültige Pluto-Geräteadresse.")
    tx["tx_gain_db"] = _number(tx.get("tx_gain_db", -50), "TX-Gain / dB", -89.75, 0)
    if abs(tx["tx_gain_db"]*4-round(tx["tx_gain_db"]*4)) > 1e-8:
        raise ValueError("TX-Gain muss in Schritten von 0,25 dB angegeben sein.")
    offsets = tx.get("attenuation_offsets_db", [0., 3., 6.])
    if not isinstance(offsets, list) or not 1 <= len(offsets) <= 32:
        raise ValueError("1–32 zusätzliche TX-Dämpfungswerte erforderlich.")
    tx["attenuation_offsets_db"] = [_number(value, "Zusätzliche TX-Dämpfung", 0, 89.75) for value in offsets]
    if any(abs(x*4-round(x*4)) > 1e-8 or tx["tx_gain_db"]-x < -89.75 for x in tx["attenuation_offsets_db"]):
        raise ValueError("Zusätzliche Dämpfung muss in 0,25-dB-Schritten liegen; Basiswert minus Dämpfung darf -89,75 dB nicht unterschreiten.")
    for key, low, high in (("sample_rate", 520834, 61.44e6), ("rf_bandwidth_hz", 200e3, 20e6)):
        tx[key] = None if tx.get(key) is None else _number(tx[key], key, low, high)
    c["transmitter"] = tx
    rx = _object(c.get("receiver", {}), "Empfänger", {"source", "uri", "shared_pluto", "sample_rate", "gain_db"})
    rx.setdefault("source", "rsp1a")
    if rx["source"] not in {"rsp1a", "pluto"}:
        raise ValueError("Für die Kabelmessung RSP1A oder Pluto als echten Empfänger wählen.")
    rx.setdefault("uri", "")
    rx.setdefault("shared_pluto", False)
    if not isinstance(rx["uri"], str) or len(rx["uri"]) > 500 or any(ord(x) < 32 for x in rx["uri"]):
        raise ValueError("Ungültige Empfängeradresse.")
    if type(rx["shared_pluto"]) is not bool or (rx["shared_pluto"] and rx["source"] != "pluto"):
        raise ValueError("Gemeinsamer RX/TX-Handle ist nur mit Pluto möglich.")
    rx["sample_rate"] = _number(rx.get("sample_rate", 2e6), "RX-Abtastrate", 520834, 10e6)
    rx["gain_db"] = _number(rx.get("gain_db", 30), "RX-Gain", -3 if rx["source"] == "pluto" else -20, 71 if rx["source"] == "pluto" else 100)
    if rx["shared_pluto"]:
        rx["uri"] = tx["uri"]
    elif rx["source"] == "pluto" and (rx["uri"] or "ip:192.168.2.1") == tx["uri"]:
        raise ValueError("Für denselben Pluto den gemeinsamen RX/TX-Modus wählen; sonst eine andere RX-Geräteadresse angeben.")
    if rx["source"] == "rsp1a" and any(x > 2e9 for x in c["frequencies_hz"]):
        raise ValueError("Mit RSP1A liegt der gemeinsame Kabelbereich bei 325 MHz bis 2 GHz.")
    c["receiver"] = rx
    overrides = c.get("parameter_overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - set(c["mode_ids"]):
        raise ValueError("Parameter-Overrides nur für ausgewählte Verfahren angeben.")
    c["parameter_overrides"] = copy.deepcopy(overrides)
    effects = _object(c.get("impairments", {}), "Kanalvariationen", _IMPAIRMENTS)
    for key, (default, low, high) in _IMPAIRMENTS.items():
        values = effects.get(key, [default])
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError(f"{key}: 1–32 Variationswerte erforderlich.")
        effects[key] = [None if key == "snr_db" and value is None else _number(value, key, low, high) for value in values]
    c["impairments"] = effects
    return c


def _params_for(entry, overrides, index, seed):
    schema = entry.get("parameters", {})
    if not isinstance(overrides, dict) or set(overrides) - set(schema):
        raise ValueError(f"{entry['id']}: unbekannte Generatorparameter.")
    rng = np.random.default_rng(seed)
    values = {}
    authored = entry.get("default_variations") or [{}]
    if index:
        values.update(authored[(index-1) % len(authored)])
    for key, spec in schema.items():
        if key in overrides:
            choices = overrides[key]
            if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                raise ValueError(f"{entry['id']}.{key}: 1–32 Werte erforderlich.")
            values[key] = choices[index % len(choices)]
        elif index and entry.get("provider") == "native" and key not in values:
            if spec.get("choices"):
                values[key] = spec["choices"][index % len(spec["choices"])]
            elif key not in {"samples", "symbols", "payload_bytes", "fft_size", "cyclic_prefix", "active_carriers", "active_subcarriers", "samples_per_symbol"}:
                default = spec.get("default")
                if isinstance(default, (int, float)) and not isinstance(default, bool):
                    value = min(spec.get("max", default), max(spec.get("min", default), default * float(rng.choice([.8, 1., 1.2]))))
                    values[key] = int(round(value)) if spec.get("type") == "integer" else value
    return values


def _case_spec(entry, c, variant):
    requested = c["transmitter"]["sample_rate"]
    native_rate = float(entry.get("sample_rate") or 1e6)
    params = _params_for(entry, c["parameter_overrides"].get(entry["id"], {}), variant, c["seed"] + variant * 7919)
    if entry.get("provider") == "native":
        rate = requested or (c["receiver"]["sample_rate"] if c["receiver"]["shared_pluto"] else 1e6)
        spec = waveforms.normalize_waveform_spec({"mode_id": entry["id"], "sample_rate": rate, "parameters": params})
        bandwidth = waveforms.estimate_bandwidth(spec)
    else:
        # External fixtures retain their native rate/quantization. The explicit
        # prefix is logged; it is never advertised as a complete protocol frame.
        spec = onair.normalize_spec({"mode_id": entry["id"], "parameters": params,
                "sample_count": min(MAX_PERIOD_SAMPLES, max(256, int(native_rate * .5)))})
        bandwidth = float(entry.get("estimated_bandwidth_hz") or native_rate * .8)
        rate = requested or (c["receiver"]["sample_rate"] if c["receiver"]["shared_pluto"] else max(520834., native_rate))
    if not 520834 <= rate <= 61.44e6:
        raise ValueError(f"{entry['id']}: TX-Rate außerhalb des Standardbereichs des Pluto.")
    bandwidth_guard = 1. if entry.get("provider") == "onair" else .8
    if bandwidth > 20e6 or bandwidth > bandwidth_guard * rate:
        raise ValueError(f"{entry['id']}: Bandbreite {bandwidth:g} Hz passt nicht zur Pluto-TX-Rate {rate:g} S/s.")
    return spec, float(rate), bandwidth


def build_plan(config, *, entries=None):
    c = normalize_config(config)
    requested_variants = c["variants_per_mode"]
    lengths = [len(values) for values in c["impairments"].values()]+[len(c["transmitter"]["attenuation_offsets_db"])]
    for override in c["parameter_overrides"].values():
        if not isinstance(override, dict):
            raise ValueError("Generator-Overrides müssen je Verfahren ein Objekt enthalten.")
        for values in override.values():
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError("Jeder Generator-Override benötigt 1–32 Werte.")
            lengths.append(len(values))
    c["variants_per_mode"] = max(requested_variants, *lengths)
    entries = entries if entries is not None else catalogue()["schemes"]
    by_id = {entry["id"]: entry for entry in entries}
    count = len(c["mode_ids"]) * c["variants_per_mode"] * c["payload_repetitions"] * len(c["frequencies_hz"])
    if count > MAX_CASES:
        raise ValueError(f"Plan enthält {count} Fälle; höchstens {MAX_CASES} zulässig. Varianten/Wiederholungen/Frequenzen verringern.")
    errors, warnings, cases = [], [], []
    if c["variants_per_mode"] != requested_variants:
        warnings.append(f"Variantenanzahl auf {c['variants_per_mode']} erhöht, damit jeder ausdrücklich gewählte Variationswert wenigstens einmal vorkommt; kein vollständiges kartesisches Raster.")
    labels = set()
    for mode_id in c["mode_ids"]:
        entry = by_id.get(mode_id)
        if not entry or not entry.get("available", True):
            errors.append(f"{mode_id}: {entry.get('reason') if entry else 'Generator nicht registriert'}")
            continue
        if entry.get("provider") == "onair":
            warnings.append(f"{mode_id}: externer Testsignal-Ausschnitt mit fester Nutzdatenherkunft; Wiederholungen erzeugen keine unabhängigen Payloads.")
        elif mode_id in {"cw", "chirp"}:
            warnings.append(f"{mode_id}: Kontrollsignal ohne frische Nutzdaten; alle Varianten bleiben zusammen und sind im vorgeplanten Split nur Trainingsdaten.")
        for variation in range(c["variants_per_mode"]):
            try:
                spec, tx_rate, bandwidth = _case_spec(entry, c, variation)
                facets = copy.deepcopy(entry.get("facets", {}))
                if mode_id == "ofdm":
                    facets["modulation"] = spec["parameters"]["subcarrier_modulation"]
                elif mode_id == "ax25-afsk1200":
                    facets["modulation"] = "FM" if spec["parameters"]["transport"] == "nfm" else "AFSK"
                elif mode_id in {"onair:general", "onair:am"}:
                    facets["modulation"] = {0: "NFM", 1: "WFM", 2: "AM", 3: "USB", 4: "LSB", 5: "CW"}.get(
                        spec["parameters"].get("sopt0", 2 if mode_id == "onair:am" else 0))
                    if spec["parameters"].get("sopt1") == 1:
                        raise ValueError("OnAir-General/AM mit sopt1=1 erzeugt nur Rauschen. Statt eines AM-Labels den expliziten Rauschgenerator wählen.")
                label = entry["name"] if c["label_facet"] == "scheme" else facets.get(c["label_facet"])
                if not isinstance(label, str) or not label or len(label) > 120:
                    raise ValueError(f"Keine belegte Facette {c['label_facet']} als Klassenlabel verfügbar.")
                labels.add(label)
                if c["receiver"]["shared_pluto"] and abs(c["receiver"]["sample_rate"]-tx_rate) > .001:
                    raise ValueError("Ein gemeinsamer Pluto erfordert identische RX-/TX-Abtastraten.")
                effects = {key: values[variation % len(values)] for key, values in c["impairments"].items()}
                occupied = bandwidth + 2*abs(effects["frequency_offset_hz"])
                bandwidth_guard = 1. if entry.get("provider") == "onair" else .8
                if occupied > bandwidth_guard * min(tx_rate, c["receiver"]["sample_rate"]):
                    raise ValueError(f"Signal einschließlich Frequenzversatz benötigt {occupied:g} Hz; RX-/TX-Fenster vergrößern.")
                for repetition in range(c["payload_repetitions"]):
                    has_payload_cohorts = entry.get("provider") == "native" and mode_id not in {"cw", "chirp"}
                    split = ("train", "validation", "test")[repetition % 3] if has_payload_cohorts else "train"
                    payload_seed = c["seed"] + repetition * 104729
                    source_origin = {"provider": entry.get("provider"), "mode_id": mode_id,
                                     "payload_seed": payload_seed if has_payload_cohorts else None,
                                     "generator_version": waveforms.ENCODER_VERSION if entry.get("provider") == "native" else onair.SOURCE_COMMIT}
                    root = "generated-payload:" + _hash(source_origin)
                    for frequency in c["frequencies_hz"]:
                        rx_config = validate_config({**c["receiver"], "center_frequency_hz": frequency,
                            "mode": "monitor", "duration_seconds": c["duration_seconds"], "classification_enabled": False})
                        tx_config = validate_tx_config({"uri": c["transmitter"]["uri"], "center_frequency_hz": frequency,
                            "sample_rate": tx_rate, "rf_bandwidth_hz": int(math.ceil(c["transmitter"]["rf_bandwidth_hz"] or min(20e6, max(200e3, occupied*1.2)))),
                            "tx_gain_db": c["transmitter"]["tx_gain_db"]-c["transmitter"]["attenuation_offsets_db"][variation % len(c["transmitter"]["attenuation_offsets_db"])]})
                        cases.append({"case_id": f"case-{len(cases)+1:04d}", "mode_id": mode_id,
                            "provider": entry.get("provider", "native"), "implementation_kind": entry["implementation_kind"],
                            "label": label, "label_facet": c["label_facet"], "facets": facets,
                            "waveform_spec": spec, "payload_seed": payload_seed, "waveform_seed": c["seed"] + repetition*104729,
                            "channel_seed": c["seed"]+variation*15485863+repetition*32452843,
                            "payload_root_id": root, "planned_split": split,
                            "variation": variation, "repetition": repetition, "tx_config": tx_config, "rx_config": rx_config,
                            "tx_gain_baseline_db": c["transmitter"]["tx_gain_db"],
                            "additional_attenuation_db": c["transmitter"]["attenuation_offsets_db"][variation % len(c["transmitter"]["attenuation_offsets_db"])],
                            "impairments": effects, "estimated_bandwidth_hz": bandwidth,
                            "requested_rx_samples": int(math.ceil(c["duration_seconds"]*c["receiver"]["sample_rate"]))})
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"{mode_id}, Variante {variation+1}: {exc}")
    byte_count = sum(item["requested_rx_samples"]*8 for item in cases)
    if byte_count > c["max_bytes"]:
        errors.append(f"Plan benötigt ungefähr {byte_count/1024**3:.2f} GiB IQ-Daten; Gesamtbudget beträgt {c['max_bytes']/1024**3:.2f} GiB.")
    if len(labels) > 64:
        warnings.append("Datenerzeugung unterstützt diese Auswahl; ein einzelnes Klassifikationsexperiment bleibt auf 64 Klassen begrenzt.")
    warnings += ["Automatische Labels sind kontrollierte Generatorreferenzen, keine unabhängige Protokolldecodierung oder verifizierte HF-Synchronisierung.",
                 "Gleiche Nutzdatenursprünge und ihre Varianten bleiben zusammen. Eine Kabelkampagne ersetzt keine unabhängigen Sender-/Empfängerbedingungen.",
                 "Zyklische TX-Puffer können einen Hostabsturz überdauern. Softwareabschaltung ist kein physischer HF-Nachweis."]
    summary = {"cases": len(cases), "mode_count": len(c["mode_ids"]), "estimated_rx_bytes": byte_count,
               "estimated_seconds": len(cases)*(c["duration_seconds"]+c["settle_seconds"]+c["rx_preroll_seconds"]),
               "classes": sorted(labels), "label_facet": c["label_facet"], "sampling": "bounded_seeded_stratified"}
    return {"id": uuid.uuid4().hex, "sha256": _hash({"config": c, "cases": cases}), "ready": bool(cases) and not errors,
            "config": c, "summary": summary, "cases": cases, "errors": errors, "warnings": list(dict.fromkeys(warnings)), "created_at": utcnow()}


def apply_impairments(iq, sample_rate, effects, seed):
    """Exact logged software changes; never claim measured RF-channel SNR."""
    x = np.asarray(iq, dtype=np.complex64).copy()
    rng = np.random.default_rng(seed)
    initial_offset = int(rng.integers(0, len(x))) if len(x) else 0
    x = np.roll(x, initial_offset)
    ppm, jitter = effects.get("sample_clock_ppm", 0), effects.get("jitter_rms_samples", 0)
    if ppm or jitter:
        indices = np.arange(len(x), dtype=np.float64)
        warped = np.mod(indices*(1+ppm*1e-6)+rng.normal(0, jitter, len(x)), len(x))
        x = (np.interp(warped, np.arange(len(x)+1), np.append(x.real, x.real[0])) +
             1j*np.interp(warped, np.arange(len(x)+1), np.append(x.imag, x.imag[0]))).astype(np.complex64)
    phase = math.radians(effects.get("iq_phase_deg", 0))
    gain = 10**(effects.get("iq_gain_db", 0)/20)
    x = (x.real + 1j*gain*(x.imag*math.cos(phase)+x.real*math.sin(phase))).astype(np.complex64)
    x += effects.get("dc_offset", 0)
    cfo = effects.get("frequency_offset_hz", 0)
    if cfo:
        x *= np.exp(2j*np.pi*cfo*np.arange(len(x))/sample_rate).astype(np.complex64)
    clip = effects.get("clip_level", 0)
    if clip:
        x = (np.clip(x.real, -clip, clip) + 1j*np.clip(x.imag, -clip, clip)).astype(np.complex64)
    snr = effects.get("snr_db")
    if snr is not None:
        power = float(np.mean(np.abs(x)**2))
        sigma = math.sqrt(power/(2*10**(snr/10)))
        x += (sigma*(rng.normal(size=len(x))+1j*rng.normal(size=len(x)))).astype(np.complex64)
    peak = float(np.max(np.abs(x))) if len(x) else 0
    if not len(x) or not np.isfinite(x).all() or peak <= 0:
        raise ValueError("Generator lieferte kein endliches, nichtleeres Sendesignal.")
    scale = min(1., .8/peak)
    x *= np.float32(scale)
    return x, {"settings": copy.deepcopy(effects), "seed": seed, "peak_scale": scale,
               "cyclic_start_offset_samples": initial_offset,
               "cyclic_boundary_scope": "finite cyclic waveform; CFO, noise and channel errors repeat each period",
               "snr_scope": "injected_noise_relative_to_software_waveform_power_before_noise; RF SNR unknown",
               "noise_scope": "finite periodic IQ noise repeated with the cyclic waveform",
               "order": ["clock_and_jitter", "iq_imbalance", "dc", "frequency_offset", "clipping", "noise", "peak_scaling"]}


class GenerationManager:
    """One reserved receiver, one explicit TX owner, bounded capture queues."""

    def __init__(self, data_dir, store, *, transmitter_factory=None, receiver_factory=None):
        self.data_dir, self.store = Path(data_dir), store
        self.root = self.data_dir / "generation"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tx_lifecycle_lock = threading.RLock()
        self._mute_lock = threading.Lock()
        self._mute_thread = None
        self._adapter_retiring = False
        self._reader_thread = None
        self._reader_join_seconds = 6.
        self._stop_join_seconds = 2.
        self._cancel = threading.Event()
        self._thread = None
        self._adapter = None
        self._plans = {}
        self._tx_factory = transmitter_factory or PlutoTxAdapter
        self._rx_factory = receiver_factory
        self._state = {"status": "idle", "busy": False, "tx_active": False, "progress": 0,
                       "completed_cases": 0, "failed_cases": 0, "total_cases": 0,
                       "recordings_saved": [], "recordings_saved_count": 0, "error": None}
        for row in store.all("generation"):
            if row.get("busy") or row.get("status") in {"running", "stopping"}:
                row.update(status="interrupted", busy=False, tx_active=None,
                           error="Anwendung während zyklischer TX-Erzeugung beendet. HF-Zustand unbekannt; TX ausdrücklich stoppen und Aufbau prüfen.", finished_at=utcnow())
                store.put("generation", row["id"], row)
                if self._state["status"] == "idle":
                    self._state = row

    def status(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def plan(self, config):
        plan = build_plan(config)
        free = shutil.disk_usage(self.data_dir).free
        if plan["summary"]["estimated_rx_bytes"] > max(0, free-128*1024**2):
            plan["errors"].append("Nicht genügend freier Speicher für den vollständigen IQ-Plan plus 128 MiB Reserve.")
            plan["ready"] = False
        with self._lock:
            if len(self._plans) >= 4:
                self._plans.pop(next(iter(self._plans)))
            self._plans[plan["id"]] = copy.deepcopy(plan)
        return plan

    def start(self, payload, *, base_counts=None):
        payload = _object(payload, "Generatorstart", {"plan_id", "plan_sha256", "transmit_enabled", "setup_confirmed", "cabled_setup"})
        if payload.get("transmit_enabled") is not True:
            raise ValueError("Senden vor dem Start ausdrücklich aktivieren.")
        with self._lock:
            if (self._state.get("busy") or self._thread is not None and self._thread.is_alive()
                    or self._mute_thread is not None and self._mute_thread.is_alive() or self._adapter_retiring):
                raise ValueError("Ein Signalgeneratorlauf läuft bereits oder wird noch beendet.")
            if self._state.get("tx_active") is None:
                raise ValueError("TX-Zustand ist unbekannt. Vor einem neuen Lauf zuerst TX ausdrücklich stoppen.")
            plan = self._plans.get(payload.get("plan_id"))
            if not plan or not plan["ready"] or payload.get("plan_sha256") != plan["sha256"]:
                raise ValueError("Ein passender, unveränderter und ausführbarer Plan ist erforderlich.")
            if not (payload.get("setup_confirmed") is True or payload.get("cabled_setup") is True and plan["config"]["transport"] == "cable"):
                raise ValueError("Den zum Plan passenden Kabel-/Antennen-Messaufbau ausdrücklich bestätigen.")
            plan = copy.deepcopy(plan)
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir()
            self._write_json(folder / "plan.json", plan)
            self._cancel = threading.Event()
            self._state = {"id": identifier, "status": "running", "busy": True, "tx_active": False,
                "created_at": utcnow(), "config": plan["config"], "summary": plan["summary"],
                "plan_id": plan["id"], "plan_sha256": plan["sha256"], "progress": 0,
                "completed_cases": 0, "failed_cases": 0, "total_cases": len(plan["cases"]),
                "bytes_written": 0,
                "recordings_saved": [], "recordings_saved_count": 0, "verified_recordings_count": 0,
                "base_counts": dict(base_counts or {}), "error": None, "current_case": None,
                "transmitter": None, "reference_kind": "controlled_generator_reference"}
            self._persist()
            self._thread = threading.Thread(target=self._run, args=(plan, identifier), name="signalclassifier-generator", daemon=True)
            self._thread.start()
            return self.status()

    @staticmethod
    def _write_json(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+"\n", encoding="utf-8")
        temporary.replace(path)

    def _persist(self):
        # Only called while RF is muted or after a completed capture. Keep the
        # indexed record small; full immutable cases/events live in own files.
        self.store.put("generation", self._state["id"], self._state)

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def _tx_update(self, adapter=None, owner_id=None):
        adapter = adapter if adapter is not None else self._adapter
        if adapter is not None:
            current = adapter.status()
            with self._lock:
                if adapter is self._adapter and (owner_id is None or self._state.get("id") == owner_id):
                    self._state.update(transmitter=current, tx_active=current.get("tx_active"))
            return current
        return {}

    def _request_mute(self, reason):
        """Mute independently of disk and keep unresolved SDK work leased."""
        with self._mute_lock:
            adapter = self._adapter
            if adapter is None:
                return None
            if self._mute_thread is not None and self._mute_thread.is_alive():
                return self._mute_thread
            if self._adapter_retiring:
                return None
            owner_id = self.status().get("id")
            self._update(tx_active=None, tx_shutdown_pending=True)

            def mute():
                try:
                    with self._tx_lifecycle_lock:
                        adapter.stop(reason)
                    self._tx_update(adapter, owner_id)
                except Exception as exc:
                    with self._lock:
                        if self._state.get("id") == owner_id and self._adapter is adapter:
                            self._state.update(tx_active=None, cleanup_error=str(exc)[:500])
                finally:
                    with self._lock:
                        if self._state.get("id") == owner_id and self._adapter is adapter:
                            self._state["tx_shutdown_pending"] = False

            self._mute_thread = threading.Thread(target=mute, name="generator-tx-mute", daemon=True)
            self._mute_thread.start()
            return self._mute_thread

    def stop(self):
        self._cancel.set()
        with self._lock:
            busy = self._state.get("busy", False)
            unresolved = self._state.get("tx_active") is None
            config = copy.deepcopy(self._state.get("config"))
            adapter = self._adapter
            if busy:
                self._state["status"] = "stopping"
        if adapter is not None:
            worker = self._request_mute("user_stop")
            if worker is not None:
                worker.join(timeout=self._stop_join_seconds)
        elif unresolved and config:
            # Explicit user stop may reconnect to mute an orphaned cyclic DMA;
            # startup itself never opens a transmitter or resumes a campaign.
            initial = {**config["transmitter"], "sample_rate": config["transmitter"].get("sample_rate") or 1e6,
                       "center_frequency_hz": config["frequencies_hz"][0]}
            if initial.get("rf_bandwidth_hz") is None:
                initial.pop("rf_bandwidth_hz", None)
            with self._mute_lock:
                if self._mute_thread is not None and self._mute_thread.is_alive():
                    worker = self._mute_thread
                else:
                    owner_id = self.status().get("id")
                    self._update(busy=True, status="stopping", tx_active=None, tx_shutdown_pending=True)

                    def emergency_mute():
                        emergency, error = None, None
                        try:
                            emergency = self._tx_factory(initial)
                            with self._mute_lock:
                                self._adapter = emergency
                                self._adapter_retiring = True
                            with self._tx_lifecycle_lock:
                                emergency.open()
                        except Exception as exc:
                            error = str(exc)[:500]
                        finally:
                            if emergency is not None:
                                try:
                                    emergency.close()
                                except Exception as exc:
                                    error = str(exc)[:500]
                                current = self._tx_update(emergency, owner_id)
                            else:
                                current = {}
                            with self._mute_lock:
                                self._adapter = None
                                self._adapter_retiring = False
                            with self._lock:
                                if self._state.get("id") == owner_id:
                                    self._state.update(busy=False, status="interrupted", tx_shutdown_pending=False,
                                        tx_active=False if not error and current.get("off_acknowledged") is True else None,
                                        error=error or (None if current.get("off_acknowledged") is True else "Notstop nicht bestätigt; HF-Zustand unbekannt."))
                                    if owner_id:
                                        self._persist()

                    self._mute_thread = threading.Thread(target=emergency_mute, name="generator-emergency-mute", daemon=True)
                    worker = self._mute_thread
                    worker.start()
            worker.join(timeout=self._stop_join_seconds)
        return self.status()

    def close(self):
        self._cancel.set()
        if self._adapter is not None:
            worker = self._request_mute("application_shutdown")
            if worker is not None:
                worker.join(timeout=2)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=8)
        worker = self._mute_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=8)

    def runs(self):
        return self.store.all("generation")

    def detail(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(x not in "0123456789abcdef" for x in identifier):
            raise ValueError("Ungültige Generatorlaufkennung.")
        state = self.store.get("generation", identifier)
        if state is None:
            raise ValueError("Generatorlauf nicht gefunden.")
        folder = self.root / identifier
        records = []
        journal = folder / "records.jsonl"
        if journal.is_file():
            if journal.stat().st_size > 128*1024**2:
                raise ValueError("Generatorjournal überschreitet das Lesebudget.")
            for line in journal.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        records.append({"status": "journal_warning", "error": "Unvollständiger Journaleintrag nach Unterbrechung."})
        return {**state, "plan": json.loads((folder / "plan.json").read_text(encoding="utf-8")), "records": records}

    def _make_waveform(self, case, folder):
        if case["provider"] == "native":
            value = waveforms.generate_waveform(case["waveform_spec"], payload_seed=case["payload_seed"], waveform_seed=case["waveform_seed"])
        else:
            value = onair.generate(case["waveform_spec"], workdir=folder / "onair-work", stop_event=self._cancel, timeout=30)
        iq = np.asarray(value["iq"], dtype=np.complex64)
        native_rate, target_rate = float(value["sample_rate"]), float(case["tx_config"]["sample_rate"])
        if abs(native_rate-target_rate) > .001:
            ratio = Fraction(target_rate/native_rate).limit_denominator(4096)
            if abs(native_rate*ratio.numerator/ratio.denominator-target_rate) > .001:
                raise ValueError("Generatorrate lässt sich nicht exakt innerhalb des Resamplingbudgets auf Pluto abbilden.")
            estimated_count = math.ceil(len(iq)*ratio.numerator/ratio.denominator)
            if estimated_count > MAX_PERIOD_SAMPLES:
                raise ValueError("Vollständiger resampelter Sendeausschnitt überschreitet den begrenzten zyklischen TX-Puffer.")
            iq = signal.resample_poly(iq, ratio.numerator, ratio.denominator).astype(np.complex64)
        if not 1 <= len(iq) <= MAX_PERIOD_SAMPLES:
            raise ValueError("Erzeugte TX-Periode überschreitet das Samplebudget.")
        clean_hash = hashlib.sha256(iq.tobytes()).hexdigest()
        iq, effects = apply_impairments(iq, target_rate, case["impairments"], case["channel_seed"])
        provenance = copy.deepcopy(value.get("provenance", {}))
        root_id = case["payload_root_id"]
        if value.get("facets") != case["facets"]:
            raise ValueError("Tatsächliche Generatorfacetten stimmen nicht mit dem eingefrorenen Plan überein.")
        return iq, {"generator": provenance, "normalization": value.get("normalization"),
                    "native_sample_rate": native_rate, "tx_sample_rate": target_rate,
                    "resampled": abs(native_rate-target_rate) > .001, "baseband_sha256": clean_hash,
                    "transmitted_iq_sha256": hashlib.sha256(iq.tobytes()).hexdigest(),
                    "tx_period_samples": len(iq), "software_channel": effects, "payload_root_id": root_id}

    def _open_receiver(self, case, config):
        if self._rx_factory is not None:
            return self._rx_factory(case["rx_config"], self._adapter)
        if config["receiver"]["shared_pluto"]:
            return self._adapter.shared_receiver(case["rx_config"])
        return (_RSP1A if case["rx_config"]["source"] == "rsp1a" else _Pluto)(case["rx_config"])

    def _capture(self, receiver, writer, count, preroll_seconds):
        rate = float(receiver.sample_rate)
        preroll = int(math.ceil(rate*preroll_seconds))
        target = count+preroll
        blocks = _IQQueue(max(2, (64*1024**2)//(131072*8)))
        done, stop_reader = threading.Event(), threading.Event()
        fault = []
        read_samples = 0
        first_receive = None
        last_receive = None
        deadline = time.monotonic()+max(8., target/rate*3+5)

        def read_loop():
            nonlocal read_samples, first_receive, last_receive
            try:
                while read_samples < target and not stop_reader.is_set() and not self._cancel.is_set():
                    if time.monotonic() > deadline:
                        raise RuntimeError("Empfangszeitbudget überschritten; angeforderte Samplezahl nicht erreicht.")
                    raw = receiver.read(min(131072, target-read_samples))
                    if raw is None:
                        continue
                    iq = np.asarray(raw, dtype=np.complex64)
                    if iq.ndim != 1 or not len(iq) or not np.isfinite(iq).all():
                        raise RuntimeError("Empfänger lieferte leere oder ungültige IQ-Daten.")
                    stamp = utcnow()
                    first_receive = first_receive or stamp
                    last_receive = stamp
                    source_start = read_samples
                    read_samples += len(iq)
                    try:
                        blocks.put_nowait((iq.copy(), source_start, stamp))
                    except queue.Full as exc:
                        raise RuntimeError("IQ-Eingangspuffer der Generatoraufnahme übergelaufen; kein stilles Überspringen.") from exc
            except Exception as exc:
                fault.append(exc)
                # The writer may be slow or blocked. Signal the independent
                # mute coordinator directly from the reader's error path.
                self._request_mute("receiver_failure")
            finally:
                done.set()

        reader = threading.Thread(target=read_loop, name="generator-hardware-reader", daemon=True)
        self._reader_thread = reader
        reader.start()
        retained = 0
        power_sum, clipped, peak = 0., 0, 0.
        first_retained, last_retained = None, None
        try:
            while (not done.is_set() or not blocks.empty()) and not self._cancel.is_set():
                if fault:
                    break
                try:
                    raw, source_start, stamp = blocks.get(timeout=.1)
                except queue.Empty:
                    continue
                start = max(0, preroll-source_start)
                data = raw[start:start+max(0, count-retained)]
                if len(data):
                    host_end = datetime.fromisoformat(stamp)
                    first_retained = first_retained or (host_end-timedelta(seconds=(len(raw)-start)/rate)).isoformat()
                    last_retained = (host_end-timedelta(seconds=(len(raw)-start-len(data))/rate)).isoformat()
                    writer.append(data)
                    retained += len(data)
                    power_sum += float(np.sum(np.abs(data)**2, dtype=np.float64))
                    clipped += int(np.count_nonzero((np.abs(data.real) >= .999) | (np.abs(data.imag) >= .999)))
                    peak = max(peak, float(np.max(np.abs(data))))
                    self._update(current_rx_samples=retained, current_rx_target_samples=count)
                if retained >= count:
                    break
        finally:
            stop_reader.set()
            reader.join(timeout=self._reader_join_seconds)
        if reader.is_alive():
            self._update(status="stopping", receiver_lease_unresolved=True)
            self._request_mute("receiver_unresponsive")
            raise _ReaderLeaseError("Empfangsthread reagiert nicht innerhalb des Stopzeitbudgets; Empfänger bleibt bis zum tatsächlichen Ende reserviert.")
        evidence = {"requested_samples": count, "retained_samples": retained, "source_samples_read": read_samples,
                    "discarded_preroll_samples": min(preroll, read_samples), "host_first_received_at": first_receive,
                    "discarded_after_stop_samples": max(0, read_samples-preroll-retained),
                    "host_last_received_at": last_receive, "host_window_closed_at": utcnow(),
                    "host_first_retained_estimate_utc": first_retained, "host_last_retained_estimate_utc": last_retained,
                    "timestamp_scope": "host_receive_end_minus_block_tail; USB latency and RF timestamps unverified",
                    "rf_timestamp_verified": False, "gap_free_verified": False,
                    "queue_high_water_bytes": blocks.metrics()["queue_high_water_bytes"],
                    "rms_dbfs": 10*math.log10(max(power_sum/retained, 1e-30)) if retained else None,
                    "clipping_fraction": clipped/retained if retained else None, "peak_magnitude": peak if retained else None,
                    "power_scope": "receiver digital full scale; not calibrated dBm or RF SNR"}
        if fault:
            error = fault[0]
            evidence.update(error=str(error)[:1000], reported_gaps=1,
                            reason=getattr(error, "reason", "receiver_error"))
        elif self._cancel.is_set():
            evidence.update(error="Aufnahme durch Benutzer gestoppt.", reason="cancelled", reported_gaps=0)
        elif retained != count:
            evidence.update(error="Empfangsfenster unvollständig.", reason="incomplete", reported_gaps=0)
        evidence["complete"] = retained == count and not fault and not self._cancel.is_set()
        if evidence["complete"] and peak <= 1e-12:
            evidence.update(complete=False, error="Empfänger lieferte ausschließlich Null-IQ; kein brauchbares Generator-Referenzfenster.", reason="zero_iq")
        return evidence

    def _record_case(self, case, plan, identifier, folder, waveform):
        c = plan["config"]
        iq, reference = waveform
        record_id = f"gen-{identifier[:16]}-{case['case_id']}"
        generation = {"campaign_id": identifier, "case_id": case["case_id"], "plan_sha256": plan["sha256"],
            "mode_id": case["mode_id"], "label_facet": case["label_facet"], "facets": case["facets"],
            "expected_label": case["label"], "reference_kind": "controlled_generator_reference",
            "reference_scope": "submitted_generator_settings; not an independent RX-class or protocol decode",
            "requested_waveform": case["waveform_spec"], "transport": c["transport"],
            "payload_seed": case["payload_seed"], "waveform_seed": case["waveform_seed"],
            "planned_split": case["planned_split"] if case["provider"] == "native" and case["mode_id"] not in {"cw", "chirp"} else "train",
            "tx_gain_baseline_db": case["tx_gain_baseline_db"], "additional_attenuation_db": case["additional_attenuation_db"],
            "rf_origin_verified": False, "protocol_rx_verified": False, **reference}
        if case["label_facet"] == "scheme":
            generation["facets"] = {**generation["facets"], "scheme": case["label"]}
        receiver, writer = None, None
        quality = {"complete": False, "retained_samples": 0, "reported_gaps": 0}
        error = None
        submitted = None
        try:
            with self._tx_lifecycle_lock:
                self._adapter.configure(case["tx_config"])
                if self._cancel.is_set():
                    raise RuntimeError("Generator vor dem Senden gestoppt.")
                submitted = self._adapter.start_cyclic(iq)
            self._tx_update()
            if self._cancel.wait(c["settle_seconds"]):
                raise RuntimeError("Generator während TX-Einschwingen gestoppt.")
            receiver = self._open_receiver(case, c)
            actual_rate, actual_center = float(receiver.sample_rate), float(receiver.center_frequency)
            if abs(actual_rate-case["rx_config"]["sample_rate"]) > case["rx_config"]["sample_rate"]*.001 or abs(actual_center-case["tx_config"]["center_frequency_hz"]) > 1:
                raise RuntimeError("Empfänger-Readback passt nicht zum geplanten Aufnahmefenster.")
            target_count = int(math.ceil(actual_rate*c["duration_seconds"]))
            if target_count*8+self.status().get("bytes_written", 0) > c["max_bytes"]:
                raise RuntimeError("Tatsächliche RX-Rate würde das verbleibende IQ-Bytebudget überschreiten.")
            generation.update(tx=submitted, rx={"source": c["receiver"]["source"], "sample_rate": actual_rate,
                "center_frequency_hz": actual_center, "gain_db_requested": c["receiver"]["gain_db"], "gain_db_measured": None,
                "shared_pluto": c["receiver"]["shared_pluto"]})
            writer = SigMFWriter(self.data_dir / "recordings" / record_id, actual_rate, actual_center,
                extra={"sc:source": c["receiver"]["source"], "sc:mode": "generated_reference_capture",
                       "core:description": f"{case['label']} · Generatorreferenz · Variante {case['variation']+1}",
                       "sc:session_id": identifier, "signalclassifier:session_id": identifier,
                       "signalclassifier:group_id": identifier, "sc:origin_recording_id": reference["payload_root_id"],
                       "sc:declared_source_synthetic": True, "sc:generation": generation,
                       "sc:data_provenance": {"origin_kind": "controlled_generated_signal_received_by_hardware",
                           "transport_source": c["receiver"]["source"], "declared_source_synthetic": True,
                           "rf_origin_verified": False, "basis": "configured_generator_reference_and_host_capture_window"}})
            quality = self._capture(receiver, writer, target_count, c["rx_preroll_seconds"])
            if not quality["complete"]:
                error = quality.get("error", "Unvollständiges Aufnahmefenster.")
        except Exception as exc:
            error = str(exc)[:1000]
            quality.update(error=error, complete=False, reason=getattr(exc, "reason", "generation_error"))
            if writer is not None:
                quality["retained_samples"] = writer.samples
        finally:
            mute = self._request_mute("capture_finished" if error is None else "capture_failed")
            reader = self._reader_thread
            if reader is not None and reader.is_alive():
                self._update(status="stopping", receiver_lease_unresolved=True)
                # Do not close a device under a still running SDK read and do
                # not make it available to a second session prematurely.
                while reader.is_alive():
                    reader.join(timeout=.1)
            self._update(receiver_lease_unresolved=False)
            if mute is not None:
                mute.join(timeout=2)
                while mute.is_alive():
                    self._update(status="stopping", tx_shutdown_pending=True)
                    mute.join(timeout=.1)
            if receiver is not None:
                try:
                    receiver.close()
                except Exception as exc:
                    error = f"RX-Cleanup: {str(exc)[:500]}"
                    quality.update(error=error, complete=False)
            stopped = self._tx_update()
            generation["tx_shutdown"] = stopped
            if stopped.get("off_acknowledged") is not True:
                error = "TX-Abschaltung nicht bestätigt; Lauf abgebrochen, HF-Zustand unbekannt."
                quality.update(error=error, complete=False)
        usable = bool(writer is not None and quality.get("complete") and not error)
        if writer is not None:
            if quality.get("host_first_retained_estimate_utc"):
                writer.metadata["captures"][0]["core:datetime"] = quality["host_first_retained_estimate_utc"]
            path = writer.close({"sc:generation": generation, "sc:integrity": {**quality, "gap_free_verified": False},
                "signalclassifier:label": case["label"], "signalclassifier:label_verified": usable,
                "signalclassifier:label_provenance": "controlled_generator_reference_not_independent_rx_decode",
                "sc:label_facet": case["label_facet"], "sc:label_facets": case["facets"]})
            if usable:
                self.store.put("label", record_id, {"recording_id": record_id, "label": case["label"], "verified": True,
                    "provenance": "controlled_generator_reference_not_independent_rx_decode", "verification_kind": "generator_reference",
                    "human_verified": False, "protocol_rx_verified": False, "label_facet": case["label_facet"],
                    "facets": case["facets"], "created_at": utcnow()})
        return {"case_id": case["case_id"], "status": "completed" if usable else "cancelled" if self._cancel.is_set() else "failed",
                "recording_id": record_id if writer is not None else None,
                "iq_bytes_written": writer.samples*8 if writer is not None else 0,
                "label_usable": usable, "error": error, "quality": quality, "generation": generation, "finished_at": utcnow()}

    def _run(self, plan, identifier):
        folder = self.root / identifier
        failure = None
        cache_key = None
        cached = None
        try:
            self._adapter = self._tx_factory(plan["cases"][0]["tx_config"])
            self._adapter.open()
            self._tx_update()
            if self._adapter.status().get("off_acknowledged") is not True:
                raise RuntimeError("Pluto konnte vor Beginn nicht bestätigt stummgeschaltet werden.")
            for index, case in enumerate(plan["cases"]):
                if self._cancel.is_set():
                    break
                self._update(current_case=case, current_rx_samples=0, current_rx_target_samples=case["requested_rx_samples"])
                key = _hash({"spec": case["waveform_spec"], "seed": case["payload_seed"] if case["provider"] == "native" else None,
                             "effects": case["impairments"], "channel_seed": case["channel_seed"], "tx_rate": case["tx_config"]["sample_rate"]})
                try:
                    stage = "waveform"
                    waveform = cached if key == cache_key else self._make_waveform(case, folder)
                    cache_key, cached = key, waveform
                    stage = "capture"
                    record = self._record_case(case, plan, identifier, folder, waveform)
                except Exception as exc:
                    self._adapter.stop("generation_failed")
                    self._tx_update()
                    record = {"case_id": case["case_id"], "status": "failed", "error": str(exc)[:1000], "recording_id": None, "label_usable": False,
                              "hard_failure": stage == "capture" or isinstance(exc, OSError)}
                with (folder / "records.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+"\n")
                with self._lock:
                    self._state["completed_cases" if record["status"] == "completed" else "failed_cases"] += 1
                    self._state["progress"] = (index+1)/len(plan["cases"])
                    if record.get("recording_id"):
                        self._state["bytes_written"] += record.get("iq_bytes_written", 0)
                        self._state["recordings_saved_count"] += 1
                        self._state["recordings_saved"] = (self._state["recordings_saved"]+[record["recording_id"]])[-64:]
                        self._state["verified_recordings_count"] += int(record.get("label_usable", False))
                    if record.get("error"):
                        self._state["last_case_error"] = record["error"]
                    self._persist()
                if record["status"] != "completed" and (record.get("hard_failure") or record.get("quality", {}).get("reason") == "receiver_unresponsive"
                        or not plan["config"]["continue_on_error"] or self._adapter.status().get("off_acknowledged") is not True):
                    failure = record.get("error", "Generatorfall fehlgeschlagen.")
                    break
        except Exception as exc:
            failure = str(exc)[:1000]
        finally:
            adapter = self._adapter
            close_failed = False
            if adapter is not None:
                pending_mute = self._request_mute("campaign_finished")
                if pending_mute is not None:
                    pending_mute.join(timeout=2)
                    while pending_mute.is_alive():
                        self._update(status="stopping", tx_shutdown_pending=True)
                        pending_mute.join(timeout=.1)
                # From this point until SDK close completes no API stop may
                # create a new worker that outlives this adapter's campaign.
                with self._mute_lock:
                    self._adapter_retiring = True
                    late_mute = self._mute_thread
                if late_mute is not None:
                    while late_mute.is_alive():
                        late_mute.join(timeout=.1)
                try:
                    adapter.close()
                except Exception as exc:
                    close_failed = True
                    failure = f"TX-Cleanup fehlgeschlagen: {str(exc)[:500]}"
                    self._update(tx_active=None, cleanup_error=failure)
                finally:
                    self._tx_update()
                if close_failed or adapter.status().get("off_acknowledged") is not True:
                    failure = failure or "Abschließende TX-Stummschaltung nicht bestätigt; HF-Zustand unbekannt."
                    self._update(tx_active=None)
            with self._mute_lock:
                self._adapter = None
                self._adapter_retiring = False
            with self._lock:
                self._state.update(status="cancelled" if self._cancel.is_set() else "failed" if failure else "completed",
                    busy=False, error=failure, finished_at=utcnow(), current_case=None)
                self._persist()
