"""The queue of chat messages the bot sends, paced for the flood protection of Webkicks.

Normal accounts are warned when the 4th message follows three gaps of 1.5 s or less, and kicked at
the third warning (see docs/webkicks-protocol.md). The outbox keeps INTERVAL between two messages.
It outlives the chat sessions: messages queued while the bot reconnects are sent afterwards, unless
they waited longer than MAX_AGE (a greeting minutes later would be odd). Chat texts are only logged
at DEBUG (see the privacy policy of wkqb.de).

Webkicks refuses messages longer than MAX_MESSAGE_BYTES, so longer ones are shortened (fit()).
"""

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from wkqb.webkicks import MAX_MESSAGE_BYTES, NotLoggedIn

logger = logging.getLogger(__name__)

INTERVAL = 2.5
MAX_AGE = 60.0
#: More waiting messages are dropped; at INTERVAL, 20 messages take almost a minute
MAX_QUEUED = 20
ELLIPSIS = "…"
#: A shortened message ends at a space if there is one within this many characters
_WORD_BOUNDARY = 40
#: An HTML tag or entity that the cut would split
_OPEN_TAG = re.compile(r"<[^>]*$")
_OPEN_ENTITY = re.compile(r"&#?\w*$")


def fit(text: str, limit: int = MAX_MESSAGE_BYTES) -> str:
    """The text, shortened to at most `limit` UTF-8 bytes with "…" at the end if it is longer.

    The cut prefers a space, and never splits a character, an HTML tag or an entity (an opened tag
    may stay unclosed: the chat ends it with the line).
    """
    if len(text.encode()) <= limit:
        return text
    budget = limit - len(ELLIPSIS.encode())
    end, size = 0, 0
    for ch in text:
        size += len(ch.encode())
        if size > budget:
            break
        end += 1
    cut = text[:end]
    for unfinished in (_OPEN_TAG, _OPEN_ENTITY):
        if match := unfinished.search(cut):
            cut = cut[: match.start()]
    space = cut.rfind(" ")
    in_word = not text[len(cut)].isspace()
    if in_word and space > 0 and len(cut) - space <= _WORD_BOUNDARY:
        cut = cut[:space]
    return cut.rstrip() + ELLIPSIS


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
        self._empty = asyncio.Event()
        self._empty.set()
        self._last_sent = float("-inf")

    def put(self, *texts: str) -> None:
        """Queues chat lines; empty ones are skipped."""
        for text in texts:
            if not text.strip():
                continue
            if len(self._queue) >= self._max_queued:
                logger.warning("Ausgabe-Warteschlange voll, Nachricht verworfen")
                logger.debug("Verworfen: %s", text)
                continue
            if (size := len(text.encode())) > MAX_MESSAGE_BYTES:
                logger.warning("Nachricht zu lang für Webkicks (%d Bytes), gekürzt", size)
                text = fit(text)
            self._queue.append(_Queued(text, time.monotonic()))
        if self._queue:
            self._waiting.set()
            self._empty.clear()

    def __len__(self) -> int:
        return len(self._queue)

    def pending(self) -> list[str]:
        """The queued texts, oldest first."""
        return [message.text for message in self._queue]

    async def empty(self) -> None:
        """Waits until everything queued was sent (or dropped)."""
        await self._empty.wait()

    def _check_empty(self) -> None:
        if not self._queue:
            self._waiting.clear()
            self._empty.set()

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
                self._check_empty()
                continue
            message = self._queue[0]
            if time.monotonic() - message.since > self._max_age:
                self._queue.popleft()
                logger.info("Nachricht zu lange in der Warteschlange, verworfen")
                logger.debug("Verworfen: %s", message.text)
                self._check_empty()
                continue
            try:
                logger.debug("> %s", message.text)
                await send(message.text)
            except NotLoggedIn:
                raise
            except Exception as e:
                logger.warning("Nachricht nicht gesendet (%s: %s)", type(e).__name__, e)
            self._queue.popleft()
            self._last_sent = time.monotonic()
            self._check_empty()
