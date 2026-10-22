"""Unit tests for the canonical PCB-family save order (R4).

Every expected order below comes from a source-verified KiCad rule, cited in
``context/audit/save-canonicalization-sweep-2026-08-13.md`` section 2 and
reproduced in ``common/pcb_family/ordering.py``.

Each test mutates a model into a non-canonical order and asserts the EMITTED
order, which is the only order a consumer sees.
"""

from __future__ import annotations

import re

from eedesigncore.common.models import Property, Stroke, TextAttributes
from eedesigncore.common.pcb_family import (
    Barcode,
    Dimension,
    PcbArc,
    PcbCircle,
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    Variant,
    VariantField,
    Zone,
    ZoneOutlinePolygon,
)
from eedesigncore.common.pcb_family.ordering import (
    FootprintFrame,
    compare_footprint_drawings,
    compare_pads,
    footprint_field_rank,
    layer_ordinal,
    layer_set_seq,
    sort_footprint_drawings,
    sort_footprint_properties,
    sort_group_members,
    sort_pads,
    sort_pcb_points,
    sort_variant_fields,
    sort_variants,
    sort_zones,
)
from eedesigncore.mod.enums import PadShape, PadType
from eedesigncore.mod.models import Footprint, FootprintPlacement, Pad
from eedesigncore.mod.writer import dump_mod

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _pad(number: str, x: float, y: float, **kwargs: object) -> Pad:
    return Pad(
        number=number,
        pad_type=PadType.smd,
        shape=PadShape.rect,
        position=(x, y),
        layers=["F.Cu"],
        **kwargs,  # type: ignore[arg-type]
    )


def _line(start: tuple[float, float], end: tuple[float, float], layer: str) -> PcbLine:
    return PcbLine(start=start, end=end, layer=layer, stroke=Stroke(width=0.1))


def _footprint(**kwargs: object) -> Footprint:
    return Footprint(fp_id="TEST", **kwargs)  # type: ignore[arg-type]


def _emitted_pad_numbers(footprint: Footprint) -> list[str]:
    return re.findall(r'\(pad "([^"]*)"', dump_mod(footprint))


# ---------------------------------------------------------------------------
# Pads: FOOTPRINT::cmp_pads (footprint.cpp:4426-4467)
# ---------------------------------------------------------------------------


def test_unnumbered_pads_sort_before_pad_one_whatever_their_x() -> None:
    """The PQFN-8-EP discriminator (sweep section 2.8, discriminator 1).

    Sixteen unnumbered pads emit before pad ``"1"``, even though pad ``"1"``
    carries the SMALLEST x in the footprint. Position is not the primary key.
    """
    unnumbered = [_pad("", x, y) for x in (-0.55, 0.3, 1.15, 2.0) for y in (-1.15, 0.0, 1.15)]
    numbered = [_pad(str(n), -2.725, -1.905 + 1.27 * (n - 1)) for n in (1, 2, 3, 4)]
    footprint = _footprint(pads=[*numbered, *unnumbered])

    emitted = _emitted_pad_numbers(footprint)

    assert emitted[:12] == [""] * 12
    assert emitted[12:] == ["1", "2", "3", "4"]


def test_unnumbered_pads_sort_among_themselves_by_x_then_y() -> None:
    pads = [_pad("", 1.15, 1.15), _pad("", -0.55, 1.15), _pad("", -0.55, -1.15)]

    assert [pad.position for pad in sort_pads(pads)] == [
        (-0.55, -1.15),
        (-0.55, 1.15),
        (1.15, 1.15),
    ]


def test_pad_nine_sorts_before_pad_ten() -> None:
    """``StrNumCmp`` compares digit runs by value (``footprint.cpp:4428-4429``)."""
    pads = [_pad("10", 0.0, 0.0), _pad("9", 1.0, 0.0), _pad("1", 2.0, 0.0)]

    assert [pad.number for pad in sort_pads(pads)] == ["1", "9", "10"]


def test_equal_numbers_fall_through_to_the_position_key() -> None:
    first = _pad("1", 5.0, 0.0)
    second = _pad("1", -5.0, 0.0)

    assert [pad.position for pad in sort_pads([first, second])] == [(-5.0, 0.0), (5.0, 0.0)]


def test_numbers_that_str_num_cmp_calls_equal_still_reach_the_position_key() -> None:
    """``"01"`` and ``"1"`` differ as strings, so the branch runs and returns 0.

    The C++ only enters the number branch when the STRINGS differ
    (``footprint.cpp:4428``), and it returns the ``StrNumCmp`` verdict from
    inside. A zero verdict is not a decision, so the position keys decide.
    """
    first = _pad("01", 5.0, 0.0)
    second = _pad("1", -5.0, 0.0)

    assert [pad.number for pad in sort_pads([first, second])] == ["1", "01"]


def test_pads_tied_on_number_and_position_sort_by_size_then_shape() -> None:
    big = _pad("1", 0.0, 0.0, size=(2.0, 1.0))
    small = _pad("1", 0.0, 0.0, size=(1.0, 1.0))

    assert [pad.size for pad in sort_pads([big, small])] == [(1.0, 1.0), (2.0, 1.0)]


def test_pads_tied_on_geometry_sort_by_uuid() -> None:
    later = _pad("1", 0.0, 0.0, id="bbbb")
    earlier = _pad("1", 0.0, 0.0, id="aaaa")

    assert [pad.id for pad in sort_pads([later, earlier])] == ["aaaa", "bbbb"]


def test_pad_sort_is_stable_for_fully_tied_pads() -> None:
    first = _pad("1", 0.0, 0.0)
    second = _pad("1", 0.0, 0.0)

    assert [id(pad) for pad in sort_pads([first, second])] == [id(first), id(second)]


def test_padstack_layer_geometry_uses_the_last_differing_layer() -> None:
    """The C++ lambda overwrites its verdict per layer (``footprint.cpp:4446-4458``)."""
    assert compare_pads(_pad("1", 0.0, 0.0, size=(1.0, 2.0)), _pad("1", 0.0, 0.0, size=(1.0, 3.0)))


# ---------------------------------------------------------------------------
# Drawings: FOOTPRINT::cmp_drawings (footprint.cpp:4316-4423)
# ---------------------------------------------------------------------------


def test_layer_outranks_shape_kind() -> None:
    """Sweep discriminator 3: a RECTANGLE on F.CrtYd sits between a POLY and a SEGMENT.

    Layer ids are F.SilkS 5, F.CrtYd 31 and F.Fab 35 (``include/layer_ids.h``).
    A shape-kind-first rule would put the SEGMENTs first.
    """
    poly = PcbPoly(pts=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], layer="F.SilkS")
    rect = PcbRect(start=(-3.55, -2.9), end=(3.55, 2.9), layer="F.CrtYd")
    segment = _line((-3.3, -1.1), (-3.3, 2.4), "F.Fab")

    assert sort_footprint_drawings([segment, rect, poly]) == [poly, rect, segment]


def test_text_emits_after_every_shape_on_the_same_layer() -> None:
    """``PCB_SHAPE_T`` is 5 and ``PCB_TEXT_T`` is 9 (``typeinfo.h:88, 92``)."""
    text = PcbText(content="${REFERENCE}", position=(0.0, 0.0), layer="F.Fab")
    segment = _line((3.3, 2.4), (3.3, -2.4), "F.Fab")

    assert sort_footprint_drawings([text, segment]) == [segment, text]


def test_shapes_tied_on_start_break_the_tie_on_end() -> None:
    """Sweep discriminator 3 rows 6 and 7: equal starts, ascending end x."""
    later = _line((-3.3, -1.1), (-2.0, -2.4), "F.Fab")
    earlier = _line((-3.3, -1.1), (-3.3, 2.4), "F.Fab")

    assert sort_footprint_drawings([later, earlier]) == [earlier, later]


def test_segments_sort_before_a_circle_on_the_same_layer() -> None:
    """``SHAPE_T::SEGMENT`` is 0 and ``CIRCLE`` is 3 (``eda_shape.h:44-53``)."""
    circle = PcbCircle(center=(-9.0, 0.0), end=(-8.0, 0.0), layer="F.Fab")
    segment = _line((8.9, 2.8), (8.9, -2.8), "F.Fab")

    assert sort_footprint_drawings([circle, segment]) == [segment, circle]


def test_arcs_tied_on_start_and_end_break_the_tie_on_the_centre() -> None:
    """``footprint.cpp:4345-4349`` compares the DERIVED centre, not the mid point.

    Both arcs share a start and an end. The shallow one has its centre at
    ``(0, 0.75)`` and the deep one at ``(0, 0)``, so the deep one leads.
    """
    shallow = PcbArc(start=(-1.0, 0.0), mid=(0.0, -0.5), end=(1.0, 0.0), layer="F.Fab")
    deep = PcbArc(start=(-1.0, 0.0), mid=(0.0, -1.0), end=(1.0, 0.0), layer="F.Fab")

    assert compare_footprint_drawings(shallow, deep, FootprintFrame()) > 0
    assert sort_footprint_drawings([shallow, deep]) == [deep, shallow]


def test_polygons_compare_vertex_count_before_vertices() -> None:
    short = PcbPoly(pts=[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)], layer="F.SilkS")
    long = PcbPoly(pts=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], layer="F.SilkS")

    assert sort_footprint_drawings([long, short]) == [short, long]


def test_barcode_sorts_before_a_dimension() -> None:
    """``PCB_BARCODE_T`` is 18 and ``PCB_DIM_ALIGNED_T`` is 19 (``typeinfo.h:101-102``)."""
    dimension = Dimension(layer="Dwgs.User", pts=[(0.0, 0.0), (5.0, 0.0)])
    barcode = Barcode(position=(3.0, 3.0), layer="Dwgs.User", text="ABC")

    assert sort_footprint_drawings([dimension, barcode]) == [barcode, dimension]


def test_drawing_ties_break_on_uuid() -> None:
    later = _line((0.0, 0.0), (1.0, 0.0), "F.Fab")
    later.id = "ffff"
    earlier = _line((0.0, 0.0), (1.0, 0.0), "F.Fab")
    earlier.id = "0000"

    assert sort_footprint_drawings([later, earlier]) == [earlier, later]


def test_drawings_sort_in_board_coordinates_for_a_rotated_footprint() -> None:
    """``cmp_drawings`` reads ABSOLUTE positions, which a rotation reorders.

    At 90 degrees, ``RotatePoint`` maps ``(x, y)`` to ``(y, -x)``
    (``trigo.cpp:241-244``), so the file-space x order inverts.
    """
    low_x = _line((0.0, 5.0), (0.0, 6.0), "F.Fab")
    high_x = _line((5.0, 0.0), (6.0, 0.0), "F.Fab")
    items = [high_x, low_x]

    assert sort_footprint_drawings(items, FootprintFrame()) == [low_x, high_x]
    assert sort_footprint_drawings(items, FootprintFrame((0.0, 0.0), 90.0)) == [high_x, low_x]


def test_a_rotated_board_footprint_emits_in_board_order() -> None:
    low_x = _line((0.0, 5.0), (0.0, 6.0), "F.Fab")
    high_x = _line((5.0, 0.0), (6.0, 0.0), "F.Fab")
    rotated = _footprint(
        uuid="fp-1",
        at=FootprintPlacement(position=(100.0, 100.0), angle=90.0),
        drawings=[low_x, high_x],
    )

    emitted = dump_mod(rotated)

    assert emitted.index("(start 5 0)") < emitted.index("(start 0 5)")


# ---------------------------------------------------------------------------
# Points: PCB_POINT::cmp_points (pcb_point.cpp:62-80)
# ---------------------------------------------------------------------------


def test_points_sort_by_layer_then_x_then_y_then_size() -> None:
    fab = PcbPoint(position=(0.0, 0.0), layer="F.Fab")
    silk_far = PcbPoint(position=(5.0, 0.0), layer="F.SilkS")
    silk_near_big = PcbPoint(position=(0.0, 0.0), size=2.0, layer="F.SilkS")
    silk_near_small = PcbPoint(position=(0.0, 0.0), size=1.0, layer="F.SilkS")

    ordered = sort_pcb_points([fab, silk_far, silk_near_big, silk_near_small])

    assert ordered == [silk_near_small, silk_near_big, silk_far, fab]


# ---------------------------------------------------------------------------
# Zones: FOOTPRINT::cmp_zones (footprint.cpp:4527-4551)
# ---------------------------------------------------------------------------


def _zone(priority: int | None, layer: str, corners: int, uuid: str) -> Zone:
    return Zone(
        priority=priority,
        layer=layer,
        id=uuid,
        polygons=[ZoneOutlinePolygon(pts=[(float(i), 0.0) for i in range(corners)])],
    )


def test_zones_sort_by_priority_then_layer_then_vertex_count() -> None:
    high_priority = _zone(2, "F.Cu", 4, "a")
    low_on_b = _zone(1, "B.Cu", 4, "b")
    low_on_f_many = _zone(1, "F.Cu", 5, "c")
    low_on_f_few = _zone(1, "F.Cu", 4, "d")

    ordered = sort_zones([high_priority, low_on_b, low_on_f_many, low_on_f_few])

    assert [zone.id for zone in ordered] == ["d", "c", "b", "a"]


def test_zone_without_priority_ranks_as_zero() -> None:
    unset = _zone(None, "F.Cu", 4, "unset")
    explicit = _zone(1, "F.Cu", 4, "one")

    assert [zone.id for zone in sort_zones([explicit, unset])] == ["unset", "one"]


# ---------------------------------------------------------------------------
# Groups and group members (pcb_io_kicad_sexpr.cpp:2495, pcb_group.cpp:329-337)
# ---------------------------------------------------------------------------


def test_group_members_emit_in_ascending_uuid_string_order() -> None:
    assert sort_group_members(["ccc", "aaa", "bbb"]) == ["aaa", "bbb", "ccc"]


def test_group_members_are_sorted_on_emission() -> None:
    group = PcbGroup(name="g", id="grp", members=["zzz", "aaa"])
    footprint = _footprint(groups=[group])

    assert '(members "aaa" "zzz")' in dump_mod(footprint)


def test_groups_sort_by_their_members_layer_union_then_uuid() -> None:
    silk_line = _line((0.0, 0.0), (1.0, 0.0), "F.SilkS")
    silk_line.id = "silk"
    fab_line = _line((0.0, 0.0), (1.0, 0.0), "F.Fab")
    fab_line.id = "fab"
    on_fab = PcbGroup(name="fab", id="g-a", members=["fab"])
    on_silk = PcbGroup(name="silk", id="g-b", members=["silk"])
    footprint = _footprint(drawings=[silk_line, fab_line], groups=[on_fab, on_silk])

    emitted = dump_mod(footprint)

    assert emitted.index('(group "silk"') < emitted.index('(group "fab"')


# ---------------------------------------------------------------------------
# Properties (footprint.cpp:105-117, parser :5167-5182)
# ---------------------------------------------------------------------------


def _property(name: str) -> Property:
    return Property(name=name, value="x", attributes=TextAttributes())


def test_mandatory_properties_lead_whatever_the_stored_order() -> None:
    stored = [
        _property(name) for name in ("MPN", "Description", "Value", "Datasheet", "Reference")
    ]

    assert [prop.name for prop in sort_footprint_properties(stored)] == [
        "Reference",
        "Value",
        "Datasheet",
        "Description",
        "MPN",
    ]


def test_user_properties_keep_their_encounter_order() -> None:
    stored = [_property(name) for name in ("Reference", "Zebra", "Alpha", "Value")]

    assert [prop.name for prop in sort_footprint_properties(stored)] == [
        "Reference",
        "Value",
        "Zebra",
        "Alpha",
    ]


def test_the_footprint_mandatory_set_excludes_footprint_itself() -> None:
    """Sweep contradiction C-6: a footprint has no mandatory ``Footprint`` field."""
    assert footprint_field_rank("Footprint", 0) >= 42
    assert footprint_field_rank("Datasheet", 0) == 3


def test_mandatory_name_match_is_case_insensitive() -> None:
    assert footprint_field_rank("REFERENCE", 0) == 1
    assert footprint_field_rank("datasheet", 0) == 3


def test_an_appended_datasheet_emits_in_its_mandatory_slot() -> None:
    footprint = _footprint(
        properties=[_property("Reference"), _property("Value"), _property("MPN")]
    )
    footprint.properties.append(_property("Datasheet"))

    emitted = re.findall(r'\(property "([^"]+)"', dump_mod(footprint))

    assert emitted == ["Reference", "Value", "Datasheet", "MPN"]


# ---------------------------------------------------------------------------
# Variants (footprint.h:963, footprint.h:218)
# ---------------------------------------------------------------------------


def test_variants_sort_by_name_case_insensitively() -> None:
    variants = [Variant(name="beta"), Variant(name="Alpha")]

    assert [variant.name for variant in sort_variants(variants)] == ["Alpha", "beta"]


def test_variant_fields_sort_by_name() -> None:
    fields = [VariantField(name="Zeta", value="1"), VariantField(name="Alpha", value="2")]

    assert [field.name for field in sort_variant_fields(fields)] == ["Alpha", "Zeta"]


# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------


def test_layer_ordinals_match_the_kicad_enum() -> None:
    assert layer_ordinal("F.Cu") == 0
    assert layer_ordinal("F.SilkS") == 5
    assert layer_ordinal("F.CrtYd") == 31
    assert layer_ordinal("F.Fab") == 35


def test_an_unknown_layer_sorts_after_every_known_one() -> None:
    assert layer_ordinal("Not.A.Layer") > layer_ordinal("User.45")


def test_layer_set_seq_is_ascending_and_expands_wildcards() -> None:
    assert layer_set_seq(["B.Cu", "F.Cu"]) == (0, 2)
    assert layer_set_seq(["F&B.Cu"]) == (0, 2)
    assert layer_set_seq(["*.Mask"]) == (1, 3)
    assert layer_set_seq(None) == ()


# ---------------------------------------------------------------------------
# The whole footprint: a reversed model writes the same bytes
# ---------------------------------------------------------------------------


def test_reversing_every_stream_writes_the_same_bytes() -> None:
    """The sort is the identity on canonical input, whatever the stored order."""
    footprint = _footprint(
        properties=[_property("Reference"), _property("Value")],
        drawings=[
            _line((0.0, 0.0), (1.0, 0.0), "F.SilkS"),
            _line((2.0, 0.0), (3.0, 0.0), "F.Fab"),
        ],
        points=[PcbPoint(position=(0.0, 0.0)), PcbPoint(position=(1.0, 0.0))],
        pads=[_pad("1", 0.0, 0.0), _pad("2", 1.0, 0.0)],
    )
    canonical = dump_mod(footprint)

    footprint.properties.reverse()
    footprint.drawings.reverse()
    footprint.points.reverse()
    footprint.pads.reverse()

    assert dump_mod(footprint) == canonical
