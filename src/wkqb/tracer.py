"""Records everything a chat account sees, to learn the Webkicks protocol (see wkqb.recorder).

    wkqb-tracer run  [--env-file FILE] [--out DIR]    stay in the chat and record the stream
    wkqb-tracer send [--env-file FILE] [--out DIR] MESSAGE...    send a chat message while "run" is logged in

The credentials come from WK_CHATURL, WK_USERNAME and WK_PASSWORD (environment or --env-file).

"run" logs in like the bot and records every HTTP exchange and every stream chunk with its
arrival time. When the stream ends or anything fails, it records why and logs in again, waiting
longer after each failed attempt. It only ends on its own when the first login fails (wrong
credentials, wrong chat) or when the account logs in elsewhere: logging in again would end that
other session, and two tracers would throw each other out forever. SIGINT/SIGTERM log the account
out.

Only use it with test accounts in a chat whose users know that it is recorded. Passwords shorter
than 6 characters can't be removed from the traces (see wkqb.recorder).
"""

import argparse
import asyncio
import contextlib
import os
import signal
import sys
import time
import traceback
from pathlib import Path

import aiohttp

from wkqb import USER_AGENT
from wkqb.recorder import Recorder
from wkqb.webkicks import ChatUrl, Credentials, Webkicks, from_env

#: Seconds without any stream data until the stream counts as dead
READ_TIMEOUT = 900.0
#: Seconds between the keep-alive requests (the old bot used 15 minutes)
KEEPALIVE_INTERVAL = 600.0
#: Waiting time before logging in again; doubles after every attempt up to RETRY_MAX
RETRY_MIN = 5.0
RETRY_MAX = 300.0
#: A stream that lasted this long resets the waiting time
STABLE_AFTER = 300.0
#: The last message of the stream when the account logs in elsewhere
TAKEN_OVER = b"Connection Closed (multiple Login detected)"


class TakenOver(Exception):
    """The account logged in elsewhere, which closed our stream."""


class Tracer:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        chat: ChatUrl,
        credentials: Credentials,
        recorder: Recorder,
        *,
        read_timeout: float = READ_TIMEOUT,
        keepalive_interval: float = KEEPALIVE_INTERVAL,
        retry_min: float = RETRY_MIN,
        retry_max: float = RETRY_MAX,
    ) -> None:
        self._session = session
        self._chat = chat
        self._credentials = credentials
        self._recorder = recorder
        self._read_timeout = read_timeout
        self._keepalive_interval = keepalive_interval
        self._retry_min = retry_min
        self._retry_max = retry_max
        self._conn = 0
        self._client: Webkicks | None = None
        self.streams = 0
        """Number of streams that delivered data so far"""

    async def run(self) -> None:
        """Records until cancelled or the account logged in elsewhere.

        Raises if a connection fails before any stream delivered data.
        """
        delay = self._retry_min
        while True:
            self._conn += 1
            started = time.monotonic()
            try:
                reason = await self._connection()
                self._event("stream_end", {"reason": reason, "seconds": round(time.monotonic() - started, 3)})
            except asyncio.CancelledError:
                raise
            except TakenOver:
                self._event(
                    "stream_end", {"reason": "logged in elsewhere", "seconds": round(time.monotonic() - started, 3)}
                )
                self._client = None  # nothing to log out
                return
            except Exception as e:
                self._event(
                    "error",
                    {
                        "type": type(e).__name__,
                        "message": str(e),
                        "seconds": round(time.monotonic() - started, 3),
                        "traceback": traceback.format_exc(),
                    },
                )
                if self.streams == 0:
                    raise
            if time.monotonic() - started >= STABLE_AFTER:
                delay = self._retry_min
            self._event("retry", {"wait": delay})
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._retry_max)

    async def logout(self) -> None:
        if self._client is not None and self._client.sid is not None:
            try:
                await self._client.logout()
                self._event("logout")
            except Exception as e:
                self._event("error", {"type": type(e).__name__, "message": str(e), "during": "logout"})

    async def _connection(self) -> str:
        """One login and stream. Returns why the stream ended."""
        conn = self._conn
        client = Webkicks(
            self._session, self._chat, self._credentials, lambda name, fields: self._event(name, fields, conn)
        )
        self._client = client
        self._recorder.redact(await client.fetch_sid(), "<sid>")
        await client.login()
        url = await client.stream_url()
        self._recorder.redact(client.stream_token, "<stream>")

        keepalive = asyncio.create_task(self._keep_alive(client, conn))
        try:
            async with contextlib.aclosing(client.stream(url, self._read_timeout)) as stream:
                received = False
                tail = b""  # the end of the stream so far; a message can span chunks
                async for chunk in stream:
                    if not received:
                        self.streams += 1
                        received = True
                    tail = (tail + chunk)[-2 * len(TAKEN_OVER) :]
            if TAKEN_OVER in tail:
                raise TakenOver()
            return "closed by Webkicks"
        except TimeoutError:
            return f"nothing received for {self._read_timeout:g} seconds"
        finally:
            keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive

    async def _keep_alive(self, client: Webkicks, conn: int) -> None:
        while True:
            await asyncio.sleep(self._keepalive_interval)
            try:
                await client.keep_alive()
            except Exception as e:
                self._event("error", {"type": type(e).__name__, "message": str(e), "during": "keep-alive"}, conn)

    def _event(self, name: str, fields: dict[str, object] | None = None, conn: int | None = None) -> None:
        self._recorder.event(self._conn if conn is None else conn, name, fields)


async def trace(chat: ChatUrl, credentials: Credentials, recorder: Recorder) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        tracer = Tracer(session, chat, credentials, recorder)
        recorder.event(0, "start", {"chat": chat.url, "read_timeout": READ_TIMEOUT, "keepalive": KEEPALIVE_INTERVAL})
        runner = asyncio.create_task(tracer.run())
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if runner.done():
            if (error := runner.exception()) is not None:
                print(f"wkqb-tracer: {error}", file=sys.stderr)
                return 1
            print("wkqb-tracer: the account logged in elsewhere, stopping", file=sys.stderr)
            recorder.event(0, "stop", {"reason": "logged in elsewhere"})
            return 0
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        await tracer.logout()
        recorder.event(0, "stop")
        return 0


async def send(chat: ChatUrl, credentials: Credentials, recorder: Recorder, message: str) -> int:
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        client = Webkicks(session, chat, credentials, lambda name, fields: recorder.event("send", name, fields))
        try:
            recorder.redact(await client.fetch_sid(), "<sid>")
            await client.send(message)
        except Exception as e:
            recorder.event("send", "error", {"type": type(e).__name__, "message": str(e)})
            print(f"wkqb-tracer: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    return 0


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines, "#" comments and quotes around the value are allowed."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


settings = from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wkqb-tracer", description="Records what a chat account sees.")
    parser.add_argument("--env-file", type=Path, help="file with WK_CHATURL, WK_USERNAME, WK_PASSWORD")
    parser.add_argument("--out", type=Path, default=Path("traces"), help="directory for the traces (default: traces)")
    parser.add_argument("--quiet", action="store_true", help="don't print the events")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="stay in the chat and record the stream")
    send_parser = commands.add_parser("send", help="send a chat message")
    send_parser.add_argument("message", nargs="+")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env |= read_env_file(args.env_file)
    try:
        chat, credentials = settings(env)
    except ValueError as e:
        parser.error(str(e))

    recorder = Recorder(args.out, credentials.username, None if args.quiet else sys.stdout)
    recorder.redact(credentials.password, "<password>")
    if len(credentials.password) < 6:
        print("wkqb-tracer: the password is too short to be removed from the traces", file=sys.stderr)

    if args.command == "send":
        return asyncio.run(send(chat, credentials, recorder, " ".join(args.message)))
    return asyncio.run(trace(chat, credentials, recorder))


if __name__ == "__main__":
    sys.exit(main())
