"""The lines of the chat stream as events (see docs/webkicks-protocol.md for the markup).

parse_line() never raises: lines it doesn't know become Unparsed, so the bot can log them and go on.
Texts are plain text: smileys become ":code", tags are removed, entities decoded.
"""

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    pass


@dataclass(frozen=True, slots=True)
class ChatStarted(Event):
    """End of the stream header; the chat runs from here on."""


@dataclass(frozen=True, slots=True)
class KeepAlive(Event):
    """Sent every 60 seconds."""


@dataclass(frozen=True, slots=True)
class TimeoutWarning(Event):
    """Webkicks logs the account out in 2 minutes unless it shows activity (a keep-alive does)."""


@dataclass(frozen=True, slots=True)
class TakenOver(Event):
    """The account logged in elsewhere; Webkicks closes this stream."""


@dataclass(frozen=True, slots=True)
class Unparsed(Event):
    line: str


@dataclass(frozen=True, slots=True)
class UserEvent(Event):
    user: str
    """As Webkicks shows it, without the guest suffix"""
    guest: bool
    time: str | None
    """HH:MM as shown in the chat"""


@dataclass(frozen=True, slots=True)
class Message(UserEvent):
    text: str


@dataclass(frozen=True, slots=True)
class Action(UserEvent):
    """/me TEXT"""

    text: str


@dataclass(frozen=True, slots=True)
class Whisper(UserEvent):
    """A whisper (/f) to us."""

    text: str


@dataclass(frozen=True, slots=True)
class OwnWhisper(UserEvent):
    """The echo of a whisper we sent; user is the recipient."""

    text: str


@dataclass(frozen=True, slots=True)
class Comment(UserEvent):
    """/comment of a team member, shown without a name."""

    text: str


@dataclass(frozen=True, slots=True)
class TeamMessage(UserEvent):
    """/team, only shown to admins and moderators."""

    text: str


@dataclass(frozen=True, slots=True)
class Login(UserEvent):
    pass


@dataclass(frozen=True, slots=True)
class Logout(UserEvent):
    timed_out: bool
    """The browser was closed instead of logging out"""


@dataclass(frozen=True, slots=True)
class RoomEnter(UserEvent):
    """The user came into our room from another one."""

    room: str
    """Where the user came from"""


@dataclass(frozen=True, slots=True)
class RoomLeave(UserEvent):
    """The user went from our room to another one."""

    room: str
    """Where the user went"""


@dataclass(frozen=True, slots=True)
class Away(UserEvent):
    reason: str


@dataclass(frozen=True, slots=True)
class Back(UserEvent):
    pass


@dataclass(frozen=True, slots=True)
class Dice(UserEvent):
    result: str
    """The number(s), e.g. "5", or "23" for two dice"""


@dataclass(frozen=True, slots=True)
class ChatBot(UserEvent):
    """A public notice of the chat, e.g. "Neuer Moderator: …"; user is who caused it."""

    text: str
    by: str | None
    """The team member who caused it, if shown"""


@dataclass(frozen=True, slots=True)
class FloodWarning(UserEvent):
    """The chat warned user for sending too fast; the third warning kicks."""

    number: int


@dataclass(frozen=True, slots=True)
class ChatBotPM(Event):
    """A notice of the chat only for us, e.g. that a whisper could not be delivered."""

    time: str | None
    text: str


@dataclass(frozen=True, slots=True)
class SystemMessage(Event):
    """A notice of Webkicks itself, e.g. the nightly maintenance."""

    text: str


@dataclass(frozen=True, slots=True)
class RoomEntered(Event):
    """We are in room now (after /join)."""

    room: str


_FLAGS = re.IGNORECASE | re.DOTALL
_ROW = re.compile(r"^<table border=0><tr><td valign=bottom>(?P<body>.*?)</td></tr></table>$", _FLAGS)
_TIME = re.compile(r"^<font size=-2>\((?P<time>\d\d:\d\d)\)</font> ", _FLAGS)
_TITLE = re.compile(r'<font title="(?P<title>[^"]*)">', _FLAGS)
_FLUESTER = re.compile(r"fluester\('(?P<user>[^']*)'\)", _FLAGS)

_MESSAGE = re.compile(
    r'^<b><font title="(?P<title>[^"]*)"><span onclick="fluester\(\'(?P<user>[^\']*)\'\)">.*?</span></b>: '
    r"(?P<text>.*)$",
    _FLAGS,
)
_ACTION = re.compile(
    r'^<font title="(?P<title>[^"]*)"><b><i><span onclick="fluester\(\'(?P<user>[^\']*)\'\)">.*?</span></b> '
    r"(?P<text>.*)</i></font>$",
    _FLAGS,
)
_WHISPER = re.compile(
    r"^<b><span onclick=\"fluester\('(?P<user>[^']*)'\)\">"
    r"(?P<own>Du fl&uuml;sterst an )?[^<]*?(?:fl&uuml;stert)?</span>: "
    r"<font color=red>(?P<text>.*)</font>$",
    _FLAGS,
)
_COMMENT = re.compile(
    r'^<img src="/pfeilg\.gif"> <font title="(?P<title>[^"]*)"><b><span class="not_reg"> (?P<text>.*)</span></b>$',
    _FLAGS,
)
_TEAM = re.compile(
    r"^<font title=\"Team-Nachricht\">.*?Team-Nachricht</span></b> \(von (?P<user>[^)]*)\):<b></font>"
    r'<span class="not_reg"> (?P<text>.*)</span></b>$',
    _FLAGS,
)
_CHATBOT = re.compile(
    r'^<font title="(?P<title>[^"]*)"><b><font color="#FF0000">(?P<kind>Chat-Bot|Chat-Bot-PM):</font>'
    r'<span class="not_reg"> (?P<text>.*)$',
    _FLAGS,
)
_SYSTEM = re.compile(r'<font color="#FF0000">System-Meldung:</font><span class="not_reg"> (?P<text>.*?)</span>', _FLAGS)
_BY = re.compile(r"<small> \(by (?P<by>[^)]*)\)</small>", _FLAGS)
_FLOOD = re.compile(r"^(?P<number>\d+)\. Flooding-Verwarnung", _FLAGS)
_LOGIN = re.compile(r"<login (?P<user>[^ ]+) />", _FLAGS)
_ROOM_FROM = re.compile(r'<span class="not_reg"> \(von <b>(?P<room>[^<]*)</b>\)</span>', _FLAGS)
_ROOM_TO = re.compile(r'<span class="not_reg"> \(nach <b>(?P<room>[^<]*)</b>\)</span>', _FLAGS)
_AWAY = re.compile(r"</b> meldet sich kurz ab(?: \((?P<reason>.*)\))?</i>", _FLAGS)
_BACK = re.compile(r"</b> meldet sich wieder zur(?:&uuml;|ü)ck\.</i>", _FLAGS)
_DICE = re.compile(r"w&uuml;rfelt eine <b>(?P<result>\d+)</b>", _FLAGS)
_ROOM_ENTERED = re.compile(r'^<span class="not_reg">Raum betreten: <b>(?P<room>[^<]*)</b></span>$', _FLAGS)

_SMILEY = re.compile(r"<span onclick=\"javascript: repClick\('(?P<code>[^']*)'\)\"[^>]*>.*?</span>", _FLAGS)
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
#: Only real tags: a user's "<3" or "a < b" stays
_TAG = re.compile(r"</?[a-z][^<>]*>", re.IGNORECASE)
_SCRIPT = re.compile(r"<script\b.*?</script>", _FLAGS)


def plain(markup: str) -> str:
    """The text of chat markup: smileys as ":code", without tags and entities."""
    text = _SMILEY.sub(lambda m: ":" + m.group("code"), markup)
    text = _SCRIPT.sub("", text)
    text = _BREAK.sub(" ", text)
    text = _TAG.sub("", text)
    return html.unescape(text).strip()


def parse_line(line: str) -> Event:
    try:
        return _parse(line.strip())
    except Exception:  # a parser bug must not stop the bot
        return Unparsed(line)


def _parse(line: str) -> Event:
    if line == "<!-- update //-->":
        return KeepAlive()
    if line == "<!-- update!! //-->":
        return ChatStarted()
    if line == '<div id="soundcontainer"></div>':
        return KeepAlive()  # follows the start, a placeholder for the login sound; nothing to do
    if "Connection Closed (multiple Login detected)" in line:
        return TakenOver()

    row = _ROW.match(line)
    if row is None:
        return Unparsed(line)
    body = row.group("body")
    if (system := _SYSTEM.search(body)) is not None:
        return SystemMessage(plain(system.group("text")))
    if "Ohne weitere Eingabe erfolgt in 2 Minuten dein Timeout" in body:
        return TimeoutWarning()
    if (room := _ROOM_ENTERED.match(body)) is not None:
        return RoomEntered(html.unescape(room.group("room")))

    time = _TIME.match(body)
    if time is None:
        return Unparsed(line)
    when = time.group("time")
    body = body[time.end() :]

    if (m := _MESSAGE.match(body)) is not None:
        user, guest = _user(m.group("user"), m.group("title"))
        return Message(user, guest, when, plain(_strip_end(m.group("text"))))
    if (m := _WHISPER.match(body)) is not None:
        user, guest = _user(m.group("user"), None)
        kind = OwnWhisper if m.group("own") else Whisper
        return kind(user, guest, when, plain(m.group("text")))
    if (m := _ACTION.match(body)) is not None:
        user, guest = _user(m.group("user"), m.group("title"))
        return Action(user, guest, when, plain(m.group("text")))
    if (m := _COMMENT.match(body)) is not None:
        user, guest = _user(None, m.group("title"))
        return Comment(user, guest, when, plain(m.group("text")))
    if (m := _TEAM.match(body)) is not None:
        return TeamMessage(html.unescape(m.group("user")), False, when, plain(m.group("text")))
    if (m := _CHATBOT.match(body)) is not None:
        by = _BY.search(m.group("text"))
        text = plain(_BY.sub("", m.group("text")))
        if m.group("kind") == "Chat-Bot-PM":
            return ChatBotPM(when, text)
        user, guest = _user(None, m.group("title"))
        if (flood := _FLOOD.match(text)) is not None:
            return FloodWarning(user, guest, when, int(flood.group("number")))
        return ChatBot(user, guest, when, text, None if by is None else html.unescape(by.group("by")))

    title = _TITLE.search(body)
    fluester = _FLUESTER.search(body)
    user, guest = _user(
        None if fluester is None else fluester.group("user"),
        None if title is None else title.group("title"),
    )
    if not user:
        return Unparsed(line)
    if "/gruen.gif" in body:
        if (login := _LOGIN.search(body)) is not None:
            return Login(html.unescape(login.group("user")), guest, when)
        if (room_from := _ROOM_FROM.search(body)) is not None:
            return RoomEnter(user, guest, when, html.unescape(room_from.group("room")))
    if "/rot.gif" in body:
        if (room_to := _ROOM_TO.search(body)) is not None:
            return RoomLeave(user, guest, when, html.unescape(room_to.group("room")))
        return Logout(user, guest, when, "(Browser geschlossen)" in body)
    if (away := _AWAY.search(body)) is not None:
        return Away(user, guest, when, plain(away.group("reason") or ""))
    if _BACK.search(body) is not None:
        return Back(user, guest, when)
    if "/wuerfel.gif" in body and (dice := _DICE.search(body)) is not None:
        return Dice(user, guest, when, dice.group("result"))
    return Unparsed(line)


def _user(name: str | None, title: str | None) -> tuple[str, bool]:
    """The user name and whether it's a guest, from fluester('…') and/or title="…"."""
    guest = title is not None and title.endswith(" (Gast)")
    if name is None:
        name = (title or "").removesuffix(" (Gast)")
    return html.unescape(name), guest


def _strip_end(text: str) -> str:
    """A message ends with the closing tag of its <font title=…>, except /link messages."""
    return text[: -len("</font>")] if text.lower().endswith("</font>") else text
