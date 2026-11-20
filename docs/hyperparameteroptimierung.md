# Automatische Hyperparameteroptimierung

Im Experiment-Setup kann die automatische Suche eingeschaltet werden. Sie läuft lokal auf CPU beziehungsweise mit der gewählten PyTorch/CUDA-Trainingsoption und benötigt keine zusätzliche Optimierungsbibliothek. Ohne PyTorch bleibt die Suche für Feature-LDA verfügbar. Die [Ausführungssteuerung](parallelisierung.md) kann mehrere Topologien parallel optimieren; bei nur einer Topologie können deren unabhängige Suchversuche parallel laufen. Es werden keine verschachtelten Trainingspools erzeugt.

## Suchverfahren und Suchraum

Das Verfahren ist **Random Search**, keine behauptete bayessche Optimierung: Ein gespeicherter Seed erzeugt eindeutige Vorschläge. Die Lernrate wird logarithmisch gleichverteilt gezogen; Segmentlänge, Batchgröße und Epochenbudget stammen aus den gewählten Listen. Die Suche ist vorab durch Versuchszahl und Zeitbudget begrenzt. Feature-LDA variiert ausschließlich Segmentlängen, weil Lernrate, Batchgröße und Epochen dort keine Wirkung haben.

Standard sind zwölf Versuche und 300 Sekunden **je Topologie**. Parallel laufende Topologiebudgets können sich zeitlich überlappen; die Summe aller Jobzeiten ist keine Experimentwandzeit. Der Standard-Suchraum umfasst halbe, aktuelle und doppelte Segmentlänge innerhalb 256–65536 Samples, Lernraten von `1e-5` bis `1e-2`, Batchgrößen 16/32/64 und Epochenbudgets 5/10/20. Die Oberfläche erlaubt eigene Werte. Ungeeignete Segmentlängen werden mit einer dokumentierten Begründung aus dem wirksamen Suchraum ausgeschlossen; Aufnahmen werden dadurch nicht heimlich weggelassen. Bestehende Speicher-, Segment- und Gruppenregeln gelten weiterhin.

Beispiel für die Experiment-API:

```json
{
  "source": "synthetic",
  "topology_ids": ["feature-lda", "feature-mlp-16", "iq-cnn-16"],
  "seed": 42,
  "evaluation_mode": "independent",
  "experiment_notes": "Ziel: AM/FM. Erst numerischer Softwareversuch, anschließend getrennte reale Empfangssitzungen.",
  "optimization": {
    "enabled": true,
    "method": "random",
    "max_trials": 12,
    "timeout_seconds": 300,
    "objective": "score",
    "search_space": {
      "segment_lengths": [1024, 2048, 4096],
      "learning_rate_min": 0.00001,
      "learning_rate_max": 0.01,
      "batch_sizes": [16, 32, 64],
      "epochs": [5, 10, 20]
    }
  }
}
```

Bis zu acht Werte sind je Auswahlliste erlaubt, insgesamt höchstens 50 Versuche je Topologie. `timeout_seconds: 0` deaktiviert das Suchzeitbudget. Manuelle Raster bleiben über `optimization.enabled: false` verfügbar. Ohne unabhängige Validierung wird automatische Optimierung abgewiesen; Trainingsgüte ist kein Ersatz für Generalisierung.

## Ziel, Vergleich und abschließender Test

Standardziel ist der vorhandene **100-Punkte-Score** mit 45 Punkten Genauigkeit, 35 Robustheit, 15 Echtzeitreserve und fünf Trainingsaufwand. Alternativ stehen Validierungs-Macro-F1 und Validierungs-Balanced-Accuracy zur Auswahl. Der gewählte Wert steuert die Auswahl innerhalb einer Topologie und, bei automatischer Suche, auch die technische Standardauswahl zwischen Topologien. Die zusätzliche Score-/Genauigkeitsrangliste und die Top-3-Live-Empfehlungen bleiben nachvollziehbar ausgewiesen.

Die Ursprungsgruppen und Seeds bleiben fest. Normalisierung wird nur auf Trainingsdaten angepasst. Alle Versuche verwenden dasselbe Validierungs-SNR-/Jitter-Raster; seine Schwierigkeit wird nicht als Hyperparameter optimiert. Bei unterschiedlichen Segmentlängen stammen synthetische Daten aus denselben längeren Referenzsignalen und reale Daten aus gemeinsamen Quellenankern. Die tatsächlichen Segmentzahlen, Signaldauern und Fingerabdrücke stehen je Versuch im Bericht; unterschiedliche Fensterlängen sind nicht automatisch identische Eingabedauern.

Während der Suche werden **keine Test-IQ-Segmente gelesen und keine Testmetriken für die Kandidaten erzeugt**. Nach Abschluss der Suche wird das Gewinnermodell festgelegt und genau einmal über das abschließende Testgitter ausgewertet. Das ist eine Testauswertung je ausgewählter Topologie, ohne erneutes Training. Die Testwerte bestimmen keine Rangfolge. Bei sehr vielen Versuchen kann die Auswahl die Validierung zunehmend ausnutzen; Versuchszahl und Zahl unabhängiger Gruppen gehören deshalb in den Bericht.

## Budget, Fehler und Protokoll

Die Zeitkontrolle erfolgt vor Versuchen und an Fortschrittspunkten, unter anderem nach Epochen. Eine laufende native Operation kann das Budget überschreiten; es ist kein harter Abbruch mitten in einer GPU-/BLAS-Operation. Ein unvollständiger Versuch wird als `timed_out` protokolliert und kann nicht gewinnen. Nur vollständig trainierte und validierte Versuche sind auswählbar. Der abschließende Test liegt außerhalb des Suchzeitbudgets und wird separat mit `final_test_seconds` erfasst.

Die Oberfläche und das Berichtspaket enthalten die angefragten und tatsächlich ausgeführten Budgets, wirksamen Suchraum, Seed, Versuchskonfigurationen, Validierungswerte, Scorekomponenten, Trainings-/Vorverarbeitungszeiten, Fehler und Stopgrund. Gesamtaufwand der Suche und Trainingsdauer des Gewinnermodells sind getrennte Angaben. Ein Benutzerabbruch lässt die bisher geschriebenen Versuchsergebnisse erhalten; ohne vollständig bewerteten Versuch gibt es keine erfundenen besten Parameter.

Die Datei `optimization.json` wird während der Suche atomisch aktualisiert. Das strukturierte Ereignisjournal ergänzt Versuchsbeginn, Abschluss und einzelne Epochen mit Lernverlusten. Die beste Konfiguration kann ins Setup übernommen werden, ohne sofort einen neuen Lauf zu starten. Die Suchvorschläge sind mit demselben Seed reproduzierbar; Laufzeiten, CPU-Last und numerische GPU-Effekte können die gemessenen Scores und damit den Gewinner beeinflussen.

Für eine PDF-Auswertung das [Berichtspaket](berichtsexport.md) exportieren. Es enthält die nötigen Rohmetriken und den Suchverlauf, während IQ-Daten und portable Modellpakete getrennt archiviert werden.
