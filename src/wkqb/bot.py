"""What the bot does with the chat events: commands, games, greetings, KI, scheduled messages.

The Bot does no I/O itself: answers go into the outbox, settings come through the reload callback,
and the runner (wkqb.runner) feeds it the events and calls tick() every second.

HTML: texts the managers entered in the web interface (reactions, quotes, greetings, words, ...)
are sent unchanged. If the bot is a chat admin, all of their HTML works; otherwise Webkicks keeps
only its whitelist (docs/webkicks-protocol.md), and the bot doesn't try to convert anything.
Everything that comes from the chat (parameters, names, matched text, guesses) is escaped, so
nobody can send HTML through the bot, whatever rights it has in the chat.
"""

import asyncio
import html
import logging
import math
import random
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import IntEnum

from wkqb import VERSION, cron
from wkqb.config import Config, ConfigError, GuestMode, Pattern, WhisperMode
from wkqb.events import (
    Back,
    Event,
    FloodWarning,
    Login,
    Logout,
    Message,
    RoomEnter,
    RoomLeave,
    SystemMessage,
    Unparsed,
    Whisper,
)
from wkqb.games import Game, Hangman, HotPotato, Quiz, Timebomb, Wordmix, quiz_menu
from wkqb.outbox import Outbox, fit
from wkqb.points import MAX_CHANGE, NICK, Points, PointsDisabled, PointsError
from wkqb.tokens import render
from wkqb.webkicks import MAX_MESSAGE_BYTES

logger = logging.getLogger(__name__)


Reload = Callable[[], Awaitable[tuple[Config, list[str]]]]
#: Changes the ignore list: (action "add" or "remove", name) -> the new list; raises ConfigError
ChangeIgnored = Callable[[str, str], Awaitable[Sequence[str]]]

#: Who comes back within this time gets the "welcome back" greeting (as the Perl bot did)
WELCOME_BACK = timedelta(hours=6)
#: Greetings wait a little, so the newcomer's chat window is ready
GREETING_DELAY = timedelta(seconds=5)
#: Seconds a KI regex may take for one message
KI_TIMEOUT = 0.1
#: How often users may start these games (mods and up always can)
COOLDOWNS = {"zeitbombe": timedelta(seconds=60), "quiz": timedelta(minutes=3)}
#: Entries of !toplist: by default, and at most (it's one chat line)
TOPLIST = (10, 20)


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
        change_ignored: ChangeIgnored | None = None,
        points: Points | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = cron.now,
    ) -> None:
        self.name = name
        self.outbox = outbox
        self._reload = reload
        self._change_ignored = change_ignored
        self._points = points
        self._reports: set[asyncio.Task[None]] = set()
        """Points being reported to wkqb-web"""
        self._started: dict[str, datetime] = {}
        """When the games with a cooldown were last started"""
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
            suffix = f"(wkQB {VERSION}, https://wkqb.de)"
            jointext = fit(self.config.jointext.strip(), MAX_MESSAGE_BYTES - len(suffix) - 1)
            self.say(f"{jointext} {suffix}".strip())

    def tick(self) -> None:
        """Called about once a second: scheduled messages, greetings, game timeouts."""
        now = self._clock()
        due = [text for at, text in self._greetings if at <= now]
        self._greetings = [(at, text) for at, text in self._greetings if at > now]
        self.say(*due)
        for name, game in list(self.games.items()):
            self.say(*game.tick(now.timestamp()))
            self._after(name, game)
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
            case RoomEnter(user=user, guest=guest):
                self._greet(user, guest or user.casefold() in self._guests)
            case Back(user=user, guest=guest):
                self._greet(user, guest or user.casefold() in self._guests, back=True)
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
        if event.guest:
            # remembered for the points of games, which other players can end
            self._guests.add(event.user.casefold())
        guest = event.user.casefold() in self._guests
        level = self.level(event.user, guest)
        if level == Level.IGNORED:
            return
        sign = re.escape(self.config.command_sign)
        match = re.match(rf"^{sign}(?P<name>\w+)(?:\s+(?P<param>.*))?$", event.text, re.DOTALL)
        if match is None:
            quiz = self.games.get("quiz")
            if isinstance(quiz, Quiz) and isinstance(event, Message):
                # quiz answers can be sent as normal messages
                self.say(*quiz.check(event.user, event.text, self._clock().timestamp()))
                self._after("quiz", quiz)
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
            # chat text like the rest: a bot admin needn't be a chat admin
            self.say(html.escape(command.param))

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
            logger.info("Beendet durch !quit")
            logger.debug("!quit von %s", command.user)
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
        """!quote / !zitat [NR or TEXT]"""
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
            self._search_quote(command)

    async def _search(self, command: Command) -> None:
        """!suche TEXT"""
        if not self.config.quotes:
            self.reply(command, "Ich habe leider keine Zitate :-(")
        elif not command.param:
            self.reply(command, f"Wonach soll ich suchen? {html.escape(self.config.command_sign)}suche TEXT")
        else:
            self._search_quote(command)

    def _search_quote(self, command: Command) -> None:
        search = command.param.casefold()
        found = [i for i, quote in enumerate(self.config.quotes) if search in quote.casefold()]
        if found:
            self._send_quote(self._rng.choice(found), command)
        else:
            self.reply(command, f"Kein Zitat mit '{html.escape(command.param)}' gefunden :-(")

    async def _bid(self, command: Command) -> None:
        """!bid, whispered by bot admins: the bot's id, which lets others manage it at wkqb.de"""
        if command.level < Level.ADMIN:
            return
        if not command.whispered:
            self.reply(command, "Das musst du mir flüstern!")
        elif self.config.bot_id:
            self.reply(command, f"Meine ID ist {html.escape(self.config.bot_id)}")
        else:
            self.reply(command, "Ich kenne meine ID leider nicht :-(")

    async def _solution(self, command: Command) -> None:
        """!solution, whispered by bot admins: the solutions of the running games (undocumented, as in Perl)"""
        if command.level < Level.ADMIN:
            return
        if not command.whispered:
            self.reply(command, "Das musst du mir flüstern!")
            return
        solutions = []
        for name, game in self.games.items():
            if isinstance(game, Hangman | Wordmix):
                solutions.append(f"{name}: {game.word}")
            elif isinstance(game, Timebomb):
                solutions.append(f"zeitbombe: {game.wire}")
            elif isinstance(game, Quiz):
                solutions.append(f"quiz: {game.current.answer}")
        self.reply(command, f"Die Lösung ist: {', '.join(solutions)}" if solutions else "Es läuft doch gar kein Spiel!")

    async def _ignore(self, command: Command) -> None:
        await self._change_ignore_list(command, add=True)

    async def _unignore(self, command: Command) -> None:
        await self._change_ignore_list(command, add=False)

    async def _change_ignore_list(self, command: Command, add: bool) -> None:
        """!ignore / !unignore NAME, for bot mods and up; the list is kept by wkqb-web."""
        if command.level < Level.MOD:
            return
        name = command.param.split()[0] if command.param else ""
        shown = html.escape(name)
        ignored = any(n.casefold() == name.casefold() for n in self.config.ignored)
        if not name:
            sign = html.escape(self.config.command_sign)
            self.reply(command, f"Wen denn? {sign}{'ignore' if add else 'unignore'} NAME")
        elif add and self._is_me(name):
            self.reply(command, "Mich selbst ignorieren? Nein danke.")
        elif add and self.level(name) >= Level.MOD:
            self.reply(command, f"{shown} gehört zum Bot-Team und kann nicht ignoriert werden.")
        elif add and ignored:
            self.reply(command, f"{shown} wird bereits ignoriert.")
        elif not add and not ignored:
            self.reply(command, f"{shown} wird gar nicht ignoriert.")
        elif self._change_ignored is None:
            self.reply(command, "Die Ignorier-Liste kann ich leider nicht ändern :-(")
        else:
            try:
                names = await self._change_ignored("add" if add else "remove", name)
            except ConfigError as e:
                logger.warning("Ignorier-Liste nicht geändert: %s", e)
                self.reply(command, "Das hat leider nicht geklappt :-(")
                return
            self.config = replace(self.config, ignored=list(names))
            self.reply(command, f"{shown} wird ab sofort ignoriert." if add else f"{shown} wird nicht mehr ignoriert.")

    # -- games (always in public)

    async def _hangman(self, command: Command) -> None:
        await self._word_game(command, "hangman")

    async def _wordmix(self, command: Command) -> None:
        await self._word_game(command, "wordmix")

    async def _word_game(self, command: Command, name: str) -> None:
        now = self._clock().timestamp()
        game = self.games.get(name)
        sign = self.config.command_sign
        if isinstance(game, Hangman | Wordmix):
            self.say(*game.guess(command.user, command.param, now))
        else:
            settings = self.config.hangman if name == "hangman" else self.config.wordmix
            if not settings.words:
                self.say("Ich habe leider keine Wörter :-(")
                return
            if not self._may_start_games(command):
                return
            if name == "hangman":
                game = Hangman(
                    settings.words,
                    settings.show_solution,
                    now,
                    self._rng,
                    sign=sign,
                    picture=settings.picture,
                    scores=self._scores,
                )
            else:
                game = Wordmix(
                    settings.words,
                    settings.show_solution,
                    now,
                    self._rng,
                    sign=sign,
                    hints=settings.hint,
                    scores=self._scores,
                )
            self.games[name] = game
            self.say(*game.start())
        self._after(name, game)

    async def _quiz(self, command: Command) -> None:
        now = self._clock().timestamp()
        game = self.games.get("quiz")
        if isinstance(game, Quiz):
            self.say(*game.answer(command.user, command.param, now))
        else:
            quizzes = self.config.quizzes
            if not command.param or not quizzes:
                self.say(*quiz_menu(quizzes, self.config.command_sign))
                return
            if not command.param.isdigit() or not 1 <= int(command.param) <= len(quizzes):
                self.say(f"{html.escape(command.param)} ist keine gültige ID.")
                return
            if not self._may_start_games(command) or not self._cooled_down(command, "quiz", "Das nächste Quiz"):
                return
            game = self.games["quiz"] = Quiz(
                quizzes[int(command.param) - 1], self.config.quiz_show_solution, now, self._rng, scores=self._scores
            )
            self.say(*game.start())
        self._after("quiz", game)

    async def _timebomb(self, command: Command) -> None:
        if "zeitbombe" in self.games:
            self.say("Es läuft bereits ein Spiel!")
            return
        victim = command.param.split()[0] if command.param else command.user
        if victim.casefold() != command.user.casefold() and command.level < Level.ADMIN:
            self.reply(command, "Nur Bot-Admins können anderen eine Bombe zuschieben.")
            return
        if self.level(victim) == Level.IGNORED:
            self.say("Mit ignorierten Usern spiele ich nicht!")
            return
        if not self._may_start_games(command) or not self._cooled_down(command, "zeitbombe", "Die nächste Bombe"):
            return
        game = Timebomb(
            command.user,
            victim,
            self._clock().timestamp(),
            self._rng,
            sign=self.config.command_sign,
            scores=self._scores,
        )
        self.games["zeitbombe"] = game
        self.say(*game.start())

    async def _cheat(self, command: Command) -> None:
        game = self.games.get("zeitbombe")
        if not isinstance(game, Timebomb):
            self.reply(command, "Es läuft doch gar kein Spiel!")
            return
        if command.user.casefold() != game.victim.casefold():
            self.reply(command, *game.cheat(command.user))
            return
        # in public, even if whispered: everybody shall see it
        self.say(*game.cheat(command.user))
        self._after("zeitbombe", game)

    async def _cut(self, command: Command) -> None:
        game = self.games.get("zeitbombe")
        if not isinstance(game, Timebomb):
            self.say("Es läuft doch gar kein Spiel!")
            return
        self.say(*game.cut(command.user, command.param))
        self._after("zeitbombe", game)

    async def _potato(self, command: Command) -> None:
        game = self.games.get("kartoffel")
        if isinstance(game, HotPotato):
            self.say(*game.join(command.user))
            return
        if not self._may_start_games(command):
            return
        game = self.games["kartoffel"] = HotPotato(
            command.user, self._clock().timestamp(), self._rng, sign=self.config.command_sign, scores=self._scores
        )
        self.say(*game.start())

    async def _throw(self, command: Command) -> None:
        game = self.games.get("kartoffel")
        if not isinstance(game, HotPotato):
            self.say("Es läuft doch gar kein Spiel!")
            return
        self.say(*game.throw(command.user, self._clock().timestamp()))
        self._after("kartoffel", game)

    def _cooled_down(self, command: Command, game: str, what: str) -> bool:
        """Users may only start some games now and then; mods and up always can. Counts the start."""
        now = self._clock()
        last = self._started.get(game)
        if command.level < Level.MOD and last is not None and now - last < COOLDOWNS[game]:
            seconds = math.ceil((last + COOLDOWNS[game] - now).total_seconds())
            self.reply(command, f"{what} gibt es erst in {seconds} Sekunden.")
            return False
        self._started[game] = now
        return True

    def _may_start_games(self, command: Command) -> bool:
        if command.level >= self.config.can_start_games:
            return True
        self.reply(command, f"{html.escape(command.user)}, du darfst leider keine Spiele starten.")
        return False

    # -- points (kept by wkqb-web)

    def _scores(self, user: str) -> bool:
        """Does a player get points? Not while the points system is off, and not as a guest."""
        config = self.config
        if not config.points_active or config.points_api is None or self._points is None:
            return False
        if NICK.fullmatch(user) is None:
            # e.g. a bomb for a made-up name; wkqb-web would refuse the whole report
            return False
        return self.level(user, user.casefold() in self._guests) >= Level.USER

    def _after(self, name: str, game: Game) -> None:
        """After a move: reports the points the game handed out, and forgets the game when it's over."""
        if game.awards:
            changes: dict[str, int] = {}
            for user, points in game.awards:
                key = next((k for k in changes if k.casefold() == user.casefold()), user)
                changes[key] = changes.get(key, 0) + points
            game.awards.clear()
            if changes:
                task = asyncio.create_task(self._report(changes))
                self._reports.add(task)
                task.add_done_callback(self._reports.discard)
        if not game.running and self.games.get(name) is game:
            del self.games[name]

    async def _report(self, changes: dict[str, int]) -> None:
        url = self.config.points_api
        if url is None or self._points is None:
            return
        try:
            await self._points.add(url, changes)
        except PointsDisabled:
            logger.info("Das Punktesystem wurde im Webinterface ausgeschaltet")
            self.config = replace(self.config, points_active=False)
        except PointsError as e:
            logger.warning("Punkte konnten nicht gespeichert werden: %s", e)
            logger.debug("Verlorene Punkte: %s", changes)

    async def settle(self) -> None:
        """Waits until the points handed out so far are reported."""
        if self._reports:
            await asyncio.gather(*self._reports)

    def _points_api(self, command: Command, *, active: bool = True) -> tuple[Points, str] | None:
        """The API and its address, if the command can use them; else answers why not."""
        url = self.config.points_api
        if url is None or self._points is None or (active and not self.config.points_active):
            self.reply(command, "Das Punktesystem ist deaktiviert!")
            return None
        return self._points, url

    def _points_failed(self, command: Command, error: PointsError) -> None:
        if isinstance(error, PointsDisabled):
            self.config = replace(self.config, points_active=False)
            self.reply(command, "Das Punktesystem ist deaktiviert!")
        else:
            logger.warning("Punkte nicht abrufbar: %s", error)
            self.reply(command, "Das hat leider nicht geklappt :-(")

    async def _mypoints(self, command: Command) -> None:
        if (api := self._points_api(command)) is None:
            return
        points, url = api
        try:
            standing = await points.standing(url, command.user)
        except PointsError as e:
            self._points_failed(command, e)
            return
        user = html.escape(command.user)
        if standing.rank is None:
            self.reply(command, f"{user}, du hast noch keine Punkte.")
        else:
            self.reply(command, f"{user}, du hast {_points(standing.points)} (Platz {standing.rank}).")

    async def _toplist(self, command: Command) -> None:
        if (api := self._points_api(command)) is None:
            return
        points, url = api
        limit = int(command.param.split()[0]) if command.param and command.param.split()[0].isdigit() else TOPLIST[0]
        try:
            entries = await points.toplist(url, max(1, min(TOPLIST[1], limit)))
        except PointsError as e:
            self._points_failed(command, e)
            return
        if not entries:
            self.reply(command, "Leider hat noch niemand einen Punkt! :-(")
            return
        # as many entries as fit into one message
        prefix = f"/f {command.user} " if command.whispered else ""
        line = "Toplist:"
        for i, entry in enumerate(entries):
            longer = f"{line}{',' if i else ''} {entry.rank}. {html.escape(entry.name)} ({_points(entry.points)})"
            if len((prefix + longer).encode()) > MAX_MESSAGE_BYTES:
                break
            line = longer
        self.reply(command, line)

    async def _switch_points(self, command: Command, active: bool) -> None:
        """!pointson / !pointsoff, for bot mods and up"""
        if command.level < Level.MOD or (api := self._points_api(command, active=False)) is None:
            return
        points, url = api
        try:
            await points.set_active(url, active)
        except PointsError as e:
            self._points_failed(command, e)
            return
        self.config = replace(self.config, points_active=active)
        self.reply(command, f"Das Punktesystem ist nun {'aktiviert' if active else 'deaktiviert'}!")

    async def _points_on(self, command: Command) -> None:
        await self._switch_points(command, True)

    async def _points_off(self, command: Command) -> None:
        await self._switch_points(command, False)

    async def _modifypoints(self, command: Command) -> None:
        """!modifypoints NAME POINTS (negative ones are taken away), for bot admins and up"""
        if command.level < Level.ADMIN or (api := self._points_api(command)) is None:
            return
        parts = command.param.split()
        if (
            len(parts) != 2
            or NICK.fullmatch(parts[0]) is None
            or re.fullmatch(r"[+-]?\d{1,7}", parts[1]) is None
            or not 0 < abs(int(parts[1])) <= MAX_CHANGE
        ):
            sign = html.escape(self.config.command_sign)
            self.reply(command, f"So geht das: {sign}modifypoints NAME PUNKTE (z. B. 5 oder -5)")
            return
        name, change = parts[0], int(parts[1])
        points, url = api
        try:
            totals = await points.add(url, {name: change})
        except PointsError as e:
            self._points_failed(command, e)
            return
        done = "gutgeschrieben" if change > 0 else "abgezogen"
        total = totals.get(name, 0)
        self.reply(
            command, f"Dem Punktekonto von {name} wurden {_points(abs(change))} {done}! Neuer Stand: {_points(total)}."
        )

    async def _resetpoints(self, command: Command) -> None:
        """!resetpoints, for the master only"""
        if command.level < Level.MASTER or (api := self._points_api(command)) is None:
            return
        points, url = api
        try:
            await points.reset(url)
        except PointsError as e:
            self._points_failed(command, e)
            return
        self.reply(command, "Die Punkte wurden zurückgesetzt!")

    # -- greetings and KI

    def _greet(self, user: str, guest: bool, back: bool = False) -> None:
        """As the Perl bot: on logins, on entering from another room, and when back from away (wb,
        at once). "Welcome back" for those greeted within WELCOME_BACK, else hello."""
        if self._is_me(user) or self.level(user, guest) == Level.IGNORED:
            return
        if guest and self.config.guest_mode == GuestMode.IGNORE:
            return
        now = self._clock()
        name = user.casefold()
        greeting = self.config.custom_greetings.get(name, self.config.greeting)
        last = self._seen.get(name)
        recently = last is not None and now - last <= WELCOME_BACK
        template = greeting.wb if back or recently else greeting.hello
        self._seen[name] = now
        if template.strip():
            text = render(template, chat_values={"user": user}, rng=self._rng)
            self._greetings.append((now if back else now + GREETING_DELAY, text))

    def _patterns(self, event: Message | Whisper, level: Level) -> None:
        """The KI: reactions to messages that contain a text or match a regex."""
        if isinstance(event, Whisper) and not self.config.ki_whisper:
            return
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
            return  # only the first matching entry reacts, as in the Perl bot

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


def _points(number: int) -> str:
    return "1 Punkt" if number == 1 else f"{number} Punkte"


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
    """The groups ($0 is the whole match) if the entry matches text, else None.

    A regex that takes longer than KI_TIMEOUT counts as no match: managers can enter patterns like
    (a|aa)+$, which would otherwise block the bot for minutes on an unlucky message.
    """
    if entry.regex is None:
        return {0: entry.pattern} if _contains_word(text, entry.pattern) else None
    try:
        found = entry.regex.search(text, timeout=KI_TIMEOUT)
    except TimeoutError:
        logger.warning("KI-Eintrag %r war zu langsam und wurde übersprungen", entry.pattern)
        return None
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
    "zitat": Bot._quote,
    "suche": Bot._search,
    "bid": Bot._bid,
    "solution": Bot._solution,
    "ignore": Bot._ignore,
    "unignore": Bot._unignore,
    "cheat": Bot._cheat,
    "hangman": Bot._hangman,
    "wordmix": Bot._wordmix,
    "quiz": Bot._quiz,
    "zeitbombe": Bot._timebomb,
    "schneide": Bot._cut,
    "kartoffel": Bot._potato,
    "wurf": Bot._throw,
    "mypoints": Bot._mypoints,
    "toplist": Bot._toplist,
    "pointson": Bot._points_on,
    "pointsoff": Bot._points_off,
    "modifypoints": Bot._modifypoints,
    "resetpoints": Bot._resetpoints,
}
