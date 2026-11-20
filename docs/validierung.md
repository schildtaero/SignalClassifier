# Softwareprüfung und Abnahmegrenzen

Prüfstand: 20. November 2026, SignalClassifier 0.1.0, Linux x86_64, Python 3.12.14, NumPy 2.3.5, SciPy 1.17.0, PyTorch 2.15.0+cpu. Kein physischer SDR und keine CUDA-GPU in der Entwicklungsumgebung.

## Ausgeführt

- Gesamtsuite: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python -m unittest discover -s tests -v`.
- 33 Softwaretests bestanden, einschließlich optionaler neuronaler CPU-Tests. Python-Wheel gebaut und auf enthaltene lokale Oberfläche und Leaflet-Dateien geprüft.
- SigMF cf32-/ci16-Roundtrip, Daten-/Indexvalidierung, begrenzte Reads, Replay bis EOF, sichtbarer Treiberfehler ohne Simulation, Stock-Pluto-Grenzen.
- Energieselektive Kanalaufnahme mit echten numerischen IQ-Daten, Frequenz-/Sampleherkunft, kontinuierlicher FIR-/Dezimationsphase und gemeinsamer Speichergrenze.
- Begrenzter Inferenzpuffer über mehrere dezimierte Blöcke mit Rücksetzung bei Kanal-/Frequenz-/Ratenwechsel; atomarer Zugriff auf IQ und zugehörige RF-Parameter.
- AM-/FM-Softwarefixtures durch die Empfangs-/Kanalstrecke und Klassifikationsfunktion; das ist kein echter HF-Versuch.
- API-Kette Import → bestätigtes Label → SigMF-Export/erneuter Import mit erhaltenem Gruppenbezug.
- API-Kette synthetisches Experiment → Bericht → Modellpaket → erneuter Import → Aktivierung → Klassifikation einer neuen Aufnahme.
- Unabhängige Aufnahmegruppen auch bei mehreren Klassen aus derselben physischen Session, dynamische Klassenausgänge und gespeicherte Trainingsnormalisierung.
- Alle neuronalen Modellfamilien mit dreiklassigem Forward-Pass; ein kleiner FB-LSTM-Trainings-/Checkpoint-/Inferenzlauf mit lokaler Attribution. Zusätzlicher Entwicklersmoke führte die neun neuronalen Familien jeweils mit einem kurzen CPU-Training und erneutem Laden aus. Ein solcher Smoke beweist Funktion, keine brauchbare Klassifikationsgüte.
- OpenDroneID-Decoder anhand unabhängig erzeugter Upstream-C-Encoder-Testvektoren, getrennte Höhen-/Positionsreferenzen, Feldalter und Loopback-UDP-Lebenszyklus.
- JavaScript-Syntax, Shell-Syntax, eindeutige HTML-IDs und Abgleich statischer JavaScript-ID-Referenzen mit dem HTML.

Die Tests messen echte Rechenpfade auf generierten beziehungsweise gespeicherten Test-IQ-Daten. Hohe AM/FM-Genauigkeit auf diesen Daten wird nicht auf reales Flugfunk-/UKW-Material übertragen.

## Hier nicht verifiziert

- Reeller Empfang, Gain-Skalierung und langfristiger Durchsatz von Pluto/RSP1A sowie USB-/Netzwerktransport und Treiberversionen.
- Installation auf einem echten Windows-/WSL2-System, USB-Durchreichung und WSL-CUDA.
- Training auf der genannten Thinkstation-GPU; CPU-PyTorch wurde getestet. Hardwareerkennung verwendet tatsächliche Gerätedaten statt einer Annahme zur GPU-Bezeichnung.
- Visuelle Browser-/Viewport-Prüfung: Die verwaltete Vorschau konnte in dieser Umgebung nicht an `127.0.0.1:4173` binden. Die Startdiagnose und `sites-preview status` bestätigten eine gestoppte Vorschau. Es wurde keine alternative Browserinfrastruktur eingesetzt. JavaScript-/HTML-Prüfungen ersetzen diesen visuellen Test nicht.
- Echtes OpenDroneID über Bluetooth/WLAN, DJI-/proprietäre HF-Decoder oder Videolinks: siehe [konkreter Decoderumfang](drohnen.md).
- Reale unbekannte Gegenklassen, absolute Fehlalarmrate und kalibrierte HF-Empfindlichkeit in dBm. Fehlende Werte werden nicht mit synthetischen Erfolgszahlen gefüllt.

## Lokaler Abnahmeversuch

Auf beiden Rechnern Installation und Browserdarstellung prüfen. Auf dem Laptop zunächst RSP1A verbinden, unterstützte Rate zurücklesen und eine kurze UKW- sowie AM-Flugfunkaufnahme mit genügend Signalbandbreite speichern. Je Klasse mindestens drei unabhängige Sessions aufnehmen, signalhaltige Clips bestätigen, auf der Workstation importieren und den Feature-LDA-/kleinen CNN-Vergleich starten. Unterschiedliche Sessions und später ganze Empfänger/Frequenzbereiche zurückhalten. Modellpaket auf dem Laptop erneut laden und dort echte Latenz, CPU-/RAM-Bedarf, Fehlentscheidungen und Ablehnungen messen. Für Drohnentracks einen passenden externen Remote-ID-Scanner anbinden und die gemeldeten Felder samt Referenzen mit dem eigenen Testgerät vergleichen.

## Installationshelfer 0.1.1

Der neue `install_driver.sh` installiert standardmäßig die Host-Komponenten für PlutoSDR und RSP1A. Die Gesamtsuite besteht jetzt **52 Tests**; davon prüfen 19 den Installer und seine Bibliotheksprüfungen mit simulierten System-/Netzwerkbefehlen und Bibliotheken. Die vollständige Suite sowie Bash- und JavaScript-Syntaxprüfung bestanden. Vorschau und Empfängerauswahl wurden ebenfalls ausgeführt.

Geprüft sind unter anderem getrennte Pluto-/RSP-Pfade, keine Änderungen bei Vorschau/Prüfmodus, HTML-/Prüfsummenablehnung, ursprüngliche API-Aufrufreihenfolge, Erhalt beziehungsweise Sicherung vorhandener Pythonumgebungen sowie Pluto-USB-Suche ohne TX-Initialisierung. Soapy-Quellstand und Pluto-USB-Regeln sind festgelegt; letztere sind mit SHA-256 geprüft. Native Pakete, libiio und Hersteller-API wurden hier **nicht administrativ installiert**. Der SDRplay-Download lieferte aus dieser Umgebung HTTP 403; das Skript bietet deshalb eine lokale Installerdatei als Alternative. Ein Test des realen Installationslaufs auf dem Zielrechner bleibt erforderlich. [Aufruf und Grenzen](install-driver.md).
