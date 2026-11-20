# Empfang, SigMF und selektive Aufnahme

Die Anwendung verwendet genau einen Empfänger. Die Quelle wird ausdrücklich als **PlutoSDR**, **RSP1A**, **Recording** oder **synthetisches Signal** gewählt. Fehlende Hardware oder Treiber erzeugen einen sichtbaren Fehler; sie starten keine Simulation. Die Softwareadapter sind implementiert, tatsächlicher USB-Empfang und Maximaldurchsatz wurden in der Entwicklungsumgebung nicht an physischer Hardware verifiziert.

## Quellen und Installation

### PlutoSDR

Alle Host-Komponenten mit `./install_driver.sh --device pluto` einrichten; ohne `--device` installiert der Helfer Pluto und RSP1A. Enthalten sind natives libiio, die passenden USB-Regeln und pyadi-iio/pylibiio. [Installationsoptionen und Prüfung](install-driver.md).

Der Adapter verwendet Analog Devices `pyadi-iio` und die installierte native `libiio`-Bibliothek. In der Projektumgebung installieren:

```bash
.venv/bin/python -m pip install -e '.[pluto]'
```

Auf Debian/Ubuntu wird für libiio je nach Distributionsversion das Paket `libiio0` beziehungsweise dessen Nachfolger benötigt. Eine mit `iio_info -u ip:192.168.2.1` erreichbare Pluto-Verbindung ist eine sinnvolle Treiberprüfung. Die URI kann im Empfangsdialog angegeben werden, zum Beispiel `ip:192.168.2.1` oder eine vom libiio-Gerätescan gemeldete USB-URI. Der Adapter setzt ausschließlich RX-Eigenschaften, keinen TX-Pfad.

Ein unveränderter Pluto deckt **325 MHz bis 3,8 GHz** und bis zu **20 MHz momentane RF-Bandbreite** ab. Damit liegen UKW-Rundfunk und VHF-Flugfunk außerhalb seiner spezifizierten Standardabstimmung. Für das erste AM/FM-Experiment RSP1A oder passende IQ-Aufnahmen verwenden. Nicht spezifizierte Firmware-/Hardwareerweiterungen werden nicht automatisch aktiviert. Die Anwendung akzeptiert im Standardadapter 520834–61440000 S/s und manuelles RX-Gain von −3 bis 71 dB; das konkrete Gerät kann Einstellungen weiter beschränken. Die Sample-Rate allein bedeutet nicht, dass die USB-Verbindung diesen Durchsatz lückenlos übertragen kann.

Primärquellen: [Analog Devices ADALM-PLUTO](https://www.analog.com/en/resources/evaluation-hardware-and-software/evaluation-boards-kits/adalm-pluto.html), [offizieller Python-Einstieg](https://developer.analog.com/docs/system-level/solutions/platforms/pluto/software/python_install.html), [pyadi-iio AD936x-Treiber](https://github.com/analogdevicesinc/pyadi-iio/blob/main/adi/ad936x.py).

### SDRplay RSP1A

Der vollständige Installationshelfer nur für RSP1A ist `./install_driver.sh --device rsp1a`; ohne Auswahl werden zusätzlich Pluto-Komponenten installiert. Er richtet Systempakete, Hersteller-API, SoapySDRPlay3 und die passende Python-Anbindung ein. Unter WSL wählt er standardmäßig USB-Bulk für das Soapy-Modul. Die Herstellerlizenz bleibt interaktiv, die USB-Durchreichung zu WSL erfolgt separat. [Aufruf, lokale Installerdatei und Diagnose](install-driver.md).

Der Adapter verwendet die **SDRplay API v3**, **SoapySDR** und das Hardwaremodul **SoapySDRPlay3**. Die proprietäre SDRplay API wird vom Hersteller separat unter dessen Lizenz installiert. Sie ist nicht im Repository enthalten. Der Soapy-Python-Import muss im verwendeten Python-Interpreter verfügbar sein. Ein systemweites `python3-soapysdr` ist in einer isolierten Pythonumgebung nicht automatisch sichtbar; eine mit dem System-Python angelegte Umgebung mit `--system-site-packages` oder eine passende Soapy-Installation in der Umgebung löst dies.

Vor dem Empfang prüfen:

```bash
SoapySDRUtil --find="driver=sdrplay"
SoapySDRUtil --probe="driver=sdrplay"
```

URI/Soapy-Argumente sind beispielsweise `driver=sdrplay` beziehungsweise `driver=sdrplay,serial=<Gerätekennung>`. Der RSP1A deckt 1 kHz bis 2 GHz und bis zu 10 MHz sichtbares Spektrum ab. Die Anwendung begrenzt die angeforderte Rate auf 10 MS/s. Die vom Treiber tatsächlich zurückgelesene Rate muss zur Anfrage passen; nicht unterstützte Raten werden sichtbar abgelehnt. Die Gain-Zuordnung erfolgt über die Gesamt-Gain-Schnittstelle des Soapy-Treibers; die Hardware kann sie frequenzabhängig begrenzen. Ein `readStream`-Fehler oder Overflow beendet den Stream, statt die Lücke als zusammenhängende IQ-Daten auszugeben.

Primärquellen: [RSP1A-Datenblatt des Herstellers](https://www.sdrplay.com/wp-content/uploads/2018/01/180109RSP1AdatasheetV1.8.pdf), [SoapySDRPlay3](https://github.com/pothosware/SoapySDRPlay3), [SoapySDR Device API](https://pothosware.github.io/SoapySDR/doxygen/latest/classSoapySDR_1_1Device.html).

### Windows mit WSL2

Die Anwendung und die Empfängertreiber laufen unter Linux in WSL2. USB-Geräte müssen mit `usbipd-win` an die aktive WSL-Distribution durchgereicht werden. Das Windows-Batchskript startet die Linux-Anwendung; es ersetzt die USB-Zuordnung und die Installation der SDRplay-/libiio-Treiber nicht. Ein als Netzwerkgerät erreichbarer Pluto kann über die libiio-IP-URI genutzt werden, sofern Windows/WSL die entsprechende Route zum Gerät besitzt. Bei USB-Zuordnung und SDRplay-Service müssen die Hardwareherstelleranleitungen für das konkrete System geprüft werden.

### Replay und synthetischer Modus

Replay liest eine importierte SigMF-Aufnahme in begrenzten Blöcken und spielt sie mit ihrer gespeicherten Sample-Rate zeitlich passend ab. Am Dateiende stoppt es. Frequenzwechsel in mehreren SigMF-Captures werden übernommen; bestehende selektive Kanäle schließen am Wechsel. Eine neue Breitbandaufnahme eines Replays enthält die Frequenzwechsel in ihrer Capture-Liste.

Der ausdrücklich gewählte synthetische Modus erzeugt AM mit Träger beziehungsweise FM mit zwei Audiosinusanteilen und additivem Rauschen. Die erzeugte Modulation steht in den Metadaten, wird aber nicht in den Klassifikator eingespeist. Das ist ein Softwarefunktionstest, keine Messung realer Empfänger oder realer Empfangsqualität.

## Drei Aufnahmemodi

**Monitor** berechnet Spektrum und stärksten Signalkanal, speichert aber keine IQ-Aufnahme.

Für die Live-Klassifikation werden bis zu 65536 zusammenhängende Samples desselben ausgewählten Kanals gesammelt. Dadurch können trainierte Modelle ihre konfigurierte Segmentlänge auch bei stark dezimierten schmalen Kanälen erreichen. Nach Kanalwechsel, Retune, Abtastratenwechsel oder Quellenneustart beginnt dieser Puffer neu; während des Aufbaus kann ein Modell noch nicht genügend Samples haben. Der Puffer vermischt keine Ausschnitte verschiedener Kanäle.

**Breitband** speichert alle gelieferten IQ-Samples der ausgewählten RF-Abstimmung fortlaufend als `cf32_le`. Die Dateigröße wächst um 8 Byte pro komplexem Sample. Bei 2 MS/s sind das 16 MB/s, rund 960 MB pro Minute. Der Speicherbedarf ist vor einem langen Lauf zu berücksichtigen.

**Nur Signale / Energie** erkennt belegte Spektralbereiche, mischt jeden ausgewählten Bereich ins Basisband, filtert ihn mit einem kontinuierlichen kausalen FIR und dezimiert ihn. Es wird somit ein schmalbandiger IQ-Kanal gespeichert, nicht bloß eine unveränderte Breitbanddatei mit einer Markierung. Bis zu acht Kanäle werden parallel aufgenommen. Frequenzlage, ursprüngliche Sample-Rate, Ausgangsrate, Start-/Stop-Sampleindex des Ursprungsstroms, FIR-Gruppenlaufzeit, Detektionsschwelle und gemeinsame Session-ID bleiben in den Metadaten erhalten. Ein Kanal schließt nach einer Erkennungspause von mindestens 0,25 s; die tatsächliche Nachlaufzeit ist auf den nächsten RF-Block gerundet. Dieses Build enthält **keinen rückwirkenden Pretrigger-Puffer**. Der erste aufgezeichnete Block kann einen Filtereinschwingvorgang enthalten.

Nahe belegte FFT-Bereiche werden standardmäßig über bis zu 3 kHz unbelegten Zwischenraum zusammengefasst. So landen beispielsweise ein AM-Träger und seine nahen Seitenbänder gemeinsam in einem Kanal. Die Mindestkanalbreite beträgt standardmäßig 12,5 kHz; erkannte breitere Signale erhalten mehr Bandbreite und Filterreserve. Beide Werte sind über `merge_gap_hz` und `minimum_channel_bandwidth_hz` im Start-API konfigurierbar. Das ist eine heuristische Segmentierung, keine protokollbasierte Kanalgrenzenerkennung. Nahe Sender können zusammenfallen; wechselnde Bandbreite kann einen neuen Clip auslösen. Dauerhaft breit belegte Spektren und starke Störer begrenzen die Medianmethode.

Alle Modi haben eine Zeitgrenze (`duration_seconds`, 0,1 s bis 24 h). Zusätzlich gilt eine gemeinsame IQ-Datenobergrenze `max_bytes` für die Aufnahme, standardmäßig 1 GiB, maximal 64 GiB. Diese Grenze gilt für alle selektiven Kanäle zusammen; die Dateien überschreiten sie nicht. Metadaten werden zusätzlich gespeichert. Eine freie Festplattenkapazität wird dadurch nicht garantiert; ein Schreibfehler bleibt als Aufnahmefehler sichtbar. Eine Aufnahme beendet sich beim ersten erreichten Limit. Leere Streams erzeugen keine vermeintlich gültigen Aufnahmen.

## Pegel und Energieerkennung

Das Hann-gefensterte FFT-Spektrum ist so normiert, dass ein komplexer Ton mit Amplitude 1 auf einer FFT-Bin-Mitte einen Spitzenwert von 0 dBFS ergibt. Der Rauschpegel ist ein **digitaler Pegel pro FFT-Bin**, keine Leistung pro Hz. Die Schwellwertentscheidung vergleicht diesen Pegel mit dem Median der FFT-Bins. `excess_db` bedeutet Abstand vom Bin-Median; daraus wird kein kalibriertes SNR behauptet. `snr_db` bleibt bei dieser Methode leer. FFT-Länge und Fenster beeinflussen den dargestellten Rauschpegel.

Pluto-Rohwerte werden unter Annahme vorzeichenbehafteter 12-Bit-I/Q-Komponenten durch 2048 normiert. Soapy-CF32-Daten verwenden die Skalierung des installierten Treibers. Importierte `ci16_le`-Daten werden komponentenweise durch 32768 geteilt; importierte `cf32_le`-Daten bleiben unverändert. Pegel sind in keinem Pfad auf dBm kalibriert. Analoge Verstärkung, Kabel, Antenne, Frontendfilter und tatsächliche AD-Wandleraussteuerung müssen bei physikalischen Leistungs- oder Empfindlichkeitsmessungen berücksichtigt werden.

Der Adapter kann Pluto-Zeitstempellücken ohne zusätzliche Hardwaretelemetrie nicht vollständig nachweisen. `gap_free_verified` bleibt deshalb falsch; die Software behauptet keinen lückenlosen HF-Nachweis allein aufgrund eines gestarteten Streams.

Für die Live-Klassifikation werden bis zu 65.536 zusammenhängende Samples desselben Signalkanals gesammelt. Der Puffer startet bei Kanal-, Frequenz-, Abtastraten- oder Quellenwechsel neu. Ein trainiertes Modell erhält die zuletzt verfügbaren Samples in seiner gespeicherten Segmentlänge; reicht der aktuelle Kontext noch nicht aus, wird die Inferenz mit einer sichtbaren Begründung übersprungen. IQ und zugehörige Abtastrate/Frequenz werden gemeinsam gelesen.

## Hohe Sample-Raten, Warteschlange und Diagnose

Der Hardware-Lesepfad läuft unabhängig von Spektrumanalyse, Kanalfiltern und Datenträgerzugriffen in einem eigenen Thread. Zwischen Empfänger und Verarbeitung liegt eine **begrenzte IQ-Warteschlange**, standardmäßig 64 MiB. Bei 5 MS/s und `cf32_le` entspricht das ungefähr 1,68 Sekunden RF-Daten. Das ist zusätzlich zum aktuellen Empfangs-/Verarbeitungsblock und den begrenzten DSP-Arbeitspuffern; es ist keine Angabe des gesamten Prozess-RAMs. `queue_capacity_bytes` erlaubt mindestens einen Empfangsblock bis maximal 256 MiB und wird auf ganze Blöcke abgerundet. Bibliotheken mit wiederverwendeten Empfangspuffern werden vor dem nächsten Lesezugriff kopiert. Die integrierten Pluto-/RSP-Adapter liefern bereits eigenständige Arrays.

Diese Entkopplung puffert kurze Scheduling- und Schreibpausen. Sie kann eine **dauerhaft zu langsame Verarbeitung nicht ausgleichen**. Ist die Queue voll, endet die Aufnahme am ersten nicht aufgenommenen Block mit einem sichtbaren `queue_overflow`. Bereits zugelassene, zusammenhängende Blöcke werden noch in Reihenfolge verarbeitet und gespeichert. Der Fehler bleibt im Status und in den Metadaten erhalten. Die Anwendung wirft keine mittleren Blöcke weg, um danach scheinbar lückenlos weiterzuschreiben. Ein vom RSP-Treiber gemeldeter Overflow/Corruption wird ebenfalls als Fehler übernommen. Ein gewollter Stop oder das Erreichen des Speicherlimits kann einen noch wartenden **Endabschnitt** verwerfen; `discarded_after_stop_samples` zählt ihn und der Stopgrund bleibt erhalten.

Die Kanäle verwenden weiterhin denselben kausalen FIR und eine kontinuierliche Dezimierungsphase. Seine Umsetzung hält Filter-FFT und Oszillator im Cache, rechnet mit komplexen 32-Bit-Werten und berechnet über Frequenzfaltung direkt die kurze, dezimierte inverse FFT. Das vermeidet einen vollständigen inversen FFT-Schritt für Samples, die anschließend verworfen würden. Der stärkste Aufnahmekanal wird für die Inferenz wiederverwendet. Er wird nicht ein zweites Mal mit voller Eingangsrate gefiltert. Ein Vergleich gegen vollständige FIR-Faltung und gegen beliebig geschnittene Streaming-Blöcke ist Teil der Softwaretests.

Monitor und Breitbandmodus aktualisieren die Spektrumanalyse standardmäßig mit **8 Hz** (`spectrum_refresh_hz`, 1–30 Hz). Der Inferenzkanal verarbeitet trotzdem aufeinanderfolgende Blöcke, damit seine Samples zusammenhängend bleiben. Im selektiven Modus erfolgt die Energieentscheidung standardmäßig **für jeden RF-Block**. Bei 131072 Samples und 5 MS/s sind das 26,2144 ms pro Entscheidung; die FFT betrachtet bis zu 32 Fenster innerhalb dieses Blocks. Ein größeres `detection_interval_seconds` (0–1 s; 0 bedeutet jeder Block) senkt den Aufwand für die Energieentscheidung, kann jedoch kurze Signale verpassen. `max_channels` begrenzt die gleichzeitig gespeicherten Kanäle auf 1–8. Die Spektrumveröffentlichung bleibt auch im selektiven Modus bei der eingestellten Bildrate.

Die FFT analysiert dabei höchstens die ersten 65536 Samples eines RF-Blocks. Bei einem 131072-Sample-Block ist daher nur die erste Hälfte durch diese Energiebeobachtung abgedeckt; kurze Signale im übrigen Abschnitt können unentdeckt bleiben. `analysis_sample_count`, `analysis_sample_offset` und `analysis_duration_seconds` beschreiben den tatsächlich analysierten Abschnitt. Die Wasserfall-Zeitsegmente verwenden diese Dauer, nicht die größere `block_duration_seconds`. `analysis_timestamp` beziehungsweise `timestamp` bezeichnen das geschätzte Ende dieses FFT-Abschnitts: Host-Empfangszeit des ganzen Blocks minus Dauer seines nicht analysierten Endabschnitts. `block_received_at` erhält die ursprüngliche Host-Empfangszeit. Das sind Host-Zeitbezüge; `rf_timestamp_verified` bleibt falsch. Unbeobachtete Zeitbereiche werden dadurch nicht als durchgängig gemessen dargestellt.

`receiver.performance` im Status und `global.sc:performance` in einer abgeschlossenen Aufnahme enthalten:

| Feld | Bedeutung |
| --- | --- |
| `actual_sample_rate` | Vom Adapter zurückgelesene Sample-Rate in S/s, kein gemessener Host-Datendurchsatz |
| `reader_ms` | Dauer des letzten Lesezugriffs einschließlich Warten auf Hardwaredaten |
| `processing_ms` | Dauer des letzten verarbeiteten Blocks einschließlich dessen IQ-Schreibzugriffen |
| `writer_ms` | Summe der IQ-Schreibzeiten dieses Blocks; Metadatenabschluss ist nicht enthalten |
| `processing_rtf` | Geglättetes Verhältnis Verarbeitungszeit / RF-Blockdauer; dauerhaft > 1 bedeutet, dass die Verarbeitung zurückfällt |
| `processing_rtf_mean` | Dasselbe Verhältnis aus kumulierter Verarbeitungs- und RF-Dauer |
| `queue_blocks`, `queue_bytes` | Aktuelle wartende Blöcke bzw. deren tatsächliche IQ-Bytes |
| `queue_capacity_blocks`, `queue_capacity_bytes` | Harte Aufnahmegrenzen der Warteschlange |
| `queue_high_water_blocks`, `queue_high_water_bytes` | Höchste erreichte Belegung dieses Laufs |
| `read_blocks`, `processed_blocks` | Gelesene beziehungsweise fertig verarbeitete Blöcke |
| `source_samples_read`, `source_samples_processed` | Zugehörige ursprüngliche IQ-Samplezähler |
| `overflows`, `gaps` | Anzahl gemeldeter Overflow-/Unterbrechungsereignisse, keine Anzahl verlorener Samples |
| `capture_error` | Konkreter Aufnahmefehler oder `null` |
| `detection_interval_seconds`, `publication_hz` | Konfigurierte/effektive Entscheidungstaktung und Spektrumbildrate |
| `gap_free_verified` | Bleibt ohne unabhängigen Nachweis `false`, auch wenn kein Fehler gemeldet wurde |

Zusätzlich enthält `global.sc:integrity` die gemeldeten Ereignisse, den Fehler, Stopgrund und Samplezähler. Damit bleiben Qualitätsprobleme auch nach dem Transfer auf die Workstation sichtbar. Eine Anzahl von **0 gemeldeten Lücken ist kein Nachweis lückenlosen HF-Empfangs**.

### Reproduzierbarer Softwarebenchmark

```bash
.venv/bin/python scripts/benchmark_capture.py --rate 5000000 --blocks 24 --channels 8
.venv/bin/python scripts/benchmark_capture.py --mode energy --blocks 24 --channels 8 --hardware-queue
```

Das Skript erzeugt dieselben deterministischen acht schmalen Töne mit Rauschen, jeweils 24 Blöcke à 131072 Samples bei 5 MS/s (0,6291456 s RF-Zeit). Es misst Monitor, Breitband und selektive Aufnahme mit tatsächlichen temporären IQ-Dateien. Die zweite Variante übt die Hardware-Queue mit einem ungebremsten **numerischen** Datenstoß; sie greift nicht auf ein Gerät zu. Das JSON enthält Umgebung, Parameter und Messwerte. Ein Lauf sollte für belastbare lokale Vergleiche mehrfach unter vergleichbarer Rechnerlast durchgeführt werden.

Ein Vorher-/Nachher-Profil mit identischen 5-MS/s-Daten auf dem Entwicklungsrechner ergab folgende Gesamtzeiten; der Profiling-Aufwand war in beiden Messungen enthalten:

| Modus | Vorher | Nachher | Verhältnis zur RF-Dauer vorher / nachher |
| --- | --- | --- | --- |
| Monitor | 0,384 s | 0,095 s | 0,61 / 0,15 |
| Breitband | 0,439 s | 0,201 s | 0,70 / 0,32 |
| Selektiv, 8 Kanäle | 2,570 s | 0,524 s | 4,09 / 0,83 |

Die ursprüngliche selektive Verarbeitung war in diesem Versuch also rund viermal langsamer als die RF-Zeit, obwohl der Monitor noch mithalten konnte. Die optimierte Variante war bei diesem Datensatz rund fünfmal schneller. Das erklärt einen möglichen Softwareengpass, bestätigt aber weder eine konkrete USB-Datenlücke am Benutzergerät noch garantiert es lückenlosen 5-MS/s-Betrieb auf jedem Laptop. Anzahl/Bandbreite der Signale, Treiber, Filesystem und paralleles Training beeinflussen die tatsächliche Reserve. Unter WSL sind Aufnahmen im Linux-Dateisystem normalerweise günstiger als Schreibzugriffe auf eingebundene Windows-Laufwerke; der lokale `writer_ms`- und Queue-Verlauf liefert dafür die konkrete Messung.

## Unterstützter SigMF-Ausschnitt

Unterstützt werden einkanalige komplexe `cf32_le`- und `ci16_le`-Datasets, gleichnamig mit `.sigmf-meta` und `.sigmf-data`, kontinuierlich und ohne eingebettete Capture-Header. JSON-Metadaten sind auf 4 MiB begrenzt. Ein direkter Lesezugriff ist auf 16 Mi komplexe Samples begrenzt; Replay und große Aufnahmen werden segmentweise verarbeitet. Annotationen und Sampleindizes werden auf den tatsächlichen Datenumfang geprüft. Nicht endliche IQ-Daten, Datenreste innerhalb eines Samples, Mehrkanalformate, externe Dataset-Pfade, Symlinks und Abtastratenwechsel innerhalb einer Datei werden sichtbar abgelehnt. Es wird nichts als ausführbarer Python-Inhalt geladen.

Die App-Metadaten stehen im optionalen `sc:`-Namensraum; `signalclassifier:session_id` ist die kompatible Gruppierungskennung für die Experimentverwaltung. Label- und Verifikationsfelder der Anwendung verwenden `signalclassifier:`. Beide Erweiterungen sind optional, sodass allgemeine SigMF-Reader sie ignorieren können. Für den Transfer Laptop → Workstation sollten Metadaten und IQ-Daten gemeinsam exportiert werden. Der ZIP-Download ist ein Transportpaket; das standardisierte SigMF-Archivformat `.sigmf` wäre dagegen ein TAR-Archiv und wird hier nicht als ZIP ausgegeben.

Primärquelle zum Dateiformat: [SigMF-Spezifikation v1.2.6](https://sigmf.org/).
