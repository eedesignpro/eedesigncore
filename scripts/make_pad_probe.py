"""Build a board holding one of every pad shape, for the kicad-cli parity oracle.

Every case is a separate footprint on a 20 mm grid, so a stray primitive can be
traced back to one pad by position alone. A real corpus board will not do: it
carries whatever shapes its footprints happened to use, and the shapes that
matter most for fidelity -- a trapezoid, a chamfered rectangle, a custom pad --
are the rarest in practice.

The output is committed at ``tests/fixtures/pcb/pad_shapes_probe.kicad_pcb``.
Regenerate it only when a case is added, and re-measure the oracle afterwards.

    .venv/Scripts/python.exe scripts/make_pad_probe.py out.kicad_pcb

Then export the reference with KiCad's own tool and compare:

    kicad-cli pcb export svg --exclude-drawing-sheet --page-size-mode 1
"""

from __future__ import annotations

import sys
from pathlib import Path

from eedesigncore.common.pcb_family.enums import ChamferPosition, PadShape, PadType
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintPlacement,
    Pad,
    PrimCircle,
    PrimPoly,
    PrimRect,
)
from eedesigncore.pcb.models import Board

CELL = 20.0
COLS = 5
ORIGIN = (30.0, 30.0)


def uid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


def cases() -> list[tuple[str, Pad]]:
    out: list[tuple[str, Pad]] = []

    out.append(
        (
            "circle",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.circle,
                size=(4.0, 4.0),
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "rect",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.rect,
                size=(5.0, 3.0),
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "rect-rot30",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.rect,
                size=(5.0, 3.0),
                angle=30.0,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "oval-wide",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.oval,
                size=(6.0, 3.0),
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "oval-tall",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.oval,
                size=(3.0, 6.0),
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "oval-rot45",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.oval,
                size=(6.0, 3.0),
                angle=45.0,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "trapezoid",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.trapezoid,
                size=(5.0, 4.0),
                rect_delta=(1.5, 0.0),
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "trapezoid-rot20",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.trapezoid,
                size=(5.0, 4.0),
                rect_delta=(0.0, 1.5),
                angle=20.0,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "roundrect-25",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.roundrect,
                size=(5.0, 3.0),
                roundrect_rratio=0.25,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "roundrect-50",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.roundrect,
                size=(4.0, 4.0),
                roundrect_rratio=0.5,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "roundrect-rot35",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.roundrect,
                size=(5.0, 3.0),
                roundrect_rratio=0.2,
                angle=35.0,
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "chamfer-one",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.roundrect,
                size=(5.0, 3.0),
                roundrect_rratio=0.2,
                chamfer_ratio=0.3,
                chamfer_positions=[ChamferPosition.top_left],
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "chamfer-all",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.roundrect,
                size=(4.0, 4.0),
                roundrect_rratio=0.15,
                chamfer_ratio=0.25,
                chamfer_positions=[
                    ChamferPosition.top_left,
                    ChamferPosition.top_right,
                    ChamferPosition.bottom_left,
                    ChamferPosition.bottom_right,
                ],
                layers=["F.Cu"],
            ),
        )
    )
    out.append(
        (
            "thru-circle",
            Pad(
                number="1",
                pad_type=PadType.thru_hole,
                shape=PadShape.circle,
                size=(4.0, 4.0),
                drill=DrillInfo(width=2.0),
                layers=["*.Cu"],
                remove_unused_layers=False,
            ),
        )
    )
    out.append(
        (
            "offset-rect",
            Pad(
                number="1",
                pad_type=PadType.thru_hole,
                shape=PadShape.rect,
                size=(5.0, 3.0),
                drill=DrillInfo(width=1.5, offset=(1.0, 0.5)),
                layers=["*.Cu"],
                remove_unused_layers=False,
            ),
        )
    )
    out.append(
        (
            "custom-circle-prim",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.custom,
                size=(2.0, 2.0),
                layers=["F.Cu"],
                primitives=[PrimCircle(center=(1.5, 0.0), end=(2.5, 0.0), width=0.0, fill=True)],
            ),
        )
    )
    out.append(
        (
            "custom-poly-prim",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.custom,
                size=(2.0, 2.0),
                layers=["F.Cu"],
                primitives=[
                    PrimPoly(
                        pts=[
                            (-2.0, -2.0),
                            (2.0, -2.0),
                            (0.0, 2.0),
                        ],
                        width=0.0,
                        fill=True,
                    )
                ],
            ),
        )
    )
    out.append(
        (
            "custom-rect-prim",
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.custom,
                size=(2.0, 2.0),
                layers=["F.Cu"],
                primitives=[PrimRect(start=(-2.0, -1.0), end=(2.0, 1.0), width=0.0, fill=True)],
            ),
        )
    )
    return out


def main() -> int:
    out_path = Path(sys.argv[1])
    board = Board()
    footprints = []
    for index, (name, pad) in enumerate(cases()):
        row, col = divmod(index, COLS)
        at = (ORIGIN[0] + col * CELL, ORIGIN[1] + row * CELL)
        pad.id = uid(0x1000 + index)
        footprints.append(
            Footprint(
                fp_id=f"probe:{name}",
                layer="F.Cu",
                at=FootprintPlacement(position=at, angle=0.0),
                uuid=uid(0x2000 + index),
                pads=[pad],
            )
        )
    board.footprints = footprints
    out_path.write_text(board.to_string(), encoding="utf-8")
    print(f"{len(footprints)} footprints -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
