"""Tests for ``commscoach person edit/rm``."""

from __future__ import annotations

import io
import json
from contextlib import closing, redirect_stderr
from unittest import mock

from commscoach import cli, db
from commscoach.cli import people as cli_people

from .cli_people_case import PeopleTestBase


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


class PersonRmTests(PeopleTestBase):
    def test_confirmation_counts_effects_and_declining_preserves_everything(self):
        person = self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "Private context")
            entry = db.create_entry(conn, "Conversation", people=("alice", "bob"))
            feedback = db.create_feedback(conn, entry.id, "Advice")
            review = db.create_review(
                conn, "Review", (entry.id,),
                scope_kind="person", scope_person_handle="alice",
            )
            db.create_review(conn, "General review", (entry.id,))
            goal = db.create_goal(conn, "Practice", person_handle="alice")
            closed = db.create_goal(conn, "Finished", person_handle="alice")
            db.close_goal(conn, closed.id)
        with mock.patch.object(cli_people, "confirm", return_value=False) as confirm:
            rc, _, err = self._run("person", "rm", "ALICE")
        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        prompt = confirm.call_args.args[0]
        for text in ("1 private note,", "1 entry link,", "2 goals", "1 person-scoped review.",
                     "Entries, feedback, goals, and reviews remain", "keep the handle"):
            self.assertIn(text, prompt)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_person(conn, person.id), person)
            self.assertEqual(db.get_entry(conn, entry.id), entry)
            self.assertEqual(db.get_goal(conn, goal.id), goal)
            self.assertEqual(db.get_review(conn, review.id), review)
            self.assertEqual(db.get_person_note(conn, person.id).body, "Private context")
        rc, out, err = self._run("person", "rm", "alice", "--yes", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"deleted": True, "id": person.id, "handle": "alice"})
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person_note(conn, person.id))
            self.assertEqual([p.handle for p in db.get_entry(conn, entry.id).people], ["bob"])
            self.assertEqual(db.list_feedback(conn, entry.id), (feedback,))
            self.assertIsNone(db.get_goal(conn, goal.id).person)
            kept = db.get_review(conn, review.id)
            self.assertIsNone(kept.scope_person)
            self.assertEqual(kept.scope_person_handle, "alice")

    def test_empty_self_deletion_prompt_explains_self_requirement(self):
        self._seed_person("me", is_self=True)
        with mock.patch.object(cli_people, "confirm", return_value=False) as confirm:
            self._run("person", "rm", "me")
        prompt = confirm.call_args.args[0]
        for text in ("0 private notes", "0 entry links", "0 goals", "0 person-scoped reviews",
                     "removes your self profile"):
            self.assertIn(text, prompt)

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
