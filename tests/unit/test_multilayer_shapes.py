"""Multi-layer shape ``(layers ...)`` + PcbCurve solder_mask_margin (S4 commit H2).

KiCad emits ``(layers ...)`` instead of ``(layer ...)`` when a shape's LSET
spans more than one layer (pcb_io_kicad_sexpr.cpp:1094-1097), and emits
``solder_mask_margin`` for all shape kinds including bezier curves (1099-1105).
No corpus exemplar exists (library footprints have no multi-layer shapes), so
these use synthetic strings. The shape loaders now hard-error on unknown tokens
(ADR-017), so a multi-layer shape that previously loaded lossily is exercised
here for correctness rather than silently corrupting to ``(layer "")``.
"""

from __future__ import annotations

import pytest

from eedesigncore.common.pcb_family import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbLine,
    PcbPoly,
    PcbRect,
)
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint

_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
"""


def _make(extra: str) -> str:
    return _HEAD + extra + "\n\t(embedded_fonts no)\n)\n"


# ---------------------------------------------------------------------------
# Multi-layer (layers ...) — one per geometry
# ---------------------------------------------------------------------------

_ML_LINE = _make(
    "\t(fp_line\n"
    "\t\t(start 0.5 0.5)\n"
    "\t\t(end 2.5 0.5)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    '\t\t(layers "F.Cu" "B.Cu")\n'
    '\t\t(uuid "line-uuid-1")\n'
    "\t)"
)

_ML_RECT = _make(
    "\t(fp_rect\n"
    "\t\t(start 0 0)\n"
    "\t\t(end 5 3)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    "\t\t(fill no)\n"
    '\t\t(layers "F.Cu" "B.Cu")\n'
    '\t\t(uuid "rect-uuid-1")\n'
    "\t)"
)

_ML_CIRCLE = _make(
    "\t(fp_circle\n"
    "\t\t(center 0 0)\n"
    "\t\t(end 1 0)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    "\t\t(fill no)\n"
    '\t\t(layers "*.Cu")\n'
    '\t\t(uuid "circle-uuid-1")\n'
    "\t)"
)

_ML_ARC = _make(
    "\t(fp_arc\n"
    "\t\t(start 0 0)\n"
    "\t\t(mid 1 -1)\n"
    "\t\t(end 2 0)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    '\t\t(layers "F.Cu" "In1.Cu" "B.Cu")\n'
    '\t\t(uuid "arc-uuid-1")\n'
    "\t)"
)

_ML_POLY = _make(
    "\t(fp_poly\n"
    "\t\t(pts\n\t\t\t(xy 0 0) (xy 1 0) (xy 1 1)\n\t\t)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    "\t\t(fill no)\n"
    '\t\t(layers "F.Cu" "B.Cu")\n'
    '\t\t(uuid "poly-uuid-1")\n'
    "\t)"
)

_ML_CURVE = _make(
    "\t(fp_curve\n"
    "\t\t(pts\n\t\t\t(xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0)\n\t\t)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    '\t\t(layers "F.Cu" "B.Cu")\n'
    '\t\t(uuid "curve-uuid-1")\n'
    "\t)"
)


@pytest.mark.parametrize(
    ("sexpr", "cls", "expected_layers"),
    [
        (_ML_LINE, PcbLine, ["F.Cu", "B.Cu"]),
        (_ML_RECT, PcbRect, ["F.Cu", "B.Cu"]),
        (_ML_CIRCLE, PcbCircle, ["*.Cu"]),
        (_ML_ARC, PcbArc, ["F.Cu", "In1.Cu", "B.Cu"]),
        (_ML_POLY, PcbPoly, ["F.Cu", "B.Cu"]),
        (_ML_CURVE, PcbCurve, ["F.Cu", "B.Cu"]),
    ],
)
class TestMultiLayerShapes:
    def test_layers_parsed(self, sexpr: str, cls: type, expected_layers: list[str]) -> None:
        fp = Footprint.from_string(sexpr)
        shape = next(d for d in fp.drawings if isinstance(d, cls))
        assert shape.layers == expected_layers

    def test_layers_byte_identical_round_trip(
        self, sexpr: str, cls: type, expected_layers: list[str]
    ) -> None:
        fp = Footprint.from_string(sexpr)
        assert fp.to_string() == sexpr

    def test_layers_supersedes_layer_token(
        self, sexpr: str, cls: type, expected_layers: list[str]
    ) -> None:
        out = Footprint.from_string(sexpr).to_string()
        assert "(layers " in out
        # single (layer ...) must NOT be emitted for a multi-layer shape
        assert "\t\t(layer " not in out


# ---------------------------------------------------------------------------
# PcbCurve solder_mask_margin (previously unmodeled)
# ---------------------------------------------------------------------------

_CURVE_SMM = _make(
    "\t(fp_curve\n"
    "\t\t(pts\n\t\t\t(xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0)\n\t\t)\n"
    "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type default)\n\t\t)\n"
    '\t\t(layer "F.Cu")\n'
    "\t\t(solder_mask_margin 0.05)\n"
    '\t\t(uuid "curve-uuid-1")\n'
    "\t)"
)


class TestPcbCurveSolderMaskMargin:
    def test_solder_mask_margin_parsed(self) -> None:
        fp = Footprint.from_string(_CURVE_SMM)
        curve = next(d for d in fp.drawings if isinstance(d, PcbCurve))
        assert curve.solder_mask_margin == pytest.approx(0.05)

    def test_solder_mask_margin_byte_identical_round_trip(self) -> None:
        fp = Footprint.from_string(_CURVE_SMM)
        assert fp.to_string() == _CURVE_SMM


# ---------------------------------------------------------------------------
# Shape loops now hard-error on unknown tokens (ADR-017)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    ["fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve"],
)
def test_unknown_shape_token_raises(token: str) -> None:
    body = "\t\t(pts\n\t\t\t(xy 0 0)\n\t\t)\n" if token in ("fp_poly", "fp_curve") else ""
    sexpr = _make(f"\t({token}\n{body}\t\t(BOGUS_SHAPE_TOKEN yes)\n\t)")
    with pytest.raises(KicadParseError, match=f"Unknown token in {token}"):
        Footprint.from_string(sexpr)
