"""Handles are typed the way they are shown: ``@handle`` and comma lists work."""

from __future__ import annotations

import json
from contextlib import closing

from commscoach import db

from .cli_entry_case import EntryTestBase


class HandleInputTests(EntryTestBase):
    def test_at_prefix_is_accepted_wherever_a_handle_is_typed(self) -> None:
        self._create_person("jdoe")
        rc, out, _ = self._run("--json", "person", "show", "@jdoe")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["handle"], "jdoe")

        rc, out, _ = self._run("--json", "entry", "add", "sync", "--person", "@jdoe")
        self.assertEqual(rc, 0)
        self.assertEqual([p["handle"] for p in json.loads(out)["people"]], ["jdoe"])

    def test_person_add_strips_the_at_prefix(self) -> None:
        rc, out, _ = self._run("--json", "person", "add", "@carol")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["handle"], "carol")

    def test_person_flag_splits_on_commas_like_tag(self) -> None:
        self._create_person("bob")
        self._create_person("carol")
        rc, out, _ = self._run("--json", "entry", "add", "sync", "--person", "bob,@carol")
        self.assertEqual(rc, 0)
        entry = json.loads(out)
        self.assertEqual(sorted(p["handle"] for p in entry["people"]), ["bob", "carol"])

        rc, out, _ = self._run(
            "--json", "entry", "edit", str(entry["id"]), "--remove-person", "bob,carol"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["people"], [])

    def test_handles_with_commas_or_whitespace_are_rejected(self) -> None:
        for handle in ("bob carol", "a,b"):
            with self.subTest(handle=handle):
                rc, _, err = self._run("person", "add", handle)
                self.assertEqual(rc, 1)
                self.assertIn("must not contain commas or whitespace", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_people(conn), ())

    def test_tag_rename_rejects_commas(self) -> None:
        self._run("entry", "add", "sync", "--tag", "x")
        rc, _, err = self._run("tag", "rename", "x", "a,b")
        self.assertEqual(rc, 1)
        self.assertIn("must not contain a comma", err)


class UnicodeMatchingTests(EntryTestBase):
    def test_substring_searches_fold_case_and_accents(self) -> None:
        self._create_person("me", is_self=True)
        rc, out, _ = self._run("--json", "entry", "add", "ÉCOLE meeting about the café")
        entry_id = str(json.loads(out)["id"])
        self._run("feedback", "add", entry_id, "Talk about the CAFÉ budget first.")
        self._run("goal", "add", "Visit the École")
        self._run("review", "add", "Café pattern.", "--entry", entry_id)
        for command in (
            ("entry", "search", "--exact", "--query", "école"),
            ("feedback", "search", "--query", "cafe"),
            ("goal", "search", "--query", "ecole"),
            ("review", "search", "--query", "cafe"),
        ):
            with self.subTest(command=command):
                rc, out, err = self._run("--json", *command)
                self.assertEqual(rc, 0, err)
                self.assertEqual(len(json.loads(out)), 1)

    def test_handles_are_unicode_case_insensitive_but_keep_accents(self) -> None:
        self._create_person("Émile")
        rc, _, err = self._run("person", "add", "émile")
        self.assertEqual(rc, 1)
        self.assertIn("émile", err)
        rc, out, _ = self._run("--json", "person", "show", "ÉMILE")
        self.assertEqual(json.loads(out)["handle"], "Émile")
        rc, _, _ = self._run("person", "add", "emile")
        self.assertEqual(rc, 0)


class FilterConsistencyTests(EntryTestBase):
    def test_entry_search_rejects_unknown_person_tag_and_blank_query(self) -> None:
        self._run("entry", "add", "hello")
        for flags, message in (
            (("--person", "nobody"), "person 'nobody' not found"),
            (("--tag", "nosuchtag"), "tag 'nosuchtag' not found"),
            (("--query", "  "), "query must be a non-empty string"),
        ):
            with self.subTest(flags=flags):
                rc, out, err = self._run("entry", "search", *flags)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)

    def test_blank_filters_are_rejected_not_ignored(self) -> None:
        self._run("entry", "add", "hello", "--tag", "work")
        for command, flags, message in (
            (("entry", "search"), ("--person", ""), "person handle must be a non-empty"),
            (("entry", "search"), ("--person", "@"), "person handle must be a non-empty"),
            (("entry", "search"), ("--tag", " "), "tag must be a non-empty"),
            (("entry", "search"), ("--source", ""), "source must be a non-empty"),
            (("review", "list"), ("--person", ""), "person handle must be a non-empty"),
            (("review", "list"), ("--tag", ""), "tag must be a non-empty"),
            (("goal", "list"), ("--person", ""), "person handle must be a non-empty"),
            (("feedback", "search"), ("--query", "x", "--person", ""), "non-empty"),
            (("review", "context"), ("--person", ""), "person handle must be a non-empty"),
        ):
            with self.subTest(command=command, flags=flags):
                rc, out, err = self._run(*command, *flags)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)

    def test_entry_add_rejects_empty_person_or_tag(self) -> None:
        for flag in ("--person", "--tag"):
            with self.subTest(flag=flag):
                rc, out, err = self._run("entry", "add", "hello", flag, "")
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn(f"{flag} needs a", err)
        rc, out, _ = self._run("entry", "list")
        self.assertIn("(no entries)", out)

    def test_explicit_empty_body_is_rejected_not_opened_in_the_editor(self) -> None:
        self._run("entry", "add", "hello")
        rc, _, err = self._run("entry", "edit", "1", "--body", "")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)


class JsonOutputTests(EntryTestBase):
    def test_json_output_keeps_non_ascii_text_readable(self) -> None:
        rc, out, _ = self._run("--json", "person", "add", "bob", "--name", "Bób")
        self.assertEqual(rc, 0)
        self.assertIn('"Bób"', out)
