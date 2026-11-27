"""The queue of chat messages the bot sends, paced for the flood protection of Webkicks.

Normal accounts are warned when the 4th message follows three gaps of 1.5 s or less, and kicked at
the third warning (see docs/webkicks-protocol.md). The outbox keeps INTERVAL between two messages.
It outlives the chat sessions: messages queued while the bot reconnects are sent afterwards, unless
they waited longer than MAX_AGE (a greeting minutes later would be odd).
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from wkqb.webkicks import NotLoggedIn

logger = logging.getLogger(__name__)

INTERVAL = 2.5
MAX_AGE = 60.0
#: More waiting messages are dropped; at INTERVAL, 20 messages take almost a minute
MAX_QUEUED = 20


@dataclass(frozen=True, slots=True)
class _Queued:
    text: str
    since: float


class Outbox:
    def __init__(self, *, interval: float = INTERVAL, max_age: float = MAX_AGE, max_queued: int = MAX_QUEUED) -> None:
        self._interval = interval
        self._max_age = max_age
        self._max_queued = max_queued
        self._queue: deque[_Queued] = deque()
        self._waiting = asyncio.Event()
        self._last_sent = float("-inf")

    def put(self, *texts: str) -> None:
        """Queues chat lines; empty ones are skipped."""
        for text in texts:
            if not text.strip():
                continue
            if len(self._queue) >= self._max_queued:
                logger.warning("Ausgabe-Warteschlange voll, Nachricht verworfen: %.60s", text)
                continue
            self._queue.append(_Queued(text, time.monotonic()))
        if self._queue:
            self._waiting.set()

    def __len__(self) -> int:
        return len(self._queue)

    async def run(self, send: Callable[[str], Awaitable[None]]) -> None:
        """Sends the queued messages until cancelled (at the end of a chat session).

        NotLoggedIn ends it, as the session is gone; the message is kept for the next session.
        Other errors of send() are logged and the message is dropped.
        """
        while True:
            await self._waiting.wait()
            pause = self._last_sent + self._interval - time.monotonic()
            if pause > 0:
                await asyncio.sleep(pause)
            if not self._queue:
                self._waiting.clear()
                continue
            message = self._queue[0]
            if time.monotonic() - message.since > self._max_age:
                self._queue.popleft()
                logger.info("Nachricht zu lange in der Warteschlange, verworfen: %.60s", message.text)
                continue
            try:
                await send(message.text)
            except NotLoggedIn:
                raise
            except Exception as e:
                logger.warning("Nachricht nicht gesendet (%s: %s): %.60s", type(e).__name__, e, message.text)
            self._queue.popleft()
            self._last_sent = time.monotonic()
            if not self._queue:
                self._waiting.clear()
