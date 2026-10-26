"""Tests for secure editor-backed body composition."""

from __future__ import annotations

import os
import shlex
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from commscoach import db
from commscoach.cli.editor import (
    compose_in_editor,
    editor_command,
    strip_comment_lines,
)


class EditorCommandTests(unittest.TestCase):
    def test_visual_takes_precedence(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"VISUAL": "visual-editor", "EDITOR": "fallback-editor"},
            clear=True,
        ):
            self.assertEqual(editor_command(), ["visual-editor"])

    def test_editor_is_used_without_visual(self) -> None:
        with mock.patch.dict(os.environ, {"EDITOR": "nano"}, clear=True):
            self.assertEqual(editor_command(), ["nano"])

    def test_vi_is_the_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(editor_command(), ["vi"])

    def test_editor_options_are_shell_split(self) -> None:
        with mock.patch.dict(os.environ, {"EDITOR": "code -w"}, clear=True):
            self.assertEqual(editor_command(), ["code", "-w"])


class EditorCompositionTests(unittest.TestCase):
    def test_strip_comment_lines_preserves_markdown_headings(self) -> None:
        text = "#> ignored\n## Markdown Heading\nBody\n"
        self.assertEqual(
            strip_comment_lines(text),
            "## Markdown Heading\nBody\n",
        )

    def test_compose_uses_secure_temporary_file_and_removes_it(self) -> None:
        observed_path: Path | None = None

        def fake_runner(command: list[str]) -> int:
            nonlocal observed_path
            self.assertEqual(command[:-1], ["fake-editor"])
            observed_path = Path(command[-1])
            self.assertEqual(stat.S_IMODE(observed_path.stat().st_mode), 0o600)
            initial = observed_path.read_text(encoding="utf-8")
            self.assertIn("#> instructions", initial)
            self.assertIn("seed body", initial)
            observed_path.write_text(
                "#> instructions\n\n## Markdown Heading\nEdited body\n",
                encoding="utf-8",
            )
            return 0

        with mock.patch.dict(
            os.environ,
            {"EDITOR": "fake-editor"},
            clear=True,
        ):
            result = compose_in_editor(
                template="seed body",
                preamble="instructions",
                runner=fake_runner,
            )

        self.assertEqual(result, "## Markdown Heading\nEdited body")
        assert observed_path is not None
        self.assertFalse(observed_path.exists())

    def test_compose_rejects_nonzero_editor_exit(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"EDITOR": "fake-editor"},
            clear=True,
        ):
            with self.assertRaisesRegex(db.DBError, "status 7"):
                compose_in_editor(
                    template="",
                    preamble="instructions",
                    runner=lambda _command: 7,
                )

    def test_compose_rejects_empty_body_after_comment_stripping(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"EDITOR": "fake-editor"},
            clear=True,
        ):
            with self.assertRaisesRegex(db.DBError, "empty body"):
                compose_in_editor(
                    template="",
                    preamble="instructions",
                    runner=lambda _command: 0,
                )

    def test_compose_with_real_subprocess(self) -> None:
        script = (
            "import sys; open(sys.argv[-1], 'a', encoding='utf-8').write('\\nsubprocess body\\n')"
        )
        command = shlex.join([sys.executable, "-c", script])
        with mock.patch.dict(os.environ, {"EDITOR": command}, clear=True):
            result = compose_in_editor(template="", preamble="instructions")
        self.assertEqual(result, "subprocess body")


if __name__ == "__main__":
    unittest.main()
