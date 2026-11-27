import random

from wkqb.tokens import render


def test_values_in_any_case() -> None:
    assert render("Hallo %USER%, es ist %time%!", {"time": "15:24"}, chat_values={"user": "anna"}) == (
        "Hallo anna, es ist 15:24!"
    )


def test_quote_numbers() -> None:
    assert render("[%THIS%/%TOTAL%]", {"this": 3, "total": 7}) == "[3/7]"


def test_unknown_placeholders_stay() -> None:
    assert render("100% sicher, %FOO% und $1", {}) == "100% sicher, %FOO% und $1"


def test_chat_values_are_escaped_but_the_template_is_not() -> None:
    assert render('<img src="%PARAM%">', chat_values={"param": 'x" onerror="alert(1)'}) == (
        '<img src="x&quot; onerror=&quot;alert(1)">'
    )
    assert render("<b>%SATZ%</b>", chat_values={"satz": "<script>alert(1)</script>"}) == (
        "<b>&lt;script&gt;alert(1)&lt;/script&gt;</b>"
    )


def test_values_are_not_replaced_again() -> None:
    assert render("%PARAM% von %USER%", chat_values={"param": "%USER% R{a%OR%b}/R $1", "user": "anna"}) == (
        "%USER% R{a%OR%b}/R $1 von anna"
    )


def test_random() -> None:
    rng = random.Random(1)
    expected = random.Random(1)
    first, second = expected.random(), expected.random()
    assert render("%RANDOM% %random%", rng=rng) == f"{first} {second}"


def test_choices() -> None:
    results = {
        render("Hallo R{Erde%OR%Welt}/R! Grüße von R{Tom%OR%Jerry}/R", rng=random.Random(seed)) for seed in range(30)
    }
    assert results == {f"Hallo {a}! Grüße von {b}" for a in ("Erde", "Welt") for b in ("Tom", "Jerry")}


def test_a_chosen_text_can_contain_placeholders() -> None:
    assert render("R{Hi %USER%%OR%Hi %USER%}/R", chat_values={"user": "anna"}) == "Hi anna"


def test_regex_groups() -> None:
    assert render("Link von %USER%: $1 ($2, $3)", chat_values={"user": "anna"}, groups={1: "<a>", 2: "x"}) == (
        "Link von anna: &lt;a&gt; (x, $3)"
    )
