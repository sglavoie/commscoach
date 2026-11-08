"""Shell completion: the ``completion`` command and the hidden ``__complete`` callback.

``commscoach completion {bash,zsh,fish}`` prints a script that, on every TAB,
runs ``commscoach __complete -- WORD...`` with the words typed so far (the last
one possibly partial). The callback prints one candidate per line, optionally
``value<TAB>description``; the bash script drops the descriptions.

Static candidates (subcommands, option strings, ``choices``) come from walking
the real parser, so they cannot drift from the CLI. Dynamic candidates come
from the database, opened read-only, according to each argument's *kind*:

- :func:`complete_as` marks an action explicitly, in one line at registration::

      complete_as(parser.add_argument("old", metavar="OLD"), TAG)

- otherwise the kind is inferred from a conventional metavar
  (:data:`_KIND_BY_METAVAR`): ``HANDLE``, ``TAG``, ``ENTRY_ID``, ``GOAL_ID``,
  ``FEEDBACK_ID``, ``REVIEW_ID``, and ``PATH``.

The callback never creates, migrates, or writes a database, and never prints a
traceback: a missing database or an unexpected schema just leaves the static
candidates.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from .. import db, paths
from .common import first_prose_line, truncate

CALLBACK = "__complete"
SHELLS = ("bash", "zsh", "fish")
# Printed alone to ask the shell for its own file-name completion.
FILES_DIRECTIVE = "__commscoach_files__"

HANDLE = "handle"
TAG = "tag"
ENTRY = "entry"
GOAL = "goal"
FEEDBACK = "feedback"
REVIEW = "review"
PATH = "path"

_KIND_ATTR = "completion_kind"
_KIND_BY_METAVAR = {
    "HANDLE": HANDLE,
    "TAG": TAG,
    "ENTRY_ID": ENTRY,
    "GOAL_ID": GOAL,
    "FEEDBACK_ID": FEEDBACK,
    "REVIEW_ID": REVIEW,
    "PATH": PATH,
    "PATH|-": PATH,
}
# Kinds whose values may be typed as a comma-separated list (``--tag a,b``).
_LISTABLE = {HANDLE, TAG}
_ID_LIMIT = 50
_DESCRIPTION_WIDTH = 60


def complete_as(action: argparse.Action, kind: str | None) -> argparse.Action:
    """Mark ``action`` as completing values of ``kind`` (``None`` opts out); returns it."""
    setattr(action, _KIND_ATTR, kind)
    return action


def value_kind(action: argparse.Action) -> str | None:
    """Return the dynamic kind an action completes: explicit mark, else metavar."""
    if hasattr(action, _KIND_ATTR):
        return getattr(action, _KIND_ATTR)
    metavar = action.metavar if isinstance(action.metavar, str) else None
    return _KIND_BY_METAVAR.get(metavar or "")


# --------------------------------------------------------------------------
# The ``completion`` command


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "completion",
        help="Print a shell completion script (bash, zsh, or fish).",
        description=(
            "Print a completion script for SHELL. Load it with, e.g.,"
            ' eval "$(commscoach completion zsh)" in your shell rc file.'
        ),
    )
    parser.add_argument("shell", choices=SHELLS, help="Target shell.")
    parser.set_defaults(func=cmd_completion)


def cmd_completion(args: argparse.Namespace) -> int:
    sys.stdout.write(script_for(args.shell))
    return 0


def script_for(shell: str, prog: str = "commscoach") -> str:
    """Return the completion script for ``shell``."""
    template = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}[shell]
    return (
        template.replace("@PROG@", prog)
        .replace("@FUNC@", "_" + prog.replace("-", "_"))
        .replace("@CALLBACK@", CALLBACK)
        .replace("@FILES@", FILES_DIRECTIVE)
    )


_BASH = r"""# bash completion for @PROG@
# Load with: eval "$(@PROG@ completion bash)"
@FUNC@_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local IFS=$'\n'
    local -a lines
    lines=($(command @PROG@ @CALLBACK@ -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null))
    if [[ "${lines[0]}" == "@FILES@" ]]; then
        [[ "$cur" == "=" ]] && cur=""
        compopt -o filenames 2>/dev/null
        COMPREPLY=($(compgen -f -- "$cur"))
        return 0
    fi
    COMPREPLY=("${lines[@]%%$'\t'*}")
    # bash splits "--opt=value" at "="; keep the "=" the completion replaces.
    if [[ "$cur" == "=" ]]; then
        COMPREPLY=("${COMPREPLY[@]/#/=}")
    fi
    return 0
}
complete -F @FUNC@_complete @PROG@
"""

_ZSH = r"""#compdef @PROG@
# zsh completion for @PROG@
# Load with: eval "$(@PROG@ completion zsh)" after compinit, or save this
# output as _@PROG@ in a directory on $fpath.
@FUNC@() {
    local -a lines candidates
    local line value
    lines=("${(@f)$(command @PROG@ @CALLBACK@ -- "${(@)words[2,CURRENT]}" 2>/dev/null)}")
    if [[ "${lines[1]}" == "@FILES@" ]]; then
        _files
        return
    fi
    for line in "${lines[@]}"; do
        [[ -z "$line" ]] && continue
        value="${line%%$'\t'*}"
        value="${value//:/\\:}"
        if [[ "$line" == *$'\t'* ]]; then
            candidates+=("${value}:${line#*$'\t'}")
        else
            candidates+=("$value")
        fi
    done
    (( ${#candidates} )) && _describe -t values '@PROG@' candidates
}
if [[ "${funcstack[1]}" == "@FUNC@" ]]; then
    @FUNC@ "$@"
else
    compdef @FUNC@ @PROG@
fi
"""

_FISH = r"""# fish completion for @PROG@
# Load with: @PROG@ completion fish | source
# or save it as ~/.config/fish/completions/@PROG@.fish
function __@PROG@_complete
    set -l words (commandline -opc)
    set -e words[1]
    set -l current (commandline -ct)
    set -l lines (command @PROG@ @CALLBACK@ -- $words $current 2>/dev/null)
    if test "$lines[1]" = "@FILES@"
        __fish_complete_path $current
        return
    end
    printf '%s\n' $lines
end
complete -c @PROG@ -f -a '(__@PROG@_complete)'
"""


# --------------------------------------------------------------------------
# The ``__complete`` callback


def run_callback(parser: argparse.ArgumentParser, words: Sequence[str]) -> int:
    """Print candidates for the last of ``words``; always exits 0, never raises."""
    try:
        words = list(words)
        if words[:1] == ["--"]:
            words = words[1:]
        lines = complete(parser, words)
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
    except Exception:  # completion must never print a traceback into the prompt
        pass
    return 0


class _State:
    """Where the typed words leave the parser walk."""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser
        self.positional = 0
        self.pending: argparse.Action | None = None
        self.pending_values = 0
        self.seen: set[str] = set()
        self.only_positionals = False
        self.db_arg: str | None = None

    def descend(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser
        self.positional = 0
        self.pending = None
        self.seen = set()

    def positionals(self) -> list[argparse.Action]:
        return [a for a in self.parser._actions if not a.option_strings]


def complete(parser: argparse.ArgumentParser, words: Sequence[str]) -> list[str]:
    """Return candidate lines (``value`` or ``value\\tdescription``) for ``words[-1]``."""
    joined, split_by_bash = _join_bash_equals(list(words) or [""])
    *done, partial = joined
    state = _State(parser)
    for word in done:
        _consume(state, word)

    # "--opt=partial": complete the value. zsh and fish replace the whole word,
    # so candidates keep the "--opt=" prefix; bash replaces only the part after
    # "=", which it split into words of its own.
    prefix = ""
    if not state.only_positionals and partial.startswith("--") and "=" in partial:
        option, _, partial = partial.partition("=")
        action = state.parser._option_string_actions.get(option)
        if action is None or action.nargs == 0:
            return []
        state.pending, state.pending_values = action, 0
        prefix = "" if split_by_bash else option + "="

    pending = state.pending
    if pending is not None:
        required = pending.nargs in (None, 1) or (
            pending.nargs == "+" and state.pending_values == 0
        )
        if required or prefix:
            return [prefix + line for line in _values(pending, partial, state)]
        if not partial.startswith("-"):
            values = _values(pending, partial, state)
            if values:
                return values

    if partial.startswith("-") and not state.only_positionals:
        return _options(state, partial)
    positional = _current_positional(state)
    if positional is not None:
        if isinstance(positional, argparse._SubParsersAction):
            return _subcommands(positional, partial)
        values = _values(positional, partial, state)
        # A required free-text value (a new handle, a goal title) comes next:
        # offering options there would only suggest skipping it.
        if values or partial or positional.nargs is None:
            return values
    return [] if partial or state.only_positionals else _options(state, partial)


def _join_bash_equals(words: list[str]) -> tuple[list[str], bool]:
    """Rejoin bash's ``--opt`` ``=`` ``value`` split into ``--opt=value``.

    Also reports whether the last word came out of such a split, so the
    candidates for it are the bare values bash expects.
    """
    joined: list[str] = []
    last_split = False
    index = 0
    while index < len(words):
        word = words[index]
        last_split = False
        if word == "=" and joined and joined[-1].startswith("--") and "=" not in joined[-1]:
            joined[-1] += "="
            if index + 1 < len(words):
                index += 1
                joined[-1] += words[index]
            last_split = True
        else:
            joined.append(word)
        index += 1
    return joined, last_split


def _consume(state: _State, word: str) -> None:
    pending = state.pending
    if pending is not None:
        if pending.nargs in (None, 1) or not word.startswith("-") or word == "-":
            if pending.dest == "db":
                state.db_arg = word
            state.pending_values += 1
            if pending.nargs in (None, 1, "?"):
                state.pending = None
            return
        state.pending = None
    if state.only_positionals:
        _consume_positional(state, word)
        return
    if word == "--":
        state.only_positionals = True
        return
    if word.startswith("-") and word != "-":
        option, has_value, value = word.partition("=")
        action = state.parser._option_string_actions.get(option)
        if action is None:
            return
        state.seen.add(action.dest)
        if has_value and action.dest == "db":
            state.db_arg = value
        if action.nargs != 0 and not has_value:
            state.pending, state.pending_values = action, 0
        return
    _consume_positional(state, word)


def _consume_positional(state: _State, word: str) -> None:
    positionals = state.positionals()
    if state.positional >= len(positionals):
        return
    action = positionals[state.positional]
    if isinstance(action, argparse._SubParsersAction):
        child = action.choices.get(word)
        if child is not None:
            state.descend(child)
        else:
            state.positional += 1
        return
    if action.nargs not in ("*", "+", argparse.REMAINDER):
        state.positional += 1


def _current_positional(state: _State) -> argparse.Action | None:
    positionals = state.positionals()
    if state.positional < len(positionals):
        return positionals[state.positional]
    return None


def _subcommands(action: argparse._SubParsersAction, partial: str) -> list[str]:
    helps = {choice.dest: choice.help for choice in action._choices_actions}
    return [
        _help_line(name, helps.get(name))
        for name in action.choices
        if name.startswith(partial) and not name.startswith("_")
    ]


def _options(state: _State, partial: str) -> list[str]:
    lines = []
    for action in state.parser._actions:
        if action.help == argparse.SUPPRESS:
            continue
        repeatable = isinstance(action, (argparse._AppendAction, argparse._ExtendAction))
        if action.dest in state.seen and not repeatable and action.dest != "help":
            continue
        for option in action.option_strings:
            if option.startswith(partial):
                lines.append(_help_line(option, action.help))
    return lines


def _line(value: str, description: str | None) -> str:
    """Join a candidate and its one-line description with a tab."""
    text = " ".join((description or "").split())
    return f"{value}\t{truncate(text, width=_DESCRIPTION_WIDTH)}" if text else value


def _help_line(value: str, help_text: str | None) -> str:
    """Describe a subcommand or option by the first sentence of its help."""
    if not help_text or help_text == argparse.SUPPRESS:
        return value
    sentence = " ".join(help_text.replace("%%", "%").split()).split(". ")[0]
    return _line(value, sentence.rstrip("."))


def _values(action: argparse.Action, partial: str, state: _State) -> list[str]:
    kind = value_kind(action)
    if kind is None:
        if action.choices is not None and not isinstance(action, argparse._SubParsersAction):
            return [str(c) for c in action.choices if str(c).startswith(partial)]
        return []
    if kind == PATH:
        return [FILES_DIRECTIVE]
    head, item = "", partial
    if kind in _LISTABLE and "," in partial:
        head, _, item = partial.rpartition(",")
        head += ","
    sigil = ""
    if kind == HANDLE and item.startswith("@"):
        sigil, item = "@", item[1:]
    elif kind == TAG and item.startswith("#"):
        sigil, item = "#", item[1:]
    taken = {part.strip().lstrip("@#").casefold() for part in head.split(",")}
    lines = []
    for value, description in _query(kind, item, state):
        if kind in _LISTABLE and value.casefold() in taken:
            continue
        typed = sigil + value
        if kind == HANDLE and value.startswith("@") and not sigil:
            typed = "@" + value  # handle_arg strips one leading "@"
        lines.append(_line(head + typed, description))
    return lines


# --------------------------------------------------------------------------
# Read-only database access


def _query(kind: str, partial: str, state: _State) -> list[tuple[str, str | None]]:
    conn = _open_read_only(state.db_arg)
    if conn is None:
        return []
    with closing(conn):
        try:
            return _QUERIES[kind](conn, partial)
        except (sqlite3.Error, KeyError):
            return []


def _open_read_only(db_arg: str | None) -> sqlite3.Connection | None:
    try:
        db_path = paths.resolve_db_path(db_arg or None)
    except Exception:  # e.g. the legacy-iCloud refusal: offer static candidates only
        return None
    try:
        if not db_path.is_file():
            return None
        uri = f"file:{quote(str(Path(db_path).resolve()), safe='/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    except (OSError, sqlite3.Error):
        return None
    try:
        row = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error:
        row = None
    if row is None or str(row[0]) != str(db.SCHEMA_VERSION):
        conn.close()
        return None
    return conn


def _like_prefix(partial: str) -> str:
    escaped = partial.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _query_handles(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = conn.execute(
        "SELECT handle, name, is_self FROM people"
        " WHERE handle LIKE ? ESCAPE '\\' ORDER BY is_self DESC, handle",
        (_like_prefix(partial),),
    ).fetchall()
    return [
        (handle, " ".join(filter(None, [name, "(self)" if is_self else None])) or None)
        for handle, name, is_self in rows
        if "," not in handle and not any(ch.isspace() for ch in handle)
    ]


def _query_tags(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = conn.execute(
        "SELECT t.name, COUNT(et.entry_id) FROM tags t"
        " LEFT JOIN entry_tags et ON et.tag_id = t.id"
        " WHERE t.name LIKE ? ESCAPE '\\' GROUP BY t.id ORDER BY t.name",
        (_like_prefix(partial),),
    ).fetchall()
    return [
        (name, f"{count} {'entry' if count == 1 else 'entries'}")
        for name, count in rows
        if "," not in name and not any(ch.isspace() for ch in name)
    ]


def _id_rows(conn: sqlite3.Connection, sql: str, partial: str) -> list[tuple]:
    if partial and not partial.isdigit():
        return []
    return conn.execute(sql, (_like_prefix(partial), _ID_LIMIT)).fetchall()


def _excerpt(body: str) -> str:
    return truncate(first_prose_line(body or ""), width=_DESCRIPTION_WIDTH)


def _query_entries(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = _id_rows(
        conn,
        "SELECT id, substr(body, 1, 400), substr(coalesce(occurred_at, created_at), 1, 10)"
        " FROM entries WHERE CAST(id AS TEXT) LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        partial,
    )
    return [(str(row_id), f"{day} {_excerpt(body)}") for row_id, body, day in rows]


def _query_goals(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = _id_rows(
        conn,
        "SELECT id, title, status FROM goals"
        " WHERE CAST(id AS TEXT) LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        partial,
    )
    return [
        (str(row_id), f"[{status}] {truncate(title, width=50)}") for row_id, title, status in rows
    ]


def _query_feedback(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = _id_rows(
        conn,
        "SELECT id, entry_id, substr(body, 1, 400) FROM feedback"
        " WHERE CAST(id AS TEXT) LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        partial,
    )
    return [(str(row_id), f"entry {entry_id}: {_excerpt(body)}") for row_id, entry_id, body in rows]


def _query_reviews(conn: sqlite3.Connection, partial: str) -> list[tuple[str, str | None]]:
    rows = _id_rows(
        conn,
        "SELECT id, scope_kind, substr(body, 1, 400) FROM reviews"
        " WHERE CAST(id AS TEXT) LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        partial,
    )
    return [(str(row_id), f"[{scope}] {_excerpt(body)}") for row_id, scope, body in rows]


_QUERIES = {
    HANDLE: _query_handles,
    TAG: _query_tags,
    ENTRY: _query_entries,
    GOAL: _query_goals,
    FEEDBACK: _query_feedback,
    REVIEW: _query_reviews,
}
