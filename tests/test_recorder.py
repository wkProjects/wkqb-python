import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest

from wkqb.recorder import Recorder


def lines(directory: Path) -> list[dict[str, Any]]:
    files = sorted(directory.glob("*/*.jsonl"))
    return [json.loads(line) for file in files for line in file.read_text(encoding="utf-8").splitlines()]


def test_writes_json_lines_per_account_and_day(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "Test Bot/ä")
    recorder.event(1, "start", {"chat": "https://x/y"})
    recorder.event(2, "chunk", {"data": "Grüße <b>".encode()})

    [file] = tmp_path.glob("*/*.jsonl")
    assert file.parent.name == "Test_Bot__"
    first, second = lines(tmp_path)
    assert first["account"] == "Test Bot/ä"
    assert (first["conn"], first["event"], first["chat"]) == (1, "start", "https://x/y")
    assert second["data"] == {
        "size": 11,
        "base64": base64.b64encode("Grüße <b>".encode()).decode(),
        "text": "Grüße <b>",
    }
    assert "T" in first["time"] and first["time"][-6] in "+-"


def test_invalid_utf8_is_kept_exactly(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "bot")
    recorder.event(1, "chunk", {"data": "zurück".encode("latin-1")})
    [line] = lines(tmp_path)
    assert base64.b64decode(line["data"]["base64"]) == "zurück".encode("latin-1")
    assert line["data"]["text"] == "zur�ck"


def test_redacts_secrets_everywhere(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "bot")
    recorder.redact("geheim123", "<password>")
    recorder.redact("88AbCdEfGhIjK", "<sid>")
    recorder.redact("kurz", "<password>")  # too short, would destroy normal text
    recorder.redact(None, "<sid>")
    recorder.event(
        1,
        "http",
        {
            "url": "https://x/chat/chatstream/bot/88AbCdEfGhIjK/start",
            "headers": [("Set-Cookie", "s=88AbCdEfGhIjK")],
            "body": 'value="geheim123" kurz',
            "data": b"pass=geheim123&sid=88AbCdEfGhIjK",
            "nested": {"list": ["geheim123", 1, None, True, 1.5]},
        },
    )
    [line] = lines(tmp_path)
    text = json.dumps(line)
    assert "geheim123" not in text and "88AbCdEfGhIjK" not in text
    assert line["url"] == "https://x/chat/chatstream/bot/<sid>/start"
    assert line["headers"] == [["Set-Cookie", "s=<sid>"]]
    assert line["body"] == 'value="<password>" kurz'
    assert base64.b64decode(line["data"]["base64"]) == b"pass=<password>&sid=<sid>"
    assert line["nested"] == {"list": ["<password>", 1, None, True, 1.5]}


def test_other_values_become_strings(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "bot")
    recorder.event(1, "x", {"path": Path("a/b")})
    assert lines(tmp_path)[0]["path"] == "a/b"


def test_never_raises(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    recorder = Recorder(blocker, "bot")  # the directory can't be created below a file
    recorder.event(1, "start")
    assert "event 'start' lost" in capsys.readouterr().err


def test_echo(tmp_path: Path) -> None:
    echo = io.StringIO()
    recorder = Recorder(tmp_path, "bot", echo)
    recorder.event(3, "chunk", {"data": b"<b>hi</b>"})
    recorder.event(3, "http", {"method": "GET", "url": "https://x/", "status": 200})
    recorder.event(3, "http", {"method": "GET", "url": "https://x/", "error": "TimeoutError: "})
    recorder.event(3, "retry", {"wait": 5})
    recorder.event(0, "stop")
    out = echo.getvalue().splitlines()
    assert out[0].endswith("[3] chunk: '<b>hi</b>'")
    assert out[1].endswith("[3] http: GET https://x/ -> 200")
    assert out[2].endswith("[3] http: GET https://x/ -> TimeoutError: ")
    assert out[3].endswith('[3] retry: {"wait": 5}')
    assert out[4].endswith("[0] stop")
