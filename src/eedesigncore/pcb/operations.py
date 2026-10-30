"""Footprint extract / inject operations (spec §2.1-2.2, R9).

These are pure model transforms between a ``.kicad_pcb`` ``Board`` and a
standalone ``.kicad_mod`` ``Footprint``. Neither performs disk IO.

``extract_footprint`` lifts a board footprint out into a library-clean
``Footprint`` (directly ``to_file``-able as a ``.kicad_mod``); ``inject_footprint``
places a library footprint into a board with board-context identity.

Every transform step cites the delta-report Part-3 row it implements
(``auditor-report-embed-context-deltas-2026-07-09.md``); the CTL-guard file:line
citations are carried from that report. UUID handling follows ADR-019
(extract strips uuids by default, group-aware; inject mints fresh uuids).

This module lives under ``pcb/`` (not ``common/``) because the operations bridge
two format packages (``pcb.Board`` + ``mod.Footprint``); hosting them in
``common/`` would force ``common`` to import format sub-packages, violating
ADR-015. ``pcb`` already depends on ``mod`` (board writer emits footprints), so
this is the acyclic home. See ADR-035.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from eedesigncore.common.base import KicadModel
from eedesigncore.common.pcb_family import PcbDrawItem, PcbGroup, PcbPoint, Zone
from eedesigncore.common.uuid_ops import (
    DERIVED_UUID_NAMESPACE,
    derive_uuids,
    generate_uuids,
    strip_uuids,
)
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import (
    BOARD_FILE_VERSION,
    Board,
    PcbGenerator,
    PcbTarget,
    TrackItem,
)
from eedesigncore.pcb.nets import rename_nets
from eedesigncore.pcb.transform import (
    rotate_board,
    set_footprint_orientation,
    translate_board,
    translate_footprint,
)
from eedesigncore.tables.resolver import ResolvedLibTable, qualify_item_name


def _bare_item_name(fp_id: str) -> str:
    """Return the bare library item name from a possibly ``lib:name`` FPID."""
    return fp_id.split(":", 1)[1] if ":" in fp_id else fp_id


def _find_footprint(board: Board, reference: str | None, uuid: str | None) -> Footprint:
    """Locate a board footprint by Reference property or footprint uuid."""
    if (reference is None) == (uuid is None):
        raise ValueError("extract_footprint requires exactly one of reference= or uuid=.")
    if reference is not None:
        for fp in board.footprints:
            for prop in fp.properties:
                if prop.name == "Reference" and prop.value == reference:
                    return fp
        raise KeyError(f"no footprint with Reference {reference!r} in board")
    for fp in board.footprints:
        if fp.uuid == uuid:
            return fp
    raise KeyError(f"no footprint with uuid {uuid!r} in board")


def extract_footprint(
    board: Board,
    *,
    reference: str | None = None,
    uuid: str | None = None,
    clear_placement_state: bool = True,
) -> Footprint:
    """Lift a board footprint into a library-clean ``Footprint`` (spec §2.1).

    Selects the footprint by ``reference`` (Reference property) or footprint-level
    ``uuid`` (exactly one required), deep-copies it, and applies the EXTRACT
    transform (Part-3 "pcb footprint -> .kicad_mod"). The returned footprint is
    directly saveable as a ``.kicad_mod`` (``to_file``), emitting the
    version/generator block in LIBRARY context.

    UUIDs are stripped group-aware by default (ADR-019: EXTRACT strips uuids);
    this overrides Part-3 step 10's KEEP-child-uuids (that is KiCad's
    editor-mimicry behavior, not a library-curation policy).

    Embedded-file data is already materialized inline on load (board root
    hydration, ``BOARD::FixupEmbeddedData`` analog, T9), so the deep copy carries
    real bytes; LIBRARY-context emission writes them inline (Part-3 step 11).
    """
    src = _find_footprint(board, reference, uuid)
    fp = src.model_copy(deep=True)

    # Step 1 REWRITE (footprint "lib:name") -> "name" (:1190-1199)
    fp.fp_id = _bare_item_name(fp.fp_id)

    # Step 2 ADD version block (:1201-1206). Model defaults already carry
    # generator/generator_version; pin the version to the board file version.
    fp.version = BOARD_FILE_VERSION

    # Steps 3 + 10 STRIP footprint-level uuid AND child uuids, group-aware
    # (:1216-1217; ADR-019 EXTRACT-strips-by-default overrides KEEP step 10).
    strip_uuids(fp, group_aware=True)

    # Step 4 STRIP (at) (:1219-1226). The orientation goes to zero FIRST, which
    # de-rotates every child angle back to a library-relative value. KiCad does
    # the same when it writes a footprint to a library: FootprintSave clones the
    # board footprint and calls SetOrientation( ANGLE_0 )
    # (pcb_io_kicad_sexpr.cpp:3446-3447). Without this the extracted part keeps
    # the board placement baked into its pad and text angles.
    if fp.at is not None:
        set_footprint_orientation(fp, 0.0)
        # Back to the library origin: board-frame children (zones, point markers,
        # embedded dimensions) carry board coordinates and follow the position
        # (pcbnew/footprint.cpp:3002-3021), as when the footprint editor loads a
        # board footprint at the origin.
        x, y = fp.at.position
        translate_footprint(fp, (-x, -y))
    fp.at = None
    # Step 5 STRIP (path) (:1267-1268)
    fp.path = None
    # Step 8 STRIP (sheetname)/(sheetfile) (:1270-1274)
    fp.sheetname = None
    fp.sheetfile = None

    # Step 9 CONSIDER (locked)/(placed): default clear placement state (§2.1 #9).
    if clear_placement_state:
        fp.locked = False
        fp.placed = False

    # Step 6 STRIP pad net/pinfunction/pintype (:1847-1859)
    for pad in fp.pads:
        pad.net_code = None
        pad.net_name = None
        pad.pinfunction = None
        pad.pintype = None

    # Step 7 STRIP zone net (:2866-2870)
    for zone in fp.zones:
        zone.net = None
        zone.net_name = None

    # Residual: fp shape board (net) (:1107-1108, spec §3.1). A board-context
    # copper shape can carry a net name; strip it for a clean library footprint.
    for item in fp.drawings:
        if getattr(item, "net_name", None) is not None:
            setattr(item, "net_name", None)  # noqa: B010 - union member field

    return fp


def inject_footprint(
    board: Board,
    footprint: Footprint,
    *,
    at: tuple[float, float],
    rotation: float = 0.0,
    reference: str | None = None,
    net_map: dict[str, str] | None = None,
    path: str | None = None,
    lib_nickname: str | None = None,
    lib_table: ResolvedLibTable | None = None,
    regenerate_uuids: bool = True,
) -> Footprint:
    """Place a library footprint into ``board`` in board context (spec §2.2).

    Deep-copies ``footprint``, applies the INJECT transform (Part-3 ".kicad_mod
    -> pcb footprint"), appends the result to ``board.footprints`` and returns
    it.

    ``path`` is the KiCad ``(path)`` token, which holds the full KIID path of the
    associated schematic symbol (``pcbnew/footprint.h:1383``:
    ``[sheetUUID, .., symbolUUID]``). It is NOT the plain sheet path that
    ``inject_symbol`` takes as ``sheet_path``, so the two keep separate names.

    ``net_map`` maps a pad key to a net NAME. Because v10 references nets by name
    (nets-by-name, A2/ADR-028), the map is ``dict[str, str]`` (net name), not the
    pre-nets-by-name spec's ``dict[str, int]`` (net code); keys are pad
    ``pinfunction`` where present, else pad ``number`` (a freshly extracted
    footprint has no pinfunction, so ``number`` is the robust key). See ADR-035.

    ``lib_nickname`` (beyond the spec §2.2 signature) prepends a library
    nickname when ``footprint.fp_id`` is a bare item name. A pre-qualified
    ``lib:name`` FPID is left as-is. See ADR-035.

    ``lib_table`` supplies the resolved ``fp-lib-table`` namespace from
    :func:`eedesigncore.resolve_lib_table`. A bare FPID then takes the
    nickname of the first discovered library holding
    ``<name>.kicad_mod`` (R4, ADR-047). Precedence: an explicit
    ``lib_nickname`` wins, the table match comes next, and the placeholder
    ``"eedesigncore"`` covers the no-match case. Passing a ``sym`` table
    raises ``ValueError``.

    ``rotation`` sets the placement ORIENTATION, so it turns the pad and text
    angles with it. Those are stored in board terms, and leaving them behind puts
    every pad and every label at the wrong angle on the plot. This is KiCad's
    ``SetOrientation`` (footprint.cpp:3100-3138), the exact inverse of what
    :func:`extract_footprint` applies, so extract and inject round-trip. It is
    NOT ``FOOTPRINT::Rotate``: a turn of an already placed footprint also applies
    ``KeepUpright`` to its text, and :func:`eedesigncore.rotate_footprint` is
    the operation for that.

    ``regenerate_uuids=True`` (default) mints a fresh footprint uuid and fresh
    child uuids (KiCad fresh-add ``Duplicate()``, uuid-lifecycle Task 1);
    ``False`` preserves the library's child uuids (fp-editor ``Clone()`` path) but
    still mints the footprint-level uuid (spec §2.2 step 3).
    """
    if lib_table is not None and lib_table.kind != "fp":
        raise ValueError("inject_footprint needs an 'fp' lib table, got a 'sym' one")

    placed = footprint.model_copy(deep=True)

    # Step 1 REWRITE (footprint "name") -> "lib:name" (:1190-1199). A bare FPID
    # takes the explicit nickname, else the resolved fp-lib-table match, else
    # the placeholder (R4, ADR-047).
    placed.fp_id = qualify_item_name(placed.fp_id, lib_nickname=lib_nickname, lib_table=lib_table)

    # Step 2 REMOVE version block (:1201-1206): board context omits it on emit
    # (dump_mod BOARD branch); no model field to clear that would be emitted.

    # Steps 3 + 7 ADD footprint-level uuid + child uuids (:1216-1217, :2073).
    if regenerate_uuids:
        generate_uuids(placed, only_missing=False)
    else:
        generate_uuids(placed, only_missing=True)
        placed.uuid = str(uuid4())  # step 3 always mints the fp-level uuid

    # Step 4 ADD (at X Y rot) (:1219-1226). The placement angle is not enough:
    # a pad angle and a footprint text angle are stored in BOARD terms, so both
    # take the turn. Setting the placement angle alone leaves every pad and every
    # label turned wrongly, which a plot shows and a resave does not. See
    # context/audit/footprint-transform-frame-2026-09-02.md. This is
    # SetOrientation, the exact inverse of what extract_footprint applies.
    # FOOTPRINT::SetPosition moves the footprint's board-frame children with it:
    # every zone, point marker and embedded dimension (pcbnew/footprint.cpp:
    # 3002-3021). A library file stores them about the origin, so they take the
    # placement offset here; otherwise a keep-out lands at board 0,0.
    start = placed.at.position if placed.at is not None else (0.0, 0.0)
    placed.at = FootprintPlacement(position=start, angle=0.0)
    translate_footprint(placed, (at[0] - start[0], at[1] - start[1]))
    if rotation != 0.0:
        set_footprint_orientation(placed, rotation)

    # Step 5 ADD-opt (path) (:1267-1268)
    if path is not None:
        placed.path = path

    # Reference override (§2.2): set the Reference property value.
    if reference is not None:
        for prop in placed.properties:
            if prop.name == "Reference":
                prop.value = reference
                break

    # Step 6 ADD-opt pad net via net_map (:1847-1859), keyed by pinfunction
    # else pad number (v10 nets-by-name).
    if net_map is not None:
        for pad in placed.pads:
            key = pad.pinfunction if pad.pinfunction else pad.number
            if key in net_map:
                pad.net_name = net_map[key]

    # Step 8 MOVE inline embedded data -> board root (:1497/:869). Strip the
    # footprint's inline (data) to a checksum-only stub; merge bytes into the
    # board's consolidated block (dedupe by filename). Checksum preserved as read.
    for i, ef in enumerate(placed.embedded_files):
        if ef.data:
            if not any(rf.name == ef.name for rf in board.embedded_files):
                board.embedded_files.append(ef.model_copy(deep=True))
            placed.embedded_files[i] = ef.model_copy(update={"data": ""})

    board.footprints.append(placed)
    return placed


# ---------------------------------------------------------------------------
# Board into board (spec P3, with P4 uuid derivation and P5 net renaming)
# ---------------------------------------------------------------------------


class InjectedContent(KicadModel):
    """Everything one :func:`inject_board` call added to the target board.

    The lists hold the SAME objects that now sit on the target, not copies, so a
    caller can group them, re-net them, or take their uuids without searching the
    target for what it just added.
    """

    footprints: list[Footprint] = []
    drawings: list[PcbDrawItem] = []
    points: list[PcbPoint] = []
    tracks: list[TrackItem] = []
    zones: list[Zone] = []
    groups: list[PcbGroup] = []
    generators: list[PcbGenerator] = []
    targets: list[PcbTarget] = []

    @property
    def uuids(self) -> list[str]:
        """Every top-level uuid this injection added, in stream order."""
        streams = (
            self.footprints,
            self.drawings,
            self.points,
            self.tracks,
            self.zones,
            self.groups,
            self.generators,
            self.targets,
        )
        found: list[str] = []
        for stream in streams:
            for item in stream:
                value = getattr(item, "uuid", None) or getattr(item, "id", None)
                if value is not None:
                    found.append(value)
        return found


def inject_board(
    target: Board,
    source: Board,
    *,
    offset: tuple[float, float] = (0.0, 0.0),
    rotation: float = 0.0,
    rotation_center: tuple[float, float] = (0.0, 0.0),
    net_map: dict[str, str] | None = None,
    instance_key: str | None = None,
    uuid_namespace: UUID = DERIVED_UUID_NAMESPACE,
    regenerate_uuids: bool = True,
) -> InjectedContent:
    """Copy the CONTENT of *source* into *target* at a placement (spec P3).

    Content means the item streams: footprints, drawings, points, tracks, zones,
    groups, generators and targets, plus any embedded files those items need.
    Board SETTINGS do not travel: the layer table, the setup block, the page, the
    title block and the board properties all belong to the target, and
    :func:`eedesigncore.new_board_from` is the operation that copies those.

    *source* is not modified. Every item is deep-copied first.

    **Placement.** The copy is turned by *rotation* degrees about
    *rotation_center*, then moved by *offset* millimetres. Both steps use the
    same rules :func:`eedesigncore.rotate_board` and
    :func:`eedesigncore.translate_board` use, so a footprint child keeps its
    footprint-local frame and a quarter turn stays exact. A turn that is not a
    multiple of 90 is refused when the source holds a rectangle.

    **Nets.** A net is a NAME in KiCad 10, so copying content that names ``GND``
    into a board that already names ``GND`` JOINS the two nets electrically.
    Pass *net_map* to rename the copy's nets on the way in. The map is applied to
    the copy alone and the target is untouched.

    **Uuids.** Every KiCad item needs its own uuid, so the copy cannot keep the
    source's. Three behaviours:

    * *instance_key* set: uuids are DERIVED, ``uuid5(namespace, f"{old}|{key}")``.
      The same source and the same key give the same uuids on every run, which is
      what makes a rebuild reproducible. The key is a caller string; what makes
      one copy differ from another is the caller's business.
    * *instance_key* unset and ``regenerate_uuids=True`` (the default): a fresh
      random uuid for every item.
    * ``regenerate_uuids=False``: the source uuids ride along unchanged. Only
      safe when the target cannot already hold them.

    Group and generator member references are remapped with the rest, so a group
    still names its members after the copy.

    Raises:
        ValueError: when *instance_key* is combined with
            ``regenerate_uuids=False``, or when the turn is refused.
    """
    if instance_key is not None and not regenerate_uuids:
        raise ValueError(
            "inject_board cannot both derive uuids from instance_key and keep the "
            "source uuids. Drop one of instance_key= and regenerate_uuids=False."
        )

    # A staging board carries the copy through the transform, so the board-level
    # frame rules apply once, in one place, instead of per stream here.
    staging = Board(
        footprints=[fp.model_copy(deep=True) for fp in source.footprints],
        drawings=[item.model_copy(deep=True) for item in source.drawings],
        trailing_drawings=[item.model_copy(deep=True) for item in source.trailing_drawings],
        points=[point.model_copy(deep=True) for point in source.points],
        tracks=[track.model_copy(deep=True) for track in source.tracks],
        zones=[zone.model_copy(deep=True) for zone in source.zones],
        groups=[group.model_copy(deep=True) for group in source.groups],
        generators=[gen.model_copy(deep=True) for gen in source.generators],
        targets=[t.model_copy(deep=True) for t in source.targets],
    )

    if rotation % 360.0 != 0.0:
        rotate_board(staging, rotation, center=rotation_center)
    if offset != (0.0, 0.0):
        translate_board(staging, offset)

    if net_map:
        rename_nets(staging, net_map)

    if instance_key is not None:
        derive_uuids(staging, key=instance_key, namespace=uuid_namespace)
    elif regenerate_uuids:
        generate_uuids(staging, only_missing=False)

    # An embedded file the copy needs must exist on the target board root, and a
    # name already there is the same file (inject_footprint step 8).
    for embedded in source.embedded_files:
        if not any(existing.name == embedded.name for existing in target.embedded_files):
            target.embedded_files.append(embedded.model_copy(deep=True))

    # A trailing drawing is a position in the SOURCE file, not a property of the
    # item, so the copy joins the target's ordinary drawing stream.
    drawings = [*staging.drawings, *staging.trailing_drawings]

    target.footprints.extend(staging.footprints)
    target.drawings.extend(drawings)
    target.points.extend(staging.points)
    target.tracks.extend(staging.tracks)
    target.zones.extend(staging.zones)
    target.groups.extend(staging.groups)
    target.generators.extend(staging.generators)
    target.targets.extend(staging.targets)

    return InjectedContent(
        footprints=staging.footprints,
        drawings=drawings,
        points=staging.points,
        tracks=staging.tracks,
        zones=staging.zones,
        groups=staging.groups,
        generators=staging.generators,
        targets=staging.targets,
    )


# ---------------------------------------------------------------------------
# Board scaffold (spec P7)
# ---------------------------------------------------------------------------


def new_board_from(source: Board) -> Board:
    """Return an empty board carrying the SETTINGS of *source* (spec P7).

    A setting travels and content does not. This is the exact complement of
    :func:`inject_board`, so the two together build a board out of another one:
    scaffold first, then inject the content as many times as you need.

    Travels, deep-copied: the file version and generator, the ``general`` block,
    the page, the title block, the board properties, the layer table, the
    ``setup`` block and its stackup, the ``embedded_fonts`` flag, and the board
    variants.

    Does NOT travel: footprints, drawings, points, tracks, zones, groups,
    generators, targets and embedded files. Those are content, and
    :func:`inject_board` carries the ones its items need.

    The PAGE travels with the rest, and a board built from many copies of one
    source is usually bigger than that page. Set ``paper`` on the result when it
    is.

    Netclasses do NOT live on the board. They live in the ``.kicad_pro`` project
    file, which this library round-trips separately: copy the project file
    itself.

    *source* is not modified.
    """
    return Board(
        version=source.version,
        generator=source.generator,
        generator_version=source.generator_version,
        general=source.general.model_copy(deep=True) if source.general else None,
        paper=source.paper.model_copy(deep=True) if source.paper else None,
        title_block=source.title_block.model_copy(deep=True) if source.title_block else None,
        properties=[prop.model_copy(deep=True) for prop in source.properties],
        layers=[layer.model_copy(deep=True) for layer in source.layers],
        setup=source.setup.model_copy(deep=True) if source.setup else None,
        embedded_fonts=source.embedded_fonts,
        variants=[variant.model_copy(deep=True) for variant in source.variants],
    )
