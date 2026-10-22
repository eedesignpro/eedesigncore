"""Caller-selected behavior: rule areas, via diameter, and the via flash rule.

Each of the three lets a caller pick KiCad's own answer or a stated step past it.
KiCad's answer is the default in every case, so an existing render does not move.
"""

from __future__ import annotations

import pytest

from eedesigncore.common.pcb_family.board_items import (
    Zone,
    ZoneKeepout,
    ZoneOutlinePolygon,
    ZonePlacement,
)
from eedesigncore.pcb.models import Board, Via, ViaPadstack, ViaPadstackLayer
from eedesigncore.render import (
    ALL_CONTENT,
    DEFAULT_CONTENT,
    KICAD_CONTENT,
    BoardContent,
    LayerStyle,
    ViaDiameter,
    ViewBox,
    render_board_svg,
)
from eedesigncore.render.traversal import is_rule_area, render_items_by_layer
from eedesigncore.render.vias import via_diameter_mm, via_flash


def outline(x: float) -> ZoneOutlinePolygon:
    return ZoneOutlinePolygon(pts=[(x, 10.0), (x + 10.0, 10.0), (x + 10.0, 20.0), (x, 20.0)])


def keepout_zone() -> Zone:
    return Zone(
        layers=["F.Cu"],
        keepout=ZoneKeepout(
            tracks=False, vias=False, pads=True, copper_pour=False, footprints=True
        ),
        polygons=[outline(10.0)],
    )


def pour_zone() -> Zone:
    return Zone(layers=["F.Cu"], polygons=[outline(30.0)])


def drawn(board: Board, content: frozenset[BoardContent]) -> int:
    svg = render_board_svg(
        board,
        ["F.Cu"],
        ViewBox(0.0, 0.0, 60.0, 40.0),
        style=LayerStyle(colors={"F.Cu": "#C83434"}),
        content=content,
    )["F.Cu"]
    return svg.count("<path") + svg.count("<circle")


# ---------------------------------------------------------------------------
# Rule areas
# ---------------------------------------------------------------------------


def test_a_keepout_token_makes_a_zone_a_rule_area() -> None:
    """``pcb_io_kicad_sexpr_parser.cpp:8178-8181``, where the comment says so."""
    assert is_rule_area(keepout_zone())
    assert not is_rule_area(pour_zone())


def test_a_placement_token_also_makes_a_zone_a_rule_area() -> None:
    """The second token, ``pcb_io_kicad_sexpr_parser.cpp:8123-8124``.

    Testing only ``keepout`` would leave a placement rule area drawing as copper.
    """
    zone = Zone(layers=["F.Cu"], placement=ZonePlacement(), polygons=[outline(10.0)])
    assert is_rule_area(zone)


def test_kicad_content_draws_no_rule_area() -> None:
    """KiCad's zone loop skips a rule area, ``plot_board_layers.cpp:848-849``.

    Drawing one puts ink on a copper layer that no KiCad export has. That is a
    defect rather than a gap, so the parity content set must not include it.
    """
    board = Board(zones=[keepout_zone(), pour_zone()])
    assert drawn(board, KICAD_CONTENT) == 1


def test_all_content_draws_the_rule_area_too() -> None:
    """The controlled step past KiCad. A caller asks for it by name."""
    board = Board(zones=[keepout_zone(), pour_zone()])
    assert drawn(board, ALL_CONTENT) == 2


def test_the_two_zone_streams_are_separate() -> None:
    board = Board(zones=[keepout_zone(), pour_zone()])
    assert drawn(board, frozenset({BoardContent.zones})) == 1
    assert drawn(board, frozenset({BoardContent.rule_areas})) == 1


def test_kicad_content_holds_no_rule_areas_member() -> None:
    """Guard the meaning of the name, not just today's behavior."""
    assert BoardContent.rule_areas not in KICAD_CONTENT
    assert BoardContent.rule_areas in ALL_CONTENT
    assert KICAD_CONTENT < ALL_CONTENT


def test_the_default_content_is_unchanged() -> None:
    """Adding a stream must not move an existing render."""
    assert frozenset({BoardContent.drawings}) == DEFAULT_CONTENT


# ---------------------------------------------------------------------------
# Via diameter
# ---------------------------------------------------------------------------


def padstack_via() -> Via:
    return Via(
        at=(20.0, 20.0),
        size=0.6,
        drill=0.3,
        layers=("F.Cu", "B.Cu"),
        padstack=ViaPadstack(
            mode="front_inner_back",
            layers=[
                ViaPadstackLayer(layer_name="Inner", size=1.2),
                ViaPadstackLayer(layer_name="B.Cu", size=0.9),
            ],
        ),
    )


@pytest.mark.parametrize(
    ("layer", "expected"),
    [("F.Cu", 0.6), ("In1.Cu", 1.2), ("In9.Cu", 1.2), ("B.Cu", 0.9)],
)
def test_per_layer_reads_the_padstack(layer: str, expected: float) -> None:
    """Front falls back to the via's own size, because KiCad writes it there."""
    assert via_diameter_mm(padstack_via(), layer, ViaDiameter.per_layer) == expected


@pytest.mark.parametrize("layer", ["F.Cu", "In1.Cu", "B.Cu"])
def test_largest_is_the_same_ring_on_every_layer(layer: str) -> None:
    assert via_diameter_mm(padstack_via(), layer, ViaDiameter.largest) == 1.2


def test_a_via_with_no_padstack_draws_the_same_either_way() -> None:
    """Which is every via on almost every board."""
    via = Via(at=(0.0, 0.0), size=0.8, drill=0.4, layers=("F.Cu", "B.Cu"))
    for layer in ("F.Cu", "In2.Cu", "B.Cu"):
        assert via_diameter_mm(via, layer, ViaDiameter.per_layer) == 0.8
        assert via_diameter_mm(via, layer, ViaDiameter.largest) == 0.8


def test_the_default_via_diameter_is_kicads_answer() -> None:
    """A single-layer plot takes the maximum over one layer, which is that layer."""
    board = Board(tracks=[padstack_via()])
    window = ViewBox(0.0, 0.0, 40.0, 40.0)
    style = LayerStyle(colors={"B.Cu": "#C83434"})
    content = frozenset({BoardContent.tracks})

    default = render_board_svg(board, ["B.Cu"], window, style=style, content=content)["B.Cu"]
    explicit = render_board_svg(
        board,
        ["B.Cu"],
        window,
        style=style,
        content=content,
        via_diameter=ViaDiameter.per_layer,
    )["B.Cu"]
    assert default == explicit
    assert 'r="0.4500"' in default, "B.Cu padstack size 0.9 gives radius 0.45"


def test_the_largest_mode_reaches_the_page() -> None:
    board = Board(tracks=[padstack_via()])
    svg = render_board_svg(
        board,
        ["B.Cu"],
        ViewBox(0.0, 0.0, 40.0, 40.0),
        style=LayerStyle(colors={"B.Cu": "#C83434"}),
        content=frozenset({BoardContent.tracks}),
        via_diameter=ViaDiameter.largest,
    )["B.Cu"]
    assert 'r="0.6000"' in svg, "the widest padstack ring is 1.2, so radius 0.6"


def test_a_zero_diameter_via_draws_nothing() -> None:
    board = Board(tracks=[Via(at=(5.0, 5.0), size=0.0, drill=0.0, layers=("F.Cu", "B.Cu"))])
    assert drawn(board, frozenset({BoardContent.tracks})) == 0


# ---------------------------------------------------------------------------
# Via flashing
# ---------------------------------------------------------------------------


def mode_via(mode: str) -> Via:
    return Via(
        at=(0.0, 0.0),
        size=0.6,
        drill=0.3,
        layers=("F.Cu", "B.Cu"),
        unconnected_layer_mode=mode,  # type: ignore[arg-type]
    )


def test_keep_all_flashes_every_layer() -> None:
    via = mode_via("keep_all")
    for layer in ("F.Cu", "In1.Cu", "B.Cu"):
        result = via_flash(via, layer)
        assert result.flashed
        assert result.certain


def test_start_end_only_flashes_the_two_named_layers() -> None:
    """The one via mode this library can answer with no connectivity graph."""
    via = mode_via("start_end_only")
    assert via_flash(via, "F.Cu").flashed
    assert via_flash(via, "B.Cu").flashed
    assert not via_flash(via, "In1.Cu").flashed


def test_remove_except_start_end_is_certain_on_outer_copper_only() -> None:
    via = mode_via("remove_except_start_end")
    assert via_flash(via, "F.Cu").certain
    inner = via_flash(via, "In1.Cu")
    assert inner.flashed, "an uncertain via still draws"
    assert not inner.certain


def test_remove_all_is_uncertain_on_every_copper_layer() -> None:
    via = mode_via("remove_all")
    for layer in ("F.Cu", "In1.Cu", "B.Cu"):
        assert not via_flash(via, layer).certain


def test_the_flash_rule_removes_a_via_from_the_layer_buckets() -> None:
    """The rule must reach traversal, not sit in a helper nobody calls."""
    board = Board(tracks=[mode_via("start_end_only")], layers=[])
    buckets = render_items_by_layer(
        board, ["F.Cu", "In1.Cu", "B.Cu"], frozenset({BoardContent.tracks})
    )
    assert len(buckets["F.Cu"]) == 1
    assert len(buckets["B.Cu"]) == 1
    assert buckets["In1.Cu"] == []


# ---------------------------------------------------------------------------
# A via and a pad do NOT share the flash rule
# ---------------------------------------------------------------------------


def buried_via(mode: str) -> Via:
    """A via spanning In1.Cu to In4.Cu. Its span touches no outer layer."""
    return Via(
        via_type="buried",
        at=(0.0, 0.0),
        size=0.6,
        drill=0.3,
        layers=("In1.Cu", "In4.Cu"),
        unconnected_layer_mode=mode,  # type: ignore[arg-type]
    )


def test_a_buried_via_keeps_copper_on_its_own_span_ends() -> None:
    """``PCB_VIA::FlashLayer``, ``pcb_track.cpp:1976-1981``.

    A VIA and a PAD do not share this rule, and the two functions have the same
    name and the same enum, so the difference is easy to miss.

    ``PAD::FlashLayer`` keeps ``REMOVE_EXCEPT_START_AND_END`` on an EXTERNAL
    copper layer. ``PCB_VIA::FlashLayer`` keeps it on the via's OWN drill start
    and end. For a through via the two agree. For a buried via they do not, and
    this is the case that separates them.
    """
    via = buried_via("remove_except_start_end")
    for layer in ("In1.Cu", "In4.Cu"):
        result = via_flash(via, layer)
        assert result.flashed
        assert result.certain, f"{layer} is a span end, so the answer is certain"

    middle = via_flash(via, "In2.Cu")
    assert middle.flashed, "an uncertain via still draws"
    assert not middle.certain


def test_a_buried_via_does_not_use_the_outer_layers() -> None:
    """The specific error this guards: testing F.Cu and B.Cu instead of the span.

    An outer layer is not on this via at all. Reading the answer as certain there
    would report confidence about a layer the via never reaches.
    """
    via = buried_via("remove_except_start_end")
    for layer in ("F.Cu", "B.Cu"):
        assert not via_flash(via, layer).certain


def test_start_end_only_on_a_buried_via_uses_the_span() -> None:
    via = buried_via("start_end_only")
    assert via_flash(via, "In1.Cu").flashed
    assert via_flash(via, "In4.Cu").flashed
    assert not via_flash(via, "In2.Cu").flashed
    assert not via_flash(via, "F.Cu").flashed


def test_a_non_copper_layer_always_flashes() -> None:
    """``if( !IsCopperLayer( layer ) ) return true;``, ``pcb_track.cpp:1968-1969``."""
    for mode in ("keep_all", "remove_all", "remove_except_start_end", "start_end_only"):
        result = via_flash(mode_via(mode), "F.Mask")
        assert result.flashed
        assert result.certain


# ---------------------------------------------------------------------------
# A custom-mode via padstack
# ---------------------------------------------------------------------------


def test_a_custom_mode_via_padstack_names_its_layers() -> None:
    """Custom mode names every copper layer it overrides except F.Cu."""
    via = Via(
        at=(0.0, 0.0),
        size=0.6,
        drill=0.3,
        layers=("F.Cu", "B.Cu"),
        padstack=ViaPadstack(
            mode="custom",
            layers=[
                ViaPadstackLayer(layer_name="In2.Cu", size=1.5),
                ViaPadstackLayer(layer_name="B.Cu", size=0.9),
            ],
        ),
    )
    assert via_diameter_mm(via, "In2.Cu", ViaDiameter.per_layer) == 1.5
    assert via_diameter_mm(via, "B.Cu", ViaDiameter.per_layer) == 0.9
    assert via_diameter_mm(via, "F.Cu", ViaDiameter.per_layer) == 0.6
    assert via_diameter_mm(via, "In1.Cu", ViaDiameter.per_layer) == 0.6, (
        "a layer the padstack does not name falls back to the via's own size"
    )
    assert via_diameter_mm(via, "In1.Cu", ViaDiameter.largest) == 1.5


def test_an_empty_via_padstack_falls_back_to_the_via_size() -> None:
    via = Via(at=(0.0, 0.0), size=0.7, drill=0.3, layers=("F.Cu", "B.Cu"))
    assert via_diameter_mm(via, "In1.Cu", ViaDiameter.per_layer) == 0.7
    assert via_diameter_mm(via, "In1.Cu", ViaDiameter.largest) == 0.7
