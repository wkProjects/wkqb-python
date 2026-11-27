"""What the bot does with the chat events: commands, games, greetings, KI, scheduled messages.

The Bot does no I/O itself: answers go into the outbox, settings come through the reload callback,
and the runner (wkqb.runner) feeds it the events and calls tick() every second.
"""

import asyncio
import html
import logging
import random
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum

from wkqb import VERSION, cron
from wkqb.config import Config, ConfigError, GuestMode, Pattern, WhisperMode
from wkqb.events import Event, FloodWarning, Login, Logout, Message, RoomLeave, SystemMessage, Unparsed, Whisper
from wkqb.games import Game, Hangman, Quiz, Timebomb, Wordmix, quiz_menu
from wkqb.outbox import Outbox
from wkqb.tokens import render

logger = logging.getLogger(__name__)

_COMMAND = re.compile(r"^!(?P<name>\w+)(?:\s+(?P<param>.*))?$", re.DOTALL)

Reload = Callable[[], Awaitable[tuple[Config, list[str]]]]

#: Who comes back within this time gets the "welcome back" greeting
WELCOME_BACK = timedelta(minutes=30)
#: Greetings wait a little, so the newcomer's chat window is ready
GREETING_DELAY = timedelta(seconds=5)


class Level(IntEnum):
    IGNORED = -1
    GUEST = 0
    USER = 1
    MOD = 2
    ADMIN = 3
    MASTER = 4

    @property
    def label(self) -> str:
        return {
            Level.IGNORED: "Ignoriert",
            Level.GUEST: "Gast",
            Level.USER: "User",
            Level.MOD: "Mod",
            Level.ADMIN: "Admin",
            Level.MASTER: "Master",
        }[self]


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    """Lower case, without the "!" """
    param: str
    """Everything after the name, trimmed"""
    user: str
    level: Level
    whispered: bool
    time: str | None


class Bot:
    def __init__(
        self,
        name: str,
        config: Config,
        outbox: Outbox,
        reload: Reload,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = cron.now,
    ) -> None:
        self.name = name
        self.outbox = outbox
        self._reload = reload
        self._rng = rng or random.Random()
        self._clock = clock
        self.quit = asyncio.Event()
        """Set by !quit: the runner sends the rest of the outbox, logs out and ends"""
        self._guests: set[str] = set()
        self._seen: dict[str, datetime] = {}
        """When users (lower case) logged in or out last"""
        self._greetings: list[tuple[datetime, str]] = []
        self.games: dict[str, Game] = {}
        """Running games by command (hangman, wordmix, quiz, zeitbombe)"""
        self._greeted_once = False
        self.config = config
        self._quote_schedule: cron.Schedule | None = None
        self._calendar: list[tuple[cron.Schedule, str]] = []
        self.apply(config)

    def apply(self, config: Config) -> None:
        """Uses new settings (start, !reload)."""
        self.config = config
        now = self._clock()
        self._quote_schedule = cron.Schedule(config.quote_cron, now) if config.quote_cron and config.quotes else None
        self._calendar = [(cron.Schedule(entry.schedule, now), entry.message) for entry in config.calendar]

    def connected(self) -> None:
        """The chat stream runs; the first time, the bot says hello."""
        if not self._greeted_once:
            self._greeted_once = True
            self.say(f"{self.config.jointext} (wkQB {VERSION}, https://wkqb.de)".strip())

    def tick(self) -> None:
        """Called about once a second: scheduled messages, greetings, game timeouts."""
        now = self._clock()
        due = [text for at, text in self._greetings if at <= now]
        self._greetings = [(at, text) for at, text in self._greetings if at > now]
        self.say(*due)
        for name, game in list(self.games.items()):
            self.say(*game.tick(now.timestamp()))
            if not game.running:
                del self.games[name]
        if self._quote_schedule is not None and self._quote_schedule.due(now):
            self._send_quote(self._rng.randrange(len(self.config.quotes)))
        for schedule, message in self._calendar:
            if schedule.due(now):
                self.say(render(message, {"time": f"{now:%H:%M}"}, rng=self._rng))

    async def handle(self, event: Event) -> None:
        match event:
            case Message() | Whisper():
                await self._message(event)
            case Login(user=user, guest=guest):
                if guest:
                    self._guests.add(user.casefold())
                self._greet(user, guest)
            case Logout(user=user):
                self._guests.discard(user.casefold())
                self._seen[user.casefold()] = self._clock()
            case RoomLeave(user=user):
                self._guests.discard(user.casefold())
            case FloodWarning(user=user, number=number) if self._is_me(user):
                logger.warning("Webkicks hat den Bot wegen Flooding verwarnt (%d. Verwarnung)", number)
            case SystemMessage(text=text):
                logger.info("System-Meldung: %s", text)
            case Unparsed(line=line):
                logger.debug("Unbekannte Zeile: %s", line)
            case _:
                pass

    def level(self, user: str, guest: bool = False) -> Level:
        name = user.casefold()
        config = self.config
        if config.master and name == config.master.casefold():
            return Level.MASTER
        if _contains(config.admins, name):
            return Level.ADMIN
        if _contains(config.mods, name):
            return Level.MOD
        # team members can't be ignored
        if _contains(config.ignored, name):
            return Level.IGNORED
        return Level.GUEST if guest else Level.USER

    def say(self, *lines: str) -> None:
        self.outbox.put(*lines)

    def reply(self, command: Command, *lines: str) -> None:
        """Answers in public, or by whisper if the command was whispered."""
        if command.whispered:
            self.say(*(f"/f {command.user} {line}" for line in lines))
        else:
            self.say(*lines)

    async def _message(self, event: Message | Whisper) -> None:
        if self._is_me(event.user):
            return
        guest = event.guest or event.user.casefold() in self._guests
        level = self.level(event.user, guest)
        if level == Level.IGNORED:
            return
        match = _COMMAND.match(event.text)
        if match is None:
            self._patterns(event, level)
            return
        if level == Level.GUEST and self.config.guest_mode != GuestMode.FULL:
            return
        command = Command(
            name=match.group("name").casefold(),
            param=(match.group("param") or "").strip(),
            user=event.user,
            level=level,
            whispered=isinstance(event, Whisper),
            time=event.time,
        )
        await self.command(command)

    async def command(self, command: Command) -> None:
        handler = _SYSTEM_COMMANDS.get(command.name)
        if handler is not None:
            await handler(self, command)
            return
        custom = [c for c in self.config.commands if c.command.casefold() == command.name]
        if not custom:
            self.reply(command, "Den Befehl kenne ich leider nicht :-(")
            return
        for entry in custom:
            allowed = WhisperMode.WHISPER if command.whispered else WhisperMode.PUBLIC
            if command.level >= entry.min_level and entry.whisper_mode in (allowed, WhisperMode.BOTH):
                values = {"user": command.user, "param": command.param, "time": self._time(command.time)}
                # in public: the reactions can be chat commands like /kick, which must not be whispered
                self.say(*(render(line, chat_values=values, rng=self._rng) for line in entry.reaction))
                return

    # -- system commands

    async def _ping(self, command: Command) -> None:
        self.reply(command, "Pong!")

    async def _say(self, command: Command) -> None:
        if command.level >= Level.ADMIN and command.param:
            self.say(command.param)

    async def _reload_command(self, command: Command) -> None:
        if command.level < Level.MOD:
            return
        try:
            config, warnings = await self._reload()
        except ConfigError as e:
            logger.error("Einstellungen konnten nicht geladen werden: %s", e)
            self.reply(command, "Die Einstellungen konnten nicht geladen werden :-(")
            return
        for warning in warnings:
            logger.warning("Einstellungen: %s", warning)
        self.apply(config)
        self.reply(command, "Einstellungen neu geladen!")

    async def _quit(self, command: Command) -> None:
        if command.level >= Level.ADMIN:
            self.say("Oh je, ich muss wohl gehen :-(")
            logger.info("Beendet durch !quit von %s", command.user)
            self.quit.set()

    async def _master(self, command: Command) -> None:
        master = self.config.master
        self.reply(command, f"Master: {html.escape(master)}" if master else "Ich habe keinen Master :-(")

    async def _admins(self, command: Command) -> None:
        self._list(command, "Admins", self.config.admins, "Es gibt keine Admins :-(")

    async def _mods(self, command: Command) -> None:
        self._list(command, "Mods", self.config.mods, "Es gibt keine Mods :-(")

    async def _ignored(self, command: Command) -> None:
        self._list(command, "Ignoriert", self.config.ignored, "Niemand wird ignoriert :-)")

    async def _level(self, command: Command) -> None:
        self.reply(command, f"{html.escape(command.user)}, dein Level ist: {command.level.label}")

    async def _quote(self, command: Command) -> None:
        quotes = self.config.quotes
        if not quotes:
            self.reply(command, "Ich habe leider keine Zitate :-(")
        elif not command.param:
            self._send_quote(self._rng.randrange(len(quotes)), command)
        elif command.param.isdigit():
            number = max(1, int(command.param))
            if number > len(quotes):
                self.reply(command, "So viele Zitate habe ich nicht :-(")
            else:
                self._send_quote(number - 1, command)
        else:
            search = command.param.casefold()
            found = [i for i, quote in enumerate(quotes) if search in quote.casefold()]
            if found:
                self._send_quote(self._rng.choice(found), command)
            else:
                self.reply(command, f"Kein Zitat mit '{html.escape(command.param)}' gefunden :-(")

    # -- games (always in public)

    async def _hangman(self, command: Command) -> None:
        await self._word_game(command, "hangman")

    async def _wordmix(self, command: Command) -> None:
        await self._word_game(command, "wordmix")

    async def _word_game(self, command: Command, name: str) -> None:
        now = self._clock().timestamp()
        game = self.games.get(name)
        if isinstance(game, Hangman | Wordmix):
            self.say(*game.guess(command.user, command.param, now))
        else:
            settings = self.config.hangman if name == "hangman" else self.config.wordmix
            if not settings.words:
                self.say("Ich habe leider keine Wörter :-(")
                return
            kind = Hangman if name == "hangman" else Wordmix
            game = self.games[name] = kind(settings.words, settings.show_solution, now, self._rng)
            self.say(*game.start())
        if not game.running:
            del self.games[name]

    async def _quiz(self, command: Command) -> None:
        now = self._clock().timestamp()
        game = self.games.get("quiz")
        if isinstance(game, Quiz):
            self.say(*game.answer(command.user, command.param, now))
        else:
            quizzes = self.config.quizzes
            if not command.param or not quizzes:
                self.say(*quiz_menu(quizzes))
                return
            if not command.param.isdigit() or not 1 <= int(command.param) <= len(quizzes):
                self.say(f"{html.escape(command.param)} ist keine gültige ID.")
                return
            game = self.games["quiz"] = Quiz(
                quizzes[int(command.param) - 1], self.config.quiz_show_solution, now, self._rng
            )
            self.say(*game.start())
        if not game.running:
            del self.games["quiz"]

    async def _timebomb(self, command: Command) -> None:
        if "zeitbombe" in self.games:
            self.say("Es läuft bereits ein Spiel!")
            return
        victim = command.param.split()[0] if command.param else command.user
        if self.level(victim) == Level.IGNORED:
            self.say("Mit ignorierten Usern spiele ich nicht!")
            return
        game = Timebomb(command.user, victim, self._clock().timestamp(), self._rng)
        self.games["zeitbombe"] = game
        self.say(*game.start())

    async def _cut(self, command: Command) -> None:
        game = self.games.get("zeitbombe")
        if not isinstance(game, Timebomb):
            self.say("Es läuft doch gar kein Spiel!")
            return
        self.say(*game.cut(command.user, command.param))
        if not game.running:
            del self.games["zeitbombe"]

    # -- greetings and KI

    def _greet(self, user: str, guest: bool) -> None:
        if self._is_me(user) or self.level(user, guest) == Level.IGNORED:
            return
        if guest and self.config.guest_mode == GuestMode.IGNORE:
            return
        now = self._clock()
        name = user.casefold()
        greeting = self.config.custom_greetings.get(name, self.config.greeting)
        last = self._seen.get(name)
        template = greeting.wb if last is not None and now - last <= WELCOME_BACK else greeting.hello
        self._seen[name] = now
        if template.strip():
            text = render(template, chat_values={"user": user}, rng=self._rng)
            self._greetings.append((now + GREETING_DELAY, text))

    def _patterns(self, event: Message | Whisper, level: Level) -> None:
        """The KI: reactions to messages that contain a text or match a regex."""
        guest = level == Level.GUEST
        for entry in self.config.patterns:
            if guest and not entry.guest_reaction:
                continue
            groups = _match(entry, event.text)
            if groups is None:
                continue
            if entry.needs_bot_name and not _contains_word(event.text, self.name):
                continue
            values = {"user": event.user, "satz": event.text, "time": self._time(event.time)}
            lines = [render(line, chat_values=values, groups=groups, rng=self._rng) for line in entry.reaction]
            if isinstance(event, Whisper):
                self.say(*(f"/f {event.user} {line}" for line in lines))
            else:
                self.say(*lines)

    # -- helpers

    def _time(self, time: str | None) -> str:
        return time or f"{self._clock():%H:%M}"

    def _send_quote(self, index: int, command: Command | None = None) -> None:
        config = self.config
        text = " ".join(part for part in (config.quote_prefix, config.quotes[index], config.quote_suffix) if part)
        line = render(text.strip(), {"this": index + 1, "total": len(config.quotes)}, rng=self._rng)
        if command is None:
            self.say(line)
        else:
            self.reply(command, line)

    def _list(self, command: Command, title: str, names: Sequence[str], empty: str) -> None:
        if names:
            self.reply(command, f"{title}: {html.escape(', '.join(names))}")
        else:
            self.reply(command, empty)

    def _is_me(self, user: str) -> bool:
        return user.casefold() == self.name.casefold()


def _contains(names: Sequence[str], casefolded: str) -> bool:
    return any(n.casefold() == casefolded for n in names)


def _contains_word(text: str, word: str) -> bool:
    """word occurs in text, not as part of a longer word ("Bot" is not in "Botschafter"), any case.

    Only edges that are letters or digits must not continue a word: "?" is found in "was?".
    """
    if not word:
        return False
    before = r"(?<!\w)" if re.match(r"\w", word[0]) else ""
    after = r"(?!\w)" if re.match(r"\w", word[-1]) else ""
    return re.search(before + re.escape(word) + after, text, re.IGNORECASE) is not None


def _match(entry: Pattern, text: str) -> dict[int, str] | None:
    """The groups ($0 is the whole match) if the entry matches text, else None."""
    if entry.regex is None:
        return {0: entry.pattern} if _contains_word(text, entry.pattern) else None
    found = entry.regex.search(text)
    if found is None:
        return None
    return {0: found.group(0)} | {i: g for i, g in enumerate(found.groups(), 1) if g is not None}


_SYSTEM_COMMANDS: dict[str, Callable[[Bot, Command], Awaitable[None]]] = {
    "ping": Bot._ping,
    "say": Bot._say,
    "reload": Bot._reload_command,
    "quit": Bot._quit,
    "master": Bot._master,
    "admins": Bot._admins,
    "mods": Bot._mods,
    "ignored": Bot._ignored,
    "level": Bot._level,
    "quote": Bot._quote,
    "hangman": Bot._hangman,
    "wordmix": Bot._wordmix,
    "quiz": Bot._quiz,
    "zeitbombe": Bot._timebomb,
    "schneide": Bot._cut,
}
