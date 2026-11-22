#!/usr/bin/env bash
# Store one private local credential, never a value in repository configuration.
set +x
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == --help ]]; then
  echo 'Aufruf: ./configure_github.sh – GitHub-Token verdeckt eingeben und dauerhaft lokal speichern.'
  echo 'Ziel: ${XDG_CONFIG_HOME:-~/.config}/signalclassifier/github-token (nur eigener Benutzer).'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo 'Keine Argumente erwartet. Token ausschließlich bei der verdeckten Eingabe einfügen.' >&2
  exit 2
fi
sc_python="${SIGNALCLASSIFIER_PYTHON:-python3}"
read -rsp 'GitHub-Token (ohne Anführungszeichen): ' sc_github_token
printf '\n'
printf '%s' "$sc_github_token" | "$sc_python" -c '
import os
from pathlib import Path
import sys
import tempfile

raw = sys.stdin.buffer.read(8193)
try:
    token = raw.decode("ascii").strip()
except UnicodeError:
    token = ""
if len(raw) > 8192 or not token or any(not 33 <= ord(char) <= 126 for char in token):
    print("Ungültige Eingabe: genau einen Token ohne Leerzeichen eingeben.", file=sys.stderr)
    sys.exit(2)
base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
if not base.is_absolute():
    print("XDG_CONFIG_HOME muss ein absoluter Pfad sein.", file=sys.stderr)
    sys.exit(2)
folder = base / "signalclassifier"
temporary = None
try:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink() or folder.stat().st_uid != os.geteuid():
        raise OSError("private configuration directory required")
    folder.chmod(0o700)
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".github-token-", delete=False) as output:
        temporary = Path(output.name)
        output.write((token + "\n").encode("ascii"))
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    temporary.replace(folder / "github-token")
    temporary = None
except OSError:
    print("Token konnte nicht gespeichert werden. Rechte des lokalen Konfigurationsordners prüfen.", file=sys.stderr)
    sys.exit(1)
finally:
    if temporary is not None:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
print("GitHub-Token dauerhaft lokal gespeichert; nur für den eigenen Benutzer lesbar.")
print("SignalClassifier liest die Datei automatisch, auch beim Start über start.cmd.")
print("Vorhandene Token-Umgebungsvariablen haben Vorrang. Kein Upload wurde gestartet.")
'
unset sc_github_token
