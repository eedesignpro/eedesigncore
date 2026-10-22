"""Newstroke, KiCad's built-in stroke font, and the text box it produces.

Every number here comes from KiCad 10.0.3. Arithmetic runs in integer internal
units and rounds through :func:`kiround`, as it does in the C++, so a measurement
lands on the same unit KiCad would land on.

The chain this module ports, top down:

    EDA_TEXT::GetTextBox            common/eda_text.cpp:742-861
      FONT::StringBoundaryLimits    common/font/font.cpp:451-478
        STROKE_FONT::GetTextAsGlyphs common/font/stroke_font.cpp:202-289
          buildGlyphBoundingBox     common/font/stroke_font.cpp:81-96
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eedesigncore.common.font.stroke_data import FIRST_ORDINAL, GLYPHS
from eedesigncore.common.kimath import kiround

#: Scale factor for a glyph, ``stroke_font.cpp:44``.
STROKE_FONT_SCALE = 1.0 / 21.0

#: Moves the origin to the baseline, ``stroke_font.cpp:47``. Historical: it keeps
#: coordinates of shapes such as ``W`` and ``M`` at or above zero.
FONT_OFFSET = -8

#: Trailing gap removed from the text box, ``stroke_font.cpp:209``.
INTER_CHAR = 0.2

#: A tab locks to the next fourth column, ``stroke_font.cpp:207``.
TAB_WIDTH = 4

#: Italic slant, ``include/font/font.h:62``.
ITALIC_TILT = 1.0 / 8

#: Extra height a STROKE font adds to its box, ``eda_text.cpp:784``. KiCad's own
#: comment calls it a fudge factor, there to catch diacriticals and descenders.
FUDGE_RATIO = 0.17

#: The character a glyph outside the table falls back to, ``stroke_font.cpp:260``.
FALLBACK_CHAR = "?"

_BASE = ord("R")


@dataclass(frozen=True)
class Glyph:
    """One decoded glyph: its advance, and its strokes in font units.

    *strokes* holds one list of points per pen-down run. A point is already
    reduced to roughly -1.0 to +1.0, so a caller scales it by the text size.
    """

    advance: float
    strokes: tuple[tuple[tuple[float, float], ...], ...]

    @property
    def max_y(self) -> float:
        """The largest y over every point, or zero when the glyph has none.

        ``buildGlyphBoundingBox`` (``stroke_font.cpp:81-96``) seeds the box at
        ``(0, 0)`` to ``(advance, 0)`` and then grows only its y, so the END of
        that box is ``(advance, max_y)``. That end is what the text walk reads.
        """
        return max((point[1] for run in self.strokes for point in run), default=0.0)


def _decode(encoded: str) -> Glyph:
    """Decode one glyph entry. ``stroke_font.cpp:132-172``."""
    if len(encoded) < 2:
        return Glyph(advance=0.0, strokes=())

    left = (ord(encoded[0]) - _BASE) * STROKE_FONT_SCALE
    right = (ord(encoded[1]) - _BASE) * STROKE_FONT_SCALE
    advance = right - left

    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for index in range(2, len(encoded) - 1, 2):
        pair = encoded[index : index + 2]
        if pair == " R":
            # RaisePen: the next point starts a new stroke.
            if current:
                runs.append(current)
                current = []
            continue
        x = (ord(pair[0]) - _BASE) * STROKE_FONT_SCALE - left
        y = (ord(pair[1]) - _BASE + FONT_OFFSET) * STROKE_FONT_SCALE
        current.append((x, y))
    if current:
        runs.append(current)

    return Glyph(advance=advance, strokes=tuple(tuple(run) for run in runs))


@dataclass(frozen=True)
class TextBox:
    """An axis-aligned box in internal units, as KiCad's ``BOX2I`` holds one."""

    x: int
    y: int
    width: int
    height: int

    @property
    def end(self) -> tuple[int, int]:
        return (self.x + self.width, self.y + self.height)

    def inflate(self, dx: int, dy: int) -> TextBox:
        """Grow on every side. ``BOX2I::Inflate``, and dy may be negative."""
        return TextBox(
            x=self.x - dx, y=self.y - dy, width=self.width + 2 * dx, height=self.height + 2 * dy
        )

    def corners(self) -> tuple[tuple[int, int], ...]:
        """The four corners, anticlockwise from the origin."""
        end_x, end_y = self.end
        return ((self.x, self.y), (self.x, end_y), (end_x, end_y), (end_x, self.y))


@runtime_checkable
class TextMetrics(Protocol):
    """What a dimension needs of a font.

    A caller overrides this when a board's text uses an installed TrueType face
    and the caller has its metrics. Nothing in this library can read such a face,
    so the default is always the stroke font.
    """

    def text_box(
        self,
        text: str,
        size: tuple[int, int],
        thickness: int,
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> TextBox:
        """Return the box of *text* at the origin, in internal units."""
        ...


def pen_size_for_normal(text_size: int) -> int:
    """``GetPenSizeForNormal``, ``gr_text.cpp:61-64``."""
    return kiround(text_size / 8.0)


def pen_size_for_bold(text_size: int) -> int:
    """``GetPenSizeForBold``, ``gr_text.cpp:37-40``."""
    return kiround(text_size / 5.0)


def clamp_pen_size(pen: int, size: tuple[int, int]) -> int:
    """``ClampTextPenSize``, ``gr_text.cpp:73-79`` and ``:91-95``, non-strict."""
    smallest = min(abs(size[0]), abs(size[1]))
    return min(pen, kiround(smallest * 0.25))


def effective_pen_width(thickness: int, size: tuple[int, int], *, bold: bool = False) -> int:
    """``EDA_TEXT::GetEffectiveTextPenWidth``, ``eda_text.cpp:449-467``.

    A stored thickness of 1 or less is not a thin pen. It means "unset", and
    KiCad substitutes a width derived from the text size.
    """
    pen = thickness
    if pen <= 1:
        pen = pen_size_for_bold(size[0]) if bold else pen_size_for_normal(size[0])
    return clamp_pen_size(pen, size)


class StrokeFont:
    """Newstroke. The font KiCad draws with when a text item names no other."""

    def __init__(self, glyphs: tuple[str, ...] = GLYPHS, first: int = FIRST_ORDINAL) -> None:
        self._glyphs = glyphs
        self._decoded: dict[int, Glyph] = {}
        self._first = first
        self._fallback = self._index(FALLBACK_CHAR)

    def _index(self, char: str) -> int:
        return ord(char) - self._first

    def glyph(self, char: str) -> Glyph:
        """Return the glyph of *char*, falling back to ``?``.

        ``GetTextAsGlyphs`` (``stroke_font.cpp:258-263``) substitutes ``?`` for
        any index outside the table it holds, so a character this library did not
        vendor degrades the way a character KiCad lacks degrades.
        """
        index = self._index(char)
        if index < 0 or index >= len(self._glyphs):
            index = self._fallback
        if index not in self._decoded:
            self._decoded[index] = _decode(self._glyphs[index])
        return self._decoded[index]

    @property
    def space_advance(self) -> float:
        """``space_width`` at ``stroke_font.cpp:216``: the first glyph's width."""
        return self.glyph(chr(self._first)).advance

    def advance(self, text: str, size: tuple[int, int]) -> int:
        """Return where the cursor lands after *text*. ``stroke_font.cpp:230-281``."""
        if any(prefix in text for prefix in ("~{", "^{", "_{")):
            from eedesigncore.common.font.markup import runs

            return runs(self, text, size)[0]
        cursor = 0
        chars = 0
        for char in text:
            if char == "\t":
                chars = (chars // TAB_WIDTH + 1) * TAB_WIDTH - 1
                landing = size[0] * chars + kiround(size[0] * self.space_advance)
                while landing <= cursor:
                    chars += TAB_WIDTH
                    landing += size[0] * TAB_WIDTH
                cursor = landing
            elif char == " ":
                cursor += kiround(size[0] * self.space_advance)
            else:
                cursor += kiround(self.glyph(char).advance * size[0])
            chars += 1
        return cursor

    def single_line_box(self, text: str, size: tuple[int, int]) -> TextBox:
        """The box one line of *text* fills, before any pen inflation.

        ``stroke_font.cpp:283-288``: the origin is the text position, and the end
        drops the trailing inter-character gap and rises by one glyph height.
        """
        end_x = self.advance(text, size) - kiround(size[0] * INTER_CHAR)
        # Normalize: the C++ box runs from (0, -size.y) to (end_x, 0).
        return TextBox(x=0, y=-size[1], width=end_x, height=size[1])

    def string_boundary_limits(
        self, text: str, size: tuple[int, int], thickness: int, *, italic: bool = False
    ) -> tuple[int, int]:
        """The SIZE of *text*. ``FONT::StringBoundaryLimits``, ``font.cpp:451-478``.

        A stroke font inflates by more than half the pen, which KiCad's comment
        says is there to catch diacriticals and descenders.
        """
        box = self.single_line_box(text, size)
        if italic:
            box = TextBox(box.x, box.y, box.width + kiround(size[1] * ITALIC_TILT), box.height)
        grown = box.inflate(kiround(thickness * 1.5), kiround(thickness * 1.5))
        return (abs(grown.width), abs(grown.height))

    def text_strokes(
        self,
        text: str,
        size: tuple[int, int],
        position: tuple[int, int],
        thickness: int,
        *,
        angle: float = 0.0,
        mirror: bool = False,
        italic: bool = False,
        halign: str = "center",
        valign: str = "center",
    ) -> list[list[tuple[int, int]]]:
        """Return the pen-down runs of *text*, in internal units.

        Each run is a polyline the caller strokes with the text's pen width. This
        is ``GetTextAsGlyphs`` (``stroke_font.cpp:230-281``) with the glyph
        branch taken, and ``STROKE_GLYPH::Transform`` (``glyph.cpp:74-111``) on
        every point:

            point *= size
            if italic: point.x -= point.y * tilt
            point += cursor
            if mirror: point.x = origin.x - (point.x - origin.x)
            if angle:  rotate about origin

        *position* is the text ANCHOR, not the cursor. Justification decides the
        gap between them, and the rotation origin stays the anchor.
        """
        if any(prefix in text for prefix in ("~{", "^{", "_{")):
            from eedesigncore.common.font.markup import placed_runs

            return placed_runs(
                self, text, size, position, thickness, angle, mirror, italic, halign, valign
            )
        tilt = ITALIC_TILT if italic else 0.0
        origin = position
        cursor_x, cursor_y = _text_start(self, text, size, position, thickness, halign, valign)
        radians = math.radians(angle)
        sin, cos = math.sin(radians), math.cos(radians)

        runs: list[list[tuple[int, int]]] = []
        chars = 0
        for char in text:
            if char == "\t":
                chars = (chars // TAB_WIDTH + 1) * TAB_WIDTH - 1
                landing = size[0] * chars + kiround(size[0] * self.space_advance)
                while landing <= cursor_x:
                    chars += TAB_WIDTH
                    landing += size[0] * TAB_WIDTH
                cursor_x = landing
                chars += 1
                continue
            if char == " ":
                cursor_x += kiround(size[0] * self.space_advance)
                chars += 1
                continue

            glyph = self.glyph(char)
            for stroke in glyph.strokes:
                run: list[tuple[int, int]] = []
                for gx, gy in stroke:
                    x = gx * size[0]
                    y = gy * size[1]
                    if tilt:
                        x -= y * tilt
                    x += cursor_x
                    y += cursor_y
                    if mirror:
                        x = origin[0] - (x - origin[0])
                    if angle:
                        dx, dy = x - origin[0], y - origin[1]
                        x = origin[0] + dx * cos + dy * sin
                        y = origin[1] - dx * sin + dy * cos
                    run.append((kiround(x), kiround(y)))
                if len(run) > 1:
                    runs.append(run)
            cursor_x += kiround(glyph.advance * size[0])
            chars += 1
        return runs

    def text_box(
        self,
        text: str,
        size: tuple[int, int],
        thickness: int,
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> TextBox:
        """``EDA_TEXT::GetTextBox``, ``eda_text.cpp:742-861``, at the origin.

        The box a CALLER wants is placed and justified, and this returns it
        unplaced: origin at ``(0, 0)``, left and top justified. The dimension code
        offsets it, because only the dimension knows the text position and the
        justification KiCad gave it.

        Multi-line text is out of scope. A dimension's text is one line, and the
        model has no way to hold a second.
        """
        pen = effective_pen_width(thickness, size, bold=bold)
        width, extents_y = self.string_boundary_limits(text, size, pen, italic=italic)

        # A stroke font grows its height by the fudge factor (``:784-787``).
        height = extents_y + kiround(extents_y * FUDGE_RATIO)

        # An overbar adds a sixth (``:792-793`` and ``:812``). It is a sixth of
        # the UNFUDGED height, because `overbarOffset` is computed from `extents`
        # at :793 and only added to `textsize` at :812.
        if "~{" in text:
            height += extents_y // 6

        return TextBox(x=0, y=0, width=width, height=height)


#: Vertical fudge KiCad applies to match its 6.0 positioning, ``font.cpp:203``.
LINE_HEIGHT_RATIO = 1.17

#: Two more 6.0 positioning fudges for a stroke font, ``font.cpp:214-215``.
STROKE_X_FUDGE = 1.52
STROKE_Y_FUDGE = 0.052


def _text_start(
    font: StrokeFont,
    text: str,
    size: tuple[int, int],
    position: tuple[int, int],
    thickness: int,
    halign: str,
    valign: str,
) -> tuple[int, int]:
    """Where the glyph cursor begins. ``FONT::getLinePositions``, ``font.cpp:181-247``.

    The stored position is the text ANCHOR. Justification moves the cursor off
    it, so a centred string starts half its advance to the left.
    """
    advance = font.advance(text, size)

    offset_x = kiround(thickness / STROKE_X_FUDGE)
    offset_y = size[1] - kiround(thickness * STROKE_Y_FUDGE)

    height = kiround(size[1] * LINE_HEIGHT_RATIO)
    if valign == "center":
        offset_y -= height // 2
    elif valign == "bottom":
        offset_y -= height

    if halign == "center":
        offset_x = -(advance // 2)
    elif halign == "right":
        offset_x = -(advance + offset_x)

    return (position[0] + offset_x, position[1] + offset_y)


_DEFAULT: StrokeFont | None = None


def default_font() -> StrokeFont:
    """Return the shared stroke font, decoding it on first use.

    Glyphs decode on first use and remain cached for subsequent exports.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = StrokeFont()
    return _DEFAULT


__all__ = [
    "FALLBACK_CHAR",
    "FONT_OFFSET",
    "FUDGE_RATIO",
    "INTER_CHAR",
    "ITALIC_TILT",
    "STROKE_FONT_SCALE",
    "TAB_WIDTH",
    "Glyph",
    "StrokeFont",
    "TextBox",
    "TextMetrics",
    "clamp_pen_size",
    "default_font",
    "effective_pen_width",
    "pen_size_for_bold",
    "pen_size_for_normal",
]
