"""The bot's settings, as exported by the web interface (wkqb-web: Wkqb\\Bot\\ConfigExporter).

The bot downloads them from WKQB_CONFIG_URL on start and on !reload (WKQB_CONFIG_FILE for local
tests). Parsing is tolerant: missing keys get defaults, broken entries (invalid cron expressions or
regexes) are left out with a warning, so a bad setting can't crash the bot.
"""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from typing import Any

import aiohttp
from croniter import croniter

from wkqb.games import Question, QuizSet

#: Timeout for downloading the settings
LOAD_TIMEOUT = 30.0


class GuestMode(IntEnum):
    IGNORE = 0
    """Guests are ignored (KI entries can still react to them)"""
    GREET = 1
    """Guests are greeted, but can't use commands"""
    FULL = 2
    """Guests are treated like registered users"""


class WhisperMode(IntEnum):
    PUBLIC = 0
    WHISPER = 1
    BOTH = 2


@dataclass(frozen=True, slots=True)
class Greeting:
    hello: str
    wb: str


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    schedule: str
    message: str


@dataclass(frozen=True, slots=True)
class CustomCommand:
    command: str
    reaction: Sequence[str]
    min_level: int
    whisper_mode: WhisperMode


@dataclass(frozen=True, slots=True)
class Pattern:
    pattern: str
    regex: re.Pattern[str] | None
    """Compiled for type "regex", None for plain text"""
    reaction: Sequence[str]
    needs_bot_name: bool
    guest_reaction: bool


@dataclass(frozen=True, slots=True)
class WordGame:
    show_solution: bool = False
    words: Sequence[str] = ()
    picture: str | None = None
    """Hangman: the picture for the used tries, "{n}" is their number (0-10), e.g. ':hangman{n}' or an <img>
    tag; None for text only"""
    hint: bool = False
    """Wordmix: give hints"""


@dataclass(frozen=True, slots=True)
class Config:
    guest_mode: GuestMode = GuestMode.IGNORE
    command_sign: str = "!"
    can_start_games: int = 0
    """Level needed to start a game"""
    ki_whisper: bool = True
    """KI entries also react to whispers"""
    jointext: str = ""
    quote_cron: str | None = None
    """None: no scheduled quotes"""
    quote_prefix: str = ""
    quote_suffix: str = ""
    quotes: Sequence[str] = ()
    calendar: Sequence[CalendarEntry] = ()
    greeting: Greeting = field(default_factory=lambda: Greeting("", ""))
    custom_greetings: Mapping[str, Greeting] = field(default_factory=dict)
    """By lower-case user name"""
    master: str = ""
    admins: Sequence[str] = ()
    mods: Sequence[str] = ()
    ignored: Sequence[str] = ()
    commands: Sequence[CustomCommand] = ()
    patterns: Sequence[Pattern] = ()
    hangman: WordGame = field(default_factory=WordGame)
    wordmix: WordGame = field(default_factory=WordGame)
    quiz_show_solution: bool = False
    quizzes: Sequence[QuizSet] = ()


class ConfigError(Exception):
    """The settings couldn't be loaded at all."""


def parse(data: Any) -> tuple[Config, list[str]]:
    """The settings and warnings about entries that were left out."""
    if not isinstance(data, dict):
        raise ConfigError("the settings are no JSON object")
    warnings: list[str] = []
    quote, users = _dict(data.get("quote")), _dict(data.get("users"))
    greeting = _dict(data.get("greeting"))
    hello, wb = _str(greeting.get("hello")), _str(greeting.get("wb"))

    quote_cron: str | None = _str(quote.get("cron")).strip() or None
    if quote_cron is not None and not croniter.is_valid(quote_cron):
        warnings.append(f"Sprüche: ungültige Zeitangabe {quote_cron!r}, keine automatischen Sprüche.")
        quote_cron = None

    calendar = []
    for entry in _dicts(_dict(data.get("calendar")).get("entries")):
        schedule, message = _str(entry.get("schedule")).strip(), _str(entry.get("message"))
        if not message.strip():
            continue
        if not croniter.is_valid(schedule):
            warnings.append(f"Kalender: ungültige Zeitangabe {schedule!r} ignoriert.")
            continue
        calendar.append(CalendarEntry(schedule, message))

    custom_greetings = {
        str(name).strip().casefold(): Greeting(_str(_dict(g).get("hello")) or hello, _str(_dict(g).get("wb")) or wb)
        for name, g in _dict(greeting.get("custom")).items()
        if str(name).strip()
    }

    commands = []
    for entry in _dicts(_dict(data.get("commands")).get("list")):
        name = _str(entry.get("command")).strip().removeprefix("!")
        reaction = _lines(entry.get("reaction"))
        if not name or not reaction:
            continue
        try:
            whisper_mode = WhisperMode(_int(entry.get("whisper_mode")))
        except ValueError:
            whisper_mode = WhisperMode.PUBLIC
        commands.append(CustomCommand(name, reaction, _int(entry.get("min_level")), whisper_mode))

    patterns = []
    for entry in _dicts(_dict(data.get("pattern")).get("list")):
        text, reaction = _str(entry.get("pattern")), _lines(entry.get("reaction"))
        if not text.strip() or not reaction:
            continue
        regex = None
        if entry.get("type") == "regex":
            try:
                regex = re.compile(text)
            except (re.error, RecursionError, OverflowError) as e:
                warnings.append(f"KI: ungültiger regulärer Ausdruck {text!r} ignoriert ({e}).")
                continue
        patterns.append(
            Pattern(text, regex, reaction, bool(entry.get("needs_bot_name")), bool(entry.get("guest_reaction")))
        )

    quiz = _dict(data.get("quiz"))
    quizzes = []
    for entry in _dicts(quiz.get("quizzes")):
        questions = [
            Question(_str(q.get("question")).strip(), _str(q.get("answer")).strip())
            for q in _dicts(entry.get("questions"))
        ]
        questions = [q for q in questions if q.question and q.answer]
        name = _str(entry.get("name")).strip()
        if name and questions:
            quizzes.append(QuizSet(name, questions))

    try:
        guest_mode = GuestMode(_int(data.get("guest_mode")))
    except ValueError:
        guest_mode = GuestMode.IGNORE

    # as wkqb-web checks it: 1-5 characters, not a chat command
    sign = _str(data.get("command_sign", "!")).strip()
    if not sign or sign.startswith("/") or len(sign) > 5 or any(ch.isspace() for ch in sign):
        warnings.append(f'Ungültiges Befehlszeichen {sign!r}, es gilt "!".')
        sign = "!"

    hangman = _word_game(data.get("hangman"))
    picture = _str(_dict(data.get("hangman")).get("picture")).strip()
    if picture:
        if "{n}" in picture:
            hangman = replace(hangman, picture=picture)
        else:
            warnings.append("Hangman: ungültige Bild-Vorlage, es wird nur Text ausgegeben.")

    config = Config(
        guest_mode=guest_mode,
        jointext=_str(data.get("jointext")),
        quote_cron=quote_cron,
        quote_prefix=_str(quote.get("prefix")),
        quote_suffix=_str(quote.get("suffix")),
        quotes=[q for q in (_str(q).strip() for q in _list(quote.get("quotes"))) if q],
        calendar=calendar,
        greeting=Greeting(hello, wb),
        custom_greetings=custom_greetings,
        master=_str(users.get("master")).strip(),
        admins=_names(users.get("admins")),
        mods=_names(users.get("mods")),
        ignored=_names(users.get("ignored")),
        commands=commands,
        patterns=patterns,
        command_sign=sign,
        can_start_games=min(4, max(0, _int(data.get("can_start_games")))),
        ki_whisper=bool(_dict(data.get("pattern")).get("whisper", True)),
        hangman=hangman,
        wordmix=replace(_word_game(data.get("wordmix")), hint=bool(_dict(data.get("wordmix")).get("hint"))),
        quiz_show_solution=bool(quiz.get("show_solution")),
        quizzes=quizzes,
    )
    return config, warnings


async def load(session: aiohttp.ClientSession, env: Mapping[str, str]) -> tuple[Config, list[str]]:
    """Downloads (WKQB_CONFIG_URL) or reads (WKQB_CONFIG_FILE) and parses the settings."""
    try:
        if path := env.get("WKQB_CONFIG_FILE"):
            text = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
        elif url := env.get("WKQB_CONFIG_URL"):
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=LOAD_TIMEOUT)) as response:
                if response.status != 200:
                    raise ConfigError(f"HTTP {response.status}")
                text = await response.text(encoding="utf-8")
        else:
            raise ConfigError("neither WKQB_CONFIG_URL nor WKQB_CONFIG_FILE is set")
        return parse(json.loads(text))
    except ConfigError:
        raise
    except (OSError, aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise ConfigError(f"{type(e).__name__}: {e}") from e


def _word_game(value: Any) -> WordGame:
    data = _dict(value)
    return WordGame(
        bool(data.get("show_solution")), [w for w in (_str(w).strip() for w in _list(data.get("words"))) if w]
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in _list(value) if isinstance(v, dict)]


def _str(value: Any) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def _lines(value: Any) -> list[str]:
    """A reaction: one line or a list of lines (the old config.json had both)."""
    lines = _list(value) if isinstance(value, list) else [value]
    return [line for line in (_str(v) for v in lines) if line.strip()]


def _names(value: Any) -> list[str]:
    return [n for n in (_str(v).strip() for v in _list(value)) if n]
