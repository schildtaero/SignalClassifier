"""Exercise the credential helper with fake stdin and isolated user folders."""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANARY = "github_pat_fixture_DO_NOT_PRINT_9f82d10"


class ConfigureGithubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = {"PATH": os.defpath, "HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / "config"),
                    "SIGNALCLASSIFIER_PYTHON": sys.executable}
        self.path = self.base / "config" / "signalclassifier" / "github-token"

    def run_helper(self, value="", args=()):
        result = subprocess.run(["bash", str(ROOT / "configure_github.sh"), *args],
                                input=value + "\n", text=True, capture_output=True, env=self.env, timeout=10)
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        return result

    def test_private_atomic_save_and_replacement_without_secret_output(self):
        result = self.run_helper(CANARY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.path.read_text(), CANARY + "\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(list(self.path.parent.glob(".github-token-*")), [])
        self.assertEqual(self.run_helper(CANARY + "_updated").returncode, 0)
        self.assertEqual(self.path.read_text(), CANARY + "_updated\n")

    def test_bad_input_keeps_previous_token_intact(self):
        self.assertEqual(self.run_helper(CANARY).returncode, 0)
        for invalid in ("", CANARY + " space", "x" * 8193, "ünicode"):
            with self.subTest(invalid_length=len(invalid)):
                result = self.run_helper(invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.path.read_text(), CANARY + "\n")

    def test_help_and_arguments_never_save_or_start_an_upload(self):
        self.assertEqual(self.run_helper(args=("--help",)).returncode, 0)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.run_helper(args=("unexpected",)).returncode, 2)
        self.assertFalse(self.path.exists())

    def test_relative_xdg_is_rejected_and_home_fallback_stays_private(self):
        self.env["XDG_CONFIG_HOME"] = "relative"
        self.assertEqual(self.run_helper(CANARY).returncode, 2)
        self.env.pop("XDG_CONFIG_HOME")
        self.assertEqual(self.run_helper(CANARY).returncode, 0)
        path = self.base / ".config" / "signalclassifier" / "github-token"
        self.assertEqual(path.read_text(), CANARY + "\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
