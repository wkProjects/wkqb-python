"""The bot: python -m wkqb (in the pod: environment from wkqb-web's BotManifests).

WK_CHATURL, WK_USERNAME, WK_PASSWORD   the chat account
WKQB_CONFIG_URL                        the settings (or WKQB_CONFIG_FILE, for local tests)
LOGLEVEL                               DEBUG, INFO (default), WARNING, ERROR
"""

import asyncio
import logging
import os
import signal
import sys

import aiohttp

from wkqb import USER_AGENT
from wkqb.runner import Runner
from wkqb.webkicks import ChatUrl, Credentials, from_env

logger = logging.getLogger("wkqb")


def main() -> int:
    level = os.environ.get("LOGLEVEL", "").strip().upper()
    logging.basicConfig(
        level=level if level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        chat, credentials = from_env(os.environ)
    except ValueError as e:
        logger.error("Falsche Umgebung: %s", e)
        return 1
    return asyncio.run(_run(chat, credentials))


async def _run(chat: ChatUrl, credentials: Credentials) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        runner = Runner(session, chat, credentials, os.environ)
        return await runner.run(stop)


if __name__ == "__main__":
    sys.exit(main())
