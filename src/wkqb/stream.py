"""Turns the chunks of the chat stream into lines."""

import codecs

#: A line longer than this is cut, so a stream without newlines can't fill the memory
MAX_LINE = 256 * 1024


class LineSplitter:
    """Feed it the chunks as they arrive; it returns the complete lines (UTF-8, without the newline).

    A chunk can end in the middle of a line or of a UTF-8 character; that part waits for the next chunk.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._rest = ""

    def feed(self, chunk: bytes) -> list[str]:
        text = self._rest + self._decoder.decode(chunk)
        *lines, self._rest = text.split("\n")
        if len(self._rest) > MAX_LINE:
            lines.append(self._rest[:MAX_LINE])
            self._rest = ""
        return [line.removesuffix("\r") for line in lines]

    def close(self) -> list[str]:
        """The incomplete last line, if any, when the stream ended."""
        rest = (self._rest + self._decoder.decode(b"", final=True)).removesuffix("\r")
        self._rest = ""
        return [rest] if rest else []
