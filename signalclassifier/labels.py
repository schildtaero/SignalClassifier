"""Auditable band hints. These are never ground-truth transmitter identities."""
from __future__ import annotations

from .db import Store, utcnow

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
