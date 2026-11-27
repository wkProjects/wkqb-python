"""Cron schedules for the quotes and the calendar, in the bots' time zone."""

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import CroniterBadDateError, croniter

#: The web interface exports the settings for this time zone
TIMEZONE = ZoneInfo("Europe/Berlin")


def now() -> datetime:
    return datetime.now(TIMEZONE)


class Schedule:
    """Fires at the times of a (valid) cron expression; runs missed while the bot was busy fire once."""

    def __init__(self, expression: str, start: datetime) -> None:
        self.expression = expression
        self.next = self._after(start)

    def due(self, at: datetime) -> bool:
        if self.next is None or at < self.next:
            return False
        self.next = self._after(at)
        return True

    def _after(self, at: datetime) -> datetime | None:
        """The next time after at; None if it never comes (e.g. "0 0 31 2 *")."""
        try:
            result = croniter(self.expression, at, ret_type=datetime, max_years_between_matches=5).get_next()
        except CroniterBadDateError:
            return None
        assert isinstance(result, datetime)
        return result
