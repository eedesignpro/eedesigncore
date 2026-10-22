"""SCH-family shared graphic shapes (the ``Symbol`` sub-grammar).

Renamed from the generic ``Arc``/``Circle``/``Rect``/``Bezier``/``Polyline`` in
``sym/models.py`` to ``Sch*`` (arch-plan section 2c): the generic names are too
ambiguous once they sit in shared ``common/``. Token grammar is the SCH form
(``arc``/``circle``/``rectangle``/``bezier``/``polyline`` with
``(fill (type ...))``), distinct from the PCB shapes in
``common/pcb_family/shapes.py``.
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Fill, Stroke


class SchArc(KicadModel):
    """An arc shape (``arc`` token)."""

    private: bool = False
    start: tuple[float, float] = (0.0, 0.0)
    mid: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    id: str | None = None  # uuid, only when non-nil


class SchCircle(KicadModel):
    """A circle shape (``circle`` token)."""

    private: bool = False
    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    id: str | None = None


class SchRect(KicadModel):
    """A rectangle shape (``rectangle`` token)."""

    private: bool = False
    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    corner_radius: float | None = None
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    id: str | None = None


class SchBezier(KicadModel):
    """A cubic Bezier curve (``bezier`` token).

    ``pts`` holds exactly four control points, in the order start, control 1,
    control 2, end. ``formatBezier`` writes those four and no others
    (sch_io_kicad_sexpr_common.cpp:292-301), so construction rejects any other
    count (GAP-015). All 574 corpus beziers carry four points.
    """

    private: bool = False
    pts: list[tuple[float, float]] = []
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    id: str | None = None

    @model_validator(mode="after")
    def _check_four_control_points(self) -> Self:
        if len(self.pts) != 4:
            raise ValueError(
                f"SchBezier needs exactly 4 control points, got {len(self.pts)}. "
                "Supply start, control 1, control 2 and end."
            )
        return self


class SchPolyline(KicadModel):
    """A polyline / polygon (``polyline`` token)."""

    private: bool = False
    pts: list[tuple[float, float]] = []
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    id: str | None = None
