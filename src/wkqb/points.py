"""The points the chatters win in the games. wkqb-web keeps them (/api/points.php, see
Wkqb\\Controller\\ApiController::points() there); the bot only reports changes and asks for standings.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

#: Timeout for one request: points are reported in the background, standings are asked for in the chat
TIMEOUT = 10.0
#: A Webkicks nick: a-z, digits and "_"; "-" only in nicks of the Webkicks team
NICK = re.compile(r"[A-Za-z0-9_-]{1,50}")
#: Points per single change, in either direction (as wkqb-web allows)
MAX_CHANGE = 1_000_000


class PointsError(Exception):
    """wkqb-web couldn't be asked or refused the request."""


class PointsDisabled(PointsError):
    """The points system is switched off (e.g. in the web interface, after the bot loaded its settings)."""


@dataclass(frozen=True, slots=True)
class Standing:
    name: str
    points: int
    rank: int | None
    """Equal points share a rank, the next one follows without a gap (1, 1, 2); None without points"""


class Points(Protocol):
    """What the bot needs; `url` is the bot's address of the API (from its settings)."""

    async def add(self, url: str, changes: Mapping[str, int]) -> dict[str, int]:
        """Adds (or takes away) points; the new totals by name, never below 0."""
        ...

    async def standing(self, url: str, name: str) -> Standing: ...

    async def toplist(self, url: str, limit: int) -> list[Standing]: ...

    async def reset(self, url: str) -> int:
        """Deletes all points; the number of chatters who had some."""
        ...

    async def set_active(self, url: str, active: bool) -> None: ...


class HttpPoints:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._http = session

    async def add(self, url: str, changes: Mapping[str, int]) -> dict[str, int]:
        answer = await self._request("POST", url, json={"add": dict(changes)})
        totals = answer.get("points")
        if not isinstance(totals, dict) or not all(isinstance(v, int) for v in totals.values()):
            raise PointsError("the answer has no totals")
        return {str(k): v for k, v in totals.items()}

    async def standing(self, url: str, name: str) -> Standing:
        return _standing(await self._get(url, {"name": name}))

    async def toplist(self, url: str, limit: int) -> list[Standing]:
        entries = (await self._get(url, {"limit": str(limit)})).get("toplist")
        if not isinstance(entries, list):
            raise PointsError("the answer has no toplist")
        return [_standing(entry) for entry in entries]

    async def reset(self, url: str) -> int:
        deleted = (await self._request("POST", url, json={"reset": True})).get("deleted")
        return deleted if isinstance(deleted, int) else 0

    async def set_active(self, url: str, active: bool) -> None:
        await self._request("POST", url, json={"active": active})

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Standings are only given while the points system is on, as in the old bot."""
        answer = await self._request("GET", url, params=params)
        if answer.get("active") is False:
            raise PointsDisabled("the points system is switched off")
        return answer

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with self._http.request(
                method, url, timeout=aiohttp.ClientTimeout(total=TIMEOUT), **kwargs
            ) as response:
                if response.status == 409:
                    raise PointsDisabled("the points system is switched off")
                if response.status != 200:
                    raise PointsError(f"HTTP {response.status}")
                answer = await response.json(content_type=None)
        except PointsError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise PointsError(f"{type(e).__name__}: {e}") from e
        if not isinstance(answer, dict):
            raise PointsError("the answer is no JSON object")
        return answer


def _standing(data: Any) -> Standing:
    if not isinstance(data, dict):
        raise PointsError("invalid entry")
    name, points, rank = data.get("name"), data.get("points"), data.get("rank")
    if not isinstance(name, str) or not isinstance(points, int) or not (rank is None or isinstance(rank, int)):
        raise PointsError("invalid entry")
    return Standing(name, points, rank)
