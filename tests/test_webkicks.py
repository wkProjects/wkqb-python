import contextlib
from typing import Any

import aiohttp
import pytest

from fake_webkicks import FakeWebkicks
from wkqb.webkicks import ChatUrl, Credentials, Webkicks, WebkicksError


class Events(list[tuple[str, dict[str, Any]]]):
    def __call__(self, name: str, fields: dict[str, Any]) -> None:
        self.append((name, fields))


def test_chat_url() -> None:
    chat = ChatUrl.parse("https://server3.webkicks.de/mychat/")
    assert chat == ChatUrl("https://server3.webkicks.de", "mychat")
    assert chat.url == "https://server3.webkicks.de/mychat"
    assert ChatUrl.parse(" https://server3.webkicks.de/mychat ") == chat


@pytest.mark.parametrize(
    "url",
    ["", "server3.webkicks.de/mychat", "https://server3.webkicks.de/", "https://server3.webkicks.de/a/b", "ftp://x/y"],
)
def test_chat_url_invalid(url: str) -> None:
    with pytest.raises(ValueError):
        ChatUrl.parse(url)


def test_credentials_repr_hides_the_password() -> None:
    assert "geheim" not in repr(Credentials("bot", "geheim123"))


async def test_session_flow(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    events = Events()
    client = Webkicks(session, chat, credentials, events)

    assert await client.fetch_sid() == "88AbCdEfGhIjK"
    assert fake.sid_requests == ["testbot"]  # lower case, like the bot always did
    await client.login()
    assert fake.logins == ["TestBot"]
    url = await client.stream_url()
    assert url.endswith("/stream/0")

    # queued before the stream opens, sent once it does
    fake.push("<!-- update //-->")
    fake.push("Hallo Grüße")
    fake.close_stream()
    chunks = []
    async with contextlib.aclosing(client.stream(url, read_timeout=5)) as stream:
        async for chunk in stream:
            chunks.append(chunk)
    assert b"".join(chunks) == "<!-- update //-->Hallo Grüße".encode()

    await client.keep_alive()
    assert fake.keepalives[0].startswith(f"/{chat.cid}/tok/testbot/88AbCdEfGhIjK/")
    await client.send("Hallo Welt")
    await client.logout()
    assert fake.sent == [("TestBot", "Hallo Welt"), ("TestBot", "/exit")]

    names = [name for name, _ in events]
    assert names[:4] == ["http", "http", "http", "stream_open"]
    assert all(name == "chunk" for name in names[4:-3])
    # neither the password (in the login answer) nor the sid (answer of get_sid, URLs) are traced
    forms = [fields["form"] for name, fields in events if "form" in fields]
    assert all(form.get("pass") in (None, "<redacted>") for form in forms)
    assert "geheim123" not in str(events) and "88AbCdEfGhIjK" not in str(events)
    assert '{"sid": "<sid>"}' in str(events)


async def test_relative_stream_url(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    fake.relative_stream_url = True
    client = Webkicks(session, chat, credentials)
    await client.fetch_sid()
    assert await client.stream_url() == f"{chat.server}/stream/0"


async def test_wrong_password(session: aiohttp.ClientSession, chat: ChatUrl) -> None:
    client = Webkicks(session, chat, Credentials("TestBot", "falsch"))
    with pytest.raises(WebkicksError, match="no sid"):
        await client.fetch_sid()
    assert client.sid is None


async def test_http_error_is_traced(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    events = Events()
    client = Webkicks(session, chat, credentials, events)
    fake.fail["sid"] = 503
    with pytest.raises(WebkicksError, match="HTTP 503"):
        await client.fetch_sid()
    assert events[-1][1]["status"] == 503
    assert events[-1][1]["body"] == "Fehler"


async def test_connection_error_is_traced(session: aiohttp.ClientSession, credentials: Credentials) -> None:
    events = Events()
    client = Webkicks(session, ChatUrl("http://127.0.0.1:1", "x"), credentials, events)
    with pytest.raises(aiohttp.ClientError):
        await client.fetch_sid()
    assert "ClientConnectorError" in events[-1][1]["error"]


async def test_stream_read_timeout(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    client = Webkicks(session, chat, credentials)
    await client.fetch_sid()
    url = await client.stream_url()
    with pytest.raises(TimeoutError):
        async with contextlib.aclosing(client.stream(url, read_timeout=0.2)) as stream:
            async for _ in stream:
                pass


async def test_stream_http_error(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    client = Webkicks(session, chat, credentials)
    await client.fetch_sid()
    url = await client.stream_url()
    fake.fail["stream"] = 500
    with pytest.raises(WebkicksError, match="HTTP 500"):
        async with contextlib.aclosing(client.stream(url, read_timeout=1)) as stream:
            async for _ in stream:
                pass


async def test_methods_need_a_sid(session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials) -> None:
    with pytest.raises(RuntimeError):
        await Webkicks(session, chat, credentials).send("x")


async def test_stream_chunks_are_redacted(
    fake: FakeWebkicks, session: aiohttp.ClientSession, chat: ChatUrl, credentials: Credentials
) -> None:
    events = Events()
    client = Webkicks(session, chat, credentials, events)
    await client.fetch_sid()
    url = await client.stream_url()
    fake.push("sid=88AbCdEfGhIjK")
    fake.close_stream()
    async with contextlib.aclosing(client.stream(url, read_timeout=5)) as stream:
        assert b"".join([chunk async for chunk in stream]) == b"sid=88AbCdEfGhIjK"  # the bot gets the real data
    assert b"".join(f["data"] for name, f in events if name == "chunk") == b"sid=<sid>"
