# PlutoSDR- und RSP1A-Treiber installieren

`install_driver.sh` richtet die Host-Empfangspfade für **PlutoSDR und RSP1A** unter **Ubuntu/Debian und WSL2** ein. Voraussetzung: Python 3.11+ und apt. Nur der RSP1A-Pfad benötigt außerdem ein laufendes systemd; für Pluto allein ist das keine Voraussetzung. SDRplay testet die Hardware-API auf Ubuntu-LTS-Versionen bis einschließlich 24.04; Debian und WSL benötigen eine Prüfung am konkreten Rechner. Unterstützte SDRplay-Installerarchitekturen: amd64, arm64 und armhf.

Im Projektordner als normaler Benutzer starten:

```bash
./install_driver.sh                    # standardmäßig beide Empfänger
./install_driver.sh --device pluto     # ausschließlich PlutoSDR
./install_driver.sh --device rsp1a     # ausschließlich RSP1A
```

sudo wird gezielt für Paketinstallation, USB-Regeln, gegebenenfalls Gruppenzugehörigkeit sowie SDRplay-API, Soapy-Modul und Dienst verwendet. Pythonumgebung und Builddateien bleiben benutzereigen. Ein Aufruf des gesamten Skripts mit sudo wird deshalb abgelehnt. Die Installation erfordert Internet für Systempakete, Quellcode und Pythonabhängigkeiten. Firmware wird nicht geflasht; kein TX-Signal wird gestartet.

## Installierte Komponenten

1. Gemeinsame Pakete: Downloadwerkzeuge, USB-Diagnose, udev und Python-venv. Weitere Pakete werden passend zur Empfängerauswahl installiert.
2. **Pluto:** `libiio0`, `libiio-utils`, pyadi-iio und die auf `pylibiio==0.25` festgelegten stabilen Python-Bindings. libiio stammt aus der Distribution; Ubuntu 24.04 liefert 0.25, Debian 12 liefert 0.24. Die Python-Bindings und native Bibliothek werden anschließend gemeinsam geprüft.
3. **Pluto-USB:** Originalregeln aus `analogdevicesinc/plutosdr-fw`, Commit `3b689562bfef2e9312a18cf3dba093c1fc00bc72`, mit festem SHA-256 geprüft. Sie berücksichtigen unter anderem `0456:b673` und `2fa2:5a02`, DFU-Kennungen und ModemManager-Ausnahmen. Die Gruppe `plugdev` wird bei Bedarf angelegt und der aufrufende Benutzer hinzugefügt. Neue Gruppenzugehörigkeit benötigt eine neue Anmeldung beziehungsweise WSL-Sitzung. Die Regel selbst gewährt passenden Gruppenmitgliedern Zugriff auf die ausgewählten ADI-Geräte.
4. **RSP1A:** Buildwerkzeuge, libusb/libudev, SoapySDR samt System-Python-Bindings sowie **SDRplay Hardware API v3.15+**, falls noch keine unterstützte Version vorhanden ist. Der voreingestellte Download ist der bekannte offizielle Installer `SDRplay_RSP_API-Linux-3.15.2.run`; es wird keine automatische Versions-/Patchsuche behauptet. Der originale Lizenz-/Installationsdialog bleibt erhalten. Ein vorhandener API-Stand wird normalerweise beibehalten.
5. **SoapySDRPlay3**, fest auf Commit `48bd8b41072534018de1d74deb3dea5874d9e0e0`. Quellcode und Build liegen unter `.runtime/driver-install/`; wiederholte Aufrufe verwenden dieselben Quellen und einen inkrementellen Build. Keine automatischen Änderungen an einer globalen Git-Konfiguration. Der Dienst **`sdrplay.service`** wird gestartet, Hersteller-USB-Regeln werden neu eingelesen.
6. Passende Pythonumgebung und SignalClassifier-Kernabhängigkeiten. Für RSP1A wird eine mit dem System-Python kompatible Umgebung benötigt; beim Umschalten auf sichtbare Systempakete wird die bisherige `pyvenv.cfg` gesichert. Pluto allein kann eine bestehende andere Python-3.11+-Umgebung beibehalten, weil seine Bindings über pip und ctypes arbeiten. Installierte optionale Pakete einer erhaltenen Umgebung werden nicht gelöscht.

Bei Auswahl beider Empfänger richtet das Skript Pluto vor der SDRplay-API ein. Wird der Herstellerdownload blockiert, ist dadurch der Pluto-Pfad bereits vorbereitet; die Meldung nennt den erneuten Aufruf mit lokaler API-Datei.

Der Installationsnachweis umfasst jeweils die ausgewählten Komponenten. Bei Pluto werden natives libiio und die Python-Bindings geladen; bei RSP1A folgen Header-/Bibliotheksprüfung, aktiver Dienst, **Open → ApiVersion → Close**, Soapy-Modulprüfung und Python-Import. Der Exitcode des Herstellerinstallers allein genügt nicht: Einzelne Versionen können auch nach Lizenzablehnung mit Status 0 enden. Angeschlossene Empfänger werden gesucht; ohne Geräte kann die Softwareinstallation trotzdem erfolgreich sein. Eine Gerätesuche ersetzt keinen echten HF-/Durchsatztest.

## Download oder vorhandene Pythonumgebung

Falls der Hersteller den automatischen Download ablehnt oder den Dateipfad ändert, den passenden Linux-Installer über die [offizielle Hardware-API-Seite](https://sdrplay.com/hardware-api/) herunterladen und angeben:

```bash
./install_driver.sh --api-installer /pfad/SDRplay_RSP_API-Linux-3.15.2.run
```

Die Datei muss zum erwarteten SDRplay/Makeself-Shellformat passen. HTML-/Fehlerseiten werden nicht ausgeführt. Optional mit `--api-sha256 <64-Hexzeichen>` einen unabhängig bekannten Hash prüfen. Ein angezeigter, selbst berechneter Hash allein ist kein Herkunftsnachweis; der automatische Download verwendet die offizielle HTTPS-Adresse mit Zertifikatsprüfung. Die Herstellerdatei und Lizenz werden nicht ins Repository kopiert. Die Installation der API benötigt ein interaktives Terminal.

Wenn der RSP1A-Pfad ausgewählt ist, verweigert das Skript bei einer `.venv` mit anderem Python oder einer Conda-basierten Umgebung den automatischen Austausch. Explizit sichern und neu anlegen:

```bash
./install_driver.sh --recreate-venv
```

Die alte Umgebung bleibt vollständig unter `.runtime/driver-install/venv-backup-*` erhalten. Darin enthaltene Startpfade zeigen weiterhin auf den ursprünglichen `.venv`-Ort; ein Backup ist zur Wiederherstellung dorthin gedacht, nicht zum direkten Weiterbetrieb am Backup-Pfad. Optionale CUDA-/CPU-PyTorch- oder Pluto-Pakete anschließend bei Bedarf erneut installieren. Aufnahmen, Labels und Modelle im Datenverzeichnis werden nicht verändert.

## Prüfung und Optionen

```bash
./install_driver.sh --dry-run           # ausschließlich Schritte anzeigen
./install_driver.sh --check             # Komponenten prüfen, nichts installieren
./install_driver.sh --device pluto --pluto-uri ip:192.168.2.1
./install_driver.sh --require-device    # alle ausgewählten Empfänger müssen verfügbar sein
./install_driver.sh --reinstall-api     # vorhandene Hersteller-API erneut installieren
./install_driver.sh --jobs 4            # parallele Buildjobs, Standard 2
./install_driver.sh --usb-mode bulk     # USB-Bulk ausdrücklich wählen
./install_driver.sh --usb-mode iso      # isochronen Modus ausdrücklich wählen
```

Der Standard `--usb-mode auto` wählt für RSP1A unter WSL Bulk, unter nativem Linux Iso. Das ist eine Buildoption der Soapy-Erweiterung; die tatsächliche Funktion und maximale Rate müssen mit dem Gerät geprüft werden. `--check` installiert nichts und startet keinen Dienst; beim RSP1A öffnet es zur Laufzeitprüfung eine Verbindung zur installierten API. Die Pluto-Prüfung lädt nur Bibliotheken. Im normalen Installationslauf erfolgt zusätzlich eine USB-Kontextsuche; eine angegebene `--pluto-uri` wird stattdessen gezielt geöffnet. Dafür wird kein Sender initialisiert.

Nach erfolgreicher Einrichtung:

```bash
./start.sh
```

In der Oberfläche **SDRplay RSP1A** mit zunächst leerer Geräteadresse oder **PlutoSDR** mit passender USB-/IP-URI auswählen. Eine frühere Vorabinstallation über `setup.sh` allein installiert keine nativen Hardwaretreiber.

## Falls kein Gerät verfügbar ist

```bash
lsusb -d 1df7:
systemctl status sdrplay --no-pager
journalctl -u sdrplay -n 50 --no-pager
SoapySDRUtil --find="driver=sdrplay"
SoapySDRUtil --probe="driver=sdrplay"
.venv/bin/python -c 'import SoapySDR; print(SoapySDR.__file__)'
./install_driver.sh --check --device pluto
iio_info -s
iio_info -u ip:192.168.2.1
```

Andere SDR-Programme schließen und den Empfänger gegebenenfalls neu anschließen. Das Skript deaktiviert oder entlädt keine Kernelmodule pauschal. Bei einer nachgewiesenen Belegung durch `msi2500`/`msi001` die [Soapy-Fehlerdiagnose](https://github.com/pothosware/SoapySDRPlay3/wiki#troubleshooting) auf das eigene System anwenden.

Für Pluto über IP muss der Rechner die Adresse, üblicherweise `192.168.2.1`, tatsächlich erreichen. Das Skript setzt keine statischen Netzwerkrouten. Der Host-Treiber erweitert auch nicht den spezifizierten RF-Frequenzbereich des Geräts.

Unter WSL das USB-Gerät mit [usbipd-win an WSL durchreichen](https://learn.microsoft.com/windows/wsl/connect-usb). Falls systemd noch nicht läuft, nach der [Microsoft-Anleitung](https://learn.microsoft.com/windows/wsl/systemd) aktivieren und WSL selbst neu starten. Das Skript verändert keine WSL-Bootkonfiguration, Windows-Treiber oder Firewallregeln und startet den Rechner nicht neu.

## Quellen und Prüfung

- [SDRplay Hardware API und Linux-Download](https://sdrplay.com/hardware-api/).
- [SDRplay API v3.15-Spezifikation](https://sdrplay.com/wp-content/uploads/2026/04/SDRplay_API_Specification_v3.15.pdf), insbesondere Aufrufreihenfolge und Fehlercodes.
- [SoapySDRPlay3, verwendeter Commit](https://github.com/pothosware/SoapySDRPlay3/commit/48bd8b41072534018de1d74deb3dea5874d9e0e0).
- [Python-venv und Systempakete](https://docs.python.org/3/library/venv.html).
- [ADI: pyadi-iio-Schnellstart](https://analogdevicesinc.github.io/pyadi-iio/guides/quick.html).
- [ADI: libiio-0.x-/1.x-Unterstützung](https://analogdevicesinc.github.io/pyadi-iio/libiio.html#libiio-v1-x-support).
- [Originale Pluto-USB-Regeln, verwendeter Commit](https://github.com/analogdevicesinc/plutosdr-fw/blob/3b689562bfef2e9312a18cf3dba093c1fc00bc72/scripts/53-adi-plutosdr-usb.rules).

Softwaretests simulieren Paket-, sudo-, Netzwerk- und Buildbefehle. Sie prüfen neben Empfängerauswahl, Argumenten und schreibfreien Betriebsarten auch die Ablehnung ungültiger Installerdateien, Prüfsummen, API-Aufrufreihenfolge sowie das Erhalten und Sichern vorhandener Pythonumgebungen. In der Entwicklungsumgebung wurden keine privilegierte Treiberinstallation und kein physischer Pluto-/RSP1A-Empfang durchgeführt. Der Herstellerdownload lieferte dort HTTP 403; der lokale Installerpfad ist deshalb ausdrücklich vorgesehen.
