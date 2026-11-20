# Klassifikatoren, Topologien und Experimente

Der Katalog enthält **34 trainierbare Architekturvorschläge**. Das ist eine begründete Auswahl unterschiedlicher Kosten-/Darstellungsansätze, keine vorgetäuschte empirische Bestenliste. Erst unabhängige Messungen mit den eigenen Empfängern erlauben eine Rangfolge. Feature-LDA ist ohne PyTorch verfügbar; alle anderen Familien benötigen die optionale neuronale Installation (`./setup.sh --neural-cpu` oder `--neural`). Ein CUDA-fähiger Rechner kann dieselben Modelle trainieren; die Gewichte werden portabel auf CPU gespeichert und auf dem Laptop geladen.

## Übernahme aus der Dissertation

Primärquelle: [Philipp Schildt, Condition Monitoring for Electric Aircraft Propulsion Systems, DOI 10.25439/rmt.33772282](https://doi.org/10.25439/rmt.33772282), Kapitel 6.1.1–6.1.3, S. 134–140 und Anhang B.4.3–B.4.5, S. 267–268. Die Originalanwendung betrifft Audio und Remaining-Useful-Life-Regression. Die hier implementierten Netze sind **RF-Adaptionen**, keine behaupteten Reproduktionen sämtlicher Originalparameter. Die Thesis beschreibt nicht alle numerischen Layergrößen eindeutig.

- **FB-LSTM:** Die Thesis verwendet 39 Hamming-Fenster mit je 128 Samples aus 0,5 s Audio bei 10 kHz, zehn Spektralmerkmale, Z-Normierung, zwei LSTM-Stufen mit Dropout 0,1 und einen Regressionskopf. Die RF-Adaption bereinigt den groben Frequenzversatz, resampelt IQ auf 2048 Punkte und berechnet 16 × 128-Sample-Fenster. Merkmale: Centroid, Crest, Decrease, Entropy, Flatness, Kurtosis, Rolloff, Skewness, Slope und Spread. Normierung ausschließlich aus Trainingsdaten; LSTM mit Klassifikationskopf. Das Verfahren ist für komplexe RF-Spektren angepasst und entspricht nicht exakt den MATLAB-Featuredefinitionen.
- **5-CNN:** Fünf kausale 1D-Faltungen mit Batch-Normierung und ReLU. Zwei IQ-Eingangskanäle, zusätzliche Average-Pooling-Stufen zur Begrenzung der CPU-Kosten, Dropout 0,5 und Global Average Pooling. Ausgabekopf für Klassen statt RUL. Kernel-/Filtergrößen sind eigene Vorschläge.
- **2D-CNN:** Ein lineares komplexes RF-Log-Leistungsspektrogramm ersetzt das psychoakustische Bark-Spektrogramm der Thesis. Varianten mit fünf Conv2D-/BN-/ReLU-Blöcken orientieren sich am Originalaufbau; die Small-Variante hat drei Blöcke. Pooling nach den ersten drei und der fünften Stufe, Global Average Pooling, Dropout 0,2 und Klassifikationskopf.

## Bewertung und Auswahl der Familien

| Familie | Varianten | Erwarteter Nutzen | Hauptkosten / Grenzen |
| --- | --- | --- | --- |
| Feature-LDA | `feature-lda` | Sehr schnelle, additive und nachvollziehbare Referenz | Lineare Grenze; Verteilungswechsel und Featureauswahl |
| Feature-MLP | 16, 32, 64, 128 | Kleine nichtlineare Kombination expliziter Merkmale | Keine Information außerhalb der Merkmale |
| IQ-CNN | 16, 32, 48, 64, 96 | Lokale RF-/Symbolmuster direkt aus IQ | Domainwechsel; Resampling auf 1024 Punkte |
| Spektrogramm-2D-CNN | Small 8, 16, 32, 48 | Zeit-/Frequenzmuster und Bursts | STFT-Aufwand; keine absolute IQ-Phase |
| 5-CNN | 16, 32, 48, 64 | Tieferer Vergleich zum Promotionsansatz | Rechenaufwand / Datenmenge |
| FB-LSTM | 16, 32, 64, 96 | Zeitverlauf von Spektralmerkmalen | Featureberechnung und sequenzielle Verarbeitung |
| Feature-GRU | 24, 48, 96 | Vergleich mit kleinerem rekurrentem Zelltyp | Begrenzte Fensterhistorie |
| IQ-ResNet | 16, 32, 64 | Tiefere Faltungsnetze mit Residualpfaden | Mehr Parameter / Trainingsdaten |
| IQ-TCN | 16, 32, 64 | Größeres zeitliches Sichtfeld ohne Rekurrenz | Festes resampeltes Fenster |
| IQ-Patch-Transformer | 32, 64, 96 | Globale Beziehungen zwischen 32 IQ-Patches | Für AM/FM vermutlich unnötig hoher Aufwand |

Die Zahlen bezeichnen die Anfangsbreite bzw. Hidden Size. Tiefe und Kernel sind im exportierten Topologie-JSON festgehalten. Eigene Varianten können innerhalb begrenzter Breite, Tiefe und Kernelgröße gespeichert werden. Der Ausgabekopf wird beim Training an die tatsächlich bestätigten Klassen angepasst. Alle Modelle brauchen getrennte Ergebnisse für Laptop und Workstation; GPU-Nutzung garantiert keine Beschleunigung kleiner Netze.

## Erste Versuche

1. Ohne Hardware einen ausdrücklich synthetischen AM/FM-Versuch mit `feature-lda` durchführen. Dies prüft Software, Speichern/Laden und Auswertung, nicht die RF-Leistung.
2. Für das reale Experiment mindestens drei unabhängige Aufnahmesitzungen pro Klasse aufnehmen und **manuell bestätigen**. Beim ersten Versuch AM-Flugfunk und UKW-FM getrennt aufnehmen und jeweils nur das isolierte gewünschte Signal verwenden. Bandplanvorschläge gelten nicht als bestätigte Labels.
3. Training, Validierung und Test werden anhand **einer globalen Zuordnung physischer Aufnahmegruppen** getrennt. Ausschnitte desselben Ursprungs bleiben zusammen, auch wenn sie unterschiedliche Klassen enthalten. Aufnahme-IDs und Gruppen sind im Report dokumentiert.
4. Feature-LDA gegen Feature-MLP und kleine IQ-/2D-CNNs vergleichen. Anschließend FB-LSTM und 5-CNN hinzufügen. Umfangreichere Netze sind nur dann sinnvoll, wenn sie auf unabhängigen Aufnahmen messbar helfen.
5. Für Frequenzunabhängigkeit dieselben Modulationen auf weiteren Frequenzen, mit verschiedenen Nutzsignalen, Antennen, Pegeln und Empfängern prüfen. Weder RF-Mittenfrequenz noch Bandplanlabel fließen als Modellmerkmal ein. Optionales Vorwissen erscheint als separat gekennzeichneter Hinweis.

Synthetische Modulationen umfassen konventionelles AM mit Träger und FM mit wechselnden Nutzsignalformen, Modulationstiefen, Frequenzhüben und Frequenzversätzen. Bestätigte reale Aufnahmen erlauben **2–64 beliebige Klassen**, etwa AM, FM, POCSAG und LoRa. Für zusätzliche Standards werden keine vermeintlich realistischen Daten erfunden. Training mit weiteren Labels ist kein Protokolldecoder-Nachweis.

## Sweep und Kanalvariationen

`segment_length` ist eine Sampleanzahl, kein Zeitwert; die tatsächliche Dauer ist `Samples / tatsächliche Abtastrate`. `segment_lengths`, `learning_rates` und `batch_sizes` können zusätzliche Kombinationen bilden; maximal zwölf Kombinationen pro Experiment. Die Modellwahl erfolgt anhand des Validierungs-Macro-F1, bei Gleichstand anhand der Modellgröße, niemals anhand des besten Testergebnisses.

`snr_db` und `jitter` bilden ein vollständiges Testgitter. Im Training wird pro Segment zufällig ein Wert aus den jeweiligen Listen gezogen; standardmäßig kommen zusätzlich die unveränderten Trainingssegmente hinzu (`include_clean=true`). Timing-Jitter wird als RMS in **Sampleeinheiten** mit linearer IQ-Interpolation injiziert. Die zufälligen Werte und Seeds sind reproduzierbar gespeichert.

Bei realen Aufnahmen ist der ursprüngliche RF-SNR unbekannt: Die SNR-Angabe bedeutet **zusätzlich injizierte Rauschleistung relativ zur gesamten aufgenommenen Leistung**. Sie ist keine kalibrierte Messung des absoluten Signal-/Rauschverhältnisses.

Optionales JSON-Feld `augmentation` unterstützt folgende Störungen. Werte sind zufällige symmetrische Betragsgrenzen, außer den explizit festen Parametern. Die Oberfläche bietet dafür erweiterte Eingabefelder; dieselben Werte sind über die Experiment-API verfügbar.

| Schlüssel | Einheit / Bedeutung | Zulässiger Bereich |
| --- | --- | --- |
| `frequency_offset_hz` | Gleichverteilter zusätzlicher Frequenzversatz ±Hz | 0–1.000.000 |
| `sample_clock_ppm` | Gleichverteilter Abtaktfehler ±ppm | 0–1000 |
| `iq_gain_db` | Q-Kanal-Gainfehler ±dB | 0–12 |
| `iq_phase_deg` | IQ-Quadraturfehler ±Grad | 0–45 |
| `dc_offset` | Fester komplexer DC-Betrag mit Zufallsphase | 0–1 |
| `clip_level` | Feste Begrenzung je I-/Q-Komponente; 0 deaktiviert | 0–10 |
| `echo_amplitude` | Ein verzögertes Echo, fester Betrag und Zufallsphase | 0–0,95 |
| `echo_delay_samples` | Feste Verzögerung des Echos; mindestens ein Sample | 0–128 |

Diese optionalen Effekte werden in Training und Test entsprechend derselben konfigurierten Verteilung angewendet. Separate Robustheitskurven entstehen in diesem Build für SNR und Jitter. Generalisierungsprüfungen auf völlig anderen Kanalbedingungen werden als getrennte Experimente angelegt. Eine kombinierte Augmentation ersetzt keine getrennte Bewertung jedes Störeinflusses.

## Metriken und Hardwarevergleich

| Reportfeld | Bedeutung |
| --- | --- |
| `accuracy`, `balanced_accuracy`, `macro_f1` | Trefferrate, mittlerer Klassenrecall, ungewichtetes Klassen-F1 |
| `confusion_matrix`, `recall` | Verwechslungen und Wiedererkennung je Klasse |
| `brier_score`, `ece` | Qualität der unkalibrierten Scores; kleiner ist günstiger |
| `training_seconds`, `preprocessing_seconds` | Training und getrennt gemessene Eingabeaufbereitung |
| `latency_ms_p50`, `latency_ms_p95` | Einzel-Segment-Latenz inklusive Darstellung/Vorverarbeitung, nach Warmup; Modelldatei ist bereits geladen |
| `real_time_factor` | Median aus Rechenzeit / **tatsächlicher IQ-Segmentdauer** je gemessenem Segment; kleiner als 1 bedeutet für diesen Verarbeitungsschritt schneller als Echtzeit |
| `normalized_latency` | Latenz / feste lokale 4096-Sample-Feature-Referenzlast; kein universeller Hardwareumrechnungsfaktor |
| `training_duration_ratio` | Trainingszeit / (Epochen × Summe der physikalischen Trainingssegmentdauern) |
| `model_bytes`, `parameter_count` | Persistierte Modellkomponenten ohne abschließenden Report und Anzahl trainierbarer Parameter |
| `peak_gpu_bytes` | PyTorch-Peakallokation bei CUDA-Training, wenn vorhanden |
| `sampled_process_rss_max_bytes` | Höchster abgetasteter RSS-Wert des gemeinsamen App-Prozesses; kein isolierter Trainings-Peak |
| `sensitivity_snr90_db` | Kleinster **getesteter** SNR mit mindestens 90 % Balanced Accuracy beim geringsten getesteten Jitter; keine dBm-Empfindlichkeit |
| `false_alarm_rate`, `unknown_detection_accuracy` | Ohne unabhängigen Negativ-/Unbekannt-Datensatz bewusst `null` |

Hardwareprofil: CPU/GPU, Betriebssystem, Python/NumPy/PyTorch, Präzision, Threadzahl, Batchgröße, Warmup und Wiederholungen. CUDA-Timings werden synchronisiert. Die feste Referenzlast ermöglicht einen eingeordneten Vergleich, aber keine genaue Vorhersage für andere CPUs, GPUs oder Batchgrößen. Aufnahmegeschwindigkeit, USB-Fehler und Gesamtpipeline müssen zusätzlich praktisch geprüft werden.

Das 95-%-Wilson-Intervall ist deskriptiv: Verschiedene Augmentierungen derselben Testaufnahme sind korreliert und erhöhen nicht die Zahl unabhängiger RF-Quellen. Für belastbare wissenschaftliche Ergebnisse sind zusätzliche Aufnahmegruppen und eine auswertungsseitige Gruppen-Bootstrap-Analyse sinnvoll.

## Entscheidungsweg und portable Modelle

Die Oberfläche zeigt Signalmerkmale, optionales Vorwissen, Modell, Ablehnung und Unsicherheit. Die untrainierte AM/FM-Heuristik wird ausdrücklich als Heuristik gekennzeichnet. Digitale Stille und klar rauschartige AM/FM-Eingaben werden **vor dem Laden bzw. Ausführen teurer Modellgewichte** abgelehnt. Die übersprungenen Schritte stehen im Ergebnis. Die Rauschregel ist nur für die anfängliche AM/FM-Domäne aktiv, damit sie kein gelerntes breitbandiges digitales Signal pauschal verwirft. Das ist noch kein allgemein validierter Open-Set-Klassifikator. Die Experimentgenauigkeit ist ein Closed-Set-Argmax-Wert ohne diese Laufzeitablehnung; Coverage und selektive Genauigkeit benötigen eine separate Open-Set-Auswertung.

LDA liefert die exakte additive Zerlegung der **Logit-Differenz zwischen den beiden stärksten Klassen** in Featurebeiträge und Bias. Auf Anfrage liefern neuronale Modelle Gradient×Input-Attributionen im tatsächlichen Eingaberaum: Merkmale, Zeitfenster, Frequenzbins oder IQ-Zeitabschnitte. Diese lokalen Sensitivitäten zeigen Einfluss auf das Modell, keine physikalische Kausalität.

Ein Modellordner enthält `model.json`, `topology.json`, `weights.npz` oder `weights.pt`, gegebenenfalls `normalization.npz` und `report.json`. Gespeichert werden Eingabedarstellung, Segmentlänge, Trainingsabtastraten, Klassenfolge, Train-only-Normierung, Seed, Gruppenprovenienz und ein SHA-256-Fingerabdruck der tatsächlich verwendeten IQ-Segmente. NumPy wird mit `allow_pickle=False` geladen, PyTorch ausschließlich mit `weights_only=True`; Dimensionen und Archivgrößen werden geprüft. Ein unbekanntes Fremd-Pickle ist kein unterstütztes Modellformat.

Ein auf der Workstation exportierter Modell-ZIP lässt sich am Laptop importieren und aktivieren. Dort erfolgt Inferenz auf CPU; Training und Tests können auf beiden Rechnern stattfinden. Die genaue Laufzeit und Modellgültigkeit auf abweichender Hardware bzw. anderen RF-Bedingungen muss separat gemessen werden.
