"""The subgraph state model, mirroring ``CONNECTION_SUBGRAPH``.

Source of truth: audit ``connection-graph-algorithm-2026-09-02.md``, section
"State: what a subgraph carries, and which phase sets it", against
``eeschema/connection_graph.h`` and ``connection_graph.cpp`` at tag 10.0.3.

Every field below names the KiCad member it ports and the PHASE that first sets
it. The phase tag is not decoration. It lets a test assert phase ordering
directly, for example that a hierarchy parent link stays unset until propagation
runs. A field that no ported phase sets stays at its default and the engine says
so, rather than filling it with a guess.

Net codes are deliberately absent. Their values depend on schematic file item
order through several layers of iteration, so a code is not reproducible and must
never be compared (audit, "Answers to the specific questions").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from eedesigncore.connectivity.bus import ConnectionType
from eedesigncore.connectivity.connection import MemberConn, clone_member_list
from eedesigncore.connectivity.geometry import PinRef


class Priority(IntEnum):
    """Driver rank, ported from the ENUM at ``connection_graph.h:67-78``.

    The doxygen comment above that enum documents seven ranks and merges the two
    power-pin ranks. It is stale. Trusting it misranks a local power pin against a
    global label. Verified against the switch at ``connection_graph.cpp:577-616``.
    """

    INVALID = -1
    NONE = 0
    PIN = 1
    SHEET_PIN = 2
    HIER_LABEL = 3
    LOCAL_LABEL = 4
    LOCAL_POWER_PIN = 5
    GLOBAL_POWER_PIN = 6
    GLOBAL = 7


class ItemKind(IntEnum):
    """The item types that take part in connectivity.

    A type absent here contributes no connection point, per the participation
    table in audit ``connectivity-items-geometry-2026-07-28.md`` section 3.
    """

    WIRE = 1
    BUS = 2
    JUNCTION = 3
    NO_CONNECT = 4
    BUS_WIRE_ENTRY = 5
    BUS_BUS_ENTRY = 6
    LABEL = 7
    GLOBAL_LABEL = 8
    HIER_LABEL = 9
    DIRECTIVE_LABEL = 10
    SHEET_PIN = 11
    PIN = 12


@dataclass(frozen=True)
class Driver:
    """One item that can drive a net name.

    ``GetNameForDriver`` caches a name per item (`connection_graph.cpp:437-449`).
    This carries the resolved name so the tie-break can compare it without
    recomputing.
    """

    kind: ItemKind
    # The ESCAPED name, porting GetNameForDriver's cache
    # (`connection_graph.cpp:414`). Every driver comparison uses this form.
    name: str
    priority: Priority
    # The RAW shown text, before escaping. `generateBusAliasMembers` classifies
    # this form rather than the escaped cache (`connection_graph.cpp:1747`), so
    # the bus grammar must see it. Empty means the two forms agree, which holds
    # for any name without a character the escape rule touches.
    shown_name: str = ""
    # Set for a pin driver only. Drives the two power ranks and the tie-break.
    pin: PinRef | None = None
    # Set for a sheet pin only. An output shape wins its tie-break rule.
    is_output_shape: bool = False
    # Set for a bus driver only. Bus subset comparison is the first tie-break.
    bus_members: frozenset[str] = frozenset()

    @property
    def raw_name(self) -> str:
        """The unescaped form when it differs, else the escaped one."""
        return self.shown_name or self.name

    @property
    def is_bus(self) -> bool:
        return self.kind is ItemKind.BUS or bool(self.bus_members)

    @property
    def is_strong(self) -> bool:
        """A strong driver: rank HIER_LABEL or above (`connection_graph.cpp:179-180`).

        The threshold is the literal ``highest_priority >= PRIORITY::HIER_LABEL``.
        It is NOT "anything above a plain pin".

        **A SHEET PIN is therefore WEAK**, at rank 2, one below the threshold.
        That is the whole reason phase 8 carries a promotion step for a sheet-pin
        driver (`:1994-2026`): the promotion is reachable only because the driver
        was weak to begin with, and it is deliberately skipped when the name
        collides. Treating a sheet pin as strong skips both the collision check
        and the promotion, so five sibling sheet instances sharing a pin name
        merge into one net instead of staying five.

        The same threshold decides ``bestIsStrong`` in propagation (`:3146`) and
        whether the unconnected-pin fixup in phase 9f applies (`:2606-2614`).
        """
        return self.priority >= Priority.HIER_LABEL


@dataclass
class Subgraph:
    """One electrically connected group, with the full KiCad state model.

    The phase tags below match the audit's state table. Creation means one of the
    three subgraph creation sites: ``buildItemSubGraphs``,
    ``generateGlobalPowerPinSubGraphs``, or ``generateBusAliasMembers``.
    """

    # --- set at creation -------------------------------------------------
    # m_sheet (connection_graph.h:315). The sheet path this subgraph lives on.
    sheet_path: tuple[str, ...] = ()
    # m_items (:309), through AddItem (connection_graph.cpp:539-550).
    pins: list[PinRef] = field(default_factory=list)
    source_items: set[str] = field(default_factory=set)
    # m_drivers (:267), through AddItem, only when the item is a driver (:543-544).
    drivers: list[Driver] = field(default_factory=list)
    # m_no_connect (:312), from the buildItemSubGraphs scan (:1547-1548).
    no_connect: bool = False
    # m_hier_pins, m_hier_ports (:289, :292), through AddItem (:546-549).
    hier_pins: list[str] = field(default_factory=list)
    hier_ports: list[str] = field(default_factory=list)
    # Each sheet pin with the child sheet it opens onto, as (pin name, sheet
    # name). Propagation needs the child path, and a bare name cannot give it.
    hier_pin_children: list[tuple[str, str]] = field(default_factory=list)
    # m_dirty (:232). The CONSTRUCTOR sets it false (`connection_graph.h:80-82`).
    # Only `buildItemSubGraphs` sets it true at its creation site (`:1575`).
    # `generateGlobalPowerPinSubGraphs` does NOT (`:1868-1885`), so the default
    # here matches the constructor and each creation site sets it explicitly.
    # An earlier version defaulted to true, which was right for one creation site
    # and wrong for the other.
    dirty: bool = False

    # --- set by resolveAllDrivers ----------------------------------------
    # m_driver (:306), m_strong_driver (:259), m_local_driver (:262),
    # m_multiple_drivers (:256), all from ResolveDrivers (:132-287).
    driver: Driver | None = None
    strong_driver: bool = False
    local_driver: bool = False
    multiple_drivers: bool = False
    # m_driver_connection (:318), set at ResolveDrivers (:274-280) and mutated in
    # place by every later phase. KiCad caches TWO forms and reads both.
    #
    # `name` ports Name(true), which is NEVER sheet-qualified
    # (`sch_connection.cpp:393-397`).
    name: str = ""
    # `full_name` ports Name(), which IS sheet-qualified when the driver kind
    # calls for it. `collectAllDriverValues` keys its net-name map on this form
    # and its label caches on the unqualified one (`connection_graph.cpp:1665-1667`).
    # Conflating the two hides a same-name collision across two sheets.
    full_name: str = ""
    # m_bus_entry (:265), from the resolveAllDrivers lambda (:1613-1615).
    bus_entry: bool = False

    # --- set by generateBusAliasMembers ----------------------------------
    # m_is_bus_member (:241), set at :1779.
    is_bus_member: bool = False

    # --- the driver connection's own bus state ---------------------------
    # m_driver_connection's type and member list. ConfigureFromLabel sets them
    # when the driver resolves (`:274-280`); Clone replaces them at every rename.
    conn_type: ConnectionType = ConnectionType.NET
    conn_members: list[MemberConn] = field(default_factory=list)
    # m_driver_connection->Sheet(), with its escaped net-name rendering. Clone
    # COPIES the sheet (`sch_connection.cpp:247`), so a propagated subgraph
    # carries the WINNER's sheet and not its own. Every bus member name is
    # qualified with THIS path, which is what lets one member net span two
    # sheets. Keeping only the subgraph's own sheet_path loses that.
    conn_sheet: tuple[str, ...] = ()
    conn_sheet_prefix: str = "/"
    # Whether the qualified form carries the path, from the prepend rule applied
    # to the connection's CURRENT driver. Clone copies the driver (`:246`), so
    # this travels with a rename and is not a property of the subgraph's own
    # driver once propagation has run.
    conn_prepend: bool = False

    # --- set by processSubGraphs ------------------------------------------
    # m_bus_neighbors, m_bus_parents (:278, :286), set at :2202-2203.
    bus_neighbors: list[BusNeighborLink] = field(default_factory=list)
    bus_parents: list[BusNeighborLink] = field(default_factory=list)
    # m_absorbed, m_absorbed_by (:235, :244), from Absorb (:502-536).
    absorbed: bool = False
    absorbed_by: Subgraph | None = None

    # --- set by propagateToNeighbors --------------------------------------
    # m_hier_parent, m_hier_children (:295, :299), from the visit lambda
    # (:2892-2893, :2944). MUST stay unset until propagation runs.
    hier_parent: Subgraph | None = None
    hier_children: list[Subgraph] = field(default_factory=list)

    @property
    def is_bus(self) -> bool:
        """``m_driver_connection->IsBus()``, the gate on bus propagation."""
        return self.conn_type in (ConnectionType.BUS, ConnectionType.BUS_GROUP)

    def as_conn(self) -> MemberConn:
        """This subgraph's driver connection, as a connection object.

        A subgraph carries its connection's fields directly rather than owning a
        connection object, so a phase that clones one subgraph's connection onto
        another goes through this view. The member list is SHARED, not copied,
        which matches source passing ``*conn`` by reference (`:3194`).
        """
        return MemberConn(
            type=self.conn_type,
            local_name=self.name,
            name=self.name,
            sheet=self.conn_sheet,
            sheet_prefix=self.conn_sheet_prefix,
            prepend=self.conn_prepend,
            members=self.conn_members,
        )

    def clone_conn_from(self, src: MemberConn) -> str:
        """Clone *src* onto this subgraph's connection, returning the OLD name.

        Ports ``m_driver_connection->Clone( *conn )``, which every renaming phase
        calls. The old name comes back because the caller must recache the
        name index with it (`:3196-3200`).

        Both name forms move, because a phase that moves only the unqualified one
        leaves the qualified form stale and the pre-rename name keeps winning
        downstream. That was a real defect in this engine.
        """
        old_name = self.full_name or self.name
        self.conn_members = clone_member_list(self.conn_type, self.conn_members, src)
        # AFTER the member list, which reads the ORIGINAL type (`:341`).
        self.conn_type = src.type
        self.name = src.cached_name
        self.conn_sheet = src.sheet
        self.conn_sheet_prefix = src.sheet_prefix
        self.conn_prepend = src.prepend
        self.full_name = src.qualified_name
        return old_name

    @property
    def members(self) -> frozenset[tuple[str, str]]:
        """The netlist NODE set: a reference and a pin number per member.

        Two exclusions, and both are rules about the netlist view rather than
        about connectivity. Every excluded pin is still electrically on this net
        and still takes part in naming it.

        A pin whose parent is a power symbol is excluded, because such a symbol's
        reference begins with a hash and the exporter skips it outright
        (`netlist_exporter_base.cpp`, the ``ref[0] == '#'`` test). It still
        drives the name.

        A pin whose parent symbol, or any sheet above it, is excluded from the
        board is excluded too (`netlist_exporter_xml.cpp:316`, `:1178`). A test
        point or a shield marked as not on the board is the usual case.

        ``pins`` keeps everything, so a caller that wants true connectivity
        rather than a netlist reads that instead.
        """
        return frozenset(
            (p.reference, p.pin_number)
            for p in self.pins
            if not p.from_power_symbol and not p.excluded_from_board
        )

    @property
    def needs_unconnected_prefix(self) -> bool:
        """The exact phase 9f condition (`connection_graph.cpp:2606-2614`).

        Not strongly driven, exactly one driver, and that driver is a pin. This is
        a SEPARATE final pass from the first naming, and the two produce different
        prefixes for the same pin. An engine that names only once gets a floating
        pin wrong.
        """
        return (
            not self.strong_driver
            and len(self.drivers) == 1
            and self.drivers[0].kind is ItemKind.PIN
        )


def prepend_path(driver: Driver | None) -> bool:
    """Whether a driver's net name carries its sheet path.

    Ports the switch in ``recacheName`` (`sch_connection.cpp:417-445`). This is
    the authoritative form of a rule an earlier attempt guessed at.

    False for a global label, because a global name is not scoped. False for an
    ordinary pin and for a GLOBAL power pin. **True for a LOCAL power pin**,
    which is the case the naive reading gets backwards. True by default, so a
    local label, a hierarchical label, and a sheet pin all carry the path.
    """
    if driver is None:
        return False
    if driver.kind is ItemKind.GLOBAL_LABEL:
        return False
    if driver.kind is ItemKind.PIN:
        # Only a local power pin prepends. A normal pin and a global power pin
        # do not, which is why an implicit name never carries a path.
        return driver.priority is Priority.LOCAL_POWER_PIN
    return True


def qualified_name(driver: Driver | None, bare_name: str, sheet_prefix: str) -> str:
    """Build the sheet-qualified form, porting `sch_connection.cpp:449-452`.

    The sheet prefix escapes each component, so a sheet name holding a slash
    cannot corrupt the path. The caller supplies the already-escaped prefix.
    """
    return f"{sheet_prefix}{bare_name}" if prepend_path(driver) else bare_name


@dataclass
class BusNeighborLink:
    """One entry of ``m_bus_neighbors`` (`connection_graph.h:278`).

    Source keys that map on the member CONNECTION, taken from a COPY of the bus
    subgraph's driver connection made at phase 8 (`connection_graph.cpp:2089`).
    The key is therefore FROZEN at phase 8 while the subgraph's own connection
    keeps being renamed, and ``matchBusMember`` re-resolves the key against the
    live connection every time.

    A map keyed on the member NAME cannot express that: the name is exactly what
    changes. That is why this is a list of frozen-member and neighbours pairs.
    """

    member: MemberConn
    neighbors: list[Subgraph] = field(default_factory=list)
