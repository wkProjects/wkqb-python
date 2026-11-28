from collections.abc import Awaitable, Callable

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from fake_webkicks import FakeWebkicks
from wkqb.points import HttpPoints, PointsDisabled, PointsError, Standing


@pytest.fixture
def url(server: TestServer) -> str:
    return str(server.make_url("/api/points.php?b_id=abcdef1234"))


@pytest.fixture
def points(session: aiohttp.ClientSession) -> HttpPoints:
    return HttpPoints(session)


async def test_add_returns_the_new_totals(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    assert await points.add(url, {"Anna": 5, "Bernd": -3}) == {"Anna": 5, "Bernd": 0}  # never below 0
    assert await points.add(url, {"anna": 2}) == {"anna": 7}  # names are case-insensitive
    assert fake.points == {"anna": ("anna", 7)}  # the last spelling wins, nobody is kept with 0


async def test_standing(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    fake.points = {"anna": ("Anna", 17), "bernd": ("Bernd", 5)}
    assert await points.standing(url, "ANNA") == Standing("Anna", 17, 1)
    assert await points.standing(url, "Bernd") == Standing("Bernd", 5, 2)


async def test_standing_of_an_unknown_chatter(url: str, points: HttpPoints) -> None:
    assert await points.standing(url, "Nobody") == Standing("Nobody", 0, None)


async def test_toplist_has_dense_ranks_and_a_limit(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    fake.points = {"a": ("Anna", 17), "b": ("Bernd", 17), "c": ("Cem", 3), "d": ("Dora", 1)}
    assert await points.toplist(url, 3) == [Standing("Anna", 17, 1), Standing("Bernd", 17, 1), Standing("Cem", 3, 2)]
    assert len(await points.toplist(url, 10)) == 4


async def test_empty_toplist(url: str, points: HttpPoints) -> None:
    assert await points.toplist(url, 10) == []


async def test_reset_returns_how_many_had_points(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    fake.points = {"a": ("Anna", 1), "b": ("Bernd", 2)}
    assert await points.reset(url) == 2
    assert fake.points == {}


async def test_set_active(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    await points.set_active(url, False)
    assert not fake.points_active
    await points.set_active(url, True)
    assert fake.points_active


async def test_adding_while_inactive_is_disabled(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    fake.points_active = False
    with pytest.raises(PointsDisabled):
        await points.add(url, {"Anna": 1})
    assert fake.points == {}
    await points.set_active(url, True)  # switching on works anyway
    assert await points.add(url, {"Anna": 1}) == {"Anna": 1}


async def test_reset_works_while_inactive(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    # as wkqb-web: only adding is refused
    fake.points = {"anna": ("Anna", 17)}
    fake.points_active = False
    assert await points.reset(url) == 1
    assert fake.points == {}


async def test_standing_and_toplist_while_inactive_are_disabled(
    fake: FakeWebkicks, url: str, points: HttpPoints
) -> None:
    fake.points_active = False
    with pytest.raises(PointsDisabled):
        await points.standing(url, "Anna")
    with pytest.raises(PointsDisabled):
        await points.toplist(url, 10)


async def test_disabled_is_a_points_error() -> None:
    assert issubclass(PointsDisabled, PointsError)


@pytest.mark.parametrize("status", [500, 403, 404])
async def test_http_errors(fake: FakeWebkicks, url: str, points: HttpPoints, status: int) -> None:
    calls: list[Callable[[], Awaitable[object]]] = [
        lambda: points.add(url, {"Anna": 1}),
        lambda: points.standing(url, "Anna"),
        lambda: points.toplist(url, 10),
        lambda: points.reset(url),
        lambda: points.set_active(url, True),
    ]
    for call in calls:
        fake.fail["points"] = status
        with pytest.raises(PointsError, match=f"HTTP {status}") as raised:
            await call()
        assert not isinstance(raised.value, PointsDisabled)


async def test_unreachable_server(points: HttpPoints) -> None:
    with pytest.raises(PointsError, match="ClientConnectorError"):
        await points.add("http://127.0.0.1:1/api/points.php", {"Anna": 1})
    with pytest.raises(PointsError):
        await points.standing("http://127.0.0.1:1/api/points.php", "Anna")


@pytest.mark.parametrize("raw", ["", "<html>Fehler</html>", "{", "[]", "null", '"text"', "17"])
async def test_no_json_object_is_an_error(fake: FakeWebkicks, url: str, points: HttpPoints, raw: str) -> None:
    fake.points_raw = raw
    with pytest.raises(PointsError):
        await points.add(url, {"Anna": 1})


@pytest.mark.parametrize(
    "raw",
    ['{"active": true}', '{"active": true, "points": []}', '{"active": true, "points": {"Anna": "5"}}'],
)
async def test_add_without_valid_totals(fake: FakeWebkicks, url: str, points: HttpPoints, raw: str) -> None:
    fake.points_raw = raw
    with pytest.raises(PointsError, match="totals"):
        await points.add(url, {"Anna": 1})


@pytest.mark.parametrize("raw", ['{"active": true}', '{"active": true, "toplist": {}}', '{"toplist": "x"}'])
async def test_toplist_without_a_list(fake: FakeWebkicks, url: str, points: HttpPoints, raw: str) -> None:
    fake.points_raw = raw
    with pytest.raises(PointsError, match="toplist"):
        await points.toplist(url, 10)


@pytest.mark.parametrize(
    "entry",
    [
        '"Anna"',
        '{"name": "Anna", "points": 1, "rank": "1"}',
        '{"name": "Anna", "points": "1", "rank": 1}',
        '{"name": 7, "points": 1, "rank": 1}',
        '{"points": 1, "rank": 1}',
        "{}",
    ],
)
async def test_invalid_entries(fake: FakeWebkicks, url: str, points: HttpPoints, entry: str) -> None:
    fake.points_raw = f'{{"active": true, "toplist": [{entry}]}}'
    with pytest.raises(PointsError, match="invalid entry"):
        await points.toplist(url, 10)
    fake.points_raw = entry.replace("{", '{"active": true, ', 1) if entry.startswith("{") else entry
    with pytest.raises(PointsError):
        await points.standing(url, "Anna")


async def test_reset_without_a_count_is_zero(fake: FakeWebkicks, url: str, points: HttpPoints) -> None:
    fake.points_raw = '{"active": true}'
    assert await points.reset(url) == 0
