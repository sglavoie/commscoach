"""Tests for the ``commscoach person`` subcommands."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db
from commscoach.cli import people as cli_people


class PeopleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "people.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self,
        *args: str,
        stdin: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(full_args)
            else:
                rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_person(
        self, handle: str, *, name: str | None = None, is_self: bool = False
    ) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name, is_self=is_self)


class PersonAddTests(PeopleTestBase):
    def test_add_minimal_creates_person(self) -> None:
        rc, out, _ = self._run("person", "add", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("created", out)
        self.assertIn("@alice", out)
        with closing(db.connect(self.db_path)) as conn:
            person = db.get_person_by_handle(conn, "alice")
        assert person is not None
        self.assertEqual(person.handle, "alice")
        self.assertIsNone(person.name)
        self.assertFalse(person.is_self)

    def test_add_with_name_and_self(self) -> None:
        rc, out, _ = self._run(
            "person", "add", "me", "--name", "Sebastien", "--self"
        )
        self.assertEqual(rc, 0)
        self.assertIn("(self)", out)
        self.assertIn("Sebastien", out)
        with closing(db.connect(self.db_path)) as conn:
            person = db.get_person_by_handle(conn, "me")
        assert person is not None
        self.assertTrue(person.is_self)
        self.assertEqual(person.name, "Sebastien")

    def test_add_duplicate_handle_exits_one(self) -> None:
        self._seed_person("alice")
        rc, _, err = self._run("person", "add", "ALICE")
        self.assertEqual(rc, 1)
        self.assertIn("alice", err.lower())
        with closing(db.connect(self.db_path)) as conn:
            people = db.list_people(conn)
        self.assertEqual(len(people), 1)

    def test_add_second_self_exits_one(self) -> None:
        self._seed_person("me", is_self=True)
        rc, _, err = self._run("person", "add", "you", "--self")
        self.assertEqual(rc, 1)
        self.assertIn("self", err)
        with closing(db.connect(self.db_path)) as conn:
            people = db.list_people(conn)
        # only the original self exists
        self.assertEqual(len(people), 1)

    def test_add_empty_handle_exits_one(self) -> None:
        rc, _, err = self._run("person", "add", "   ")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_add_json_output(self) -> None:
        rc, out, _ = self._run(
            "--json", "person", "add", "alice", "--name", "Alice"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["name"], "Alice")
        self.assertFalse(payload["is_self"])
        self.assertIsInstance(payload["id"], int)


class PersonShowTests(PeopleTestBase):
    def test_recent_view_limits_interactions_and_includes_only_linked_active_goals(self):
        alice = self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, alice.id, "Prefers short updates")
            newest = db.create_entry(conn, "latest conversation", people=("alice",), occurred_at="2026-02-01")
            db.create_entry(conn, "older conversation", people=("alice",), occurred_at="2026-01-01")
            db.create_entry(conn, "unrelated", people=("bob",), occurred_at="2026-03-01")
            db.create_feedback(conn, newest.id, "Good listening")
            goal = db.create_goal(conn, "Ask a question", person_handle="alice")
            closed = db.create_goal(conn, "Completed", person_handle="alice")
            db.close_goal(conn, closed.id)
            db.create_goal(conn, "Unrelated", person_handle="bob")
            db.create_goal(conn, "General goal")
        rc, out, err = self._run("person", "show", "ALICE", "--recent", "1", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["notes"], "Prefers short updates")
        self.assertEqual([e["id"] for e in payload["recent_entries"]], [newest.id])
        self.assertEqual([g["id"] for g in payload["active_goals"]], [goal.id])
        rc, out, err = self._run("person", "show", "alice", "--recent", "1")
        self.assertEqual(rc, 0, err)
        for text in ("Prefers short updates", "Recent interactions:", "latest conversation",
                     "has feedback", "Active goals:", "Ask a question"):
            self.assertIn(text, out)
        self.assertNotIn("older conversation", out)
        rc, out, err = self._run("person", "show", "alice", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(json.loads(out)), {"id", "handle", "name", "is_self", "notes"})
        rc, out, err = self._run("person", "show", "alice", "--recent", "0", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["recent_entries"], [])
        self.assertEqual([g["id"] for g in json.loads(out)["active_goals"]], [goal.id])

    def test_recent_view_empty_missing_and_negative_limit(self):
        self._seed_person("alice")
        rc, out, err = self._run("person", "show", "alice", "--recent", "5")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no entries)", out)
        self.assertIn("(no goals)", out)
        rc, _, err = self._run("person", "show", "ghost", "--recent", "5")
        self.assertEqual(rc, 1, err)
        self.assertIn("ghost", err)
        with self.assertRaises(SystemExit) as raised:
            self._run("person", "show", "alice", "--recent", "-1")
        self.assertEqual(raised.exception.code, 2)

    def test_show_existing_human(self) -> None:
        person = self._seed_person("alice", name="Alice Smith")
        rc, out, _ = self._run("person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{person.id}]", out)
        self.assertIn("@alice", out)
        self.assertIn("Alice Smith", out)

    def test_show_case_insensitive_lookup(self) -> None:
        self._seed_person("alice")
        rc, out, _ = self._run("person", "show", "ALICE")
        self.assertEqual(rc, 0)
        self.assertIn("@alice", out)

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("person", "show", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_show_json(self) -> None:
        person = self._seed_person("alice", name="Alice", is_self=True)
        rc, out, _ = self._run("--json", "person", "show", "alice")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], person.id)
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["name"], "Alice")
        self.assertTrue(payload["is_self"])
        self.assertIsNone(payload["notes"])

    def test_show_renders_notes_human_and_json(self) -> None:
        person = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "Prefers async updates\nAsk why.")

        rc, out, _ = self._run("person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("Notes:", out)
        self.assertIn("Prefers async updates\nAsk why.", out)

        rc, out, _ = self._run("--json", "person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["notes"], "Prefers async updates\nAsk why.")


class PersonEditTests(PeopleTestBase):
    def test_notes_editor_prefills_existing_notes_or_starts_empty(self) -> None:
        person = self._seed_person("alice")
        for existing in (None, "Existing private context"):
            with self.subTest(existing=existing):
                with closing(db.connect(self.db_path)) as conn:
                    if existing is not None:
                        db.set_person_note(conn, person.id, existing)
                with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(
                    cli_people, "compose_in_editor", return_value="Revised notes",
                ) as compose:
                    rc, _, err = self._run("person", "edit", "alice", "--notes")
                self.assertEqual(rc, 0, err)
                self.assertEqual(compose.call_args.kwargs["template"], existing or "")
                with closing(db.connect(self.db_path)) as conn:
                    self.assertEqual(db.get_person_note(conn, person.id).body, "Revised notes")

    def test_notes_editor_checks_person_and_terminal_before_opening(self) -> None:
        self._seed_person("alice")
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch.object(
            cli_people, "compose_in_editor",
        ) as compose:
            rc, _, err = self._run("person", "edit", "ghost", "--notes")
            self.assertEqual(rc, 1)
            self.assertIn("ghost", err)
            rc, _, err = self._run("person", "edit", "alice", "--notes")
            self.assertEqual(rc, 1)
            self.assertIn("notes required", err)
            compose.assert_not_called()

    def test_edit_handle(self) -> None:
        person = self._seed_person("alice")
        rc, out, _ = self._run(
            "person", "edit", "alice", "--handle", "alicia"
        )
        self.assertEqual(rc, 0)
        self.assertIn("updated", out)
        self.assertIn("@alicia", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person_by_handle(conn, "alice"))
            renamed = db.get_person_by_handle(conn, "alicia")
        assert renamed is not None
        self.assertEqual(renamed.id, person.id)

    def test_edit_name(self) -> None:
        person = self._seed_person("alice")
        rc, _, _ = self._run(
            "person", "edit", "alice", "--name", "Alice Smith"
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_person(conn, person.id)
        assert updated is not None
        self.assertEqual(updated.name, "Alice Smith")

    def test_edit_name_empty_string_clears(self) -> None:
        person = self._seed_person("alice", name="Alice")
        rc, _, _ = self._run("person", "edit", "alice", "--name", "")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_person(conn, person.id)
        assert updated is not None
        self.assertIsNone(updated.name)

    def test_edit_handle_collision_exits_one(self) -> None:
        self._seed_person("alice")
        self._seed_person("bob")
        rc, _, err = self._run(
            "person", "edit", "alice", "--handle", "BOB"
        )
        self.assertEqual(rc, 1)
        self.assertIn("bob", err.lower())
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_person_by_handle(conn, "alice"))

    def test_edit_missing_person_exits_one(self) -> None:
        rc, _, err = self._run(
            "person", "edit", "ghost", "--name", "Ghosty"
        )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_edit_without_any_field_exits_one(self) -> None:
        self._seed_person("alice")
        rc, _, err = self._run("person", "edit", "alice")
        self.assertEqual(rc, 1)
        self.assertIn("requires at least one", err)

    def test_edit_json_output(self) -> None:
        self._seed_person("alice")
        rc, out, _ = self._run(
            "--json", "person", "edit", "alice", "--name", "Alice"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["name"], "Alice")

    def test_edit_notes_from_literal_stdin_and_file(self) -> None:
        person = self._seed_person("alice")

        rc, _, _ = self._run("person", "edit", "alice", "--notes", "literal")
        self.assertEqual(rc, 0)
        rc, _, _ = self._run(
            "person", "edit", "alice", "--notes", "-", stdin=io.StringIO("stdin\n")
        )
        self.assertEqual(rc, 0)
        note_path = self.tmpdir / "note.txt"
        note_path.write_text("from file", encoding="utf-8")
        rc, _, _ = self._run(
            "person", "edit", "alice", "--notes", f"@{note_path}"
        )
        self.assertEqual(rc, 0)

        with closing(db.connect(self.db_path)) as conn:
            note = db.get_person_note(conn, person.id)
        assert note is not None
        self.assertEqual(note.body, "from file")

    def test_edit_clear_notes(self) -> None:
        person = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "note")

        rc, _, _ = self._run("person", "edit", "alice", "--clear-notes")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person_note(conn, person.id))

    def test_edit_notes_and_clear_notes_are_mutually_exclusive(self) -> None:
        self._seed_person("alice")
        err_buf = io.StringIO()
        with redirect_stderr(err_buf), self.assertRaises(SystemExit) as raised:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "person",
                    "edit",
                    "alice",
                    "--notes",
                    "note",
                    "--clear-notes",
                ]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", err_buf.getvalue())

    def test_edit_notes_file_error_exits_one_without_mutating_person(self) -> None:
        person = self._seed_person("alice", name="Original")
        rc, _, err = self._run(
            "person",
            "edit",
            "alice",
            "--name",
            "Changed",
            "--notes",
            "@/definitely/missing/person-note.txt",
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)
        with closing(db.connect(self.db_path)) as conn:
            unchanged = db.get_person(conn, person.id)
        assert unchanged is not None
        self.assertEqual(unchanged.name, "Original")


class PersonListTests(PeopleTestBase):
    def test_list_empty(self) -> None:
        rc, out, _ = self._run("person", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no people)", out)

    def test_list_self_first_then_alphabetical(self) -> None:
        self._seed_person("zoe")
        self._seed_person("alice")
        me = self._seed_person("me", is_self=True)
        rc, out, _ = self._run("person", "list")
        self.assertEqual(rc, 0)
        idx_me = out.find(f"[{me.id}]")
        idx_alice = out.find("@alice")
        idx_zoe = out.find("@zoe")
        self.assertGreaterEqual(idx_me, 0)
        self.assertGreater(idx_alice, idx_me)
        self.assertGreater(idx_zoe, idx_alice)

    def test_list_json_empty(self) -> None:
        rc, out, _ = self._run("--json", "person", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_json_payload(self) -> None:
        self._seed_person("alice")
        me = self._seed_person("me", is_self=True)
        rc, out, _ = self._run("--json", "person", "list")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 2)
        # Self should be first per db.list_people contract.
        self.assertEqual(payload[0]["id"], me.id)
        self.assertTrue(payload[0]["is_self"])
        self.assertEqual(payload[1]["handle"], "alice")


class PersonRmTests(PeopleTestBase):
    def test_rm_with_yes_skips_prompt(self) -> None:
        person = self._seed_person("alice")
        rc, out, _ = self._run("--yes", "person", "rm", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("@alice", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person_by_handle(conn, "alice"))

    def test_rm_confirm_yes_deletes(self) -> None:
        self._seed_person("alice")
        with mock.patch.object(cli_people, "confirm", return_value=True):
            rc, out, _ = self._run("person", "rm", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("deleted", out)

    def test_rm_confirm_no_aborts(self) -> None:
        self._seed_person("alice")
        with mock.patch.object(cli_people, "confirm", return_value=False):
            rc, _, err = self._run("person", "rm", "alice")
        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_person_by_handle(conn, "alice"))

    def test_rm_missing_person_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "person", "rm", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_rm_cascades_entry_people_only(self) -> None:
        alice = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(
                conn,
                "with alice",
                tags=("convo",),
                people=("alice",),
            )
            db.create_feedback(conn, entry.id, "good chat")
        rc, _, _ = self._run("--yes", "person", "rm", "alice")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person(conn, alice.id))
            surviving = db.get_entry(conn, entry.id)
            assert surviving is not None
            self.assertEqual(surviving.body, "with alice")
            self.assertEqual(surviving.people, ())
            self.assertEqual(set(surviving.tags), {"convo"})
            self.assertEqual(len(db.list_feedback(conn, entry.id)), 1)

    def test_rm_json_output(self) -> None:
        person = self._seed_person("alice")
        rc, out, _ = self._run(
            "--yes", "--json", "person", "rm", "alice"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            payload,
            {"deleted": True, "id": person.id, "handle": "alice"},
        )


class PersonSelfTests(PeopleTestBase):
    def test_self_promotes_first_person(self) -> None:
        person = self._seed_person("me")
        rc, out, _ = self._run("person", "self", "me")
        self.assertEqual(rc, 0)
        self.assertIn("self ->", out)
        self.assertIn("@me", out)
        with closing(db.connect(self.db_path)) as conn:
            self_row = db.get_self_person(conn)
        assert self_row is not None
        self.assertEqual(self_row.id, person.id)
        self.assertTrue(self_row.is_self)

    def test_self_atomic_switch_keeps_single_self(self) -> None:
        old = self._seed_person("old", is_self=True)
        new = self._seed_person("new")
        rc, _, _ = self._run("person", "self", "new")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            old_after = db.get_person(conn, old.id)
            new_after = db.get_person(conn, new.id)
            count = conn.execute(
                "SELECT COUNT(*) FROM people WHERE is_self = 1"
            ).fetchone()[0]
        assert old_after is not None and new_after is not None
        self.assertFalse(old_after.is_self)
        self.assertTrue(new_after.is_self)
        self.assertEqual(count, 1)

    def test_self_missing_handle_exits_one(self) -> None:
        rc, _, err = self._run("person", "self", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_self_idempotent_when_already_self(self) -> None:
        self._seed_person("me", is_self=True)
        rc, _, _ = self._run("person", "self", "me")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM people WHERE is_self = 1"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_self_json_output(self) -> None:
        self._seed_person("me")
        rc, out, _ = self._run("--json", "person", "self", "me")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["is_self"])
        self.assertEqual(payload["handle"], "me")


class PersonHelpTests(PeopleTestBase):
    def test_person_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("person")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("show", out)
        self.assertIn("edit", out)
        self.assertIn("list", out)
        self.assertIn("rm", out)
        self.assertIn("self", out)


if __name__ == "__main__":
    unittest.main()
