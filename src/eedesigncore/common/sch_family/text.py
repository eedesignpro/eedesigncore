"""SCH-family symbol text items (``Symbol`` sub-grammar).

Names kept (not renamed ``Sch*``): per the T2 sym audit these carry a unique
angle convention. ``SymText`` stores its rotation in **tenths of a degree as an
integer**, exactly as written in the file (KiCad emits the raw int-tenths value
for symbol ``text`` and re-reads it verbatim), whereas ``SymTextBox`` uses a
plain degree ``float``. Do not conflate the two: the int-tenths semantics are
load-bearing for byte-identical round-trips.
"""

from __future__ import annotations

from typing import Self

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Fill, Stroke, TextAttributes

# One degree in the tenth-of-a-degree unit SymText.angle uses.
TENTHS_PER_DEGREE = 10


class SymText(KicadModel):
    """A free-standing text item (``text`` token) in a symbol unit.

    ``angle`` is stored in **tenths of a degree** as an integer, matching the raw
    value KiCad writes for symbol ``text`` (unique to this class per T2 audit).

    UNIT TRAP (GAP-016): every sibling angle in the symbol grammar is a plain
    degree float. ``Pin.angle`` and ``Property.angle`` are degrees, and so is
    ``SymTextBox.angle``. Only this class is tenths, because ``saveText`` alone
    emits ``GetTextAngle().AsTenthsOfADegree()`` with ``%d``
    (sch_io_kicad_sexpr_lib_cache.cpp:751). So ``SymText(angle=90)`` renders
    NINE degrees, not ninety.

    Use :meth:`from_degrees` or :attr:`angle_degrees` to work in degrees, and
    keep ``angle`` for the raw on-disk value. The int-tenths field stays as it is
    because byte-identical round-trips depend on it.
    """

    private: bool = False
    content: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    # Angle stored in tenths of degrees as written in the file
    angle: int = 0
    attributes: TextAttributes | None = None

    @property
    def angle_degrees(self) -> float:
        """The rotation in DEGREES, converted from the tenths stored in ``angle``."""
        return self.angle / TENTHS_PER_DEGREE

    @angle_degrees.setter
    def angle_degrees(self, degrees: float) -> None:
        self.angle = round(degrees * TENTHS_PER_DEGREE)

    @classmethod
    def from_degrees(
        cls,
        degrees: float,
        *,
        content: str = "",
        position: tuple[float, float] = (0.0, 0.0),
        private: bool = False,
        attributes: TextAttributes | None = None,
    ) -> Self:
        """Build a ``SymText`` from an angle in DEGREES (GAP-016).

        ``SymText.from_degrees(90, content="A")`` stores ``angle=900``, which
        KiCad reads as ninety degrees.
        """
        return cls(
            private=private,
            content=content,
            position=position,
            angle=round(degrees * TENTHS_PER_DEGREE),
            attributes=attributes,
        )


class SymTextBox(KicadModel):
    """A text box (``text_box`` token) in a symbol unit.

    ``angle`` is a plain degree ``float`` (contrast ``SymText`` int-tenths).
    """

    private: bool = False
    content: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    size: tuple[float, float] = (0.0, 0.0)
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    attributes: TextAttributes | None = None
