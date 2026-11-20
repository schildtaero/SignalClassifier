# Drohnen: tatsächlicher Funktionsumfang und Empfangsvoraussetzungen

Der Drohnen-Tab zeigt **tatsächlich zugeführte Beobachtungen** und getrennte Marker für Drohne, Operator und Startpunkt. Es werden keine Demo-Tracks automatisch angelegt. Ohne Quelle bleibt die Karte leer. Ein einzelner universeller Decoder für „alle üblichen Drohnensignale“ ist mit der genannten Hardware und ohne protokollspezifische Empfänger nicht realisierbar.

## In diesem Build implementiert

- Validierte, persistente Remote-ID-/Telemetriebeobachtungen über `POST /api/drones/ingest`.
- Explizit startbare UDP-JSON-Bridge auf Loopback, standardmäßig `127.0.0.1:5700`. Externe Scanner können per SSH-Tunnel oder über einen lokalen Adapter eingebunden werden. Kein automatischer Listenerstart.
- Entpacken bereits demodulierter **OpenDroneID-v2-Payloads**: Basic ID, Location, System und Message Pack mit bis zu neun 25-Byte-Nachrichten. UTM-/Session-IDs bleiben bei binären Formaten Hexdaten. Andere Nachrichtentypen werden als nicht ausgewertet ausgewiesen; insbesondere werden Authentifizierungsdaten nicht verifiziert.
- Speicherung des letzten Zustands je `(source, protocol, id)` in SQLite, höchstens 2000 Tracks. Die Quelle ist eine angegebene Herkunft, keine kryptographische Vertrauensbestätigung. Derselbe Identifier von verschiedenen Scannern wird nicht stillschweigend zu einem Track verschmolzen.
- Zeit pro Datenfeld: Ein neues Basic-ID-Paket macht eine alte Position nicht frisch. Nach 15 Sekunden ist die Position als veraltet markiert. Verspätete Pakete überschreiben neuere Felder nicht. Ein explizites `null`-Koordinatenpaar setzt den aktuellen Positionswert auf unbekannt.
- Getrennte Größen: WGS84-Ellipsoidhöhe `altitude_geodetic_m`, Druckhöhe `altitude_pressure_m`, Höhe über Boden `height_agl_m` und Höhe über Startpunkt `height_above_takeoff_m`. Es wird keine unzulässige Umrechnung ohne Gelände-/Geoid-/Druckdaten vorgenommen.

**Kein direkter RF-/IQ-Decoder:** Bluetooth-PHY, Wi-Fi-Demodulation, Kanalhopping, BLE-/802.11-Frameextraktion und PCAP-Parsing sind in diesem Build nicht enthalten. Ein externer Scanner muss JSON oder transportbereinigte Payloads liefern. Eine Bridge ist keine Zusage, dass das unveränderte Ausgabeformat eines beliebigen Scanners direkt passt.

## Geeignete zusätzliche Empfangswege

OpenDroneID verwendet Bluetooth- und Wi-Fi-Broadcasts. Ein BLE-Empfänger muss den verwendeten PHY unterstützen; ein WLAN-Empfänger braucht für Beacon-/NAN-Erfassung eine geeignete Monitor-/Scannerintegration. Die OpenDroneID-Projekte bieten Referenzempfänger und Hinweise zu Scanner-Hardware. Ein einzelner Empfänger überwacht nicht gleichzeitig alle Kanäle und Bänder.

Der **RSP1A** deckt laut Hersteller 1 kHz bis 2 GHz mit bis zu 10 MHz sichtbarer Bandbreite ab: Er erreicht weder 2,4 noch 5,8 GHz. Der **PlutoSDR** ist im Standardbetrieb für 325 MHz bis 3,8 GHz und 20 MHz Momentanbandbreite spezifiziert: 2,4 GHz liegt darin, 5,8 GHz nicht. Frequenzabdeckung allein stellt noch keinen Protokolldecoder dar. Erweiterte Pluto-Firmwarebereiche werden nicht automatisch aktiviert und sind keine Garantie für die spezifizierte RF-Leistung oder dauerhaften USB-Durchsatz.

Die Protokolltabelle in der Oberfläche unterscheidet außerdem DJI DroneID, digitale Hersteller-Videolinks (z. B. OcuSync/Lightbridge), analoges FPV, OpenHD/WFB-ng, MAVLink und Steuerlinks. Dafür ist **kein universeller RF-Decoder eingebaut**. Bei DJI unterscheiden sich Verfahren nach Modell/Firmware; die Positionsdecodierung eines Verfahrens decodiert nicht automatisch dessen Videolink. Analoges FPV liefert nicht grundsätzlich Positionsdaten. Verschlüsselte Video-/Telemetrielinks benötigen eine passende eigene Empfangskette und gegebenenfalls Schlüssel; ein Entschlüsseler ist nicht enthalten.

## JSON-Schema und Adapterbeispiel

Eine Meldung benötigt `id`, `source` und `observed_at` als ISO-8601-Zeit mit Zeitzone. `protocol` hat standardmäßig den Wert `OpenDroneID`. Koordinaten immer als Paar angeben; unbekannte Werte weglassen oder beide auf `null` setzen. Ein Startpunkt ist keine aktuelle Operatorposition.

```json
{
  "id": "scanner-interne-stabile-id",
  "source": "mein-wlan-scanner",
  "protocol": "OpenDroneID",
  "observed_at": "2026-11-20T12:00:00Z",
  "transport": "wifi-beacon",
  "drone": {
    "lat": 49.45,
    "lon": 11.08,
    "altitude_geodetic_m": 400,
    "height_above_takeoff_m": 50
  },
  "home": {"lat": 49.449, "lon": 11.079, "location_type": "takeoff"}
}
```

Die oben stehenden Werte sind ein Schema-Beispiel, keine Messung. `GET /api/drones/example` erzeugt ein Beispiel mit aktueller Zeit, **ohne** es einzuspeisen. Bei Aufzeichnungsimporten bleibt die ursprüngliche Beobachtungszeit erhalten; alte Aufnahmen erscheinen daher bewusst veraltet.

Für Rohpayloads `payload_hex` (Einzelnachricht oder Message Pack) oder `messages_hex` (Liste einzelner Hexnachrichten) statt der normalisierten `drone`-/`operator`-/`home`-Objekte senden. `id` dient auch dann der stabilen Zuordnung des Empfängers, während eine empfangene Seriennummer getrennt als `basic_id` gespeichert wird. Transportheader, OUI/UUID und Sequenzzähler müssen vorab entfernt sein. Payloadzeiten seit Stundenbeginn werden als `transmitted_seconds_after_hour` separat geführt und ersetzen keinen vollständigen Empfangszeitstempel.

UDP aus einem vorhandenen lokalen Adapter:

```python
import json, socket
from datetime import datetime, timezone
observation = {
    "id": "stable-radio-id", "source": "my-adapter",
    "protocol": "OpenDroneID",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "messages_hex": [already_demodulated_25_byte_payload.hex()],
}
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.sendto(json.dumps(observation).encode(), ("127.0.0.1", 5700))
```

Maximal 16 KiB pro Meldung. Ungültige Pakete erhöhen einen Fehlerzähler und beenden den Listener nicht. Es werden keine System-Bluetooth-/WLAN-Einstellungen und keine Firewall-Regeln verändert.

## Quellen und Prüfung

- [OpenDroneID Core C und Referenzempfänger](https://github.com/opendroneid/opendroneid-core-c), insbesondere die Wire-Layouts und Einheiten in `libopendroneid/opendroneid.h` und `.c`; geprüft gegen Commit `a92bd5ffe113edd076c159a06799152aed3b1fb0`.
- [OpenDroneID Android-Referenzempfänger](https://github.com/opendroneid/receiver-android).
- [Analog Devices: Pluto-Spezifikationen](https://developer.analog.com/docs/system-level/solutions/platforms/pluto/index.html).
- [SDRplay: Herstellerübersicht der RSP-Empfänger](https://www.sdrplay.com/wp-content/uploads/2018/12/SDRplayDisplaySlideshowA.pdf).
- [WFB-ng-Projekt](https://github.com/svpcom/wfb-ng).

Softwaretests prüfen Bytepositionen und Einheiten, Message-Pack-Längen, ungültige Daten, Aktualität, getrennte Höhenreferenzen, Startpunkt-/Operatorsemantik und UDP-Lebenszyklus. Zwei Testvektoren wurden unabhängig mit den C-Encoderfunktionen des genannten Upstream-Commits erzeugt (Location mit Geschwindigkeitsmultiplikator und System mit Live-GNSS-Operator). Ein physischer RF-Empfang oder ein realer Drohnen-/Videolink wurde hier nicht abgenommen.
