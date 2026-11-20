"""Remote-ID payload decoding, validated observations and an explicit local UDP bridge.

This module decodes *already demodulated* OpenDroneID payloads. It does not turn
arbitrary SDR IQ into Wi-Fi/Bluetooth packets and does not decrypt video links.
Wire layouts were checked against opendroneid-core-c a92bd5ffe113edd076c159a06799152aed3b1fb0.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import copy
import ipaddress
import json
import math
from pathlib import Path
import socket
import sqlite3
import struct
import threading
import time


MAX_DATAGRAM_BYTES = 16_384
STALE_SECONDS = 15.0
_DOMAINS = ("drone", "operator", "home")
_ALTITUDES = ("altitude_geodetic_m", "altitude_pressure_m", "height_agl_m", "height_above_takeoff_m")
_SOURCE = "https://github.com/opendroneid/opendroneid-core-c"


def capabilities() -> list[dict]:
    """Describe actual decoding support separately from radio prerequisites."""
    return [
        {"protocol": "OpenDroneID / Bluetooth LE", "status": "Payload-Decoder + JSON-Bridge",
         "hardware": "BLE-Scanner mit passendem PHY (BT4/BT5); Pluto-IQ allein genügt nicht",
         "position": "Drohne und gemeldeter Operator/Startpunkt; getrennte Höhenreferenzen",
         "video": "Nicht Bestandteil von Remote ID",
         "notes": "Basic ID, Location, System und Message Pack v2 werden entpackt. Kein eingebauter BLE-RF-Demodulator; RSP1A erreicht 2,4 GHz nicht.", "source": _SOURCE},
        {"protocol": "OpenDroneID / Wi-Fi Beacon + NAN", "status": "Payload-Decoder + JSON-Bridge",
         "hardware": "WLAN-Monitor-Adapter / externer Scanner; Kanal und PHY müssen passen",
         "position": "Drohne und gemeldeter Operator/Startpunkt, sofern gesendet",
         "video": "Nicht Bestandteil von Remote ID",
         "notes": "Demodulierte 25-Byte-Nachrichten oder Message Packs. Keine direkte Verarbeitung von 802.11-Frames oder PCAP. Ein einzelner Empfänger überwacht nicht gleichzeitig alle Kanäle.", "source": _SOURCE},
        {"protocol": "DJI DroneID (ältere proprietäre Varianten)", "status": "Kein RF-Decoder enthalten",
         "hardware": "Protokoll-/Firmware-abhängiger Empfänger mit passender RF-Bandbreite",
         "position": "Nur mit verifiziertem externem Decoder und tatsächlichen Feldern",
         "video": "Nicht durch Positionsdecodierung verfügbar",
         "notes": "Keine universelle Unterstützung aller DJI-Modelle. Standard-Remote-ID eines DJI-Geräts kann unabhängig davon über OpenDroneID eingehen.", "source": _SOURCE},
        {"protocol": "OcuSync / Lightbridge / andere digitale Videolinks", "status": "Kein Decoder enthalten",
         "hardware": "Herstellerempfänger oder protokollspezifische Implementierung",
         "position": "Nicht aus Energie im Spektrum ableitbar",
         "video": "Nur über separat bereitgestellten decodierten Stream; kein Entschlüsseler",
         "notes": "RF-Erkennung, Telemetrie und Videodecodierung sind getrennte Fähigkeiten. Stock-Pluto erreicht 5,8 GHz nicht; RSP1A weder 2,4 noch 5,8 GHz."},
        {"protocol": "Analoges FPV-Video (typisch 5,8 GHz)", "status": "Kein Decoder enthalten",
         "hardware": "Passender 5,8-GHz-Empfänger und Video-Capture; alternativ geeigneter SDR",
         "position": "Analogvideo garantiert keine Positionsmetadaten",
         "video": "Externer Empfänger erforderlich",
         "notes": "Die genannten SDRs decken 5,8 GHz im spezifizierten Standardbetrieb nicht ab. Keine automatische Firmware-Modifikation."},
        {"protocol": "OpenHD / WFB-ng", "status": "Externe Bodenstation erforderlich",
         "hardware": "Kompatibler WLAN-Adapter und passend konfigurierte Bodenstation",
         "position": "Über extern normalisierte Telemetrie-JSON möglich",
         "video": "Extern decodierter Stream; passendes eigenes Schlüsselmaterial bei verschlüsselten Links",
         "notes": "Kein eingebauter WFB-ng/OpenHD-Demodulator. Keine Umgehung von Verschlüsselung.",
         "source": "https://github.com/svpcom/wfb-ng"},
        {"protocol": "MAVLink / eigene Telemetrie", "status": "Normalisierte JSON-Bridge",
         "hardware": "Vorhandener Telemetrieempfänger / externer MAVLink-Adapter",
         "position": "Explizite Drohnen-, Operator- und Home-Felder",
         "video": "Kein Bestandteil dieser Bridge",
         "notes": "MAVLink-Bytes werden nicht unmittelbar geparst. Die Bridge übernimmt keine nicht gemeldete Operatorposition."},
        {"protocol": "ExpressLRS / Crossfire / sonstige Steuerlinks", "status": "Kein Decoder enthalten",
         "hardware": "Passende, protokollspezifische RX-/Telemetrieintegration",
         "position": "Nicht garantiert; Steuerlink allein liefert keine Positionszusage",
         "video": "Separater Videolink",
         "notes": "Keine universelle Decodierung proprietärer oder verschlüsselter Steuersignale."},
    ]


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value, *, now: float) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("observed_at muss ein ISO-8601-Zeitstempel mit Zeitzone sein.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        result = parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ValueError("observed_at muss ein gültiger ISO-8601-Zeitstempel mit Zeitzone sein.") from exc
    if not math.isfinite(result) or result < 0 or result > now + 30:
        raise ValueError("observed_at liegt vor 1970 oder mehr als 30 Sekunden in der Zukunft.")
    return result


def _text(value, name: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} muss ein nichtleerer Text mit höchstens {maximum} Zeichen sein.")
    return value.strip()


def _number(value, name: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} liegt nicht im gültigen Bereich [{low}, {high}].")
    return float(value)


def _coordinates(raw: bytes, offset: int) -> dict:
    lat, lon = struct.unpack_from("<ii", raw, offset)
    if lat == 0 and lon == 0:  # OpenDroneID's explicit unknown position sentinel.
        return {"lat": None, "lon": None}
    lat, lon = lat / 1e7, lon / 1e7
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("OpenDroneID-Koordinaten außerhalb des gültigen Bereichs.")
    return {"lat": lat, "lon": lon}


def _altitude(raw: bytes, offset: int) -> float | None:
    value = struct.unpack_from("<H", raw, offset)[0]
    return value * .5 - 1000.0 if value else None


def decode_payload(raw: bytes) -> dict:
    """Decode a v2 OpenDroneID payload (25-byte message or message pack).

    Transport headers (BLE UUID, Wi-Fi vendor element, sequence counter etc.)
    must have been removed by the radio adapter. Authentication messages are
    explicitly not verified. Unknown datums/sentinel values remain absent.
    """
    if not isinstance(raw, bytes) or not raw or len(raw) > 228:
        raise ValueError("OpenDroneID-Payload muss zwischen 1 und 228 Bytes umfassen.")
    version, message_type = raw[0] & 15, raw[0] >> 4
    if version != 2:
        raise ValueError("Nur OpenDroneID-Protokollversion 2 wird unterstützt.")
    if message_type == 15:
        if len(raw) < 3 or raw[1] != 25 or not 1 <= raw[2] <= 9 or len(raw) != 3 + raw[2] * 25:
            raise ValueError("Ungültige OpenDroneID-Message-Pack-Länge.")
        merged: dict = {"message_types": [], "authentication": "not_verified"}
        for offset in range(3, len(raw), 25):
            part = decode_payload(raw[offset:offset + 25])
            for key, value in part.items():
                if key in ("message_types", "unsupported_message_types"):
                    merged.setdefault(key, []).extend(value)
                elif key in _DOMAINS:
                    merged.setdefault(key, {}).update(value)
                else:
                    merged[key] = value
        return merged
    if len(raw) != 25:
        raise ValueError("Eine OpenDroneID-Einzelnachricht muss genau 25 Bytes umfassen.")
    result: dict = {"message_types": [message_type], "authentication": "not_verified"}
    if message_type == 0:
        id_type, ua_type = raw[1] >> 4, raw[1] & 15
        if id_type in (1, 2):
            id_bytes = raw[2:22].split(b"\0", 1)[0]
            try:
                basic_id = id_bytes.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("Ungültige ASCII Basic ID.") from exc
            if basic_id:
                result["basic_id"] = _text(basic_id, "basic_id", 20)
        else:
            # A UTM/session ID can be binary. Do not interpret it as UTF-8.
            result["basic_id_hex"] = raw[2:22].hex()
        result.update(id_type=id_type, ua_type=ua_type)
    elif message_type == 1:
        drone = _coordinates(raw, 5)
        drone["altitude_pressure_m"] = _altitude(raw, 13)
        drone["altitude_geodetic_m"] = _altitude(raw, 15)
        height_key = "height_agl_m" if raw[1] & 4 else "height_above_takeoff_m"
        drone[height_key] = _altitude(raw, 17)
        direction = raw[2] + (180 if raw[1] & 2 else 0)
        if 0 <= direction < 360:
            drone["heading_deg"] = float(direction)
        speed = raw[3] * (.75 if raw[1] & 1 else .25) + (63.75 if raw[1] & 1 else 0)
        if speed != 255:
            drone["speed_mps"] = speed
        vertical = struct.unpack_from("b", raw, 4)[0] * .5
        if -62 <= vertical <= 62:
            drone["vertical_speed_mps"] = vertical
        stamp = struct.unpack_from("<H", raw, 21)[0]
        result["transmitted_seconds_after_hour"] = stamp / 10 if stamp != 65535 else None
        result["status_code"] = raw[1] >> 4
        result["drone"] = drone
    elif message_type == 4:
        kind = raw[1] & 3
        if kind == 3:
            raise ValueError("Reservierter OpenDroneID-Operator-Location-Typ.")
        domain = "home" if kind == 0 else "operator"
        result[domain] = _coordinates(raw, 2)
        result[domain]["altitude_geodetic_m"] = _altitude(raw, 18)
        result[domain]["location_type"] = ("takeoff", "live_gnss", "fixed")[kind]
    else:
        result["unsupported_message_types"] = [message_type]
    return result


def _normalise(payload: dict, *, now: float) -> tuple[dict, float]:
    if not isinstance(payload, dict):
        raise ValueError("Drohnenmeldung muss ein JSON-Objekt sein.")
    try:
        size = len(json.dumps(payload, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Drohnenmeldung enthält ungültige JSON-Werte.") from exc
    if size > MAX_DATAGRAM_BYTES:
        raise ValueError("Drohnenmeldung ist größer als 16 KiB.")
    observed = _timestamp(payload.get("observed_at"), now=now)
    result = {"id": _text(payload.get("id"), "id"),
              "source": _text(payload.get("source"), "source", 256),
              "protocol": _text(payload.get("protocol", "OpenDroneID"), "protocol", 80),
              "observed_at": _iso(observed), "authentication": "not_verified"}
    decoded: dict = {}
    if "payload_hex" in payload or "messages_hex" in payload:
        if "payload_hex" in payload and "messages_hex" in payload:
            raise ValueError("Entweder payload_hex oder messages_hex angeben.")
        raw_list = [payload["payload_hex"]] if "payload_hex" in payload else payload["messages_hex"]
        if not isinstance(raw_list, list) or not 1 <= len(raw_list) <= 9:
            raise ValueError("messages_hex benötigt 1 bis 9 Nachrichten.")
        for encoded in raw_list:
            if not isinstance(encoded, str) or len(encoded) > 456:
                raise ValueError("Ungültige hexadezimale OpenDroneID-Payload.")
            try:
                part = decode_payload(bytes.fromhex(encoded))
            except ValueError as exc:
                raise ValueError(f"OpenDroneID-Payload ungültig: {exc}") from exc
            for key, value in part.items():
                if key in _DOMAINS:
                    decoded.setdefault(key, {}).update(value)
                elif key in ("message_types", "unsupported_message_types"):
                    decoded.setdefault(key, []).extend(value)
                else:
                    decoded[key] = value
        if any(domain in payload for domain in _DOMAINS):
            raise ValueError("Raw-Payload und normalisierte Positionsfelder nicht gleichzeitig senden.")
    for domain in _DOMAINS:
        raw = decoded.get(domain, payload.get(domain))
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"{domain} muss ein JSON-Objekt sein.")
        clean: dict = {}
        if ("lat" in raw) != ("lon" in raw):
            raise ValueError(f"{domain}: lat und lon müssen gemeinsam angegeben werden.")
        if "lat" in raw:
            clean["lat"] = _number(raw["lat"], f"{domain}.lat", -90, 90)
            clean["lon"] = _number(raw["lon"], f"{domain}.lon", -180, 180)
            if (clean["lat"] is None) != (clean["lon"] is None):
                raise ValueError(f"{domain}: lat/lon müssen beide gültig oder beide null sein.")
        for key in _ALTITUDES:
            if key in raw:
                clean[key] = _number(raw[key], f"{domain}.{key}", -1000, 100_000)
        for key, bounds in {"speed_mps": (0, 2000), "vertical_speed_mps": (-2000, 2000), "heading_deg": (0, 360)}.items():
            if key in raw:
                clean[key] = _number(raw[key], f"{domain}.{key}", *bounds)
        if "location_type" in raw:
            location_type = _text(raw["location_type"], "location_type", 32)
            if location_type not in ("takeoff", "live_gnss", "fixed", "unknown"):
                raise ValueError("Unbekannter location_type.")
            clean["location_type"] = location_type
        if clean:
            result[domain] = clean
    for key in ("basic_id", "basic_id_hex", "transport", "notes"):
        value = decoded.get(key, payload.get(key))
        if value is not None:
            result[key] = _text(value, key, 512 if key == "notes" else 128)
    for key in ("message_types", "unsupported_message_types", "id_type", "ua_type", "status_code", "transmitted_seconds_after_hour"):
        if key in decoded:
            result[key] = decoded[key]
    return result, observed


class DroneStore:
    """Persistent latest observations, keyed by (source, protocol, receiver ID).

    Per-field observation times prevent a Basic-ID packet from rejuvenating an
    old position. Older packets can fill missing fields but never replace newer
    observations. This is a latest-state store, not a flight-history recorder.
    """

    def __init__(self, db_path, *, stale_seconds: float = STALE_SECONDS, max_tracks: int = 2000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.stale_seconds = float(stale_seconds)
        self.max_tracks = int(max_tracks)
        if not 1 <= self.max_tracks <= 100_000 or not 1 <= self.stale_seconds <= 3600:
            raise ValueError("Ungültige Track-Grenzen.")
        with self._connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS drone_observations (source TEXT NOT NULL, protocol TEXT NOT NULL, track_id TEXT NOT NULL, state_json TEXT NOT NULL, observed REAL NOT NULL, PRIMARY KEY(source, protocol, track_id))")

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            with conn:
                yield conn
        finally:
            conn.close()

    def ingest(self, payload: dict) -> dict:
        now = time.time()
        clean, observed = _normalise(payload, now=now)
        key = (clean["source"], clean["protocol"], clean["id"])
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state_json FROM drone_observations WHERE source=? AND protocol=? AND track_id=?", key).fetchone()
            state = json.loads(row[0]) if row else {"_times": {}, "_latest": 0.0}
            times = state["_times"]
            for name, value in clean.items():
                if name == "observed_at":
                    continue
                if name in _DOMAINS:
                    for field, field_value in value.items():
                        field_key = f"{name}.{field}"
                        if observed >= times.get(field_key, -1):
                            state.setdefault(name, {})[field] = field_value
                            times[field_key] = observed
                elif observed >= times.get(name, -1):
                    state[name] = value
                    times[name] = observed
            state["out_of_order"] = observed < state["_latest"]
            state["_latest"] = max(state["_latest"], observed)
            state["observed_at"] = _iso(state["_latest"])
            state["received_at"] = _iso(now)
            conn.execute("INSERT INTO drone_observations(source, protocol, track_id, state_json, observed) VALUES(?,?,?,?,?) ON CONFLICT(source,protocol,track_id) DO UPDATE SET state_json=excluded.state_json, observed=excluded.observed", (*key, json.dumps(state, allow_nan=False), state["_latest"]))
            conn.execute("DELETE FROM drone_observations WHERE rowid IN (SELECT rowid FROM drone_observations ORDER BY observed DESC LIMIT -1 OFFSET ?)", (self.max_tracks,))
        return self._public(state, now)

    def _public(self, state: dict, now: float) -> dict:
        result = {key: copy.deepcopy(value) for key, value in state.items() if not key.startswith("_")}
        result["age_seconds"] = max(0., now - state["_latest"])
        result["stale"] = result["age_seconds"] > self.stale_seconds
        result["position_age_seconds"] = None
        result["position_stale"] = True
        for domain in _DOMAINS:
            if domain not in result:
                continue
            position = result[domain]
            position["field_age_seconds"] = {key: max(0., now - state["_times"][f"{domain}.{key}"]) for key in position}
            valid = position.get("lat") is not None and position.get("lon") is not None
            field_time = min(state["_times"].get(f"{domain}.lat", 0), state["_times"].get(f"{domain}.lon", 0))
            age = max(0., now - field_time) if valid else None
            position["position_age_seconds"] = age
            position["stale"] = age is None or age > self.stale_seconds
            if domain == "drone":
                result["position_age_seconds"] = age
                result["position_stale"] = position["stale"]
        return result

    def tracks(self) -> list[dict]:
        now = time.time()
        with self._connection() as conn:
            rows = conn.execute("SELECT state_json FROM drone_observations ORDER BY observed DESC LIMIT ?", (self.max_tracks,)).fetchall()
        return [self._public(json.loads(row[0]), now) for row in rows]


class Bridge:
    """Explicitly started UDP JSON input bound to a loopback address only."""

    def __init__(self, store: DroneStore):
        self.store = store
        self._lock = threading.RLock()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = {"running": False, "host": "127.0.0.1", "port": 5700, "accepted": 0, "rejected": 0, "last_error": None, "last_received_at": None}

    def start(self, config: dict | None = None) -> dict:
        config = config or {}
        host = config.get("host", "127.0.0.1")
        try:
            address = ipaddress.ip_address(host)
        except (ValueError, TypeError) as exc:
            raise ValueError("Die UDP-Bridge benötigt eine numerische Loopback-Adresse.") from exc
        if not address.is_loopback:
            raise ValueError("Die UDP-Bridge bindet ausschließlich an Loopback; externe Scanner per SSH-Tunnel anbinden.")
        port = config.get("port", 5700)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("UDP-Port muss zwischen 0 und 65535 liegen.")
        with self._lock:
            if self._state["running"]:
                raise ValueError("UDP-Bridge läuft bereits.")
            sock = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((str(address), port))
                sock.settimeout(.2)
            except OSError:
                sock.close()
                raise
            self._socket = sock
            self._stop.clear()
            self._state.update(running=True, host=str(address), port=sock.getsockname()[1], last_error=None)
            self._thread = threading.Thread(target=self._run, name="drone-json-bridge", daemon=True)
            self._thread.start()
        return self.status()

    def _run(self):
        while not self._stop.is_set():
            try:
                packet, _ = self._socket.recvfrom(MAX_DATAGRAM_BYTES + 1)
                if len(packet) > MAX_DATAGRAM_BYTES:
                    raise ValueError("UDP-Paket größer als 16 KiB.")
                payload = json.loads(packet.decode("utf-8"))
                self.store.ingest(payload)
                with self._lock:
                    self._state["accepted"] += 1
                    self._state["last_received_at"] = _iso(time.time())
            except socket.timeout:
                continue
            except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error) as exc:
                if self._stop.is_set():
                    break
                with self._lock:
                    self._state["rejected"] += 1
                    self._state["last_error"] = str(exc)[:512]
                # Prevent a persistent I/O error from becoming a tight loop.
                if isinstance(exc, OSError):
                    self._stop.wait(.05)
        with self._lock:
            self._state["running"] = False

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            sock, thread = self._socket, self._thread
        if sock is not None:
            sock.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        with self._lock:
            self._state["running"] = False
            self._socket = None
            self._thread = None
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)


def example_payload() -> dict:
    """Return a clearly labelled schema example, never ingest it implicitly."""
    return {"id": "BEISPIEL-KEIN-EMPFANG", "protocol": "OpenDroneID", "source": "schema-example",
            "observed_at": _iso(time.time()), "transport": "wifi-beacon",
            "drone": {"lat": 49.45, "lon": 11.08, "altitude_geodetic_m": 400.0,
                      "height_above_takeoff_m": 50.0},
            "home": {"lat": 49.449, "lon": 11.079, "location_type": "takeoff"},
            "notes": "Nur Schema-Beispiel, keine Messung. Operatorposition wird nicht abgeleitet."}
