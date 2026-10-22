"""Which items exist, where they sit, and what layer they name.

This layer answers those three questions and nothing else. It picks no colors and
it drops nothing on its own. The style policy owns every choice about what
reaches the page.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum

from eedesigncore.common.kimath import rotate_point_iu
from eedesigncore.common.layers import (
    CANONICAL_LAYER_NAMES,
    is_copper_layer_name,
    item_layers,
    on_layers,
)
from eedesigncore.common.pcb_family.board_items import Barcode, Dimension, ReferenceImage, Zone
from eedesigncore.common.pcb_family.enums import PadType
from eedesigncore.common.pcb_family.ordering import layer_set_seq
from eedesigncore.common.pcb_family.shapes import PcbPoint
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.pcb.models import Board, Via
from eedesigncore.pcb.ordering import copper_stack, via_layer_seq
from eedesigncore.render.geometry import Point, PointMap
from eedesigncore.render.pads import pad_flash
from eedesigncore.render.vias import via_flash


class BoardContent(Enum):
    """One stream of board content a render may include.

    A board keeps its content in separate streams, and a caller does not always
    want all of them. A silkscreen preview wants ``drawings`` only. A copper
    preview wants ``tracks`` and ``zones`` as well, and those two are far larger:
    one board in the corpus holds 160 footprints and 3188 tracks.

    So the streams are opt-in per call rather than a fixed rule.
    """

    #: Board and footprint graphics, footprint fields, and board points.
    drawings = "drawings"
    #: Copper track segments, track arcs and vias.
    tracks = "tracks"
    #: Copper zones, drawn as their filled polygons or their outline.
    zones = "zones"
    #: Footprint pad copper, resolved per layer through the padstack.
    pads = "pads"
    #: Rule areas, also called keep-out zones.
    #:
    #: KiCad's plot SKIPS these (``plot_board_layers.cpp:848-849``), so this
    #: stream draws what no KiCad export contains. It is here because a rule area
    #: is real board content that a design review wants to see. Ask for it and
    #: the render stops matching ``kicad-cli`` by exactly these outlines.
    rule_areas = "rule_areas"


#: What a render includes when the caller says nothing.
#:
#: Drawings only, which is what this renderer has always drawn. Copper is opt-in
#: because turning it on silently would change every existing render and would
#: break the ``kicad-cli`` parity oracle, which measures a plot that KiCad
#: produced with its own layer selection.
DEFAULT_CONTENT: frozenset[BoardContent] = frozenset({BoardContent.drawings})

#: Every stream KiCad's own board plot draws, and nothing else.
#:
#: Use this for a render that must stay comparable against ``kicad-cli``.
KICAD_CONTENT: frozenset[BoardContent] = frozenset(
    {BoardContent.drawings, BoardContent.tracks, BoardContent.zones, BoardContent.pads}
)

#: Every stream, including the ones that go past KiCad.
#:
#: This is ``KICAD_CONTENT`` plus :attr:`BoardContent.rule_areas`. A render that
#: asks for it holds board content no KiCad export contains, so it is not a
#: parity baseline.
ALL_CONTENT: frozenset[BoardContent] = frozenset(BoardContent)


def identity_map(point: Point) -> Point:
    """Return *point* unchanged."""
    return point


def via_layers(via: Via, board: Board) -> tuple[str, ...]:
    """Return every copper layer name *via* reaches.

    A via's ``(layers …)`` pair is a SPAN, not a list, so matching the two stored
    names alone drops a through via from every inner layer and a preview of that
    layer shows a hole where copper is.

    The rule itself is :func:`eedesigncore.pcb.ordering.via_layer_seq`, which
    ports ``PCB_VIA::GetLayerSet`` (``pcbnew/pcb_track.cpp:1568-1594``). An
    earlier version of this walk expanded only the stack range between the two
    named layers. That is right for a blind or buried via and WRONG for a through
    one, which covers every copper layer the board enables.
    """
    return _copper_names(via_layer_seq(via, copper_stack(board)))


def footprint_point_map(footprint: Footprint) -> PointMap:
    """Return the map from *footprint*-local coordinates to board coordinates.

    A footprint child is stored footprint-local and de-rotated. KiCad's parser
    turns it about the origin by the footprint orientation, then moves it by the
    footprint position (``pcb_io_kicad_sexpr_parser.cpp:3611``). The arithmetic
    runs in integer internal units, as it does in KiCad, so a child lands on the
    same coordinate the parser would give it.

    A library footprint carries no placement, so it maps to itself.
    """
    if footprint.at is None:
        return identity_map

    origin = point_to_iu(footprint.at.position)
    angle = footprint.at.angle

    def to_board(point: Point) -> Point:
        turned = rotate_point_iu(point_to_iu(point), angle)
        return (to_mm(turned[0] + origin[0]), to_mm(turned[1] + origin[1]))

    return to_board


@dataclass(frozen=True)
class RenderItem:
    """One board item selected for one layer, with its host and its map."""

    item: object
    layer: str
    layers: tuple[str, ...]
    footprint: Footprint | None
    uuid: str | None
    to_board: PointMap

    @property
    def in_footprint(self) -> bool:
        """True when a footprint hosts this item."""
        return self.footprint is not None


def _make(item: object, layer: str, footprint: Footprint | None, to_board: PointMap) -> RenderItem:
    return RenderItem(
        item=item,
        layer=layer,
        layers=tuple(item_layers(item)),
        footprint=footprint,
        uuid=getattr(item, "id", None),
        to_board=to_board,
    )


def iter_render_items(
    board: Board,
    layer: str,
    content: Collection[BoardContent] = DEFAULT_CONTENT,
) -> Iterator[RenderItem]:
    """Yield every item of *board* that names *layer*, in stored order.

    Stored order is board drawings, board points, then each footprint in turn,
    then tracks and zones when *content* asks for them. Paint order fidelity is
    out of scope, so stored order is the whole rule. It is stable, which is what
    determinism needs.

    A multi-layer shape writes ``(layers …)`` instead of ``(layer …)``, and
    ``common.layers.on_layers`` holds that rule, so this function does not
    restate it. Two item types do not go through it:

    - A VIA, because its pair of layers is a SPAN rather than a list. See
      :func:`via_layers`.
    - A PAD, because it carries a per-layer FLASH rule on top of its layer
      list. :func:`pad_draws_on` applies both. The wildcards in that list, such
      as ``*.Cu``, need no special handling here: ``on_layers`` expands them.

    A point stores BOARD coordinates even inside a footprint, so it always
    carries the identity map. A track, a via and a zone all store board
    coordinates too. A PAD does not: its position is footprint-relative, so it
    carries its host's map.
    """
    selector = {layer}

    if BoardContent.drawings in content:
        for item in board.drawings:
            if on_layers(item, selector):
                yield _make(item, layer, None, identity_map)

        for target in board.targets:
            if on_layers(target, selector):
                yield _make(target, layer, None, identity_map)

        for point in board.points:
            if on_layers(point, selector):
                yield _make(point, layer, None, identity_map)

        for footprint in board.footprints:
            to_board = footprint_point_map(footprint)
            for item in footprint.drawings:
                if on_layers(item, selector):
                    yield _make(
                        item,
                        layer,
                        footprint,
                        identity_map
                        if isinstance(item, Barcode | Dimension | ReferenceImage)
                        else to_board,
                    )
            for point in footprint.points:
                if on_layers(point, selector):
                    yield _make(point, layer, footprint, identity_map)

    if BoardContent.tracks in content:
        for track in board.tracks:
            if isinstance(track, Via):
                if layer in via_layers(track, board) and via_flash(track, layer).flashed:
                    yield _make(track, layer, None, identity_map)
            elif layer in item_layers(track):
                yield _make(track, layer, None, identity_map)

    for zone in board.zones:
        stream = BoardContent.rule_areas if is_rule_area(zone) else BoardContent.zones
        if stream in content and on_layers(zone, selector):
            yield _make(zone, layer, None, identity_map)

    if BoardContent.pads in content:
        external = external_copper_names(board)
        for footprint in board.footprints:
            to_board = footprint_point_map(footprint)
            for pad in footprint.pads:
                if pad_draws_on(pad, layer, external):
                    yield _make(pad, layer, footprint, to_board)

    if BoardContent.drawings in content:
        for item in board.trailing_drawings:
            if on_layers(item, selector):
                yield _make(item, layer, None, identity_map)


def iter_all_render_items(
    board: Board,
    layers: Sequence[str],
    content: Collection[BoardContent] = DEFAULT_CONTENT,
) -> Iterator[RenderItem]:
    """Yield the items of every layer in *layers*, one layer at a time."""
    for layer in layers:
        yield from iter_render_items(board, layer, content)


def render_items_by_layer(
    board: Board,
    layers: Sequence[str],
    content: Collection[BoardContent] = DEFAULT_CONTENT,
) -> dict[str, list[RenderItem]]:
    """Group the items of *board* by layer, walking the board once.

    :func:`iter_render_items` walks every footprint again for each layer it is
    asked about. On a board of 261 footprints that walk costs about 4 ms, so six
    layers spend roughly 26 ms finding the same items six times. A live preview
    redraws on every user action, so the repeat is worth removing.

    An item that names two requested layers appears in both buckets, and each
    bucket keeps stored order.

    A footprint field is text, which this renderer never draws. It still reaches
    the caller here so that ``on_skip`` can report it. A reference designator is
    the most visible thing on a silkscreen layer, and a preview that loses it
    without a word looks like a defect in the board. That is the one place this
    function differs from :func:`iter_render_items`, which does not walk fields.
    """
    wanted = list(dict.fromkeys(layers))
    buckets: dict[str, list[RenderItem]] = {layer: [] for layer in wanted}
    selector = set(wanted)

    def collect(item: object, footprint: Footprint | None, to_board: PointMap) -> None:
        names = [name for name in item_layers(item) if name in selector]
        if not names:
            return
        for name in names:
            buckets[name].append(_make(item, name, footprint, to_board))

    if BoardContent.drawings in content:
        for item in board.drawings:
            collect(item, None, identity_map)
        for target in board.targets:
            collect(target, None, identity_map)
        for point in board.points:
            collect(point, None, identity_map)

        for footprint in board.footprints:
            to_board = footprint_point_map(footprint)
            for item in footprint.drawings:
                collect(
                    item,
                    footprint,
                    identity_map
                    if isinstance(item, Barcode | Dimension | ReferenceImage)
                    else to_board,
                )
            for point in footprint.points:
                collect(point, footprint, identity_map)
            for field in footprint.properties:
                collect(field, footprint, to_board)

    if BoardContent.tracks in content:
        for track in board.tracks:
            # A via's stored pair is a span, so it cannot go through `collect`.
            # It also carries the unused-layer rule, which `via_flash` applies.
            if isinstance(track, Via):
                for name in via_layers(track, board):
                    if name in selector and via_flash(track, name).flashed:
                        buckets[name].append(_make(track, name, None, identity_map))
            else:
                for name in item_layers(track):
                    if name in selector:
                        buckets[name].append(_make(track, name, None, identity_map))

    for zone in board.zones:
        stream = BoardContent.rule_areas if is_rule_area(zone) else BoardContent.zones
        if stream in content:
            collect(zone, None, identity_map)

    if BoardContent.pads in content:
        external = external_copper_names(board)
        for footprint in board.footprints:
            to_board = footprint_point_map(footprint)
            for pad in footprint.pads:
                # A pad cannot go through `collect`: it carries a flash rule
                # -bearing AND it carries a per-layer flash rule.
                for name in wanted:
                    if pad_draws_on(pad, name, external):
                        buckets[name].append(_make(pad, name, footprint, to_board))

    if BoardContent.drawings in content:
        for item in board.trailing_drawings:
            collect(item, None, identity_map)

    return buckets


def is_rule_area(zone: Zone) -> bool:
    """True when *zone* is a rule area rather than a copper pour.

    KiCad's plot skips a rule area outright
    (``plot_board_layers.cpp:848-849``), so a renderer that draws one puts ink on
    a copper layer that no KiCad export has.

    A zone becomes a rule area on either of two tokens. ``(keepout ...)`` sets it
    (``pcb_io_kicad_sexpr_parser.cpp:8178-8181``, where the comment reads
    "keepout now means rule area, but the file token stays the same"), and
    ``(placement ...)`` sets it too (``:8123-8124``).
    """
    return zone.keepout is not None or zone.placement is not None


def external_copper_names(board: Board) -> frozenset[str]:
    """Return the OUTER copper layer names of *board*.

    ``LSET::ExternalCuMask`` is ``F.Cu`` and ``B.Cu`` (``lset.cpp``), and a
    one-layer board has only ``F.Cu``. The pad flash rule for
    ``keep_end_layers`` needs this set.

    A board whose ``(layers …)`` table this library has not read gives an empty
    stack. Falling back to the empty set there would silently stop treating
    ``F.Cu`` as outer copper, so the fallback is the two-layer answer instead.
    """
    names = _copper_names(copper_stack(board))
    if not names:
        return frozenset({"F.Cu", "B.Cu"})
    if len(names) < 2:
        return frozenset(names)
    return frozenset({names[0], names[-1]})


def pad_layer_names(pad: Pad) -> tuple[str, ...]:
    """Return the layers *pad* names, with every wildcard expanded.

    A pad is the one board item whose ``(layers …)`` list carries wildcards:
    ``*.Cu``, ``*.Mask``, ``*.Paste`` and the rest
    (``PCB_IO_KICAD_SEXPR::formatLayers``, ``pcb_io_kicad_sexpr.cpp:1540-1612``).
    The loader keeps them verbatim so the writer round-trips KiCad's bytes, which
    leaves the expansion to the reader.

    ``common.layers.on_layers`` expands them too, so a caller that only needs a
    yes or no should use that. This function returns the NAMES, which a caller
    that has to enumerate the layers of one pad needs instead.
    """
    return tuple(
        CANONICAL_LAYER_NAMES[ordinal]
        for ordinal in layer_set_seq(item_layers(pad))
        if ordinal in CANONICAL_LAYER_NAMES
    )


def pad_draws_on(pad: Pad, layer: str, external_copper: frozenset[str]) -> bool:
    """True when *pad* puts copper on *layer*.

    Two rules, both KiCad's:

    1. The pad's ``(layers …)`` must name the layer, after
       :func:`pad_layer_names` expands the wildcards.
    2. The pad must FLASH there. A plated through-hole pad with unused layers
       removed shows no copper on a layer it does not connect on
       (``PAD::FlashLayer``). See :class:`eedesigncore.render.pads.PadFlash` for
       the one case this library cannot decide and what it does instead.
    """
    if layer not in pad_layer_names(pad):
        return False
    return pad_flash(pad, layer, external_copper=external_copper).flashed


def is_point(render_item: RenderItem) -> bool:
    """True when *render_item* holds a point marker."""
    return isinstance(render_item.item, PcbPoint)


__all__ = [
    "ALL_CONTENT",
    "DEFAULT_CONTENT",
    "HOLES",
    "HOLES_NON_PLATED",
    "HOLES_PLATED",
    "HOLE_LAYERS",
    "BoardContent",
    "Hole",
    "RenderItem",
    "KICAD_CONTENT",
    "external_copper_names",
    "footprint_point_map",
    "is_rule_area",
    "identity_map",
    "is_point",
    "iter_all_render_items",
    "iter_holes",
    "iter_render_items",
    "pad_draws_on",
    "pad_layer_names",
    "via_layers",
    "render_items_by_layer",
]


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------

#: Every hole on the board, in one document.
HOLES = "Holes"
#: Plated holes only: a via, or a pad whose type is ``thru_hole``.
HOLES_PLATED = "Holes.Plated"
#: Non-plated holes only: a pad whose type is ``np_thru_hole``.
HOLES_NON_PLATED = "Holes.NonPlated"

#: The three pseudo-layer names a caller may request beside canonical layers.
#:
#: A hole is not on a layer. KiCad draws drill marks as their own pass over
#: everything else, so a caller that stacks per-layer documents needs holes as
#: one more document rather than as content of a copper layer.
#:
#: Three names rather than one, because a caller who wants plated and non-plated
#: in different colors gets two stackable documents with no custom policy, and a
#: caller who wants one document asks for ``Holes`` and reads ``Hole.plated`` in
#: its own policy. Both work; neither costs the other anything.
HOLE_LAYERS: frozenset[str] = frozenset({HOLES, HOLES_PLATED, HOLES_NON_PLATED})


@dataclass(frozen=True)
class Hole:
    """One hole, in board coordinates.

    ``width`` and ``height`` are the full extents of the opening, not radii. They
    match for a round hole. ``angle`` turns a slot and means nothing for a round
    hole.
    """

    position: Point
    width: float
    height: float
    angle: float
    plated: bool
    source: str  # "pad" or "via"
    uuid: str | None
    layers: tuple[str, ...]

    @property
    def is_slot(self) -> bool:
        """True when the opening is longer one way than the other."""
        return self.width != self.height

    def touches(self, layer: str) -> bool:
        """True when this hole passes through *layer*.

        A hole document holds every hole on the board, because a hole is not on a
        layer. KiCad's drill pass is per plot instead, and it skips a via whose
        layer span misses the layers being plotted
        (``plot_brditems_plotter.cpp:1297-1298``). A caller reproducing that
        subset, to compare against a single-layer ``kicad-cli`` export, filters on
        this from its own style policy.
        """
        return layer in self.layers


def _copper_names(ordinals: tuple[int, ...]) -> tuple[str, ...]:
    """Return the canonical names of *ordinals* that are copper."""
    names = [CANONICAL_LAYER_NAMES.get(ordinal, "") for ordinal in ordinals]
    return tuple(name for name in names if name and is_copper_layer_name(name))


def _pad_hole(pad: Pad, to_board: PointMap) -> Hole | None:
    """Return the hole of *pad*, or ``None`` when it has none.

    The hole sits at the pad position. It is NOT moved by the ``(offset …)``
    inside ``(drill …)``, whatever that token's name suggests: the parser feeds
    that value to ``PAD::SetOffset``, and ``PAD::ShapePos`` adds it to place the
    COPPER (``pcbnew/pad.cpp:1558-1570``). KiCad plots the drill mark at
    ``GetPosition()`` (``plot_brditems_plotter.cpp:1337``).

    A pad angle is board-absolute already, so a slot needs no further turn for
    its footprint.
    """
    drill = pad.drill
    if drill is None or drill.width <= 0.0:
        return None

    height = drill.height if drill.oval and drill.height is not None else drill.width
    return Hole(
        position=to_board(pad.position),
        width=drill.width,
        height=height,
        angle=pad.angle,
        plated=pad.pad_type != PadType.np_thru_hole,
        source="pad",
        uuid=pad.id,
        layers=_copper_names(layer_set_seq(item_layers(pad))),
    )


def _via_hole(via: Via, stack: tuple[int, ...]) -> Hole | None:
    """Return the hole of *via*. Every via is plated.

    A THROUGH via spans every copper layer the board enables, not just the two it
    names. ``via_layer_seq`` holds that rule.
    """
    if via.drill <= 0.0:
        return None
    return Hole(
        position=via.at,
        width=via.drill,
        height=via.drill,
        angle=0.0,
        plated=True,
        source="via",
        uuid=via.uuid,
        layers=_copper_names(via_layer_seq(via, stack)),
    )


def iter_holes(board: Board, layer: str = HOLES) -> Iterator[Hole]:
    """Yield the holes of *board* that *layer* selects, in stored order.

    Vias come first, then each footprint's pads, which is the order KiCad's own
    drill pass walks (``plot_brditems_plotter.cpp:1291-1338``). Order is stable,
    which is what determinism needs.

    A blind or buried via is included. KiCad's drill pass tests a via against the
    layers being plotted, and a hole document plots no layer, so there is nothing
    to test it against.
    """
    plated_only = layer == HOLES_PLATED
    non_plated_only = layer == HOLES_NON_PLATED

    def wanted(hole: Hole) -> bool:
        if plated_only:
            return hole.plated
        if non_plated_only:
            return not hole.plated
        return True

    stack = copper_stack(board)
    for track in board.tracks:
        if isinstance(track, Via):
            hole = _via_hole(track, stack)
            if hole is not None and wanted(hole):
                yield hole

    for footprint in board.footprints:
        to_board = footprint_point_map(footprint)
        for pad in footprint.pads:
            hole = _pad_hole(pad, to_board)
            if hole is not None and wanted(hole):
                yield hole
