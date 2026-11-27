"""Runs the bot: loads the settings, logs in, reads the stream, and logs in again when it ends.

The pod is never restarted by Kubernetes, and wkqb-web reads exit code 0 as "stopped on purpose",
so the bot only ends with 0 on !quit, SIGTERM/SIGINT or when its account logs in elsewhere. Lost
sessions (the nightly maintenance, timeouts, network errors) are handled here by logging in again.
Chat lines are only logged at DEBUG (wkqb-web's debug mode, see its privacy policy).
Exit code 1 means it gave up: no working chat session for GIVE_UP_AFTER, e.g. after a password
change, so wkqb-web's check of the credentials decides about a restart.
"""

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import Coroutine, Mapping
from typing import Any

import aiohttp

from wkqb import config as settings
from wkqb.bot import Bot
from wkqb.config import Config, ConfigError
from wkqb.events import ChatStarted, KeepAlive, Logout, TakenOver, TimeoutWarning, parse_line
from wkqb.outbox import INTERVAL, Outbox
from wkqb.redaction import redact, secrets
from wkqb.stream import LineSplitter
from wkqb.webkicks import ChatUrl, Credentials, Webkicks

logger = logging.getLogger(__name__)

#: Webkicks sends a keep-alive every 60 seconds; three missing ones mean the stream is dead
READ_TIMEOUT = 180.0
KEEPALIVE_INTERVAL = 600.0
RETRY_MIN = 5.0
RETRY_MAX = 300.0
#: A session that lasted this long resets the waiting time
STABLE_AFTER = 300.0
GIVE_UP_AFTER = 1800.0
#: Time for the last messages after !quit
QUIT_FLUSH = 30.0
LOGOUT_TIMEOUT = 10.0


class End(enum.Enum):
    QUIT = "!quit"
    STOP = "signal"
    TAKEN_OVER = "account logged in elsewhere"
    LOST = "session lost"


class Runner:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        chat: ChatUrl,
        credentials: Credentials,
        env: Mapping[str, str],
        *,
        read_timeout: float = READ_TIMEOUT,
        keepalive_interval: float = KEEPALIVE_INTERVAL,
        retry_min: float = RETRY_MIN,
        retry_max: float = RETRY_MAX,
        give_up_after: float = GIVE_UP_AFTER,
        send_interval: float = INTERVAL,
        tick_interval: float = 1.0,
    ) -> None:
        self._http = session
        self._chat = chat
        self._credentials = credentials
        self._env = env
        self._read_timeout = read_timeout
        self._keepalive_interval = keepalive_interval
        self._retry_min = retry_min
        self._retry_max = retry_max
        self._give_up_after = give_up_after
        self._tick_interval = tick_interval
        self.outbox = Outbox(interval=send_interval)
        self.bot: Bot | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        """Background work, referenced so it isn't garbage-collected"""
        self._last_chat = time.monotonic()
        """When a stream last delivered the chat"""

    async def run(self, stop: asyncio.Event) -> int:
        """Runs until stop is set or the bot ends; returns the exit code."""
        config = await self._first_config(stop)
        if config is None:
            return 0
        bot = self.bot = Bot(
            self._credentials.username, config, self.outbox, self._load_config, change_ignored=self._change_ignored
        )
        ticker = asyncio.create_task(self._ticker(bot))
        try:
            return await self._sessions(bot, stop)
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker

    async def _sessions(self, bot: Bot, stop: asyncio.Event) -> int:
        delay = self._retry_min
        self._last_chat = time.monotonic()
        while True:
            started = time.monotonic()
            try:
                end = await self._session(bot, stop)
            except Exception as e:
                logger.warning("Chat-Sitzung beendet: %s: %s", type(e).__name__, e)
                end = End.LOST
            if end is not End.LOST:
                logger.info("Bot beendet (%s)", end.value)
                return 0
            if time.monotonic() - self._last_chat > self._give_up_after:
                logger.error("Seit %d Minuten keine Verbindung zum Chat, Bot gibt auf", self._give_up_after // 60)
                return 1
            if time.monotonic() - started >= STABLE_AFTER:
                delay = self._retry_min
            logger.info("Neuer Login in %g Sekunden", delay)
            if await _wait(stop, delay):
                return 0
            delay = min(delay * 2, self._retry_max)

    async def _session(self, bot: Bot, stop: asyncio.Event) -> End:
        client = Webkicks(self._http, self._chat, self._credentials)
        await client.fetch_sid()
        await client.login()
        url = await client.stream_url()
        logger.info("Eingeloggt in %s", self._chat.url)

        reader = asyncio.create_task(self._read(client, url, bot))
        sender = asyncio.create_task(self.outbox.run(client.send))
        keepalive = asyncio.create_task(self._keep_alive(client))
        stopped = asyncio.create_task(stop.wait())
        quitting = asyncio.create_task(bot.quit.wait())
        tasks = {reader, sender, keepalive, stopped, quitting}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if quitting.done():
                await _flush(self.outbox, QUIT_FLUSH)
                await _logout(client)
                return End.QUIT
            if stopped.done():
                await _logout(client)
                return End.STOP
            if reader.done():
                return reader.result()  # raises the stream's error
            if sender.done():
                sender.result()  # NotLoggedIn
            return End.LOST
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read(self, client: Webkicks, url: str, bot: Bot) -> End:
        splitter = LineSplitter()
        chat_started = False
        async with contextlib.aclosing(client.stream(url, self._read_timeout)) as stream:
            async for chunk in stream:
                for line in splitter.feed(chunk):
                    event = parse_line(line)
                    if isinstance(event, TakenOver):
                        return End.TAKEN_OVER
                    if isinstance(event, ChatStarted):
                        chat_started = True
                        self._last_chat = time.monotonic()
                        bot.connected()
                        continue
                    if not chat_started:
                        continue  # the HTML header
                    self._last_chat = time.monotonic()
                    if logger.isEnabledFor(logging.DEBUG) and not isinstance(event, KeepAlive):
                        found = secrets(sid=client.sid, stream=client.stream_token)
                        logger.debug("< %s", redact(line, found))
                    if isinstance(event, TimeoutWarning):
                        # as the Perl bot did: activity right away, not only at the next keep-alive
                        logger.info("Webkicks warnt vor dem Timeout, sende Keep-Alive")
                        self._background(client.keep_alive())
                    if isinstance(event, Logout) and event.user.casefold() == bot.name.casefold():
                        logger.info("Der Bot hat den Chat verlassen%s", " (Timeout)" if event.timed_out else "")
                    try:
                        await bot.handle(event)
                    except Exception:
                        # without the event: chat texts are only logged at DEBUG (privacy policy)
                        logger.exception("Fehler bei %s", type(event).__name__)
                        logger.debug("Fehler bei %r", event)
        logger.info("Webkicks hat den Stream geschlossen")
        return End.LOST

    def _background(self, work: Coroutine[Any, Any, None]) -> None:
        """Runs work without waiting for it; errors are logged."""

        async def run() -> None:
            try:
                await work
            except Exception as e:
                logger.warning("Keep-Alive fehlgeschlagen: %s: %s", type(e).__name__, e)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _keep_alive(self, client: Webkicks) -> None:
        while True:
            await asyncio.sleep(self._keepalive_interval)
            try:
                await client.keep_alive()
            except Exception as e:
                logger.warning("Keep-Alive fehlgeschlagen: %s: %s", type(e).__name__, e)

    async def _ticker(self, bot: Bot) -> None:
        while True:
            await asyncio.sleep(self._tick_interval)
            try:
                bot.tick()
            except Exception:
                logger.exception("Fehler bei geplanten Nachrichten")

    async def _first_config(self, stop: asyncio.Event) -> Config | None:
        """The settings; while the web interface isn't reachable, it tries again. None if stopped."""
        delay = self._retry_min
        while True:
            try:
                config, _ = await self._load_config()
                return config
            except ConfigError as e:
                logger.error("Einstellungen konnten nicht geladen werden: %s; neuer Versuch in %g Sekunden", e, delay)
            if await _wait(stop, delay):
                return None
            delay = min(delay * 2, self._retry_max)

    async def _change_ignored(self, action: str, name: str) -> list[str]:
        url = self.bot.config.ignored_api if self.bot is not None else None
        if url is None:
            raise ConfigError("wkqb-web gave no address for the ignore list")
        return await settings.change_ignored(self._http, url, action, name)

    async def _load_config(self) -> tuple[Config, list[str]]:
        config, warnings = await settings.load(self._http, self._env)
        for warning in warnings:
            logger.warning("Einstellungen: %s", warning)
        return config, warnings


async def _wait(stop: asyncio.Event, seconds: float) -> bool:
    """Waits; True if stop was set meanwhile."""
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
            return True
    except TimeoutError:
        return False


async def _flush(outbox: Outbox, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            await outbox.empty()


async def _logout(client: Webkicks) -> None:
    try:
        async with asyncio.timeout(LOGOUT_TIMEOUT):
            await client.logout()
    except Exception as e:
        logger.warning("Logout fehlgeschlagen: %s: %s", type(e).__name__, e)
