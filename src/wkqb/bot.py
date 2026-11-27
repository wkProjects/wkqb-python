"""What the bot does with the chat events: commands, scheduled messages, greetings.

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
from datetime import datetime
from enum import IntEnum

from wkqb import VERSION, cron
from wkqb.config import Config, ConfigError, GuestMode
from wkqb.events import Event, FloodWarning, Login, Logout, Message, RoomLeave, SystemMessage, Unparsed, Whisper
from wkqb.outbox import Outbox
from wkqb.tokens import render

logger = logging.getLogger(__name__)

_COMMAND = re.compile(r"^!(?P<name>\w+)(?:\s+(?P<param>.*))?$", re.DOTALL)

Reload = Callable[[], Awaitable[tuple[Config, list[str]]]]


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
        """Called about once a second: scheduled messages."""
        now = self._clock()
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
            case Logout(user=user) | RoomLeave(user=user):
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
        if handler is None:
            self.reply(command, "Den Befehl kenne ich leider nicht :-(")
            return
        await handler(self, command)

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

    # -- helpers

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
}
