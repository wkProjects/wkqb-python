"""A fake Webkicks chat server for the tests, modelled on what the bot sends and expects."""

import asyncio
from dataclasses import dataclass, field

from aiohttp import web

CID = "testchat"
#: Followed by the number of the stream
STREAM_TOKEN = "StReAmToKeN"


@dataclass
class Account:
    password: str
    sid: str


@dataclass
class FakeWebkicks:
    accounts: dict[str, Account] = field(
        default_factory=lambda: {"testbot": Account("geheim123", "88AbCdEfGhIjK")},
    )
    logins: list[str] = field(default_factory=list)
    """User names of the logins, as sent"""
    online: set[str] = field(default_factory=set)
    """Lower-case names of the logged in accounts"""
    sid_requests: list[str] = field(default_factory=list)
    keepalives: list[str] = field(default_factory=list)
    """Paths of the keep-alive requests"""
    sent: list[tuple[str, str]] = field(default_factory=list)
    """(user, message) of the chat messages"""
    fail: dict[str, int] = field(default_factory=dict)
    """Status to answer with, by step ("sid", "login", "frame", "stream", "tok", "send"); used once"""
    disconnect: set[str] = field(default_factory=set)
    """Steps whose next request is answered by closing the connection (once)"""
    relative_stream_url: bool = False
    streams_opened: int = 0
    _queues: list[asyncio.Queue[bytes | None]] = field(default_factory=list)
    stream_connected: asyncio.Event = field(default_factory=asyncio.Event)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(f"/{CID}/api", self._api)
        app.router.add_post(f"/{CID}/", self._login)
        app.router.add_get(f"/{CID}/chatstream/{{user}}/{{sid}}/start", self._frame)
        app.router.add_get(f"/{CID}/chatstream/{{user}}/{{token}}/stream", self._stream)
        app.router.add_get(f"/{CID}/tok/{{user}}/{{sid}}/{{millis}}", self._tok)
        app.router.add_post("/cgi-bin/chat.cgi", self._send)
        return app

    def push(self, data: str | bytes) -> None:
        """Sends data on the current stream."""
        self._queues[-1].put_nowait(data.encode() if isinstance(data, str) else data)

    def close_stream(self) -> None:
        self._queues[-1].put_nowait(None)

    async def wait_for_stream(self, number: int, within: float = 5.0) -> None:
        """Waits until the number-th stream is open."""
        async with asyncio.timeout(within):
            while self.streams_opened < number:
                self.stream_connected.clear()
                await self.stream_connected.wait()

    def _failure(self, step: str) -> web.Response | None:
        status = self.fail.pop(step, None)
        return None if status is None else web.Response(status=status, text="Fehler")

    def _account(self, user: str, secret: str, by_sid: bool) -> Account | None:
        account = self.accounts.get(user.lower())
        if account is None or secret != (account.sid if by_sid else account.password):
            return None
        return account

    def _drop(self, request: web.Request, step: str) -> bool:
        if step not in self.disconnect:
            return False
        self.disconnect.discard(step)
        assert request.transport is not None
        request.transport.close()
        return True

    async def _api(self, request: web.Request) -> web.Response:
        form = await request.post()
        if self._drop(request, "sid"):
            return web.Response()
        if failure := self._failure("sid"):
            return failure
        user, password = str(form.get("user", "")), str(form.get("pass", ""))
        self.sid_requests.append(user)
        account = self._account(user, password, by_sid=False)
        if form.get("job") != "get_sid" or form.get("cid") != CID or account is None:
            return web.json_response({"error": "wrong credentials"})
        return web.json_response({"sid": account.sid})

    async def _login(self, request: web.Request) -> web.Response:
        form = await request.post()
        if failure := self._failure("login"):
            return failure
        user = str(form.get("user", ""))
        self.logins.append(user)
        account = self._account(user, str(form.get("pass", "")), by_sid=False)
        if account is None:
            return web.Response(text="Falsches Passwort", content_type="text/html")
        self.online.add(user.lower())
        # the real chat page has the credentials in it, too
        return web.Response(
            text=f'<frameset><frame src="/{CID}/chatstream/{user}/{account.sid}/start">'
            f'<input type="hidden" name="pass" value="{account.password}"></frameset>',
            content_type="text/html",
        )

    async def _frame(self, request: web.Request) -> web.Response:
        if failure := self._failure("frame"):
            return failure
        if self._account(request.match_info["user"], request.match_info["sid"], by_sid=True) is None:
            return web.Response(status=403, text="Zugriff verweigert")
        n = len(self._queues)
        self._queues.append(asyncio.Queue())
        path = f"/{CID}/chatstream/{request.match_info['user'].lower()}/{STREAM_TOKEN}{n}/stream"
        url = path if self.relative_stream_url else f"http://{request.host}{path}"
        return web.Response(text=f'<meta http-equiv="refresh" content="0; URL={url}">', content_type="text/html")

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        if failure := self._failure("stream"):
            return failure
        queue = self._queues[int(request.match_info["token"].removeprefix(STREAM_TOKEN))]
        response = web.StreamResponse(headers={"Content-Type": "text/html; charset=utf-8"})
        await response.prepare(request)
        self.streams_opened += 1
        self.stream_connected.set()
        while (data := await queue.get()) is not None:
            await response.write(data)
        await response.write_eof()
        return response

    async def _tok(self, request: web.Request) -> web.Response:
        if failure := self._failure("tok"):
            return failure
        self.keepalives.append(request.path)
        return web.Response(text="")

    async def _send(self, request: web.Request) -> web.Response:
        form = await request.post()
        if failure := self._failure("send"):
            return failure
        user = str(form.get("user", ""))
        if self._account(user, str(form.get("pass", "")), by_sid=True) is None or form.get("cid") != CID:
            return web.Response(status=403, text="Zugriff verweigert")
        if user.lower() not in self.online:
            # as recorded from Webkicks
            return web.Response(
                text="<html><title>Fehler</title><body>Fehler: Timeout. Bitte neu einloggen.</body></html>",
                content_type="text/html",
            )
        message = str(form.get("message", ""))
        self.sent.append((user, message))
        if message == "/exit":
            self.online.discard(user.lower())
            return web.Response(text="<i>bye, <b>" + user + "</b></i>", content_type="text/html")
        return web.Response(status=204)
