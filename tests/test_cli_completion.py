"""Tests for shell completion: the ``completion`` command and ``__complete`` callback."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db
from commscoach.cli import completion

from .cli_entry_case import EntryTestBase


def _values(lines: list[str]) -> list[str]:
    return [line.split("\t", 1)[0] for line in lines]


class _CompletionCase(unittest.TestCase):
    def _complete(self, *words: str, env: dict[str, str] | None = None) -> tuple[list[str], str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, env or {}),
            redirect_stdout(out_buf),
            redirect_stderr(err_buf),
        ):
            rc = cli.main([completion.CALLBACK, "--", *words])
        self.assertEqual(rc, 0)
        return out_buf.getvalue().splitlines(), err_buf.getvalue()


class StaticCompletionTests(_CompletionCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.missing = Path(self._tmp.name) / "missing.sqlite3"
        self.env = {"COMMSCOACH_DB": str(self.missing)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def complete(self, *words: str) -> list[str]:
        lines, err = self._complete(*words, env=self.env)
        self.assertEqual(err, "")
        return lines

    def test_top_level_subcommands_come_from_the_parser(self) -> None:
        parser = cli.build_parser()
        expected = next(
            a for a in parser._actions if isinstance(a, completion.argparse._SubParsersAction)
        ).choices
        self.assertEqual(_values(self.complete("")), list(expected))
        self.assertIn("completion", expected)

    def test_subcommands_are_filtered_by_prefix_and_described(self) -> None:
        lines = self.complete("e")
        self.assertEqual(_values(lines), ["entry", "export"])
        self.assertEqual(lines[0], "entry\tManage communication entries")

    def test_nested_subcommands(self) -> None:
        self.assertEqual(
            _values(self.complete("entry", "")), ["add", "show", "edit", "list", "search", "rm"]
        )
        self.assertEqual(_values(self.complete("goal", "c")), ["close"])

    def test_options_of_the_innermost_command(self) -> None:
        values = _values(self.complete("entry", "show", "--"))
        self.assertIn("--with-feedback", values)
        self.assertNotIn("--occurred-at", values)
        self.assertIn("--occurred-at", _values(self.complete("entry", "add", "--o")))

    def test_global_flags_at_any_level(self) -> None:
        for words in (("--",), ("entry", "--"), ("entry", "list", "--")):
            values = _values(self.complete(*words))
            for flag in ("--db", "--json", "--yes"):
                self.assertIn(flag, values, words)

    def test_global_flags_before_the_command_do_not_derail_the_walk(self) -> None:
        self.assertEqual(
            _values(self.complete("--json", "--db", "x.sqlite3", "entry", "sh")), ["show"]
        )
        self.assertEqual(_values(self.complete("entry", "--json", "sh")), ["show"])

    def test_used_single_valued_options_are_not_offered_again(self) -> None:
        values = _values(self.complete("entry", "list", "--json", "--"))
        self.assertNotIn("--json", values)
        # Repeatable options stay available.
        values = _values(self.complete("entry", "add", "--tag", "a", "--"))
        self.assertIn("--tag", values)

    def test_choices_for_options_and_positionals(self) -> None:
        self.assertEqual(self.complete("export", "--format", ""), ["markdown", "json"])
        self.assertEqual(self.complete("export", "--format=j"), ["--format=json"])
        self.assertEqual(self.complete("goal", "list", "--status", "d"), ["done", "dropped"])
        self.assertEqual(self.complete("completion", ""), list(completion.SHELLS))

    def test_value_expected_offers_no_options(self) -> None:
        self.assertEqual(self.complete("entry", "add", "--occurred-at", ""), [])
        self.assertEqual(self.complete("person", "add", ""), [])

    def test_paths_defer_to_the_shell(self) -> None:
        self.assertEqual(self.complete("export", "--out", ""), [completion.FILES_DIRECTIVE])
        self.assertEqual(self.complete("--db", ""), [completion.FILES_DIRECTIVE])
        self.assertEqual(self.complete("import", ""), [completion.FILES_DIRECTIVE])

    def test_missing_db_offers_static_candidates_and_creates_nothing(self) -> None:
        self.assertEqual(self.complete("entry", "show", ""), [])
        self.assertIn("--with-feedback", _values(self.complete("entry", "show", "1", "")))
        self.assertEqual(self.complete("person", "show", ""), [])
        self.assertFalse(self.missing.exists())
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])

    def test_default_db_location_is_not_created(self) -> None:
        data_home = Path(self._tmp.name) / "xdg"
        env = {"XDG_DATA_HOME": str(data_home)}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("COMMSCOACH_DB", None)
            lines, _ = self._complete("tag", "rm", "")
        self.assertEqual(lines, [])
        self.assertFalse(data_home.exists())

    def test_non_database_file_is_ignored(self) -> None:
        self.missing.write_text("not sqlite")
        self.assertEqual(self.complete("tag", "rm", ""), [])
        self.assertEqual(self.missing.read_text(), "not sqlite")

    def test_errors_never_escape(self) -> None:
        with mock.patch.object(completion, "complete", side_effect=RuntimeError("boom")):
            lines, err = self._complete("entry", "")
        self.assertEqual((lines, err), ([], ""))

    def test_callback_is_hidden_from_help(self) -> None:
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertNotIn(completion.CALLBACK, out_buf.getvalue())
        self.assertNotIn(completion.CALLBACK, _values(self.complete("_")))


class DynamicCompletionTests(EntryTestBase, _CompletionCase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("jdoe", name="John Doe")
        self._create_person("asmith")
        self.entry = self._seed_entry(
            "# Heading\n\nTalked about the roadmap.", tags=("1on1", "review"), people=("jdoe",)
        )
        self._seed_entry("Second one", tags=("retro",))
        with closing(db.connect(self.db_path)) as conn:
            self.feedback = db.create_feedback(conn, self.entry.id, "Lead with the ask.")
            self.goal = db.create_goal(conn, "Ask first")
        self.assertEqual(self._run("review", "add", "Review body", "--entry", "1")[0], 0)

    def complete(self, *words: str) -> list[str]:
        lines, err = self._complete("--db", str(self.db_path), *words)
        self.assertEqual(err, "")
        return lines

    def test_handles_for_positionals_and_person_flags(self) -> None:
        self.assertEqual(self.complete("person", "show", ""), ["asmith", "jdoe\tJohn Doe"])
        self.assertEqual(_values(self.complete("entry", "search", "--person", "j")), ["jdoe"])
        self.assertEqual(
            _values(self.complete("goal", "add", "t", "--person", "")), ["asmith", "jdoe"]
        )
        self.assertEqual(_values(self.complete("review", "context", "--person", "a")), ["asmith"])

    def test_handles_typed_with_at_keep_it(self) -> None:
        self.assertEqual(_values(self.complete("person", "show", "@j")), ["@jdoe"])

    def test_comma_separated_lists_complete_the_last_part(self) -> None:
        self.assertEqual(
            _values(self.complete("entry", "add", "--person", "jdoe,")), ["jdoe,asmith"]
        )
        self.assertEqual(
            _values(self.complete("entry", "add", "--tag", "1on1,r")), ["1on1,retro", "1on1,review"]
        )

    def test_tags_for_flags_and_tag_commands(self) -> None:
        self.assertEqual(
            self.complete("entry", "search", "--tag", "re"), ["retro\t1 entry", "review\t1 entry"]
        )
        self.assertEqual(_values(self.complete("tag", "rm", "1")), ["1on1"])
        self.assertEqual(_values(self.complete("tag", "merge", "retro", "r")), ["retro", "review"])
        self.assertEqual(_values(self.complete("review", "list", "--tag", "1")), ["1on1"])
        self.assertEqual(_values(self.complete("review", "add", "x", "--tag", "1")), ["1on1"])
        self.assertEqual(
            _values(self.complete("entry", "edit", "1", "--add-tag", "re")), ["retro", "review"]
        )

    def test_entry_ids_with_excerpts(self) -> None:
        lines = self.complete("entry", "show", "")
        self.assertEqual(_values(lines), ["2", "1"])
        self.assertTrue(lines[1].endswith("Talked about the roadmap."), lines[1])
        for words in (
            ("entry", "edit", ""),
            ("entry", "rm", ""),
            ("feedback", "add", ""),
            ("feedback", "list", ""),
            ("context", ""),
            ("review", "add", "body", "--entry", ""),
            ("review", "add", "body", "--entry", "2", ""),
        ):
            self.assertEqual(_values(self.complete(*words)), ["2", "1"], words)
        self.assertEqual(_values(self.complete("entry", "show", "1")), ["1"])
        self.assertEqual(self.complete("entry", "show", "x"), [])

    def test_goal_feedback_and_review_ids(self) -> None:
        for sub in ("show", "edit", "close", "rm"):
            self.assertEqual(
                self.complete("goal", sub, ""), [f"{self.goal.id}\t[active] Ask first"]
            )
        self.assertEqual(self.complete("feedback", "show", ""), ["1\tentry 1: Lead with the ask."])
        self.assertEqual(_values(self.complete("review", "show", "")), ["1"])
        self.assertEqual(_values(self.complete("goal", "add", "t", "--review", "")), ["1"])

    def test_db_flag_after_the_command_and_equals_form(self) -> None:
        lines, _ = self._complete("person", "show", "--db", str(self.db_path), "")
        self.assertEqual(_values(lines), ["asmith", "jdoe"])
        lines, _ = self._complete(f"--db={self.db_path}", "person", "show", "")
        self.assertEqual(_values(lines), ["asmith", "jdoe"])

    def test_db_flag_overrides_environment(self) -> None:
        other = self.tmpdir / "other.sqlite3"
        lines, _ = self._complete(
            "--db", str(self.db_path), "person", "show", "", env={"COMMSCOACH_DB": str(other)}
        )
        self.assertEqual(_values(lines), ["asmith", "jdoe"])
        self.assertFalse(other.exists())

    def test_environment_selects_the_database(self) -> None:
        lines, _ = self._complete("person", "show", "", env={"COMMSCOACH_DB": str(self.db_path)})
        self.assertEqual(_values(lines), ["asmith", "jdoe"])

    def test_bash_style_equals_split(self) -> None:
        self.assertEqual(_values(self.complete("entry", "search", "--person", "=", "j")), ["jdoe"])
        self.assertEqual(
            _values(self.complete("entry", "search", "--person", "=")), ["asmith", "jdoe"]
        )
        self.assertEqual(_values(self.complete("entry", "search", "--person=j")), ["--person=jdoe"])

    def test_stale_schema_offers_static_candidates_only(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("UPDATE _meta SET value = '3' WHERE key = 'schema_version'")
            conn.commit()
        self.assertEqual(self.complete("person", "show", ""), [])
        self.assertIn("--with-feedback", _values(self.complete("entry", "show", "1", "")))

    def test_read_only_open_leaves_the_file_untouched(self) -> None:
        before = self.db_path.stat().st_mtime_ns, self.db_path.read_bytes()
        self.complete("entry", "show", "")
        self.assertEqual((self.db_path.stat().st_mtime_ns, self.db_path.read_bytes()), before)


class KindRegistryTests(unittest.TestCase):
    def test_metavar_inference_and_explicit_marks(self) -> None:
        parser = completion.argparse.ArgumentParser()
        inferred = parser.add_argument("--who", metavar="HANDLE")
        marked = completion.complete_as(parser.add_argument("old"), completion.TAG)
        opted_out = completion.complete_as(parser.add_argument("--new", metavar="HANDLE"), None)
        self.assertEqual(completion.value_kind(inferred), completion.HANDLE)
        self.assertEqual(completion.value_kind(marked), completion.TAG)
        self.assertIsNone(completion.value_kind(opted_out))

    def test_marks_survive_build_parser_rewrites(self) -> None:
        # build_parser swaps option classes (_StoreOnce); the marks must survive.
        parser = cli.build_parser()

        def action(path: list[str], dest: str) -> completion.argparse.Action:
            current = parser
            for name in path:
                sub = next(
                    a
                    for a in current._actions
                    if isinstance(a, completion.argparse._SubParsersAction)
                )
                current = sub.choices[name]
            return next(a for a in current._actions if a.dest == dest)

        self.assertEqual(completion.value_kind(action(["tag", "rename"], "old")), completion.TAG)
        self.assertIsNone(completion.value_kind(action(["tag", "rename"], "new")))
        self.assertEqual(
            completion.value_kind(action(["review", "add"], "scope_tag")), completion.TAG
        )
        self.assertEqual(
            completion.value_kind(action(["review", "add"], "entry_ids")), completion.ENTRY
        )
        self.assertEqual(
            completion.value_kind(action(["goal", "add"], "review")), completion.REVIEW
        )
        self.assertIsNone(completion.value_kind(action(["person", "add"], "handle")))


class ScriptTests(unittest.TestCase):
    def _script(self, shell: str) -> str:
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = cli.main(["completion", shell])
        self.assertEqual(rc, 0)
        return out_buf.getvalue()

    def test_each_script_calls_back_into_the_cli(self) -> None:
        for shell in completion.SHELLS:
            script = self._script(shell)
            self.assertIn(f"commscoach {completion.CALLBACK} --", script, shell)
            self.assertIn(completion.FILES_DIRECTIVE, script, shell)
            self.assertNotRegex(script, r"@[A-Z]+@")

    def test_unknown_shell_is_a_usage_error(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main(["completion", "tcsh"])
        self.assertEqual(caught.exception.code, 2)

    def test_scripts_parse(self) -> None:
        for shell in completion.SHELLS:
            binary = shutil.which(shell)
            if binary is None:
                continue
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [binary, "-n"], input=self._script(shell), capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_bash_function_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c.sqlite3"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                db.create_person(conn, "jdoe")
            shim = Path(tmp) / "commscoach"
            shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m commscoach "$@"\n')
            shim.chmod(0o755)
            driver = (
                self._script("bash")
                + 'COMP_WORDS=(commscoach person show "")\nCOMP_CWORD=3\n'
                + '_commscoach_complete\nprintf "%s\\n" "${COMPREPLY[@]}"\n'
                + "COMP_WORDS=(commscoach entry search --person =)\nCOMP_CWORD=4\n"
                + '_commscoach_complete\nprintf "%s\\n" "${COMPREPLY[@]}"\n'
            )
            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "COMMSCOACH_DB": str(db_path),
                "PYTHONPATH": os.pathsep.join(sys.path),
            }
            result = subprocess.run(
                [shutil.which("bash"), "--norc", "--noprofile"],
                input=driver,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["jdoe", "=jdoe"])


if __name__ == "__main__":
    unittest.main()
