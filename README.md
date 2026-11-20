# SignalClassifier 0.1.0

Lokales SDR-Labor für **Linux und Windows mit WSL2**: Breitband-IQ aufnehmen, belegte Kanäle selektiv speichern, Daten labeln und reproduzierbare Klassifikationsexperimente durchführen. Die deutsche Weboberfläche übernimmt die dunkle/cyanfarbene technische Gestaltung und den hellen Modus aus [KrakenDF](https://github.com/schildtaero/krakensdr), mit lokal ausgelieferten Assets.

Zentrales Repository: [schildtaero/SignalClassifier](https://github.com/schildtaero/SignalClassifier). Erste Version mit funktionalen Softwarepfaden; eine Abnahme an physischem PlutoSDR/RSP1A, der Windows-/WSL-Installation und einer CUDA-GPU steht aus. Synthetische Ergebnisse sind ausdrücklich gekennzeichnet.

## Start

Ubuntu 24.04 oder Debian 12+, Python 3.11+, mindestens 16 GB RAM empfohlen:

```bash
git clone https://github.com/schildtaero/SignalClassifier.git
cd SignalClassifier
./start.sh
```

Beim ersten Start richtet `setup.sh` eine lokale Pythonumgebung ein und installiert die Kernabhängigkeiten. Internet wird für diese Installation benötigt. Falls `venv` fehlt: `sudo apt install python3-venv`. Danach **http://127.0.0.1:8090** öffnen. Die Anwendung startet im gestoppten Zustand. Live, Recording und synthetische Quelle werden ausdrücklich gewählt; Hardwarefehler starten keine Simulation.

Unter Windows `start.cmd` ausführen. Eine vorhandene WSL2-Distribution mit Python 3.11+ wird verwendet. Bei fehlendem WSL zunächst in einer administrativen PowerShell `wsl --install -d Ubuntu-24.04` ausführen und die Einrichtung abschließen. Für größere IQ-Datensätze ist ein Checkout im Linux-Dateisystem von WSL sinnvoll. Es gibt keinen nativen Windows-USB-Pfad.

Neuronale Netze optional installieren:

```bash
./setup.sh --neural-cpu     # Laptop / reines CPU-Training
./setup.sh --neural         # Standard-PyTorch-Paket; CUDA-Verfügbarkeit danach prüfen
.venv/bin/python -c 'import torch; print(torch.__version__, torch.cuda.is_available())'
```

Für die NVIDIA-Workstation ein zur GPU und zum installierten Treiber passendes Paket aus dem [offiziellen PyTorch-Installer](https://pytorch.org/get-started/locally/) verwenden. Unter WSL benötigt CUDA den passenden **Windows-NVIDIA-Treiber**; keine zusätzliche Linux-Grafiktreiberinstallation durch diese Anwendung. Der Hardwarebericht erfasst den tatsächlich erkannten GPU-Namen, VRAM und Backend. Auf dem Ryzen-Laptop ist CPU der unterstützte Standard; eine Radeon-Beschleunigung wird nicht vorausgesetzt. Ohne PyTorch bleibt die Feature-LDA-Referenz verfügbar.

Eigener Datenordner oder anderer Port:

```bash
./start.sh --data-dir /pfad/zur/bibliothek --port 8091
```

Standard ist `~/.signalclassifier`, alternativ `SIGNALCLASSIFIER_DATA`. Dort liegen SQLite-Bibliotheken, Aufnahmen, Experimentberichte, Topologien und Modelle. Es wird nichts automatisch gelöscht. Die Anwendung bindet standardmäßig an localhost. `--host 0.0.0.0` erlaubt die Bedienung im eigenen LAN; dieser Einzelbenutzerbetrieb enthält keine Anmeldung und ist nicht für öffentliche Bereitstellung vorgesehen. Nicht mehrere Backendprozesse auf denselben Datenordner starten.

## Empfang und erster AM/FM-Versuch

**RSP1A:** Hersteller-API v3, SoapySDR und SoapySDRPlay3 gesondert installieren. Der Soapy-Python-Import muss in der Projektumgebung sichtbar sein. Ein systemweites Ubuntu-/Debian-Modul benötigt eine Umgebung mit dem passenden System-Python und `--system-site-packages`; siehe [Aufnahme und Treiber](docs/aufnahme.md). Ein unveränderter PlutoSDR empfängt UKW/Flugfunk nicht: Sein spezifizierter Standardbereich beginnt bei 325 MHz. Für UKW-FM gegen konventionelles Flugfunk-AM zunächst den RSP1A oder IQ-Recordings verwenden.

**Pluto:** `./setup.sh --pluto` und natives libiio installieren; URI beispielsweise `ip:192.168.2.1`. Die Anwendung nutzt ausschließlich den Empfangspfad.

USB unter WSL wird mit [usbipd-win](https://learn.microsoft.com/windows/wsl/connect-usb) an die Distribution durchgereicht. Treiber-, USB- und maximale Durchsatzprüfung erfolgen am konkreten Aufbau. Die Anwendung zeigt Treiber-/Streamfehler und beendet erkannte Überläufe.

1. Im Tab **Empfang** Quelle, Mittenfrequenz, Sample-Rate und Gain einstellen. Zum Softwaretest ausdrücklich „Synthetisch“ wählen.
2. **Breitband** zeichnet das gesamte eingestellte IQ-Fenster auf; **Nur Signale** filtert und dezimiert erkannte Kanäle in eigene SigMF-Aufnahmen. Zeit- und Bytegrenze setzen. Bei 2 MS/s benötigt `cf32_le` ungefähr 16 MB/s.
3. Unter **Aufnahmen** die gespeicherten Kanäle prüfen und unabhängig als `AM` oder `FM` bestätigen. Bandplanvorschläge sind keine bestätigten Labels. Für breite Recordings können per Replay im Modus „Nur Signale“ schmalbandige Trainingsclips erzeugt werden.
4. Unter **Experimente** entweder den klar gekennzeichneten synthetischen AM/FM-Versuch wählen oder bestätigte Recordings verwenden. Für reale Daten mindestens drei **unabhängige Aufnahmegruppen je Klasse** vorsehen. Mehrere Clips derselben Session zählen zusammen als eine Gruppe.
5. Mit **Feature-LDA** beginnen, anschließend kleine CNNs und die Promotionsadaptionen vergleichen. Konfiguration, Seed, Gruppenaufteilung, Vorverarbeitung, Hardware und Ergebnisse werden gespeichert.
6. Das Modell aktivieren und neue Aufnahmen oder den Live-Signalkanal prüfen. „Unbekannt“ ist eine heuristische Ablehnung, kein Nachweis eines umfassend validierten Open-Set-Klassifikators.

Für den ersten realen Vergleich Inhalte, Pegel und Empfangsbedingungen variieren. Ganze Sessions, Empfangstage und Frequenzbereiche zurückhalten. Flugfunk-Sprachpausen und UKW-Musik sind zusätzliche Einflussfaktoren; der Klassifikator soll Modulation lernen und keine Frequenz-/Nutzinhaltsabkürzung.

## Laptop → Workstation → Laptop

Auf dem Laptop aufzeichnen und Labels bestätigen. Die Aufnahme als SigMF-ZIP exportieren; auf der Workstation Metadaten und IQ-Datei aus dem ZIP gemeinsam importieren. Labels und ursprüngliche Aufnahmegruppen bleiben in den Metadaten erhalten. Dort trainieren, das **Modellpaket** exportieren, auf dem Laptop importieren und aktivieren. Das Paket enthält Topologie, Gewichte, Trainingsnormalisierung, Klassenreihenfolge, Vorverarbeitung und Bericht. CUDA-trainierte Gewichte werden CPU-portabel gespeichert.

Der Austausch ist dateibasiert. Es gibt keinen automatisch synchronisierenden Dienst und keinen Remote-GPU-Scheduler. Die komplette Oberfläche kann auf beiden Rechnern lokal laufen.

## Modelle, Experimente und Erklärung

Der Topologiekatalog enthält **34 Vorschläge** aus zehn Familien: Feature-LDA, Feature-MLP, IQ-CNN, Spektrogramm-CNN, 5-CNN, FB-LSTM, GRU, ResNet, TCN und Patch-Transformer. Breite, Tiefe und gegebenenfalls Kernel/Heads sind als validierte JSON-Konfiguration speicherbar. Vorschläge sind vor dem Experiment keine empirische Bestenliste. Die FB-LSTM-/2D-CNN-/5-CNN-Varianten sind anhand der Dissertation dokumentierte **HF-Adaptionen**, keine behaupteten identischen Nachbauten.

Experimentparameter umfassen Lernrate, Batchgröße, Epochen, Segmentlänge, SNR und Timing-Jitter in **RMS-Samples**. Mehrere Segmentlängen, Lernraten und Batchgrößen bilden eine begrenzte Versuchsreihe. Optional variieren zufällige Kanaleffekte innerhalb dokumentierter Grenzen: Frequenzversatz, Sampletaktfehler, IQ-Amplituden-/Phasenfehler, DC-Offset, Clipping und ein verzögertes Echo. Eigene bestätigte Aufnahmen können 2–64 frei benannte Klassen enthalten; die synthetische Quelle bleibt AM/FM. Train/Validation/Test werden nach Ursprungsgruppen getrennt, Normalisierung wird nur auf Training angepasst; die Auswahl erfolgt über Validierung. SNR bei realen Aufnahmen bezeichnet zusätzlich injiziertes Rauschen relativ zur vorhandenen Gesamtleistung, keinen gemessenen ursprünglichen RF-SNR.

Ergebnisse enthalten Konfusionsmatrix, Accuracy/Balanced Accuracy/Macro-F1, Recall, Brier/ECE, Trainings- und Vorverarbeitungszeit, p50/p95-Inferenzlatenz, Echtzeitfaktor, Modellgröße, Parameterzahl und SNR-Kurve. Die Laufzeit relativ zu einer festen Referenzlast unterstützt Gerätevergleiche, ist aber keine exakte Hardwareprognose. Fehlalarmrate, Open-Set-Güte und kalibrierte Empfindlichkeit in dBm benötigen eigene Negativ-/Kalibrierdatensätze; fehlende Messwerte bleiben offen.

Der Entscheidungsweg zeigt Merkmale, Vorwissen, gewähltes Modell und Ablehnung. Für LDA werden additive Merkmalsbeiträge angegeben; die Aufnahmeprüfung neuronaler Netze kann lokale Gradient×Input-Sensitivität darstellen. Diese Beiträge sind Hinweise zur Modellentscheidung und keine physikalischen Kausalitätsbeweise. Frequenzvorwissen kann optional als erklärender Bandhinweis zugeschaltet werden; es ändert in dieser Version keine Modellwerte. Weitere experimentelle Variationen und die Quellen der Promotionsansätze: [Methodik](docs/quellen-und-methodik.md), [Topologien](docs/topologien.md).

## Vorlabel-Datenbank und Drohnen

Die lokale SQLite-Datenbank trennt Funkdienst, Standard und Modulation. Suchbereiche sind für UKW, Flugfunk, ADS-B/Mode S, DAB+, DVB-T2, POCSAG, AIS, ACARS, VDL2, PMR446, DMR, TETRA, LoRa/SRD, APRS, Amateurfunk und Remote ID angelegt. Das sind dokumentierte **Suchhinweise**, keine verifizierte lokale Senderliste für Bamberg/Nürnberg. Eigene Einträge und Quellen können ergänzt werden. Bestätigte Aufnahmelabels werden separat geführt.

Der Drohnen-Tab enthält einen **OpenDroneID-v2-Payloaddecoder**, eine explizite HTTP-/UDP-JSON-Eingangsschnittstelle und Kartenpositionen für tatsächlich zugeführte Drohnen-/Operator-/Startpunktdaten mit getrennten Höhenreferenzen und Aktualität. Die Karte startet offline ohne externe Kachelanfragen; Online-OSM ist eine ausdrückliche Option. Ohne Kartenkacheln gibt es ein Koordinatenraster.

**Direkte Bluetooth-/WLAN-RF-Erfassung und universelles DJI-/FPV-/Video-Decodieren sind nicht enthalten.** Die Protokolltabelle nennt nötige externe Empfangswege und Grenzen. Der RSP1A reicht bis 2 GHz und erreicht die gängigen 2,4-/5,8-GHz-Bänder nicht; ein Standard-Pluto erreicht 2,4 GHz, aber kein 5,8 GHz. Für Remote ID ist ein geeigneter BLE-/WLAN-Scanner der nächste Integrationsschritt. [Decoderstatus und JSON-Schema](docs/drohnen.md).

## Softwareprüfung

```bash
.venv/bin/python -m pip install -e '.[test]'
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python -m unittest discover -s tests -v
node --check signalclassifier/static/app.js
bash -n start.sh setup.sh
```

Die Suite benötigt keine SDR-Hardware; UDP-Tests verwenden nur Loopback. Neuronale Tests laufen zusätzlich, wenn PyTorch installiert ist. GitHub Actions prüft den Kern unter Python 3.11/3.12 und einen CPU-PyTorch-Pfad. Die API-Dokumentation ist lokal unter `/docs` verfügbar. Prüfstand und bekannte Grenzen: [Validierung](docs/validierung.md).

Leaflet wird lokal mit BSD-Lizenz ausgeliefert, siehe `signalclassifier/static/vendor/leaflet/LICENSE`. Die Anwendung lädt weder Modelle aus Python-Pickles noch fremde ausführbare Netzdefinitionen. Modellpakete enthalten validierte Konfiguration und numerische Gewichte; PyTorch-Laden verwendet `weights_only=True`.
