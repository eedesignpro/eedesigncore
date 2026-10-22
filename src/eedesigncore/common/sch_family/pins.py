"""SCH-family symbol pins (``Symbol`` sub-grammar)."""

from __future__ import annotations

from eedesigncore.common.base import KicadModel
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape


class PinAlternate(KicadModel):
    """An alternate pin definition (``alternate`` token)."""

    name: str
    electrical_type: PinElectricalType
    shape: PinShape


class Pin(KicadModel):
    """A symbol pin (``pin`` token)."""

    electrical_type: PinElectricalType
    shape: PinShape
    # position is (x, y); angle stored separately
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    length: float = 2.54
    visible: bool = True
    name: str = ""
    name_text_size: float = 1.27
    number: str = ""
    number_text_size: float = 1.27
    alternates: list[PinAlternate] = []
