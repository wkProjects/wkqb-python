import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from fake_webkicks import FakeWebkicks
from wkqb.config import (
    CalendarEntry,
    ConfigError,
    CustomCommand,
    Greeting,
    GuestMode,
    WhisperMode,
    WordGame,
    load,
    parse,
)
from wkqb.games import Question, QuizSet

#: As wkqb-web's ConfigExporter writes it
EXPORTED: dict[str, Any] = {
    "guest_mode": 1,
    "jointext": "Bin da!",
    "quote": {"cron": "*/15 8-22 * * *", "prefix": "", "suffix": "[%THIS%/%TOTAL%]", "quotes": ["Eins", "Zwei"]},
    "calendar": {"entries": [{"message": "Guten Morgen!", "schedule": "0 8 * * *"}]},
    "greeting": {
        "hello": "Hallo %USER%!",
        "wb": "wb %USER%!",
        "custom": {"Anna": {"hello": "Hi Anna!", "wb": "wb Anna!"}},
    },
    "users": {"master": "Anna", "admins": ["Bert"], "mods": ["Cem", "Dora"], "ignored": ["Spam"]},
    "commands": {
        "list": [
            {"command": "greet", "reaction": ["Hallo %PARAM%!", "Zweite Zeile"], "whisper_mode": 2, "min_level": 1}
        ]
    },
    "pattern": {
        "list": [
            {
                "pattern": "hallo",
                "type": "plain",
                "reaction": ["Hi %USER%"],
                "needs_bot_name": True,
                "guest_reaction": False,
            },
            {
                "pattern": r"\d{3}",
                "type": "regex",
                "reaction": ["Zahl $0"],
                "needs_bot_name": False,
                "guest_reaction": True,
            },
        ]
    },
    "hangman": {"show_solution": True, "words": ["Katze", "Hund"]},
    "wordmix": {"show_solution": False, "words": ["Maus"]},
    "quiz": {
        "show_solution": True,
        "quizzes": [{"name": "Tiere", "questions": [{"question": "Wie macht die Kuh?", "answer": "Muh"}]}],
    },
}


def test_exported_settings() -> None:
    config, warnings = parse(EXPORTED)
    assert warnings == []
    assert config.guest_mode is GuestMode.GREET
    assert config.jointext == "Bin da!"
    assert (config.quote_cron, config.quote_suffix, config.quotes) == (
        "*/15 8-22 * * *",
        "[%THIS%/%TOTAL%]",
        ["Eins", "Zwei"],
    )
    assert config.calendar == [CalendarEntry("0 8 * * *", "Guten Morgen!")]
    assert config.greeting == Greeting("Hallo %USER%!", "wb %USER%!")
    assert config.custom_greetings == {"anna": Greeting("Hi Anna!", "wb Anna!")}
    assert (config.master, config.admins, config.mods, config.ignored) == ("Anna", ["Bert"], ["Cem", "Dora"], ["Spam"])
    assert config.commands == [CustomCommand("greet", ["Hallo %PARAM%!", "Zweite Zeile"], 1, WhisperMode.BOTH)]
    plain, regex = config.patterns
    assert (plain.pattern, plain.regex, plain.needs_bot_name, plain.guest_reaction) == ("hallo", None, True, False)
    assert regex.regex is not None and regex.regex.pattern == r"\d{3}"
    assert config.hangman == WordGame(True, ["Katze", "Hund"])
    assert config.wordmix == WordGame(False, ["Maus"])
    assert config.quiz_show_solution
    assert config.quizzes == [QuizSet("Tiere", [Question("Wie macht die Kuh?", "Muh")])]


def test_old_config_format() -> None:
    # the old config.json of the repository: single reactions as strings, an "interval" that's unused
    config, warnings = parse(
        {
            "guest_mode": 0,
            "quote": {"interval": 15, "cron": "*/5 * * * *", "prefix": "", "suffix": "", "quotes": ["Q"]},
            "commands": {"list": [{"command": "test", "reaction": "Erfolgreich!", "min_level": 1, "whisper_mode": 0}]},
        }
    )
    assert warnings == []
    assert config.commands == [CustomCommand("test", ["Erfolgreich!"], 1, WhisperMode.PUBLIC)]


def test_empty_settings_get_defaults() -> None:
    config, warnings = parse({})
    assert warnings == []
    assert config.guest_mode is GuestMode.IGNORE
    assert config.quote_cron is None and config.quotes == []
    assert config.master == "" and config.commands == [] and config.quizzes == []


def test_broken_entries_are_left_out() -> None:
    config, warnings = parse(
        {
            "guest_mode": 7,
            "quote": {"cron": "61 * * * *", "quotes": ["x"]},
            "calendar": {"entries": [{"schedule": "nie", "message": "x"}, {"schedule": "* * * * *", "message": " "}]},
            "commands": {
                "list": [{"command": "", "reaction": ["x"]}, {"command": "!x", "reaction": [], "min_level": "a"}]
            },
            "pattern": {"list": [{"pattern": "(", "type": "regex", "reaction": ["x"]}, "kaputt"]},
            "users": {"admins": "Bert", "mods": [None, " ", "Cem"]},
            "quiz": {"quizzes": [{"name": "Leer", "questions": [{"question": "?", "answer": " "}]}]},
        }
    )
    assert config.guest_mode is GuestMode.IGNORE
    assert config.quote_cron is None
    assert config.calendar == [] and config.commands == [] and config.patterns == [] and config.quizzes == []
    assert config.admins == [] and config.mods == ["Cem"]
    assert len(warnings) == 3
    assert "Sprüche" in warnings[0] and "Kalender" in warnings[1] and "KI" in warnings[2]


@pytest.mark.parametrize("data", [[], "x", None, 3])
def test_no_object(data: Any) -> None:
    with pytest.raises(ConfigError):
        parse(data)


async def test_load_file(tmp_path: Path) -> None:
    file = tmp_path / "config.json"
    file.write_text(json.dumps(EXPORTED), encoding="utf-8")
    async with aiohttp.ClientSession() as session:
        config, _ = await load(session, {"WKQB_CONFIG_FILE": str(file)})
    assert config.jointext == "Bin da!"


async def test_load_url(fake: FakeWebkicks, server: TestServer, session: aiohttp.ClientSession) -> None:
    fake.config = EXPORTED
    url = str(server.make_url("/api/config.php?b_id=abcdef1234"))
    config, _ = await load(session, {"WKQB_CONFIG_URL": url})
    assert config.master == "Anna"

    fake.fail["config"] = 404
    with pytest.raises(ConfigError, match="HTTP 404"):
        await load(session, {"WKQB_CONFIG_URL": url})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "neither"),
        ({"WKQB_CONFIG_FILE": "/does/not/exist"}, "FileNotFoundError"),
        ({"WKQB_CONFIG_URL": "http://127.0.0.1:1/x"}, "ClientConnectorError"),
    ],
)
async def test_load_errors(env: dict[str, str], message: str, session: aiohttp.ClientSession) -> None:
    with pytest.raises(ConfigError, match=message):
        await load(session, env)


async def test_load_invalid_json(tmp_path: Path, session: aiohttp.ClientSession) -> None:
    file = tmp_path / "config.json"
    file.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSONDecodeError"):
        await load(session, {"WKQB_CONFIG_FILE": str(file)})
