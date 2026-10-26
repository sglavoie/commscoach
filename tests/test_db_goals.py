"""Tests for goal CRUD and schema invariants."""

from __future__ import annotations

import dataclasses
import sqlite3

from commscoach import db

from .db_case import DBTestCase

T0 = "2026-08-25T10:00:00+00:00"
T1 = "2026-08-25T11:00:00+00:00"
T2 = "2026-08-25T12:00:00+00:00"


class GoalCrudTests(DBTestCase):
    def test_outcome_update_changes_only_outcome_and_update_timestamp(self) -> None:
        goal = db.create_goal(self.conn, "Experiment", now=T0)
        closed = db.close_goal(self.conn, goal.id, outcome="Original", dropped=True, now=T1)
        edited = db.update_goal(self.conn, goal.id, outcome="Corrected", now=T2)
        self.assertEqual(edited.outcome, "Corrected")
        self.assertEqual(edited.status, "dropped")
        self.assertEqual(edited.closed_at, closed.closed_at)
        self.assertEqual(edited.created_at, T0)
        self.assertEqual(edited.updated_at, T2)
        cleared = db.update_goal(self.conn, goal.id, outcome=None, now=T2)
        self.assertIsNone(cleared.outcome)
        self.assertEqual(cleared.closed_at, T1)

    def test_unknown_person_is_rejected_even_with_general_goals_or_zero_limit(self) -> None:
        db.create_goal(self.conn, "General goal")
        for options in ({}, {"include_general": True}, {"limit": 0}, {"query": "General"}):
            with self.subTest(options=options), self.assertRaises(db.PersonNotFoundError):
                db.list_goals(self.conn, person_handle="missing", **options)
        db.create_person(self.conn, "alice")
        self.assertEqual(db.list_goals(self.conn, person_handle="ALICE"), ())

    def test_query_filters_all_text_fields_before_sorting_and_limit(self) -> None:
        db.create_person(self.conn, "alice")
        title = db.create_goal(self.conn, "Try A_B!", person_handle="alice", now=T0)
        detail = db.create_goal(
            self.conn, "Detail", detail="Try a_b!", person_handle="alice", now=T1
        )
        closed = db.create_goal(self.conn, "Outcome", person_handle="alice", now=T2)
        db.close_goal(self.conn, closed.id, outcome="Tried A_B!", now=T2)
        db.create_goal(self.conn, "Wrong person A_B!", now=T2)
        db.create_goal(self.conn, "Try AXB!", person_handle="alice", now=T2)
        found = db.list_goals(self.conn, query="a_b!", person_handle="ALICE", status=None)
        self.assertEqual([goal.id for goal in found], [detail.id, title.id, closed.id])
        self.assertEqual(
            db.list_goals(
                self.conn, query="a_b!", person_handle="alice", oldest_first=True, limit=1
            ),
            (title,),
        )
        with self.assertRaisesRegex(db.DBError, "non-empty"):
            db.list_goals(self.conn, query=" ")

    def test_person_general_and_review_filters_apply_before_limit(self) -> None:
        db.create_person(self.conn, "alice")
        db.create_person(self.conn, "bob")
        review = db.create_review(self.conn, "review", [db.create_entry(self.conn, "entry").id])
        linked = db.create_goal(
            self.conn, "linked", person_handle="alice", review_id=review.id, now=T0
        )
        general = db.create_goal(self.conn, "general", review_id=review.id, now=T1)
        closed = db.create_goal(self.conn, "closed general", review_id=review.id, now=T2)
        db.close_goal(self.conn, closed.id)
        db.create_goal(self.conn, "bob", person_handle="bob", review_id=review.id, now=T2)
        db.create_goal(self.conn, "another review", person_handle="alice", now=T2)
        self.assertEqual(
            db.list_goals(
                self.conn, person_handle="ALICE", include_general=True, review_id=review.id
            ),
            (general, linked),
        )
        self.assertEqual(
            db.list_goals(
                self.conn,
                person_handle="ALICE",
                include_general=True,
                review_id=review.id,
                oldest_first=True,
                limit=1,
            ),
            (linked,),
        )
        with self.assertRaisesRegex(db.DBError, "requires person_handle"):
            db.list_goals(self.conn, include_general=True)

    def test_repeated_status_edit_preserves_timestamps_and_outcome(self) -> None:
        for status in ("active", "done", "dropped"):
            with self.subTest(status=status):
                goal = db.create_goal(self.conn, "goal", now=T0)
                if status != "active":
                    goal = db.close_goal(
                        self.conn,
                        goal.id,
                        outcome="original outcome",
                        dropped=status == "dropped",
                        now=T1,
                    )
                self.assertEqual(
                    db.update_goal(self.conn, goal.id, status=status, now=T2),
                    goal,
                )
                edited = db.update_goal(
                    self.conn,
                    goal.id,
                    status=status,
                    detail="new detail",
                    now=T2,
                )
                self.assertEqual(edited.detail, "new detail")
                self.assertEqual(edited.updated_at, T2)
                self.assertEqual(edited.closed_at, goal.closed_at)
                self.assertEqual(edited.outcome, goal.outcome)
                self.assertEqual(edited.created_at, T0)

    def test_oldest_first_sorts_before_limit_with_id_tiebreaker(self) -> None:
        alice = db.create_person(self.conn, "alice")
        db.create_goal(self.conn, "unrelated", now=T0)
        first = db.create_goal(self.conn, "first", person_handle=alice.handle, now=T1)
        second = db.create_goal(self.conn, "second", person_handle=alice.handle, now=T1)
        db.create_goal(self.conn, "newest", person_handle=alice.handle, now=T2)
        self.assertEqual(
            db.list_goals(self.conn, person_handle="ALICE", oldest_first=True, limit=2),
            (first, second),
        )
        self.assertEqual(db.list_goals(self.conn, oldest_first=True, limit=0), ())

    def test_oldest_first_all_keeps_active_goals_first(self) -> None:
        closed = db.create_goal(self.conn, "closed", now=T0)
        closed = db.close_goal(self.conn, closed.id, now=T1)
        older = db.create_goal(self.conn, "older", now=T1)
        newer = db.create_goal(self.conn, "newer", now=T2)
        self.assertEqual(
            db.list_goals(self.conn, status=None, oldest_first=True),
            (older, newer, closed),
        )

    def test_repeated_close_preserves_outcome_and_timestamps(self) -> None:
        for dropped in (False, True):
            with self.subTest(dropped=dropped):
                goal = db.create_goal(self.conn, "goal", now=T0)
                closed = db.close_goal(
                    self.conn, goal.id, outcome="worked", dropped=dropped, now=T1
                )
                self.assertEqual(db.close_goal(self.conn, goal.id, dropped=dropped, now=T2), closed)
                self.assertEqual(
                    db.close_goal(self.conn, goal.id, outcome="worked", dropped=dropped, now=T2),
                    closed,
                )

    def test_close_can_correct_outcome_or_change_status(self) -> None:
        goal = db.create_goal(self.conn, "goal", now=T0)
        db.close_goal(self.conn, goal.id, outcome="original", now=T1)
        corrected = db.close_goal(self.conn, goal.id, outcome="corrected", now=T2)
        self.assertEqual(
            (corrected.outcome, corrected.closed_at, corrected.updated_at), ("corrected", T1, T2)
        )
        dropped = db.close_goal(self.conn, goal.id, dropped=True, now=T2)
        self.assertEqual(
            (dropped.status, dropped.outcome, dropped.closed_at), ("dropped", "corrected", T2)
        )

    def test_create_and_get_goal_hydrates_relationships(self) -> None:
        person = db.create_person(self.conn, "alex")
        entry = db.create_entry(self.conn, "entry")
        review = db.create_review(self.conn, "review", [entry.id])

        goal = db.create_goal(
            self.conn,
            "Ask a clarifying question",
            detail="In the next 1:1",
            person_handle="alex",
            review_id=review.id,
            now=T0,
        )

        self.assertTrue(goal.is_open)
        self.assertEqual(goal.status, "active")
        self.assertEqual(goal.created_at, T0)
        self.assertEqual(goal.person, person)
        # The goal carries its source review without the covered entries.
        self.assertEqual(goal.review, dataclasses.replace(review, entries=()))
        self.assertEqual(db.get_goal(self.conn, goal.id), goal)

    def test_create_rejects_blank_title(self) -> None:
        with self.assertRaisesRegex(db.DBError, "title"):
            db.create_goal(self.conn, "   ")

    def test_create_resolves_references_before_write(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.create_goal(self.conn, "title", person_handle="missing")
        self.assertEqual(db.list_goals(self.conn), ())

        with self.assertRaises(db.ReviewNotFoundError):
            db.create_goal(self.conn, "title", review_id=999)
        self.assertEqual(db.list_goals(self.conn), ())

    def test_update_fields_and_reopen_explicitly_clears_closed_at(self) -> None:
        person = db.create_person(self.conn, "alex")
        goal = db.create_goal(self.conn, "old", now=T0)
        closed = db.close_goal(self.conn, goal.id, outcome="worked", now=T1)
        self.assertEqual(closed.closed_at, T1)

        updated = db.update_goal(
            self.conn,
            goal.id,
            title="new",
            detail="details",
            person_handle="alex",
            status="active",
            now=T2,
        )

        self.assertEqual(updated.title, "new")
        self.assertEqual(updated.detail, "details")
        self.assertEqual(updated.person, person)
        self.assertEqual(updated.status, "active")
        self.assertIsNone(updated.closed_at)
        self.assertEqual(updated.updated_at, T2)

    def test_update_closes_done_or_dropped_with_python_timestamp(self) -> None:
        goal = db.create_goal(self.conn, "goal", now=T0)
        done = db.update_goal(self.conn, goal.id, status="done", now=T1)
        self.assertEqual((done.status, done.closed_at), ("done", T1))
        dropped = db.update_goal(self.conn, goal.id, status="dropped", now=T2)
        self.assertEqual((dropped.status, dropped.closed_at), ("dropped", T2))

    def test_update_unknown_reference_leaves_goal_unchanged(self) -> None:
        goal = db.create_goal(self.conn, "original", now=T0)
        with self.assertRaises(db.PersonNotFoundError):
            db.update_goal(
                self.conn,
                goal.id,
                title="should not stick",
                person_handle="missing",
                now=T1,
            )
        self.assertEqual(db.get_goal(self.conn, goal.id), goal)

    def test_clear_relationships(self) -> None:
        person = db.create_person(self.conn, "alex")
        entry = db.create_entry(self.conn, "entry")
        review = db.create_review(self.conn, "review", [entry.id])
        goal = db.create_goal(
            self.conn,
            "goal",
            person_handle=person.handle,
            review_id=review.id,
            now=T0,
        )
        updated = db.update_goal(self.conn, goal.id, person_handle=None, review_id=None, now=T1)
        self.assertIsNone(updated.person)
        self.assertIsNone(updated.review)

    def test_close_done_and_dropped(self) -> None:
        first = db.create_goal(self.conn, "first", now=T0)
        second = db.create_goal(self.conn, "second", now=T0)
        done = db.close_goal(self.conn, first.id, outcome="success", now=T1)
        dropped = db.close_goal(self.conn, second.id, dropped=True, now=T2)
        self.assertEqual((done.status, done.outcome, done.closed_at), ("done", "success", T1))
        self.assertEqual((dropped.status, dropped.closed_at), ("dropped", T2))

    def test_list_defaults_active_and_supports_all_person_limit(self) -> None:
        db.create_person(self.conn, "alex")
        first = db.create_goal(self.conn, "first", person_handle="alex", now=T0)
        second = db.create_goal(self.conn, "second", now=T1)
        third = db.create_goal(self.conn, "third", person_handle="alex", now=T2)
        db.close_goal(self.conn, third.id, now=T2)

        self.assertEqual([g.id for g in db.list_goals(self.conn)], [second.id, first.id])
        self.assertEqual(
            [g.id for g in db.list_goals(self.conn, status=None)],
            [second.id, first.id, third.id],
        )
        self.assertEqual(
            [g.id for g in db.list_goals(self.conn, status=None, person_handle="alex", limit=1)],
            [first.id],
        )

    def test_list_rejects_bad_status_and_limit(self) -> None:
        with self.assertRaisesRegex(db.DBError, "status"):
            db.list_goals(self.conn, status="paused")
        with self.assertRaisesRegex(db.DBError, "limit"):
            db.list_goals(self.conn, limit=-1)

    def test_delete_and_missing_errors(self) -> None:
        goal = db.create_goal(self.conn, "goal", now=T0)
        self.assertTrue(db.delete_goal(self.conn, goal.id))
        self.assertFalse(db.delete_goal(self.conn, goal.id))
        with self.assertRaises(db.GoalNotFoundError):
            db.update_goal(self.conn, goal.id, title="missing")
        with self.assertRaises(db.GoalNotFoundError):
            db.close_goal(self.conn, goal.id)


class GoalSchemaInvariantTests(DBTestCase):
    def test_raw_update_cannot_break_closed_at_invariant(self) -> None:
        goal = db.create_goal(self.conn, "goal", now=T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE goals SET status = 'done' WHERE id = ?", (goal.id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE goals SET closed_at = ? WHERE id = ?", (T1, goal.id))

    def test_deleting_person_and_review_sets_goal_foreign_keys_null(self) -> None:
        person = db.create_person(self.conn, "alex")
        entry = db.create_entry(self.conn, "entry")
        review = db.create_review(self.conn, "review", [entry.id])
        goal = db.create_goal(
            self.conn,
            "goal",
            person_handle=person.handle,
            review_id=review.id,
            now=T0,
        )

        db.delete_person(self.conn, person.id)
        db.delete_review(self.conn, review.id)

        kept = db.get_goal(self.conn, goal.id)
        assert kept is not None
        self.assertIsNone(kept.person)
        self.assertIsNone(kept.review)

    def test_person_note_is_one_to_one_and_cascades(self) -> None:
        person = db.create_person(self.conn, "alex")
        self.conn.execute(
            "INSERT INTO person_notes (person_id, body, created_at, updated_at)"
            " VALUES (?, 'note', ?, ?)",
            (person.id, T0, T0),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO person_notes (person_id, body, created_at, updated_at)"
                " VALUES (?, 'duplicate', ?, ?)",
                (person.id, T0, T0),
            )
        self.conn.rollback()
        db.delete_person(self.conn, person.id)
        count = self.conn.execute("SELECT COUNT(*) FROM person_notes").fetchone()[0]
        self.assertEqual(count, 0)
