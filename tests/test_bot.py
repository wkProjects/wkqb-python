import logging
import random
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wkqb.bot import Bot, Level
from wkqb.config import CalendarEntry, Config, ConfigError, GuestMode, parse
from wkqb.cron import TIMEZONE
from wkqb.events import Back, FloodWarning, Login, Logout, Message, RoomEnter, SystemMessage, Unparsed, Whisper
from wkqb.games import POTATO_SIGNUP, POTATO_THROW_TIME, HotPotato, Timebomb
from wkqb.outbox import Outbox
from wkqb.points import PointsDisabled, PointsError, Standing
from wkqb.webkicks import MAX_MESSAGE_BYTES

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
    # what a normal user typed as text (Webkicks escaped it, the parser decoded it) stays text
    await bot.handle(msg("Bert", "!say <img src=x onerror=alert(1)> & /me"))
    await bot.handle(msg("Bert", "!say /kick Spam"))
    assert said(bot) == ["&lt;img src=x onerror=alert(1)&gt; &amp; /me", "/kick Spam"]


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


# -- features


def configured(**data: object) -> Config:
    users = {"master": "Anna", "admins": ["Bert"], "mods": ["Cem"], "ignored": ["Spam"]}
    config, warnings = parse({"users": users, **data})
    assert warnings == []
    return config


GREETINGS = {
    "greeting": {"hello": "Hallo %USER%!", "wb": "wb %USER%!", "custom": {"Dora": {"hello": "Hi Dora!", "wb": ""}}},
}


async def test_greetings_wait_a_little() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(Login("Emil", False, "12:34"))
    bot.tick()
    assert said(bot) == []
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == ["Hallo Emil!"]


async def test_welcome_back_within_6_hours() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(Login("Emil", False, "12:34"))
    clock.now += timedelta(hours=5, minutes=59)
    await bot.handle(Login("emil", False, "18:33"))  # counted from the last greeting, as in the Perl bot
    clock.now += timedelta(hours=6, seconds=1)
    await bot.handle(Login("Emil", False, "00:34"))
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == ["Hallo Emil!", "wb emil!", "Hallo Emil!"]


async def test_greetings_for_room_changes_and_returns_from_away() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(RoomEnter("Emil", False, "12:34", "Smalltalk"))
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == ["Hallo Emil!"]
    await bot.handle(Back("Emil", False, "12:40"))  # wb at once
    bot.tick()
    assert said(bot) == ["wb Emil!"]
    await bot.handle(Back("Dora", False, "12:41"))  # also without an earlier greeting
    await bot.handle(Back("wkQB", False, "12:41"))
    await bot.handle(Back("Spam", False, "12:41"))
    bot.tick()
    assert said(bot) == ["wb Dora!"]


async def test_custom_greetings() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(Login("DORA", False, "12:34"))
    await bot.handle(Logout("Dora", False, "12:35", False))
    await bot.handle(Login("Dora", False, "12:36"))  # "wb" is empty: the exporter fills in the default
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == ["Hi Dora!", "wb Dora!"]


@pytest.mark.parametrize(("mode", "greeted"), [(0, False), (1, True), (2, True)])
async def test_guest_greetings(mode: int, greeted: bool) -> None:
    bot, clock, _ = make(configured(guest_mode=mode, **GREETINGS))
    await bot.handle(Login("gast", True, "12:34"))
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == (["Hallo gast!"] if greeted else [])


async def test_no_greetings_for_itself_and_ignored_users() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(Login("wkQB", False, "12:34"))
    await bot.handle(Login("Spam", False, "12:34"))
    await bot.handle(RoomEnter("Spam", False, "12:34", "Smalltalk"))
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == []


COMMANDS = {
    "commands": {
        "list": [
            {
                "command": "Hallo",
                "reaction": ["Hallo %PARAM%, sagt %USER% um %TIME%"],
                "whisper_mode": 0,
                "min_level": 1,
            },
            {"command": "geheim", "reaction": ["/kick %PARAM%"], "whisper_mode": 1, "min_level": 3},
            {"command": "beides", "reaction": ["eins", "zwei"], "whisper_mode": 2, "min_level": 0},
            {"command": "ping", "reaction": ["nie"], "whisper_mode": 2, "min_level": 0},
        ]
    }
}


async def test_custom_commands() -> None:
    bot, _, _ = make(configured(**COMMANDS))
    await bot.handle(msg("Emil", "!hallo <b>Welt</b>"))
    await bot.handle(Whisper("Emil", False, "12:34", "!hallo Welt"))  # not when whispered
    await bot.handle(msg("Emil", "!beides"))
    await bot.handle(Whisper("Emil", False, "12:34", "!beides"))
    await bot.handle(msg("Emil", "!ping"))  # system commands come first
    assert said(bot) == ["Hallo &lt;b&gt;Welt&lt;/b&gt;, sagt Emil um 12:34", "eins", "zwei", "eins", "zwei", "Pong!"]


async def test_custom_command_levels_and_whisper_only() -> None:
    bot, _, _ = make(configured(**COMMANDS))
    await bot.handle(msg("Bert", "!geheim Spam"))  # only whispered
    await bot.handle(Whisper("Emil", False, "12:34", "!geheim Spam"))  # admins only
    assert said(bot) == []
    await bot.handle(Whisper("Bert", False, "12:34", "!geheim Spam"))
    assert said(bot) == ["/kick Spam"]  # in public: a chat command, not a whisper


async def test_custom_commands_for_guests() -> None:
    bot, _, _ = make(configured(guest_mode=2, **COMMANDS))
    await bot.handle(msg("gast", "!beides", guest=True))
    await bot.handle(msg("gast", "!hallo", guest=True))  # registered users only
    assert said(bot) == ["eins", "zwei"]


KI = {
    "pattern": {
        "list": [
            {
                "pattern": "hallo",
                "type": "plain",
                "reaction": ["Hi %USER%!"],
                "needs_bot_name": True,
                "guest_reaction": False,
            },
            {
                "pattern": "Bot",
                "type": "plain",
                "reaction": ["Wer, ich?"],
                "needs_bot_name": False,
                "guest_reaction": True,
            },
            {
                "pattern": "?",
                "type": "plain",
                "reaction": ["Gute Frage"],
                "needs_bot_name": False,
                "guest_reaction": False,
            },
            {
                "pattern": r"(https?://\S+)",
                "type": "regex",
                "reaction": ["%USER% hat $1 gepostet: %SATZ%"],
                "needs_bot_name": False,
                "guest_reaction": False,
            },
        ]
    }
}


@pytest.mark.parametrize(
    ("text", "answers"),
    [
        ("hallo", []),
        ("HALLO wkqb", ["Hi Emil!"]),
        ("wkQB, hallo!", ["Hi Emil!"]),
        ("hallowkQB", []),
        ("Hallo wkQBs", []),
        ("Der Bot ist da", ["Wer, ich?"]),
        ("Der Botschafter", []),
        ("was?", ["Gute Frage"]),
        ("siehe https://x.de/<b>", ["Emil hat https://x.de/&lt;b&gt; gepostet: siehe https://x.de/&lt;b&gt;"]),
        ("!hallo wkQB", ["Den Befehl kenne ich leider nicht :-("]),  # commands are no KI
    ],
)
async def test_ki(text: str, answers: list[str]) -> None:
    bot, _, _ = make(configured(**KI))
    await bot.handle(msg("Emil", text))
    assert said(bot) == answers


async def test_ki_for_guests_ignores_the_guest_mode() -> None:
    bot, _, _ = make(configured(guest_mode=0, **KI))
    await bot.handle(msg("gast", "Bot? hallo wkQB", guest=True))
    assert said(bot) == ["Wer, ich?"]


async def test_ki_answers_whispers_by_whisper() -> None:
    bot, _, _ = make(configured(**KI))
    await bot.handle(Whisper("Emil", False, "12:34", "hallo wkQB"))
    assert said(bot) == ["/f Emil Hi Emil!"]


async def test_ki_can_ignore_whispers() -> None:
    bot, _, _ = make(configured(pattern={**KI["pattern"], "whisper": False}))
    await bot.handle(Whisper("Emil", False, "12:34", "hallo wkQB"))
    await bot.handle(msg("Emil", "hallo wkQB"))
    assert said(bot) == ["Hi Emil!"]


WORDS = {"hangman": {"show_solution": True, "words": ["Katze"]}, "wordmix": {"show_solution": True, "words": ["ab"]}}


async def test_hangman() -> None:
    bot, _, _ = make(configured(**WORDS))
    await bot.handle(msg("Emil", "!hangman"))
    assert said(bot)[0] == "Hangman gestartet! Gesucht: _ _ _ _ _ (5 Zeichen)"
    await bot.handle(msg("Emil", "!hangman <b>"))
    assert said(bot) == ["Emil, &lt;b&gt; war leider nicht die richtige Lösung.", "Verbleibende Versuche: 7"]
    await bot.handle(Whisper("Dora", False, "12:34", "!hangman katze"))  # games answer in public
    assert said(bot) == ["Volltreffer Dora, Katze ist richtig!"]
    assert "hangman" not in bot.games
    await bot.handle(msg("Emil", "!hangman"))
    assert said(bot)[0].startswith("Hangman gestartet!")


async def test_game_timeouts() -> None:
    bot, clock, _ = make(configured(**WORDS))
    await bot.handle(msg("Emil", "!hangman"))
    await bot.handle(msg("Emil", "!wordmix"))
    said(bot)
    clock.now += timedelta(seconds=60)
    bot.tick()
    assert said(bot) == [
        "Das Hangman-Spiel wurde beendet, weil es zu lange ignoriert wurde.",
        "Die richtige Lösung war: Katze",
    ]
    assert list(bot.games) == ["wordmix"]
    clock.now += timedelta(seconds=240)
    bot.tick()
    assert said(bot)[0] == "Die Zeit für Wordmix ist abgelaufen!"
    assert bot.games == {}


async def test_no_words() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Emil", "!hangman"))
    await bot.handle(msg("Emil", "!wordmix"))
    assert said(bot) == ["Ich habe leider keine Wörter :-(", "Ich habe leider keine Wörter :-("]


async def test_wordmix() -> None:
    bot, _, _ = make(configured(**WORDS))
    await bot.handle(msg("Emil", "!wordmix"))
    assert said(bot)[0] == "Wordmix gestartet! Gesucht: <b>ba</b>"
    await bot.handle(msg("Emil", "!wordmix AB"))
    assert said(bot) == ["Volltreffer Emil, ab ist richtig!"]
    assert bot.games == {}


QUIZ = {
    "quiz": {
        "show_solution": True,
        "quizzes": [{"name": "Tiere", "questions": [{"question": "Wie macht die Kuh?", "answer": "Muh"}]}],
    }
}


async def test_quiz() -> None:
    bot, _, _ = make(configured(**QUIZ))
    for text in ["!quiz", "!quiz 2", "!quiz <b>"]:
        await bot.handle(msg("Emil", text))
    assert said(bot) == [
        "Ich habe folgende Quizze anzubieten: 1: Tiere (1 Fragen)",
        "Sende !quiz ID, um das gewünschte Quiz zu starten!",
        "2 ist keine gültige ID.",
        "&lt;b&gt; ist keine gültige ID.",
    ]
    await bot.handle(msg("Emil", "!quiz 1"))
    assert said(bot) == ["Starte Quiz: Tiere", "Wie macht die Kuh?"]
    await bot.handle(msg("Emil", "!quiz mäh"))
    await bot.handle(msg("Dora", "!quiz muh"))
    answers = said(bot)
    assert answers[0] == "mäh ist leider falsch!" and answers[1].endswith("Dora, Muh ist korrekt!")
    assert answers[2] == "Das waren alle Fragen, das Quiz ist daher beendet! Dora hat mit 1 richtigen Antwort gewonnen!"
    assert bot.games == {}


async def test_no_quiz() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Emil", "!quiz 1"))
    assert said(bot) == ["Ich kenne leider kein Quiz :-("]


async def test_timebomb() -> None:
    bot, clock, _ = make(configured())
    await bot.handle(msg("Emil", "!schneide rot"))
    await bot.handle(msg("Emil", "!zeitbombe Dora"))  # only bot admins bomb others
    await bot.handle(msg("Bert", "!zeitbombe spam"))
    assert said(bot) == [
        "Es läuft doch gar kein Spiel!",
        "Nur Bot-Admins können anderen eine Bombe zuschieben.",
        "Mit ignorierten Usern spiele ich nicht!",
    ]

    await bot.handle(msg("Bert", "!zeitbombe Dora bitte"))
    assert said(bot)[0].startswith("Bert schiebt Dora eine Bombe zu!")
    await bot.handle(msg("Dora", "!zeitbombe"))
    await bot.handle(msg("Emil", "!schneide rot"))
    await bot.handle(msg("Emil", "!cheat"))
    assert said(bot) == ["Es läuft bereits ein Spiel!", "Du bist doch gar nicht dran!", "Du hast doch gar keine Bombe!"]

    game = bot.games["zeitbombe"]
    assert isinstance(game, Timebomb)
    await bot.handle(msg("Dora", f"!schneide {game.wire}"))
    assert said(bot) == ["Dora hat die Bombe entschärft. Glückwunsch!"]
    assert bot.games == {}

    await bot.handle(msg("Bert", "!zeitbombe <i>x</i>"))
    assert said(bot)[0].startswith("Bert schiebt &lt;i&gt;x&lt;/i&gt; eine Bombe zu!")
    clock.now += timedelta(seconds=30)
    bot.tick()
    [line] = said(bot)
    assert line.startswith("&lt;i&gt;x&lt;/i&gt; hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-( ")
    assert line.endswith("</b> gewesen.")


async def test_cheating_loses() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Bert", "!zeitbombe Dora"))
    said(bot)
    await bot.handle(Whisper("Dora", False, "12:34", "!cheat"))
    assert said(bot) == ["Dora versuchte zu mogeln!"]  # in public
    assert bot.games == {}
    await bot.handle(msg("Dora", "!cheat"))
    assert said(bot) == ["Es läuft doch gar kein Spiel!"]


async def test_timebomb_cooldown_for_users() -> None:
    bot, clock, _ = make(configured())
    await bot.handle(msg("Emil", "!zeitbombe"))
    game = bot.games["zeitbombe"]
    assert isinstance(game, Timebomb)
    await bot.handle(msg("Emil", f"!schneide {game.wire}"))
    said(bot)
    clock.now += timedelta(seconds=20)
    await bot.handle(msg("Dora", "!zeitbombe"))
    assert said(bot) == ["Die nächste Bombe gibt es erst in 40 Sekunden."]
    await bot.handle(msg("Cem", "!zeitbombe"))  # mods aren't held back
    assert said(bot)[0].startswith("Cem schiebt Cem eine Bombe zu!")


async def test_quiz_cooldown_and_plain_answers() -> None:
    bot, clock, _ = make(configured(**QUIZ))
    await bot.handle(msg("Emil", "!quiz 1"))
    said(bot)
    await bot.handle(msg("Dora", "Ich glaube Mäh"))  # wrong answers as messages: no reaction
    await bot.handle(Whisper("Dora", False, "12:34", "muh"))  # only public messages count
    assert said(bot) == []
    await bot.handle(msg("Dora", " MUH "))
    assert said(bot)[0].endswith("Dora, Muh ist korrekt!")
    assert bot.games == {}
    clock.now += timedelta(minutes=2)
    await bot.handle(msg("Emil", "!quiz 1"))
    assert said(bot) == ["Das nächste Quiz gibt es erst in 60 Sekunden."]
    await bot.handle(msg("Cem", "!quiz 1"))
    assert said(bot)[0] == "Starte Quiz: Tiere"


async def test_zitat_and_suche() -> None:
    bot, _, _ = make()
    for text in ["!zitat 1", "!suche drei", "!suche 2", "!suche"]:
        await bot.handle(msg("Dora", text))
    assert said(bot) == [
        "Spruch: Eins [1/3]",
        "Spruch: Drei [3/3]",
        "Kein Zitat mit '2' gefunden :-(",
        "Wonach soll ich suchen? !suche TEXT",
    ]


async def test_bid() -> None:
    bot, _, _ = make(configured(bot_id="abcdef1234"))
    await bot.handle(Whisper("Emil", False, "12:34", "!bid"))  # users get nothing
    await bot.handle(msg("Bert", "!bid"))
    await bot.handle(Whisper("Bert", False, "12:34", "!bid"))
    assert said(bot) == ["Das musst du mir flüstern!", "/f Bert Meine ID ist abcdef1234"]


class IgnoreList:
    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    async def __call__(self, action: str, name: str) -> list[str]:
        self.calls.append((action, name))
        if self.fail:
            raise ConfigError("HTTP 500")
        others = [n for n in self.names if n.casefold() != name.casefold()]
        self.names = [*others, name] if action == "add" else others
        return self.names


async def test_ignore_and_unignore(caplog: pytest.LogCaptureFixture) -> None:
    api = IgnoreList(["Spam"])
    bot = Bot("wkQB", configured(), Outbox(), Reloader(), change_ignored=api, clock=Clock())
    await bot.handle(msg("Emil", "!ignore Dora"))  # mods and up only
    for text in ["!ignore", "!ignore wkqb", "!ignore Bert", "!ignore SPAM", "!unignore Dora", "!ignore Dora nervt"]:
        await bot.handle(msg("Cem", text))
    assert said(bot) == [
        "Wen denn? !ignore NAME",
        "Mich selbst ignorieren? Nein danke.",
        "Bert gehört zum Bot-Team und kann nicht ignoriert werden.",
        "SPAM wird bereits ignoriert.",
        "Dora wird gar nicht ignoriert.",
        "Dora wird ab sofort ignoriert.",
    ]
    assert api.calls == [("add", "Dora")]
    await bot.handle(msg("Dora", "!ping"))
    assert said(bot) == []

    await bot.handle(msg("Cem", "!unignore dora"))
    assert said(bot) == ["dora wird nicht mehr ignoriert."]
    assert bot.config.ignored == ["Spam"]

    api.fail = True
    await bot.handle(msg("Cem", "!ignore Emil"))
    assert said(bot) == ["Das hat leider nicht geklappt :-("]
    assert "HTTP 500" in caplog.text and bot.config.ignored == ["Spam"]


async def test_ignore_without_api() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Cem", "!ignore Dora"))
    assert said(bot) == ["Die Ignorier-Liste kann ich leider nicht ändern :-("]


async def test_command_sign() -> None:
    bot, _, _ = make(configured(command_sign="#", **KI))
    await bot.handle(msg("Emil", "#ping"))
    await bot.handle(msg("Emil", "!ping"))  # no command now
    await bot.handle(msg("Emil", "!ping Bot"))  # but KI
    assert said(bot) == ["Pong!", "Wer, ich?"]


async def test_only_some_may_start_games() -> None:
    bot, _, _ = make(configured(can_start_games=2, **WORDS, **QUIZ))
    for text in ["!hangman", "!wordmix", "!quiz 1", "!zeitbombe", "!kartoffel"]:
        await bot.handle(msg("Emil", text))
    assert said(bot) == ["Emil, du darfst leider keine Spiele starten."] * 5
    await bot.handle(Whisper("Emil", False, "12:34", "!hangman"))
    assert said(bot) == ["/f Emil Emil, du darfst leider keine Spiele starten."]
    await bot.handle(msg("Emil", "!quiz"))  # the list is for everyone
    assert said(bot)[0].startswith("Ich habe folgende Quizze")

    await bot.handle(msg("Bert", "!hangman"))
    said(bot)
    await bot.handle(msg("Emil", "!hangman k"))  # playing is for everyone
    assert said(bot) == ["Emil, der Buchstabe K kommt vor: K _ _ _ _"]


async def test_hot_potato() -> None:
    bot, clock, _ = make(configured())
    await bot.handle(msg("Emil", "!wurf"))
    assert said(bot) == ["Es läuft doch gar kein Spiel!"]
    await bot.handle(msg("Emil", "!kartoffel"))
    assert said(bot)[0].startswith("Emil startet Heiße Kartoffel!")
    await bot.handle(msg("Dora", "!kartoffel"))
    await bot.handle(msg("Bert", "!kartoffel"))
    assert said(bot) == ["Dora spielt mit! (2 Spieler)", "Bert spielt mit! (3 Spieler)"]
    clock.now += timedelta(seconds=60)
    bot.tick()
    lines = said(bot)
    game = bot.games["kartoffel"]
    assert isinstance(game, HotPotato) and game.holder is not None
    assert lines[1] == f"Die heiße Kartoffel fliegt zu <b>{game.holder}</b>! Schnell, <b>!wurf</b>!"
    game.cold_after = 1
    await bot.handle(msg(game.holder, "!wurf"))
    assert said(bot) == ["Die Kartoffel ist kalt! Gewonnen haben: Emil, Dora, Bert"]
    assert bot.games == {}


async def test_hangman_pictures_and_wordmix_hints_from_the_settings() -> None:
    settings = {
        "hangman": {"words": ["Katze"], "picture": '<img src="https://x/hm{n}.gif">'},
        "wordmix": {"words": ["Katze"], "hint": True},
    }
    bot, clock, _ = make(configured(**settings))
    await bot.handle(msg("Emil", "!hangman"))
    await bot.handle(msg("Emil", "!wordmix"))
    lines = said(bot)
    assert lines[0].endswith('<img src="https://x/hm0.gif">')
    clock.now += timedelta(seconds=150)
    bot.tick()
    assert "Tipp: <b>k _ _ _ e</b>" in said(bot)


async def test_ki_only_the_first_matching_entry_reacts() -> None:
    bot, _, _ = make(configured(**KI))
    await bot.handle(msg("Emil", "hallo wkQB, bist du ein Bot?"))  # matches three entries
    assert said(bot) == ["Hi Emil!"]


async def test_solution() -> None:
    bot, _, _ = make(configured(**WORDS, **QUIZ))
    await bot.handle(Whisper("Bert", False, "12:34", "!solution"))
    assert said(bot) == ["/f Bert Es läuft doch gar kein Spiel!"]
    for text in ["!hangman", "!zeitbombe", "!quiz 1"]:
        await bot.handle(msg("Bert", text))
    said(bot)
    bomb = bot.games["zeitbombe"]
    assert isinstance(bomb, Timebomb)
    await bot.handle(Whisper("Emil", False, "12:34", "!solution"))  # bot admins only
    await bot.handle(msg("Bert", "!solution"))
    await bot.handle(Whisper("Bert", False, "12:34", "!solution"))
    assert said(bot) == [
        "Das musst du mir flüstern!",
        f"/f Bert Die Lösung ist: hangman: Katze, zeitbombe: {bomb.wire}, quiz: Muh",
    ]


async def test_slow_ki_regexes_are_cut_off(caplog: pytest.LogCaptureFixture) -> None:
    def ki(pattern: str, reaction: str, regex: bool = False) -> dict[str, object]:
        return {"pattern": pattern, "type": "regex" if regex else "plain", "reaction": [reaction]}

    patterns = {
        "pattern": {
            "list": [
                ki(r"(a|aa)+$", "nie", regex=True),  # takes ages on many a's followed by something else
                ki(r"^(\w+\s?)*$", "nur Wörter", regex=True),  # was 15 s with re on a normal sentence
                ki("Ende", "Ende erkannt"),
            ]
        }
    }
    bot, _, _ = make(configured(**patterns))
    started = time.monotonic()
    await bot.handle(msg("Emil", "a" * 40 + "!"))
    await bot.handle(msg("Emil", "Ein ganz normaler Satz mit vielen Wörtern und am Ende!"))
    assert time.monotonic() - started < 1
    assert said(bot) == ["Ende erkannt"]  # the next entry still gets its turn
    assert "'(a|aa)+$' war zu langsam" in caplog.text


# -- points

POINTS_URL = "http://x/api/points.php"


class FakePoints:
    """An in-memory Points: records the calls, and fails when told to."""

    def __init__(self) -> None:
        self.adds: list[dict[str, int]] = []
        self.calls: list[tuple[str, object]] = []
        self.totals: dict[str, int] = {}
        self.error: PointsError | None = None
        self.standings: dict[str, Standing] = {}
        self.entries: list[Standing] = []
        self.deleted = 3

    def _check(self, call: str, arg: object) -> None:
        self.calls.append((call, arg))
        if self.error is not None:
            raise self.error

    async def add(self, url: str, changes: Mapping[str, int]) -> dict[str, int]:
        assert url == POINTS_URL
        self._check("add", dict(changes))
        self.adds.append(dict(changes))
        for name, change in changes.items():
            self.totals[name] = max(0, self.totals.get(name, 0) + change)
        return {name: self.totals[name] for name in changes}

    async def standing(self, url: str, name: str) -> Standing:
        assert url == POINTS_URL
        self._check("standing", name)
        return self.standings.get(name, Standing(name, 0, None))

    async def toplist(self, url: str, limit: int) -> list[Standing]:
        assert url == POINTS_URL
        self._check("toplist", limit)
        return self.entries

    async def reset(self, url: str) -> int:
        assert url == POINTS_URL
        self._check("reset", None)
        return self.deleted

    async def set_active(self, url: str, active: bool) -> None:
        assert url == POINTS_URL
        self._check("set_active", active)


def points_bot(data: Mapping[str, object] | None = None, *, active: bool = True) -> tuple[Bot, FakePoints, Clock]:
    config = configured(api={"points": POINTS_URL}, points={"active": active}, **(data or {}))
    assert config.points_api == POINTS_URL and config.points_active is active
    points, clock = FakePoints(), Clock()
    return Bot("wkQB", config, Outbox(), Reloader(), points=points, rng=random.Random(1), clock=clock), points, clock


async def defuse(bot: Bot, starter: str, victim: str = "") -> list[str]:
    await bot.handle(msg(starter, f"!zeitbombe {victim}".strip()))
    game = bot.games["zeitbombe"]
    assert isinstance(game, Timebomb)
    said(bot)
    await bot.handle(msg(victim or starter, f"!schneide {game.wire}"))
    return said(bot)


async def test_points_for_defusing_a_bomb() -> None:
    bot, points, _ = points_bot()
    assert await defuse(bot, "Emil") == ["Emil hat die Bombe entschärft. Glückwunsch! Emil bekommt 5 Punkte!"]
    await bot.settle()
    assert points.adds == [{"Emil": 5}]


async def test_nothing_is_reported_before_the_game_awards_something() -> None:
    bot, points, _ = points_bot()
    await bot.handle(msg("Emil", "!zeitbombe"))
    await bot.settle()
    assert points.calls == []


async def test_a_lost_bomb_gives_no_points() -> None:
    bot, points, clock = points_bot()
    await bot.handle(msg("Bert", "!zeitbombe Dora"))
    clock.now += timedelta(seconds=30)
    bot.tick()
    await bot.handle(msg("Bert", "!zeitbombe Dora"))
    await bot.handle(msg("Dora", "!cheat"))
    await bot.settle()
    assert points.calls == []


async def test_hangman_points_are_reported_per_move() -> None:
    bot, points, _ = points_bot(WORDS)
    await bot.handle(msg("Emil", "!hangman"))
    await bot.handle(msg("Emil", "!hangman k"))
    await bot.handle(msg("Dora", "!hangman a"))
    said(bot)
    await bot.handle(msg("EMIL", "!hangman katze"))
    assert said(bot) == ["Volltreffer EMIL, Katze ist richtig! EMIL bekommt 10 Punkte!"]
    await bot.settle()
    assert points.adds == [{"Emil": 1}, {"Dora": 1}, {"EMIL": 10}]


async def test_wordmix_points() -> None:
    bot, points, _ = points_bot(WORDS)
    await bot.handle(msg("Emil", "!wordmix"))
    said(bot)
    await bot.handle(msg("Emil", "!wordmix ab"))
    assert said(bot) == ["Volltreffer Emil, ab ist richtig! Emil bekommt 1 Punkt!"]
    await bot.settle()
    assert points.adds == [{"Emil": 1}]


async def test_case_variants_of_a_name_are_merged_into_one_request() -> None:
    quiz = {
        "show_solution": True,
        "quizzes": [{"name": "Z", "questions": [{"question": "1?", "answer": "a"}, {"question": "2?", "answer": "b"}]}],
    }
    bot, points, _ = points_bot({"quiz": quiz})
    await bot.handle(msg("Emil", "!quiz 1"))
    for user in ["Dora", "dora"]:
        await bot.handle(msg(user, "a" if user == "Dora" else "b"))
        await bot.settle()
    assert bot.games == {}
    # the answers are in random order, so only the sums matter
    assert [sum(call.values()) for call in points.adds] == [2, 12]
    assert [len(call) for call in points.adds] == [1, 1]


async def test_quiz_answers_as_plain_messages_score() -> None:
    bot, points, _ = points_bot(QUIZ)
    await bot.handle(msg("Emil", "!quiz 1"))
    said(bot)
    await bot.handle(msg("Dora", "muh"))
    assert said(bot)[0].endswith("Dora, Muh ist korrekt! Dora bekommt 2 Punkte!")
    await bot.settle()
    assert points.adds == [{"Dora": 12}]


async def test_quiz_end_line_has_the_bonus() -> None:
    bot, _, _ = points_bot(QUIZ)
    await bot.handle(msg("Emil", "!quiz 1"))
    said(bot)
    await bot.handle(msg("Dora", "muh"))
    assert said(bot)[1] == (
        "Das waren alle Fragen, das Quiz ist daher beendet! Dora hat mit 1 richtigen Antwort gewonnen! "
        "Dora bekommt 10 Punkte!"
    )


async def test_potato_points_by_the_ticker() -> None:
    bot, points, clock = points_bot()
    for user in ["Emil", "Dora", "Fritz"]:
        await bot.handle(msg(user, "!kartoffel"))
    said(bot)
    clock.now += timedelta(seconds=POTATO_SIGNUP)
    bot.tick()
    for _ in range(2):
        clock.now += timedelta(seconds=POTATO_THROW_TIME)
        bot.tick()
    assert "bekommt 5 Punkte!" in said(bot)[-1]
    assert bot.games == {}
    await bot.settle()
    [call] = points.adds
    assert list(call.values()) == [5]


async def test_guests_get_no_points() -> None:
    bot, points, _ = points_bot(QUIZ)
    await bot.handle(msg("Emil", "!quiz 1"))
    said(bot)
    await bot.handle(msg("Gast", "muh", guest=True))
    assert said(bot)[0].endswith("Gast, Muh ist korrekt!")
    await bot.settle()
    assert points.calls == []


async def test_guests_known_from_the_login_get_no_points() -> None:
    bot, points, _ = points_bot(QUIZ)
    await bot.handle(Login("Gast", True, "12:34"))
    await bot.handle(msg("Emil", "!quiz 1"))
    said(bot)
    await bot.handle(msg("Gast", "muh"))  # the message doesn't say it's a guest
    assert said(bot)[0].endswith("Gast, Muh ist korrekt!")
    await bot.settle()
    assert points.calls == []


async def test_guests_are_remembered_from_their_messages() -> None:
    bot, _, _ = points_bot()
    assert bot._scores("Gast")
    await bot.handle(msg("Gast", "hallo", guest=True))
    assert not bot._scores("Gast") and not bot._scores("gast") and bot._scores("Emil")


async def test_guests_score_again_after_logout() -> None:
    bot, _, _ = points_bot()
    await bot.handle(Login("Gast", True, "12:34"))
    assert not bot._scores("Gast")
    await bot.handle(Logout("Gast", True, "12:35", False))
    assert bot._scores("Gast")


async def test_ignored_users_get_no_points() -> None:
    bot, _, _ = points_bot()
    assert not bot._scores("Spam") and not bot._scores("SPAM")
    assert bot._scores("Cem")  # team members can't be ignored


async def test_a_bomb_for_something_that_is_no_nick_gives_no_points() -> None:
    bot, points, _ = points_bot()
    assert await defuse(bot, "Bert", "Jürgen") == ["Jürgen hat die Bombe entschärft. Glückwunsch!"]
    await bot.settle()
    assert points.calls == []


async def test_nothing_while_the_points_are_off() -> None:
    bot, points, _ = points_bot(active=False)
    assert await defuse(bot, "Emil") == ["Emil hat die Bombe entschärft. Glückwunsch!"]
    await bot.settle()
    assert points.calls == []


async def test_nothing_without_an_api() -> None:
    config = configured(points={"active": True})
    bot = Bot("wkQB", config, Outbox(), Reloader(), points=FakePoints(), rng=random.Random(1), clock=Clock())
    assert await defuse(bot, "Emil") == ["Emil hat die Bombe entschärft. Glückwunsch!"]


async def test_nothing_without_a_client() -> None:
    bot, _, _ = make(configured(api={"points": POINTS_URL}, points={"active": True}))
    assert await defuse(bot, "Emil") == ["Emil hat die Bombe entschärft. Glückwunsch!"]
    await bot.settle()


async def test_points_disabled_during_a_report_switches_the_points_off() -> None:
    bot, points, _ = points_bot()
    points.error = PointsDisabled("off")
    await defuse(bot, "Emil")
    await bot.settle()
    assert points.calls == [("add", {"Emil": 5})]
    assert not bot.config.points_active
    points.error = None
    assert await defuse(bot, "Cem") == ["Cem hat die Bombe entschärft. Glückwunsch!"]
    await bot.settle()
    assert len(points.calls) == 1


async def test_a_points_error_is_only_logged_and_the_game_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    bot, points, _ = points_bot(WORDS)
    points.error = PointsError("HTTP 500")
    await bot.handle(msg("Emil", "!hangman"))
    await bot.handle(msg("Emil", "!hangman k"))
    await bot.settle()
    assert any(r.levelno == logging.WARNING and "HTTP 500" in r.getMessage() for r in caplog.records)
    assert bot.config.points_active and "hangman" in bot.games
    said(bot)
    points.error = None
    await bot.handle(msg("Emil", "!hangman katze"))
    assert said(bot) == ["Volltreffer Emil, Katze ist richtig! Emil bekommt 10 Punkte!"]
    await bot.settle()
    assert points.adds == [{"Emil": 10}]


async def test_settle_without_reports_returns_at_once() -> None:
    bot, _, _ = make()
    await bot.settle()


# commands


async def test_mypoints() -> None:
    bot, points, _ = points_bot()
    points.standings = {"Dora": Standing("Dora", 17, 2), "Fritz": Standing("Fritz", 1, 3)}
    for user in ["Emil", "Dora", "Fritz"]:
        await bot.handle(msg(user, "!mypoints"))
    await bot.handle(Whisper("Dora", False, "12:34", "!mypoints"))
    assert said(bot) == [
        "Emil, du hast noch keine Punkte.",
        "Dora, du hast 17 Punkte (Platz 2).",
        "Fritz, du hast 1 Punkt (Platz 3).",
        "/f Dora Dora, du hast 17 Punkte (Platz 2).",
    ]
    assert [call for call, _ in points.calls] == ["standing"] * 4


async def test_mypoints_escapes_the_name() -> None:
    bot, _, _ = points_bot()
    await bot.handle(msg("<b>", "!mypoints"))
    assert said(bot) == ["&lt;b&gt;, du hast noch keine Punkte."]


async def test_toplist() -> None:
    bot, points, _ = points_bot()
    points.entries = [Standing("Anna", 17, 1), Standing("Bert", 17, 1), Standing("<b>", 1, 2)]
    await bot.handle(msg("Emil", "!toplist"))
    await bot.handle(Whisper("Emil", False, "12:34", "!toplist"))
    assert said(bot) == [
        "Toplist: 1. Anna (17 Punkte), 1. Bert (17 Punkte), 2. &lt;b&gt; (1 Punkt)",
        "/f Emil Toplist: 1. Anna (17 Punkte), 1. Bert (17 Punkte), 2. &lt;b&gt; (1 Punkt)",
    ]


async def test_toplist_fits_into_one_message() -> None:
    bot, points, _ = points_bot()
    points.entries = [Standing(f"Sehr_langer_Nick_Nummer_{i:02}", 1_000_000 - i, i) for i in range(1, 21)]
    await bot.handle(msg("Emil", "!toplist 20"))
    await bot.handle(Whisper("Emil_mit_langem_Nick", False, "12:34", "!toplist 20"))
    public, whispered = said(bot)
    for line in (public, whispered):
        assert len(line.encode()) <= MAX_MESSAGE_BYTES
        assert line.endswith(" Punkte)")  # whole entries only
    assert whispered.startswith("/f Emil_mit_langem_Nick Toplist: 1. Sehr_langer_Nick_Nummer_01")
    assert 10 < whispered.count("Punkte)") <= public.count("Punkte)") < 20


async def test_long_jointext_keeps_the_version() -> None:
    bot, _, _ = make(replace(CONFIG, jointext="Hallo " * 200))
    bot.connected()
    [line] = said(bot)
    assert line.endswith("… (wkQB 6.0, https://wkqb.de)") and len(line.encode()) <= MAX_MESSAGE_BYTES


async def test_empty_toplist() -> None:
    bot, _, _ = points_bot()
    await bot.handle(msg("Emil", "!toplist"))
    assert said(bot) == ["Leider hat noch niemand einen Punkt! :-("]


@pytest.mark.parametrize(
    ("param", "limit"),
    [
        ("", 10),
        (" 5", 5),
        (" 20", 20),
        (" 21", 20),
        (" 999999999999", 20),
        (" 1", 1),
        (" 0", 1),
        (" abc", 10),
        (" -3", 10),
        (" 2.5", 10),
        (" 3 foo", 3),
        (" foo 3", 10),
    ],
)
async def test_toplist_limit(param: str, limit: int) -> None:
    bot, points, _ = points_bot()
    await bot.handle(msg("Emil", f"!toplist{param}"))
    assert points.calls == [("toplist", limit)]


@pytest.mark.parametrize("text", ["!mypoints", "!toplist"])
async def test_reading_commands_while_off_or_without_api(text: str) -> None:
    bot, points, _ = points_bot(active=False)
    await bot.handle(msg("Emil", text))
    await bot.handle(Whisper("Emil", False, "12:34", text))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!", "/f Emil Das Punktesystem ist deaktiviert!"]
    assert points.calls == []
    bot, _, _ = make(configured())
    await bot.handle(msg("Emil", text))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]


@pytest.mark.parametrize("text", ["!mypoints", "!toplist"])
async def test_reading_commands_without_a_client(text: str) -> None:
    bot, _, _ = make(configured(api={"points": POINTS_URL}, points={"active": True}))
    await bot.handle(msg("Emil", text))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]


@pytest.mark.parametrize("text", ["!mypoints", "!toplist", "!modifypoints Dora 5", "!resetpoints"])
async def test_commands_fail_gracefully_on_a_points_error(text: str, caplog: pytest.LogCaptureFixture) -> None:
    bot, points, _ = points_bot()
    points.error = PointsError("HTTP 500")
    await bot.handle(msg("Anna", text))
    await bot.handle(Whisper("Anna", False, "12:34", text))
    assert said(bot) == ["Das hat leider nicht geklappt :-(", "/f Anna Das hat leider nicht geklappt :-("]
    assert "HTTP 500" in caplog.text and bot.config.points_active


async def test_pointson_and_off_fail_gracefully_on_a_points_error() -> None:
    bot, points, _ = points_bot()
    points.error = PointsError("HTTP 500")
    await bot.handle(msg("Cem", "!pointsoff"))
    assert said(bot) == ["Das hat leider nicht geklappt :-("]
    assert bot.config.points_active


@pytest.mark.parametrize("text", ["!mypoints", "!toplist", "!modifypoints Dora 5", "!resetpoints"])
async def test_a_disabled_answer_switches_the_points_off(text: str) -> None:
    bot, points, _ = points_bot()
    points.error = PointsDisabled("off")
    await bot.handle(msg("Anna", text))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]
    assert not bot.config.points_active


async def test_pointsoff_and_pointson() -> None:
    bot, points, _ = points_bot()
    await bot.handle(msg("Emil", "!pointsoff"))  # mods and up only
    assert said(bot) == [] and bot.config.points_active and points.calls == []
    await bot.handle(msg("Cem", "!pointsoff"))
    assert said(bot) == ["Das Punktesystem ist nun deaktiviert!"]
    assert not bot.config.points_active and points.calls == [("set_active", False)]
    await bot.handle(msg("Emil", "!toplist"))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]
    await bot.handle(Whisper("Bert", False, "12:34", "!pointson"))  # works while off
    assert said(bot) == ["/f Bert Das Punktesystem ist nun aktiviert!"]
    assert bot.config.points_active and points.calls[-1] == ("set_active", True)
    await bot.handle(msg("Anna", "!pointson"))  # and while on
    assert said(bot) == ["Das Punktesystem ist nun aktiviert!"]


async def test_pointson_without_api() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Cem", "!pointson"))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]
    assert not bot.config.points_active


async def test_pointson_without_a_client() -> None:
    bot, _, _ = make(configured(api={"points": POINTS_URL}, points={"active": False}))
    await bot.handle(msg("Cem", "!pointson"))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"]


async def test_modifypoints() -> None:
    bot, points, _ = points_bot()
    for user in ["Emil", "Cem"]:
        await bot.handle(msg(user, "!modifypoints Dora 5"))  # admins and up only
    assert said(bot) == [] and points.calls == []
    await bot.handle(msg("Bert", "!modifypoints Dora 5"))
    await bot.handle(msg("Anna", "!modifypoints Dora +2"))
    await bot.handle(msg("Anna", "!modifypoints Dora -1"))
    await bot.handle(Whisper("Bert", False, "12:34", "!modifypoints dora -100"))
    assert said(bot) == [
        "Dem Punktekonto von Dora wurden 5 Punkte gutgeschrieben! Neuer Stand: 5 Punkte.",
        "Dem Punktekonto von Dora wurden 2 Punkte gutgeschrieben! Neuer Stand: 7 Punkte.",
        "Dem Punktekonto von Dora wurden 1 Punkt abgezogen! Neuer Stand: 6 Punkte.",
        "/f Bert Dem Punktekonto von dora wurden 100 Punkte abgezogen! Neuer Stand: 0 Punkte.",
    ]
    assert points.adds == [{"Dora": 5}, {"Dora": 2}, {"Dora": -1}, {"dora": -100}]


async def test_modifypoints_bounds() -> None:
    bot, points, _ = points_bot()
    await bot.handle(msg("Bert", "!modifypoints Dora 1000000"))
    await bot.handle(msg("Bert", "!modifypoints Dora -1000000"))
    assert points.adds == [{"Dora": 1_000_000}, {"Dora": -1_000_000}]
    assert said(bot)[1].endswith("Neuer Stand: 0 Punkte.")


@pytest.mark.parametrize(
    "param",
    [
        "",
        "Dora",
        "5",
        "Dora 0",
        "Dora +0",
        "Dora -0",
        "Dora 1000001",
        "Dora -1000001",
        "Dora 12345678",
        "Dora 99999999999999999999",
        "Dora fünf",
        "Dora 2.5",
        "Dora 5 6",
        "a b 5",
        "a b",
        "Jürgen 5",
        "Dora\u0663 5",
        "<b> 5",
        "Dora 5x",
        "Dora ++5",
        "Dora 1e3",
    ],
)
async def test_modifypoints_validation(param: str) -> None:
    bot, points, _ = points_bot()
    await bot.handle(msg("Bert", f"!modifypoints {param}"))
    await bot.handle(Whisper("Bert", False, "12:34", f"!modifypoints {param}"))
    usage = "So geht das: !modifypoints NAME PUNKTE (z. B. 5 oder -5)"
    assert said(bot) == [usage, f"/f Bert {usage}"]
    assert points.calls == []


async def test_modifypoints_usage_has_the_command_sign() -> None:
    bot, _, _ = points_bot({"command_sign": "#"})
    await bot.handle(msg("Bert", "#modifypoints"))
    assert said(bot) == ["So geht das: #modifypoints NAME PUNKTE (z. B. 5 oder -5)"]


async def test_modifypoints_while_off() -> None:
    bot, points, _ = points_bot(active=False)
    await bot.handle(msg("Bert", "!modifypoints Dora 5"))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"] and points.calls == []


async def test_resetpoints() -> None:
    bot, points, _ = points_bot()
    for user in ["Emil", "Cem", "Bert"]:
        await bot.handle(msg(user, "!resetpoints"))  # the master only
    assert said(bot) == [] and points.calls == []
    await bot.handle(msg("Anna", "!resetpoints"))
    await bot.handle(Whisper("Anna", False, "12:34", "!resetpoints"))
    assert said(bot) == ["Die Punkte wurden zurückgesetzt!", "/f Anna Die Punkte wurden zurückgesetzt!"]
    assert points.calls == [("reset", None)] * 2


async def test_resetpoints_while_off() -> None:
    bot, points, _ = points_bot(active=False)
    await bot.handle(msg("Anna", "!resetpoints"))
    assert said(bot) == ["Das Punktesystem ist deaktiviert!"] and points.calls == []


async def test_ignored_users_cant_use_the_point_commands() -> None:
    bot, points, _ = points_bot()
    for text in ["!mypoints", "!toplist", "!pointson", "!modifypoints Dora 5", "!resetpoints"]:
        await bot.handle(msg("Spam", text))
    assert said(bot) == [] and points.calls == []
