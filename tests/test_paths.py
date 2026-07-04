"""Tests for path resolution, precedence, and secure permission helpers."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from commscoach import paths


class ResolveDBPathTests(unittest.TestCase):
    def test_default_when_no_overrides(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_VAR, None)
            self.assertEqual(paths.resolve_db_path(), paths.DEFAULT_DB_PATH)

    def test_env_overrides_default(self):
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(paths.resolve_db_path(), Path("/tmp/env.sqlite3"))

    def test_cli_overrides_env(self):
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(
                paths.resolve_db_path("/tmp/cli.sqlite3"),
                Path("/tmp/cli.sqlite3"),
            )

    def test_empty_cli_falls_through_to_env(self):
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(paths.resolve_db_path(""), Path("/tmp/env.sqlite3"))

    def test_empty_env_falls_through_to_default(self):
        with mock.patch.dict(os.environ, {paths.ENV_VAR: ""}):
            self.assertEqual(paths.resolve_db_path(), paths.DEFAULT_DB_PATH)

    def test_user_home_is_expanded(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_VAR, None)
            resolved = paths.resolve_db_path("~/custom.sqlite3")
            self.assertFalse(str(resolved).startswith("~"))
            self.assertTrue(resolved.is_absolute())

    def test_default_lives_in_current_user_icloud_drive(self):
        expected_dir = (
            Path.home()
            / "Library"
            / "Mobile Documents"
            / "com~apple~CloudDocs"
            / "commscoach"
        )
        self.assertEqual(paths.DEFAULT_DATA_DIR, expected_dir)
        self.assertEqual(paths.DEFAULT_DB_PATH, expected_dir / "commscoach.sqlite3")


class EnsureDataDirTests(unittest.TestCase):
    def test_creates_missing_dir_with_0700(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "data" / "commscoach.sqlite3"
            parent = paths.ensure_data_dir(target)
            self.assertTrue(parent.is_dir())
            mode = stat.S_IMODE(parent.stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_tightens_existing_dir_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            os.chmod(data_dir, 0o755)
            db_path = data_dir / "commscoach.sqlite3"
            paths.ensure_data_dir(db_path)
            mode = stat.S_IMODE(data_dir.stat().st_mode)
            self.assertEqual(mode, 0o700)


class SecureFileTests(unittest.TestCase):
    def test_chmods_file_to_0600(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            tmp_path = Path(fh.name)
        try:
            os.chmod(tmp_path, 0o644)
            paths.secure_file(tmp_path)
            mode = stat.S_IMODE(tmp_path.stat().st_mode)
            self.assertEqual(mode, 0o600)
        finally:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
