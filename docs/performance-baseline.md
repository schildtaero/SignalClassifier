# Ausgangsmessung vor CPU-/GPU-Parallelisierung

20. November 2026. Drei kleine, aufeinanderfolgende CPU-Versuche auf einem **AMD EPYC 9V74**, Python 3.12, PyTorch 2.15.0+cpu. CUDA war nicht verfügbar. Die Messung diagnostiziert Phasen; sie misst weder die Auslastung der Nutzer-GPU noch einen GPU-Geschwindigkeitsgewinn.

Eine anschließende Kontrolle der Entwicklungsumgebung ergab `cpu.max = 200000 100000` (zwei CPU-Kapazitäten), `memory.max = 8589934592` (8 GiB) und drei per Affinität verfügbare logische CPUs. Hostangaben allein beschreiben diese Containergrenzen nicht. Die neue automatische Ressourcenplanung berücksichtigt lesbare cgroup-v2-Grenzen zusätzlich zu Host und Affinität.

HEAD zu Messbeginn: `20f014860b35a109d8a7225f2f30c29c168ab550`. Der verwendete Python-Quellstand wurde vor parallelen Entwicklungsänderungen in ein temporäres Verzeichnis kopiert. SHA-256 von `ml.py`: `fb7b257a8b83ae1a9200ad92bcf81817907e72ceb5b7b750de16934fe3f5985c`. Weitere Quellfingerprints, Hardware, Konfiguration, cProfile-Auszüge und Phasenwerte stehen im [Messartefakt](performance-baseline-2026-11-20.json).

Gleiche Vorgaben: 48 synthetische Segmente je Klasse, 2048 IQ-Samples, 3 Epochen, Batchgröße 32, SNR −5/5/15 dB, Jitter 0/0,1 Samples; unabhängige Trainings-/Validierungs-/Testgruppen. Daraus entstehen 56 Trainingssegmente beziehungsweise 112 Beispiele einschließlich unverrauschter Varianten. Torch-/Optimizer-Importe wurden vorgewärmt. Der Diagnoselauf umfasst den regulären abschließenden Test; ein Optimierungs-Trial überspringt diesen weiterhin.

| Modell | Gesamt / s | Training / s | `_dataset` gesamt / s | CPU-Klassifikation / s |
| --- | ---: | ---: | ---: | ---: |
| Feature-LDA, 22 Parameter | 0,512 | 0,00067 | 0,405 | 0,033 |
| Feature-MLP 32, 1474 Parameter | 0,683 | 0,081 | 0,415 | 0,048 |
| IQ-CNN 16, 3922 Parameter | 0,770 | 0,361 | 0,178 | 0,080 |

„Training“ ist die im Runner gemessene Trainingsphase einschließlich Modell-/Optimizer-Aufbau und Epochenvalidierung, keine isolierte Kernelzeit. `_dataset` enthält Vorverarbeitung und die SNR-/Jitter-Prüfraster. Seine Zeit überschneidet sich daher mit der separat im Experimentbericht ausgewiesenen Vorverarbeitung und darf nicht nochmals addiert werden. Die letzte Spalte summiert drei Aufwärmaufrufe und 20 CPU-Klassifikationen einschließlich des ersten Modellladens. cProfile und Messwrapper erzeugen zusätzlichen Aufwand; diese Zeiten dürfen keine produktiven p95-Ranglisten ersetzen. Je Topologie wurde genau ein kleiner Lauf gemessen, ohne Kontrolle anderer Hostlast.

Beim MLP entfallen etwa **12 %** auf Training und **61 %** auf `_dataset`; dabei werden 430 Signalmerkmalsvektoren berechnet. Allein die sechs Validierungsrasterpunkte benötigen rund 0,136 s, die sechs Testpunkte 0,132 s. Beim kleinen CNN entfallen etwa **47 %** auf Training, **23 %** auf `_dataset` und **10 %** auf die CPU-Klassifikationsmessung. LDA ist ein CPU-Verfahren. Kleine Netze mit vier Batches pro Epoche beschäftigen eine GPU nur kurz; eine niedrige Gesamtanzeige allein belegt deshalb keinen Treiberfehler.

## Mikromessung der Vorverarbeitungs-Threads

Der inzwischen integrierte deterministische `_dataset`-Pfad wurde separat mit 128 und 512 Segmenten zu je 2048 Samples gemessen. Gleiche Quell-IQ, Seeds und Kanalaugmentationen; ein Aufwärmdurchlauf und drei Messungen pro Zelle, wechselnde Reihenfolge von 1/2/4 Threads. **Alle Ergebnisarrays und Labels waren bitgleich.** Dieser Modus nutzt kein cProfile. Er misst Taskplanung, Augmentation, Darstellung, Poolaufbau/-abbau und Zusammenfügen; Signalerzeugung und Training liegen außerhalb. OpenBLAS und OMP waren auf jeweils einen Thread begrenzt. Andere Hostlast wurde nicht kontrolliert.

| Segmente | Darstellung | 1 Thread / ms | 2 Threads / ms | 4 Threads / ms |
| ---: | --- | ---: | ---: | ---: |
| 128 | Signalmerkmale | 137 | 129 | 164 |
| 128 | IQ | 63 | 80 | 97 |
| 128 | Spektrogramm | 84 | 99 | 133 |
| 512 | Signalmerkmale | 624 | 640 | 745 |
| 512 | IQ | 258 | 369 | 433 |
| 512 | Spektrogramm | 333 | 440 | 566 |

Die Werte sind Mediane. Der kleine Vorteil von zwei Threads bei 128 Merkmalssegmenten ist angesichts der Streuung kein belastbarer allgemeiner Geschwindigkeitsgewinn; bei 512 Segmenten verschwindet er. Vier Threads sind in allen Zellen langsamer. Deshalb ist **ein Vorverarbeitungs-Thread als automatischer Standard** für diesen Arbeitsumfang begründet; zwei oder vier Threads bleiben eine ausdrücklich messbare Option. Daraus folgt keine Aussage gegen separate CPU-/GPU-Workerprozesse oder größere Segmente. Die vollständigen Einzelmessungen, Konfiguration und Quellfingerprints stehen im [Thread-Messartefakt](performance-dataset-workers-2026-11-20.json). Ein Featurecache wurde nicht gebaut.

## Praktische Konsequenzen

- CPU-Vorverarbeitung und GPU-Training können sich überlappen. Ein begrenzter CPU-Worker und ein GPU-Worker sind ein nachvollziehbarer Einstieg; Datenwarteschlange, Featurecache und VRAM dürfen nicht unbegrenzt wachsen.
- Worker sollten getrennte Prozesse mit `spawn` verwenden. NumPy-/Torch-Zufallszustände, Torch-Threadzahl, Inferenzcache und CUDA-Allocator sind Prozesszustand. Threads mit gleichzeitigen globalen Seed-/Threadänderungen gefährden die Reproduzierbarkeit.
- Nur rohe, nicht mit globaler Normalisierung angepasste Darstellungen wiederverwenden. Cache-Schlüssel müssen verwendete IQ-Samples, Segmentierung, Eingabedarstellung, Augmentierung und Seed unterscheiden. Die Trainingsnormalisierung bleibt pro Trial; Testdaten bleiben bis zur festgelegten Gewinnerauswertung unangetastet.
- Die für Score und Live-Empfehlung relevante CPU-p95-Messung nach einer internen Worker-Barriere ausführen. Eine Messung unter konkurrierender CPU-Trainingslast als solche kennzeichnen und nicht mit isolierten Ranglistenwerten vermischen.
- Im Ausgangscode erzwingt `loss.detach().cpu()` eine Synchronisation je CUDA-Minibatch. Verluste können zunächst auf dem Gerät gesammelt und einmal pro Epoche protokolliert werden. Unveränderte Validierungsdaten sollten bei passendem VRAM-Budget einmal übertragen werden. Auch Vorhersageergebnisse und beste Gewichte erzeugen beim Zurückkopieren Synchronisationen. Siehe den [PyTorch Performance Tuning Guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide) und die [CUDA-Semantik](https://docs.pytorch.org/docs/stable/notes/cuda).
- Abbruch und Zeitbudget müssen Worker und Warteschlange kooperativ erreichen; bereits abgeschlossene Trialberichte bleiben erhalten. Pro Worker Seed, Threads, tatsächliches Gerät und Phasenzeiten protokollieren. End-to-End-Durchsatz und Modellqualität sind aussagekräftiger als ein möglichst hoher GPU-Prozentwert.

## Windows-/WSL-Anzeige

Im Task-Manager die Bezeichnung der angezeigten Engine prüfen: Ein **3D**-Diagramm ist keine isolierte CUDA-Messung. Je nach Treiber lassen sich passende Compute-/CUDA-Engines auswählen; Engineauslastung und reservierter Grafikspeicher sind unterschiedliche Größen. Microsoft beschreibt die Auswahl und Zusammenfassung der Engines in [GPUs in the Task Manager](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/).

Unter WSL kann `nvidia-smi` unter `/usr/lib/wsl/lib/nvidia-smi` liegen. NVIDIA dokumentiert Einschränkungen bestimmter NVML-Abfragen, darunter Auslastungs- und Prozessinformationen. Fehlende Messwerte daher als nicht verfügbar behandeln. Maßgeblich für die Anwendung bleiben das tatsächlich gewählte Torch-Gerät sowie gemessene Phasen und Durchsatz. Siehe [CUDA on WSL User Guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).

## Lokaler CPU-Referenzlauf in der Oberfläche

**Referenz messen** verwendet 4096 synthetische Samples, drei Aufwärmläufe und den Median aus zwölf Aufrufen der Merkmalsextraktion auf der CPU, dem Hauptprozessor (*Central Processing Unit*). Das ist kein GPU-Test; ein kleinerer Millisekundenwert ist schneller. Eine Workstation mit älterem Xeon kann bei dieser kleinen Aufgabe pro Kern langsamer sein als ein neuerer Laptopprozessor. Softwareversionen, Energiesparmodus, WSL-CPU-Zuteilung und gleichzeitige Last beeinflussen den Vergleich.

Seit 0.5.2 erzwingt jeder Klick eine neue Messung mit `GET /api/benchmark?refresh=true`. Der bisherige Aufruf ohne `refresh` kann den letzten Wert derselben Anwendungssitzung weiterverwenden. Messzeit sowie laufender Empfang und laufende Experimente werden mit ausgegeben. Für einen Vergleich Empfang und Experimente auf beiden Rechnern stoppen und dieselbe Software verwenden. Die Experimentworker koordinieren ihre eigenen CPU-Rangmessungen; Live-Empfang, automatische Empfangsanalyse und andere Programme sind damit nicht vollständig isoliert. Der Referenzwert allein belegt weder einen Treiberfehler noch die Echtzeitfähigkeit eines Modells.

## Wiederholen

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python \
  scripts/benchmark_experiments.py --output /tmp/signalclassifier-profile.json
```

Mit `--source-root /pfad/zum/eingefrorenen/projekt` kann ein vorhandener, unveränderter Quellstand profiliert werden. Modelle entstehen nur temporär und werden anschließend entfernt. Das Skript öffnet keine SDR-Hardware und verwendet ausschließlich die CPU.

Die enge Thread-Matrix wiederholen:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python \
  scripts/benchmark_experiments.py --dataset-workers \
  --output /tmp/signalclassifier-dataset-workers.json
```
