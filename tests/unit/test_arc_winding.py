"""Unit tests for the arc winding normalisation that runs at LOAD time (ADR-067).

``EDA_SHAPE::SetArcGeometry`` (``common/eda_shape.cpp:1180-1205``) swaps the two
ends of an arc whose input winding disagrees with the KiCad internal winding, and
every S-expression parser that builds an ``EDA_SHAPE`` arc calls it. These tests
cover the port, every load site that must apply it, and the two arc kinds that
must NOT be touched.

The empirical anchor for the degenerate case is a real corpus library, named by
its corpus-relative path suffix rather than an operator machine path (LOW-11):
``Symbols/EPC_FETs.kicad_sym`` in a private KiCad 7 library set
holds arcs whose three points are exactly collinear. ``CalcArcCenter`` puts their
centre on the ``int`` rail, and every later subtraction WRAPS. KiCad keeps those
arcs as written, and so must this port.
"""

from __future__ import annotations

import pytest

from eedesigncore.common.arcs import (
    arc_mid_iu,
    arc_winding_swaps,
    normalize_arc_iu,
    normalize_arc_mm,
)
from eedesigncore.common.pcb_family.loaders import PCB_IU_PER_MM
from eedesigncore.common.pcb_family.shapes import PcbArc
from eedesigncore.common.sch_family.shapes import SchArc
from eedesigncore.mod.models import Footprint, PrimArc
from eedesigncore.pcb.models import Board
from eedesigncore.sch.models import Schematic
from eedesigncore.sym.models import SymbolLib

PCB_IU = 1e6
SCH_IU = 1e4


class TestWindingDetection:
    def test_a_disagreeing_winding_swaps(self) -> None:
        # Screen coordinates run Y down, so a mid BELOW the chord disagrees.
        assert arc_winding_swaps((0, 0), (1_000_000, 1_000_000), (2_000_000, 0)) is True

    def test_an_agreeing_winding_does_not_swap(self) -> None:
        assert arc_winding_swaps((0, 0), (1_000_000, -1_000_000), (2_000_000, 0)) is False

    def test_the_swap_is_the_mirror_of_the_agreeing_case(self) -> None:
        assert arc_winding_swaps((2_000_000, 0), (1_000_000, 1_000_000), (0, 0)) is False

    def test_normalisation_swaps_the_ends_and_keeps_the_mid(self) -> None:
        start, mid, end = normalize_arc_iu((0, 0), (1_000_000, 1_000_000), (2_000_000, 0))
        assert start == (2_000_000, 0)
        assert end == (0, 0)
        assert mid == (1_000_000, 1_000_000)

    def test_normalisation_is_idempotent(self) -> None:
        once = normalize_arc_iu((0, 0), (1_000_000, 1_000_000), (2_000_000, 0))
        assert normalize_arc_iu(*once) == once

    def test_the_calculated_mid_matches_the_stored_mid_of_a_clean_arc(self) -> None:
        assert arc_mid_iu((0, 0), (2_000_000, 0), (1_000_000, 0)) == (1_000_000, -1_000_000)


class TestDegenerateArcs:
    """Collinear input puts the centre on the int rail, where every step wraps."""

    def test_a_collinear_arc_keeps_its_winding(self) -> None:
        # EPC_FETs.kicad_sym, symbol EPC2001C: three points at the same Y. The
        # kicad-cli oracle leaves this arc untouched, so the port must too.
        start, mid, end = (154, -51_040), (77, -51_040), (0, -51_040)
        assert arc_winding_swaps(start, mid, end) is False
        assert normalize_arc_iu(start, mid, end) == (start, mid, end)

    def test_the_mirrored_collinear_arc_also_keeps_its_winding(self) -> None:
        start, mid, end = (0, -51_040), (77, -51_040), (154, -51_040)
        assert arc_winding_swaps(start, mid, end) is False

    def test_a_fully_degenerate_arc_at_the_origin_is_handled(self) -> None:
        # SetArcGeometry zeroes its mid cache, so this one arc hits the cached
        # branch of GetArcMid. It must not raise.
        assert arc_winding_swaps((0, 0), (0, 0), (0, 0)) is False


class TestMillimetreWrapper:
    def test_an_unchanged_arc_returns_the_identical_float_objects(self) -> None:
        start, mid, end = (0.0, 0.0), (1.27, -1.27), (2.54, 0.0)
        result = normalize_arc_mm(start, mid, end, iu_per_mm=PCB_IU)
        assert result == (start, mid, end)

    def test_a_swapped_arc_reuses_the_source_floats_for_the_two_ends(self) -> None:
        start, mid, end = (0.0, 0.0), (1.27, 1.27), (2.54, 0.0)
        new_start, new_mid, new_end = normalize_arc_mm(start, mid, end, iu_per_mm=PCB_IU)
        assert new_start == end
        assert new_end == start
        assert new_mid == mid

    def test_the_inverted_frame_reverses_the_verdict(self) -> None:
        start, mid, end = (0.0, 0.0), (1.27, 1.27), (2.54, 0.0)
        assert normalize_arc_mm(start, mid, end, iu_per_mm=SCH_IU, invert_y=False) != (
            start,
            mid,
            end,
        )
        assert normalize_arc_mm(start, mid, end, iu_per_mm=SCH_IU, invert_y=True) == (
            start,
            mid,
            end,
        )


_FP_HEADER = (
    '(footprint "TEST"\n'
    "\t(version 20260206)\n"
    '\t(generator "pcbnew")\n'
    '\t(generator_version "10.0")\n'
    '\t(layer "F.Cu")\n'
    "\t(duplicate_pad_numbers_are_jumpers no)\n"
)
_FP_TAIL = "\t(embedded_fonts no)\n)\n"


def _footprint(body: str) -> Footprint:
    return Footprint.from_string(_FP_HEADER + body + _FP_TAIL)


class TestFootprintLoadSites:
    def test_a_wrong_winding_fp_arc_normalises_at_load(self) -> None:
        fp = _footprint(
            "\t(fp_arc\n"
            "\t\t(start 0 0)\n"
            "\t\t(mid 1 1)\n"
            "\t\t(end 2 0)\n"
            "\t\t(stroke (width 0.12) (type default))\n"
            '\t\t(layer "F.SilkS")\n'
            "\t)\n"
        )
        arc = next(d for d in fp.drawings if isinstance(d, PcbArc))
        assert (arc.start, arc.mid, arc.end) == ((2.0, 0.0), (1.0, 1.0), (0.0, 0.0))

    def test_a_right_winding_fp_arc_is_untouched(self) -> None:
        fp = _footprint(
            "\t(fp_arc\n"
            "\t\t(start 0 0)\n"
            "\t\t(mid 1 -1)\n"
            "\t\t(end 2 0)\n"
            "\t\t(stroke (width 0.12) (type default))\n"
            '\t\t(layer "F.SilkS")\n'
            "\t)\n"
        )
        arc = next(d for d in fp.drawings if isinstance(d, PcbArc))
        assert (arc.start, arc.mid, arc.end) == ((0.0, 0.0), (1.0, -1.0), (2.0, 0.0))

    def test_a_pad_primitive_arc_normalises_too(self) -> None:
        fp = _footprint(
            '\t(pad "1" smd custom\n'
            "\t\t(at 0 0)\n"
            "\t\t(size 1 1)\n"
            '\t\t(layers "F.Cu")\n'
            "\t\t(options (clearance outline) (anchor rect))\n"
            "\t\t(primitives\n"
            "\t\t\t(gr_arc (start -0.3 0) (mid 0 0.3) (end 0.3 0) (width 0.08))\n"
            "\t\t)\n"
            "\t)\n"
        )
        primitives = fp.pads[0].primitives
        assert primitives is not None
        prim = next(p for p in primitives if isinstance(p, PrimArc))
        assert prim.start == (0.3, 0.0)
        assert prim.end == (-0.3, 0.0)


class TestBoardLoadSites:
    def test_a_wrong_winding_gr_arc_normalises_at_load(self) -> None:
        board = Board.from_string(
            '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
            " (gr_arc (start 0 0) (mid 1 1) (end 2 0) (stroke (width 0.1) (type solid))"
            ' (layer "F.SilkS") (uuid "a1")) (embedded_fonts no))'
        )
        arc = next(d for d in board.drawings if isinstance(d, PcbArc))
        assert (arc.start, arc.end) == ((2.0, 0.0), (0.0, 0.0))

    def test_a_track_arc_is_never_normalised(self) -> None:
        # PCB_ARC takes its points through plain setters, with no SetArcGeometry
        # (pcb_io_kicad_sexpr_parser.cpp:7195-7215).
        board = Board.from_string(
            '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
            ' (arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2) (layer "F.Cu") (net 0)'
            ' (uuid "t1")) (embedded_fonts no))'
        )
        track = board.tracks[0]
        assert (track.start, track.mid, track.end) == ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0))

    def test_an_arc_inside_a_pts_chain_is_never_normalised(self) -> None:
        # A pts arc becomes a SHAPE_ARC in a SHAPE_LINE_CHAIN, not an EDA_SHAPE.
        board = Board.from_string(
            '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
            " (gr_poly (pts (xy 0 0) (arc (start 0 0) (mid 1 1) (end 2 0)) (xy 2 0))"
            ' (stroke (width 0.1) (type solid)) (fill yes) (layer "F.Cu") (uuid "p1"))'
            " (embedded_fonts no))"
        )
        text = board.to_string()
        assert "(start 0 0)" in text
        assert "(end 2 0)" in text


_SYM_HEADER = (
    "(kicad_symbol_lib\n"
    "\t(version 20251024)\n"
    '\t(generator "kicad_symbol_editor")\n'
    '\t(generator_version "10.0")\n'
)


def _symbol_lib(arc_body: str) -> SymbolLib:
    return SymbolLib.from_string(
        _SYM_HEADER + '\t(symbol "A"\n'
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(in_pos_files yes)\n"
        "\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        '\t\t(symbol "A_1_1"\n' + arc_body + "\t\t)\n\t)\n)\n"
    )


class TestSymbolLoadSite:
    def test_a_wrong_winding_symbol_arc_normalises_at_load(self) -> None:
        # A library symbol negates Y on write, so the disagreeing mid sits ABOVE
        # the chord in file space.
        lib = _symbol_lib(
            "\t\t\t(arc\n"
            "\t\t\t\t(start 0 0)\n"
            "\t\t\t\t(mid 1 -1)\n"
            "\t\t\t\t(end 2 0)\n"
            "\t\t\t\t(stroke (width 0) (type default))\n"
            "\t\t\t\t(fill (type none))\n"
            "\t\t\t)\n"
        )
        arc = next(i for i in lib.symbols[0].units[0].items if isinstance(i, SchArc))
        assert (arc.start, arc.mid, arc.end) == ((2.0, 0.0), (1.0, -1.0), (0.0, 0.0))

    def test_a_right_winding_symbol_arc_is_untouched(self) -> None:
        lib = _symbol_lib(
            "\t\t\t(arc\n"
            "\t\t\t\t(start 0 0)\n"
            "\t\t\t\t(mid 1 1)\n"
            "\t\t\t\t(end 2 0)\n"
            "\t\t\t\t(stroke (width 0) (type default))\n"
            "\t\t\t\t(fill (type none))\n"
            "\t\t\t)\n"
        )
        arc = next(i for i in lib.symbols[0].units[0].items if isinstance(i, SchArc))
        assert (arc.start, arc.mid, arc.end) == ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0))


_SCH_HEADER = (
    "(kicad_sch\n"
    "\t(version 20260306)\n"
    '\t(generator "eeschema")\n'
    '\t(generator_version "10.0")\n'
    '\t(uuid "u")\n'
    '\t(paper "A4")\n'
    "\t(lib_symbols)\n"
)


class TestSchematicLoadSite:
    def test_a_schematic_arc_uses_the_uninverted_frame(self) -> None:
        # sch_io_kicad_sexpr.cpp:1317-1318 passes aInvertY false for a schematic
        # arc, so the same three points swap here and not in a library symbol.
        sch = Schematic.from_string(
            _SCH_HEADER + "\t(arc\n"
            "\t\t(start 0 0)\n"
            "\t\t(mid 1 1)\n"
            "\t\t(end 2 0)\n"
            "\t\t(stroke (width 0) (type default))\n"
            "\t\t(fill (type none))\n"
            '\t\t(uuid "a1")\n'
            "\t)\n)\n"
        )
        arc = next(s for s in sch.graphic_shapes if isinstance(s, SchArc))
        assert (arc.start, arc.mid, arc.end) == ((2.0, 0.0), (1.0, 1.0), (0.0, 0.0))


class TestNormalisedOutputIsAFixedPoint:
    @pytest.mark.parametrize("mid_y", ["1", "-1"])
    def test_writing_a_normalised_arc_reloads_unchanged(self, mid_y: str) -> None:
        fp = _footprint(
            "\t(fp_arc\n"
            "\t\t(start 0 0)\n"
            f"\t\t(mid 1 {mid_y})\n"
            "\t\t(end 2 0)\n"
            "\t\t(stroke (width 0.12) (type default))\n"
            '\t\t(layer "F.SilkS")\n'
            "\t)\n"
        )
        canonical = fp.to_string()
        assert Footprint.from_string(canonical).to_string() == canonical


# ---------------------------------------------------------------------------
# The reordering reported on 2026-09-05, item 3
# ---------------------------------------------------------------------------


def test_a_panel_tab_arc_converges_to_the_winding_kicad_writes() -> None:
    """A consumer reported that a first write reordered arcs and lines on one layer.

    The cause is not a sort fault. The input board was not in KiCad's canonical
    form: this arc is stored with the winding KiCad would swap. Normalising it
    changes the sort key, so the item lands in a different place, and the second
    write is stable.

    The expected values here are not derived. ``kicad-cli pcb upgrade --force``
    was run on that board, and KiCad wrote exactly this mid point. KiCad rounds
    the two ends to the nanometre grid; this library keeps the precision the
    input carried, which is the only difference between the two outputs.
    """
    file_start = (202.6429835, 133.6158061)
    file_mid = (202.6037748, 133.6311201)
    file_end = (202.5874819, 133.6699323)

    start, mid, end = normalize_arc_mm(file_start, file_mid, file_end, iu_per_mm=PCB_IU_PER_MM)

    assert start == file_end
    assert end == file_start
    assert mid == (202.603784, 133.631128)


def test_that_arc_is_a_fixed_point_on_the_second_pass() -> None:
    """Convergent: normalising the normalised arc changes nothing."""
    once = normalize_arc_mm(
        (202.6429835, 133.6158061),
        (202.6037748, 133.6311201),
        (202.5874819, 133.6699323),
        iu_per_mm=PCB_IU_PER_MM,
    )
    assert normalize_arc_mm(*once, iu_per_mm=PCB_IU_PER_MM) == once
