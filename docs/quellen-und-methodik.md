# Quellen und experimentelle Methodik

Quellenstand: 20. November 2026. Architekturvorschläge sind vor der Messung Hypothesen; eine größere Topologie ist kein Beleg für bessere HF-Klassifikation.

## Promotionsansätze und Übertragung auf HF

Primärquelle: Philipp Schildt, *Condition Monitoring for Electric Aircraft Propulsion Systems*, [RMIT, DOI 10.25439/rmt.33772282](https://doi.org/10.25439/rmt.33772282). Die öffentliche [Figshare-Metadaten-API](https://api.figshare.com/v2/articles/33772282) verweist auf [Schildt.pdf](https://ndownloader.figshare.com/files/68763622), Datei-ID 68763622, MD5 `fbea7473653853e69e83388d6603cf0f`. Die Angaben wurden im PDF geprüft; das PDF wird nicht mit der Anwendung verteilt.

| Ansatz | In der Dissertation beschrieben | Gezielte Anpassung für diese Anwendung |
| --- | --- | --- |
| FB-LSTM | Kap. 6.1.1, S. 134–135; Anhang B.4.3, S. 267: kurze Hamming-Fenster, zehn spektrale Merkmale, Standardisierung, zwei LSTM-Stufen mit Dropout 0,1, voll verbundener Regressionskopf. | Merkmalsfolgen aus komplexem Empfangssignal; Trainingsstatistik separat speichern; Klassifikationskopf. |
| 5-CNN | Kap. 6.1.2, S. 136–137; Anhang B.4.4: Rohsignal mit zufälligem Ausschnitt, fünf kausale Conv1D/BN/ReLU-Stufen, Dropout 0,5 und zeitliche Aggregation. | I und Q als zwei Kanäle, Klassifikationskopf; Segmentdauer als Experimentparameter. |
| 2D-CNN | Kap. 6.1.3, S. 138–140; Anhang B.4.5, S. 268: logarithmisches 50-Band-Bark-Spektrogramm, fünf Conv2D/BN/ReLU-Stufen, Pooling, Dropout 0,2. | Komplexes lineares HF-Spektrogramm ersetzt die akustische Bark-Skala; Klassifikationskopf. |

Die Quelle untersucht Schwingungen und Restlebensdauer. Ihre Ergebnisse sind keine AM/FM-Validierung. Numerische Filter- und Hidden-Dimensionen sind in den genannten Beschreibungen nicht vollständig spezifiziert; die bereitgestellten HF-Konfigurationen sind deshalb **inspirierte Anpassungen**, keine behaupteten identischen Nachbauten oder übernommenen Gewichte.

## BFT als kontrollierte Signalquelle

Primärquelle: [DL7CR/BFT](https://codeberg.org/DL7CR/BFT), überprüfter Commit `4a670550cbb82a48a0e244c3bfeb102feac510cb`. Das [README](https://codeberg.org/DL7CR/BFT/src/commit/4a670550cbb82a48a0e244c3bfeb102feac510cb/README.md) nennt RTTY, Morse, AX.25, 4-FSK, LoRa, Hellschreiber, DTMF und POCSAG sowie Testmodi. [LoraModem.h](https://codeberg.org/DL7CR/BFT/src/commit/4a670550cbb82a48a0e244c3bfeb102feac510cb/src/LoraModem.h) setzt SF9, 125 kHz Bandbreite, CR 4/5 und Syncword 0x12. [PocsagModem.h](https://codeberg.org/DL7CR/BFT/src/commit/4a670550cbb82a48a0e244c3bfeb102feac510cb/src/PocsagModem.h) beschreibt 1200 Bit/s und ±4,5 kHz Hub. Die tatsächlichen Geräteeinstellungen gehören zusätzlich ins Aufnahmemanifest. Die FSK-Varianten sind laut README nicht automatisch mit klassischem AFSK kompatibel.

Ein BFT eignet sich zur späteren Prüfung dieser digitalen Klassen. Die aufgeführten Modi ersetzen keine konventionelle AM-Quelle und keinen UKW-Rundfunksender. Funkdienst, Modulation, Nutztext und Geräteidentität müssen getrennte Labels bleiben.

## Versuchsplanung

Für AM gegen FM zunächst zwei unabhängige Stränge verwenden: synthetische Signale für reproduzierbare Fehlerstudien und reale Aufnahmegruppen für die Übertragungsprüfung. Ein synthetischer Erfolg ist kein Nachweis am Empfänger. Leerlauf, reine Träger, digitale Signale und überlagerte Signale bilden zusätzliche negative beziehungsweise unbekannte Beispiele.

Sinnvolle Variationen sind:

- Segmentlänge und zeitlicher Versatz des Ausschnitts; unterschiedliche Nutzinhalte, Sprachpausen und Musik.
- SNR, Trägerfrequenzversatz, Abtastratenfehler, zufällige Phase und ein genau definiertes Jittermodell. „Jitter“ allein ist kein reproduzierbarer Parameter.
- AM-Modulationsgrad, FM-Hub und Nutzsignalbandbreite; diese Größen unabhängig vom Klassenlabel variieren.
- Empfangspegel, IQ-Amplituden-/Phasenfehler, DC-Offset, Quantisierung und Clipping.
- Mehrweg, Fading, impulsive Störer, benachbarte Kanäle und zwei gleichzeitig aktive Signale.
- Lernrate, Batchgröße, Regularisierung, Optimierer und Modellbreite; mehrere Zufallsseeds und feste Rechenbudgets.

Die aktuelle API-/UI-Implementierung entscheidet, welche dieser Variationen ausführbar sind. Diese Liste beschreibt die vollständige Versuchsplanung und behauptet keine bereits implementierte Augmentation.

Aufnahme- und Sendergruppen **vor** Segmentierung, Normalisierung und Augmentation trennen. Benachbarte Fenster einer Aufnahme dürfen nicht auf Training und Test verteilt werden. Hyperparameter nur anhand Training/Validierung wählen; den unabhängigen Test für die abschließende Bewertung zurückhalten. Zusätzlich ganze Aufnahmetage, Empfänger und Frequenzbereiche zurückhalten. Bei Frequenz-Vorwissen stets einen Vergleich ohne Vorwissen ausweisen. Vorlabels aus Bandplänen erhalten eine Herkunft und bleiben unbestätigt, bis eine unabhängige Prüfung erfolgt.

Seit 0.6.0 kann im Experiment-Setup ausdrücklich zugestimmt werden, **unterschiedliche ursprüngliche Aufnahmen derselben Sitzung** auf Training, Validierung und Test zu verteilen. Dann sind je Klasse mindestens drei ursprüngliche Aufnahmen erforderlich. Die Auswahl wird vor Segmentierung festgelegt; abgeleitete Clips, bekannte Vorfahren, Dateialiasse und erkannte exakte Dateikopien bleiben zusammen. Die automatische Hyperparameteroptimierung verwendet dieselbe Aufteilung und hält den Finaltest weiterhin bis zur Modellwahl zurück.

Diese Bewertung prüft getrennte Aufnahmen unter möglicherweise gemeinsamen Sender-, Empfänger- und Kanalbedingungen. Viele Segmente erhöhen die Datenmenge, ersetzen aber keine unabhängigen Sitzungen. Genauigkeit und Robustheitswerte können dadurch günstiger ausfallen; eine Generalisierung auf andere Tage, Geräte oder Sitzungen ist nicht nachgewiesen. `allow_same_session`, `split_policy` und `validation_independence` dokumentieren die Zustimmung und Aussagegrenze in Modell- und Experimentberichten. Ohne Zustimmung bleibt die Sitzung das Trennkriterium.

Die Berichte enthalten zusätzlich `recording_group_memberships`, also die Zuordnung jeder Aufnahme zur ursprünglichen Sitzung, zum Aufnahmeursprung und zur tatsächlich verwendeten Splitgruppe. Ausdrücklich als ungeklärt markierte Abstammungsketten werden im Modus mit gemeinsamen Sitzungen abgewiesen; fehlende Vorfahren dürfen nicht zur Erfindung unabhängiger Aufnahmen führen. Solche Clips können weiterhin abgespielt und im strengeren Sitzungsmodus verwendet werden, sofern dessen übrige Bedingungen erfüllt sind.

Für den Duplikatschutz liest die Vorprüfung bei aktivierter Option die Dateien zur vollständigen SHA-256-Prüfsumme (einem digitalen Inhaltsfingerabdruck); ein begrenzter Cache je Prozess vermeidet wiederholtes Hashen unveränderter Dateien. Neue Trainingsprozesse benötigen ihren eigenen ersten Lesevorgang. Das kann bei großen Datensätzen dauern. Es erfolgt dabei keine Merkmalsauswertung und keine Qualitätsmessung auf Testdaten. Unbekannte, extern erzeugte und nicht gekennzeichnete überlappende Ausschnitte sind dadurch nicht allgemein erkennbar; korrekte Herkunftsmetadaten bleiben erforderlich.

## Vergleichbare Leistungsmaße

Neben Accuracy und Macro-F1: Konfusionsmatrix, klassenweise Recall/Precision, Fehlalarmrate, unbekannte Signale, Kalibrierung und Erkennungsrate über SNR dokumentieren. Sensitivität in dBm setzt einen kalibrierten HF-Aufbau voraus; digitale IQ-Pegel reichen dafür nicht.

Die Dissertation verwendet auf S. 140–142 Dauerquotienten für Vorverarbeitung, Training und Inferenz. Hier ist der Inferenz-Echtzeitfaktor `Latenz / Signaldauer` sinnvoll. Dazu p50/p95, Durchsatz, Modellbytes, Parameterzahl und Speicherbedarf erfassen. Kalte Initialisierung und eingelaufene Inferenz, Batch 1 und Batchbetrieb getrennt messen. Bei CUDA vor und nach der Messung synchronisieren.

Ein Referenzbenchmark hilft beim Übertragen zwischen Rechnern: gleiche feste Arbeitslast auf beiden Geräten ausführen, Hardware, Backend, Bibliotheksversionen, Threads, Batchgröße und Genauigkeitsformat speichern. Ein Zeitverhältnis ist nur eine **arbeitslastbezogene Schätzung**; CPU-, Speicher- und GPU-lastige Topologien skalieren unterschiedlich. Auf dem Ziel-Laptop tatsächlich nachmessen. Ein GPU-Training kann als portables Paket exportiert werden; Gewichte, Topologie, Klassenreihenfolge, Segmentierung, Normalisierung und Experimentherkunft gehören gemeinsam hinein.

## Nachvollziehbarkeit

Jede Entscheidung soll beobachtete Merkmale, verwendete Priors, ausgeführte und übersprungene Tests, Modellversion und Unsicherheit enthalten. Eine Aktivierungs-Heatmap erklärt nicht allein die physikalische Ursache. Für neuronale Verfahren ergänzend gezielte Gegenbeispiele prüfen: Frequenz verschieben, Pegel ändern, Signalteil maskieren, Modulationsparameter variieren. So wird sichtbar, ob eine Entscheidung auf Modulation oder auf einer zufälligen Datensatzkorrelation beruht.
