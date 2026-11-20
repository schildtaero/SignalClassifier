# CPU-/GPU-Ausführung und Leistungsdiagnose

Ein Experiment behält einen Coordinator. Unabhängige Trainingsjobs laufen in begrenzten, persistenten `spawn`-Prozessen. Mehrere ausgewählte Topologien bilden die parallele Ebene; bei einer einzelnen Topologie mit automatischer Hyperparameteroptimierung bilden deren Versuche die parallele Ebene. Innerhalb eines bereits gestarteten Trainingsworkers entsteht kein weiterer Trainingspool. Ein einzelnes festes Netz wird dadurch nicht automatisch auf mehrere GPUs verteilt.

## Automatisch und manuell steuern

Im Setup unter **Ressourcen / Parallelisierung** stehen `auto`, `serial` und `parallel` zur Wahl. Nullwerte bedeuten automatische Auflösung:

```json
{
  "execution": {
    "mode": "auto",
    "max_workers": 0,
    "cpu_threads": 0,
    "preprocessing_workers": 0,
    "gpu_workers": 0
  }
}
```

| Einstellung | Bereich | Geltungsbereich |
| --- | --- | --- |
| `max_workers` | 0 oder 1–8 | Gleichzeitig aktive Trainingsprozesse insgesamt |
| `gpu_workers` | 0 oder 1–4 | Gleichzeitig zugelassene CUDA-Trainingsworker auf dem gewählten Gerät |
| `cpu_threads` | 0 oder 1–32 | Torch-/Bibliotheksbudget je Trainingsworker |
| `preprocessing_workers` | 0 oder 1–8 | Darstellung/Augmentation je Trainingsworker |

Die Auflösung berücksichtigt CPU-Affinität, lesbare cgroup-v2-Quoten, aktuell verfügbaren RAM, sichtbaren VRAM und Zahl der Jobs. Ein CPU-Anteil und Speicherreserven bleiben für Coordinator, Oberfläche und andere Arbeit. Angeforderte Werte können daher begrenzt werden; angefragter und tatsächlicher Plan samt Gründen werden gespeichert. Unter WSL gelten die der VM bereitgestellten Ressourcen, nicht automatisch der gesamte Windows-RAM oder alle physischen Kerne.

Auto lässt bei ausreichendem freien VRAM konservativ bis zu zwei GPU-Worker zu. RAM-/VRAM-Werte sind Zulassungsschätzungen und keine garantierten Modellpeaks. Mit `device: "cuda"` werden neuronale Jobs auf CUDA beschränkt; Feature-LDA bleibt CPU. Bei `device: "auto"` können zusätzliche CPU-Jobs verwendet werden. Jede tatsächliche Zuordnung steht im Workerfortschritt und Modellbericht.

Bei gemischten CPU-/CUDA-Trials hängt der mit fünf Prozent gewichtete Trainingskostenanteil auch von der Gerätezuweisung ab. Für einen Vergleich auf festem Trainingsgerät `cpu` oder `cuda` ausdrücklich wählen; die Balanced-Accuracy-/Macro-F1-Ziele bleiben von diesem Laufzeitanteil getrennt.

Persistente Slots sparen wiederholte Python-/Torch-Imports. Startup, Jobzeit und Gesamtwandzeit sind getrennt. Seed, Threadbudget, Gatezustand und Jobkontext werden je Versuch zurückgesetzt; Modell-/Tensorcaches werden beim Jobabschluss freigegeben. Getrennte Prozesse verhindern, dass parallele Versuche globale NumPy-/Torch-Zufallszustände verändern. Threadvariablen werden vor dem Prozessstart für das Kind gesetzt und im Parent wiederhergestellt.

Eine erste Lazy-Initialisierung des Optimizers oder Modellbackends kann zusätzlich im ersten Job liegen; sie wird nicht rückwirkend als Training aller weiteren Jobs gezählt. Eine Lern-/Trainingsphase ist damit nicht automatisch eine isolierte CUDA-Kernelzeit. `training_wall_seconds` und `managed_training_pause_seconds` trennen beobachtete Wandzeit und verwaltete Benchmarkpausen; der Trainingsanteil des Scores verwendet die um diese Pausen bereinigten `training_seconds`.

Mehr Vorverarbeitungsthreads sind ausdrücklich auswählbar. Der automatische Standard bleibt **ein Thread je Worker**, da die [Mikromessung](performance-baseline.md) bei den untersuchten IQ-/Spektrogramm-/Featuregrößen keinen allgemeinen Vorteil zusätzlicher Threads zeigte. Parallelität zwischen Jobs kann trotzdem CPU-Vorbereitung und GPU-Training überlappen.

## Vergleichbare Messung und Abbruch

Für den CPU-p95-Anteil des Scores und den Hardware-Referenzbenchmark werden andere verwaltete Experimentworker an kooperativen Fortschrittspunkten pausiert. Eine gemeinsame POSIX-Sperre erlaubt die Messung erst nach Freigabe ihrer Rechenphasen. Externe Programme, Liveempfang und manuelle Klassifikationen liegen außerhalb dieser Sperre; ein vollständig ruhender Host wird nicht behauptet.

Wartezeiten und aktive Rechenphasen bleiben unterscheidbar. CUDA-Trainings-/Validierungsintervalle verwenden Geräteereignisse; Hostphasen verwenden Wandzeit. Die Verlustwerte werden auf dem Gerät gesammelt und einmal pro Epoche übernommen. Validierungstensoren werden einmal übertragen und in begrenzten Batches ausgewertet. Beste Gewichte bleiben bei ausreichender Reserve auf dem Gerät; ein dokumentierter CPU-Checkpointpfad bleibt verfügbar.

Abbruch und Suchzeitbudget erreichen laufende Worker an Fortschrittspunkten. Native Operationen sind nicht beliebig mitten in einer Berechnung unterbrechbar. Beim Beenden werden Worker und Warteschlange aufgeräumt. Eine abgestürzte Worker-PID hält keine permanente POSIX-Sperre zurück; der Coordinator kann den Slot freigeben. Vorliegende Trialberichte und Ereignisse bleiben erhalten. Herkunftsgruppen, nur auf Training angepasste Normalisierung und die Abschottung von Test-IQ während der Suche gelten auch parallel.

Reagiert ein Worker nicht auf den kooperativen Abbruch, kann der Supervisor den Prozess nach einer Schonfrist beenden. Ein dadurch unvollständiger Versuch ist kein gültiger Gewinner und kann unvollständige temporäre Modellartefakte hinterlassen; abgeschlossene Trialberichte bleiben verfügbar.

## Was die Oberfläche misst

- **CPU System:** systemweit sichtbare Auslastung.
- **CPU App + Worker:** Prozessbaum einschließlich Kindworker. Der rohe Wert zählt einen voll beschäftigten logischen Kern als 100 % und kann darüber liegen. Der normalisierte Wert berücksichtigt die verfügbare CPU-Kapazität einschließlich lesbarer Prozessgrenzen.
- **GPU/VRAM:** `nvidia-smi` für das gesamte NVIDIA-Gerät, einschließlich anderer Programme. Unter WSL können einzelne Abfragen fehlen. Unbekannt, Aufwärmphase und veraltete Messung bleiben von echten 0 % getrennt.
- **Aktuelle Worker:** tatsächlich gestartete Prozesse mit Topologie, Trial, Phase, Gerät und PID. Die Zahl ausgewählter Modelle ist keine aktive Workerzahl.

Die Telemetrie läuft in einem eigenen Hintergrundthread, standardmäßig alle zwei Sekunden. HTTP-Statusabfragen verwenden den Cache. Während eines Experiments gelangen Messpunkte ins strukturierte Journal; der Bericht enthält zusätzlich Mittel-/Maximalwerte mit Beobachtungszahlen. Kurze GPU-Bursts können zwischen den Messpunkten liegen. Ein Windows-Task-Manager-Graph mit der Bezeichnung „3D“ ist keine Messung ausschließlich des CUDA-Trainings.

Für Vergleiche denselben Datensatz, Suchraum, Seed, Epochen und Batchgrößen verwenden. Zunächst seriell und danach mit zwei Workern messen: Gesamtwandzeit, abgeschlossene Versuche pro Minute, Phasen, Pausen und Speicherbedarf sind aussagekräftiger als ein möglichst hoher Prozentwert. Die Trainingsgröße wird durch Parallelisierung nicht heimlich erhöht. Das [Berichtspaket](berichtsexport.md) enthält Ausführungsplan, Job-/Phasenzeiten und `resource_usage.csv` mit den Beobachtungen. Eine reale Beschleunigung auf der Nutzer-GPU muss dort gemessen werden; die Entwicklungsumgebung hat keine CUDA-GPU.
