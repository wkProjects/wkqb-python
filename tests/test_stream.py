from wkqb.stream import MAX_LINE, LineSplitter


def test_lines_across_chunks() -> None:
    splitter = LineSplitter()
    assert splitter.feed(b"<!-- update!! //-->\n<div id") == ["<!-- update!! //-->"]
    assert splitter.feed(b'="soundcontainer"></div>\n<table') == ['<div id="soundcontainer"></div>']
    assert splitter.feed(b"></table>\n\n") == ["<table></table>", ""]
    assert splitter.close() == []


def test_utf8_split_at_every_byte() -> None:
    data = "Grüße 😀 verlässt\n".encode()
    for cut in range(len(data) + 1):
        splitter = LineSplitter()
        assert splitter.feed(data[:cut]) + splitter.feed(data[cut:]) == ["Grüße 😀 verlässt"]


def test_byte_by_byte() -> None:
    splitter = LineSplitter()
    lines = []
    for byte in b"eins\nzwei\r\ndrei":
        lines += splitter.feed(bytes([byte]))
    assert lines == ["eins", "zwei"]
    assert splitter.close() == ["drei"]
    assert splitter.close() == []


def test_invalid_utf8_is_replaced() -> None:
    splitter = LineSplitter()
    assert splitter.feed("zurück\n".encode("latin-1")) == ["zur�ck"]


def test_overlong_lines_are_cut() -> None:
    splitter = LineSplitter()
    assert splitter.feed(b"x" * 100) == []
    [line] = splitter.feed(b"x" * MAX_LINE)
    assert len(line) == MAX_LINE
    assert splitter.feed(b"y\n") == ["y"]
