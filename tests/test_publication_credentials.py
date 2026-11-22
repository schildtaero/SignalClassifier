"""Offline credential tests: every path/environment is isolated and synthetic."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from signalclassifier import publication


FILE_TOKEN = "test-only-local-token-abc123"
ENV_TOKEN = "test-only-environment-token-def456"


class PublicationCredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "xdg-config"
        self.token_path = self.config / "signalclassifier" / "github-token"
        self.token_path.parent.mkdir(parents=True, mode=0o700)
        self.environment = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_token(self, content=FILE_TOKEN, mode=0o600):
        self.token_path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        self.token_path.chmod(mode)

    def status(self):
        value = publication.credential_status()
        self.assertEqual(set(value), {"configured", "credential_source", "credential_error", "default_repository", "default_branch"})
        encoded = json.dumps(value)
        for secret in (FILE_TOKEN, ENV_TOKEN, str(self.root), str(self.token_path)):
            self.assertNotIn(secret, encoded)
        return value

    def assert_unavailable(self, error):
        value = self.status()
        self.assertFalse(value["configured"])
        self.assertIsNone(value["credential_source"])
        self.assertEqual(value["credential_error"], error)
        self.assertIsNone(publication._token())

    def test_environment_precedence_never_opens_or_stats_the_token_file(self):
        values = {"SIGNALCLASSIFIER_GITHUB_TOKEN": ENV_TOKEN, "GITHUB_TOKEN": "test-secondary", "GH_TOKEN": "test-tertiary"}
        with mock.patch.dict(os.environ, values), \
             mock.patch.object(os, "open", side_effect=AssertionError("environment must not open a file")), \
             mock.patch.object(os, "stat", side_effect=AssertionError("environment must not stat a file")), \
             mock.patch.object(os, "lstat", side_effect=AssertionError("environment must not lstat a file")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("environment must not read a file")):
            self.assertEqual(publication._token(), ENV_TOKEN)
            value = self.status()
        self.assertTrue(value["configured"])
        self.assertEqual(value["credential_source"], "environment")
        self.assertIsNone(value["credential_error"])

    def test_empty_environment_entries_fall_through_priority_then_local_file(self):
        self.write_token()
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_GITHUB_TOKEN": " \t\n", "GITHUB_TOKEN": ENV_TOKEN, "GH_TOKEN": "third"}):
            self.assertEqual(publication._token(), ENV_TOKEN)
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_GITHUB_TOKEN": "", "GITHUB_TOKEN": " ", "GH_TOKEN": ENV_TOKEN}):
            self.assertEqual(publication._token(), ENV_TOKEN)
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_GITHUB_TOKEN": "", "GITHUB_TOKEN": " ", "GH_TOKEN": "\n"}):
            self.assertEqual(publication._token(), FILE_TOKEN)
            self.assertEqual(self.status()["credential_source"], "local_file")

    def test_owner_only_regular_file_is_used_without_returning_the_credential(self):
        self.write_token()
        self.assertEqual(stat.S_IMODE(self.token_path.stat().st_mode), 0o600)
        self.assertEqual(publication._token(), FILE_TOKEN)
        value = self.status()
        self.assertTrue(value["configured"])
        self.assertEqual(value["credential_source"], "local_file")
        self.assertIsNone(value["credential_error"])

    def test_group_permissions_are_rejected_even_when_the_process_can_read(self):
        self.write_token(mode=0o640)
        self.assert_unavailable("token_file_permissions")

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX file ownership checks")
    def test_file_owned_by_another_user_is_rejected(self):
        self.write_token()
        actual_uid = self.token_path.stat().st_uid
        with mock.patch.object(os, "geteuid", return_value=actual_uid + 1):
            self.assert_unavailable("token_file_permissions")

    def test_symbolic_link_and_nonregular_token_paths_are_rejected(self):
        target = self.root / "other-token"
        target.write_text(FILE_TOKEN)
        target.chmod(0o600)
        self.token_path.symlink_to(target)
        self.assert_unavailable("token_file_permissions")
        self.token_path.unlink()
        self.token_path.mkdir(mode=0o700)
        self.assert_unavailable("token_file_permissions")

    def test_missing_file_is_normal_unconfigured_state(self):
        self.assert_unavailable(None)

    def test_empty_invalid_utf8_nonascii_and_inner_whitespace_are_rejected(self):
        contents = [b"", b" \r\n\t ", b"\xff\xfe", "tökén".encode(), b"part one",
                    b"part\ntwo", b"part\ttwo", b"part\x00two"]
        for content in contents:
            with self.subTest(content_type="invalid synthetic fixture"):
                self.write_token(content)
                self.assert_unavailable("token_file_invalid")

    def test_outer_ascii_whitespace_including_crlf_is_trimmed(self):
        self.write_token(b"\r\n\t " + FILE_TOKEN.encode() + b" \r\n")
        self.assertEqual(publication._token(), FILE_TOKEN)
        self.assertEqual(self.status()["credential_source"], "local_file")

    def test_file_size_is_bounded_before_accepting_a_credential(self):
        self.write_token(b"x" * 8193)
        self.assert_unavailable("token_file_too_large")

    def test_open_failure_is_a_fixed_sanitized_error(self):
        self.write_token()
        with mock.patch.object(os, "open", side_effect=PermissionError(FILE_TOKEN + str(self.token_path))):
            self.assert_unavailable("token_file_unreadable")

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is required on the supported POSIX path")
    def test_open_uses_nofollow_and_fstat_inode_must_match_preopen_identity(self):
        self.write_token()
        original_open, original_fstat = os.open, os.fstat
        with mock.patch.object(os, "open", wraps=original_open) as opened:
            self.assertEqual(publication._token(), FILE_TOKEN)
        self.assertTrue(any(call.args[1] & os.O_NOFOLLOW for call in opened.call_args_list))

        def replaced_inode(fd):
            info = original_fstat(fd)
            class ChangedInode:
                st_ino = info.st_ino + 1
                def __getattr__(self, name):
                    return getattr(info, name)
            return ChangedInode()
        with mock.patch.object(os, "fstat", side_effect=replaced_inode):
            self.assert_unavailable("token_file_unreadable")

    def test_token_file_override_environment_variables_are_ignored(self):
        self.write_token()
        other = self.root / "override"
        other.write_text(ENV_TOKEN)
        other.chmod(0o600)
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_GITHUB_TOKEN_FILE": str(other),
                                          "GITHUB_TOKEN_FILE": str(other), "GH_TOKEN_FILE": str(other)}):
            self.assertEqual(publication._token(), FILE_TOKEN)

    def test_default_path_uses_isolated_home_when_xdg_is_unset(self):
        fake_home = self.root / "home"
        default = fake_home / ".config" / "signalclassifier" / "github-token"
        default.parent.mkdir(parents=True, mode=0o700)
        default.write_text(FILE_TOKEN)
        default.chmod(0o600)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(Path, "home", return_value=fake_home):
            self.assertEqual(publication._token(), FILE_TOKEN)
            self.assertEqual(self.status()["credential_source"], "local_file")


if __name__ == "__main__":
    unittest.main()
