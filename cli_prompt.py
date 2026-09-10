# -*- coding: utf-8 -*-
"""
Interactive input for the SSH CLI dashboard: command completion + searchable history.

Built on prompt_toolkit (TAB completion, Ctrl+R reverse-search, persistent
history). If prompt_toolkit is missing (or stdin is not a TTY) the module stays
importable (PromptSession.available == False) and cli_dashboard falls back to
rich.Prompt / input().

History is written to a file (default /app/.cli_history, bind-mounted in compose.yaml,
survives docker compose --force-recreate). Secrets never go into the file: commands in
SECRET_ARG_COMMANDS are stored without arguments (see redact_for_history).
"""

import os
import sys
from typing import Dict, List, Optional

# Commands whose positional argument is a secret (pre-auth key, etc.).
# Only the command name is written to history, without arguments.
SECRET_ARG_COMMANDS = {"/headscale_revoke"}

DEFAULT_HISTORY_PATH = os.getenv("CLI_HISTORY_PATH", "/app/.cli_history")


def redact_for_history(line: str) -> Optional[str]:
    """What to store in history for this input line (or None — skip).

    Empty lines are skipped. For SECRET_ARG_COMMANDS with arguments,
    only the command name is returned (the secret never hits disk).
    """
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split()
    cmd = parts[0].lower()
    if cmd in SECRET_ARG_COMMANDS and len(parts) > 1:
        return cmd
    return stripped


try:
    from prompt_toolkit import PromptSession as _PTSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory, InMemoryHistory

    HAVE_PTK = True
except ImportError:  # graceful degradation — same as rich elsewhere in the project
    HAVE_PTK = False


if HAVE_PTK:

    class _RedactingFileHistory(FileHistory):
        """FileHistory that does not write secret arguments to disk."""

        def store_string(self, string: str) -> None:
            safe = redact_for_history(string)
            if safe is not None:
                super().store_string(safe)

    class _CommandCompleter(Completer):
        """TAB-complete commands and static argument hints.

        Custom class instead of NestedCompleter: that one treats a "word" as
        alphanumeric and sees a leading ``/`` as a word boundary, so
        ``/head`` never completes to ``/headscale_*``. Here the first token
        (including ``/``) is matched as a whole.
        """

        def __init__(self, commands, arg_hints):
            self.commands = sorted(commands)
            self.arg_hints = arg_hints or {}

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            if " " not in text:
                # Complete the command name (including the leading slash).
                for cmd in self.commands:
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text))
                return
            # Complete the argument from the command's static hints.
            cmd, _, rest = text.partition(" ")
            frag = rest.rsplit(" ", 1)[-1]
            for hint in self.arg_hints.get(cmd, []):
                if hint.startswith(frag):
                    yield Completion(hint, start_position=-len(frag))


class PromptSession:
    """Narrow input interface: .ask(prompt) -> str.

    If prompt_toolkit is present — TAB completion and searchable history.
    Otherwise .available == False and the caller uses its own fallback.
    """

    def __init__(
        self,
        commands: List[str],
        arg_hints: Optional[Dict[str, List[str]]] = None,
        history_path: str = DEFAULT_HISTORY_PATH,
    ):
        self.available = False
        self._session = None
        # prompt_toolkit needs an interactive TTY; in a pipe / no console (and
        # one-shot mode) fall back to the caller's input().
        if not HAVE_PTK or not (sys.stdin.isatty() and sys.stdout.isatty()):
            return

        try:
            self._session = _PTSession(
                completer=_CommandCompleter(commands, arg_hints),
                history=self._make_history(history_path),
                complete_while_typing=False,
            )
            self.available = True
        except Exception:
            # Any init problem (no console, etc.) — soft fallback.
            self._session = None
            self.available = False

    @staticmethod
    def _make_history(history_path: str):
        """History with mode 0600; if the file is unavailable — in memory."""
        try:
            if not os.path.exists(history_path):
                # Do not create the directory: the path must already exist (bind-mount).
                open(history_path, "a").close()
            os.chmod(history_path, 0o600)
            return _RedactingFileHistory(history_path)
        except OSError:
            # Path is a directory (Docker bind without pre-create) or no permissions.
            return InMemoryHistory()

    def ask(self, prompt: str) -> str:
        """Prompt for input. Raises EOFError/KeyboardInterrupt, like input()."""
        return self._session.prompt(prompt)
