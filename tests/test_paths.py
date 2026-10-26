"""Tests for path resolution, precedence, and secure permission helpers."""

from __future__ import annotations

import os
import shlex
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from commscoach import paths


class ResolveDBPathTests(unittest.TestCase):
    def test_default_when_no_overrides(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {"HOME": td}, clear=True
        ), mock.patch.object(
            paths, "LEGACY_ICLOUD_DB_PATH", Path(td) / "missing-legacy.sqlite3"
        ):
            self.assertEqual(paths.resolve_db_path(), paths.default_db_path())

    def test_env_overrides_default_and_legacy(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}
        ), mock.patch.object(
            paths, "LEGACY_ICLOUD_DB_PATH", Path(td) / "legacy.sqlite3"
        ):
            paths.LEGACY_ICLOUD_DB_PATH.touch()
            self.assertEqual(paths.resolve_db_path(), Path("/tmp/env.sqlite3"))

    def test_cli_overrides_env_default_and_legacy(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}
        ), mock.patch.object(
            paths, "LEGACY_ICLOUD_DB_PATH", Path(td) / "legacy.sqlite3"
        ):
            paths.LEGACY_ICLOUD_DB_PATH.touch()
            self.assertEqual(
                paths.resolve_db_path("/tmp/cli.sqlite3"),
                Path("/tmp/cli.sqlite3"),
            )

    def test_empty_cli_falls_through_to_env(self):
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(paths.resolve_db_path(""), Path("/tmp/env.sqlite3"))

    def test_empty_env_falls_through_to_default(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {"HOME": td, paths.ENV_VAR: ""}, clear=True
        ), mock.patch.object(
            paths, "LEGACY_ICLOUD_DB_PATH", Path(td) / "missing-legacy.sqlite3"
        ):
            self.assertEqual(paths.resolve_db_path(), paths.default_db_path())

    def test_user_home_is_expanded(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_VAR, None)
            resolved = paths.resolve_db_path("~/custom.sqlite3")
            self.assertFalse(str(resolved).startswith("~"))
            self.assertTrue(resolved.is_absolute())

    def test_xdg_data_home_is_honored(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"HOME": str(Path(td) / "home"), "XDG_DATA_HOME": str(Path(td) / "xdg")},
            clear=True,
        ):
            expected_dir = Path(td) / "xdg" / "commscoach"
            self.assertEqual(paths.default_data_dir(), expected_dir)
            self.assertEqual(
                paths.default_db_path(), expected_dir / "commscoach.sqlite3"
            )

    def test_xdg_unset_uses_local_share_under_current_home(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {"HOME": td}, clear=True
        ):
            expected_dir = Path(td) / ".local" / "share" / "commscoach"
            self.assertEqual(paths.default_data_dir(), expected_dir)
            self.assertEqual(
                paths.default_db_path(), expected_dir / "commscoach.sqlite3"
            )

    def test_legacy_database_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "Legacy Mobile Documents" / "commscoach.sqlite3"
            legacy.parent.mkdir()
            legacy.touch()
            with mock.patch.dict(
                os.environ, {"XDG_DATA_HOME": str(root / "xdg")}, clear=True
            ), mock.patch.object(paths, "LEGACY_ICLOUD_DB_PATH", legacy):
                target = paths.default_db_path()
                with self.assertRaises(
                    paths.LegacyDatabaseMigrationRequired
                ) as raised:
                    paths.resolve_db_path()

            message = str(raised.exception)
            self.assertIn(shlex.join(["mkdir", "-p", str(target.parent)]), message)
            self.assertIn(shlex.join(["mv", str(legacy), str(target)]), message)
            self.assertIn("--db PATH", message)
            self.assertIn("$COMMSCOACH_DB", message)

    def test_missing_default_without_legacy_returns_new_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "missing-legacy.sqlite3"
            with mock.patch.dict(
                os.environ, {"XDG_DATA_HOME": str(root / "xdg")}, clear=True
            ), mock.patch.object(paths, "LEGACY_ICLOUD_DB_PATH", legacy):
                self.assertEqual(paths.resolve_db_path(), paths.default_db_path())

    def test_existing_default_skips_legacy_check_entirely(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.dict(
                os.environ, {"XDG_DATA_HOME": str(root / "xdg")}, clear=True
            ):
                target = paths.default_db_path()
                target.parent.mkdir(parents=True)
                target.touch()
                legacy = mock.Mock(spec=Path)
                legacy.exists.side_effect = AssertionError("legacy path was inspected")
                with mock.patch.object(paths, "LEGACY_ICLOUD_DB_PATH", legacy):
                    self.assertEqual(paths.resolve_db_path(), target)
                legacy.exists.assert_not_called()


class SQLiteCompanionPathTests(unittest.TestCase):
    def test_icloud_placeholder_uses_hidden_icloud_name(self):
        db_path = Path("/tmp/commscoach.sqlite3")
        self.assertEqual(
            paths.icloud_placeholder_for(db_path),
            Path("/tmp/.commscoach.sqlite3.icloud"),
        )

    def test_sidecar_paths_include_delete_and_wal_artifacts(self):
        db_path = Path("/tmp/commscoach.sqlite3")
        self.assertEqual(
            paths.sidecar_paths(db_path),
            (
                Path("/tmp/commscoach.sqlite3-journal"),
                Path("/tmp/commscoach.sqlite3-wal"),
                Path("/tmp/commscoach.sqlite3-shm"),
            ),
        )

    def test_conflict_copies_filters_and_sorts_matching_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "commscoach.sqlite3"
            db_path.touch()
            later = root / "commscoach (Zed's conflicted copy).sqlite3"
            earlier = root / "commscoach (Amy's conflicted copy).sqlite3"
            unrelated = root / "other (Amy's conflicted copy).sqlite3"
            later.touch()
            earlier.touch()
            unrelated.touch()
            self.assertEqual(paths.conflict_copies(db_path), (earlier, later))


class EnsureDataDirTests(unittest.TestCase):
    def test_creates_missing_dir_with_0700(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "data" / "commscoach.sqlite3"
            parent = paths.ensure_data_dir(target)
            self.assertTrue(parent.is_dir())
            mode = stat.S_IMODE(parent.stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_tightens_existing_default_data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "commscoach"
            data_dir.mkdir()
            os.chmod(data_dir, 0o755)
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": td}):
                paths.ensure_data_dir(data_dir / "commscoach.sqlite3")
            mode = stat.S_IMODE(data_dir.stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_leaves_existing_user_chosen_dir_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            shared = Path(td) / "shared"
            shared.mkdir()
            os.chmod(shared, 0o755)
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(Path(td) / "xdg")}):
                paths.ensure_data_dir(shared / "journal.sqlite3")
                paths.ensure_data_dir(shared / "new" / "journal.sqlite3")
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((shared / "new").stat().st_mode), 0o700)


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
