"""Writes trace events as JSON lines: one file per account and day, e.g. traces/mybot/2026-11-27.jsonl.

A line looks like
    {"time": "2026-11-27T23:59:01.123+01:00", "account": "mybot", "conn": 3, "event": "chunk",
     "data": {"size": 52, "base64": "...", "text": "..."}}

Bytes are stored as base64 (exactly as received) plus a UTF-8 decoding for reading. Session ids
and passwords are replaced by "<sid>"/"<password>" everywhere, so traces can be passed on.
Writing never raises: a failure is reported on stderr and the event is lost.
"""

import base64
import contextlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from wkqb.redaction import MIN_LENGTH, redact


class Recorder:
    def __init__(self, directory: Path, account: str, echo: TextIO | None = None) -> None:
        self._directory = directory / re.sub(r"[^A-Za-z0-9_-]", "_", account)
        self._account = account
        self._echo = echo
        self._secrets: dict[str, str] = {}

    def redact(self, secret: str | None, placeholder: str) -> None:
        """Replace secret by placeholder in all later events."""
        if secret is not None and len(secret) >= MIN_LENGTH:
            self._secrets[secret] = placeholder

    def event(self, conn: int | str, name: str, fields: dict[str, Any] | None = None) -> None:
        try:
            now = datetime.now().astimezone()
            record = {
                "time": now.isoformat(timespec="milliseconds"),
                "account": self._account,
                "conn": conn,
                "event": name,
                **self._clean(redact(fields or {}, self._secrets)),
            }
            self._directory.mkdir(parents=True, exist_ok=True)
            with (self._directory / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            if self._echo is not None:
                self._echo.write(_summary(record) + "\n")
                self._echo.flush()
        except Exception as e:
            with contextlib.suppress(Exception):
                print(f"recorder: event {name!r} lost: {type(e).__name__}: {e}", file=sys.stderr)

    def _clean(self, value: Any) -> Any:
        """Makes value JSON-serializable."""
        if isinstance(value, bytes):
            return {
                "size": len(value),
                "base64": base64.b64encode(value).decode("ascii"),
                "text": value.decode("utf-8", errors="replace"),
            }
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return {str(k): self._clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(v) for v in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return redact(str(value), self._secrets)


def _summary(record: dict[str, Any]) -> str:
    """One line for the terminal."""
    head = f"{record['time']} [{record['conn']}] {record['event']}"
    match record["event"]:
        case "chunk":
            return f"{head}: {record['data']['text']!r}"
        case "http":
            return f"{head}: {record.get('method')} {record.get('url')} -> {record.get('status', record.get('error'))}"
        case _:
            details = {k: v for k, v in record.items() if k not in ("time", "account", "conn", "event")}
            return f"{head}: {json.dumps(details, ensure_ascii=False)}" if details else head
