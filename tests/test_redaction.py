from wkqb.redaction import redact, secrets


def test_secrets_skips_short_and_missing_ones() -> None:
    assert secrets(sid="88AbCdEfGhIjK", password="kurz", other=None) == {"88AbCdEfGhIjK": "<sid>"}


def test_redact() -> None:
    found = {"geheim123": "<password>"}
    value = {"a": ["x geheim123", b"geheim123", ("geheim123", 1)], "b": None, "c": 2.5}
    assert redact(value, found) == {"a": ["x <password>", b"<password>", ("<password>", 1)], "b": None, "c": 2.5}
