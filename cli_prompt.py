# -*- coding: utf-8 -*-
"""
Интерактивный ввод для SSH CLI-дашборда: автодополнение команд + история с поиском.

Реализован на prompt_toolkit (TAB-дополнение, Ctrl+R reverse-search, персистентная
история). Если prompt_toolkit недоступен (или ввод не из TTY) — модуль остаётся
импортируемым (PromptSession.available == False), а cli_dashboard откатывается на
rich.Prompt / input().

История пишется в файл (по умолчанию /app/.cli_history, bind-mount в compose.yaml,
переживает docker compose --force-recreate). Секреты не попадают в файл: команды из
SECRET_ARG_COMMANDS сохраняются без аргументов (см. redact_for_history).
"""

import os
import sys
from typing import Dict, List, Optional

# Команды, у которых позиционный аргумент — секрет (pre-auth ключ и т.п.).
# В историю пишется только имя команды, без аргументов.
SECRET_ARG_COMMANDS = {"/headscale_revoke"}

DEFAULT_HISTORY_PATH = os.getenv("CLI_HISTORY_PATH", "/app/.cli_history")


def redact_for_history(line: str) -> Optional[str]:
    """Что записать в историю для строки ввода (или None — не записывать).

    Пустые строки пропускаются. Для команд из SECRET_ARG_COMMANDS с аргументами
    возвращается только имя команды (секрет на диск не попадает).
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
except ImportError:  # graceful degradation — как rich в остальном проекте
    HAVE_PTK = False


if HAVE_PTK:

    class _RedactingFileHistory(FileHistory):
        """FileHistory, который не пишет секретные аргументы на диск."""

        def store_string(self, string: str) -> None:
            safe = redact_for_history(string)
            if safe is not None:
                super().store_string(safe)

    class _CommandCompleter(Completer):
        """TAB-дополнение команд и статических подсказок аргументов.

        Свой класс вместо NestedCompleter: тот определяет «слово» по
        алфавитно-цифровому паттерну и считает ведущий ``/`` границей слова,
        поэтому ``/head`` у него не дополняется до ``/headscale_*``. Здесь
        первый токен (с ``/``) матчится целиком.
        """

        def __init__(self, commands, arg_hints):
            self.commands = sorted(commands)
            self.arg_hints = arg_hints or {}

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            if " " not in text:
                # Дополняем имя команды (включая ведущий слэш).
                for cmd in self.commands:
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text))
                return
            # Дополняем аргумент по статическим подсказкам команды.
            cmd, _, rest = text.partition(" ")
            frag = rest.rsplit(" ", 1)[-1]
            for hint in self.arg_hints.get(cmd, []):
                if hint.startswith(frag):
                    yield Completion(hint, start_position=-len(frag))


class PromptSession:
    """Узкий интерфейс ввода: .ask(prompt) -> str.

    Если prompt_toolkit есть — TAB-дополнение команд и история с поиском.
    Иначе .available == False, и вызывающий код использует свой фолбэк.
    """

    def __init__(
        self,
        commands: List[str],
        arg_hints: Optional[Dict[str, List[str]]] = None,
        history_path: str = DEFAULT_HISTORY_PATH,
    ):
        self.available = False
        self._session = None
        # prompt_toolkit нужен интерактивный TTY; в пайпе/без консоли (и в
        # one-shot режиме) откатываемся на input() у вызывающего кода.
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
            # Любая проблема инициализации (нет консоли и т.п.) — мягкий фолбэк.
            self._session = None
            self.available = False

    @staticmethod
    def _make_history(history_path: str):
        """История с правами 0600; при недоступности файла — в памяти."""
        try:
            if not os.path.exists(history_path):
                # Каталог не создаём: путь должен существовать (bind-mount).
                open(history_path, "a").close()
            os.chmod(history_path, 0o600)
            return _RedactingFileHistory(history_path)
        except OSError:
            # Файл — директория (Docker bind без pre-create) или нет прав.
            return InMemoryHistory()

    def ask(self, prompt: str) -> str:
        """Запросить ввод. Бросает EOFError/KeyboardInterrupt, как input()."""
        return self._session.prompt(prompt)
