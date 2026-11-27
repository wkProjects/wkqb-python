"""HTTP access to a Webkicks chat: session id, login, the chat stream, keep-alive and sending.

The protocol is not documented; this does what the bot did since version 5. Every request has a
timeout, and every exchange can be passed to a trace hook (see wkqb.tracer). Traced data never
contains the password or the session id.
"""

import json
import re
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from wkqb.redaction import redact, secrets

#: Timeout of a whole request, and of connecting to the stream
REQUEST_TIMEOUT = 30.0

#: Gets the event name and its fields, e.g. ("http", {"method": "POST", ...})
Trace = Callable[[str, dict[str, Any]], None]

#: Form fields that hold the password or the session id
_SECRET_FIELDS = frozenset({"pass"})
_STREAM_URL = re.compile(r"""URL=([^"'\s>]+)""", re.IGNORECASE)
#: The stream URL has a token of its own, e.g. /nexus/chatstream/mybot/<token>/stream
_STREAM_TOKEN = re.compile(r"/chatstream/[^/]+/([^/]+)/stream")
#: chat.cgi answers a message with HTTP 204 and no body; without a login it shows an error page
_SEND_NOT_LOGGED_IN = "Bitte neu einloggen"


class WebkicksError(Exception):
    """Webkicks answered, but not as expected."""


class NotLoggedIn(WebkicksError):
    """The account isn't logged in (any more), e.g. after the nightly maintenance."""


@dataclass(frozen=True)
class ChatUrl:
    """A chat, e.g. https://server1.webkicks.de/mychat/"""

    server: str
    """Scheme and host, e.g. https://server1.webkicks.de"""
    cid: str
    """The chat's name in the URL"""

    @classmethod
    def parse(cls, url: str) -> ChatUrl:
        parts = urlsplit(url.strip())
        cid = parts.path.strip("/")
        if parts.scheme not in ("http", "https") or not parts.netloc or not cid or "/" in cid:
            raise ValueError(f"invalid chat URL: {url!r}")
        return cls(f"{parts.scheme}://{parts.netloc}", cid)

    @property
    def url(self) -> str:
        return f"{self.server}/{self.cid}"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


def from_env(env: Mapping[str, str]) -> tuple[ChatUrl, Credentials]:
    """The chat and account from WK_CHATURL, WK_USERNAME and WK_PASSWORD (see wkqb-web's BotManifests)."""
    missing = [k for k in ("WK_CHATURL", "WK_USERNAME", "WK_PASSWORD") if not env.get(k)]
    if missing:
        raise ValueError(f"missing: {', '.join(missing)}")
    return ChatUrl.parse(env["WK_CHATURL"]), Credentials(env["WK_USERNAME"].strip(), env["WK_PASSWORD"])


class Webkicks:
    """One login of an account in a chat. The session id comes from fetch_sid()."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        chat: ChatUrl,
        credentials: Credentials,
        trace: Trace | None = None,
    ) -> None:
        self._session = session
        self.chat = chat
        self.credentials = credentials
        self._trace = trace
        self.sid: str | None = None
        self.stream_token: str | None = None
        """Like the sid, it gives access to the chat while its stream is open"""

    async def fetch_sid(self) -> str:
        """Asks the chat API for the session id of the account."""
        self.sid = None
        await self._request(
            "POST",
            f"{self.chat.url}/api",
            {
                "cid": self.chat.cid,
                "user": self.credentials.username.lower(),
                "pass": self.credentials.password,
                "job": "get_sid",
            },
            # before the answer is traced, so the sid is removed from it
            on_body=self._take_sid,
        )
        if self.sid is None:
            raise WebkicksError("get_sid: the answer has no sid")
        return self.sid

    def _take_sid(self, body: str) -> None:
        try:
            answer = json.loads(body)
        except ValueError:
            return
        sid = answer.get("sid") if isinstance(answer, dict) else None
        if isinstance(sid, str) and sid:
            self.sid = sid

    async def login(self) -> None:
        """Logs the account into the chat, like the login form."""
        await self._request(
            "POST",
            f"{self.chat.url}/",
            {
                "user": self.credentials.username,
                "pass": self.credentials.password,
                "cid": self.chat.cid,
                "job": "ok",
                "login": "Login",
                "guest": "",
            },
        )

    async def stream_url(self) -> str:
        """The URL of the chat stream, from the frame that starts it."""
        url: str | None = None

        def take_url(frame: str) -> None:
            nonlocal url
            if (match := _STREAM_URL.search(frame)) is not None:
                url = urljoin(f"{self.chat.url}/", str(match.group(1)))
                if (token := _STREAM_TOKEN.search(url)) is not None:
                    self.stream_token = token.group(1)

        # the token is known before the frame is traced, so it is removed from it
        await self._request(
            "GET", f"{self.chat.url}/chatstream/{self.credentials.username}/{self._sid()}/start", on_body=take_url
        )
        if url is None:
            raise WebkicksError("the stream frame has no stream URL")
        return url

    async def stream(self, url: str, read_timeout: float) -> AsyncGenerator[bytes]:
        """The chat stream in the chunks it arrives in. Ends when Webkicks closes it.

        Raises TimeoutError when nothing arrives for read_timeout seconds.
        """
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=REQUEST_TIMEOUT, sock_read=read_timeout)
        async with self._session.get(url, timeout=timeout) as response:
            self._emit(
                "stream_open", {"url": url, "status": response.status, "headers": list(response.headers.items())}
            )
            if response.status != 200:
                raise WebkicksError(f"stream: HTTP {response.status}")
            async for chunk in response.content.iter_any():
                self._emit("chunk", {"data": chunk})
                yield chunk

    async def keep_alive(self) -> None:
        """Tells the chat that the account is still there (the browser does this every few minutes)."""
        millis = round(time.time() * 1000)
        await self._request("GET", f"{self.chat.url}/tok/{self.credentials.username.lower()}/{self._sid()}/{millis}")

    async def send(self, message: str) -> None:
        """Sends a chat message or command. Raises NotLoggedIn if the account isn't in the chat."""
        body = await self._request(
            "POST",
            f"{self.chat.server}/cgi-bin/chat.cgi",
            {"user": self.credentials.username, "pass": self._sid(), "cid": self.chat.cid, "message": message},
        )
        if _SEND_NOT_LOGGED_IN in body:
            raise NotLoggedIn("chat.cgi: not logged in")

    async def logout(self) -> None:
        await self.send("/exit")

    def _sid(self) -> str:
        if self.sid is None:
            raise RuntimeError("fetch_sid() first")
        return self.sid

    def _emit(self, event: str, fields: dict[str, Any]) -> None:
        if self._trace is not None:
            found = secrets(sid=self.sid, stream=self.stream_token, password=self.credentials.password)
            self._trace(event, redact(fields, found))

    async def _request(
        self,
        method: str,
        url: str,
        form: Mapping[str, str] | None = None,
        on_body: Callable[[str], None] | None = None,
    ) -> str:
        """Sends a request and returns the body as text. Raises WebkicksError on HTTP errors.

        A request on a pooled connection that the server had already closed fails with
        ServerDisconnectedError before the server saw it, so it is sent once more.
        """
        try:
            return await self._request_once(method, url, form, on_body)
        except aiohttp.ServerDisconnectedError:
            return await self._request_once(method, url, form, on_body)

    async def _request_once(
        self,
        method: str,
        url: str,
        form: Mapping[str, str] | None,
        on_body: Callable[[str], None] | None,
    ) -> str:
        traced: dict[str, Any] = {"method": method, "url": url}
        if form is not None:
            traced["form"] = {k: "<redacted>" if k in _SECRET_FIELDS else v for k, v in form.items()}
        try:
            async with self._session.request(
                method, url, data=form, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                raw = await response.read()
                body = raw.decode(response.charset or "utf-8", errors="replace")
                traced |= {"status": response.status, "headers": list(response.headers.items()), "body": body}
                if on_body is not None and response.status < 400:
                    on_body(body)
        except Exception as e:
            traced["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            self._emit("http", traced)
        if response.status >= 400:
            raise WebkicksError(f"{method} {url}: HTTP {response.status}")
        return body
