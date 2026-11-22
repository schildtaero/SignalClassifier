# Paper-Daten prüfen und optional auf GitHub veröffentlichen

SignalClassifier kann nach einem Experiment ein **eigenes Veröffentlichungspaket** auf GitHub ablegen. Es enthält die ausgewählten wissenschaftlichen Daten für einen späteren Artikel: Versuchsstruktur, Topologien, Ergebnisse und ihren Rechenkontext. Es ist weder ein fertiger Fachartikel noch ein automatisch erzeugtes Dokument im *Portable Document Format* (**PDF**).

Die Veröffentlichung ist **für jedes Experiment standardmäßig ausgeschaltet**. Der vollständige lokale [Berichtsdatenexport](berichtsexport.md) bleibt davon unabhängig. Dieser private Bericht enthält auch ausführliche Herkunftsangaben, Notizen und Fehlerprotokolle und wird nicht als Ganzes zu GitHub hochgeladen.

## Verwendung in der Oberfläche

1. Im Tab **Experimente** den Versuch wie üblich einrichten. Nur wenn dieser konkrete Lauf veröffentlicht werden soll, **Paper-Daten und Ergebnisse veröffentlichen** aktivieren.
2. Zielrepository und Zielbranch prüfen. Ein Repository ist das GitHub-Projekt; ein Branch ist ein eigener Versionszweig darin. Vorgaben sind `schildtaero/SignalClassifier` und `experiment-results`.
3. Das Experiment ausführen. Bei eingeschalteter Veröffentlichung werden nach erfolgreichem Abschluss, Fehler oder Benutzerabbruch die jeweils vorhandenen Ergebnisse in eine getrennte Uploadwarteschlange gelegt. Fehlgeschlagene oder abgebrochene Läufe werden dabei ausdrücklich als unvollständig ausgewiesen.
4. Für einen beendeten Lauf **Paper-Daten lokal herunterladen** und ansehen. Dieser Download funktioniert auch bei ausgeschaltetem Upload und ohne GitHub-Zugangsdaten. Er dient zur Kontrolle genau des aufbereiteten Veröffentlichungspakets; er aktiviert keine Veröffentlichung.
5. Den getrennten Publikationsstatus beobachten. Bei einem Fehler Zugang oder Verbindung berichtigen und **Veröffentlichung wiederholen** wählen. Das Experiment muss dafür nicht neu trainiert werden, sofern seine für den Export benötigten lokalen Berichte noch vorhanden sind und die Veröffentlichung für diesen Lauf aktiviert wurde.

Das Opt-in wird in der Experimentkonfiguration gespeichert. Ein deaktivierter Lauf erhält durch die bloße Einrichtung eines Tokens oder einen Klick auf den lokalen Download keinen Uploadauftrag. Ein fehlendes Token verhindert das Training nicht; es wird bei aktivierter Veröffentlichung als Hinweis angezeigt und der spätere Upload kann scheitern.

Bei eingeschalteter Automatik beginnt die Veröffentlichung nach Laufende ohne zusätzlichen Freigabedialog. Für eine ausschließlich lokale Prüfung den Upload beim Anlegen des Experiments ausgeschaltet lassen; der lokale Download ist kein weiterer Bestätigungsschritt vor einem bereits freigegebenen automatischen Upload.

## Welche Daten veröffentlicht werden

Das Paket wird **aus ausdrücklich erlaubten Feldern neu aufgebaut**. Der Export kopiert weder das private Berichtsarchiv noch einen beliebigen Experimentordner. Frei hinzugefügte Berichtsfelder werden nicht automatisch Bestandteil der Veröffentlichung.

Der lokale Download ist ein komprimiertes ZIP-Archiv. Die Dateien mit `.json` verwenden **JSON**, *JavaScript Object Notation*, ein Textformat für strukturierte Daten. `README.md` verwendet Markdown, ein mit gewöhnlichen Textprogrammen lesbares Format mit einfacher Auszeichnung für Überschriften und Tabellen.

| Datei | Inhalt |
| --- | --- |
| `README.md` | Kurze Beschreibung des Pakets, seines Formats und seiner Aussagegrenzen. |
| `experiment.json` | Aufbereitete Versuchsparameter, begrenzte Topologiebeschreibungen, vorhandene Metriken, getrennte Validierungs-/Testwerte, Trainingsverläufe und Optimierungsresultate sowie freigegebene Software-/Hardwareangaben. |
| `dataset.json` | Aufbereitete Datensatz- und Gruppeninformationen mit experimentbezogen pseudonymisierten Kennungen und eindeutig bezeichneten verfügbaren Prüfwerten. |
| `events-summary.json` | Begrenzte, strukturierte Zusammenfassung des Ablaufs; keine rohen Ereignistexte, Fehlermeldungen oder vollständigen lokalen Logs. |
| `manifest.json` | Beschreibung des Paketformats sowie Dateigrößen und Prüfwerte der vier anderen Dateien; das Manifest hasht sich nicht selbst. |

„Topologie“ bezeichnet den Aufbau eines Klassifikators, beispielsweise Eingabedarstellung, Schichten und einstellbare Parameter. Sie ist von den im Training erlernten Gewichten zu unterscheiden. Metriken sind Kennzahlen wie Genauigkeit, Trainingsdauer, Auswertungszeit und Modellgröße. Eine Hyperparameteroptimierung sucht nach geeigneten Trainingsparametern; ihre vorhandenen Suchversuche und Zahlenverläufe können im Paket beschrieben werden. Die Kurvendaten werden als strukturierte Zahlen gespeichert, nicht als behauptete zusätzliche Messung.

Die öffentliche Topologiebeschreibung enthält bekannte Katalogkennungen oder ein Pseudonym, die Modellfamilie, die Eingabedarstellung und die erlaubten Zahlenparameter `width`, `depth`, `kernel`, `heads` und `dropout`, soweit vorhanden. Diese beschreiben Breite und Tiefe des Netzes, Filtergröße, Zahl paralleler Aufmerksamkeitsfunktionen und den Anteil im Training zufällig deaktivierter Einheiten. Frei formulierte Namen, Schichtbeschreibungen und Begründungstexte werden nicht kopiert. Die entsprechende Softwareversion und erlaubte Quellcodeprüfwerte helfen, einen bekannten Modellaufbau einzuordnen.

Validierung und abschließender Test bleiben getrennt. Der Export berechnet keinen nachträglichen Gewinner anhand von Testwerten. Bei einem nur trainierten, unbewerteten Modell werden keine Genauigkeit oder unabhängigen Qualitätswerte ergänzt. Für einen fehlgeschlagenen Lauf können lediglich Konfiguration, Teilresultate und ein unvollständiger Status vorliegen. Leere Felder sind keine gemessenen Nullwerte.

Fehler erscheinen höchstens als strukturierte Kategorien, beispielsweise Zeitbudgetüberschreitung oder fehlendes Artefakt. Für den genauen ursprünglichen Fehlertext dient weiterhin der lokale private Bericht.

Manuelle Parametervergleiche behalten die getesteten Kandidaten und ihre Zahlenverläufe. Bei automatischer Hyperparametersuche stehen die vollständigen Versuchsdaten unter `optimization.trials`; die Kandidatenliste verweist über `optimization_trial` auf die dortige Versuchsnummer. Dadurch werden identische Kurvendaten nicht doppelt gespeichert. Verfügbare CPU-/GPU-Ressourcenwerte werden als typisierte Zahlenreihen mit relativer Zeit und begrenzten Zusammenfassungen übernommen. Unbekannte Werte bleiben unbekannt; die GPU-Werte betreffen das ganze Gerät einschließlich anderer Programme. Aufnahmefrequenzen bleiben als numerischer Versuchskontext erhalten, Standort- und Freitextangaben werden nicht übernommen.

Namen und Kennungen von Aufnahmen und Ursprungsgruppen werden mit **SHA-256**, dem *Secure Hash Algorithm mit 256 Bit*, experimentbezogen pseudonymisiert. So lässt sich innerhalb eines Laufs nachvollziehen, welche Ausschnitte zur gleichen Ursprungsgruppe gehören, ohne den ursprünglichen Gruppennamen zu veröffentlichen. Frei benannte Klassen und Modellkennungen erhalten ebenfalls Pseudonyme; die Zuordnung zu ihren ursprünglichen Namen wird nicht veröffentlicht. Bekannte Klassencodes wie Amplitudenmodulation (**AM**), Frequenzmodulation (**FM**) oder die im Katalog freigegebenen Protokollnamen bleiben lesbar. Bekannte Varianten werden dabei nicht nachträglich zu einer gemeinsamen Elternklasse zusammengefasst.

Pseudonymisierung bedeutet, dass eine Kennung ersetzt wird. Sie ist kein Nachweis vollständiger Anonymisierung, physisch unabhängiger Aufnahmen oder korrekter Labels. Bei frei benannten Klassen bleibt die statistische Struktur sichtbar, ihre ursprüngliche Bedeutung lässt sich aus dem Paket aber nicht ablesen.

Vorhandene Datenprüfwerte werden mit ihrem Geltungsbereich übernommen. Ein Prüfwert für Metadaten oder einen Dateistatistik-Fingerprint ist kein Nachweis, dass die gesamten **IQ-Daten**, die digitalen Inphase- und Quadraturkomponenten des Funksignals, inhaltlich gehasht oder veröffentlicht wurden. Die Herkunft einer Aufnahme bleibt eine Deklaration; Aufnahmemetadaten beweisen keinen realen oder authentifizierten Funkempfang.

**Nicht enthalten sind** Roh-IQ-Aufnahmen, trainierte Modellgewichte, Rohmetadaten im *Signal Metadata Format* (**SigMF**), absolute lokale Dateipfade, Freitextnotizen, rohe Ereignis-/Fehlertexte und Zugangsdaten. Das öffentliche Paket enthält daher nicht alles, was für eine exakte Wiederholung des Trainings nötig ist. Originalaufnahmen und Modellpakete werden weiterhin separat und gezielt archiviert.

Das Paket umfasst genau die fünf genannten Dateien. Die Größenprüfung erlaubt höchstens **8 Mebibyte (MiB) pro Datei und 16 MiB insgesamt**, jeweils anhand der unkomprimierten Dateibytes; ein MiB entspricht 1 048 576 Bytes. Eine Überschreitung erzeugt einen Export-/Publikationsfehler und verändert das Trainingsergebnis nicht.

Zusätzlich ist der Datenumfang innerhalb der erlaubten Struktur begrenzt:

| Inhalt | Höchstzahl in der Projektion |
| --- | --- |
| Modellsichten und Klassen je Liste | jeweils 64 |
| Aufnahmen und Ursprungsgruppen je Trainings-/Validierungs-/Testliste | jeweils 1000 |
| Optimierungsversuche je Modell | 50 |
| Punkte je Trainingsverlauf | 100 |
| Zellen je Raster von Signal-Rausch-Verhältnis und zeitlicher Abweichung | 128 |
| Eingelesene Ablaufereignisse | 100.000 |
| Numerische Fortschrittspunkte aus diesen Ereignissen | 10.000 |
| Ressourcenmesspunkte aus diesen Ereignissen | 10.000 |

Von der Projektion gezählte Auslassungen stehen in `experiment.json` unter `omitted_counts`. Das öffentliche Paket ist eine begrenzte Ergebnissicht; der private Bericht bleibt für ausführlichere Originaldaten maßgeblich.

## Zugangsdaten unter Linux und Windows mit WSL

Das Backend, also der lokal laufende Anwendungsprozess, liest ein GitHub-Token ausschließlich aus einer dieser Umgebungsvariablen:

- `SIGNALCLASSIFIER_GITHUB_TOKEN`
- `GITHUB_TOKEN`
- `GH_TOKEN`

Ein Token ist ein Zugangsschlüssel. Es wird nicht in der Oberfläche eingegeben und gehört weder in das Repository noch in Experimentnotizen oder Konfigurationsdateien. Der Tokenstatus in der Oberfläche zeigt nur, ob eine Umgebungsvariable konfiguriert ist; er prüft damit noch keine gültige GitHub-Berechtigung.

Es wird die erste nichtleere Variable in der oben angegebenen Reihenfolge verwendet.

Auf GitHub einen **Fine-grained Personal Access Token**, einen auf ausgewählte Projekte begrenzten persönlichen Zugangsschlüssel, für das gewünschte Repository einrichten. Die Repositoryberechtigung **Contents: Read and write** ist erforderlich. Eine Organisationsfreigabe oder Branchschutzregel kann den Zugriff zusätzlich einschränken. Kein Tokenwert wird für die Veröffentlichung in Dateien gespeichert.

Unter **WSL**, *Windows Subsystem for Linux*, diese Schritte in einer Bash-Shell innerhalb der Linux-Umgebung ausführen. Das Token verdeckt einlesen und die Anwendung in derselben Shell starten:

```bash
read -rsp 'GitHub-Token: ' SIGNALCLASSIFIER_GITHUB_TOKEN
printf '\n'
export SIGNALCLASSIFIER_GITHUB_TOKEN
./start.sh
```

Bei dieser Eingabe steht der Tokenwert nicht im Shellbefehl und wird deshalb nicht als Teil dieses Befehls in der Shell-Historie abgelegt. Die Variable gilt für die gestartete Anwendung und deren Prozessumgebung. Eine bereits laufende Anwendung übernimmt keine nachträgliche Änderung aus einem anderen Terminal: Für neu gesetzte Zugangsdaten das Backend nach Ende laufender Arbeit in der vorbereiteten Shell neu starten.

Nach Beenden der Anwendung kann die Variable aus der aktuellen Shell entfernt werden:

```bash
unset SIGNALCLASSIFIER_GITHUB_TOKEN
```

## Zielbranch, Upload und Wiederholung

Standardmäßig entsteht ein Ergebnisverzeichnis nach diesem Muster:

```text
Repository: schildtaero/SignalClassifier
Branch:     experiment-results
Verzeichnis: paper-results/<experiment-id>/
```

Die Experimentkennung ist die lokale, 32-stellige hexadezimale Laufkennung. Unterschiedliche Experimente erhalten unterschiedliche Verzeichnisse. Ein gegebenenfalls fehlender Zielbranch wird vom aktuellen Standardbranch des Repositorys abgezweigt. Als Uploadziel werden `main`, `master` und der tatsächlich konfigurierte Standardbranch abgelehnt. Der Standardbranch und die lokale Quellcode-Arbeitskopie werden nicht verändert.

Wer die Ergebnisse lesen kann, bestimmen die Zugangseinstellungen des Zielrepositorys. Die Veröffentlichungsfunktion ändert dessen Sichtbarkeit nicht.

Die Anwendung verwendet die GitHub-**API**, das *Application Programming Interface* beziehungsweise die Programmschnittstelle. Sie übermittelt die fünf Paketdateien und erstellt einen gemeinsamen Commit, also einen versionierten Änderungssatz. Erst anschließend wird die Branchreferenz ohne erzwungenes Überschreiben aktualisiert. Dadurch erscheint kein halb hochgeladenes Paket als neuer sichtbarer Branchstand. Vorhandene andere Repositoryinhalte bleiben erhalten; es wird kein beliebiger lokaler Arbeitsordner mit `git push` veröffentlicht.

Jede Anfrage hat ein Zeitlimit von 15 Sekunden, der gesamte Upload ein Budget von 120 Sekunden und höchstens 40 Programmschnittstellenaufrufen. Ändert jemand den Zielbranch während des Uploads, kann die Anwendung die Aktualisierung höchstens zweimal neu versuchen. Es erfolgt kein Force-Push. Identischer bereits vorhandener Paketinhalt wird erkannt, statt unnötig eine weitere identische Version zu erzeugen. Das hilft auch bei einer Netzwerkunterbrechung, bei der ein vorheriger Änderungssatz bereits angekommen sein könnte.

Upload und Experiment besitzen getrennte Zustände. Der Publikationsstatus kann `disabled`, `queued`, `publishing`, `published`, `failed` oder `interrupted` lauten: ausgeschaltet, vorgemerkt, in Übertragung, veröffentlicht, fehlgeschlagen oder unterbrochen. Bei Erfolg werden unter anderem Ziel, Commitkennung und Link zurückgegeben. Ein Uploadfehler macht aus einem erfolgreichen Experiment kein fehlgeschlagenes Experiment und entfernt keine lokalen Modelle oder Berichte.

Beim Neustart werden noch vorgemerkte beziehungsweise laufende Uploads als unterbrochen markiert. Sie werden **nicht blind automatisch fortgesetzt**. Nach Kontrolle des Zustands kann ein bereits freigegebener Lauf ausdrücklich erneut veröffentlicht werden. Die Wiederholung verwendet die freigegebenen Paper-Daten, nicht den privaten Bericht. Ein bereits als veröffentlicht gespeicherter Auftrag wird nicht erneut in die Uploadwarteschlange gelegt.

## Lokale Programmschnittstellen

Die Oberfläche verwendet diese Endpunkte. `{id}` steht jeweils für eine vorhandene Experimentkennung.

| Methode und Pfad | Wirkung |
| --- | --- |
| `GET /api/publication/status` | Auskunft über konfigurierte Zugangsdaten ohne deren Wert. |
| `GET /api/experiments/{id}/paper-bundle` | Aufbereitetes Paper-Paket als ZIP herunterladen; für beendete Läufe auch ohne Opt-in und ohne Token. |
| `GET /api/experiments/{id}` | Experimentzustand einschließlich getrenntem `publication`-Status lesen. |
| `POST /api/experiments/{id}/publish` | Upload eines beendeten, zuvor freigegebenen Experiments anstoßen beziehungsweise nach einem Fehler wiederholen. |
| `GET /api/experiments/{id}/report-bundle` | Separaten vollständigen privaten Berichtsdatenexport herunterladen. |

Die beim Anlegen eines Experiments mitgespeicherte Teilkonfiguration lautet:

```json
{
  "github_publication": {
    "enabled": true,
    "repository": "schildtaero/SignalClassifier",
    "branch": "experiment-results"
  }
}
```

Dieser Ausschnitt gehört zur vollständigen Experimentkonfiguration und ersetzt nicht die übrigen Trainingsangaben. Ohne ausdrückliches `enabled: true` bleibt die Veröffentlichung aus. Das Paket ist die für GitHub vorgesehene, begrenzte Sicht auf einen Lauf; die wissenschaftliche Interpretation und ein späterer Fachartikel müssen seine tatsächlichen Daten und Aussagegrenzen weiter berücksichtigen.
