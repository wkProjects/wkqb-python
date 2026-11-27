import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from wkqb import events
from wkqb.events import (
    ChatBot,
    Dice,
    Event,
    Login,
    Message,
    SystemMessage,
    Unparsed,
    Whisper,
    parse_line,
    plain,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROW = "<table border=0><tr><td valign=bottom>{}</td></tr></table>"
T = "<FONT SIZE=-2>(23:59)</FONT> "


def recorded() -> list[dict[str, Any]]:
    """Real lines of the stream (see docs/webkicks-protocol.md), with their expected events."""
    lines = (FIXTURES / "lines-2026-11-27.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.mark.parametrize("case", recorded(), ids=lambda case: f"{case['event']}-{case['line'][40:80]}")
def test_recorded_lines(case: dict[str, Any]) -> None:
    expected: Event = getattr(events, case["event"])(**case["fields"])
    assert parse_line(case["line"]) == expected


def test_recorded_lines_cover_all_events() -> None:
    kinds = {case["event"] for case in recorded()}
    concrete = {
        name
        for name, value in vars(events).items()
        if isinstance(value, type) and issubclass(value, Event) and value not in (Event, events.UserEvent)
    }
    # the nightly maintenance message has not been recorded yet
    assert concrete - kinds == {"SystemMessage"}


def test_system_message() -> None:
    # from the old bot's pattern for the nightly maintenance
    line = ROW.format(
        '<br /><font title="WebKicks.De - Sysadmin"><b><font color="#FF0000">System-Meldung:</font>'
        '<span class="not_reg"> Der Chat wird aufgrund des n&auml;chtlichen Wartungszyklus f&uuml;r ca. 10 Sekunden '
        "ausfallen.</span></b><br /><br />"
    )
    assert parse_line(line) == SystemMessage(
        "Der Chat wird aufgrund des nächtlichen Wartungszyklus für ca. 10 Sekunden ausfallen."
    )


def test_two_dice() -> None:
    line = ROW.format(
        T + '<img src="/wuerfel.gif"> <b><i><span onclick="fluester(\'anna\')"><b>anna</b></span></b> '
        "w&uuml;rfelt eine <b>23</b></i>."
    )
    assert parse_line(line) == Dice("anna", False, "23:59", "23")


def test_names_and_texts_are_unescaped() -> None:
    line = ROW.format(
        T + '<b><font title="M&amp;M"><span onclick="fluester(\'M&amp;M\')">M&amp;M</span></b>: '
        "!quiz &quot;a&quot; &lt;3</font>"
    )
    assert parse_line(line) == Message("M&M", False, "23:59", '!quiz "a" <3')


def test_message_texts_can_contain_markup_of_the_sender() -> None:
    line = ROW.format(
        T + '<b><font title="anna"><span onclick="fluester(\'anna\')">anna</span></b>: '
        "ich <3 dich, <b>fett</b> </font> und 5 > 3</font>"
    )
    assert parse_line(line) == Message("anna", False, "23:59", "ich <3 dich, fett  und 5 > 3")


def test_whisper_from_a_guest_is_not_marked() -> None:
    # whispers show no guest suffix; the bot has to know its guests from their logins
    line = ROW.format(T + "<b><span onclick=\"fluester('gast')\">gast fl&uuml;stert</span>: <font color=red>hi</font>")
    assert parse_line(line) == Whisper("gast", False, "23:59", "hi")


def test_chat_bot_by() -> None:
    line = ROW.format(
        T + '<font title="anna"><b><font color="#FF0000">Chat-Bot:</font><span class="not_reg"> '
        "bert wurde gekickt.</b><small> (by anna)</small></span></b>"
    )
    assert parse_line(line) == ChatBot("anna", False, "23:59", "bert wurde gekickt.", "anna")


def test_login_name_comes_from_the_login_tag() -> None:
    line = ROW.format(
        T + '<font title="Bert (Gast)"><img src="/gruen.gif"><login Bert /><B><i>'
        '<span onclick="fluester(\'Bert\')">Bert</span><span class="commandcolor"> betritt den Chat.</b></span></i>'
    )
    assert parse_line(line) == Login("Bert", True, "23:59")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "<hr>",
        "\tparent.atok = 'UuXaM';",
        ROW.format("<span>no time</span>"),
        ROW.format(T + "<i>something new</i>"),
        ROW.format(T + '<font title=""><img src="/rot.gif">'),
    ],
)
def test_unknown_lines(line: str) -> None:
    assert parse_line(line) == Unparsed(line.strip())


def test_parser_errors_become_unparsed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(line: str) -> Event:
        raise ValueError("bug")

    monkeypatch.setattr(events, "_parse", broken)
    assert parse_line("<!-- update //-->") == Unparsed("<!-- update //-->")


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        ("a <b>b</b> c", "a b c"),
        ("x<br>y<br />z", "x y z"),
        ("&quot;&#x27;&amp;&lt;&#58;", "\"'&<:"),
        ("<3 a < b > c", "<3 a < b > c"),
        (
            '<span onclick="javascript: repClick(\'hm\')" style="cursor: pointer;">'
            '<img src="/mychat/replacer/hm.gif" alt="&#58;hm"></span>!',
            ":hm!",
        ),
        ('a<script language="JavaScript">x("<b>");</script>b', "ab"),
        # as recorded from a normal account: rejected tags arrive escaped, typed entities unchanged
        ("<s>s</s> &lt;strike&gt;strike&lt;/strike&gt;", "s <strike>strike</strike>"),
        ("&lt;font &gt;size</font>", "<font >size"),
        ("&auml; &amp; &lt;b&gt; &#58;", "ä & <b> :"),
        ("  padded  ", "padded"),
    ],
)
def test_plain(markup: str, text: str) -> None:
    assert plain(markup) == text


def test_events_are_immutable() -> None:
    message = Message("anna", False, None, "hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        message.text = "x"  # type: ignore[misc]
