"""Tests for the CLI framework: shared helpers, exit codes, and ``init``."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db, paths
from commscoach.cli import app as cli_app
from commscoach.cli.common import read_body_arg
from commscoach.db import schema as db_schema


class HelperTests(unittest.TestCase):
    def test_print_err_writes_to_stream(self) -> None:
        buf = io.StringIO()
        cli.print_err("oops", stream=buf)
        self.assertEqual(buf.getvalue(), "oops\n")

    def test_print_json_is_indented_and_sorted(self) -> None:
        buf = io.StringIO()
        cli.print_json({"b": 1, "a": 2}, stream=buf)
        rendered = buf.getvalue()
        self.assertTrue(rendered.endswith("\n"))
        self.assertLess(rendered.find('"a"'), rendered.find('"b"'))
        decoded = json.loads(rendered)
        self.assertEqual(decoded, {"a": 2, "b": 1})

    def test_print_json_falls_back_for_paths(self) -> None:
        buf = io.StringIO()
        cli.print_json({"p": Path("/tmp/x")}, stream=buf)
        self.assertIn("/tmp/x", buf.getvalue())

    def test_read_body_returns_literal_string(self) -> None:
        self.assertEqual(cli.read_body("hello"), "hello")

    def test_read_body_reads_stdin_on_dash(self) -> None:
        stdin = io.StringIO("from stdin\n")
        self.assertEqual(cli.read_body("-", stdin=stdin), "from stdin\n")

    def test_read_body_reads_file_for_at_prefix(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("file contents")
            tmp_path = fh.name
        try:
            self.assertEqual(cli.read_body(f"@{tmp_path}"), "file contents")
        finally:
            os.unlink(tmp_path)

    def test_read_body_at_prefix_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            cli.read_body("@/nonexistent/path/should/not/exist.txt")

    def test_read_body_arg_non_tty_fails_without_composing(self) -> None:
        compose = mock.Mock()
        with self.assertRaisesRegex(
            db.DBError,
            "body required: pass BODY, '-' for stdin, or '@PATH'",
        ):
            read_body_arg(
                None,
                what="body",
                template="",
                preamble="instructions",
                isatty=lambda: False,
                compose=compose,
            )
        compose.assert_not_called()

    def test_read_body_arg_composes_on_tty(self) -> None:
        compose = mock.Mock(return_value="composed")
        result = read_body_arg(
            None,
            what="body",
            template="seed",
            preamble="instructions",
            isatty=lambda: True,
            compose=compose,
        )
        self.assertEqual(result, "composed")
        compose.assert_called_once_with(template="seed", preamble="instructions")

    def test_confirm_assume_yes_skips_prompt(self) -> None:
        calls: list[str] = []

        def fake_input(prompt: str) -> str:
            calls.append(prompt)
            return "n"

        self.assertTrue(cli.confirm("ok?", assume_yes=True, input_fn=fake_input))
        self.assertEqual(calls, [])

    def test_confirm_y_returns_true(self) -> None:
        result = cli.confirm(
            "ok?", stream=io.StringIO(), input_fn=lambda _: "y"
        )
        self.assertTrue(result)

    def test_confirm_yes_returns_true(self) -> None:
        result = cli.confirm(
            "ok?", stream=io.StringIO(), input_fn=lambda _: "YES"
        )
        self.assertTrue(result)

    def test_confirm_n_returns_false(self) -> None:
        result = cli.confirm(
            "ok?", stream=io.StringIO(), input_fn=lambda _: "no"
        )
        self.assertFalse(result)

    def test_confirm_empty_returns_false(self) -> None:
        result = cli.confirm(
            "ok?", stream=io.StringIO(), input_fn=lambda _: ""
        )
        self.assertFalse(result)

    def test_confirm_eof_returns_false(self) -> None:
        def boom(_prompt: str) -> str:
            raise EOFError()

        result = cli.confirm("ok?", stream=io.StringIO(), input_fn=boom)
        self.assertFalse(result)

    def test_confirm_writes_prompt_to_stream(self) -> None:
        buf = io.StringIO()
        cli.confirm("delete?", stream=buf, input_fn=lambda _: "n")
        self.assertIn("delete?", buf.getvalue())
        self.assertIn("[y/N]", buf.getvalue())


class ResolveDBPathTests(unittest.TestCase):
    def test_uses_cli_db_argument(self) -> None:
        args = mock.Mock(spec=["db"], db="/tmp/cli.sqlite3")
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(cli.resolve_db_path(args), Path("/tmp/cli.sqlite3"))

    def test_falls_through_to_env_when_no_cli(self) -> None:
        args = mock.Mock(spec=["db"], db=None)
        with mock.patch.dict(os.environ, {paths.ENV_VAR: "/tmp/env.sqlite3"}):
            self.assertEqual(cli.resolve_db_path(args), Path("/tmp/env.sqlite3"))

    def test_falls_through_to_default_when_neither_set(self) -> None:
        args = mock.Mock(spec=["db"], db=None)
        expected = Path("/tmp/default.sqlite3")
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
            paths, "default_db_path", return_value=expected
        ), mock.patch.object(
            paths, "LEGACY_ICLOUD_DB_PATH", Path("/tmp/missing-legacy.sqlite3")
        ):
            os.environ.pop(paths.ENV_VAR, None)
            self.assertEqual(cli.resolve_db_path(args), expected)

    def test_legacy_migration_error_is_a_cli_semantic_error(self) -> None:
        args = mock.Mock(spec=["db"], db=None)
        with mock.patch.object(
            paths,
            "resolve_db_path",
            side_effect=paths.LegacyDatabaseMigrationRequired("move the database"),
        ):
            with self.assertRaises(db.DBError) as raised:
                cli.resolve_db_path(args)
        self.assertEqual(str(raised.exception), "move the database")


class InitCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "nested" / "commscoach.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_init_creates_db_and_secures_permissions(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--db", str(self.db_path), "init"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.db_path.exists())
        self.assertEqual(
            stat.S_IMODE(self.db_path.stat().st_mode), paths.DB_FILE_MODE
        )
        self.assertEqual(
            stat.S_IMODE(self.db_path.parent.stat().st_mode), paths.DATA_DIR_MODE
        )

    def test_init_text_output_includes_path(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["--db", str(self.db_path), "init"])
        self.assertIn(str(self.db_path), buf.getvalue())

    def test_init_is_idempotent(self) -> None:
        with redirect_stdout(io.StringIO()):
            first = cli.main(["--db", str(self.db_path), "init"])
            second = cli.main(["--db", str(self.db_path), "init"])
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)

    def test_init_json_emits_path_and_schema_version(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--json", "--db", str(self.db_path), "init"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["db_path"], str(self.db_path))
        self.assertEqual(payload["schema_version"], db.SCHEMA_VERSION)

    def test_init_honors_env_var_when_no_cli_db(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.dict(
            os.environ, {paths.ENV_VAR: str(self.db_path)}, clear=False
        ):
            rc = cli.main(["init"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.db_path.exists())


class ExitCodeTests(unittest.TestCase):
    def test_no_command_prints_help_and_returns_zero(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([])
        self.assertEqual(rc, 0)
        self.assertIn("commscoach", buf.getvalue())
        self.assertIn("init", buf.getvalue())

    def test_unknown_command_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["definitely-not-a-command"])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_flag_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["init", "--definitely-not-a-flag"])
        self.assertEqual(ctx.exception.code, 2)

    def test_db_error_maps_to_exit_one_with_message(self) -> None:
        def boom(_args: object) -> int:
            raise db.DBError("simulated semantic failure")

        err_buf = io.StringIO()
        with mock.patch.object(cli_app, "cmd_init", boom), redirect_stderr(err_buf):
            rc = cli.main(["init"])
        self.assertEqual(rc, 1)
        self.assertIn("simulated semantic failure", err_buf.getvalue())

    def test_keyboard_interrupt_maps_to_exit_one(self) -> None:
        def boom(_args: object) -> int:
            raise KeyboardInterrupt()

        err_buf = io.StringIO()
        with mock.patch.object(cli_app, "cmd_init", boom), redirect_stderr(err_buf):
            rc = cli.main(["init"])
        self.assertEqual(rc, 1)
        self.assertIn("interrupted", err_buf.getvalue())

    def test_unexpected_exception_propagates(self) -> None:
        def boom(_args: object) -> int:
            raise RuntimeError("not a DBError")

        with mock.patch.object(cli_app, "cmd_init", boom):
            with self.assertRaises(RuntimeError):
                cli.main(["init"])


class GlobalFlagPositionTests(unittest.TestCase):
    def test_global_flags_work_at_every_command_level(self) -> None:
        parser = cli.build_parser()
        for prefix, suffix in (
            (["--json", "--yes", "--db", "example.sqlite3"], ["entry", "list"]),
            (["entry", "--json", "--yes", "--db", "example.sqlite3"], ["list"]),
            (["entry", "list"], ["--json", "--yes", "--db", "example.sqlite3"]),
        ):
            with self.subTest(prefix=prefix):
                args = parser.parse_args([*prefix, *suffix])
                self.assertTrue(args.json)
                self.assertTrue(args.yes)
                self.assertEqual(args.db, "example.sqlite3")

    def test_later_db_value_wins_and_earlier_flags_survive(self) -> None:
        args = cli.build_parser().parse_args(
            ["--db", "first.sqlite3", "--json", "entry", "list", "--db", "last.sqlite3"]
        )
        self.assertEqual(args.db, "last.sqlite3")
        self.assertTrue(args.json)

    def test_suffix_json_emits_json_for_nested_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            db.init_db(db_path)
            output = io.StringIO()
            with redirect_stdout(output):
                rc = cli.main(["entry", "list", "--db", str(db_path), "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.getvalue()), [])


class OpenDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def test_missing_db_exits_one_without_creating_empty_db(self) -> None:
        db_path = self.tmpdir / "missing.sqlite3"
        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            rc = cli.main(["--db", str(db_path), "entry", "list"])
        self.assertEqual(rc, 1)
        self.assertIn("run 'commscoach init' first", err_buf.getvalue())
        self.assertFalse(db_path.exists())

    def test_uninitialized_db_exits_one_with_init_guidance(self) -> None:
        db_path = self.tmpdir / "empty.sqlite3"
        db_path.touch()
        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            rc = cli.main(["--db", str(db_path), "entry", "list"])
        self.assertEqual(rc, 1)
        self.assertIn("run 'commscoach init' first", err_buf.getvalue())

    def test_v1_db_exits_one_with_init_guidance(self) -> None:
        db_path = self.tmpdir / "v1.sqlite3"
        paths.ensure_data_dir(db_path)
        with closing(db.connect(db_path)) as conn:
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('schema_version', '1')"
            )
            conn.commit()

        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            rc = cli.main(["--db", str(db_path), "entry", "list"])
        self.assertEqual(rc, 1)
        self.assertIn("run 'commscoach init' first", err_buf.getvalue())


class MainModuleTests(unittest.TestCase):
    def test_main_module_exposes_main(self) -> None:
        from commscoach import __main__ as main_module

        self.assertIs(main_module.main, cli.main)


class VersionFlagTests(unittest.TestCase):
    def test_version_flag_prints_package_version_and_exits_zero(self) -> None:
        from commscoach import __version__

        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out_buf.getvalue())
        self.assertIn("commscoach", out_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
