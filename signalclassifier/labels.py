"""Auditable band hints. These are never ground-truth transmitter identities."""
from __future__ import annotations

import math
import re
from numbers import Real

from .db import Store

SEED_VERSION = "DE-regional-hints-2026-11-v1"
BANDPLAN_SOURCE = "https://www.bundesnetzagentur.de/DE/Fachthemen/Telekommunikation/Frequenzen/Grundlagen/Frequenzplan/frequenzplan-node.html"


def _entry(key, name, service, standard, modulation, low, high, notes, confidence=0.6, region="DE"):
    return dict(id=key, name=name, service=service, standard=standard, modulation=modulation,
                frequency_min_hz=low, frequency_max_hz=high, region=region, confidence=confidence,
                source=BANDPLAN_SOURCE, notes=notes, verified=False, seed_version=SEED_VERSION)


SEEDS = [
    _entry("fm-broadcast", "UKW-Rundfunk", "Rundfunk", "FM / RDS", "WFM", 87.5e6, 108e6, "Typisch 150–200 kHz. Bandhinweis; keine lokale Senderliste."),
    _entry("airband", "Flugfunk Sprache", "Flugfunk", "VHF Airband", "AM", 117.975e6, 137e6, "VDL-Datenkanäle liegen ebenfalls im Band. Nicht jeder Kanal ist AM."),
    _entry("adsb", "ADS-B / Mode S", "Flugsicherung", "1090ES / Mode S", "PPM", 1089e6, 1091e6, "1090 MHz. Mode S ist nicht automatisch ADS-B mit Position."),
    _entry("dab", "DAB+ Band III", "Rundfunk", "ETSI EN 300 401", "OFDM", 174e6, 230e6, "Ungefähr 1,536 MHz Signalbandbreite. Regionale Belegung zusätzlich prüfen."),
    _entry("dvbt", "DVB-T/T2 UHF", "Rundfunk", "DVB-T2", "OFDM", 470e6, 694e6, "In Deutschland vor allem DVB-T2. Ein Bandhinweis bestätigt keinen Empfang."),
    _entry("pocsag", "Funkruf / POCSAG", "Funkruf", "POCSAG", "2FSK", 165e6, 174e6, "Nur ein typischer Suchbereich. Weitere Zuteilungen und Amateurfunk möglich.", 0.3),
    _entry("ais", "AIS", "Seefunk", "ITU-R M.1371", "GMSK", 161.95e6, 162.05e6, "AIS 1: 161,975 MHz; AIS 2: 162,025 MHz. In Bamberg nicht als lokaler Sender voraussetzen."),
    _entry("acars", "ACARS VHF", "Flugfunk Daten", "ACARS", "AM-MSK", 131.4e6, 136.975e6, "Kandidatenbereich; überlappt Sprachflugfunk. Nachrichtendecoder erforderlich.", 0.3),
    _entry("vdl2", "VDL Mode 2", "Flugfunk Daten", "VDL2", "D8PSK", 136.7e6, 136.975e6, "Typischer Kanal 136,975 MHz. Kanalzuordnung gesondert prüfen."),
    _entry("pmr446", "PMR446", "Sprechfunk", "Analog / dPMR / DMR", "NFM / 4FSK", 446e6, 446.2e6, "Mehrere Standards im selben Band; Frequenz ist kein Modulationslabel."),
    _entry("dmr", "DMR Kandidaten", "Betriebs-/Amateurfunk", "ETSI TS 102 361", "4FSK", 430e6, 440e6, "Amateurfunkbereich, viele andere Modulationen; nur schwacher Suchhinweis.", 0.15),
    _entry("tetra", "TETRA Kandidaten", "Bündelfunk", "ETSI EN 300 392", "pi/4-DQPSK", 380e6, 400e6, "Erkennung ist keine Nutzlastentschlüsselung. Viele Netze verschlüsseln Inhalte.", 0.4),
    _entry("lora433", "LoRa / BFT / SRD 433", "SRD / Amateurfunk", "LoRa", "CSS", 433.05e6, 434.79e6, "Geteiltes Band mit FSK/OOK u.a.; BFT-Einstellungen als Versuchsmetadaten dokumentieren.", 0.2),
    _entry("lora868", "LoRa / LoRaWAN / SRD 868", "SRD", "LoRa / LoRaWAN", "CSS", 863e6, 870e6, "Band enthält viele andere Funkverfahren. Nicht pauschal vorlabeln.", 0.2),
    _entry("aprs", "APRS VHF", "Amateurfunk", "AX.25 APRS", "AFSK-FM", 144.79e6, 144.81e6, "In Europa typischer Kanal 144,800 MHz."),
    _entry("amateur2m", "Amateurfunk 2 m", "Amateurfunk", "Verschiedene", "AM / FM / SSB / Digital", 144e6, 146e6, "Breites Suchgebiet, Modulation unbekannt.", 0.1),
    _entry("amateur70cm", "Amateurfunk 70 cm", "Amateurfunk", "Verschiedene", "FM / SSB / Digital", 430e6, 440e6, "Breites Suchgebiet, Modulation unbekannt.", 0.1),
    _entry("srd-ook", "SRD / Sensorik", "Telemetrie", "Verschiedene", "OOK / FSK", 433.05e6, 434.79e6, "Wetterstationen, Fernbedienungen und Sensoren; Identität ohne Decoder unbekannt.", 0.2),
    _entry("remote-id", "Remote ID / WLAN / Bluetooth", "Drohnenidentifikation / ISM", "OpenDroneID / Wi-Fi / BLE", "OFDM / GFSK / DSSS", 2400e6, 2483.5e6, "Gemeinsames ISM-Band: Energie allein beweist keine Drohne. Wi-Fi/BLE-Schnittstelle empfohlen.", 0.1),
    _entry("video58", "Video-/Datenlink 5,8 GHz", "ISM / FPV", "Analog / proprietär / WLAN", "Verschiedene", 5725e6, 5875e6, "Außerhalb des spezifizierten Pluto- und RSP1A-Bereichs. Keine universelle Decodierung.", 0.1),
]


def seed(store: Store):
    for entry in SEEDS:
        if store.get("emitter", entry["id"]) is None:
            store.put("emitter", entry["id"], entry)


def suggest(store: Store, frequency: float) -> list[dict]:
    matches = []
    for entry in store.all("emitter"):
        if entry["frequency_min_hz"] <= frequency <= entry["frequency_max_hz"]:
            matches.append({**entry, "label_status": "suggested", "requires_confirmation": True,
                            "reason": "Frequenz im Suchbereich; Modulation und Emitter sind unbestätigt."})
    return sorted(matches, key=lambda item: item.get("confidence", 0), reverse=True)


# Explicit mappings retain existing seed records and their editable source
# metadata. Broad/mixed entries deliberately provide alternatives rather than
# suggesting that a frequency proves one modulation or protocol.
_SEED_LABELS = {
    "fm-broadcast": ("FM",), "airband": ("AM",),
    "adsb": ("ADS-B", "Mode S"), "dab": ("DAB+",), "dvbt": ("DVB-T2",),
    "pocsag": ("POCSAG",), "ais": ("AIS",), "acars": ("ACARS",), "vdl2": ("VDL2",),
    "pmr446": ("FM", "dPMR", "DMR"), "dmr": ("DMR",), "tetra": ("TETRA",),
    "lora433": ("LoRa",), "lora868": ("LoRa",), "aprs": ("APRS",),
    "amateur2m": ("AM", "FM", "SSB", "Digital"),
    "amateur70cm": ("FM", "SSB", "Digital"), "srd-ook": ("OOK", "FSK"),
    "remote-id": ("Remote ID", "Wi-Fi", "Bluetooth"),
    "video58": ("Analoges FPV", "Digitaler Videolink"),
}
_KNOWN_LABELS = {
    "am": "AM", "fm": "FM", "wfm": "FM", "nfm": "FM", "ssb": "SSB", "usb": "SSB", "lsb": "SSB",
    "pocsag": "POCSAG", "lora": "LoRa", "lorawan": "LoRa", "lora / lorawan": "LoRa",
    "ais": "AIS", "acars": "ACARS", "vdl2": "VDL2", "vdl mode 2": "VDL2", "aprs": "APRS",
    "dmr": "DMR", "dpmr": "dPMR", "tetra": "TETRA", "dab": "DAB+", "dab+": "DAB+", "dvb-t2": "DVB-T2",
    "fsk": "FSK", "2fsk": "2FSK", "4fsk": "4FSK", "gmsk": "GMSK", "ook": "OOK", "css": "CSS", "ofdm": "OFDM",
    "bpsk": "BPSK", "qpsk": "QPSK", "8psk": "8PSK", "qam": "QAM",
}


def _number(value):
    if not isinstance(value, Real) or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _entry_labels(entry):
    # Recognise specific documented standards first for user-created entries;
    # mixed or unknown names do not become arbitrary training class names.
    if entry.get("id") in _SEED_LABELS:
        return _SEED_LABELS[entry["id"]], False
    standard = str(entry.get("standard", "")).strip().casefold()
    modulation = str(entry.get("modulation", "")).strip().casefold()
    if standard in _KNOWN_LABELS:
        return (_KNOWN_LABELS[standard],), False
    if modulation in _KNOWN_LABELS:
        return (_KNOWN_LABELS[modulation],), False
    parts = re.split(r"\s*[/,|]\s*", modulation)
    return tuple(dict.fromkeys(_KNOWN_LABELS[part] for part in parts if part in _KNOWN_LABELS)), any(part not in _KNOWN_LABELS for part in parts)


def _region_code(value):
    match = re.match(r"^([A-Z]{2}(?:-[A-Z0-9]+)?)(?:\s|$|·)", str(value or "").strip().upper())
    return match[1] if match else None


def _in_region(entry, region):
    code = _region_code(entry.get("region"))
    if not code or not region:
        return True
    return code == region or code.startswith(region + "-") or region.startswith(code + "-")


def _windows(recording):
    if not isinstance(recording, dict) or recording.get("error"):
        raise ValueError("Aufnahme-Metadaten fehlen oder sind ungültig.")
    meta = recording.get("metadata", recording if "global" in recording else {})
    if not isinstance(meta, dict) or ("global" in meta and not isinstance(meta["global"], dict)):
        raise ValueError("Aufnahme-Metadaten sind ungültig.")
    glob = meta.get("global", {})
    rate = _number(glob.get("core:sample_rate", recording.get("sample_rate")))
    if rate is None or not 0 < rate <= 1e9:
        raise ValueError("Die aufgezeichnete Bandbreite/Abtastrate fehlt oder ist ungültig.")
    centers = []
    if "captures" in meta:
        captures = meta["captures"]
        if not isinstance(captures, list) or not 1 <= len(captures) <= 10000:
            raise ValueError("Capture-Metadaten fehlen oder sind ungültig.")
        previous_start = -1
        for capture in captures:
            if not isinstance(capture, dict):
                raise ValueError("Ein Capture besitzt ungültige Metadaten.")
            if "core:sample_rate" in capture:
                raise ValueError("Wechselnde Capture-Abtastraten benötigen eine getrennte Vorlabel-Prüfung.")
            if "core:sample_start" in capture:
                start = capture["core:sample_start"]
                if isinstance(start, bool) or not isinstance(start, int) or start < 0 or start <= previous_start:
                    raise ValueError("Capture-Sampleindizes sind ungültig.")
                previous_start = start
            frequency = _number(capture.get("core:frequency"))
            if frequency is None:
                raise ValueError("Eine absolute Capture-Frequenz fehlt oder ist ungültig.")
            centers.append(frequency)
    else:
        centers.append(_number(recording.get("center_frequency_hz")))
    if any(center is None or not 0 < center <= 100e9 for center in centers):
        raise ValueError("Die absolute Empfangsfrequenz fehlt oder ist ungültig.")
    centers = list(dict.fromkeys(centers))
    windows = [(center - rate / 2, center + rate / 2) for center in centers]
    region = _region_code(recording.get("region") or glob.get("signalclassifier:region") or glob.get("sc:region") or "DE")
    return windows, len(centers) > 1, region


def _covered(window, intervals):
    low, high = window
    end = low
    for left, right in sorted(intervals):
        if right <= end:
            continue
        if left > end:
            return False
        end = right
        if end >= high:
            return True
    return False


def recording_suggestions(store: Store, recording: dict, entries=None) -> dict:
    """Pure provisional labels for the entire recorded RF window, never writes.

    ``entries`` allows an API caller to reuse one emitter snapshot for an entire
    recording list. Frequency hints are independent of classification priors and
    human annotations. They are not evidence that one clean signal was received.
    """
    empty = {"label_suggestion": None, "label_suggestions": []}
    try:
        windows, retuned, region = _windows(recording)
    except ValueError as exc:
        return {**empty, "label_suggestion_reason": str(exc)}
    matches = {}
    intervals = [[] for _ in windows]
    unresolved = False
    for entry in store.all("emitter") if entries is None else entries:
        if not isinstance(entry, dict) or not _in_region(entry, region):
            continue
        low, high = _number(entry.get("frequency_min_hz")), _number(entry.get("frequency_max_hz"))
        if low is None or high is None or not 0 <= low <= high:
            continue
        overlap = [(i, (max(low, a), min(high, b))) for i, (a, b) in enumerate(windows) if low < b and high > a]
        if not overlap:
            continue
        labels, incomplete = _entry_labels(entry)
        unresolved |= not labels or incomplete
        for index, interval in overlap:
            intervals[index].append(interval)
        confidence = _number(entry.get("confidence"))
        confidence = min(1., max(0., confidence)) if confidence is not None else 0.
        for label in labels:
            item = matches.setdefault(label, {"label": label, "confidence": 0., "emitter_ids": [], "names": [], "sources": []})
            item["confidence"] = max(item["confidence"], confidence)
            for key, value in (("emitter_ids", entry.get("id")), ("names", entry.get("name")), ("sources", entry.get("source"))):
                if value and str(value) not in item[key]:
                    item[key].append(str(value))
    coverage = all(_covered(window, intervals[i]) for i, window in enumerate(windows))
    ambiguous = len(matches) != 1 or retuned or not coverage or unresolved
    reasons = []
    if retuned:
        reasons.append("Die Aufnahme enthält mehrere Abstimmfrequenzen; globales Vorlabel gesperrt.")
    if not coverage:
        reasons.append("Das gesamte Aufnahmefenster liegt nicht innerhalb bekannter Suchbereiche.")
    if unresolved:
        reasons.append("Ein überlappender Datenbankeintrag hat keine eindeutige unterstützte Klassenabbildung.")
    if len(matches) > 1:
        reasons.append("Mehrere Klassen sind im vollständigen Aufnahmefenster möglich.")
    if not matches:
        reasons.append("Kein zugeordneter Klassenhinweis für diesen Frequenzbereich.")
    suggestions = []
    for item in matches.values():
        names = ", ".join(item.pop("names"))
        sources = item.pop("sources")
        item.update(source="; ".join(sources), ambiguous=ambiguous, requires_confirmation=True, label_status="suggested",
                    reason=f"Frequenz-/Bandhinweis aus {names or 'Emitter-Datenbank'}; die volle IQ-Bandbreite wurde berücksichtigt. "
                           "Kein Signal- oder Identitätsnachweis; mehrere Sender und andere Modulationen bleiben möglich."
                           + (" " + " ".join(reasons) if ambiguous else ""))
        suggestions.append(item)
    suggestions.sort(key=lambda item: (-item["confidence"], item["label"]))
    return {"label_suggestion": suggestions[0] if suggestions and not ambiguous else None,
            "label_suggestions": suggestions,
            "label_suggestion_reason": " ".join(reasons) if reasons else "Unbestätigter Frequenzhinweis; vor dem Training prüfen und bestätigen."}
