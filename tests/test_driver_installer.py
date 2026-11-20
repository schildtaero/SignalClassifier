"""Installer safety checks; all package, network and privilege commands are spies.

No test runs the vendor installer or alters a system installation. Tests source
the shell functions in a subprocess and use temporary numeric/text fixtures.
"""
from __future__ import annotations

import hashlib
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_driver.sh"


@unittest.skipUnless(shutil.which("bash"), "Linux/WSL Bash is required")
class DriverInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rsp-installer-test-")
        self.directory = Path(self.temporary.name)
        self.spies = self.directory / "commands"
        self.spies.mkdir()
        self.calls = self.directory / "forbidden-calls"
        # These commands must never be reached by help/dry-run/check or by
        # argument validation. Absolute Python remains usable for read checks.
        for name in ("sudo", "apt", "apt-get", "curl", "wget", "git", "cmake", "make", "pip", "pip3", "install", "mv", "cp"):
            executable = self.spies / name
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$RSP_TEST_CALLS"\nexit 97\n')
            executable.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": str(self.spies) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "RSP_TEST_CALLS": str(self.calls),
            "NO_COLOR": "1",
        }
        self.fixture = self.directory / "SDRplay-test.run"
        self.payload_marker = self.directory / "vendor-executed"
        self.fixture.write_text(
            '#!/bin/sh\n# This script was generated using Makeself 2.5.0\n'
            '# SDRplay RSP API 3.15 fixture, not an actual installer.\n'
            'label="SDRplay API 3.15"\n'
            'printf "unexpected execution\\n" > "$RSP_TEST_PAYLOAD"\n'
            'exit 0\n', encoding="utf-8")
        self.environment["RSP_TEST_PAYLOAD"] = str(self.payload_marker)

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments):
        return subprocess.run(["bash", str(INSTALLER), *arguments], cwd=self.directory,
                              env=self.environment, text=True, capture_output=True, timeout=20)

    def run_function(self, name, *arguments):
        # Positional arguments preserve spaces and metacharacters in file paths.
        return subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"', "rsp-test",
                               str(INSTALLER), name, *map(str, arguments)], cwd=self.directory,
                              env=self.environment, text=True, capture_output=True, timeout=20)

    def assert_no_external_mutations(self):
        calls = self.calls.read_text() if self.calls.exists() else ""
        self.assertEqual(calls, "", "A read-only installer path invoked a mutation/network command")
        self.assertFalse(self.payload_marker.exists(), "Verification must never execute the vendor payload")

    def test_source_and_help_do_not_start_installation(self):
        sourced = subprocess.run(["bash", "-c", 'source "$1"; printf "SOURCE_OK"', "rsp-test", str(INSTALLER)],
                                 cwd=self.directory, env=self.environment, text=True, capture_output=True, timeout=10)
        self.assertEqual(sourced.returncode, 0, sourced.stderr)
        self.assertTrue(sourced.stdout.endswith("SOURCE_OK"), sourced.stdout)
        help_result = self.run_cli("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for flag in ("--dry-run", "--check", "--api-installer", "--recreate-venv"):
            self.assertIn(flag, help_result.stdout)
        self.assert_no_external_mutations()

    def test_dry_run_describes_installation_without_running_commands(self):
        result = self.run_cli("--dry-run", "--usb-mode", "bulk", "--jobs", "2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "Dry-run should show a reviewable plan")
        self.assertIn("libiio", result.stdout.lower(), "Default installation must include Pluto's native library")
        self.assertIn("SoapySDRPlay3", result.stdout, "Default installation must include the RSP1A module")
        self.assert_no_external_mutations()

    def test_pluto_only_dry_run_skips_sdrplay_download_service_and_build(self):
        result = self.run_cli("--dry-run", "--device", "pluto")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("libiio", result.stdout.lower())
        self.assertIn("pluto", result.stdout.lower())
        for unselected in ("SoapySDRPlay3", "SDRplay_RSP_API", "systemctl enable --now sdrplay"):
            self.assertNotIn(unselected, result.stdout)
        self.assert_no_external_mutations()

    def test_rsp_only_dry_run_skips_pluto_native_library_and_rules(self):
        result = self.run_cli("--dry-run", "--device", "rsp1a")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SoapySDRPlay3", result.stdout)
        self.assertNotIn("libiio0", result.stdout)
        self.assertNotIn("53-adi-plutosdr-usb.rules", result.stdout)
        self.assert_no_external_mutations()

    def test_check_does_not_install_or_download(self):
        result = self.run_cli("--check")
        # Missing hardware or absent libraries may legitimately fail the check.
        self.assertNotEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assert_no_external_mutations()

    def test_pluto_check_never_probes_sdrplay(self):
        soapy = self.spies / "SoapySDRUtil"
        soapy.write_text('#!/bin/sh\nprintf "unexpected RSP probe\\n" >> "$RSP_TEST_CALLS"\nexit 97\n')
        soapy.chmod(0o755)
        result = self.run_cli("--check", "--device", "pluto")
        self.assertNotEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertNotIn("sdrplay", (result.stdout + result.stderr).lower())
        self.assert_no_external_mutations()

    def test_invalid_arguments_fail_before_system_commands(self):
        for arguments in (("--jobs", "0"), ("--jobs", "65"), ("--jobs", "two"),
                          ("--usb-mode", "invalid"), ("--api-sha256", "wrong"),
                          ("--device", "invalid"), ("--device",),
                          ("--unknown-option",), ("--api-installer",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_no_external_mutations()

    def test_html_response_is_refused_even_with_installer_filename(self):
        downloaded = self.directory / "SDRplay_RSP_API-Linux-3.15.2.run"
        downloaded.write_text('<!doctype html><html><body>Forbidden SDRplay Makeself</body></html>')
        result = self.run_function("verify_installer", downloaded)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_no_external_mutations()

    def test_checksum_mismatch_is_refused_before_execution(self):
        result = self.run_function("verify_installer", self.fixture, "0" * 64)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_no_external_mutations()

    def test_valid_shell_header_and_matching_checksum_are_read_only(self):
        expected = hashlib.sha256(self.fixture.read_bytes()).hexdigest()
        result = self.run_function("verify_installer", self.fixture, expected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_no_external_mutations()

    def test_wsl_auto_mode_and_explicit_overrides(self):
        for mode, expected in (("auto", "bulk"), ("bulk", "bulk"), ("iso", "iso")):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["bash", "-c", 'source "$1"; WSL_DISTRO_NAME=TestDistribution; USB_MODE="$2"; effective_usb_mode',
                     "rsp-test", str(INSTALLER), mode], cwd=self.directory, env=self.environment,
                    text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        self.assert_no_external_mutations()

    def existing_venv(self):
        project = self.directory / "project with spaces"
        environment = project / ".venv"
        (environment / "bin").mkdir(parents=True)
        python = environment / "bin" / "python"
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        (environment / "pyvenv.cfg").write_text("home = /test/old-python\ninclude-system-site-packages = false\n")
        (environment / "installed-package-sentinel").write_text("keep existing packages and model tooling")
        return project, environment

    def prepare_venv(self, project, recreate):
        fake_python = self.directory / "fake-system-python"
        # Only emulate venv creation in the designated temporary project. No
        # subprocess may install packages or execute the existing environment.
        fake_python.write_text(
            '#!/bin/bash\nset -eu\n'
            '[[ "$1" == -m && "$2" == venv ]] || exit 96\n'
            'target="${@: -1}"\n'
            '[[ "$target" == "$RSP_TEST_PROJECT/.venv" ]] || exit 95\n'
            'mkdir -p "$target/bin"\n'
            'printf "#!/bin/sh\\nexit 0\\n" > "$target/bin/python"\n'
            'chmod +x "$target/bin/python"\n'
            'printf "include-system-site-packages = true\\n" > "$target/pyvenv.cfg"\n'
            'touch "$target/CREATED_BY_TEST"\n'
        )
        fake_python.chmod(0o755)
        temporary_operations = self.directory / "venv-commands"
        temporary_operations.mkdir(exist_ok=True)
        move = temporary_operations / "mv"
        move.write_text(
            '#!/bin/bash\nset -eu\n'
            'source_path="${@: -2:1}"; destination="${@: -1}"\n'
            '[[ "$source_path" == "$RSP_TEST_PROJECT/.venv" ]] || exit 94\n'
            '[[ "$destination" == "$RSP_TEST_PROJECT/.runtime/driver-install/venv-backup-"* ]] || exit 93\n'
            'exec /bin/mv "$@"\n'
        )
        move.chmod(0o755)
        command = '''source "$1"
SYSTEM_PYTHON="$2"
RECREATE_VENV="$3"
DRY_RUN=0
venv_compatible() { [[ -f "$1/CREATED_BY_TEST" ]]; }
prepare_venv "$4"
'''
        return subprocess.run(["bash", "-c", command, "rsp-test", str(INSTALLER), str(fake_python),
                               str(recreate), str(project)], cwd=self.directory,
                              env={**self.environment, "RSP_TEST_PROJECT": str(project),
                                   "PATH": str(temporary_operations) + os.pathsep + self.environment["PATH"]},
                              text=True, capture_output=True, timeout=20)

    def test_incompatible_existing_environment_is_preserved_without_opt_in(self):
        project, environment = self.existing_venv()
        original = {p.relative_to(environment): p.read_bytes() for p in environment.rglob("*") if p.is_file()}
        result = self.prepare_venv(project, recreate=0)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        current = {p.relative_to(environment): p.read_bytes() for p in environment.rglob("*") if p.is_file()}
        self.assertEqual(current, original)
        self.assertFalse((project / ".runtime").exists(), "Refusal must precede backup directory creation")
        self.assert_no_external_mutations()

    def test_explicit_environment_recreation_keeps_a_complete_backup(self):
        project, environment = self.existing_venv()
        original_config = (environment / "pyvenv.cfg").read_text()
        result = self.prepare_venv(project, recreate=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((environment / "CREATED_BY_TEST").exists())
        backups = list((project / ".runtime" / "driver-install").glob("venv-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "pyvenv.cfg").read_text(), original_config)
        self.assertEqual((backups[0] / "installed-package-sentinel").read_text(), "keep existing packages and model tooling")
        self.assertFalse((backups[0] / "CREATED_BY_TEST").exists())
        self.assert_no_external_mutations()


class DriverRuntimeCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specification = importlib.util.spec_from_file_location("rsp_driver_check_test", ROOT / "scripts" / "rsp_driver_check.py")
        cls.check = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.check)

    def fake_library(self, version=3.15, open_error=0, version_error=0):
        library = mock.Mock()
        library.sdrplay_api_Open.return_value = open_error
        library.sdrplay_api_Close.return_value = 0
        def return_version(pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_float)).contents.value = version
            return version_error
        library.sdrplay_api_ApiVersion.side_effect = return_version
        return library

    def test_header_version_parses_vendor_float_macro_and_old_versions(self):
        self.assertEqual(self.check.parse_version("#define SDRPLAY_API_VERSION (float)(3.15)\r\n"), (3, 15))
        self.assertEqual(self.check.parse_version("# define SDRPLAY_API_VERSION (float)(3.07)\n"), (3, 7))
        with self.assertRaises(ValueError):
            self.check.parse_version("<!doctype html><html>Forbidden</html>")

    def test_runtime_probe_opens_then_reads_then_closes(self):
        library = self.fake_library()
        self.assertEqual(self.check.runtime_version(library), "3.15")
        self.assertEqual(library.mock_calls, [mock.call.sdrplay_api_Open(),
                                             mock.call.sdrplay_api_ApiVersion(mock.ANY),
                                             mock.call.sdrplay_api_Close()])

    def test_failed_version_probe_also_closes_the_api(self):
        for version, error in ((3.14, 0), (3.15, 24)):
            with self.subTest(version=version, error=error):
                library = self.fake_library(version=version, version_error=error)
                with self.assertRaises(ValueError):
                    self.check.runtime_version(library)
                self.assertEqual(library.mock_calls, [mock.call.sdrplay_api_Open(),
                                                     mock.call.sdrplay_api_ApiVersion(mock.ANY),
                                                     mock.call.sdrplay_api_Close()])

    def test_failed_open_never_calls_version_or_close(self):
        library = self.fake_library(open_error=1)
        with self.assertRaises(ValueError):
            self.check.runtime_version(library)
        self.assertEqual(library.mock_calls, [mock.call.sdrplay_api_Open()])


class DriverPlutoCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specification = importlib.util.spec_from_file_location("pluto_driver_check_test", ROOT / "scripts" / "pluto_driver_check.py")
        cls.check = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.check)

    def fake_modules(self):
        context = SimpleNamespace(devices=[SimpleNamespace(name="ad9361-phy"), SimpleNamespace(name="xadc")])
        iio = SimpleNamespace(version=(0, 25, "fixture"), __file__="/fixture/iio.py",
                              scan_contexts=mock.Mock(return_value={"usb:1.2.3": "Fixture PlutoSDR"}),
                              Context=mock.Mock(return_value=context))
        adi = SimpleNamespace(__version__="0.0.21", __file__="/fixture/adi.py",
                              Pluto=mock.Mock(side_effect=AssertionError("A driver check must never initialize SDR/TX controls")))
        return iio, adi

    def test_library_check_does_not_discover_or_open_hardware(self):
        iio, adi = self.fake_modules()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"iio": iio, "adi": adi}), \
             mock.patch.object(sys, "argv", ["pluto_driver_check.py"]), redirect_stdout(output):
            result = self.check.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["libiio_version"], [0, 25, "fixture"])
        iio.scan_contexts.assert_not_called()
        iio.Context.assert_not_called()
        adi.Pluto.assert_not_called()

    def test_probe_discovers_only_usb_and_never_initializes_sdr_controls(self):
        iio, adi = self.fake_modules()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"iio": iio, "adi": adi}), \
             mock.patch.object(sys, "argv", ["pluto_driver_check.py", "--probe"]), redirect_stdout(output):
            result = self.check.main()
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["pluto_contexts"], [{"uri": "usb:1.2.3", "description": "Fixture PlutoSDR"}])
        iio.scan_contexts.assert_called_once_with("usb")
        iio.Context.assert_called_once_with("usb:1.2.3")
        adi.Pluto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
