"""Source-retaining syntax for custom design rules."""

from __future__ import annotations

from dataclasses import dataclass, field

from eedesigncore.exceptions import KicadParseError

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_NODES = 250_000


@dataclass
class Node:
    source: str
    start: int
    end: int
    value: str | None = None
    children: list[Node] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.source[self.start : self.end]

    def error(self, message: str) -> KicadParseError:
        line = self.source.count("\n", 0, self.start) + 1
        column = self.start - self.source.rfind("\n", 0, self.start)
        return KicadParseError(f"{message} at line {line}, column {column}", line=line)


def quote(value: str) -> str:
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    return (
        '"'
        + "".join(replacements.get(c, f"\\x{ord(c):02x}" if ord(c) < 32 else c) for c in value)
        + '"'
    )


def parse(text: str) -> list[Node]:
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise KicadParseError("Custom rule file exceeds the size limit")
    position = 0
    stack: list[Node] = []
    roots: list[Node] = []
    line_has_token = False
    count = 0
    while position < len(text):
        c = text[position]
        if c in " \t\r\n" or (position == 0 and c == "\ufeff"):
            if c == "\n":
                line_has_token = False
            position += 1
            continue
        start = position
        node = Node(text, start, start)
        if c == "#" and not line_has_token:
            end = text.find("\n", start)
            position = len(text) if end < 0 else end + 1
            continue
        line_has_token = True
        if c == ")":
            if not stack:
                raise node.error("Unexpected closing parenthesis")
            stack.pop().end = position + 1
            position += 1
            continue
        (stack[-1].children if stack else roots).append(node)
        count += 1
        if count > MAX_NODES:
            raise node.error("Custom rule file exceeds the token limit")
        if c == "(":
            if len(stack) >= 100:
                raise node.error("Custom rule nesting exceeds the depth limit")
            stack.append(node)
            position += 1
            continue
        if c == '"':
            position += 1
            decoded = bytearray()
            escapes = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11}
            while position < len(text) and text[position] != '"':
                c = text[position]
                if c in "\r\n":
                    raise node.error("A quoted string cannot cross a line")
                if c != "\\":
                    decoded.extend(c.encode("utf-8"))
                    position += 1
                    continue
                position += 1
                if position >= len(text) or text[position] in "\r\n":
                    raise node.error("Unterminated escape sequence")
                c = text[position]
                if c in escapes or c in '\\"':
                    decoded.append(escapes.get(c, ord(c)))
                    position += 1
                elif c == "x" or c in "01234567":
                    hexadecimal = c == "x"
                    if hexadecimal:
                        position += 1
                    first = position
                    alphabet = "0123456789abcdefABCDEF" if hexadecimal else "01234567"
                    while (
                        position < len(text)
                        and position - first < (2 if hexadecimal else 3)
                        and text[position] in alphabet
                    ):
                        position += 1
                    digits = text[first:position]
                    decoded.append(int(digits, 16 if hexadecimal else 8) % 256 if digits else 120)
                else:
                    decoded.append(92)
            if position == len(text):
                raise node.error("Unterminated quoted string")
            position += 1
            try:
                node.value = decoded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise node.error("Invalid UTF-8 in quoted string") from exc
        else:
            while position < len(text) and text[position] not in ' \t\r\n()"':
                position += 1
            if position == start:
                raise node.error("Unexpected token")
            node.value = text[start:position]
        node.end = position
    if stack:
        raise stack[-1].error("Unclosed parenthesis")
    return roots


def atom(node: Node) -> str:
    if node.value is None:
        raise node.error("Expected a value")
    return node.value


def head(node: Node) -> str:
    if node.value is not None or not node.children:
        raise node.error("Expected a nonempty expression")
    if node.children[0].text.startswith('"'):
        raise node.error("A clause keyword must not be quoted")
    return atom(node.children[0])


def patch(source: str, edits: list[tuple[int, int, str]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, value in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        if start < cursor:
            raise ValueError("Overlapping custom rule edits")
        pieces.extend((source[cursor:start], value))
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def replace_children(node: Node, values: list[str]) -> str:
    edits = [
        (
            child.start - node.start,
            child.end - node.start,
            values[index] if index < len(values) else "",
        )
        for index, child in enumerate(node.children)
    ]
    if len(values) > len(node.children):
        edits.append(
            (len(node.text) - 1, len(node.text) - 1, " " + " ".join(values[len(node.children) :]))
        )
    return patch(node.text, edits)
