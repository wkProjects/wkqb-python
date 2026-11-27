import logging
import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wkqb.bot import Bot, Level
from wkqb.config import CalendarEntry, Config, ConfigError, GuestMode, parse
from wkqb.cron import TIMEZONE
from wkqb.events import Back, FloodWarning, Login, Logout, Message, RoomEnter, SystemMessage, Unparsed, Whisper
from wkqb.games import HotPotato, Timebomb
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
    assert answers[2] == "Das waren alle Fragen, das Quiz ist daher beendet!"
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
