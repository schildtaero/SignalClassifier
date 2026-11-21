# SignalClassifier 0.4.1

Lokales SDR-Labor für **Linux und Windows mit WSL2**: Breitband-IQ aufnehmen, belegte Kanäle selektiv speichern, Daten labeln und reproduzierbare Klassifikationsexperimente durchführen. Die deutsche Weboberfläche übernimmt die dunkle/cyanfarbene technische Gestaltung und den hellen Modus aus [KrakenDF](https://github.com/schildtaero/krakensdr), mit lokal ausgelieferten Assets.

Zentrales Repository: [schildtaero/SignalClassifier](https://github.com/schildtaero/SignalClassifier). Version 0.4.1 zeigt konkrete Suchfehler mit Statuszahlen und Budgethinweisen an und übernimmt gültige Teilberichte nach vorübergehenden Lesefehlern erneut. Die in 0.4.0 eingeführten begrenzten parallelen Experimentworker, die effizientere Trainingsschleife und CPU-/GPU-Telemetrie bleiben enthalten. Automatische Hyperparameteroptimierung, Berichtsdatenexport, Sammelauswahl, Bereinigung kurzer Aufnahmen, Batchlabels, Mehrmodellvergleich und Live-Stichproben sind enthalten. Eine Abnahme an physischem PlutoSDR/RSP1A, der Windows-/WSL-Installation und einer CUDA-GPU steht aus. Synthetische Ergebnisse sind ausdrücklich gekennzeichnet.

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

**Empfängertreiber:** Alle benötigten Komponenten für PlutoSDR und RSP1A unter Ubuntu/Debian mit dem beiliegenden Helfer einrichten:

```bash
./install_driver.sh                   # beide Empfänger
./install_driver.sh --device pluto    # nur PlutoSDR
./install_driver.sh --device rsp1a    # nur RSP1A
```

Das Skript installiert für Pluto natives libiio, USB-Regeln und pyadi-iio/pylibiio; für RSP1A die Hersteller-API v3.15+, eine fest versionierte SoapySDRPlay3-Erweiterung und die Python-Anbindung. Eine vorhandene kompatible `.venv` samt optionalem PyTorch bleibt erhalten; eine inkompatible Umgebung wird nur mit `--recreate-venv` gesichert und ersetzt. Die SDRplay-Lizenz wird im Originaldialog beantwortet. Falls der Hersteller den automatischen Download blockiert, den Installer von der [offiziellen Linux-Downloadseite](https://sdrplay.com/download/hardware-api-linux/) beziehen und `./install_driver.sh --api-installer /pfad/zur/Datei.run` verwenden. Pluto wird im Standardlauf vor der SDRplay-Installation eingerichtet. `--check` prüft die Installation, `--dry-run` zeigt die Schritte ohne Änderungen. [Vollständige Treiberinstallation und Fehlerdiagnose](docs/install-driver.md).

Ein unveränderter PlutoSDR empfängt UKW/Flugfunk nicht: Sein spezifizierter Standardbereich beginnt bei 325 MHz. Für UKW-FM gegen konventionelles Flugfunk-AM zunächst den RSP1A oder IQ-Recordings verwenden.

**Pluto:** `./install_driver.sh --device pluto` richtet den vollständigen Host-Empfangspfad ein; URI beispielsweise `ip:192.168.2.1`. Die Anwendung nutzt ausschließlich den Empfangspfad. Für Netzwerkempfang ist zusätzlich eine Route zum Pluto nötig; USB unter WSL muss durchgereicht sein. Neue Mitgliedschaft in `plugdev` wird gegebenenfalls erst nach erneuter Anmeldung wirksam.

USB unter WSL wird mit [usbipd-win](https://learn.microsoft.com/windows/wsl/connect-usb) an die Distribution durchgereicht. Treiber-, USB- und maximale Durchsatzprüfung erfolgen am konkreten Aufbau. Die Anwendung zeigt Treiber-/Streamfehler und beendet erkannte Überläufe.

1. Im Tab **Empfang** Quelle, Mittenfrequenz, Sample-Rate und Gain einstellen. Zum Softwaretest ausdrücklich „Synthetisch“ wählen.
2. **Breitband** zeichnet das gesamte eingestellte IQ-Fenster auf; **Nur Signale** filtert und dezimiert erkannte Kanäle in eigene SigMF-Aufnahmen. Zeit- und Bytegrenze setzen. Bei 2 MS/s benötigt `cf32_le` ungefähr 16 MB/s.
3. Unter **Aufnahmen** die gespeicherten Kanäle prüfen. Für typische Frequenzfenster werden `AM`, `FM` oder andere Klassen vorgeschlagen. Zeilen auswählen, gegebenenfalls **Alle sichtbaren auswählen**, dann **Labels bestätigen**: entweder die individuellen eindeutigen Vorschläge übernehmen oder ein gemeinsames Label ausdrücklich angeben. Die Vorschau nennt die betroffenen Dateien. Bereits bestätigte und mehrdeutige Vorschläge werden standardmäßig übersprungen. Für breite Recordings können per Replay im Modus „Nur Signale“ schmalbandige Trainingsclips erzeugt werden.
4. Unter **Experimente** bestätigte Recordings einzeln oder mit **Alle auswählen** übernehmen; **Auswahl aufheben** entfernt die Auswahl. Die Vorprüfung zeigt Dateien, unabhängige Sitzungen und verwendbare Segmente je Klasse. **Mit unabhängiger Bewertung** braucht drei Aufnahmegruppen je Klasse; mehrere Clips derselben Session zählen zusammen als eine Gruppe. Für den Einstieg reicht im ausdrücklich gewählten Modus **Nur trainieren** eine Gruppe je Klasse. Solche Modelle bleiben unbewertet und bekommen keine Genauigkeit oder Qualitätsrangfolge.
5. Mehrere Topologien auswählen und mit denselben Daten, Gruppen und Einstellungen vergleichen. Auch hier sind Sammelauswahl und Aufheben möglich; ohne PyTorch bleiben neuronale Ansätze gesperrt. Mit **Feature-LDA**, einem kleinen CNN und einer Promotionsadaption beginnen. Konfiguration, Seed, Gruppenaufteilung, Vorverarbeitung, Hardware, sämtliche Modelle und Ergebnisse werden gespeichert. Bis zu 1000 Aufnahmen und 50 Topologien sind auswählbar; das 128-MiB-IQ-Budget und die Segmentgrenzen gelten weiterhin und werden nicht durch eine Sammelauswahl umgangen.
6. Ergebnis nach Validierungsgenauigkeit oder Gesamtscore sortieren, **Top 3 live testen** wählen oder selbst bis zu drei Modelle markieren. Der Empfang wird separat gestartet. Live-Stichproben zeigen Vorhersagen und auf diesem Rechner gemessene Laufzeiten; ohne Referenzlabel gibt es keine Live-Genauigkeit. „Unbekannt“ ist eine heuristische Ablehnung, kein Nachweis eines umfassend validierten Open-Set-Klassifikators.

Für den ersten realen Vergleich Inhalte, Pegel und Empfangsbedingungen variieren. Ganze Sessions, Empfangstage und Frequenzbereiche zurückhalten. Flugfunk-Sprachpausen und UKW-Musik sind zusätzliche Einflussfaktoren; der Klassifikator soll Modulation lernen und keine Frequenz-/Nutzinhaltsabkürzung.

**Kurze Aufnahmen entfernen:** Unter **Aufnahmen** die Bereinigung **unter 0,5 s** öffnen. Die Vorschau zeigt die konkreten Dateien, Dauer, Labels und Gesamtgröße; erst die Bestätigung löscht die angezeigten unveränderten SigMF-Dateipaare samt Labels dauerhaft. Genau 0,5 s lange Aufnahmen bleiben erhalten. Empfang und Replay müssen gestoppt sein; Daten laufender oder vorgemerkter Experimente werden übersprungen. Neue Dateien nach Öffnen der Vorschau werden nicht nachträglich einbezogen. Auch bereits bestätigte Labels und Quelldaten abgeschlossener Experimente können gelöscht werden: Berichte und Modelle bleiben bestehen, ein erneuter Trainingslauf benötigt jedoch die ursprünglichen Dateien. Exportierte ZIP-Kopien werden nicht entfernt.

**5 MS/s und längere Aufnahmen:** Breitband benötigt 40 MB/s, für drei Minuten also rund 7,2 GB. Das voreingestellte UI-Dateibudget von 512 MiB ist dann nach ungefähr 13,4 s erreicht. Die Oberfläche zeigt Größenabschätzung und Budget-/Zeitstopps ausdrücklich an. Selektive Aufnahmen hängen von Anzahl und Ausgangsrate der Kanäle ab. Der Hardwareleser arbeitet über eine begrenzte Queue unabhängig von DSP und Schreiben; tatsächliche Überläufe, Verarbeitung/RF-Zeit und Schreibzeiten bleiben sichtbar. Unter **Aufnahmeleistung einstellen** lassen sich Kanalzahl, Puffer, Anzeigerate und Signalsuchintervall ändern. Größere Suchintervalle können kurze Signale übersehen. Ein numerischer Vergleich mit acht Kanälen bei 5 MS/s ergab beim selektiven Pfad RTF 4,09 → 0,83; das ist ein Softwarebenchmark, keine Empfangszusage für den Zielrechner. [Messung und Grenzen](docs/aufnahme.md#hohe-sample-raten-warteschlange-und-diagnose).

**Wasserfall:** Viridis, Cividis oder Inferno mit beschrifteter dBFS-Skala, einstellbarem Pegelbereich und 60/180/600-s-Zeitfenster. Detektionsrahmen zeigen geschätzte Bandbreite, Mittenfrequenz und die für den betreffenden Kanal berechnete Modulation. Andere Detektionen bleiben „unbekannt“. Zwischen nicht beobachteten Zeitpunkten werden keine kontinuierlich empfangenen Signale erfunden. Host-Empfangszeit ist kein kalibrierter RF-Zeitstempel.

## Laptop → Workstation → Laptop

Auf dem Laptop aufzeichnen und Labels bestätigen. Die Aufnahme als SigMF-ZIP exportieren; auf der Workstation Metadaten und IQ-Datei aus dem ZIP gemeinsam importieren. Labels und ursprüngliche Aufnahmegruppen bleiben in den Metadaten erhalten. Dort trainieren, das **Modellpaket** exportieren, auf dem Laptop importieren und aktivieren. Das Paket enthält Topologie, Gewichte, Trainingsnormalisierung, Klassenreihenfolge, Vorverarbeitung und Bericht. CUDA-trainierte Gewichte werden CPU-portabel gespeichert.

Auch importierte Modelle können im Tab **Modelle** für einen Vergleich von bis zu drei Live-Stichproben ausgewählt werden. Der gespeicherte Score und dessen Hardwareprofil gehören zum ursprünglichen Bewertungsrechner; neue Live-Laufzeiten werden auf dem aktuellen Laptop gemessen.

Der Austausch ist dateibasiert. Es gibt keinen automatisch synchronisierenden Dienst und keinen Remote-GPU-Scheduler. Die komplette Oberfläche kann auf beiden Rechnern lokal laufen.

## Modelle, Experimente und Erklärung

Der Topologiekatalog enthält **34 Vorschläge** aus zehn Familien: Feature-LDA, Feature-MLP, IQ-CNN, Spektrogramm-CNN, 5-CNN, FB-LSTM, GRU, ResNet, TCN und Patch-Transformer. Breite, Tiefe und gegebenenfalls Kernel/Heads sind als validierte JSON-Konfiguration speicherbar. Vorschläge sind vor dem Experiment keine empirische Bestenliste. Die FB-LSTM-/2D-CNN-/5-CNN-Varianten sind anhand der Dissertation dokumentierte **HF-Adaptionen**, keine behaupteten identischen Nachbauten.

Experimentparameter umfassen Lernrate, Batchgröße, Epochen, Segmentlänge, SNR und Timing-Jitter in **RMS-Samples**. Mehrere Segmentlängen, Lernraten und Batchgrößen bilden eine begrenzte Versuchsreihe. Optional variieren zufällige Kanaleffekte innerhalb dokumentierter Grenzen: Frequenzversatz, Sampletaktfehler, IQ-Amplituden-/Phasenfehler, DC-Offset, Clipping und ein verzögertes Echo. Eigene bestätigte Aufnahmen können 2–64 frei benannte Klassen enthalten; die synthetische Quelle bleibt AM/FM. Train/Validation/Test werden nach Ursprungsgruppen getrennt, Normalisierung wird nur auf Training angepasst; die Auswahl erfolgt über Validierung. SNR bei realen Aufnahmen bezeichnet zusätzlich injiziertes Rauschen relativ zur vorhandenen Gesamtleistung, keinen gemessenen ursprünglichen RF-SNR.

Ergebnisse enthalten Konfusionsmatrix, Accuracy/Balanced Accuracy/Macro-F1, Recall, Brier/ECE, Trainings- und Vorverarbeitungszeit, p50/p95-Inferenzlatenz, Echtzeitfaktor, Modellgröße, Parameterzahl und SNR-Kurve. Die Laufzeit relativ zu einer festen Referenzlast unterstützt Gerätevergleiche, ist aber keine exakte Hardwareprognose. Fehlalarmrate, Open-Set-Güte und kalibrierte Empfindlichkeit in dBm benötigen eigene Negativ-/Kalibrierdatensätze; fehlende Messwerte bleiben offen.

Der **100-Punkte-Score** gewichtet Validierungs-Balanced-Accuracy mit 45, Robustheit im Validierungs-SNR-/Jitter-Raster mit 35, Echtzeitreserve mit 15 und Trainingszeit mit 5 Punkten. Qualitätswerte werden um das Zufallstrefferniveau der Klassen bereinigt. Robustheit kombiniert 70 % Mittelwert und 30 % schlechtesten Rasterwert. Geschwindigkeit erhält ab **5-facher Echtzeit** die vollen Punkte; darüber steigt dieser Anteil nicht weiter. Maßgeblich ist der gemessene CPU-p95-Echtzeitfaktor, passend zum aktuellen Live-Inferenzpfad. Der Trainingsanteil ist `5 / (1 + Trainingssekunden / 300)`. Für die Top-3-Live-Empfehlung muss die CPU-p95-Verarbeitung außerdem innerhalb der Signaldauer liegen. Die separate Genauigkeitsrangliste und der Score verwenden keine abschließenden Testwerte. Fehlende Bewertung erzeugt keinen erfundenen Score. [Formeln, Messbedingungen und Grenzen](docs/topologien.md).

**Automatische Hyperparameteroptimierung:** Im Experiment-Setup einschalten, Ziel und Suchraum wählen. Eine reproduzierbar vorgeschlagene Zufallssuche testet Segmentlänge, logarithmisch verteilte Lernrate, Batchgröße und Epochenbudget; Feature-LDA variiert nur die wirksame Segmentlänge. Standardziel ist der Gesamtscore, alternativ Validierungs-Macro-F1 oder Balanced Accuracy. Standardbudget: zwölf Versuche und 300 s Suchzeit **je Topologie**. Zeitgrenzen werden an Fortschrittspunkten geprüft; eine laufende native Rechenoperation kann sie überschreiten. Nur vollständig bewertete Versuche können gewinnen. Testdaten werden während der Suche nicht gelesen; erst das festgelegte Gewinnermodell erhält den abschließenden Test. Suchraum, alle Versuche, Fehler, Abbruchgrund, beste Einstellungen und gesamter Suchaufwand werden gespeichert. Die besten Werte lassen sich ohne automatischen Trainingsstart ins Setup übernehmen. Ohne unabhängige Validierung bleibt automatische Optimierung gesperrt. [Suchverfahren und Messbedingungen](docs/hyperparameteroptimierung.md).

**CPU und GPU parallel nutzen:** Unter **Ressourcen / Parallelisierung** stehen Auto, seriell und parallel zur Wahl. Mehrere Topologien laufen als unabhängige Prozesse; bei einer einzelnen Topologie werden automatische Suchversuche parallelisiert. Beide Ebenen erzeugen keine verschachtelten Trainingspools. Persistente Spawn-Worker laden Bibliotheken einmal pro Slot und setzen Seed, Threadbudget und Jobzustand je Versuch neu. Auto begrenzt Worker anhand verfügbarer CPU, RAM und VRAM, einschließlich lesbarer Affinitäts-/cgroup-Grenzen. GPU-, Gesamtworker- und Threadgrenzen können angepasst werden; Schätzbudgets ersetzen keine Peakmessung. CPU-p95- und Referenzmessungen pausieren andere Experimentworker. Externe Anwendungen und Liveverarbeitung werden damit nicht als ausgeschlossen behauptet.

Die CUDA-Trainingsschleife sammelt Verlustwerte auf dem Gerät und überträgt sie einmal je Epoche, hält Validierungstensoren vor und validiert in begrenzten Batches. Phasenzeiten unterscheiden Vorverarbeitung, Übertragung, Training, Validierung, Checkpoints und Benchmarkpausen. Die Oberfläche zeigt aktive Worker, tatsächliches Trainingsgerät und Batches sowie gemessene CPU-Auslastung einschließlich Kindprozessen und NVIDIA-Geräteauslastung/VRAM. Fehlende oder veraltete WSL-/NVIDIA-Messwerte bleiben unbekannt. Sehr kleine Netze und lange CPU-Prüfraster können weiterhin niedrige GPU-Mittelwerte erzeugen; entscheidend sind Durchsatz und gemessene Phasen. Mehr Vorverarbeitungsthreads waren im kleinen Softwarebenchmark überwiegend langsamer, deshalb verwendet Auto dort einen Thread pro Worker. [Messdaten und Einordnung](docs/performance-baseline.md), [Ausführungssteuerung](docs/parallelisierung.md).

**Berichtsdaten für ChatGPT/PDF:** Eine optionale Versuchsbeschreibung hält Ziel, Hypothese, Messaufbau und Beobachtungen fest. **Berichtsdaten exportieren** lädt ein ZIP mit vollständigem Ergebnis-JSON, deutscher Markdown-Auswertung, CSV-Tabellen, SVG-Diagrammen, Ereignisjournal, Hardware-/Softwareprofil, Gruppenherkunft, eingefrorenen Labels und Prüfsummen. Das ZIP kann später zusammen mit den Berichtsvorgaben in ChatGPT hochgeladen werden, um daraus einen PDF-Bericht zu erstellen; die Anwendung erzeugt hier noch kein PDF. Auch abgebrochene/fehlgeschlagene Läufe sind exportierbar. Spätere Labeländerungen oder Löschungen schreiben die dokumentierten Eingaben nicht um. Roh-IQ und Modellgewichte werden separat archiviert; ihre tatsächlich verfügbaren Hashes werden mit eindeutigem Geltungsbereich beschrieben. Alte Läufe erhalten keine rückwirkend erfundene Herkunft. [Inhalt und Verwendung des Berichtspakets](docs/berichtsexport.md). Die bestätigte [Berichtsvorlage](docs/berichtsvorlage.md) und [Gliederung als JSON](docs/berichtsgliederung.json) dienen als Standard für folgende wissenschaftliche Berichte.

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
node tests/test_frontend.js
bash -n start.sh setup.sh install_driver.sh
```

Die Suite benötigt keine SDR-Hardware; UDP-Tests verwenden nur Loopback. Neuronale Tests laufen zusätzlich, wenn PyTorch installiert ist. GitHub Actions prüft den Kern unter Python 3.11/3.12 und einen CPU-PyTorch-Pfad. Die API-Dokumentation ist lokal unter `/docs` verfügbar. Prüfstand und bekannte Grenzen: [Validierung](docs/validierung.md).

Leaflet wird lokal mit BSD-Lizenz ausgeliefert, siehe `signalclassifier/static/vendor/leaflet/LICENSE`. Die Anwendung lädt weder Modelle aus Python-Pickles noch fremde ausführbare Netzdefinitionen. Modellpakete enthalten validierte Konfiguration und numerische Gewichte; PyTorch-Laden verwendet `weights_only=True`.
