"""Placeholders in the texts configured in the web interface.

    %USER% %TIME% %PARAM% %SATZ% %THIS% %TOTAL% ...   replaced by the given values (any case)
    %RANDOM%                                          a random number between 0 and 1
    R{Text1%OR%Text2}/R                               one of the texts, chosen at random
    $1, $2, ...                                       groups of a regex match (KI)

Values are inserted in a single pass, so a value that contains a placeholder (e.g. a %PARAM% of
"%USER%" or "R{a%OR%b}/R") is not replaced again. Values that come from the chat are HTML-escaped:
the bot may be a chat admin, whose messages can contain HTML, and must not let others send markup
through it. The configured texts themselves can contain HTML.
"""

import html
import random
import re
from collections.abc import Mapping

_CHOICE = re.compile(r"R\{(?P<options>.+?)\}/R", re.DOTALL)
_PLACEHOLDER = re.compile(r"%(?P<name>[A-Za-z]+)%|\$(?P<group>\d)")


def render(
    template: str,
    values: Mapping[str, str | int] | None = None,
    *,
    chat_values: Mapping[str, str] | None = None,
    groups: Mapping[int, str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """The template with its placeholders replaced.

    values: trusted values like %THIS%; chat_values: from chat users (escaped), like %USER%, %PARAM%;
    groups: regex groups for $1..$9 (escaped). Unknown placeholders stay as they are.
    """
    rng = rng or random.Random()
    names = {k.upper(): str(v) for k, v in (values or {}).items()}
    names |= {k.upper(): html.escape(v) for k, v in (chat_values or {}).items()}
    escaped_groups = {k: html.escape(v) for k, v in (groups or {}).items()}

    # the choices first, so a chosen text can contain placeholders
    text = _CHOICE.sub(lambda m: rng.choice(m.group("options").split("%OR%")), template)

    def replace(match: re.Match[str]) -> str:
        if (group := match.group("group")) is not None:
            return escaped_groups.get(int(group), match.group(0))
        name = match.group("name").upper()
        if name == "RANDOM":
            return str(rng.random())
        return names.get(name, match.group(0))

    return _PLACEHOLDER.sub(replace, text)
