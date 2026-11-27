from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from fake_webkicks import CID, FakeWebkicks
from wkqb.webkicks import ChatUrl, Credentials


@pytest.fixture
def fake() -> FakeWebkicks:
    return FakeWebkicks()


@pytest.fixture
async def server(fake: FakeWebkicks) -> AsyncIterator[TestServer]:
    server = TestServer(fake.app())
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
def chat(server: TestServer) -> ChatUrl:
    return ChatUrl.parse(str(server.make_url(f"/{CID}/")))


@pytest.fixture
def credentials() -> Credentials:
    return Credentials("TestBot", "geheim123")


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session
