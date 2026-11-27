import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from fake_webkicks import FakeWebkicks
from wkqb import tracer
from wkqb.recorder import Recorder
from wkqb.tracer import Tracer, read_env_file, settings
from wkqb.webkicks import ChatUrl, Credentials


def events(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for file in sorted(directory.glob("*/*.jsonl")) for line in file.read_text().splitlines()]


def recorder(directory: Path, credentials: Credentials) -> Recorder:
    rec = Recorder(directory, credentials.username)
    rec.redact(credentials.password, "<password>")
    return rec


@contextlib.asynccontextmanager
async def running(t: Tracer) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(t.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def until(condition: Any, within: float = 5.0) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.01)


def make(
    session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, out: Path, **options: float
) -> Tracer:
    options = {"retry_min": 0.01, "retry_max": 0.05, **options}
    return Tracer(session, chat, credentials, recorder(out, credentials), **options)


async def test_records_the_stream_and_logs_in_again_when_it_ends(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path)
    async with running(t):
        await fake.wait_for_stream(1)
        fake.push("<!-- update //-->")
        fake.push("<b>Hallo</b>")
        fake.close_stream()
        await fake.wait_for_stream(2)
        fake.push("zweiter Stream")
        await until(lambda: any(e.get("data", {}).get("text") == "zweiter Stream" for e in events(tmp_path)))

    assert fake.logins == ["TestBot", "TestBot"]
    recorded = events(tmp_path)
    # TCP may merge or split the pushes, so only the stream per connection is fixed
    streams: dict[int, str] = {}
    for e in recorded:
        if e["event"] == "chunk":
            streams[e["conn"]] = streams.get(e["conn"], "") + e["data"]["text"]
    assert streams == {1: "<!-- update //--><b>Hallo</b>", 2: "zweiter Stream"}
    end = next(e for e in recorded if e["event"] == "stream_end")
    assert end["conn"] == 1 and end["reason"] == "closed by Webkicks"
    assert any(e["event"] == "retry" for e in recorded)

    # neither the password (in the login answer) nor the sid end up in the trace
    text = json.dumps(recorded)
    assert "geheim123" not in text and "88AbCdEfGhIjK" not in text and "StReAmToKeN" not in text
    assert "<password>" in text and "<sid>" in text and "<stream>" in text


async def test_survives_errors_after_the_first_stream(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path)
    async with running(t) as task:
        await fake.wait_for_stream(1)
        fake.push("x")
        await until(lambda: t.streams == 1)
        # the nightly maintenance: the stream ends and Webkicks is gone for a moment
        fake.fail.update({"sid": 503, "login": 500})
        fake.close_stream()
        await fake.wait_for_stream(2)
        assert not task.done()

    errors = [e for e in events(tmp_path) if e["event"] == "error"]
    assert [e["message"] for e in errors] == [
        f"POST {chat.url}/api: HTTP 503",
        f"POST {chat.url}/: HTTP 500",
    ]
    assert "Traceback" in errors[0]["traceback"]


async def test_read_timeout_ends_the_stream(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path, read_timeout=0.2)
    async with running(t):
        await fake.wait_for_stream(1)
        fake.push("x")
        await fake.wait_for_stream(2)
    end = next(e for e in events(tmp_path) if e["event"] == "stream_end")
    assert end["reason"] == "nothing received for 0.2 seconds"


async def test_keep_alive(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path, keepalive_interval=0.05)
    async with running(t) as task:
        await fake.wait_for_stream(1)
        fake.fail["tok"] = 500  # a failed keep-alive is recorded, nothing else
        await until(lambda: len(fake.keepalives) >= 2)
        assert not task.done()
    recorded = events(tmp_path)
    assert any(e["event"] == "error" and e.get("during") == "keep-alive" for e in recorded)
    assert fake.streams_opened == 1


async def test_first_connection_failure_ends_it(session: aiohttp.ClientSession, chat: ChatUrl, tmp_path: Path) -> None:
    t = make(session, chat, Credentials("TestBot", "falsch12"), tmp_path)
    with pytest.raises(Exception, match="no sid"):
        await t.run()


async def test_logout(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path)
    await t.logout()  # not logged in yet: nothing to do
    async with running(t):
        await fake.wait_for_stream(1)
    await t.logout()
    assert fake.sent == [("TestBot", "/exit")]
    fake.fail["send"] = 500
    await t.logout()  # recorded, doesn't raise
    assert events(tmp_path)[-1]["during"] == "logout"


async def test_send(
    fake: FakeWebkicks, chat: ChatUrl, credentials: Credentials, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await tracer.send(chat, credentials, recorder(tmp_path, credentials), "x") == 1
    assert "not logged in" in capsys.readouterr().err

    fake.online.add("testbot")  # "run" is logged in
    assert await tracer.send(chat, credentials, recorder(tmp_path, credentials), "Hallo Welt") == 0
    assert fake.sent == [("TestBot", "Hallo Welt")]
    assert {e["conn"] for e in events(tmp_path)} == {"send"}

    fake.fail["send"] = 500
    assert await tracer.send(chat, credentials, recorder(tmp_path, credentials), "x") == 1
    assert events(tmp_path)[-1]["event"] == "error"


async def test_trace_returns_1_if_the_first_connection_fails(
    chat: ChatUrl, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials = Credentials("TestBot", "falsch12")
    assert await tracer.trace(chat, credentials, recorder(tmp_path, credentials)) == 1
    assert "no sid" in capsys.readouterr().err


def test_read_env_file(tmp_path: Path) -> None:
    file = tmp_path / ".env"
    file.write_text(
        "# Test-Account\n\nWK_CHATURL=https://server1.webkicks.de/test/\n"
        "WK_USERNAME = 'Bot A'\nWK_PASSWORD=\"a=b#c\"\nkaputt\n",
        encoding="utf-8",
    )
    env = read_env_file(file)
    assert env == {"WK_CHATURL": "https://server1.webkicks.de/test/", "WK_USERNAME": "Bot A", "WK_PASSWORD": "a=b#c"}
    chat, credentials = settings(env)
    assert chat.cid == "test" and credentials == Credentials("Bot A", "a=b#c")


def test_main_needs_credentials(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WK_CHATURL", "WK_USERNAME", "WK_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        tracer.main(["run"])
    assert exit_info.value.code == 2
    assert "missing: WK_CHATURL, WK_USERNAME, WK_PASSWORD" in capsys.readouterr().err


async def test_stops_when_the_account_logs_in_elsewhere(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials, tmp_path: Path
) -> None:
    t = make(session, chat, credentials, tmp_path)
    task = asyncio.create_task(t.run())
    await fake.wait_for_stream(1)
    fake.push("<!-- update //-->\n")
    # split across two chunks
    fake.push("<font color=red>Connection Closed (multiple ")
    await until(lambda: t.streams == 1)
    fake.push("Login detected)</font><br>\n")
    fake.close_stream()
    async with asyncio.timeout(5):
        await task  # ends without an error
    assert fake.streams_opened == 1
    assert events(tmp_path)[-1]["reason"] == "logged in elsewhere"
    await t.logout()
    assert fake.sent == []  # the other session must not be logged out
