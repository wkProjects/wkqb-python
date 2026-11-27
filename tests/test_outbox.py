import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

import pytest

from wkqb.outbox import Outbox
from wkqb.webkicks import NotLoggedIn


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[float, str]] = []
        self.fail: dict[str, Exception] = {}

    async def send(self, text: str) -> None:
        if (error := self.fail.pop(text, None)) is not None:
            raise error
        self.sent.append((time.monotonic(), text))

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


@contextlib.asynccontextmanager
async def running(outbox: Outbox, chat: Chat) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(outbox.run(chat.send))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def until(condition: object, within: float = 3.0) -> None:
    async with asyncio.timeout(within):
        while not condition():  # type: ignore[operator]
            await asyncio.sleep(0.005)


async def test_keeps_the_interval() -> None:
    outbox, chat = Outbox(interval=0.1), Chat()
    async with running(outbox, chat):
        outbox.put("eins", "zwei", "", "  ", "drei")
        await until(lambda: len(chat.sent) == 3)
        outbox.put("vier")  # right after the last one: it waits for the interval, too
        await until(lambda: len(chat.sent) == 4)
    assert chat.texts == ["eins", "zwei", "drei", "vier"]
    gaps = [b - a for (a, _), (b, _) in zip(chat.sent, chat.sent[1:], strict=False)]
    assert all(gap >= 0.095 for gap in gaps), gaps


async def test_a_message_after_a_pause_is_sent_at_once() -> None:
    outbox, chat = Outbox(interval=0.1), Chat()
    async with running(outbox, chat):
        outbox.put("eins")
        await until(lambda: len(chat.sent) == 1)
        await asyncio.sleep(0.15)
        queued = time.monotonic()
        outbox.put("zwei")
        await until(lambda: len(chat.sent) == 2)
    assert chat.sent[1][0] - queued < 0.05


async def test_full_queue_drops(caplog: pytest.LogCaptureFixture) -> None:
    outbox = Outbox(max_queued=2)
    outbox.put("a", "b", "c")
    assert len(outbox) == 2
    assert "Warteschlange voll" in caplog.text


async def test_old_messages_are_dropped(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    outbox, chat = Outbox(interval=0.01, max_age=0.05), Chat()
    outbox.put("alt")  # e.g. queued while the bot reconnected
    await asyncio.sleep(0.1)
    outbox.put("neu")
    async with running(outbox, chat):
        await until(lambda: len(chat.sent) == 1)
    assert chat.texts == ["neu"]
    assert "zu lange" in caplog.text


async def test_errors_drop_the_message(caplog: pytest.LogCaptureFixture) -> None:
    outbox, chat = Outbox(interval=0.01), Chat()
    chat.fail["kaputt"] = TimeoutError()
    async with running(outbox, chat) as task:
        outbox.put("kaputt", "heil")
        await until(lambda: len(chat.sent) == 1)
        assert not task.done()
    assert chat.texts == ["heil"]
    assert "nicht gesendet (TimeoutError" in caplog.text


async def test_not_logged_in_ends_the_session_and_keeps_the_message() -> None:
    outbox, chat = Outbox(interval=0.01), Chat()
    chat.fail["eins"] = NotLoggedIn("weg")
    outbox.put("eins", "zwei")
    with pytest.raises(NotLoggedIn):
        await outbox.run(chat.send)
    assert len(outbox) == 2
    async with running(outbox, chat):  # the next session
        await until(lambda: len(chat.sent) == 2)
    assert chat.texts == ["eins", "zwei"]
