"""Phase 6, ``CONNECTION_GRAPH::generateGlobalPowerPinSubGraphs``.

Source: ``eeschema/connection_graph.cpp:1797-1889``, tag 10.0.3, read in full.
Mapped by audit ``connection-graph-algorithm-2026-09-02.md``, section
"Phase 6" (lines 515-586) and "Answers to the specific questions" 2 and 3
(lines 1093-1118).

ADR-090 governs this module. Port the source. Do not fit an oracle. A phase
with no counterpart in source does not ship.

**What this phase does.** It merges global power pins that resolve to the
same net name into one subgraph, regardless of sheet, by keying on net code
(audit line 1109). This is one of two places, and the only phase-6-sized
place, where a global net, including a power net, reaches across a sheet
boundary without a hierarchy walk (audit "Answers to the specific
questions" 3, lines 1104-1118). The other mechanism,
``propagateToNeighbors``, is a different phase and out of this module's
scope.

**The deliberate sort.** Source sorts ``m_global_power_pins`` before
processing it (``:1807-1828``), and the comment at ``:1804-1806`` states why:
so that, when several power pins share a net name, the SAME pin always
creates the merged subgraph, run after run. This is one of only two places
in the whole pipeline where KiCad imposes an order on purpose, rather than
relying on order-independent comparisons (audit "Impact on eedesigncore",
lines 1279-1282). ``_sort_key`` below ports it: sheet path first
(``SCH_SHEET_PATH::Cmp``, ``sch_sheet_path.cpp:197-216``, length before
content), then the resolved reference (``SCH_SYMBOL::GetRef``,
``sch_symbol.cpp:624-671``), then the pin number.

**Net codes are not modeled, on purpose.** ``getOrCreateNetCode``
(``connection_graph.cpp:2806-2823``) is a first-come counter keyed by name.
The SAME name always returns the SAME code inside one run, and a different
name always returns a different code. Grouping the local
``global_power_pin_subgraphs`` map (``:1830``) by name is therefore
behaviorally identical to grouping it by code, without carrying a value that
``subgraph.py`` and every other phase in this package already refuse to
model, because a code's numeric value depends on schematic-file item order
through several layers of iteration (audit "Answers to the specific
questions" 7, lines 1173-1195).

**``ResolveDrivers`` is reused, not reinvented.** Source creates the founding
subgraph for a name and calls ``subgraph->ResolveDrivers()`` on it
(``:1877``) before registering it. ``ResolveDrivers`` itself lives at
``connection_graph.cpp:132-287``, phase 4, and is already ported at
``drivers.resolve()`` against the same ``Subgraph``/``Driver`` model this
module uses. This module calls it at the same point source does, and does
not duplicate its logic.

**A later pin sharing a name does NOT re-run driver resolution.** Source's
merge branch (``:1864-1867``) only calls ``subgraph->AddItem(pin)``, never
``ResolveDrivers`` again. So the founding pin, meaning the first pin the sort
places under a given name, permanently stays the subgraph's ``m_driver``,
even after later same-name pins join. ``m_multiple_drivers`` never becomes
true here either, because it is a ``ResolveDrivers`` side effect and
``ResolveDrivers`` runs exactly once per subgraph in this phase. A port that
re-resolves on every merge picks a different, non-source driver whenever the
founding pin is not first in whatever order the port happens to iterate.

**``m_dirty`` starts false here, unlike phase 3.** The ``CONNECTION_SUBGRAPH``
constructor initializes ``m_dirty( false )`` (``connection_graph.h:80-82``).
``buildItemSubGraphs`` (phase 3) explicitly sets its own new subgraph's
``m_dirty = true`` (``connection_graph.cpp:1575``), and the audit's state
table cites exactly that line for the true value. This phase's own creation
block (``:1861-1885``) never touches ``m_dirty``, so a subgraph this phase
creates stays not dirty. ``Subgraph`` in ``subgraph.py`` defaults ``dirty`` to
``True`` for every creation site alike, which is the correct default for
phase 3 and the wrong one here. This module overrides it explicitly after
creation rather than silently accepting the shared default.

**What this module does NOT have, and why.** Phases 1 through 3 are not
ported in this file. Whether a power pin already sits in a subgraph
(``connection->SubgraphCode() > 0``, ``:1846``) and whether it is already
wired to something else (``pin->ConnectedItems( sheet )``, ``:1836``) are
both facts phase 3's flood fill would establish. Neither exists here, so
``GlobalPowerPinEntry`` carries them as caller-supplied booleans, named after
the exact C++ predicate each one stands in for. A caller with no phase-3
port yet should treat every pin as fresh (both booleans false), which is the
useful default for testing this phase in isolation, but is not a claim about
what a full pipeline run would actually produce.

**The reference gap.** Source sorts by ``SCH_SYMBOL::GetRef( &sheet, false )``
(``:1819-1820``), which resolves a DISTINCT reference per sheet path by
scanning the symbol's ``m_instances`` list for the entry whose path matches
the current sheet, falling back to the raw ``REFERENCE`` field text, and
finally to an unannotated placeholder (``sch_symbol.cpp:635-663``). This
project's ``PinRef.reference`` (``connectivity/geometry.py``) is the raw
``REFERENCE`` field text alone, with no instance lookup. That is exactly
source's own fallback path, used only when no instance matches. For a
symbol placed once, the two agree. For a sheet instantiated more than once,
they can disagree, because each instance can carry a distinct reference
while ``PinRef.reference`` carries the same text for all of them.
``GlobalPowerPinEntry.reference`` is therefore a field separate from
``PinRef.reference``, so a caller with a correct per-instance lookup can
supply it without this module changing. Building that lookup needs the
symbol's serialized instance table (a per-sheet-path reference and unit,
carried in the ``.kicad_sch`` file's ``instances`` block for that symbol),
which ``geometry.py`` does not parse today. This module does not add that
parsing, per the task boundary that reserves ``geometry.py`` for another
agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eedesigncore.connectivity.drivers import resolve
from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


@dataclass(frozen=True)
class GlobalPowerPinEntry:
    """One row of ``m_global_power_pins``, populated by phase 2 (out of scope).

    ``connection_graph.cpp:1204`` (``updateSymbolConnectivity``) and ``:1267``
    (``updatePinConnectivity``) are the two sites that append a pin here, one
    per (sheet, pin) pair on every sheet the pin's symbol is placed on.
    """

    sheet_path: tuple[str, ...]
    pin: PinRef
    # SCH_SYMBOL::GetRef(&sheet, false) for this pin's parent symbol on THIS
    # sheet path. See the module docstring, "The reference gap", for why this
    # is not pin.reference.
    reference: str
    # !pin->ConnectedItems(sheet).empty(), the wiring check at `:1836`.
    already_connected: bool = False
    # connection->SubgraphCode() > 0, the claim check at `:1846`.
    already_has_subgraph: bool = False


@dataclass
class GlobalPowerPinSubgraphsResult:
    """What this phase adds to graph-level state.

    Field names echo the C++ members this phase appends to
    (``connection_graph.h:817, 820, 842``), so a caller assembling the full
    engine can fold these into the real maps directly.
    """

    # m_subgraphs / m_driver_subgraphs (`:1881-1882`). This phase's own new
    # subgraphs only, in the order they were founded.
    subgraphs: list[Subgraph] = field(default_factory=list)
    # m_net_code_to_subgraphs_map (`:1880`), keyed by name rather than
    # (name, code). See the module docstring for why the two are equivalent
    # for grouping purposes.
    net_code_to_subgraphs: dict[str, list[Subgraph]] = field(default_factory=dict)


def _sort_key(entry: GlobalPowerPinEntry) -> tuple[int, tuple[str, ...], str, str]:
    """Port of the comparator at ``:1807-1828``.

    ``SCH_SHEET_PATH::Cmp`` (``sch_sheet_path.cpp:197-216``) compares LENGTH
    first, and only falls to element-wise comparison when both paths hold the
    same number of sheets (``:199-213``). Leading with ``len(sheet_path)``
    reproduces that. The reference and pin number follow, matching
    ``:1822-1827``.

    This assumes each ``sheet_path`` component is a canonical-form sheet
    UUID string, so Python string comparison agrees with ``KIID``'s own
    comparison (``m_Uuid <``, a byte compare `sch_sheet_path.cpp:208`). A
    caller supplying anything else, for example a human-readable sheet name,
    gets a stable but non-source order.
    """
    return (len(entry.sheet_path), entry.sheet_path, entry.reference, entry.pin.pin_number)


def _connection_name(pin: PinRef) -> str:
    """Port of the naming branch at ``:1849-1855``.

    A power symbol names its net from its resolved VALUE field
    (``pin->GetParentSymbol()->GetValue(true, &sheet, false)``, ``:1853``).
    ``PinRef.symbol_value`` is that field's raw text, with no variable
    resolution, an existing gap already recorded in ``geometry.py`` and
    ``naming.py`` and not one this module adds to.

    A legacy invisible power-in pin on a non-power symbol names its net from
    the library pin's shown name (``pin->GetShownName()``, ``:1855``).
    ``PinRef.pin_name`` is that pin's library name, per its own docstring in
    ``geometry.py``, citing the same ``sch_pin.cpp:1441-1455`` this branch
    exists for.
    """
    if pin.from_power_symbol:
        return pin.symbol_value
    return pin.pin_name


def generate_global_power_pin_subgraphs(
    global_power_pins: list[GlobalPowerPinEntry],
) -> GlobalPowerPinSubgraphsResult:
    """Port of ``generateGlobalPowerPinSubGraphs`` (``connection_graph.cpp:1797-1889``).

    Sorts *global_power_pins* with ``_sort_key``, then walks the sorted list
    once, exactly as source does. See the module docstring for the parts of
    source this reuses (``ResolveDrivers``) and the parts it cannot see
    (phase 3's claim state, carried on each entry instead).
    """
    result = GlobalPowerPinSubgraphsResult()
    by_name: dict[str, Subgraph] = {}

    for entry in sorted(global_power_pins, key=_sort_key):
        # `:1836-1841`. A wired-up invisible pin on a non-power symbol is an
        # ERC-flagged user error, not this phase's job. `!libParent ||
        # !libParent->IsGlobalPower()` collapses onto `not from_power_symbol`,
        # because `PinRef.from_power_symbol` is already false when there is
        # no library parent to check.
        if entry.already_connected and not entry.pin.from_power_symbol:
            continue

        # `:1843-1847`. Phase 3 already grouped this pin with something.
        if entry.already_has_subgraph:
            continue

        name = _connection_name(entry.pin)
        driver = Driver(
            kind=ItemKind.PIN,
            name=name,
            priority=Priority.GLOBAL_POWER_PIN,
            pin=entry.pin,
        )

        subgraph = by_name.get(name)

        if subgraph is not None:
            # `:1864-1867`. Join the existing subgraph. No re-resolution, so
            # the founding pin keeps the driver seat. See the module
            # docstring, "A later pin sharing a name does NOT re-run driver
            # resolution."
            subgraph.pins.append(entry.pin)
            subgraph.drivers.append(driver)
        else:
            # `:1869-1884`. Found this name's subgraph on this pin's sheet.
            subgraph = Subgraph(sheet_path=entry.sheet_path)
            subgraph.pins.append(entry.pin)
            subgraph.drivers.append(driver)
            resolve(subgraph)
            # A GLOBAL_POWER_PIN driver never prepends a sheet path
            # (`subgraph.py`'s `prepend_path`, the `ItemKind.PIN` branch), so
            # the sheet-qualified and unqualified forms agree here.
            subgraph.full_name = subgraph.name
            # See the module docstring, "`m_dirty` starts false here, unlike
            # phase 3."
            subgraph.dirty = False

            result.subgraphs.append(subgraph)
            result.net_code_to_subgraphs.setdefault(name, []).append(subgraph)
            by_name[name] = subgraph

    return result
