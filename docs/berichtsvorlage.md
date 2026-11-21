# Bestätigte Berichtsvorlage für SignalClassifier

Vorlagenversion: **1.1.0** · Bestätigt am **21.11.2026** · Sprache: Deutsch

Diese Vorlage legt die wiederzuverwendende Gliederung wissenschaftlicher PDF-Berichte fest. Die zehn nummerierten Hauptkapitel und ihre Titel bleiben unverändert. Unterabschnitte richten sich nach den tatsächlich vorhandenen Daten. Deckblatt und Inhaltsverzeichnis dürfen vorangestellt werden; sie sind keine zusätzlichen Hauptkapitel. Die maschinenlesbare Entsprechung steht in [berichtsgliederung.json](berichtsgliederung.json).

Auf dem Deckblatt stehen Berichtstitel, betrachtete Experiment-IDs, Datenstand mit Zeitzone, Softwareversion und Berichtsversion als Platzhalter: `{{berichtstitel}}`, `{{experiment_ids}}`, `{{datenstand_utc}}`, `{{softwareversion}}`, `{{berichtsversion}}`. Laufstatus, Datenherkunft und Umfang der unabhängigen Bewertung gehören außerdem in eine kurze Zusammenfassung vor Kapitel 1. Diese Vorlage enthält keine privaten Experimentdaten.

Für jeden Folgebericht gelten diese Regeln:

- Für Menschen mit guter Allgemeinbildung schreiben; keine Promotion, Spezialkenntnisse in Funktechnik oder Erfahrung mit maschinellem Lernen voraussetzen. Zuerst die fachliche Frage und praktische Bedeutung erklären, danach Methode und Details.
- Jede fachliche Abkürzung bei der ersten Verwendung im Fließtext ausschreiben und ihre Funktion kurz erklären. Beispielsweise: „Faltungsnetz (englisch Convolutional Neural Network, CNN), das wiederkehrende lokale Muster erkennt“ oder „rauscharmer Vorverstärker (englisch Low-Noise Amplifier, LNA), der schwache Empfangssignale verstärkt“. Ein Abkürzungsverzeichnis ergänzt diese Einführung, ersetzt sie aber nicht. Zusammenfassung, Haupttext und allein lesbare Bildunterschriften/Tabellen jeweils auf Verständlichkeit prüfen.
- Fachbegriffe wie Topologie, Validierung, Signal-Rausch-Verhältnis, Klassifikation oder Balanced Accuracy beim ersten Auftreten in verständlichem Deutsch erläutern. Symbole und Einheiten vor ihrer ersten Verwendung definieren. Alltagssprachliche Erklärung, Messdefinition und fachliche Einschränkung sinnvoll verbinden.
- Modellnamen unverändert lassen und ihre Bestandteile beim ersten Auftreten erklären: Eingabedarstellung, Architekturprinzip und Bedeutung der Zahlen. Eine Modellziffer nicht pauschal als Schichtzahl ausgeben; ihre tatsächliche Bedeutung aus der gespeicherten Topologie ableiten. Hardwarebausteine und Lernverfahren begrifflich sauber zuordnen.
- Eine kurze Executive Summary soll Ziel, wichtigste Ergebnisse, Rechenaufwand, Aussagegrenzen und nächste Entscheidung ohne Rückgriff auf den Methodenteil verständlich machen. Beobachtete Unterschiede nennen, ohne kleine Rangabstände oder veränderte Versuchsbedingungen als gesicherte Überlegenheit darzustellen.
- Aussagen und Abbildungen müssen auf konkrete Paketdateien und Felder beziehungsweise JSON-Pointer zurückführbar sein. Historische Queue-Snapshots verwenden; fehlende historische Angaben nicht durch aktuelle Labels, Hardware oder Einstellungen ersetzen.
- Abgeschlossen, laufend, fehlgeschlagen, abgebrochen und ausschließlich trainiert unterscheiden. Fehlende Messwerte mit „nicht erfasst“, „nicht verfügbar“ oder „nicht anwendbar“ samt Begründung kennzeichnen. Fehlend ist weder null Prozent noch ein Nachweis des Ausbleibens eines Effekts.
- Synthetische Daten, reale HF-Aufnahmen und Replay ausdrücklich unterscheiden. Frequenzbasierte Vorlabels sind keine unabhängige Ground Truth. Klassifikation, Funkdienst und Emitteridentität getrennt behandeln.
- Modellwahl und Rangfolge auf die dokumentierte Validierung und Auswahlregel stützen; Finaltestwerte getrennt als Endprüfung berichten. Ohne unabhängige Bewertung keine Qualitätsrangfolge, Testgenauigkeit oder Qualitäts-Scores ergänzen.
- Messhardware und Messpfad jeder Zeit-, Speicher- und Auslastungsangabe nennen. CPU-Inferenzzeiten nicht als GPU-Zeiten darstellen oder ohne Messung auf einen anderen Rechner übertragen. Referenznormierung ist keine universelle Hardwareumrechnung.
- Einheiten in Tabellenköpfen und Achsen nennen, Größen eindeutig definieren und Umrechnungen offenlegen. Insbesondere Samples, S/s beziehungsweise MS/s, Hz/kHz/MHz, s/ms, B/MiB/GiB, dB, dBFS, ppm und Prozent nicht vermischen. dBFS ist kein kalibrierter dBm-Wert. Zusätzlich injiziertes SNR ist kein gemessener absoluter RF-SNR.
- Theorie, typische Eigenschaften einer Architektur und beobachtete Ergebnisse auseinanderhalten. Keine SOTA-Überlegenheit, statistische Signifikanz, physische Empfindlichkeit oder Generalisierung behaupten, die der Versuch nicht belegt.
- Alle fachlichen Fremdaussagen belegen. Tatsächlich konsultierte Literatur mit DOI oder stabiler URL, Versionsstand und gegebenenfalls Abrufdatum angeben; keine Quellen oder Seitenzahlen erfinden. Interne Ergebnisquellen zusätzlich eindeutig referenzieren.

Das [Berichtsexport-Handbuch](berichtsexport.md) beschreibt Paketinhalt und Provenienz. Rohwerte und alle Trials bleiben im Begleitpaket erhalten; der PDF-Bericht darf Übersichtstabellen kürzen, muss solche Kürzungen aber kenntlich machen. Abbildungen erhalten Titel, Achsen, Einheiten, Legende, Datengrundlage und einen Hinweis auf Mittelung, Streuung beziehungsweise fehlende Messungen. Zahlen sinnvoll runden; keine Rangfolge allein aus gerundeten Anzeigewerten neu bestimmen.

## 1. Einleitung, Zielsetzung und Stand der Technik

Die untersuchte Frage, Motivation und Anwendung beschreiben. Ziel, Hypothese, minimale Erfolgskriterien und Abgrenzung aus Versuchsbeschreibung und Konfiguration übernehmen. Falls sie nicht dokumentiert wurden, dies offen nennen.

Einen kurzen, quellenbasierten Stand der Technik einordnen: klassische Signalmerkmale und passende Lernverfahren zur automatischen Modulationsklassifikation. Nur für die Fragestellung relevante Ansätze erläutern. Literaturergebnisse auf anderen Datensätzen nicht unmittelbar mit den vorliegenden Messwerten gleichsetzen. Der Abschnitt ist eine Einordnung und keine behauptete umfassende Literaturübersicht.

## 2. Getestete Netzwerktopologien: Aufbau, typische Stärken und Schwächen

Nur Modellfamilien aufnehmen, für die das Paket tatsächliche Läufe nachweist. Reine Katalogvorschläge erscheinen nicht als getestete Netze. Begonnene, fehlgeschlagene oder unvollständige Läufe mit ihrem Status aufführen; sie sind keine abgeschlossene Leistungsbewertung. Klassische Referenzverfahren ausdrücklich als Baselines und nicht als neuronale Netze benennen.

Je Familie Eingabedarstellung, Segmentlänge, wesentliche Schichten, Kanal-/Hidden-Dimensionen, Aktivierungen, Normalisierung, Pooling, Regularisierung, Klassifikationskopf und verfügbare Parameterzahl darstellen. Variationen innerhalb einer Familie knapp tabellieren. Typische Stärken und Schwächen anhand der Architektur und belegter Literatur erklären; davon die im konkreten Versuch beobachteten Stärken und Schwächen trennen.

Bei tatsächlich geprüften FB-LSTM-, 2D-CNN- oder 5-CNN-Adaptionen Herkunft und RF-Anpassungen nennen. Keine identische Reproduktion einer Audioarchitektur oder übernommene Gewichte behaupten, wenn dies nicht nachgewiesen ist.

## 3. Datengrundlage und Vorverarbeitung

Datenquelle, Klassen und Labelherkunft, Empfänger beziehungsweise Generator, verfügbare Frequenzen und Abtastraten, Aufnahmegruppen, Datensatzumfang und Ausschlüsse dokumentieren. Bestätigte Labels, vorläufige Frequenzhinweise und Decoderevidenz kennzeichnen. Herkunftsgruppen und Aufnahmesitzungen anhand des gespeicherten Inventars beschreiben.

Training, Validierung und Test mit Anzahl unabhängiger Gruppen sowie tatsächlich verwendeter Segmente und Beispiele ausweisen. Die Trennung muss vor Segmentierung, Augmentation und Anpassung der Normalisierung erfolgt sein. Überlappende Fenster oder mehrere Clips einer Sitzung sind keine unabhängigen Wiederholungen.

Vorverarbeitung, Merkmalsberechnung, IQ- oder Spektrogrammdarstellung, Resampling, Normalisierung und Segmentbildung reproduzierbar erläutern. Augmentationen einschließlich SNR-Modell, RMS-Jitter in Samples, Frequenzversatz, Abtaktfehlern und weiteren tatsächlich verwendeten Störungen mit Einheiten angeben. Bei realen Aufnahmen ursprünglichen RF-SNR und zusätzlich injiziertes Rauschen unterscheiden; unbekannten RF-SNR nicht rekonstruieren oder erfinden.

## 4. Versuchsaufbau, Hardware und Hyperparameteroptimierung

Gespeicherten Messaufbau, Software-/Abhängigkeitsversionen, Quellstand, CPU, RAM, GPU/VRAM, Betriebssystem sowie Training- und Inferenzgerät dokumentieren. Messbedingungen aus dem damaligen Lauf verwenden. Gerätepräzision, Batchgrößen, Threads, Warmup und Messwiederholungen nennen, soweit erfasst.

Manuelles Raster oder automatische HPO mit Methode, Seed, Zielfunktion, Suchraum, Budgets und Auswahlregel erklären. Suchdauer und Budgetgeltung pro Topologie sowie abgeschlossene, fehlgeschlagene, abgebrochene und zeitlich begrenzte Trials ausweisen. Ausgeschlossene Segmentlängen mit Gründen behalten. Gewinnerkonfiguration und Anzahl der anschließenden Finaltestauswertungen dokumentieren; Testdaten dürfen die Hyperparametersuche nicht gesteuert haben.

Angeforderte und aufgelöste Ausführung getrennt nennen: Worker, Parallelitätsachse, CPU-Threads und Vorverarbeiter pro Trainingworker, GPU-Worker sowie gemeldete Ressourcenbudgets. Job-Wandzeit, summierte Worker-/Sucharbeitszeit und Gewinnertraining unterscheiden. GPU-Eventzeiten und CPU-Wandzeiten entsprechend `phase_timing_scope` kennzeichnen. Überlappende Phasen nicht als Gesamtwandzeit addieren.

Auslastung als abgetastete Messung mit Quelle, Zeitbereich und Geltungsbereich beschreiben. GPU-Gerätewerte können andere Anwendungen und CPU-/Wartephasen einschließen; kurze Bursts können zwischen Stichproben fehlen. Fehlende oder veraltete Telemetrie ist keine gemessene Nullauslastung.

## 5. Bewertungsmetriken und Gesamtscore

Die tatsächlich verfügbaren Qualitätsmetriken definieren: beispielsweise Accuracy, Balanced Accuracy, Macro-F1, klassenweisen Recall, Verwechslungsmatrix und Kalibrierungsmaße. Split, Klassenreihenfolge, Nenner, Aggregation und gegebenenfalls Unsicherheit nennen. Nicht erhobene Fehlalarm-, Unbekannt- oder Empfindlichkeitsmetriken ausdrücklich als fehlend behandeln.

Rechenmetriken getrennt definieren: Trainings-/Suchdauer, eingelaufene CPU-Inferenzlatenzen p50/p95, Batchgröße, Modellgröße, Parameterzahl und gemessener Speicherumfang. Für ein Segment mit `N` Samples und Abtastrate `f_s` gilt `Signaldauer = N / f_s` und `RTF = Inferenzdauer / Signaldauer`; einheitenkonsistent rechnen. Ein RTF unter 1 bezeichnet eine auf diesem Messpfad kürzere Rechenzeit als Signaldauer, aber keinen Nachweis lückenloser Live-Verarbeitung.

Gesamtscore, Komponenten, Gewichte, Chance-Korrektur, Robustheitsaggregation, Zeitbudgets und Echtzeitschwellen exakt aus der gespeicherten `score_definition` übernehmen. Ebenso die tatsächliche `selection_objective` nennen. Eine geänderte Formel als neue Auswertung kennzeichnen; gespeicherte Scores nicht stillschweigend ersetzen.

## 6. Ergebnisse: Genauigkeit, Robustheit und Rechenaufwand

Validierungs- und Finaltestresultate mit Laufstatus getrennt tabellieren. Konfusionsmatrizen mit absoluten Zählungen und klar definierter Normalisierung darstellen. Für ausschließlich trainierte Modelle nur belegte Trainings- und Rechenkennzahlen nennen; keine vermeintliche Testmatrix oder Nullgenauigkeit ergänzen.

Robustheit über die tatsächlich geprüften SNR-/Jitterstufen und sonstigen Variationen darstellen. Mittelwerte, Wertebereiche und deren Bildung explizit erläutern. Unterschiedliche Augmentationen oder Jitterstufen derselben Ursprungsgruppe nicht als unabhängige Replikate oder Konfidenzintervalle ausgeben. Kandidaten, Seeds und Splits nicht unkenntlich vermischen.

Job-Wandzeit, gesamte HPO-/Workerarbeitszeit, Gewinnertraining, Phasenzeiten, CPU-p50/p95, RTF und Modellgröße gemeinsam, aber mit getrennten Geltungsbereichen ausweisen. Hardware- und Telemetrieangaben müssen zur jeweiligen Messung gehören. Trainingshardware und CPU-Inferenzbenchmark nicht gleichsetzen.

## 7. Modellranking und Auswahl der besten Kandidaten

Gespeicherte Rangfolgen und Auswahlkriterien übernehmen: etwa Validierungs-Gesamtscore und ausgewogene Validierungsgenauigkeit. Bindungsregeln und Bedingungen für Live-Eignung nennen. Das Berichtsmodell, das Score-Ranking und die Empfehlung für bis zu drei Modelle können unterschiedlichen dokumentierten Regeln folgen; diese Unterschiede offenlegen.

Finaltestwerte neben den bereits festgelegten Kandidaten als Endprüfung darstellen, aber keine nachträgliche Test-Rangfolge zur Auswahl verwenden. Modelle mit fehlender unabhängiger Bewertung nicht in eine Qualitätsrangliste einordnen. Für Übertragung auf einen Laptop die dortige Nachmessung als eigenen Versuch kennzeichnen; ein importierter Score bleibt eine Bewertung auf dem ursprünglichen Rechner.

## 8. Diskussion und Aussagegrenzen

Zielerreichung, Klassenverwechslungen, Robustheit und Kosten anhand der vorliegenden Evidenz diskutieren. Theoretische Architektureigenschaften, gemessene Zusammenhänge und vermutete Ursachen auseinanderhalten. Beispielsweise eine niedrige GPU-Auslastung nur mit den vorhandenen Geräte-, Batch-, Phasen- und Abtastdaten einordnen.

Abhängigkeiten durch Aufnahmegruppen, Augmentationen, Inhalte, Frequenzvorwissen, Empfänger und Aufnahmebedingungen behandeln. Fehlende unabhängige Wiederholungen, Kalibrierung, Konfidenzintervalle, unbekannte Signalarten, Langzeit-/Echtzeitprüfung und reale HF-Validierung offen benennen, soweit relevant. Aus einem erfolgreichen synthetischen AM/FM-Versuch folgt keine allgemeine Modulations- oder Emittererkennung.

Laufabbrüche, verworfene oder fehlgeschlagene Trials, Datenlücken und nicht verifizierte Hardwareeigenschaften in die Aussagegrenzen einbeziehen. Bestehende Einschränkungen nicht durch aktuelle Rechnerwerte oder nachträglich korrigierte Labels verdecken.

## 9. Fazit und Empfehlungen für weitere Experimente

Die Forschungsfrage mit belegten Aussagen beantworten. Geeignete Kandidaten und den beobachteten Zielkonflikt zwischen Qualität, Robustheit und Rechenaufwand knapp zusammenführen. Die Reichweite der Empfehlung an Datenbasis und Messhardware binden.

Nächste Experimente priorisieren: jeweils offene Frage, kontrollierte Änderung, unabhängige Daten-/Gruppenbasis, Vergleichsmetrik und Erfolgskriterium nennen. Künftige Messungen als Vorschläge kennzeichnen. Keine Ergebnisse für noch nicht getestete Netze, Empfänger, Frequenzbereiche oder Zielrechner vorwegnehmen.

## 10. Literaturverzeichnis und Anhang

Alle im Text tatsächlich verwendeten Quellen vollständig und in einem konsistenten Zitierstil aufführen. DOI oder stabile URL, Software-/Dokumentversion und bei veränderlichen Onlinequellen Abrufdatum ergänzen. Ein kurzer SOTA-Abschnitt entbindet nicht von Quellenangaben; unbelegte Literaturbehauptungen entfernen.

Innerhalb dieses Kapitels den Anhang mit Verweisen auf das vollständige Berichtspaket strukturieren: Konfiguration und Notizen, Dateninventar/Gruppen/Labels, Provenienz und Prüfsummen, vollständige Trial- und Epochenlisten, zusätzliche Matrizen und Rastermessungen, Phasen-/Ressourcendaten sowie relevante Fehler- und Ereignisprotokolle. Gekürzte PDF-Übersichten müssen auf die vollständigen Dateien verweisen. Prüfsummen mit ihrem tatsächlichen Geltungsbereich benennen; Paket- oder Metadatenprüfsummen nicht als vollständige IQ-Inhaltsprüfung ausgeben.

Private IQ-Daten, Datenbanken, Gewichte und personenbezogene Rohdaten gehören nicht in diese Git-Vorlage. Falls sie für eine konkrete Auswertung erforderlich sind, nur im autorisierten lokalen Berichtskontext verwenden. Für jeden konkreten PDF-Bericht abschließend Gliederung, Quellen, Zahlenübernahme, Einheiten, Abbildungslesbarkeit, Tabellenumbrüche und Seitenlayout visuell prüfen.
