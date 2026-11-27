import logging
import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wkqb.bot import Bot, Level
from wkqb.config import CalendarEntry, Config, ConfigError, GuestMode
from wkqb.cron import TIMEZONE
from wkqb.events import FloodWarning, Login, Logout, Message, SystemMessage, Unparsed, Whisper
from wkqb.outbox import Outbox

CONFIG = Config(
    jointext="Bin da!",
    master="Anna",
    admins=["Bert"],
    mods=["Cem"],
    ignored=["Spam", "cem"],
    quotes=["Eins", "Zwei <b>fett</b>", "Drei"],
    quote_prefix="Spruch:",
    quote_suffix="[%THIS%/%TOTAL%]",
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 12, 0, 30, tzinfo=TIMEZONE)

    def __call__(self) -> datetime:
        return self.now


class Reloader:
    def __init__(self) -> None:
        self.config: Config | Exception = CONFIG

    async def __call__(self) -> tuple[Config, list[str]]:
        if isinstance(self.config, Exception):
            raise self.config
        return self.config, ["eine Warnung"]


def make(config: Config = CONFIG) -> tuple[Bot, Clock, Reloader]:
    clock, reloader = Clock(), Reloader()
    return Bot("wkQB", config, Outbox(), reloader, rng=random.Random(1), clock=clock), clock, reloader


def said(bot: Bot) -> list[str]:
    lines = bot.outbox.pending()
    bot.outbox._queue.clear()
    return lines


def msg(user: str, text: str, guest: bool = False) -> Message:
    return Message(user, guest, "12:34", text)


async def test_says_hello_once() -> None:
    bot, _, _ = make()
    bot.connected()
    bot.connected()  # after a reconnect
    assert said(bot) == ["Bin da! (wkQB 6.0, https://wkqb.de)"]


@pytest.mark.parametrize(
    ("user", "guest", "level"),
    [
        ("anna", False, Level.MASTER),
        ("BERT", False, Level.ADMIN),
        ("Cem", False, Level.MOD),  # also in ignored: team members can't be ignored
        ("spam", False, Level.IGNORED),
        ("Dora", False, Level.USER),
        ("Dora", True, Level.GUEST),
    ],
)
def test_levels(user: str, guest: bool, level: Level) -> None:
    bot, _, _ = make()
    assert bot.level(user, guest) is level


async def test_ping_public_and_whispered() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Dora", "!ping"))
    await bot.handle(msg("Dora", "!PING  "))
    await bot.handle(Whisper("Dora", False, "12:34", "!ping"))
    assert said(bot) == ["Pong!", "Pong!", "/f Dora Pong!"]


async def test_ignores_itself_ignored_users_and_plain_text() -> None:
    bot, _, _ = make()
    for event in [
        msg("wkqb", "!ping"),
        msg("Spam", "!ping"),
        msg("Dora", "Hallo !ping"),
        msg("Dora", "!"),
        msg("Dora", ""),
    ]:
        await bot.handle(event)
    assert said(bot) == []


@pytest.mark.parametrize(
    ("mode", "answers"), [(GuestMode.IGNORE, False), (GuestMode.GREET, False), (GuestMode.FULL, True)]
)
async def test_guests_use_commands_only_in_full_guest_mode(mode: GuestMode, answers: bool) -> None:
    bot, _, _ = make(replace(CONFIG, guest_mode=mode))
    await bot.handle(msg("gast", "!ping", guest=True))
    # whispers don't show that it's a guest: known from the login
    await bot.handle(Login("gast2", True, "12:34"))
    await bot.handle(Whisper("gast2", False, "12:34", "!ping"))
    assert said(bot) == (["Pong!", "/f gast2 Pong!"] if answers else [])


async def test_guests_are_forgotten_on_logout() -> None:
    bot, _, _ = make()
    await bot.handle(Login("gast", True, "12:34"))
    await bot.handle(Logout("gast", False, "12:35", False))
    await bot.handle(Whisper("gast", False, "12:34", "!level"))
    assert said(bot) == ["/f gast gast, dein Level ist: User"]


async def test_say() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Dora", "!say Hallo"))
    await bot.handle(msg("Bert", "!say"))
    await bot.handle(msg("Bert", "!say <b>Hallo</b> /me"))
    assert said(bot) == ["<b>Hallo</b> /me"]


async def test_reload(caplog: pytest.LogCaptureFixture) -> None:
    bot, _, reloader = make()
    reloader.config = replace(CONFIG, mods=["Dora"])
    await bot.handle(msg("Dora", "!reload"))  # not a mod yet
    assert said(bot) == []
    await bot.handle(msg("Cem", "!reload"))
    assert said(bot) == ["Einstellungen neu geladen!"]
    assert bot.config.mods == ["Dora"]
    assert "eine Warnung" in caplog.text

    reloader.config = ConfigError("HTTP 503")
    await bot.handle(msg("Dora", "!reload"))
    assert said(bot) == ["Die Einstellungen konnten nicht geladen werden :-("]
    assert bot.config.mods == ["Dora"]  # the old settings stay


async def test_quit() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Cem", "!quit"))
    assert not bot.quit.is_set()
    await bot.handle(msg("Bert", "!quit"))
    assert bot.quit.is_set()
    assert said(bot) == ["Oh je, ich muss wohl gehen :-("]


async def test_team_lists() -> None:
    bot, _, _ = make()
    for command in ["!master", "!admins", "!mods", "!ignored"]:
        await bot.handle(msg("Dora", command))
    assert said(bot) == ["Master: Anna", "Admins: Bert", "Mods: Cem", "Ignoriert: Spam, cem"]

    bot.apply(Config())
    for command in ["!master", "!admins", "!mods", "!ignored"]:
        await bot.handle(msg("Dora", command))
    assert said(bot) == [
        "Ich habe keinen Master :-(",
        "Es gibt keine Admins :-(",
        "Es gibt keine Mods :-(",
        "Niemand wird ignoriert :-)",
    ]


async def test_level() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Bert", "!level"))
    assert said(bot) == ["Bert, dein Level ist: Admin"]


async def test_quote() -> None:
    bot, _, _ = make()
    for text in ["!quote 2", "!quote 0", "!quote 4", "!quote drei", "!quote <b>", "!quote vier"]:
        await bot.handle(msg("Dora", text))
    assert said(bot) == [
        "Spruch: Zwei <b>fett</b> [2/3]",
        "Spruch: Eins [1/3]",
        "So viele Zitate habe ich nicht :-(",
        "Spruch: Drei [3/3]",
        "Spruch: Zwei <b>fett</b> [2/3]",
        "Kein Zitat mit 'vier' gefunden :-(",
    ]
    await bot.handle(msg("Dora", "!quote"))
    [line] = said(bot)
    assert line.startswith("Spruch: ") and line.endswith("/3]")


async def test_quote_search_text_is_escaped() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Dora", "!quote <script>"))
    assert said(bot) == ["Kein Zitat mit '&lt;script&gt;' gefunden :-("]


async def test_no_quotes() -> None:
    bot, _, _ = make(Config())
    await bot.handle(msg("Dora", "!quote"))
    assert said(bot) == ["Ich habe leider keine Zitate :-("]


async def test_unknown_command() -> None:
    bot, _, _ = make()
    await bot.handle(msg("Dora", "!gibtsnicht"))
    assert said(bot) == ["Den Befehl kenne ich leider nicht :-("]


def test_scheduled_quotes() -> None:
    bot, clock, _ = make(replace(CONFIG, quote_cron="*/15 * * * *"))
    bot.tick()
    assert said(bot) == []
    clock.now = clock.now.replace(minute=15, second=0)
    bot.tick()
    bot.tick()
    [line] = said(bot)
    assert line.startswith("Spruch: ")
    # after a long pause it fires once, not for every missed time
    clock.now += timedelta(hours=2)
    bot.tick()
    bot.tick()
    assert len(said(bot)) == 1


def test_no_scheduled_quotes_without_quotes() -> None:
    bot, clock, _ = make(replace(CONFIG, quote_cron="* * * * *", quotes=[]))
    clock.now += timedelta(minutes=5)
    bot.tick()
    assert said(bot) == []


def test_calendar() -> None:
    entries = [CalendarEntry("0 13 * * *", "Mittag! R{a%OR%a}/R %TIME%"), CalendarEntry("0 0 31 2 *", "nie")]
    bot, clock, _ = make(replace(CONFIG, calendar=entries))
    clock.now = clock.now.replace(hour=13, minute=0, second=1)
    bot.tick()
    assert said(bot) == ["Mittag! a 13:00"]


def test_reload_restarts_the_schedules() -> None:
    bot, clock, _ = make(replace(CONFIG, quote_cron="0 * * * *"))
    clock.now = clock.now.replace(minute=59)
    bot.apply(replace(CONFIG, quote_cron="30 * * * *"))
    clock.now = clock.now.replace(hour=13, minute=0)
    bot.tick()
    assert said(bot) == []


async def test_other_events(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bot, _, _ = make()
    await bot.handle(FloodWarning("wkqb", False, "12:34", 2))
    await bot.handle(FloodWarning("Dora", False, "12:34", 1))
    await bot.handle(SystemMessage("Wartung"))
    await bot.handle(Unparsed("<blink>"))
    assert said(bot) == []
    assert "2. Verwarnung" in caplog.text and "1. Verwarnung" not in caplog.text
    assert "System-Meldung: Wartung" in caplog.text and "<blink>" in caplog.text
