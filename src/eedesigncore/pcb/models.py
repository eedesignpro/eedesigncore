"""Pydantic v2 domain models for KiCad board files (.kicad_pcb).

The ``Board`` root model captures the ``format(const BOARD*)`` skeleton from the
board-root-grammar report (§1) and the A1 layer-table report. Header blocks
(general/paper/title_block/layers/setup/properties/variants) are shaped here and
fleshed out with fields + loaders in T2; the item streams (tracks/generators/
targets) are stubs fleshed out in T4/T8. Shared child models are reused from
``common/pcb_family`` with no duplication (R1).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin
from eedesigncore.common.models import EmbeddedFile, Property, PtsElement
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.pcb_family import (
    BackdrillInfo,
    PcbDrawItem,
    PcbGroup,
    PcbPoint,
    PostMachining,
    Zone,
    ZoneLayerProperty,
)
from eedesigncore.mod.models import Footprint, TeardropParams

# Board file format version targeted for round-trip (SEXPR_BOARD_FILE_VERSION,
# pcb_io_kicad_sexpr.h:202).
BOARD_FILE_VERSION = 20260206

# Type-column token set for the (layers) table (A1 §2): the 7 LAYER::ShowType
# tokens for copper rows, plus the literal ``user`` substituted for tech/user
# rows (A1 headline 1). ``user`` is a row-level sentinel, not a real LAYER_T.
LayerTypeToken = Literal[
    "signal", "power", "mixed", "jumper", "auxiliary", "front", "back", "user"
]


# ---------------------------------------------------------------------------
# Header sub-models
# ---------------------------------------------------------------------------


class BoardGeneral(KicadModel):
    """The ``(general …)`` block (formatGeneral, W:640-655)."""

    thickness: float = 1.6
    legacy_teardrops: bool = False


class BoardLayer(KicadModel):
    """One row of the ``(layers …)`` table (A1 report §3).

    ``ordinal`` is the raw ``PCB_LAYER_ID`` value, preserved verbatim on
    round-trip (the parser recomputes it from name/position, A1 headline 2).
    ``layer_type`` is the type-column token: a real ``LAYER::ShowType`` for
    copper rows, the literal ``user`` for tech/user rows (A1 headline 1).
    ``user_name`` is the optional 4th column, present only when the layer was
    renamed (canonical_name != displayed name, A1 §3c).
    """

    ordinal: int = 0
    canonical_name: str = ""
    layer_type: LayerTypeToken = "signal"
    user_name: str | None = None


class FrontBack(KicadModel):
    """A ``(front yes|no)(back yes|no)`` nested pair (tenting/covering/plugging)."""

    front: bool = False
    back: bool = False


class BoardStackupSubLayer(KicadModel):
    """One sublayer of a board stackup layer (board_stackup.cpp:761-796).

    All fields are guarded on emit; ``None``/``False`` means the token is absent.
    ``thickness`` uses internal units; ``epsilon_r``/``loss_tangent`` use
    ``FormatDouble2Str``. ``thickness_locked`` appends ``locked`` to the
    thickness token (dielectric layers only).
    """

    color: str | None = None
    thickness: float | None = None
    thickness_locked: bool = False
    material: str | None = None
    epsilon_r: float | None = None
    loss_tangent: float | None = None


class BoardStackupLayer(KicadModel):
    """One ``(layer …)`` entry of the board stackup (board_stackup.cpp:756-799).

    ``name`` is the quoted layer name (``LSET::Name`` or ``dielectric <n>``).
    ``layer_type`` is the quoted stackup type name (``GetTypeName()``, e.g.
    ``copper``, ``core``, ``Top Solder Mask``). ``sublayers`` holds one entry per
    sublayer; entries after the first are emitted with a leading ``addsublayer``.
    """

    name: str = ""
    layer_type: str = ""
    sublayers: list[BoardStackupSubLayer] = []


class BoardStackup(KicadModel):
    """The board ``(stackup …)`` block (board_stackup.cpp:736-818).

    DISTINCT from the footprint stackup (``common/pcb_family`` ``Stackup`` is a
    bare layer-name list). Emitted only when the board has a stackup.
    """

    layers: list[BoardStackupLayer] = []
    copper_finish: str | None = None
    dielectric_constraints: bool = False  # always emitted inside the block
    edge_connector: str | None = None  # "yes" or "bevelled"; None = absent
    edge_plating: bool = False  # (edge_plating yes) only when true


class PlotParams(KicadModel):
    """The ``(pcbplotparams …)`` block (pcb_plot_params.cpp:142-207).

    Fully typed per the board-root report §2c-iii (complete inventory). Field
    order matches emission order. All children are unconditional EXCEPT
    ``gerberprecision`` (emitted only when set / non-default). ``layerselection``
    and ``plot_on_all_layers_selection`` are preserved verbatim as their raw
    ``0x…`` token strings. ``dashed_line_*_ratio`` use ``FormatDouble2Str``.
    Writer-omitted legacy tokens (plotreference/plotvalue/plotfptext) are not
    modeled (never emitted at 20260206).
    """

    layerselection: str = "0x00000000_00000000_00000000_00000000"
    plot_on_all_layers_selection: str = "0x00000000_00000000_00000000_00000000"
    disableapertmacros: bool = False
    usegerberextensions: bool = False
    usegerberattributes: bool = True
    usegerberadvancedattributes: bool = True
    creategerberjobfile: bool = True
    gerberprecision: int | None = None  # only when != gbrDefaultPrecision
    dashed_line_dash_ratio: float = 12.0
    dashed_line_gap_ratio: float = 3.0
    svgprecision: int = 4
    plotframeref: bool = False
    mode: int = 1  # 2 if SKETCH else 1
    useauxorigin: bool = False
    pdf_front_fp_property_popups: bool = True
    pdf_back_fp_property_popups: bool = True
    pdf_metadata: bool = True
    pdf_single_document: bool = False
    dxfpolygonmode: bool = True
    dxfimperialunits: bool = True
    dxfusepcbnewfont: bool = True
    psnegative: bool = False
    psa4output: bool = False
    plot_black_and_white: bool = True
    sketchpadsonfab: bool = False
    plotpadnumbers: bool = False
    hidednponfab: bool = False
    sketchdnponfab: bool = False
    crossoutdnponfab: bool = False
    subtractmaskfromsilk: bool = False
    outputformat: int = 1
    mirror: bool = False
    drillshape: int = 0
    scaleselection: int = 1
    outputdirectory: str = ""


class Setup(KicadModel):
    """The ``(setup …)`` block (formatSetup, W:550-637).

    Children in strict emission order. Guarded blocks use ``None``/``False`` to
    mean absent. ``pcbplotparams`` is always emitted; ``pad_to_mask_clearance``
    and the tenting/covering/plugging/capping/filling defaults are always
    emitted.
    """

    stackup: BoardStackup | None = None
    pad_to_mask_clearance: float = 0.0  # always emitted
    solder_mask_min_width: float | None = None
    pad_to_paste_clearance: float | None = None
    pad_to_paste_clearance_ratio: float | None = None
    allow_soldermask_bridges_in_footprints: bool = False  # always emitted
    # A board that carries no (tenting …) keeps KiCad's constructor default, and
    # that default is TENTED on both sides (``board_design_settings.cpp:231-232``).
    # The other four protection settings default to off at ``:234-242``, so only
    # this one differs from a bare FrontBack(). 139 boards in KiCad's own qa tree
    # omit the token, and reading them as untented silently exposes every via.
    tenting: FrontBack = FrontBack(front=True, back=True)  # always emitted
    covering: FrontBack = FrontBack()  # always emitted
    plugging: FrontBack = FrontBack()  # always emitted
    capping: bool = False  # always emitted
    filling: bool = False  # always emitted
    zone_defaults: list[ZoneLayerProperty] | None = None
    aux_axis_origin: tuple[float, float] | None = None  # internal units (mm), != (0,0)
    grid_origin: tuple[float, float] | None = None  # internal units (mm), != (0,0)
    pcbplotparams: PlotParams = PlotParams()  # always emitted


# ---------------------------------------------------------------------------
# Item-stream stub models (shape lands in T1; fleshed out in later tasks)
# ---------------------------------------------------------------------------


class TrackSegment(KicadModel):
    """A straight track ``(segment …)`` (§4c, W:2829-2835).

    Field order mirrors the writer's emission order: start/end/width, locked,
    layer(s), solder_mask_margin, then the common ``(net …)``/``(uuid …)`` tail.
    ``layers`` (multi-layer enumerated set form) is emitted in place of ``layer``
    when present, mirroring ``count()>1 ? formatLayers : formatLayer`` (W:2840).
    ``net`` is a name-only reference (A2); ``None`` omits the token.
    """

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0
    locked: bool = False
    layer: str = ""
    layers: list[str] | None = None
    solder_mask_margin: float | None = None
    net: str | None = None
    uuid: str | None = None


class TrackArc(KicadModel):
    """An arc track ``(arc …)`` (§4b, W:2819-2828).

    Same tail as ``TrackSegment`` but with a ``mid`` control point.
    """

    start: tuple[float, float] = (0.0, 0.0)
    mid: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0
    locked: bool = False
    layer: str = ""
    layers: list[str] | None = None
    solder_mask_margin: float | None = None
    net: str | None = None
    uuid: str | None = None


class ViaFrontBackOpt(KicadModel):
    """A tri-state ``(front …)(back …)`` opt-bool pair (FormatOptBool).

    Each side is ``True``/``False``/``None`` -> ``yes``/``no``/``none`` (the
    ``none`` token means "unspecified", io_utils:40-46). Used for the via
    tenting/covering/plugging finishing blocks (§4a).
    """

    front: bool | None = None
    back: bool | None = None


class ViaPadstackLayer(KicadModel):
    """One ``(layer "<name>" (size <iu>))`` entry inside a via padstack (§4a).

    DISTINCT from the pad ``PadstackLayer``: a via padstack layer carries only a
    per-layer scalar diameter (``size``), not a full pad shape (parser
    parseViastack :7726-7820, writer W:2778-2812).
    """

    layer_name: str = ""
    size: float = 0.0


class ViaPadstack(KicadModel):
    """A via ``(padstack …)`` block, emitted only when mode != NORMAL (§4a).

    ``mode`` is ``front_inner_back`` (Inner + B.Cu size overrides) or ``custom``
    (per-copper-layer size overrides, F_Cu excluded).
    """

    mode: Literal["front_inner_back", "custom"] = "front_inner_back"
    layers: list[ViaPadstackLayer] = []


class Via(KicadModel):
    """A via ``(via …)`` item (§4a, W:2607-2816).

    Field order mirrors the writer exactly. ``via_type`` is the bare qualifier
    right after ``(via`` (``through`` prints nothing). Finishing opt-bools
    (tenting/covering/plugging = tri-state front/back; capping/filling = single
    tri-state) are ``None`` when the block is absent; a present block with both
    sides unspecified is dropped (matches KiCad's has_value emit guard).
    ``teardrops`` reuses the shared ``TeardropParams`` (token parity with pads,
    W:780-798). ``net`` is name-only (A2).
    """

    via_type: Literal["through", "blind", "buried", "micro"] = "through"
    at: tuple[float, float] = (0.0, 0.0)
    size: float = 0.0
    drill: float = 0.0
    backdrill: BackdrillInfo | None = None
    tertiary_drill: BackdrillInfo | None = None
    front_post_machining: PostMachining | None = None
    back_post_machining: PostMachining | None = None
    layers: tuple[str, str] = ("F.Cu", "B.Cu")
    unconnected_layer_mode: Literal[
        "keep_all", "remove_all", "remove_except_start_end", "start_end_only"
    ] = "keep_all"
    locked: bool = False
    free: bool = False
    zone_layer_connections: list[str] | None = None
    tenting: ViaFrontBackOpt | None = None
    capping: bool | None = None
    covering: ViaFrontBackOpt | None = None
    plugging: ViaFrontBackOpt | None = None
    filling: bool | None = None
    padstack: ViaPadstack | None = None
    teardrops: TeardropParams | None = None
    net: str | None = None
    uuid: str | None = None


# A board track-stream item is a segment, an arc, or a via (§1 item 6).
TrackItem = TrackSegment | TrackArc | Via


# ---------------------------------------------------------------------------
# Generator property bag (§5 / A3 report §2 — exhaustive 5-branch value union)
# ---------------------------------------------------------------------------


class GenNumber(KicadModel):
    """A numeric generator property value -> ``(<key> <num>)`` (A3 branch 1).

    Emitted unquoted via ``{:.10g}``. mm units for length-typed keys (``set_iu``);
    plain doubles otherwise. All numbers parse back as double (A3 §6).
    """

    kind: Literal["number"] = "number"
    value: float


class GenBool(KicadModel):
    """A boolean generator property value -> ``(<key> yes|no)`` (A3 branch 2)."""

    kind: Literal["bool"] = "bool"
    value: bool


class GenPoint(KicadModel):
    """A VECTOR2I generator property value -> ``(<key> (xy <x> <y>))`` (A3 branch 3, mm)."""

    kind: Literal["point"] = "point"
    value: tuple[float, float]


class GenPts(KicadModel):
    """A SHAPE_LINE_CHAIN generator value -> ``(<key> (pts (xy …) …))`` (A3 branch 4, mm)."""

    kind: Literal["pts"] = "pts"
    value: list[PtsElement] = []


class GenString(KicadModel):
    """A string generator property value -> ``(<key> "<str>")`` (A3 branch 5, quoted)."""

    kind: Literal["string"] = "string"
    value: str


# The exhaustive property-bag value union (A3 §2: exactly five branches). The
# discriminator makes number/bool distinct despite bool<:int in Python.
GeneratorValue = Annotated[
    GenNumber | GenBool | GenPoint | GenPts | GenString,
    Field(discriminator="kind"),
]


class PcbGenerator(KicadModel):
    """A ``(generated …)`` item (§5 / A3 report).

    A generic type-dispatched property bag: ``properties`` maps a key to one of
    the five ``GeneratorValue`` branches, emitted sorted-by-key. ``members`` are
    referenced item UUIDs, emitted sorted (never embedded). Only one generator
    ``type`` ships in 10.0.3: ``tuning_pattern``. A ``tuning_pattern`` with no
    members is a "ghost" and is dropped on write (A3 §1 / W:2510-2514).
    """

    uuid: str | None = None
    generator_type: str = "tuning_pattern"  # (type <sym>) — bare symbol, not quoted
    name: str = ""
    layer: str = ""
    locked: bool = False
    properties: dict[str, GeneratorValue] = {}
    members: list[str] = []


class PcbTarget(KicadModel):
    """A ``(target …)`` alignment marker (§6 / A3 §7).

    ``(target <x|plus> (at <pos>) (size <mm>) [(width <mm>) if !=0]
    (layer "<name>") (uuid "<kiid>"))``; the shape token is ``x`` when
    ``is_x`` else ``plus`` (bare symbol, W:1161-1174).
    """

    is_x: bool = False  # True -> shape token "x"; False -> "plus"
    position: tuple[float, float] = (0.0, 0.0)
    size: float = 0.0
    width: float = 0.0  # (width …) emitted only when non-zero
    layer: str = ""
    uuid: str | None = None


class BoardVariant(KicadModel):
    """A board-level ``(variant …)`` (formatVariants, W:721-743).

    Board variant grammar is name + optional description ONLY (§2e), distinct
    from the footprint ``Variant`` (dnp / excludes / field overrides). The
    ``(description …)`` child is emitted only when non-empty.
    """

    name: str = ""
    description: str | None = None


# ---------------------------------------------------------------------------
# Board root
# ---------------------------------------------------------------------------


class Board(KicadFileMixin):
    """The root of a ``.kicad_pcb`` board file (``format(const BOARD*)``, §1).

    Field order mirrors the writer's emission order (§1/§2/§8). Header blocks are
    shaped here; item streams reuse ``common/pcb_family`` shared models. No board
    net table is modeled: nets are name-only references synthesized on load
    (headline 1); ``nets`` holds the synthesized name set (never emitted as a
    table or with codes).
    """

    # §8 header wrapper
    version: int = BOARD_FILE_VERSION
    generator: str = "pcbnew"
    generator_version: str | None = None

    # §2 header blocks
    general: BoardGeneral | None = None
    paper: Paper | None = None
    title_block: TitleBlock | None = None
    layers: list[BoardLayer] = []
    setup: Setup | None = None
    properties: list[Property] = []

    # Nets-by-name (§3): synthesized name set; never a table, never codes.
    nets: list[str] = []

    # §1 item streams
    footprints: list[Footprint] = []
    drawings: list[PcbDrawItem] = []
    points: list[PcbPoint] = []
    tracks: list[TrackItem] = []
    zones: list[Zone] = []
    groups: list[PcbGroup] = []
    generators: list[PcbGenerator] = []
    targets: list[PcbTarget] = []

    # §7 root miscellany
    embedded_fonts: bool = False
    # Drawings encountered AFTER (embedded_fonts …) in the source (rare; e.g. a
    # trailing fab-template gr_text_box). KiCad's format(BOARD) emits all drawings
    # before groups, so these come from files not written by the standard board
    # save path; we preserve their post-embedded_fonts position for byte-identity
    # (encounter-order lesson, ADR-025 / ADR-034).
    trailing_drawings: list[PcbDrawItem] = []
    embedded_files: list[EmbeddedFile] = []
    variants: list[BoardVariant] = []

    # ------------------------------------------------------------------
    # Nets-by-name (§3, A2)
    # ------------------------------------------------------------------

    @property
    def net_names(self) -> set[str]:
        """The set of net names referenced by board items (order-independent).

        Synthesized from item net references (tracks/vias, zones, footprint pads,
        and copper drawings), mirroring KiCad's on-load ``NETINFO_LIST``
        reconstruction (A2 Headline 1). This is a derived utility set; nets are
        never stored as a table or with codes and are never emitted as such.
        """
        names: set[str] = set()
        for track in self.tracks:
            if track.net is not None:
                names.add(track.net)
        for zone in self.zones:
            if zone.net_name is not None:
                names.add(zone.net_name)
        for item in (*self.drawings, *self.trailing_drawings):
            net_name = getattr(item, "net_name", None)
            if net_name is not None:
                names.add(net_name)
        for fp in self.footprints:
            for pad in fp.pads:
                if pad.net_name is not None:
                    names.add(pad.net_name)
            for zone in fp.zones:
                if zone.net_name is not None:
                    names.add(zone.net_name)
            for item in fp.drawings:
                net_name = getattr(item, "net_name", None)
                if net_name is not None:
                    names.add(net_name)
        return names

    # ------------------------------------------------------------------
    # KicadFileMixin hooks
    # ------------------------------------------------------------------

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.pcb.loader import load_pcb
        from eedesigncore.sexpr.parser import parse

        tree = parse(text)
        return load_pcb(tree)  # type: ignore[return-value]

    def _to_lf_string(self) -> str:
        from eedesigncore.pcb.writer import write_pcb

        return write_pcb(self)
