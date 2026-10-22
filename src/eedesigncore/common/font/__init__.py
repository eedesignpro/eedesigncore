"""KiCad's built-in stroke font, and the text metrics that depend on it.

A board file states the text of a dimension but not its shape. KiCad computes the
shape from its own font, and the shape feeds back into the LINE work: a dimension
cuts a gap in its crossbar where the text sits
(``pcbnew/pcb_dimension.cpp:994-1007``). So a renderer that cannot measure text
cannot draw a dimension correctly either.

This package ports the measurement. :class:`StrokeFont` is Newstroke, the font
KiCad uses when a text item names no other, which is every dimension in
practice. :class:`TextMetrics` is the protocol a caller implements to override
it, for a board whose text really does use an installed TrueType face.
"""

from __future__ import annotations

from eedesigncore.common.font.stroke import (
    FONT_OFFSET,
    INTER_CHAR,
    STROKE_FONT_SCALE,
    Glyph,
    StrokeFont,
    TextBox,
    TextMetrics,
    default_font,
    effective_pen_width,
)

__all__ = [
    "FONT_OFFSET",
    "INTER_CHAR",
    "STROKE_FONT_SCALE",
    "Glyph",
    "StrokeFont",
    "TextBox",
    "TextMetrics",
    "default_font",
    "effective_pen_width",
]
