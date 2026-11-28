"""A fake Webkicks chat server for the tests, modelled on what the bot sends and expects."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

CID = "testchat"
#: Followed by the number of the stream
STREAM_TOKEN = "StReAmToKeN"

# Stream lines in the markup recorded from Webkicks (see tests/fixtures/lines-*.jsonl)
_ROW = "<table border=0><tr><td valign=bottom>{}</td></tr></table>\n"
_TIME = "<FONT SIZE=-2>(12:34)</FONT> "
CHAT_STARTED = '<!-- update!! //-->\n<div id="soundcontainer"></div>\n'
KEEP_ALIVE = "<!-- update //-->\n"
TAKEN_OVER = "<font color=red>Connection Closed (multiple Login detected)</font><br>\n"
HEADER = '<!DOCTYPE html>\n<html lang="de">\n<body>\npowered by <a href="https://www.webkicks.de">x</a>\n<hr>\n'


def message(user: str, text: str, guest: bool = False) -> str:
    title = f"{user} (Gast)" if guest else user
    return _ROW.format(
        f'{_TIME}<b><font title="{title}"><span onclick="fluester(\'{user}\')"><b>{user}</b></span></b>: {text}</font>'
    )


def whisper(user: str, text: str) -> str:
    return _ROW.format(
        f"{_TIME}<b><span onclick=\"fluester('{user}')\">{user} fl&uuml;stert</span>: <font color=red>{text}</font>"
    )


def login(user: str, guest: bool = False) -> str:
    title = f"{user} (Gast)" if guest else user
    return _ROW.format(
        f'{_TIME}<font title="{title}"><img src="/gruen.gif"><login {user} /><B><i>'
        f"<span onclick=\"fluester('{user}')\"><b>{user}</b></span>"
        '<span class="commandcolor"> betritt den Chat</b></span></i>'
    )


def logout(user: str) -> str:
    return _ROW.format(
        f'{_TIME}<font title="{user}"><img src="/rot.gif"><B><i><span onclick="fluester(\'{user}\')"><b>{user}</b>'
        '</span><span class="commandcolor"> verlässt den Chat</b></span></i></font>'
    )


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
    _abort: bool = False
    stream_connected: asyncio.Event = field(default_factory=asyncio.Event)
    config: Any = field(default_factory=dict)
    """Served as the bot's settings at /api/config.php"""
    points: dict[str, tuple[str, int]] = field(default_factory=dict)
    """(name, points) by lower-case name"""
    points_active: bool = True
    points_raw: str | None = None
    """Body to answer the next points request with, instead of the real answer (used once)"""

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(f"/{CID}/api", self._api)
        app.router.add_post(f"/{CID}/", self._login)
        app.router.add_get(f"/{CID}/chatstream/{{user}}/{{sid}}/start", self._frame)
        app.router.add_get(f"/{CID}/chatstream/{{user}}/{{token}}/stream", self._stream)
        app.router.add_get(f"/{CID}/tok/{{user}}/{{sid}}/{{millis}}", self._tok)
        app.router.add_post("/cgi-bin/chat.cgi", self._send)
        app.router.add_get("/api/config.php", self._config)
        app.router.add_post("/api/ignored.php", self._ignored)
        app.router.add_get("/api/points.php", self._points_get)
        app.router.add_post("/api/points.php", self._points_post)
        return app

    @property
    def messages(self) -> list[str]:
        """The texts sent to the chat"""
        return [message for _, message in self.sent]

    async def _ignored(self, request: web.Request) -> web.Response:
        """wkqb-web's ignore list: add=NAME or remove=NAME, answers with the new list"""
        form = await request.post()
        if failure := self._failure("ignored"):
            return failure
        users = self.config.setdefault("users", {})
        names = [
            n
            for n in users.get("ignored", [])
            if n.casefold() != str(form.get("add", form.get("remove", ""))).casefold()
        ]
        if "add" in form:
            names.append(str(form["add"]))
        users["ignored"] = names
        return web.json_response({"ignored": names})

    def _ranked(self) -> list[dict[str, Any]]:
        """Everybody with points, most first, with dense ranks"""
        entries = sorted(self.points.values(), key=lambda entry: (-entry[1], entry[0].casefold()))
        ranks = sorted({points for _, points in entries}, reverse=True)
        return [{"rank": ranks.index(points) + 1, "name": name, "points": points} for name, points in entries]

    def _points_failure(self) -> web.Response | None:
        if self.points_raw is not None:
            raw, self.points_raw = self.points_raw, None
            return web.Response(text=raw, content_type="application/json")
        return self._failure("points")

    async def _points_get(self, request: web.Request) -> web.Response:
        """wkqb-web's points: ?limit=N for the toplist, ?name=X for one standing"""
        if failure := self._points_failure():
            return failure
        ranked = self._ranked()
        if "name" in request.query:
            name = request.query["name"]
            found = next((entry for entry in ranked if entry["name"].casefold() == name.casefold()), None)
            entry = found or {"name": name, "points": 0, "rank": None}
            return web.json_response({"active": self.points_active, **entry})
        limit = int(request.query.get("limit", "10"))
        return web.json_response({"active": self.points_active, "toplist": ranked[:limit]})

    async def _points_post(self, request: web.Request) -> web.Response:
        """wkqb-web's points: exactly one of add={name: change}, reset=true, active=bool"""
        body = await request.json()
        if failure := self._points_failure():
            return failure
        if "active" in body:
            self.points_active = bool(body["active"])
            return web.json_response({"active": self.points_active})
        if body.get("reset") is True:
            deleted = len(self.points)
            self.points.clear()
            return web.json_response({"active": self.points_active, "deleted": deleted})
        if not self.points_active:
            return web.json_response({"error": "points disabled", "active": False}, status=409)
        totals: dict[str, int] = {}
        for name, change in body["add"].items():
            _, before = self.points.get(name.casefold(), (name, 0))
            totals[name] = max(0, before + change)
            # the last spelling wins, nobody is listed with 0
            if totals[name]:
                self.points[name.casefold()] = (name, totals[name])
            else:
                self.points.pop(name.casefold(), None)
        return web.json_response({"active": True, "points": totals})

    async def _config(self, request: web.Request) -> web.Response:
        if failure := self._failure("config"):
            return failure
        return web.json_response(self.config)

    def push(self, data: str | bytes) -> None:
        """Sends data on the current stream."""
        self._queues[-1].put_nowait(data.encode() if isinstance(data, str) else data)

    def close_stream(self) -> None:
        self._queues[-1].put_nowait(None)

    def abort_stream(self) -> None:
        """Drops the connection without ending the response, as the nightly maintenance does."""
        self._abort = True
        self.close_stream()

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
        if self._abort:
            self._abort = False
            assert request.transport is not None
            request.transport.close()
            return response
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
        if len(message.encode()) > 820:
            # as measured in a real chat: the message is dropped
            return web.Response(
                text="<html><title>Fehler</title><body>Fehler: fs54SdFsA.</body></html>", content_type="text/html"
            )
        self.sent.append((user, message))
        if message == "/exit":
            self.online.discard(user.lower())
            return web.Response(text="<i>bye, <b>" + user + "</b></i>", content_type="text/html")
        return web.Response(status=204)
