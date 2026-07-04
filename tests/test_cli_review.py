"""Tests for the ``commscoach review`` subcommands."""

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


class ReviewTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "reviews.sqlite3"
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

    def _seed_self(self, handle: str = "me") -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, is_self=True)

    def _seed_entry(self, body: str = "seed body") -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(conn, body)

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)

    def _seed_review(
        self,
        body: str,
        entry_ids: list[int],
        *,
        scope_limit: int | None = 20,
    ) -> db.Review:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_review(
                conn, body, entry_ids, scope_limit=scope_limit
            )


class ReviewContextTests(ReviewTestBase):
    def test_context_emits_review_synthesis_shape(self) -> None:
        self._seed_self("coach")
        first = self._seed_entry("first")
        second = self._seed_entry("second")
        third = self._seed_entry("third")
        self._seed_feedback(second.id, "second note")
        prior = self._seed_review("prior review", [first.id], scope_limit=1)

        rc, out, _ = self._run("review", "context", "--limit", "2")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            set(payload.keys()),
            {"self", "scope", "entries", "prior_reviews"},
        )
        self.assertEqual(payload["self"]["handle"], "coach")
        self.assertEqual(
            payload["scope"],
            {"kind": "latest", "limit": 2, "entry_count": 2},
        )
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [second.id, third.id],
        )
        feedback_by_entry = {
            item["entry"]["id"]: item["feedback"] for item in payload["entries"]
        }
        self.assertEqual(feedback_by_entry[second.id][0]["body"], "second note")
        self.assertEqual(feedback_by_entry[third.id], [])
        self.assertEqual([row["id"] for row in payload["prior_reviews"]], [prior.id])
        self.assertEqual(payload["prior_reviews"][0]["entry_ids"], [first.id])

    def test_context_missing_self_exits_one(self) -> None:
        self._seed_entry("orphan")
        rc, _, err = self._run("review", "context")
        self.assertEqual(rc, 1)
        self.assertIn("self", err.lower())

    def test_context_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "review",
                        "context",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)

    def test_context_prior_review_limit_one(self) -> None:
        self._seed_self("coach")
        entry = self._seed_entry("entry")
        self._seed_review("first review", [entry.id])
        self._seed_review("second review", [entry.id])
        self._seed_review("third review", [entry.id])
        latest = self._seed_review("latest review", [entry.id])

        rc, out, _ = self._run(
            "review", "context", "--prior-review-limit", "1"
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            [row["id"] for row in payload["prior_reviews"]],
            [latest.id],
        )

    def test_context_prior_review_limit_zero(self) -> None:
        self._seed_self("coach")
        entry = self._seed_entry("entry")
        self._seed_review("prior review", [entry.id])

        rc, out, _ = self._run(
            "review", "context", "--prior-review-limit", "0"
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["prior_reviews"], [])

    def test_context_negative_prior_review_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "review",
                        "context",
                        "--prior-review-limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)


class ReviewAddTests(ReviewTestBase):
    def test_add_creates_review_with_default_scope(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")

        rc, out, _ = self._run(
            "review",
            "add",
            "weekly synthesis",
            "--entry",
            str(second.id),
            str(first.id),
        )

        self.assertEqual(rc, 0)
        self.assertIn("created review 1", out)
        self.assertIn("weekly synthesis", out)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.scope_kind, "latest")
        self.assertEqual(review.scope_limit, 20)
        self.assertEqual([entry.id for entry in review.entries], [second.id, first.id])

    def test_add_json_output_uses_review_shape(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")

        rc, out, _ = self._run(
            "--json",
            "review",
            "add",
            "json synthesis",
            "--entry",
            str(second.id),
            "--entry",
            str(first.id),
            "--limit",
            "5",
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            set(payload.keys()),
            {
                "id",
                "body",
                "scope_kind",
                "scope_limit",
                "created_at",
                "updated_at",
                "entry_ids",
                "entries",
            },
        )
        self.assertEqual(payload["body"], "json synthesis")
        self.assertEqual(payload["scope_kind"], "latest")
        self.assertEqual(payload["scope_limit"], 5)
        self.assertEqual(payload["entry_ids"], [second.id, first.id])
        self.assertEqual([entry["id"] for entry in payload["entries"]], [second.id, first.id])

    def test_add_body_from_stdin(self) -> None:
        entry = self._seed_entry("entry")
        rc, _, _ = self._run(
            "review",
            "add",
            "-",
            "--entry",
            str(entry.id),
            stdin=io.StringIO("piped review\n"),
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.body, "piped review\n")

    def test_add_body_from_file(self) -> None:
        entry = self._seed_entry("entry")
        body_file = self.tmpdir / "review.txt"
        body_file.write_text("file review", encoding="utf-8")

        rc, _, _ = self._run(
            "review", "add", f"@{body_file}", "--entry", str(entry.id)
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.body, "file review")

    def test_add_missing_file_exits_one(self) -> None:
        entry = self._seed_entry("entry")
        rc, _, err = self._run(
            "review", "add", "@/no/such/review.txt", "--entry", str(entry.id)
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)

    def test_add_unknown_entry_exits_one_without_writing(self) -> None:
        rc, _, err = self._run("review", "add", "body", "--entry", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_add_without_entry_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "review",
                        "add",
                        "body",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)


class ReviewListTests(ReviewTestBase):
    def test_list_empty_prints_marker(self) -> None:
        rc, out, _ = self._run("review", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no reviews)", out)

    def test_list_newest_first_and_limit(self) -> None:
        first_entry = self._seed_entry("first entry")
        second_entry = self._seed_entry("second entry")
        first = self._seed_review("first review", [first_entry.id])
        second = self._seed_review("second review", [second_entry.id])
        third = self._seed_review("third review", [first_entry.id])

        rc, out, _ = self._run("review", "list", "--limit", "2")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{third.id}]", out)
        self.assertIn(f"[{second.id}]", out)
        self.assertIn("context-limit:20", out)
        self.assertNotIn(f"[{first.id}]", out)
        self.assertLess(out.find(f"[{third.id}]"), out.find(f"[{second.id}]"))

    def test_list_json_payload(self) -> None:
        first_entry = self._seed_entry("first entry")
        second_entry = self._seed_entry("second entry")
        first = self._seed_review("first review", [first_entry.id])
        second = self._seed_review("second review", [second_entry.id])

        rc, out, _ = self._run("--json", "review", "list")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([row["id"] for row in payload], [second.id, first.id])
        self.assertEqual(payload[0]["entry_ids"], [second_entry.id])

    def test_list_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "review",
                        "list",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)


class ReviewGetTests(ReviewTestBase):
    def test_get_existing_prints_human(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("strong summary", [entry.id])

        rc, out, _ = self._run("review", "get", str(review.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"[{review.id}]", out)
        self.assertIn("context limit 20", out)
        self.assertIn("strong summary", out)
        self.assertIn(f"entries: {entry.id}", out)

    def test_get_json_round_trip(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("stored summary", [entry.id])

        rc, out, _ = self._run("--json", "review", "get", str(review.id))

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], review.id)
        self.assertEqual(payload["body"], "stored summary")
        self.assertEqual(payload["entry_ids"], [entry.id])

    def test_get_missing_exits_one(self) -> None:
        rc, _, err = self._run("review", "get", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_get_non_integer_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--db", str(self.db_path), "review", "get", "abc"])
        self.assertEqual(ctx.exception.code, 2)


class ReviewRmTests(ReviewTestBase):
    def test_rm_with_yes_deletes(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("delete me", [entry.id])

        rc, out, _ = self._run("--yes", "review", "rm", str(review.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"deleted review {review.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_review(conn, review.id))

    def test_rm_confirm_no_aborts(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("keep me", [entry.id])

        with mock.patch.object(cli, "confirm", return_value=False):
            rc, _, err = self._run("review", "rm", str(review.id))

        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_review(conn, review.id))

    def test_rm_json_output(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("delete me", [entry.id])

        rc, out, _ = self._run("--yes", "--json", "review", "rm", str(review.id))

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"deleted": True, "id": review.id})

    def test_rm_missing_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "review", "rm", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)


class ReviewHelpTests(ReviewTestBase):
    def test_review_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("review")
        self.assertEqual(rc, 0)
        self.assertIn("context", out)
        self.assertIn("add", out)
        self.assertIn("list", out)
        self.assertIn("get", out)
        self.assertIn("rm", out)


if __name__ == "__main__":
    unittest.main()
