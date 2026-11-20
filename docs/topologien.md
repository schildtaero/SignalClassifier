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

## Mehrere Topologien in einem Experiment vergleichen

Ein Experiment kann **1–50 unterschiedliche Topologien** umfassen. Alle ausgewählten Ansätze werden nacheinander mit denselben Aufnahmen, Aufnahmegruppen, Seeds und Augmentationskonfigurationen trainiert. Damit bleibt der GPU-/RAM-Bedarf auf einen Trainingslauf begrenzt. Bei gleicher Segmentlänge stimmt auch der Fingerabdruck der verwendeten IQ-Segmente überein. Feature-LDA läuft auf CPU, auch wenn die neuronalen Läufe CUDA verwenden.

Jeder Ansatz erhält eigene Modelle, Konfigurationen und einen vollständigen Bericht. Der gemeinsame Bericht enthält eine Vergleichstabelle mit Metriken, Validierung und Links auf alle Modellartefakte. Topologieauswahl, Konfiguration und Fortschritt gehören zu einem gemeinsamen Experiment; es ist kein manuelles Anlegen eines separaten Experiments pro Netz erforderlich.

Im Setup lassen sich alle bestätigten verfügbaren Aufnahmen beziehungsweise alle verfügbaren Topologien gemeinsam auswählen und wieder abwählen. Die API und der Loader erlauben bis zu 1000 Aufnahmen; das 128-MiB-IQ-Budget und maximal 4096 Segmente je Split bleiben bestehen. Eine Sammelauswahl schneidet keine Dateien stillschweigend ab. Die Vorprüfung zeigt überschrittene Grenzen vor dem Start.

Im Modus **Unabhängig auswerten** entscheidet bei manuellen Konfigurationen das Validierungs-Macro-F1 über die vorgeschlagene Auswahl; bei Gleichstand gewinnt das kleinere Modell. Die [automatische Hyperparameteroptimierung](hyperparameteroptimierung.md) verwendet das gewählte Validierungsziel, standardmäßig den Gesamtscore. Testgenauigkeit oder Test-F1 werden dafür **nicht** benutzt. Auch innerhalb einer Topologie wird eine Hyperparameterkombination nur nach Validierung ausgewählt. Sämtliche anderen Netze bleiben gespeichert und können separat geladen werden.

Im Modus **Nur trainieren** werden ebenfalls alle ausgewählten Netze trainiert und gespeichert. Es gibt dabei keine Qualitätsrangfolge und keinen Gewinner. Das erste Netz bleibt lediglich eine technische Standardreferenz für kompatible Modelloperationen; daraus folgt keine Empfehlung. Rechenzeiten und Modellgrößen können verglichen werden, während Genauigkeit und F1 unbekannt bleiben.

Die API nimmt die Auswahl als `topology_ids` entgegen, zum Beispiel `["feature-lda", "feature-mlp-16", "fb-lstm-32"]`. Bereits vorhandene Einzelkonfigurationen mit `topology_id` bleiben gültig. Die zugehörigen Topologien werden vor dem Einreihen aufgelöst; auch optionale Lernraten, Batchgrößen und Kanalaugmentationsgrenzen werden synchron geprüft.

## Transparenter Gesamtscore und Live-Empfehlungen

Der Vergleich bietet zwei getrennte Rangfolgen: **Validierungs-Balanced-Accuracy** und **Gesamtscore von 0 bis 100 Punkten**. Die abschließenden Testergebnisse bleiben sichtbar, bestimmen aber weder Score noch Rangfolge oder Empfehlung. Die bisherige technische Standardauswahl anhand des Validierungs-Macro-F1 bleibt im Bericht getrennt von der Gesamtscore-Empfehlung erhalten.

Bei `K` Klassen wird Qualität oberhalb der Zufallstreffergrenze normiert: `q(b)=max(0,min(1,(b−1/K)/(1−1/K)))`. Damit erhält ein zufälliger Zweiklassen-Klassifikator mit Balanced Accuracy 0,5 keine Genauigkeitspunkte.

| Anteil | Punkte | Berechnung |
| --- | --- | --- |
| Genauigkeit | 45 | `45 × q(Validierungs-Balanced-Accuracy)` |
| Robustheit | 35 | `35 × q(0,7 × Mittelwert + 0,3 × schlechtester Wert)` der Balanced Accuracy im **Validierungs**-SNR-/Jitter-Raster |
| Echtzeitreserve | 15 | `15 × min(1, (1 / p95-RTF) / 5)` |
| Trainingsaufwand | 5 | `5 / (1 + Trainingssekunden / 300)` |

Die Echtzeitpunkte sind ab **fünffacher Echtzeitgeschwindigkeit** vollständig erreicht. Zehnfach schneller erhält dieselben 15 Punkte; weitere Beschleunigung beeinflusst auch bei Punktegleichstand die Reihenfolge nicht. Bei gleichem Score entscheiden höhere Validierungs-Balanced-Accuracy und danach kleinere Modelldateien; ansonsten bleibt die Auswahlreihenfolge erhalten. Die 300 Sekunden im Trainingsanteil sind ein offen dokumentiertes Referenzbudget, kein Zeitlimit und keine hardwareunabhängige Messung.

Der p95-Echtzeitfaktor wird über `Rechenzeit / tatsächliche Segmentdauer` gemessen. Für den Score wird der **CPU-Livepfad** verwendet: geladenes Modell nach Warmup, Featuregate, Eingabedarstellung, Vorwärtsrechnung und Entscheidung, ohne zusätzliche Attribution und ohne erstmaliges Laden der Gewichte. Diese Zeitmessung verwendet Validierungssegmente, damit auch hier keine abschließenden Testinformationen in die Auswahl fließen. Training und Validierung können auf CUDA stattfinden; `training_device` und `inference_device="cpu"` werden getrennt ausgewiesen. Die gemessene Quote von Merkmals-Ablehnungen steht unter `timing_skipped_model_fraction`.

Ein Modell ist für die Live-Einzelprüfung nur dann zeitlich geeignet, wenn sein **gemessener CPU-p95-RTF höchstens 1** beträgt. Unter diesen Modellen werden höchstens drei nach dem Gesamtscore empfohlen. Ein sehr genaues, aber langsames Modell kann in der Qualitätsrangfolge hoch stehen und wird trotzdem nicht als Live-Kandidat empfohlen. Die gemeinsame Verarbeitung mehrerer Modelle braucht zusätzlich eine Messung der gesamten Pipeline.

Fehlen Validierung, das Robustheitsraster oder erforderliche CPU-Laufzeit-/Trainingsmetriken, bleibt der Gesamtscore unbekannt. Im Modus **Nur trainieren** gibt es weder Qualitätsscore noch Qualitätsränge oder automatisch empfohlene Topologien. Bereits vorhandene Berichte ohne p95-CPU-Messung bekommen keinen nachträglich erfundenen Score.

Der Bericht exportiert Komponenten, Rohwerte, Normierung, Gewichte, Formeln, Parameter, Gründe für fehlende Werte und Hardwareprofil. Die Messung gilt für den dokumentierten Rechner. Ein auf der Workstation berechneter Score ist keine exakte Vorhersage für den Laptop; dort müssen Laufzeiten erneut praktisch gemessen werden. Synthetische Qualitätsscores bleiben Softwareversuche und beweisen keine RF-Generalisierung.

## Erste Versuche

Vor dem Start prüft die Datensatz-Vorprüfung die bestätigten Labels, die tatsächliche Zahl unabhängiger Aufnahmegruppen sowie lesbare SigMF-Dateien und vollständige Segmente. Sie lädt dabei keine IQ-Daten in den Arbeitsspeicher. Mehrere Ausschnitte aus derselben Aufnahmesitzung bleiben **eine** Gruppe, auch wenn es mehrere Dateien sind. Die Meldung zeigt je Klasse Dateien, Gruppen, vollständige Segmente und den konkreten nächsten Schritt.

Es gibt zwei ausdrücklich auswählbare Betriebsarten:

- **Unabhängig auswerten** (`evaluation_mode="independent"`, Standard): mindestens drei unabhängige Gruppen je Klasse für Training, Validierung und Test. Keine Gruppe wird über diese Bereiche aufgeteilt. Für eine belastbare Auswertung sind weitere Gruppen sinnvoll.
- **Nur trainieren** (`evaluation_mode="training_only"`): mindestens eine ausreichend lange, bestätigte Aufnahmegruppe je Klasse und mindestens zwei Klassen. Damit lässt sich bereits mit je einer AM- und FM-Aufnahme ein Modell trainieren, speichern, übertragen und ausprobieren. Es entstehen **keine** Validierungs-/Testsplits und keine Genauigkeits-, F1-, Kalibrierungs- oder Empfindlichkeitswerte. Es ist genau eine vorher festgelegte Kombination aus Segmentlänge, Lernrate und Batchgröße erlaubt; das neuronale Modell nutzt den letzten Trainingsstand, keine Auswahl anhand von Trainingsdaten. Trainingsverlust ist nur eine Optimierungsdiagnose. Laufzeiten können an Trainingssegmenten gemessen werden und werden entsprechend gekennzeichnet.

Nur trainierte Modelle und Berichte enthalten `evaluation_available=false` und `validation_status="unvalidated"`. Ihre Scores sind kein Nachweis der Generalisierung. Für die spätere unabhängige Bewertung zusätzliche getrennte Sitzungen aufnehmen und ein neues Experiment im Standardmodus starten.

1. Ohne Hardware einen ausdrücklich synthetischen AM/FM-Versuch mit `feature-lda` durchführen. Dies prüft Software, Speichern/Laden und Auswertung, nicht die RF-Leistung.
2. Für einen ersten unvalidierten Trainingslauf genügt je eine bestätigte AM- und FM-Aufnahme im Modus **Nur trainieren**. Für eine unabhängige reale Auswertung mindestens drei getrennte Aufnahmesitzungen pro Klasse aufnehmen und **manuell bestätigen**. Beim ersten Versuch AM-Flugfunk und UKW-FM getrennt aufnehmen und jeweils nur das isolierte gewünschte Signal verwenden. Bandplanvorschläge gelten nicht als bestätigte Labels.
3. Training, Validierung und Test werden anhand **einer globalen Zuordnung physischer Aufnahmegruppen** getrennt. Ausschnitte desselben Ursprungs bleiben zusammen, auch wenn sie unterschiedliche Klassen enthalten. Aufnahme-IDs und Gruppen sind im Report dokumentiert.
4. Feature-LDA gegen Feature-MLP und kleine IQ-/2D-CNNs vergleichen. Anschließend FB-LSTM und 5-CNN hinzufügen. Umfangreichere Netze sind nur dann sinnvoll, wenn sie auf unabhängigen Aufnahmen messbar helfen.
5. Für Frequenzunabhängigkeit dieselben Modulationen auf weiteren Frequenzen, mit verschiedenen Nutzsignalen, Antennen, Pegeln und Empfängern prüfen. Weder RF-Mittenfrequenz noch Bandplanlabel fließen als Modellmerkmal ein. Optionales Vorwissen erscheint als separat gekennzeichneter Hinweis.

Synthetische Modulationen umfassen konventionelles AM mit Träger und FM mit wechselnden Nutzsignalformen, Modulationstiefen, Frequenzhüben und Frequenzversätzen. Bestätigte reale Aufnahmen erlauben **2–64 beliebige Klassen**, etwa AM, FM, POCSAG und LoRa. Für zusätzliche Standards werden keine vermeintlich realistischen Daten erfunden. Training mit weiteren Labels ist kein Protokolldecoder-Nachweis.

## Sweep und Kanalvariationen

`segment_length` ist eine Sampleanzahl, kein Zeitwert; die tatsächliche Dauer ist `Samples / tatsächliche Abtastrate`. Im manuellen Modus bilden `segment_lengths`, `learning_rates` und `batch_sizes` bis zu zwölf Kombinationen pro Topologie. Die Modellwahl erfolgt dort anhand des Validierungs-Macro-F1, bei Gleichstand anhand der Modellgröße, niemals anhand des besten Testergebnisses. Die automatische Suche besitzt ein eigenes gespeichertes Budget und Validierungsziel.

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
| `latency_ms_p50`, `latency_ms_p95` | Einzel-Segment-Latenz des CPU-Livepfads inklusive Featuregate/Darstellung/Vorverarbeitung nach Warmup; Gewichte sind bereits geladen, Attribution ist aus |
| `real_time_factor` | Median aus Rechenzeit / **tatsächlicher IQ-Segmentdauer** je gemessenem Segment; kleiner als 1 bedeutet für diesen Verarbeitungsschritt schneller als Echtzeit |
| `real_time_factor_p95` | 95. Perzentil desselben CPU-Zeitverhältnisses; Grundlage für Echtzeitreserve und Live-Eignung |
| `normalized_latency` | Latenz / feste lokale 4096-Sample-Feature-Referenzlast; kein universeller Hardwareumrechnungsfaktor |
| `training_duration_ratio` | Trainingszeit / (Epochen × Summe der physikalischen Trainingssegmentdauern) |
| `model_bytes`, `parameter_count` | Persistierte Modellkomponenten ohne abschließenden Report und Anzahl trainierbarer Parameter |
| `peak_gpu_bytes` | PyTorch-Peakallokation bei CUDA-Training, wenn vorhanden |
| `sampled_process_rss_max_bytes` | Höchster abgetasteter RSS-Wert des gemeinsamen App-Prozesses; kein isolierter Trainings-Peak |
| `sensitivity_snr90_db` | Kleinster **getesteter** SNR mit mindestens 90 % Balanced Accuracy beim geringsten getesteten Jitter; keine dBm-Empfindlichkeit |
| `false_alarm_rate`, `unknown_detection_accuracy` | Ohne unabhängigen Negativ-/Unbekannt-Datensatz bewusst `null` |

Hardwareprofil: CPU/GPU, Betriebssystem, Python/NumPy/PyTorch, Präzision, Threadzahl, Trainingsgerät, CPU-Inferenzgerät, Batchgröße, Warmup und Wiederholungen. CUDA-Training wird für die Trainingszeit synchronisiert; die ausgewiesene Inferenzmessung läuft auf CPU. Die feste Referenzlast ermöglicht einen eingeordneten Vergleich, aber keine genaue Vorhersage für andere CPUs, GPUs oder Batchgrößen. Aufnahmegeschwindigkeit, USB-Fehler und Gesamtpipeline müssen zusätzlich praktisch geprüft werden.

Das 95-%-Wilson-Intervall ist deskriptiv: Verschiedene Augmentierungen derselben Testaufnahme sind korreliert und erhöhen nicht die Zahl unabhängiger RF-Quellen. Für belastbare wissenschaftliche Ergebnisse sind zusätzliche Aufnahmegruppen und eine auswertungsseitige Gruppen-Bootstrap-Analyse sinnvoll.

## Entscheidungsweg und portable Modelle

Die Oberfläche zeigt Signalmerkmale, optionales Vorwissen, Modell, Ablehnung und Unsicherheit. Die untrainierte AM/FM-Heuristik und nur trainierte, unvalidierte Modelle werden ausdrücklich gekennzeichnet. Digitale Stille und klar rauschartige AM/FM-Eingaben werden **vor dem Laden bzw. Ausführen teurer Modellgewichte** abgelehnt. Die übersprungenen Schritte stehen im Ergebnis. Die Rauschregel ist nur für die anfängliche AM/FM-Domäne aktiv, damit sie kein gelerntes breitbandiges digitales Signal pauschal verwirft. Das ist noch kein allgemein validierter Open-Set-Klassifikator. Die Experimentgenauigkeit ist bei unabhängiger Auswertung ein Closed-Set-Argmax-Wert ohne diese Laufzeitablehnung; Coverage und selektive Genauigkeit benötigen eine separate Open-Set-Auswertung. Beim reinen Training fehlen diese Gütemaße vollständig.

LDA liefert die exakte additive Zerlegung der **Logit-Differenz zwischen den beiden stärksten Klassen** in Featurebeiträge und Bias. Auf Anfrage liefern neuronale Modelle Gradient×Input-Attributionen im tatsächlichen Eingaberaum: Merkmale, Zeitfenster, Frequenzbins oder IQ-Zeitabschnitte. Diese lokalen Sensitivitäten zeigen Einfluss auf das Modell, keine physikalische Kausalität.

Ein Modellordner enthält `model.json`, `topology.json`, `weights.npz` oder `weights.pt`, gegebenenfalls `normalization.npz` und `report.json`. Gespeichert werden Eingabedarstellung, Segmentlänge, Trainingsabtastraten, Klassenfolge, Train-only-Normierung, Seed, Gruppenprovenienz und ein SHA-256-Fingerabdruck der tatsächlich verwendeten IQ-Segmente. NumPy wird mit `allow_pickle=False` geladen, PyTorch ausschließlich mit `weights_only=True`; Dimensionen und Archivgrößen werden geprüft. Ein unbekanntes Fremd-Pickle ist kein unterstütztes Modellformat.

Ein auf der Workstation exportierter Modell-ZIP lässt sich am Laptop importieren und aktivieren. Dort erfolgt Inferenz auf CPU; Training und Tests können auf beiden Rechnern stattfinden. Die genaue Laufzeit und Modellgültigkeit auf abweichender Hardware bzw. anderen RF-Bedingungen muss separat gemessen werden.
