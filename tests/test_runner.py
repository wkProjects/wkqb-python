import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from fake_webkicks import CHAT_STARTED, HEADER, KEEP_ALIVE, TAKEN_OVER, FakeWebkicks, message, whisper
from wkqb.runner import Runner
from wkqb.webkicks import ChatUrl, Credentials

CONFIG = {"jointext": "Bin da!", "users": {"master": "Anna", "admins": ["Bert"]}, "quote": {"quotes": ["Q"]}}


@pytest.fixture
def runner_factory(
    fake: FakeWebkicks, server: TestServer, chat: ChatUrl, credentials: Credentials, session: aiohttp.ClientSession
) -> Callable[..., Runner]:
    fake.config = CONFIG
    env = {"WKQB_CONFIG_URL": str(server.make_url("/api/config.php?b_id=abcdef1234"))}

    def make(**options: Any) -> Runner:
        timing = {"retry_min": 0.01, "retry_max": 0.05, "send_interval": 0.01, "tick_interval": 0.01, **options}
        return Runner(session, chat, credentials, env, **timing)

    return make


class Running:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.stop = asyncio.Event()
        self.task: asyncio.Task[int] | None = None

    async def result(self, within: float = 5.0) -> int:
        assert self.task is not None
        async with asyncio.timeout(within):
            return await self.task


@contextlib.asynccontextmanager
async def running(runner: Runner) -> AsyncIterator[Running]:
    handle = Running(runner)
    handle.task = asyncio.create_task(runner.run(handle.stop))
    try:
        yield handle
    finally:
        if not handle.task.done():
            handle.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handle.task


async def until(condition: Callable[[], bool], within: float = 5.0) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.005)


async def chat_started(fake: FakeWebkicks, stream: int = 1) -> None:
    await fake.wait_for_stream(stream)
    fake.push(HEADER + CHAT_STARTED)


async def test_answers_and_stops_on_a_signal(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    async with running(runner_factory()) as bot:
        await chat_started(fake)
        await until(lambda: fake.messages == ["Bin da! (wkQB 6.0, https://wkqb.de)"])
        # header lines before the start are not messages
        fake.push(message("Dora", "!ping") + KEEP_ALIVE + whisper("Dora", "!ping"))
        await until(lambda: len(fake.messages) == 3)
        bot.stop.set()
        assert await bot.result() == 0
    assert fake.messages[1:] == ["Pong!", "/f Dora Pong!", "/exit"]


async def test_logs_in_again_when_the_stream_ends(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    async with running(runner_factory()) as bot:
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 1)
        fake.close_stream()
        await chat_started(fake, 2)
        fake.push(message("Dora", "!ping"))
        await until(lambda: len(fake.messages) == 2)
        assert not bot.task.done()  # type: ignore[union-attr]
    assert fake.logins == ["TestBot", "TestBot"]
    assert fake.messages == ["Bin da! (wkQB 6.0, https://wkqb.de)", "Pong!"]  # hello only once


async def test_logs_in_again_after_the_nightly_maintenance(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    async with running(runner_factory()):
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 1)
        fake.abort_stream()  # recorded 2026-11-28: a second after the announcement, mid-response
        await chat_started(fake, 2)
        fake.push(message("Dora", "!ping"))
        await until(lambda: len(fake.messages) == 2)
    assert "Webkicks hat den Stream abgebrochen" in caplog.text
    assert "Chat-Sitzung beendet" not in caplog.text  # no warning for something that happens every night


async def test_logs_in_again_after_errors(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    fake.fail.update({"sid": 503, "login": 500, "frame": 502})
    async with running(runner_factory()) as bot:
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 1)
        assert not bot.task.done()  # type: ignore[union-attr]


async def test_a_dead_stream_is_noticed(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    async with running(runner_factory(read_timeout=0.2)):
        await chat_started(fake)
        await fake.wait_for_stream(2)


async def test_a_lost_login_is_noticed_when_sending(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    async with running(runner_factory()):
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 1)
        fake.online.clear()  # Webkicks forgot the session, the stream still runs
        fake.push(message("Dora", "!ping"))
        await fake.wait_for_stream(2)
        fake.push(CHAT_STARTED)
        await until(lambda: fake.messages[-1] == "Pong!")  # the answer was kept for the new session


async def test_stops_when_the_account_logs_in_elsewhere(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner]
) -> None:
    async with running(runner_factory()) as bot:
        await chat_started(fake)
        fake.push(TAKEN_OVER)
        assert await bot.result() == 0
    assert fake.logins == ["TestBot"]
    assert "/exit" not in fake.messages  # that would log out the other session


async def test_quit(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    async with running(runner_factory()) as bot:
        await chat_started(fake)
        fake.push(message("Bert", "!quit"))
        assert await bot.result() == 0
    assert fake.messages[1:] == ["Oh je, ich muss wohl gehen :-(", "/exit"]


async def test_errors_in_the_bot_are_logged(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner], caplog: pytest.LogCaptureFixture
) -> None:
    runner = runner_factory()
    async with running(runner):
        await chat_started(fake)
        await until(lambda: runner.bot is not None)
        assert runner.bot is not None
        original = runner.bot.handle

        async def broken(event: Any) -> None:
            if "kaputt" in str(event):
                raise RuntimeError("bug")
            await original(event)

        runner.bot.handle = broken  # type: ignore[method-assign]
        fake.push(message("Dora", "kaputt") + message("Dora", "!ping"))
        await until(lambda: fake.messages[-1:] == ["Pong!"])
    assert "Fehler bei Message" in caplog.text and "RuntimeError: bug" in caplog.text
    assert "kaputt" not in caplog.text  # chat texts only at DEBUG


async def test_debug_logs_the_chat_lines_without_secrets(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with running(runner_factory()):
        await chat_started(fake)
        fake.push(message("Dora", "geheim? 88AbCdEfGhIjK") + KEEP_ALIVE)
        await until(lambda: "Dora" in caplog.text)
    # the bot's records only: the fake server's access log has the sid in its URLs
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("wkqb"))
    assert "geheim? <sid>" in logged
    assert "88AbCdEfGhIjK" not in logged and "update //" not in logged


async def test_waits_for_the_settings(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    fake.fail["config"] = 503
    async with running(runner_factory()):
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 1)


async def test_stop_while_waiting_for_the_settings(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    fake.config = []  # never valid
    async with running(runner_factory(retry_min=10)) as bot:
        await asyncio.sleep(0.05)
        bot.stop.set()
        assert await bot.result() == 0
    assert fake.logins == []


async def test_gives_up_without_a_chat(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner], caplog: pytest.LogCaptureFixture
) -> None:
    fake.accounts["testbot"].password = "geändert"  # e.g. changed in the chat
    async with running(runner_factory(give_up_after=0.2)) as bot:
        assert await bot.result() == 1
    assert "gibt auf" in caplog.text


async def test_scheduled_messages_wait_for_the_session(
    fake: FakeWebkicks, runner_factory: Callable[..., Runner], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    runner = runner_factory()
    async with running(runner):
        await until(lambda: runner.bot is not None)
        assert runner.bot is not None
        runner.bot.say("vorher")  # queued before the chat started
        await chat_started(fake)
        await until(lambda: len(fake.messages) == 2)
    assert fake.messages == ["vorher", "Bin da! (wkQB 6.0, https://wkqb.de)"]


async def test_ignore_goes_to_the_web_interface(
    fake: FakeWebkicks, server: TestServer, runner_factory: Callable[..., Runner]
) -> None:
    fake.config = {**CONFIG, "api": {"ignored": str(server.make_url("/api/ignored.php?b_id=abcdef1234"))}}
    async with running(runner_factory()):
        await chat_started(fake)
        fake.push(message("Bert", "!ignore Dora"))
        await until(lambda: fake.messages[-1:] == ["Dora wird ab sofort ignoriert."])
        fake.push(message("Dora", "!ping") + message("Bert", "!reload") + message("Dora", "!ping"))
        await until(lambda: fake.messages[-1:] == ["Einstellungen neu geladen!"])
        await asyncio.sleep(0.1)
    assert fake.config["users"]["ignored"] == ["Dora"]
    assert "Pong!" not in fake.messages  # ignored, also after the reload


async def test_timeout_warning_sends_a_keep_alive(fake: FakeWebkicks, runner_factory: Callable[..., Runner]) -> None:
    warning = (
        '<table border=0><tr><td valign=bottom><b><FONT COLOR="red">'
        "Ohne weitere Eingabe erfolgt in 2 Minuten dein Timeout.</font></b></td></tr></table>\n"
    )
    async with running(runner_factory()):
        await chat_started(fake)
        assert fake.keepalives == []
        fake.push(warning)
        await until(lambda: len(fake.keepalives) == 1)
