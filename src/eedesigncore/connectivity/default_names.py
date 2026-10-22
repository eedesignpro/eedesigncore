"""The naming layer: ``getDefaultConnection``, ``GetDefaultNetName``, and the
phase 9f unconnected-prefix fixup.

Ported, line range by line range, from:

- ``eeschema/sch_pin.cpp:1435-1542`` ``SCH_PIN::GetDefaultNetName``
- ``eeschema/connection_graph.cpp:3243-3273`` ``CONNECTION_GRAPH::getDefaultConnection``
- ``eeschema/connection_graph.cpp:2602-2657`` the second ``UpdateItemConnections``
  pass, specifically the fixup block at ``:2606-2614``

ADR-090 governs this module: port the source, do not fit an oracle. A pin is
named TWICE, by two different call sites, with two different results for the
same floating pin:

1. Driver resolution names it (``CONNECTION_SUBGRAPH::driverName``,
   ``connection_graph.cpp:399-405``, itself outside this module's scope) with
   ``aForceNoConnect`` taken from the subgraph's OWN ``m_no_connect`` state.
2. The phase 9f fixup (``:2606-2614``) renames it, unconditionally forcing
   ``aForceNoConnect = true``, whenever the subgraph is weakly driven, has
   exactly one driver, and that driver is a pin
   (``Subgraph.needs_unconnected_prefix``). This is why an otherwise-floating
   pin ends up ``unconnected-(...)`` and not ``Net-(...)``.

Both call sites share the same core, ``get_default_net_name``, which takes
``force_no_connect`` as an explicit parameter exactly as the real function
does. They are kept as separate functions below (``get_default_net_name`` for
the shared core, ``apply_unconnected_prefix_fixup`` for the phase 9f gate plus
call) rather than one function whose caller has to know when to flip a flag.

Known gaps, called out at the point each arises rather than silently
approximated:

- No per-pin ``m_net_name_map`` cache (``sch_pin.cpp:1457-1465``, ``:1538-1539``).
  Pure memoization; the returned string never depends on it.
- No symbol UUID on ``PinRef``, so Branch A (``sch_pin.cpp:1504-1515``, the
  unannotated-symbol case) cannot build its ``<uuid>-Pad<n>`` form. Raises
  ``UnannotatedSymbolNameNotSupported`` instead of guessing.
- No stacked-pin de-stacking data on ``PinRef``
  (``SCH_PIN::GetEffectivePadNumber`` / ``GetSmallestLogicalNumber``,
  ``sch_pin.cpp:622-640``). ``effective_pad_number`` falls back to the raw pin
  number, which is exact for every pin that is not part of a stacked group.
- No pin-visibility data on ``PinRef``. The power short circuit's legacy
  ``else`` branch (``sch_pin.cpp:1449-1454``, an invisible power-input pin on a
  NON-power symbol) can never be reached here, because ``PinRef.is_power`` is
  already narrowed to require ``from_power_symbol`` (see that property's own
  docstring). A design that relies on that legacy behavior is named as a plain
  pin instead.
- ``PinRef.symbol_value`` is the raw ``Value`` field text. The real call is
  ``symbol->GetValue( true, &aPath, false )`` (``sch_pin.cpp:1447``), which
  resolves text variables first. No variable resolution happens here.
"""

from __future__ import annotations

from collections.abc import Sequence

from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.netname import escape_netname
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Subgraph

# getDefaultConnection only builds a connection for these label kinds
# (connection_graph.cpp:3256-3260). Everything else falls to the `default:`
# case at :3262-3264.
_LABEL_KINDS = frozenset({ItemKind.GLOBAL_LABEL, ItemKind.HIER_LABEL, ItemKind.LABEL})


class UnannotatedSymbolNameNotSupported(NotImplementedError):
    """Branch A (`sch_pin.cpp:1504-1515`) needs a symbol UUID.

    KiCad builds ``<symbol-uuid>-Pad<n>`` for a pin on an unannotated symbol
    (reference ends in ``?``). ``PinRef`` carries no symbol UUID, so this name
    cannot be built. Raised rather than approximated.
    """


def get_default_connection_name(driver: Driver) -> str | None:
    """Port of ``getDefaultConnection`` (`connection_graph.cpp:3243-3273`).

    Only two item shapes build a connection there: a pin with ``IsPower()``
    true (``:3250-3254``), and a global, hierarchical, or plain label
    (``:3256-3260``). Everything else returns null (``:3262-3264``).

    ``SCH_PIN::IsPower()`` is ``IsLocalPower() || IsGlobalPower()``
    (`sch_pin.cpp:383-386`), both of which require the parent library symbol to
    be a power symbol on the path this module can see. ``PinRef.is_power``
    encodes exactly that narrow reading (see its own docstring), so
    ``driver.pin.is_power`` stands in for ``IsPower()`` here.

    On a hit, the source builds a connection and calls
    ``ConfigureFromLabel( aSubgraph->GetNameForDriver( aItem ) )``
    (``:3266-3270``). ``Driver.name`` already carries that resolved,
    driver-name-cache value (see the ``Driver`` docstring), so this returns it
    directly instead of recomputing it.
    """
    if driver.kind is ItemKind.PIN:
        if driver.pin is not None and driver.pin.is_power:
            return driver.name
        return None

    if driver.kind in _LABEL_KINDS:
        return driver.name

    return None


def get_default_net_name(
    pin: PinRef,
    force_no_connect: bool,
    sibling_pins: Sequence[PinRef] = (),
) -> str:
    """Port of ``SCH_PIN::GetDefaultNetName`` (`sch_pin.cpp:1435-1542`).

    *sibling_pins* stands in for ``symbol->GetPins( &aPath )`` (`:1478`), the
    full pin list of the symbol on this sheet path, used only to compute
    ``has_multiple`` (`:1481-1490`). Including or excluding *pin* itself in
    *sibling_pins* makes no difference: the source's own loop compares each
    sibling's shown number against ``GetShownNumber()``, so a pin never
    matches itself (``pin->GetShownNumber() != GetShownNumber()`` is false for
    the pin against itself).
    """
    # Power short circuit, runs before any of the three branches
    # (`sch_pin.cpp:1441-1455`). Only the `parent is a power symbol` arm is
    # reachable here; see the module docstring for the unreachable legacy arm.
    if pin.is_power:
        return escape_netname(pin.symbol_value)

    # `sch_pin.cpp:1467-1474`.
    unconnected = force_no_connect or pin.is_no_connect
    name = "unconnected-(" if unconnected else "Net-("

    # `sch_pin.cpp:1478-1490`. The pin list comes from the pin itself when the
    # caller supplies none, because the geometry stage is the only place that
    # holds the whole symbol. Passing nothing used to mean has_multiple was
    # always false, which silently dropped the pad suffix from every repeated
    # pin name and changed which pin got to name an unlabelled net.
    siblings: tuple[tuple[str, str, bool], ...] = (
        tuple((s.pin_name, s.pin_number, s.is_no_connect) for s in sibling_pins)
        if sibling_pins
        else pin.symbol_pins
    )
    has_multiple = any(
        name == pin.pin_name and number != pin.pin_number and unconnected == is_nc
        for name, number, is_nc in siblings
    )

    # `sch_pin.cpp:1492-1494`. No stacked-pin de-stacking data is available;
    # see the module docstring.
    effective_pad_number = pin.pin_number

    # `sch_pin.cpp:1504-1515`, Branch A: the reference is unannotated, meaning it
    # ends in a question mark, so the name uses the SYMBOL identifier instead.
    # `GetRefDesUnannotated` builds that form as prefix plus "?"
    # (`common/refdes_utils.cpp:48-51`), and the source tests the last character.
    if not pin.is_annotated:
        if not pin.symbol_uuid:
            raise UnannotatedSymbolNameNotSupported(
                f"pin {pin.pin_number!r} of {pin.reference!r} is on an "
                "unannotated symbol (sch_pin.cpp:1504-1515), and the name needs "
                "the symbol identifier, which this PinRef does not carry."
            )
        # The raw pin NUMBER, not the shown number (`:1508`).
        name += pin.symbol_uuid
        name += "-Pad" + effective_pad_number + ")"
        return name

    # `sch_pin.cpp:1516-1530`, Branch B: the pin has a name distinct from its
    # number. The reference carries the unit token, because a pin name can
    # repeat across units (`:1518-1519`).
    library_name = pin.library_pin_name if pin.library_pin_name is not None else pin.pin_name
    if library_name and library_name != pin.pin_number:
        name += pin.reference + pin.unit_letter
        name += "-" + escape_netname(library_name)
        if unconnected or has_multiple:
            name += "-Pad" + escape_netname(effective_pad_number)
        name += ")"
        return name

    # `sch_pin.cpp:1531-1536`, Branch C: the pin name equals the number, or is
    # empty. Pin numbers are unique, so NO unit token (`:1533`).
    name += pin.reference
    name += "-Pad" + escape_netname(effective_pad_number) + ")"
    return name


def apply_unconnected_prefix_fixup(
    subgraph: Subgraph,
    pin: PinRef,
    sibling_pins: Sequence[PinRef] = (),
) -> str | None:
    """Port of the phase 9f fixup block (`connection_graph.cpp:2606-2614`).

    Runs the ``Subgraph.needs_unconnected_prefix`` gate, which already encodes
    the exact condition at ``:2606-2608`` (not strongly driven, exactly one
    driver, that driver is a pin). When it holds, recomputes the name with
    ``force_no_connect`` forced to ``True`` regardless of the subgraph's own
    no-connect state (``:2611``, the literal ``true`` argument) and returns it
    for the caller to apply via ``ConfigureFromLabel`` (``:2613``). Returns
    ``None`` when the gate does not hold, meaning no rename happens.

    *pin* must be ``subgraph.drivers[0].pin``; this function does not reach
    into the subgraph to find it, matching the source's use of
    ``subgraph->m_driver`` cast to ``SCH_PIN*`` (``:2610``) without adding
    lookup behavior of its own.
    """
    if not subgraph.needs_unconnected_prefix:
        return None
    return get_default_net_name(pin, force_no_connect=True, sibling_pins=sibling_pins)
