"""Mask and paste inheritance and aperture geometry from KiCad source rules."""

import xml.etree.ElementTree as ET

import pytest

from eedesigncore.common.pcb_family.enums import PadShape
from eedesigncore.common.pcb_family.footprint_scoped import Padstack, PadstackLayer, PadstackMode
from eedesigncore.mod.models import Footprint, FootprintPlacement, Pad, PrimCircle, PrimRect
from eedesigncore.pcb.models import Board, Setup
from eedesigncore.render import BoardContent, LayerStyle, ViewBox, render_board_svg
from eedesigncore.render.apertures import pad_aperture_margin
from eedesigncore.render.pads import pad_outline


def make_pad(**values: object) -> Pad:
    return Pad.model_validate(
        dict(shape="rect", size=(4, 2), layers=["F.Cu", "F.Mask", "F.Paste"], **values)
    )


@pytest.mark.parametrize("layer", ["F.Mask", "B.Mask"])
def test_mask_inheritance_and_explicit_zero(layer: str) -> None:
    pad = make_pad()
    footprint = Footprint(solder_mask_margin=0.2)
    setup = Setup(pad_to_mask_clearance=0.3)
    assert pad_aperture_margin(pad, layer, setup=setup) == (0.3, 0.3)
    assert pad_aperture_margin(pad, layer, footprint=footprint, setup=setup) == (0.2, 0.2)
    pad.solder_mask_margin = 0
    assert pad_aperture_margin(pad, layer, footprint=footprint, setup=setup) == (0, 0)
    assert pad_aperture_margin(pad, layer, footprint=footprint, setup=setup, version=20240201) == (
        0.2,
        0.2,
    )


def test_paste_values_inherit_independently() -> None:
    pad = make_pad(solder_paste_margin=0)
    footprint = Footprint(solder_paste_margin=0.2, solder_paste_margin_ratio=-0.1)
    setup = Setup(pad_to_paste_clearance=0.3, pad_to_paste_clearance_ratio=0.2)
    assert pad_aperture_margin(pad, "F.Paste", footprint=footprint, setup=setup) == (-0.4, -0.2)
    pad.solder_paste_margin = None
    pad.solder_paste_margin_ratio = 0
    assert pad_aperture_margin(pad, "F.Paste", footprint=footprint, setup=setup) == (0.2, 0.2)
    footprint.solder_paste_margin = None
    assert pad_aperture_margin(pad, "F.Paste", footprint=footprint, setup=setup) == (0.3, 0.3)


def test_non_copper_pad_has_no_expansion() -> None:
    pad = make_pad(solder_mask_margin=0.2, solder_paste_margin=0.3)
    pad.layers = ["F.Mask", "F.Paste"]
    for layer in pad.layers:
        assert pad_aperture_margin(pad, layer) == (0, 0)


def test_back_padstack_size_sets_paste_ratio() -> None:
    pad = make_pad(
        solder_paste_margin_ratio=-0.1,
        padstack=Padstack(
            mode=PadstackMode.front_inner_back,
            layers=[PadstackLayer(layer_name="B.Cu", size=(8, 6))],
        ),
    )
    assert pad_aperture_margin(pad, "B.Paste") == (-0.8, -0.6)
    assert pad_aperture_margin(pad, "F.Paste") == (-0.4, -0.2)


@pytest.mark.parametrize("layer", ["F.Mask", "B.Mask", "F.Paste", "B.Paste"])
def test_contraction_can_remove_aperture(layer: str) -> None:
    pad = make_pad(solder_mask_margin=-10, solder_paste_margin=-10)
    assert pad_outline(pad, layer).is_empty()


def test_expanded_rect_has_round_corners_and_copper_is_unchanged() -> None:
    pad = make_pad(solder_mask_margin=0.2)
    copper = pad_outline(pad, "F.Cu")
    mask = pad_outline(pad, "F.Mask")
    assert len(copper.polygons[0]) == 4
    assert len(mask.polygons[0]) > 4
    assert max(x for x, _ in mask.polygons[0]) == pytest.approx(2.2)
    assert max(y for _, y in mask.polygons[0]) == pytest.approx(1.2)
    assert pad.size == (4, 2)


def test_rotated_paste_uses_local_axes() -> None:
    pad = make_pad(angle=90, solder_paste_margin_ratio=-0.1)
    points = pad_outline(pad, "F.Paste").polygons[0]
    assert max(x for x, _ in points) == pytest.approx(0.8)
    assert max(y for _, y in points) == pytest.approx(1.6)


def test_uniform_roundrect_offset_uses_kicad_10_0_4_radius_correction() -> None:
    pad = make_pad(solder_mask_margin=0.2)
    pad.shape = PadShape.roundrect
    pad.size = (5, 3)
    pad.roundrect_rratio = 0.25
    points = pad_outline(pad, "F.Mask").polygons[0]
    # Measured with kicad-cli 10.0.4: original radius 0.75 plus clearance 0.2.
    assert points[0] == pytest.approx((-2.7, -0.75))


def test_custom_mask_clamps_contraction_to_anchor() -> None:
    pad = make_pad(solder_mask_margin=-0.2)
    pad.shape = PadShape.custom
    pad.size = (0, 0)
    assert pad_aperture_margin(pad, "F.Mask") == (0, 0)


def test_oversized_polygon_is_rejected_before_native_offset() -> None:
    from eedesigncore.render.pad_polygons import offset_polygons

    with pytest.raises(ValueError, match="coordinate range"):
        offset_polygons([[(0, 0), (2**62, 0), (0, 2**62)]], 1000)


def test_custom_union_precedes_contraction() -> None:
    pad = make_pad(solder_paste_margin=-0.1)
    pad.shape = PadShape.custom
    pad.size = (0, 0)
    pad.primitives = [
        PrimRect(start=(-2, -1), end=(0, 1), fill=True),
        PrimRect(start=(0, -1), end=(2, 1), fill=True),
    ]
    outline = pad_outline(pad, "F.Paste")
    assert len(outline.compound_polygons) == 1
    assert max(x for x, _ in outline.compound_polygons[0]) == pytest.approx(1.9)
    assert min(x for x, _ in outline.compound_polygons[0]) == pytest.approx(-1.9)


def test_custom_paste_preserves_hole_in_svg_and_input() -> None:
    pad = make_pad(solder_paste_margin=0.1)
    pad.shape = PadShape.custom
    pad.size = (0, 0)
    pad.primitives = [PrimCircle(center=(0, 0), end=(2, 0), width=0.4)]
    board = Board(footprints=[Footprint(pads=[pad])])
    before = board.model_dump()
    svg = render_board_svg(
        board,
        ["F.Paste"],
        ViewBox(-5, -5, 10, 10),
        style=LayerStyle({}, default_color="#ffffff"),
        content={BoardContent.pads},
    )
    root = ET.fromstring(svg["F.Paste"])
    path = root.find(".//{http://www.w3.org/2000/svg}path")
    assert path is not None
    assert path.attrib["fill-rule"] == "evenodd"
    assert path.attrib["d"].count("M") == 2
    assert board.model_dump() == before


def test_public_export_applies_footprint_and_board_settings() -> None:
    pad = make_pad()
    footprint = Footprint(
        pads=[pad], at=FootprintPlacement(position=(10, 20)), solder_mask_margin=-0.1
    )
    board = Board(footprints=[footprint], setup=Setup(pad_to_paste_clearance=-0.2))
    svg = render_board_svg(
        board,
        ["F.Mask", "F.Paste"],
        ViewBox(0, 0, 30, 30),
        style=LayerStyle({}, default_color="#fff"),
        content={BoardContent.pads},
    )
    assert "8.1000" in svg["F.Mask"]
    assert "8.2000" in svg["F.Paste"]
    for page in svg.values():
        ET.fromstring(page)
