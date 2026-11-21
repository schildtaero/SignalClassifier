# Experimentpaket für einen späteren PDF-Bericht

Ein Experiment kann als eigenständiges Berichtspaket exportiert werden. Das ZIP enthält die gespeicherten Ergebnisse und ihren Entstehungskontext; es erzeugt noch keine PDF-Datei. Es eignet sich als vollständige Eingabe für einen anschließenden Bericht in ChatGPT und bleibt mit gewöhnlichen JSON-/CSV-/SVG-Werkzeugen lesbar.

Der Export ist auch für laufende, fehlgeschlagene und abgebrochene Experimente möglich. Sein Status wird mitgeliefert. Vorliegende Optimierungsteilberichte, abgeschlossene Epochen, Fehler und Timeouts bleiben erhalten. Ein unvollständiger Lauf erhält dadurch keine erfundenen Qualitätswerte und keinen nachträglichen Gesamtgewinner.

## Inhalt des ZIP

| Datei | Inhalt |
| --- | --- |
| `report.json` | Vollständiger Experimentzustand einschließlich Konfiguration, Notizen, Reports, Modellvergleichen, allen Kandidaten, Optimierung, Teilberichten und Zusatzfeldern |
| `report.md` | Deutsche Übersicht mit Versuchsziel/Messaufbau, Aussagegrenzen, Tabellen, Diagrammverweisen und Fehlern |
| `provenance.json` | Übergebene Provenienz, beispielsweise Queue-Zeitpunkt, Software-/Abhängigkeitsversionen, Quell-SHA, Hardware und Einstellungen |
| `dataset_inventory.json`, `.csv` | Übergebenes Aufnahmeinventar mit Labels, Gruppen, RF-Parametern, verfügbaren SigMF-Metadaten und Fingerprints |
| `events.json`, `.jsonl`, `.csv` | Vollständige übergebene Ablaufereignisse in ihrer Reihenfolge; JSONL wird normalisiert neu serialisiert |
| `resource_usage.csv` | Abgetastete System-/App-Prozessbaum-CPU, GPU-Geräteauslastung und VRAM mit Messzeit, Quelle und Geltungsbereich; fehlende Werte bleiben leer |
| `phase_timings.csv` | Phasenzeiten ausgewählter Modelle und Optimierungsversuche, einschließlich CPU-Benchmark-Wartezeiten |
| `model_comparison.csv` | Validierung und abschließender Test getrennt, Score, CPU-/Trainingszeiten, Modellgröße und Hardwarekontext |
| `candidates.csv` | Alle vorhandenen Such-/Sweepkandidaten mit Konfiguration und Originalzusatzfeldern |
| `optimization_trials.csv` | Alle HPO-Versuche, einschließlich fehlgeschlagener, abgebrochener und zeitlich begrenzter Versuche |
| `epoch_history.csv` | Vorhandene Epochen je Modell/Trial mit Verlusten und Trialstatus; keine ersetzten oder erfundenen Epochen |
| `snr_jitter.csv` | Vorhandene Rastermessungen mit Split, Modell, Kandidat, SNR und Jitter |
| `confusion_matrices.csv` | Vorhandene Verwechslungsmatrizen als Zeilen-/Spaltenzählungen einschließlich Klassen und Split |
| `plots/*.svg` | Lokal renderbare Diagramme für Modellqualität, Segmentlatenz, SNR/Jitter und getrennte Validierungs-/Test-Verwechslungen, soweit Daten vorliegen |
| `serialization.json` | Explizite Diagnose nicht endlicher Werte mit Originalmarker und vollständigem JSON-Pointer |
| `manifest.json` | Dateiliste, tatsächliche Größen und SHA-256-Prüfsummen aller Nutzdateien; das Manifest hasht sich nicht selbst |
| `README.txt` | Anleitung und konkreter Vorschlag für die anschließende ChatGPT-PDF-Anfrage |

Es gibt keine externen Bibliotheken, Schriften oder CDNs in den Diagrammen. Modellnamen und Klassen werden als XML-Text escaped. Generierte Dateinamen stammen nicht aus benutzerdefinierten Modellnamen.

## Aussagekraft und Vollständigkeit

`report.json` behält sämtliche JSON-Felder bei; auch neue Optimierungs- oder Provenienzfelder werden nicht auf einen festen Exportkatalog reduziert. Die CSVs bieten zusätzlich `raw_json`-Spalten für Zusatzfelder und JSON-Pointer als Quellenbezug. Der lesbare Markdown-Bericht begrenzt lediglich lange Übersichtstabellen und Ereignisauszüge; die JSON-/CSV-Daten bleiben vollständig.

Nicht endliche Zahlen wie NaN oder Infinity sind in standardkonformem JSON nicht erlaubt. Sie werden als `null` gespeichert und zusätzlich mit ihrem ursprünglichen Marker und ihrem vollständigen Pfad in `serialization.json` verzeichnet. Dadurch gehen sie nicht still verloren. Leere Qualitätsfelder sind keine Null-Prozent-Messungen.

Die Berichtstabellen übernehmen die gespeicherte Reihenfolge und berechnen keinen neuen Gewinner aus Testwerten. Validierungswerte und endgültige Testwerte bleiben getrennt. Bei `evaluation_available=false`, `validation_status=unvalidated` oder `evaluation_mode=training_only` werden Qualitätstabellen und Qualitätsdiagramme nicht mit vermeintlichen Ergebnissen gefüllt. Widersprüchliche Originalfelder bleiben zur Nachprüfung im Roh-JSON erhalten. Gemessene Trainingszeiten, Latenzen und Modellgrößen können weiterhin exportiert werden.

Bei Hyperparameteroptimierung werden alle Trials einschließlich ihrer Status- und Fehlerfelder exportiert. Direkte `optimization.json`-Teilberichte werden unterstützt. Fertige Teilberichte, die bereits identisch als Modellvergleich vorliegen, erscheinen im Roh-JSON weiterhin, werden jedoch nicht als zusätzliche Modelle doppelt gezählt. SNR-Diagramme eines ausgewählten Modells vermischen nicht die Suchkandidaten einer noch unvollständigen Optimierung.

Der Export öffnet **keine IQ-Dateien oder Modellgewichte** und folgt keinen Pfaden aus einem Bericht. Solche Pfade sind Herkunftsangaben. Er kopiert ausschließlich die übergebenen Zustands-, Ereignis- und Inventardaten. Ein historischer Lauf ohne unveränderlichen Queue-Snapshot bekommt keine aktuellen Labels oder aktuelle Hardwarewerte als vermeintlich historische Daten untergeschoben. Ein vorhandener Warnhinweis erscheint im Bericht. Explizite Inventarhinweise wie `metadata_omitted_reason` werden übernommen.

Die Prüfsummen haben unterschiedliche Geltungsbereiche: Das ZIP-Manifest hasht seine tatsächlichen Nutzdateibytes. `configuration_sha256` und `inventory_sha256` in der Provenienz können dagegen kanonische JSON-Payloads hashen; `snapshot_hash_format` beschreibt deren Format. Eine Metadaten-SHA oder ein Stat-Fingerprint ist keine behauptete vollständige IQ-Inhaltsprüfung. Fingerprints beweisen außerdem nicht die wissenschaftliche Richtigkeit eines Resultats.

## Verwendung in ChatGPT

Für neue wissenschaftliche Berichte gilt die am 21.11.2026 bestätigte [Berichtsvorlage](berichtsvorlage.md) mit zehn Hauptkapiteln. Die [maschinenlesbare Gliederung](berichtsgliederung.json) bewahrt Kapitel und Berichtsregeln für folgende Auswertungen. Abweichende ausdrückliche Nutzerwünsche haben Vorrang.

1. Das vollständige Berichtspaket herunterladen und später an ChatGPT übergeben.
2. Den Vorschlag aus `README.txt` verwenden und Umfang, Zielpublikum sowie gewünschte PDF-Gliederung ergänzen.
3. Für den Bericht Versuchsziel, Empfänger/Messaufbau, Datenherkunft und Gruppenaufteilung, Modelltopologien, Suchräume/Budgets, sämtliche Trials, Trainingsverläufe, Hardware, Scoredefinitionen und Aussagegrenzen auswerten lassen.
4. Validierung zur Auswahl und den abschließenden Test als getrennte Endprüfung darstellen. Synthetische Daten, unvalidiertes Training und abgebrochene Läufe ausdrücklich kennzeichnen.
5. Vorhandene SVGs verwenden oder anhand der CSVs eigene, klar beschriftete Abbildungen erstellen. Fehlende HF-, Hardware- oder Generalisierungserfolge dürfen nicht ergänzt werden.

Für reale Aufnahmen bleibt der ursprüngliche absolute RF-SNR häufig unbekannt. Das Augmentations-SNR beschreibt zusätzlich injiziertes Rauschen; Jitter wird in Sampleeinheiten angegeben. Latenz und Echtzeitfaktor gelten für den dokumentierten Rechner und Messpfad, einschließlich Batchgröße, Warmup, Featuregate und Modellladeumfang. Eine Referenznormierung ist kein universeller Umrechnungsfaktor zwischen Workstation und Laptop.

Parallele Jobzeiten dürfen nicht als Experimentwandzeit addiert werden. CUDA-Trainingsphasen und Hostphasen können unterschiedliche Zeitquellen verwenden; `phase_timing_scope` beschreibt sie. Die CPU-Benchmark-Barriere pausiert verwaltete Experimentworker, kontrolliert aber keine fremden Anwendungen. GPU-Telemetrie betrifft das gesamte Gerät und enthält gegebenenfalls andere Programme; kurze Rechenbursts können zwischen Messpunkten fehlen. CPU-App-Prozentwerte enthalten Kindworker und können vor Normalisierung über 100 % liegen. Die normalisierte App-Anzeige berücksichtigt verfügbare CPU-Kapazität einschließlich lesbarer Affinitäts-/cgroup-Grenzen.

## Programmierschnittstelle

```python
from pathlib import Path
from signalclassifier.reporting import build_report_bundle

archive = build_report_bundle(
    experiment=experiment_state,
    destination=Path("exports/experiment-report.zip"),
    events=event_records,
    dataset_inventory=queue_time_inventory,
)
```

`events` und `dataset_inventory` sind optional. Ohne explizites Inventar verwendet die Funktion ausschließlich bereits im Zustand vorhandene `config.recordings`; sie ermittelt keine aktuellen Dateisystem- oder Labeldaten. Das Manifest nennt die Inventarquelle. Das fertige ZIP ersetzt das Ziel atomar. Ein Exportfehler vor diesem Abschluss lässt ein zuvor vorhandenes Ziel unverändert.
