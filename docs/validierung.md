# Softwareprüfung und Abnahmegrenzen

Prüfstand: 21. November 2026, SignalClassifier 0.4.1, Linux x86_64, Python 3.12.14, NumPy 2.3.5, SciPy 1.17.0, PyTorch 2.15.0+cpu. Kein physischer SDR und keine CUDA-GPU in der Entwicklungsumgebung.

## Ausgeführt

- Gesamtsuite: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python -m unittest discover -s tests -v`.
- 153 Python-Softwaretests bestanden, einschließlich optionaler neuronaler CPU-Tests; zusätzliche JavaScript-Logiktests prüfen Wasserfall, Vergleichsansicht, Sammelauswahl, Bereinigung, Optimierung, Ressourcensteuerung und Telemetrie. Der Wheel-/Asset-Prüfstand 0.4.0 bleibt als frühere Prüfung dokumentiert.
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

## Ergänzungen in 0.4.1

- Konkrete Fehlergruppen, Statuszahlen, Suchbudget und Teilberichtpfad bleiben bei einer Suche ohne validierten Abschluss in `failure_summary` und der Fehlermeldung erhalten. Hinweise zum Erhöhen des Budgets erscheinen nur bei tatsächlichem Zeitabbruch. Die letzte beobachtete Trialphase und Meldung werden gespeichert.
- Vier neue Regressionstests prüfen vollständig fehlgeschlagene Berichtsübergaben, erneutes Einlesen eines zunächst nicht erreichbaren Completion-Berichts, Abbruch ohne vollständigen Trial und den Nested-Fall mit gemeinsamer PID: zwölf rohe Timeoutstatus werden korrekt als ein begonnener Abbruch und elf nicht gestartete Vorschläge erklärt. Bestehende Originalfehler-/Abbruchtests wurden erweitert.
- Ein gültiger Abschlussbericht kann nach einem vorübergehenden Lesefehler erneut aus dem endgültigen Workerresultat übernommen werden. Endgültig defekte Berichte sind fehlgeschlagene Trials; sie erzeugen keinen Gewinner. Der Finaltest wird weiterhin genau einmal nach der Gewinnerwahl ausgeführt.
- Gesamtsuite: 153 Tests bestanden (einschließlich CPU-PyTorch, echter Spawn-Worker und API-Pfade); für die API-/Loopback-Tests wurde in der verwalteten Entwicklungsumgebung Netzwerkzugriff freigegeben. JavaScript-Logik-/Syntaxprüfungen, Shell-Syntax und `git diff --check` bestanden. Die HPO-Zeitbudgetsemantik wurde nicht geändert; ein zu knappes Budget bleibt ein legitimer Abbruchgrund.

## Ergänzungen in 0.4.0

- 15 Scheduler-Tests mit echten Spawn-Prozessen: begrenzte Parallelität, geordnete Resultate, sechs Jobs auf zwei wiederverwendeten PIDs, jobweise zurückgesetzte Zufalls-/Abbruchzustände, keine verschachtelten Trainingspools und Threadlimits bereits vor dem Child-Mainimport.
- Mehrere CPU-/neuronale Topologien mit zwei gleichzeitig aktiven Slots, parallele Einzel-HPO-Trials sowie Mehrtopologien-HPO geprüft. Testabschottung, ausschließlich auf Validierung basierende Auswahl und einmaliger Finaltest bleiben bestehen.
- CPU-Benchmark-Sperre pausiert verwaltete Rechenworker; Crash und Prozessende geben Sperren frei. Abbruch, unkooperative Deadlines und Fail-fast räumen zugehörige Prozesse auf. Dynamisch gemessene Gatepausen fließen nicht in den aktiven Trainingszeitanteil des Scores ein.
- Multibatch-Training mit einmaliger Loss-Übernahme je Epoche, wiederverwendetem Validierungstensor, Minibatch-Validierung und respektiertem Worker-Threadbudget geprüft. CUDA-Ereignis-/Checkpointpfade sind implementiert, wurden hier aber nicht auf physischer GPU ausgeführt.
- Zwei zusätzliche echte MLP-Läufe im selben persistenten PID erzeugten bei gleichem Seed identische Gewichtetensoren und Validierungswerte. Bibliotheksstartup war einmalig; Lazy-Optimizeraufbau konnte weiterhin im ersten Job liegen. Das ist ein CPU-Funktionstest, kein GPU-Durchsatznachweis.
- Fünf Telemetrie-Tests prüfen CPU-Prozessbaum, Quotennormalisierung, NVIDIA-CSV, echte Nullwerte, unbekannte WSL-Werte, Cache, Alter und Messzusammenfassung. Berichtsexport ergänzt Ressourcenbeobachtungen und Phasenzeiten ohne Nullauffüllung; API-Tests prüfen Ausführungsplan und Export.
- CPU-Ausgangsprofil und deterministische Vorverarbeitungs-Threadmatrix sind dokumentiert: zusätzliche Threads waren für die untersuchten Größen überwiegend langsamer. Auto verwendet deshalb einen Vorverarbeitungsthread je Prozess. [Messwerte](performance-baseline.md), [Ausführung und Grenzen](parallelisierung.md).
- Verwaltete Vorschau erneut am lokalen Port-Binding gescheitert; visuelle Browserabnahme bleibt offen. Reale CUDA-Auslastung, Workstation-/Laptopbeschleunigung und gleichzeitiger physischer SDR-Betrieb sind hier nicht gemessen.

## Ergänzungen in 0.3.0

- Abhängigkeitsfreie, begrenzte Random Search mit Seed, logarithmischer Lernratensuche, eindeutigen wirksamen Konfigurationen und gleichbleibendem Validierungsgitter. Feature-LDA und ein neuronales Netz laufen gemeinsam durch echte automatische Optimierung, erneutes Laden und API-Modellpersistierung.
- Tests schirmen synthetische Testquellen und reale Test-IQ-Dateien während der Suche ab; erst der festgelegte Gewinner erhält einen abschließenden Test. Gemeinsame Referenzsignale beziehungsweise Quellenanker für unterschiedliche Fensterlängen sind numerisch geprüft. Auswahlziel, Timeout, Benutzerabbruch und vollständig fehlgeschlagene Suche bleiben unterscheidbar.
- Inkrementelle atomische Optimierungsteilberichte und strukturierte Epochen-/Versuchsereignisse. Bei einem unvollständigen Journalende wird ein neuer Neustart-/Unterbrechungseintrag nicht verschluckt.
- Eingabekonfiguration, Aufnahmegruppen, bestätigte Labels, Metadaten, Hardware-/Softwareprofil und Quellprüfsummen werden vor der Queue eingefroren. Nachträgliches Umlabeln und Löschen der Quelldateien ändern das exportierte Inventar nicht. Nicht endliche Werte und ausgelassene optionale Metadaten werden ausdrücklich diagnostiziert.
- API-Kette automatische Optimierung → Modellartefakte mit SHA-256 → Berichtspaket. Auch fehlgeschlagene, abgebrochene und ältere Läufe sind exportierbar; historische Herkunft wird nicht nachträglich erfunden. JSON/CSV-Vollständigkeit, Epochen fehlgeschlagener Trials, SVG-XML, Manifestprüfsummen und atomarer ZIP-Abschluss sind geprüft.
- Visuelle Vorschau erneut versucht; der verwaltete Start scheiterte weiterhin am lokalen Port-Binding. CUDA und echte Empfänger bleiben außerhalb dieser Softwaretests.

## Ergänzungen in 0.2.1

- Sammelauswahl bestätigter Aufnahmen und verfügbarer Topologien im Experiment-Setup, Aufheben der Auswahl, Erhalt in Entwürfen und erneute Vorprüfung. Nicht verfügbare neuronale Ansätze bleiben gesperrt.
- Einheitliches Limit von 1000 Aufnahmen in API, Vorprüfung und Loader. Ein LDA-Versuch verarbeitet 130 reale numerische SigMF-Testdateien vollständig mit erhaltener Gruppenherkunft; 1001 Dateien werden vor Zugriff abgewiesen. 128-MiB-IQ-Budget und 4096 Segmente je Split bleiben bestehen und werden vor dem Start geprüft.
- Sieben API-Tests zur Bereinigung kurzer Aufnahmen: strikt weniger als 0,5 s, beide IQ-Datentypen, Dateipaare samt Labels, explizite Vorschau-IDs, keine nachträgliche Aufnahme neuer Dateien, erneute Dauer-/Änderungsprüfung sowie Schutz aktiver Empfänger und Experimentdaten. Genau 0,5 s bleibt erhalten.
- Simulierte Datei-/Datenbankfehler vor dem Commit stellen die betroffenen Paare wieder her. Ein simulierter Unlink-Fehler nach dem Commit liefert eine eigene Warnung und zählt nur tatsächlich entfernte Dateibytes. Ein Prozessabbruch während einer mehrteiligen Dateioperation wird damit nicht als vollständig crash-atomar ausgewiesen.
- Visuelle Vorschau erneut versucht; der verwaltete Start scheiterte weiterhin am lokalen Port-Binding. Die Browserabnahme bleibt offen.

## Ergänzungen in 0.2.0

- Batchvorschläge für volle RF-Fenster und Mehrdeutigkeit; menschliche Bestätigung, Schutz vorhandener Labels, Vorschauabgleich und Rückabwicklung bei simuliertem Datenbank-/Datenträgerfehler. Gruppenherkunft bleibt erhalten.
- Vorprüfung der unabhängigen Gruppen vor dem Start und ausdrücklich unbewertetes Training mit nur einer Gruppe je Klasse. Ohne Validierung keine Genauigkeit oder Qualitätsrangfolge.
- LDA und zwei ANNs im selben Experiment, gemeinsame Datenfingerabdrücke/Gruppen, alle Modelle erneut ladbar. Genauigkeitsrangliste und Score wählen anhand Validierung; absichtlich vertauschte Test-/Validierungswerte prüfen den Schutz vor Testauswahl.
- Score 45/35/15/5, Echtzeitpunkte bei 5× begrenzt, CPU-p95-Eignung und maximal drei Empfehlungen. CUDA-Training und CPU-Live-Inferenz sind getrennte Messpfade.
- API-Live-Vergleich mehrerer Modelle auf demselben aktuellen IQ-Ring ohne automatischen Empfängerstart; Warmup und verworfene/zu kurze Inferenzfenster erzeugen keine fingierten Laufzeitserien.
- Bounded Hardwarequeue, geliehene Treiberpuffer, simulierte Überläufe und Schreibfehler, zusammenhängender gespeicherter Präfix sowie schnelle Dezimierung gegen vollständige FIR-Referenz. [Numerischer 5-MS/s-Vergleich](aufnahme.md#reproduzierbarer-softwarebenchmark).
- Wasserfall-Leistungsaggregation, Palette, Frequenz-/Zeitgrenzen, Zuordnung eines Klassifikationsergebnisses zu genau einem Kanal, deduplizierte Zeitstempel und sichtbar fehlende Beobachtungsintervalle mit `node tests/test_frontend.js` geprüft. Auch Dateibudgetabschätzung, Validierungsranking und Live-Warmup sind abgedeckt. Eine visuelle Browserabnahme ersetzt dies nicht.

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

Mit Version 0.1.1 kam `install_driver.sh` für die Host-Komponenten von PlutoSDR und RSP1A hinzu. Die damalige Gesamtsuite umfasste **52 Tests**; davon prüften 19 den Installer und seine Bibliotheksprüfungen mit simulierten System-/Netzwerkbefehlen und Bibliotheken. Die vollständige Suite sowie Bash- und JavaScript-Syntaxprüfung bestanden. Installationsvorschau und Empfängerauswahl wurden ebenfalls ausgeführt.

Geprüft sind unter anderem getrennte Pluto-/RSP-Pfade, keine Änderungen bei Vorschau/Prüfmodus, HTML-/Prüfsummenablehnung, ursprüngliche API-Aufrufreihenfolge, Erhalt beziehungsweise Sicherung vorhandener Pythonumgebungen sowie Pluto-USB-Suche ohne TX-Initialisierung. Soapy-Quellstand und Pluto-USB-Regeln sind festgelegt; letztere sind mit SHA-256 geprüft. Native Pakete, libiio und Hersteller-API wurden hier **nicht administrativ installiert**. Der SDRplay-Download lieferte aus dieser Umgebung HTTP 403; das Skript bietet deshalb eine lokale Installerdatei als Alternative. Ein Test des realen Installationslaufs auf dem Zielrechner bleibt erforderlich. [Aufruf und Grenzen](install-driver.md).
