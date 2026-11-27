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


class WebkicksError(Exception):
    """Webkicks answered, but not as expected."""


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
        frame = await self._request(
            "GET", f"{self.chat.url}/chatstream/{self.credentials.username}/{self._sid()}/start"
        )
        match = _STREAM_URL.search(frame)
        if match is None:
            raise WebkicksError("the stream frame has no stream URL")
        return urljoin(f"{self.chat.url}/", str(match.group(1)))

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
        await self._request(
            "POST",
            f"{self.chat.server}/cgi-bin/chat.cgi",
            {"user": self.credentials.username, "pass": self._sid(), "cid": self.chat.cid, "message": message},
        )

    async def logout(self) -> None:
        await self.send("/exit")

    def _sid(self) -> str:
        if self.sid is None:
            raise RuntimeError("fetch_sid() first")
        return self.sid

    def _emit(self, event: str, fields: dict[str, Any]) -> None:
        if self._trace is not None:
            self._trace(event, redact(fields, secrets(sid=self.sid, password=self.credentials.password)))

    async def _request(
        self,
        method: str,
        url: str,
        form: Mapping[str, str] | None = None,
        on_body: Callable[[str], None] | None = None,
    ) -> str:
        """Sends a request and returns the body as text. Raises WebkicksError on HTTP errors."""
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
