"""CLI tests for complete JSON export and empty-database import."""

from __future__ import annotations

import io
import json
import stat
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db


class PortingCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.source_path = self.tmpdir / "source.sqlite3"
        self.target_path = self.tmpdir / "target.sqlite3"
        db.init_db(self.source_path)
        db.init_db(self.target_path)
        with closing(db.connect(self.source_path)) as conn:
            person = db.create_person(conn, "alex", is_self=True)
            entry = db.create_entry(
                conn,
                "hello",
                tags=("work",),
                people=(person.handle,),
            )
            db.create_feedback(conn, entry.id, "useful")
            db.create_review(conn, "weekly", [entry.id], scope_limit=1)

    def _run(self, db_path: Path, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--db", str(db_path), *args])
        return rc, out.getvalue(), err.getvalue()

    def _export_payload(self) -> dict[str, object]:
        rc, out, _ = self._run(self.source_path, "export", "--format", "json")
        self.assertEqual(rc, 0)
        return json.loads(out)

    def _v1_archive(self) -> Path:
        """Write an archive in the pre-scope format version 1 shape."""
        payload = self._export_payload()
        payload["format_version"] = 1
        payload["schema_version"] = 3
        for review in payload["reviews"]:
            for column in (
                "scope_person_id",
                "scope_person_handle",
                "scope_tag",
                "scope_since",
                "scope_until",
            ):
                del review[column]
        archive = self.tmpdir / "v1-archive.json"
        archive.write_text(json.dumps(payload), encoding="utf-8")
        return archive

    def test_json_export_to_stdout_is_complete(self) -> None:
        payload = self._export_payload()

        self.assertEqual(payload["format_version"], 2)
        self.assertEqual(payload["entries"][0]["person_ids"], [1])
        self.assertEqual(payload["entries"][0]["tag_ids"], [1])

    def test_global_json_flag_selects_json_export(self) -> None:
        rc, out, err = self._run(self.source_path, "export", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["format_version"], 2)

        rc, _, err = self._run(self.source_path, "export", "--json", "--format", "markdown")
        self.assertEqual(rc, 2)
        self.assertIn("--json conflicts with --format markdown", err)

        out_path = self.tmpdir / "export.md"
        rc, out, err = self._run(
            self.source_path, "export", "--json", "--format", "markdown", "--out", str(out_path)
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"format": "markdown", "path": str(out_path)})
        self.assertFalse(out_path.read_text(encoding="utf-8").lstrip().startswith("{"))

    def test_json_export_file_has_mode_0600(self) -> None:
        out_path = self.tmpdir / "archive.json"

        rc, stdout, stderr = self._run(
            self.source_path,
            "export",
            "--format",
            "json",
            "--out",
            str(out_path),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertIn(str(out_path), stderr)
        self.assertEqual(stat.S_IMODE(out_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(out_path.read_text())["format_version"], 2)

    def test_json_export_to_file_and_import_report_what_happened(self) -> None:
        archive = self.tmpdir / "archive.json"
        rc, out, err = self._run(self.source_path, "--json", "export", "--out", str(archive))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"format": "json", "path": str(archive)})

        target = self.tmpdir / "target.sqlite3"
        db.init_db(target)
        rc, out, err = self._run(target, "--json", "import", str(archive), "--dry-run")
        self.assertEqual(rc, 0, err)
        counts = json.loads(out)["counts"]
        self.assertEqual(counts["entries"], 1)
        self.assertEqual(
            set(counts),
            {
                "people",
                "entries",
                "tags",
                "feedback",
                "reviews",
                "goals",
                "person_notes",
            },
        )

    def test_import_from_path_round_trips(self) -> None:
        archive = self.tmpdir / "archive.json"
        self._run(
            self.source_path,
            "export",
            "--format",
            "json",
            "--out",
            str(archive),
        )

        rc, stdout, _ = self._run(self.target_path, "import", str(archive))

        self.assertEqual(rc, 0)
        self.assertRegex(stdout, r"^imported 1 person, 1 entry, .* from ")
        with (
            closing(db.connect(self.source_path)) as source,
            closing(db.connect(self.target_path)) as target,
        ):
            self.assertEqual(db.dump_to_dict(target), db.dump_to_dict(source))

    def test_import_from_stdin(self) -> None:
        rendered = json.dumps(self._export_payload())

        with mock.patch.object(sys, "stdin", io.StringIO(rendered)):
            rc, _, _ = self._run(self.target_path, "import", "-")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.target_path)) as conn:
            self.assertEqual(len(db.dump_to_dict(conn)["entries"]), 1)

    def test_dry_run_writes_nothing(self) -> None:
        archive = self.tmpdir / "archive.json"
        archive.write_text(json.dumps(self._export_payload()), encoding="utf-8")

        rc, stdout, _ = self._run(self.target_path, "import", str(archive), "--dry-run")

        self.assertEqual(rc, 0)
        self.assertIn("no data written", stdout)
        with closing(db.connect(self.target_path)) as conn:
            self.assertEqual(db.dump_to_dict(conn)["entries"], [])

    def test_dry_run_accepts_a_format_version_one_archive(self) -> None:
        archive = self._v1_archive()

        # A v3 dump restored into a v4 database is the normal migration path;
        # the schema-version mismatch is reported, not refused.
        with self.assertWarns(RuntimeWarning):
            rc, stdout, _ = self._run(self.target_path, "import", str(archive), "--dry-run")

        self.assertEqual(rc, 0)
        self.assertIn("no data written", stdout)

    def test_format_version_one_archive_imports_with_null_scope_columns(self) -> None:
        archive = self._v1_archive()

        with self.assertWarns(RuntimeWarning):
            rc, _, _ = self._run(self.target_path, "import", str(archive))

        self.assertEqual(rc, 0)
        with closing(db.connect(self.target_path)) as conn:
            row = conn.execute(
                "SELECT scope_kind, scope_limit, scope_person_handle, scope_tag,"
                " scope_since, scope_until FROM reviews"
            ).fetchone()
        self.assertEqual(
            dict(row),
            {
                "scope_kind": "latest",
                "scope_limit": 1,
                "scope_person_handle": None,
                "scope_tag": None,
                "scope_since": None,
                "scope_until": None,
            },
        )

    def test_json_mode_reports_dry_run(self) -> None:
        archive = self.tmpdir / "archive.json"
        archive.write_text(json.dumps(self._export_payload()), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()

        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(
                [
                    "--json",
                    "--db",
                    str(self.target_path),
                    "import",
                    str(archive),
                    "--dry-run",
                ]
            )

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["dry_run"], True)

    def test_non_empty_target_exits_one_without_changes(self) -> None:
        archive = self.tmpdir / "archive.json"
        archive.write_text(json.dumps(self._export_payload()), encoding="utf-8")
        with closing(db.connect(self.target_path)) as conn:
            db.create_entry(conn, "existing")
            before = db.dump_to_dict(conn)

        rc, _, stderr = self._run(self.target_path, "import", str(archive))

        self.assertEqual(rc, 1)
        self.assertIn("database is not empty", stderr)
        with closing(db.connect(self.target_path)) as conn:
            self.assertEqual(db.dump_to_dict(conn), before)

    def test_invalid_json_exits_one(self) -> None:
        archive = self.tmpdir / "bad.json"
        archive.write_text("{not json", encoding="utf-8")

        rc, _, stderr = self._run(self.target_path, "import", str(archive))

        self.assertEqual(rc, 1)
        self.assertIn("invalid JSON", stderr)

    def test_non_utf8_archive_exits_one(self) -> None:
        archive = self.tmpdir / "bad.json"
        archive.write_bytes(b"{\xff}")

        rc, _, stderr = self._run(self.target_path, "import", str(archive))

        self.assertEqual(rc, 1)
        self.assertIn("not valid UTF-8", stderr)

    def test_help_describes_lossless_boundaries(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["import", "--help"])

        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("empty database", out.getvalue())


if __name__ == "__main__":
    unittest.main()
