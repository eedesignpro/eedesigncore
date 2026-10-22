"""Editable drawing-sheet templates, independent of page size and document text."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, cast

from pydantic import ConfigDict, Field, PrivateAttr, model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Color
from eedesigncore.common.paths import is_remote_uri


class DrawingSheetCoordinate(KicadModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = 0
    y: float = 0
    anchor: Literal["ltcorner", "lbcorner", "rtcorner", "rbcorner"] = "rbcorner"


class DrawingSheetSetup(KicadModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text_size: tuple[float, float] = (1.5, 1.5)
    line_width: float = Field(default=0, ge=0)
    text_line_width: float = Field(default=0, ge=0)
    left_margin: float = 10
    right_margin: float = 10
    top_margin: float = 10
    bottom_margin: float = 10

    @model_validator(mode="after")
    def positive_text_size(self) -> Self:
        if any(value <= 0 for value in self.text_size):
            raise ValueError("Default drawing-sheet text sizes must be positive.")
        return self


class DrawingSheetItem(KicadModel):
    """One template item. Coordinates and repeat increments use millimeters."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["line", "rect", "tbtext", "polygon", "bitmap"]
    name: str = ""
    comment: str = ""
    position: DrawingSheetCoordinate = DrawingSheetCoordinate()
    end: DrawingSheetCoordinate = DrawingSheetCoordinate()
    page_option: Literal["all", "page1only", "notonpage1"] = "all"
    repeat: int = Field(default=1, ge=1, le=100)
    increment: tuple[float, float] = (0, 0)
    line_width: float = Field(default=0, ge=0)
    rotation: float = 0
    text: str = ""
    text_size: tuple[float, float] = (0, 0)
    bold: bool = False
    italic: bool = False
    font_face: str | None = None
    color: Color | None = None
    justify_h: Literal["left", "center", "right"] = "left"
    justify_v: Literal["top", "center", "bottom"] = "center"
    max_length: float = Field(default=0, ge=0)
    max_height: float = Field(default=0, ge=0)
    increment_label: int = 1
    polygons: list[list[tuple[float, float]]] = []
    image_data: str = ""
    image_scale: float = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if any(value < 0 for value in self.text_size):
            raise ValueError("Drawing-sheet text sizes cannot be negative.")
        if self.image_data:
            import base64

            from eedesigncore.render.images import image_dimensions

            image_dimensions(base64.b64decode(self.image_data, validate=True))
        return self


class DrawingSheet(KicadModel):
    """A .kicad_wks template. Unchanged loaded text retains its source formatting."""

    version: int = Field(default=20231118, ge=0, le=20231118)
    generator: str = "pl_editor"
    generator_version: str = "10.0"
    setup: DrawingSheetSetup = DrawingSheetSetup()
    items: list[DrawingSheetItem] = []
    _source: str | None = PrivateAttr(default=None)
    _snapshot: str | None = PrivateAttr(default=None)
    _newline: str = PrivateAttr(default="\n")
    _bom: bool = PrivateAttr(default=False)

    @classmethod
    def from_string(cls, text: str) -> Self:
        from eedesigncore.wks.io import load_wks

        return cast(Self, load_wks(text))

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        if is_remote_uri(str(path)):
            raise ValueError("Drawing-sheet files require a local path.")
        with Path(path).open(encoding="utf-8", newline="") as stream:
            text = stream.read()
        result = cls.from_string(text.removeprefix("\ufeff"))
        result._bom = text.startswith("\ufeff")
        result._newline = "\r\n" if "\r\n" in text else "\n"
        return result

    def to_string(self) -> str:
        from eedesigncore.wks.io import dump_wks

        return dump_wks(self)

    def to_file(self, path: str | Path) -> None:
        if is_remote_uri(str(path)):
            raise ValueError("Drawing-sheet files require a local path.")
        text = self.to_string()
        with Path(path).open("w", encoding="utf-8", newline=self._newline) as stream:
            stream.write(("\ufeff" if self._bom else "") + text)
