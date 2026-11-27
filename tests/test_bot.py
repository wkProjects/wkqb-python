import logging
import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wkqb.bot import Bot, Level
from wkqb.config import CalendarEntry, Config, ConfigError, GuestMode, parse
from wkqb.cron import TIMEZONE
from wkqb.events import FloodWarning, Login, Logout, Message, RoomEnter, SystemMessage, Unparsed, Whisper
from wkqb.games import Timebomb
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
    config, warnings = parse({"users": {"master": "Anna", "admins": ["Bert"], "ignored": ["Spam"]}, **data})
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


async def test_welcome_back() -> None:
    bot, clock, _ = make(configured(**GREETINGS))
    await bot.handle(Login("Emil", False, "12:34"))
    await bot.handle(Logout("Emil", False, "12:35", False))
    clock.now += timedelta(minutes=29)
    await bot.handle(Login("emil", False, "13:04"))
    await bot.handle(Logout("Emil", False, "13:05", False))
    clock.now += timedelta(minutes=31)
    await bot.handle(Login("Emil", False, "13:36"))
    clock.now += timedelta(seconds=5)
    bot.tick()
    assert said(bot) == ["Hallo Emil!", "wb emil!", "Hallo Emil!"]


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
    await bot.handle(RoomEnter("Emil", False, "12:34", "Smalltalk"))  # as the old bot: no greeting
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
    clock.now += timedelta(seconds=60)
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
    assert answers[2] == "Das waren alle Fragen, das Quiz ist daher beendet!"
    assert bot.games == {}


async def test_no_quiz() -> None:
    bot, _, _ = make(configured())
    await bot.handle(msg("Emil", "!quiz 1"))
    assert said(bot) == ["Ich kenne leider kein Quiz :-("]


async def test_timebomb() -> None:
    bot, clock, _ = make(configured())
    await bot.handle(msg("Emil", "!schneide rot"))
    await bot.handle(msg("Emil", "!zeitbombe spam"))
    assert said(bot) == ["Es läuft doch gar kein Spiel!", "Mit ignorierten Usern spiele ich nicht!"]

    await bot.handle(msg("Emil", "!zeitbombe Dora bitte"))
    start = said(bot)
    assert start[0].startswith("Emil schiebt Dora eine Bombe zu!")
    await bot.handle(msg("Dora", "!zeitbombe Emil"))
    await bot.handle(msg("Emil", "!schneide rot"))
    assert said(bot) == ["Es läuft bereits ein Spiel!", "Du bist doch gar nicht dran!"]

    game = bot.games["zeitbombe"]
    assert isinstance(game, Timebomb)
    await bot.handle(msg("Dora", f"!schneide {game.wire}"))
    assert said(bot) == ["Dora hat die Bombe entschärft. Glückwunsch!"]
    assert bot.games == {}

    await bot.handle(msg("Emil", "!zeitbombe <i>x</i>"))
    assert said(bot)[0].startswith("Emil schiebt &lt;i&gt;x&lt;/i&gt; eine Bombe zu!")
    clock.now += timedelta(seconds=30)
    bot.tick()
    assert said(bot) == ["&lt;i&gt;x&lt;/i&gt; hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-("]
