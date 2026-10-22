"""KiCad overbars, subscripts, and superscripts for the built-in stroke font."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from eedesigncore.common.kimath import kiround

if TYPE_CHECKING:
    from eedesigncore.common.font.stroke import StrokeFont


@dataclass(frozen=True)
class Node:
    text: str = ""
    kind: str = ""
    children: tuple[Node, ...] = ()


_URL = re.compile(r"https?://[^ ]+")
_ESCAPE = re.compile(r"\{[A-Za-z_][A-Za-z_0-9]*\}")


def parse(text: str) -> tuple[Node, ...]:
    def sequence(start: int, nested: bool, depth: int = 0) -> tuple[tuple[Node, ...], int]:
        if depth > 256:
            raise ValueError("Text markup nesting exceeds 256 levels.")
        nodes: list[Node] = []
        plain = ""
        index = start
        while index < len(text):
            if nested and text[index] == "}":
                break
            url = _URL.match(text, index) if not nested else None
            escape = _ESCAPE.match(text, index) if nested else None
            if url or escape:
                token = (url or escape).group()  # type: ignore[union-attr]
                plain += token
                index += len(token)
                continue
            if text[index : index + 2] in ("~{", "^{", "_{"):
                children, end = sequence(index + 2, True, depth + 1)
                if end < len(text) and text[end] == "}":
                    if plain:
                        nodes.append(Node(text=plain))
                        plain = ""
                    nodes.append(Node(kind=text[index], children=children))
                    index = end + 1
                    continue
            plain += text[index]
            index += 1
        if plain:
            nodes.append(Node(text=plain))
        return tuple(nodes), index

    return sequence(0, False)[0]


def runs(
    font: StrokeFont, text: str, size: tuple[int, int], *, italic: bool = False
) -> tuple[int, list[list[tuple[float, float]]]]:
    strokes: list[list[tuple[float, float]]] = []

    def walk(nodes: tuple[Node, ...], cursor: int, sub: bool, superscript: bool) -> int:
        for node in nodes:
            start = cursor
            if node.kind:
                cursor = walk(
                    node.children, cursor, sub or node.kind == "_", superscript or node.kind == "^"
                )
                if node.kind == "~":
                    strokes.append(
                        [
                            (start + size[0] * 0.1, -size[1] * 1.23),
                            (cursor - size[0] * 0.1, -size[1] * 1.23),
                        ]
                    )
                continue
            glyph_size = (
                tuple(kiround(value * 0.8) for value in size) if sub or superscript else size
            )
            dy = glyph_size[1] * (0.15 if sub else -0.35) if sub or superscript else 0
            for char in node.text:
                glyph = font.glyph(char)
                for stroke in glyph.strokes:
                    strokes.append(
                        [
                            (
                                cursor
                                + x * glyph_size[0]
                                - (y * glyph_size[1] / 8 if italic else 0),
                                y * glyph_size[1] + dy,
                            )
                            for x, y in stroke
                        ]
                    )
                cursor += font.advance(char, (glyph_size[0], glyph_size[1]))
        return cursor

    return walk(parse(text), 0, False, False), strokes


def placed_runs(
    font: StrokeFont,
    text: str,
    size: tuple[int, int],
    position: tuple[int, int],
    thickness: int,
    angle: float,
    mirror: bool,
    italic: bool,
    halign: str,
    valign: str,
) -> list[list[tuple[int, int]]]:
    from eedesigncore.common.font.stroke import _text_start

    _, strokes = runs(font, text, size, italic=italic)
    x0, y0 = _text_start(font, text, size, position, thickness, halign, valign)
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    result = []
    for stroke in strokes:
        points = []
        for x, y in stroke:
            x, y = x + x0, y + y0
            if mirror:
                x = 2 * position[0] - x
            dx, dy = x - position[0], y - position[1]
            points.append(
                (
                    kiround(position[0] + dx * cosine + dy * sine),
                    kiround(position[1] - dx * sine + dy * cosine),
                )
            )
        if len(points) > 1:
            result.append(points)
    return result


def words(text: str) -> list[str]:
    def original(node: Node) -> str:
        return (
            node.text
            if not node.kind
            else node.kind + "{" + "".join(original(child) for child in node.children) + "}"
        )

    result = []
    for node in parse(text):
        if node.kind:
            result.append(original(node))
        else:
            result.extend(re.findall(r"[^ ]+ ?| ", node.text))
    return result
