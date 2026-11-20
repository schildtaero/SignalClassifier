#!/usr/bin/env bash
# PlutoSDR + RSP1A: native libraries, USB rules and application bindings.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PYTHON=/usr/bin/python3
SOAPY_REPOSITORY=https://github.com/pothosware/SoapySDRPlay3.git
SOAPY_COMMIT=48bd8b41072534018de1d74deb3dea5874d9e0e0
API_URL=https://www.sdrplay.com/software/SDRplay_RSP_API-Linux-3.15.2.run
API_DOWNLOAD_PAGE=https://sdrplay.com/download/hardware-api-linux/
PLUTO_RULES_URL=https://raw.githubusercontent.com/analogdevicesinc/plutosdr-fw/3b689562bfef2e9312a18cf3dba093c1fc00bc72/scripts/53-adi-plutosdr-usb.rules
PLUTO_RULES_SHA256=929083eb2cf55bd71f46ca79481a0eb0b2e7f876c73941f4ad5396e5f7dc3759
DEVICE=all
PLUTO_URI=""
DRY_RUN=0
CHECK_ONLY=0
REINSTALL_API=0
RECREATE_VENV=0
REQUIRE_DEVICE=0
API_INSTALLER=""
API_SHA256=""
USB_MODE=auto
JOBS=2
PACKAGES=(curl ca-certificates python3-venv usbutils udev file)
RSP_PACKAGES=(build-essential cmake git libusb-1.0-0-dev libudev-dev
  libsoapysdr-dev soapysdr-tools python3-soapysdr)
PLUTO_PACKAGES=(libiio0 libiio-utils)

usage() {
  cat <<'USAGE'
PlutoSDR- und RSP1A-Treiber für SignalClassifier auf Ubuntu/Debian installieren.

  ./install_driver.sh
  ./install_driver.sh --device pluto
  ./install_driver.sh --device rsp1a
  ./install_driver.sh --api-installer /pfad/SDRplay_RSP_API-Linux-3.15.2.run

Optionen:
  --device EMPFÄNGER       all, pluto oder rsp1a (Standard all).
  --pluto-uri URI         Pluto-Gerät zusätzlich über URI prüfen, z.B. ip:192.168.2.1.
  --check                 Nur installierte Komponenten prüfen; nichts installieren.
  --dry-run               Schritte anzeigen; keine Downloads oder Änderungen.
  --api-installer DATEI   Lokalen Hersteller-Installer statt Download verwenden.
  --api-sha256 HEX        Installer gegen einen bekannten SHA-256 prüfen.
  --reinstall-api         Hersteller-API auch bei vorhandener v3.15+ neu installieren.
  --recreate-venv         Inkompatible .venv sichern und mit System-Python neu anlegen.
  --usb-mode MODUS        auto, bulk oder iso; auto wählt unter WSL Bulk, sonst Iso.
  --require-device        Alle ausgewählten Empfänger müssen am Ende verfügbar sein.
  --jobs ANZAHL           Parallele Buildjobs, 1–64 (Standard 2).
  --help                  Diese Hilfe anzeigen.

Als normaler Benutzer aufrufen. sudo wird nur für Systemänderungen genutzt.
Die SDRplay-Lizenz wird im Originaldialog bestätigt; kein automatisches Akzeptieren.
USB-Zuordnung zu WSL erfolgt separat; der SDRplay-Dienst benötigt systemd.
USAGE
}

die() { printf 'Fehler: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
need_value() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "Für $1 fehlt ein Wert."; }

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h) usage; exit 0 ;;
      --dry-run) DRY_RUN=1; shift ;;
      --check) CHECK_ONLY=1; shift ;;
      --reinstall-api) REINSTALL_API=1; shift ;;
      --recreate-venv) RECREATE_VENV=1; shift ;;
      --require-device) REQUIRE_DEVICE=1; shift ;;
      --device) need_value "$@"; DEVICE=$2; shift 2 ;;
      --pluto-uri) need_value "$@"; PLUTO_URI=$2; shift 2 ;;
      --api-installer) need_value "$@"; API_INSTALLER=$2; shift 2 ;;
      --api-sha256) need_value "$@"; API_SHA256=${2,,}; shift 2 ;;
      --usb-mode) need_value "$@"; USB_MODE=$2; shift 2 ;;
      --jobs) need_value "$@"; JOBS=$2; shift 2 ;;
      *) die "Unbekannte Option: $1. Siehe --help." ;;
    esac
  done
  [[ "$JOBS" =~ ^[1-9][0-9]?$ && "$JOBS" -le 64 ]] || die '--jobs muss zwischen 1 und 64 liegen.'
  [[ "$USB_MODE" == auto || "$USB_MODE" == bulk || "$USB_MODE" == iso ]] || die '--usb-mode muss auto, bulk oder iso sein.'
  [[ "$DEVICE" == all || "$DEVICE" == pluto || "$DEVICE" == rsp1a ]] || die '--device muss all, pluto oder rsp1a sein.'
  [[ -z "$API_SHA256" || "$API_SHA256" =~ ^[0-9a-f]{64}$ ]] || die '--api-sha256 benötigt genau 64 Hexzeichen.'
  [[ "$DRY_RUN" -eq 0 || "$CHECK_ONLY" -eq 0 ]] || die '--check und --dry-run bitte getrennt verwenden.'
}

rsp_selected() { [[ "$DEVICE" == all || "$DEVICE" == rsp1a ]]; }
pluto_selected() { [[ "$DEVICE" == all || "$DEVICE" == pluto ]]; }

selected_packages() {
  INSTALL_PACKAGES=("${PACKAGES[@]}")
  if rsp_selected; then INSTALL_PACKAGES+=("${RSP_PACKAGES[@]}"); fi
  if pluto_selected; then INSTALL_PACKAGES+=("${PLUTO_PACKAGES[@]}"); fi
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  '; printf '%q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

verify_installer() {
  local path=$1 expected=${2:-${API_SHA256:-}}
  "$SYSTEM_PYTHON" - "$path" "$expected" <<'PY'
import hashlib
import pathlib
import sys
path, expected = pathlib.Path(sys.argv[1]), sys.argv[2].lower()
if not path.is_file() or path.is_symlink() or not 64 <= path.stat().st_size <= 100 * 1024**2:
    sys.exit("Kein unterstützter regulärer SDRplay-Installer (64 Byte bis 100 MiB).")
with path.open("rb") as source:
    header = source.read(65536)
lower = header.lower()
if not header.startswith(b"#!") or b"makeself" not in lower or b"sdrplay" not in lower or b"<html" in lower[:4096] or b"<!doctype html" in lower[:4096]:
    sys.exit("Datei ist kein erwarteter SDRplay/Makeself-Installer; HTML/Fehlerseiten werden nicht ausgeführt.")
with path.open("rb") as source:
    digest = hashlib.file_digest(source, "sha256").hexdigest()
if expected and digest != expected:
    sys.exit(f"SHA-256 stimmt nicht überein: {digest}")
print(f"SDRplay-Installer: SHA-256 {digest}")
PY
}

api_installed() { "$SYSTEM_PYTHON" "$PROJECT_DIR/scripts/rsp_driver_check.py"; }

venv_compatible() {
  local venv=$1
  [[ -x "$venv/bin/python" ]] || return 1
  local expected actual
  expected=$("$SYSTEM_PYTHON" -c 'import sys; print(sys.base_prefix, *sys.version_info[:2])')
  actual=$("$venv/bin/python" -c 'import sys; print(sys.base_prefix, *sys.version_info[:2])') || return 1
  [[ "$expected" == "$actual" ]]
}

prepare_venv() {
  local project=${1:-$PROJECT_DIR} venv backup
  venv="$project/.venv"
  if [[ -x "$venv/bin/python" ]] && ! rsp_selected && "$venv/bin/python" -c 'import sys; assert sys.version_info >= (3,11)'; then
    # Pluto uses pip/ctypes bindings, so no matching apt-Python ABI is needed.
    return
  fi
  if [[ -e "$venv" ]] && ! venv_compatible "$venv"; then
    [[ "$RECREATE_VENV" -eq 1 ]] || die 'Vorhandene .venv verwendet nicht den passenden System-Python. Mit --recreate-venv sichern und neu anlegen; vorhandene Netze/Aufnahmen bleiben erhalten.'
    backup="$project/.runtime/driver-install/venv-backup-$(date +%Y%m%dT%H%M%S)-$$"
    mkdir -p -- "$(dirname -- "$backup")"
    mv -- "$venv" "$backup"
    note "Bisherige Pythonumgebung gesichert: $backup"
    note 'Optionale PyTorch-/Pluto-Pakete bei Bedarf in der neuen Umgebung erneut installieren.'
  fi
  if [[ ! -e "$venv" ]]; then
    if rsp_selected; then "$SYSTEM_PYTHON" -m venv --system-site-packages "$venv";
    else "$SYSTEM_PYTHON" -m venv "$venv"; fi
  elif rsp_selected; then
    # Keep the existing interpreter and all installed packages. Only expose the
    # matching system bindings; never clear/recreate a compatible environment.
    "$SYSTEM_PYTHON" - "$venv/pyvenv.cfg" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
if not re.search(r"^include-system-site-packages\s*=", value, re.MULTILINE):
    sys.exit(".venv/pyvenv.cfg enthält keine gültige include-system-site-packages-Zeile")
replacement = re.sub(r"^include-system-site-packages\s*=.*$", "include-system-site-packages = true", value, flags=re.MULTILINE)
if value != replacement:
    backup = path.with_name("pyvenv.cfg.before-driver-install")
    if not backup.exists():
        backup.write_text(value, encoding="utf-8")
    path.write_text(replacement, encoding="utf-8")
PY
  fi
}

effective_usb_mode() {
  if [[ "$USB_MODE" != auto ]]; then printf '%s\n' "$USB_MODE";
  elif [[ -n "${WSL_DISTRO_NAME:-}" ]] || { [[ -r /proc/sys/kernel/osrelease ]] && grep -qi microsoft /proc/sys/kernel/osrelease; }; then printf 'bulk\n';
  else printf 'iso\n'; fi
}

preflight() {
  [[ "$(uname -s)" == Linux ]] || die 'Dieses Skript ist für Linux beziehungsweise WSL2 bestimmt.'
  [[ $EUID -ne 0 ]] || die 'Bitte ohne sudo als normaler Benutzer starten; das Skript ruft sudo gezielt selbst auf.'
  command -v apt-get >/dev/null || die 'Dieses Skript benötigt Ubuntu/Debian mit apt-get.'
  command -v sudo >/dev/null || die 'sudo fehlt; Systempakete müssen administrativ installiert werden.'
  [[ -x "$SYSTEM_PYTHON" ]] || die 'System-Python /usr/bin/python3 fehlt.'
  "$SYSTEM_PYTHON" -c 'import sys; assert sys.version_info >= (3,11)' || die 'Python 3.11+ benötigt, beispielsweise Ubuntu 24.04 oder Debian 12+.'
  if rsp_selected; then
    case "$(dpkg --print-architecture)" in amd64|arm64|armhf) ;; *) die 'Die SDRplay-API unterstützt hier amd64, arm64 und armhf.' ;; esac
  fi
  if rsp_selected && [[ -e "$PROJECT_DIR/.venv" && "$RECREATE_VENV" -eq 0 ]] && ! venv_compatible "$PROJECT_DIR/.venv"; then
    die 'Vorhandene .venv passt nicht zum System-Python. Erneut mit --recreate-venv starten; die alte Umgebung wird gesichert.'
  fi
  if rsp_selected && [[ -n "$API_INSTALLER" ]]; then verify_installer "$API_INSTALLER"; fi
}

install_pluto_rules() {
  local work=$1 rules="$1/53-adi-plutosdr-usb.rules" user
  user=$(id -un)
  note 'Richte PlutoSDR-USB-Zugriff ein (offizielle Regeln, fest versioniert).'
  if [[ ! -f "$rules" ]] || ! printf '%s  %s\n' "$PLUTO_RULES_SHA256" "$rules" | sha256sum --check --status; then
    curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --connect-timeout 20 --max-time 60 --max-filesize 65536 --output "$rules.part" "$PLUTO_RULES_URL"
    printf '%s  %s\n' "$PLUTO_RULES_SHA256" "$rules.part" | sha256sum --check --status || die 'Pluto-USB-Regeln stimmen nicht mit dem festgelegten SHA-256 überein.'
    mv -- "$rules.part" "$rules"
  fi
  if ! getent group plugdev >/dev/null; then sudo groupadd --system plugdev; fi
  sudo install -m 0644 "$rules" /etc/udev/rules.d/53-adi-plutosdr-usb.rules
  if ! id -nG "$user" | tr ' ' '\n' | grep -qx plugdev; then sudo usermod -a -G plugdev "$user"; fi
  if ! id -nG | tr ' ' '\n' | grep -qx plugdev; then
    note 'Pluto-USB-Zugriff: bitte nach Abschluss ab- und wieder anmelden oder eine neue WSL-Sitzung öffnen, damit die Gruppe plugdev wirksam wird.'
  fi
  if ! sudo udevadm control --reload-rules; then note 'udev-Regeln konnten nicht neu geladen werden; Pluto anschließend neu verbinden.'; fi
  if ! sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=0456; then note 'ADI-USB-Regeln werden gegebenenfalls beim erneuten Anschließen wirksam.'; fi
  if ! sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2fa2; then note 'Neue ADI-USB-Regeln werden gegebenenfalls beim erneuten Anschließen wirksam.'; fi
}

obtain_api_installer() {
  local work=$1 partial
  if [[ -n "$API_INSTALLER" ]]; then
    API_INSTALLER=$("$SYSTEM_PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).absolute())' "$API_INSTALLER")
    verify_installer "$API_INSTALLER"
    return
  fi
  API_INSTALLER="$work/SDRplay_RSP_API-Linux-3.15.2.run"
  if [[ ! -f "$API_INSTALLER" ]]; then
    partial="$API_INSTALLER.part"
    note "Lade Hersteller-Installer von $API_URL"
    if ! curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --connect-timeout 20 --max-time 180 --max-filesize 104857600 --output "$partial" "$API_URL"; then
      rm -f -- "$partial"
      die "Herstellerdownload fehlgeschlagen. Von $API_DOWNLOAD_PAGE herunterladen und erneut mit --api-installer /pfad/zur/Datei.run starten. Es wurde kein heruntergeladener Inhalt ausgeführt."
    fi
    verify_installer "$partial"
    mv -- "$partial" "$API_INSTALLER"
  fi
  verify_installer "$API_INSTALLER"
}

build_soapy() {
  local work=$1 mode=$2 source="$1/SoapySDRPlay3" bulk=OFF
  [[ "$mode" != bulk ]] || bulk=ON
  if [[ ! -d "$source/.git" ]]; then
    git init -q "$source"
    git -C "$source" remote add origin "$SOAPY_REPOSITORY"
  fi
  [[ "$(git -C "$source" remote get-url origin)" == "$SOAPY_REPOSITORY" ]] || die 'Unerwartete Herkunft des Soapy-Buildverzeichnisses.'
  [[ -z "$(git -C "$source" status --porcelain --untracked-files=no)" ]] || die 'Soapy-Buildquellen wurden lokal verändert; Verzeichnis zunächst sichern.'
  if ! git -C "$source" cat-file -e "$SOAPY_COMMIT^{commit}" 2>/dev/null; then
    git -C "$source" fetch --depth 1 origin "$SOAPY_COMMIT"
  fi
  git -C "$source" checkout --detach "$SOAPY_COMMIT"
  cmake -S "$source" -B "$source/build" -DCMAKE_BUILD_TYPE=Release -DSTREAMING_USB_MODE_BULK="$bulk"
  cmake --build "$source/build" --parallel "$JOBS"
  sudo cmake --install "$source/build"
  sudo ldconfig
}

check_components() {
  local result=0
  note 'Prüfe installierte Komponenten (keine Installation).'
  if rsp_selected; then
    if [[ ! -x "$SYSTEM_PYTHON" ]]; then note 'System-Python fehlt.'; result=1;
    elif ! api_installed; then result=1; fi
    if command -v systemctl >/dev/null; then
      if ! systemctl is-active --quiet sdrplay; then note 'Dienst sdrplay ist nicht aktiv.'; result=1;
      elif [[ -x "$SYSTEM_PYTHON" ]] && ! timeout --foreground 15 "$SYSTEM_PYTHON" "$PROJECT_DIR/scripts/rsp_driver_check.py" --runtime; then result=1; fi
    else note 'systemctl fehlt.'; result=1; fi
    if command -v SoapySDRUtil >/dev/null; then
      if ! timeout --foreground 20 SoapySDRUtil --check=sdrplay; then result=1; fi
    else note 'SoapySDRUtil fehlt.'; result=1; fi
    if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]] || ! "$PROJECT_DIR/.venv/bin/python" -c 'import SoapySDR; print("SoapySDR-Python:", SoapySDR.__file__)'; then
      note 'SoapySDR ist in .venv nicht verfügbar.'; result=1
    fi
  fi
  if pluto_selected; then
    if command -v iio_info >/dev/null; then iio_info -V || result=1;
    else note 'iio_info (libiio-utils) fehlt.'; result=1; fi
    if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]] || ! "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/pluto_driver_check.py"; then result=1; fi
  fi
  return "$result"
}

dry_plan() {
  note 'Vorschau: keine Downloads, sudo-Aufrufe oder Dateiänderungen.'
  selected_packages
  run sudo apt-get update
  run sudo apt-get install --no-install-recommends -y "${INSTALL_PACKAGES[@]}"
  if pluto_selected; then
    note "PlutoSDR: native libiio + libiio-utils; offizielle USB-Regeln von $PLUTO_RULES_URL (SHA-256 $PLUTO_RULES_SHA256)."
    note 'USB-Gruppe plugdev prüfen/Benutzer hinzufügen; gegebenenfalls danach neu anmelden.'
    note "Pluto-Geräteprüfung: ${PLUTO_URI:-USB-Suche}; pyadi-iio und pylibiio 0.25 in der Projektumgebung installieren."
    run "$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR[pluto]" 'pylibiio==0.25'
  fi
  if rsp_selected; then
    note "SDRplay-API: vorhandene v3.15+ prüfen; sonst ${API_INSTALLER:-$API_URL} prüfen und interaktiv installieren."
    note "Original-Lizenzdialog bleibt erhalten. Optionaler SHA-256: ${API_SHA256:-nicht vorgegeben}."
    note "SoapySDRPlay3: $SOAPY_REPOSITORY, Commit $SOAPY_COMMIT, USB-Modus $(effective_usb_mode)."
    run sudo systemctl enable --now sdrplay
    note "Passende .venv erhalten und Systempakete sichtbar machen; inkompatible .venv nur mit --recreate-venv sichern/ersetzen."
    run "$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"
  fi
  note 'Abschließend die ausgewählten Bibliotheken, Python-Bindings und gegebenenfalls Empfänger prüfen.'
}

install_rsp() {
  local work=$1 mode=$2 installed=0
  [[ -d /run/systemd/system ]] || die 'systemd läuft nicht. Für RSP1A unter WSL in /etc/wsl.conf [boot] systemd=true setzen und WSL selbst neu starten. Pluto kann separat mit --device pluto eingerichtet werden.'
  if [[ "$REINSTALL_API" -eq 0 ]] && api_installed; then
    note 'Unterstützte Hersteller-API vorhanden; Installation wird beibehalten.'
  else
    obtain_api_installer "$work"
    [[ -t 0 ]] || die 'Die Herstellerinstallation benötigt ein interaktives Terminal für Lizenz und Installationsdialog.'
    note 'Starte Original-SDRplay-Installer. Bitte dessen Lizenz und Installationsdialog beantworten.'
    sudo bash "$API_INSTALLER"
    sudo ldconfig
    # Some vendor installer versions exit 0 even when the license is declined.
    api_installed || die 'Keine nutzbare API v3.15+ installiert. Herstellerinstallation beziehungsweise Lizenzdialog prüfen.'
    installed=1
  fi
  sudo systemctl enable --now sdrplay
  if [[ "$installed" -eq 1 ]]; then sudo systemctl restart sdrplay; fi
  systemctl is-active --quiet sdrplay || die 'SDRplay-Dienst läuft nicht. journalctl -u sdrplay -n 50 --no-pager prüfen.'
  timeout --foreground 15 "$SYSTEM_PYTHON" "$PROJECT_DIR/scripts/rsp_driver_check.py" --runtime
  if ! sudo udevadm control --reload-rules; then note 'udev-Regeln konnten nicht neu geladen werden; Empfänger nach Abschluss neu verbinden.'; fi
  if ! sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1df7; then note 'USB-Regeln werden gegebenenfalls erst beim erneuten Anschließen wirksam.'; fi
  build_soapy "$work" "$mode"
}

probe_devices() {
  local failed=0
  if pluto_selected; then
    note 'Suche Pluto-IIO-Kontext; der TX-Pfad bleibt unberührt.'
    if ! timeout --foreground 20 "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/pluto_driver_check.py" --probe --uri "$PLUTO_URI"; then
      note 'Kein zugänglicher Pluto erkannt. USB/WSL-usbipd, neue Gruppenzugehörigkeit oder --pluto-uri ip:192.168.2.1 prüfen.'
      failed=1
    fi
  fi
  if rsp_selected; then
    note 'Suche angeschlossene RSP-Empfänger (noch kein HF-Empfangstest).'
    if ! timeout --foreground 20 "$PROJECT_DIR/.venv/bin/python" - <<'PY'
import SoapySDR
devices = SoapySDR.Device.enumerate({"driver": "sdrplay"})
for device in devices:
    print(dict(device))
raise SystemExit(0 if devices else 1)
PY
    then
      note 'Kein verfügbarer RSP erkannt. USB/WSL-usbipd, andere SDR-Programme oder einen Kernelkonflikt prüfen.'
      note 'Diagnose: lsusb -d 1df7: und journalctl -u sdrplay -n 50 --no-pager.'
      failed=1
    fi
  fi
  [[ "$REQUIRE_DEVICE" -eq 0 || "$failed" -eq 0 ]] || die 'Treiber installiert, aber nicht alle ausgewählten Empfänger verfügbar (--require-device).'
}

main() {
  parse_args "$@"
  if [[ "$DRY_RUN" -eq 1 ]]; then dry_plan; return; fi
  if [[ "$CHECK_ONLY" -eq 1 ]]; then check_components; return; fi
  preflight
  local work="$PROJECT_DIR/.runtime/driver-install" mode
  mode=$(effective_usb_mode)
  note "Installiere Empfänger-Treiber: $DEVICE."
  selected_packages
  sudo apt-get update
  sudo apt-get install --no-install-recommends -y "${INSTALL_PACKAGES[@]}"
  mkdir -p -- "$work"
  prepare_venv "$PROJECT_DIR"
  if pluto_selected; then
    install_pluto_rules "$work"
    "$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR[pluto]" 'pylibiio==0.25'
    "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/pluto_driver_check.py"
    note 'PlutoSDR-Bibliotheken und Python-Anbindung eingerichtet.'
  else
    "$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"
  fi
  if rsp_selected; then install_rsp "$work" "$mode"; fi
  check_components
  probe_devices
  note 'Ausgewählte Treiber und Python-Anbindungen sind eingerichtet. Start: ./start.sh'
  note 'Unter WSL muss das USB-Gerät separat per usbipd durchgereicht sein. Maximaldurchsatz und HF-Empfang am Gerät prüfen.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
