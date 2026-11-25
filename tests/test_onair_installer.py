"""Installer tests use tiny local archives only; no vendor/network execution."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("onair_installer", ROOT / "scripts" / "install_onair.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class OnAirInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def archive(self, extras=()):
        self.counter += 1
        path = self.root / f"fixture-{self.counter}.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data, mode in (("bin/onair-cli", b"#!/bin/sh\nexit 0\n", 0o755),
                                     ("lib/placeholder.so", b"fixture library, never loaded", 0o644),
                                     ("LICENSE", b"fixture vendor notice", 0o644)):
                item = tarfile.TarInfo(installer.ROOT_NAME+"/"+name)
                item.size, item.mode = len(data), mode
                archive.addfile(item, io.BytesIO(data))
            for item, data in extras:
                archive.addfile(item, io.BytesIO(data) if data is not None else None)
        return path

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_local_checksum_install_preserves_vendor_tree_and_previous_install(self):
        archive = self.archive()
        destination = self.root / "installed"
        first = installer.install_archive(archive, destination, expected_checksum=self.digest(archive))
        self.assertTrue(first.is_file())
        self.assertEqual((first.parent.parent / "LICENSE").read_bytes(), b"fixture vendor notice")
        self.assertEqual((destination / "current" / "bin" / "onair-cli").resolve(), first.resolve())
        second = installer.install_archive(archive, destination, expected_checksum=self.digest(archive))
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file(), "Existing installation stays available for rollback")
        self.assertEqual((destination / "current" / "bin" / "onair-cli").resolve(), second.resolve())

    def test_wrong_digest_installs_nothing(self):
        archive = self.archive()
        destination = self.root / "installed"
        with self.assertRaisesRegex(ValueError, "Prüfsumme"):
            installer.install_archive(archive, destination, expected_checksum="0"*64)
        self.assertFalse(destination.exists())

    def test_traversal_absolute_external_links_and_devices_are_rejected(self):
        cases = []
        for name in ("../escape", "/absolute", installer.ROOT_NAME+"/../escape"):
            item = tarfile.TarInfo(name); item.size = 1
            cases.append((item, b"x"))
        link = tarfile.TarInfo(installer.ROOT_NAME+"/evil-link")
        link.type, link.linkname = tarfile.SYMTYPE, "../../escape"
        cases.append((link, None))
        device = tarfile.TarInfo(installer.ROOT_NAME+"/device")
        device.type = tarfile.CHRTYPE
        cases.append((device, None))
        for index, case in enumerate(cases):
            with self.subTest(index=index):
                archive = self.archive((case,))
                with self.assertRaises(ValueError):
                    installer.install_archive(archive, self.root / f"bad-{index}", expected_checksum=self.digest(archive))
        self.assertFalse((self.root / "escape").exists())

    def test_relative_vendor_library_links_stay_inside_installation(self):
        link = tarfile.TarInfo(installer.ROOT_NAME+"/lib/linked.so")
        link.type, link.linkname = tarfile.SYMTYPE, "placeholder.so"
        archive = self.archive(((link, None),))
        binary = installer.install_archive(archive, self.root / "installed", expected_checksum=self.digest(archive))
        self.assertEqual((binary.parent.parent / "lib/linked.so").read_bytes(), b"fixture library, never loaded")
