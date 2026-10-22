"""The connectivity pipeline, in KiCad's own call order.

Source: ``CONNECTION_GRAPH::Recalculate`` (`connection_graph.cpp:750-899`) and
``buildConnectionGraph`` (`:2257-2803`), tag 10.0.3. The order below is the call
sequence the audit ``connection-graph-algorithm-2026-09-02.md`` recorded, not a
summary of it.

**The order is the contract.** Several phases set state a later phase reads, and
two of them are order-sensitive on purpose. Running them in a different sequence
gives different nets, so this module is deliberately a flat, readable list of
calls rather than anything clever.

Phases not yet ported are named and skipped explicitly. A caller can read this
file and see exactly what the engine does and does not do, which is the property
ADR-090 asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from eedesigncore.connectivity.bus import AliasResolver
from eedesigncore.connectivity.bus_members import generate_bus_alias_members
from eedesigncore.connectivity.bus_propagate import link_multi_parent_buses
from eedesigncore.connectivity.connection import configure_connection
from eedesigncore.connectivity.default_names import apply_unconnected_prefix_fixup
from eedesigncore.connectivity.driver_values import collect_all_driver_values
from eedesigncore.connectivity.drivers import resolve_all
from eedesigncore.connectivity.global_names import close_global_names
from eedesigncore.connectivity.net_index import NetNameIndex
from eedesigncore.connectivity.process import process
from eedesigncore.connectivity.propagate import propagate_all
from eedesigncore.connectivity.subgraph import (
    Priority,
    Subgraph,
    prepend_path,
    qualified_name,
)


@dataclass(frozen=True)
class DerivedNet:
    """One derived net. A name and the pins on it, and never a code.

    A net code's value depends on schematic file item order through several
    layers, so it is not reproducible and this engine does not model one.
    """

    name: str
    members: frozenset[tuple[str, str]]


@dataclass
class Report:
    """What each phase did, so a caller can see the pipeline work."""

    subgraphs_built: int = 0
    driver_subgraphs: int = 0
    globals_canonicalized: int = 0
    surviving_after_absorption: int = 0
    propagation_renames: int = 0
    unconnected_fixups: int = 0
    bus_relinks: int = 0
    bus_member_subgraphs: int = 0
    skipped_phases: list[str] = field(default_factory=list)
    resolved_subgraphs: list[Subgraph] = field(default_factory=list, repr=False)


def _name_subgraph(
    subgraph: Subgraph, sheet_prefix: str, resolve_alias: AliasResolver | None = None
) -> None:
    """Configure a subgraph's driver connection, after its driver is resolved.

    Ports the tail of ``ResolveDrivers`` (`:274-280`): the connection is
    configured from the winning driver's name, the driver is attached, and the
    name is recached. So this sets both name forms AND the connection's own bus
    state, because ``ConfigureFromLabel`` classifies the name and builds the
    member list at the same moment.

    Source classifies the driver's name cache (`:277`), which is the ESCAPED
    form. The bus grammar wants the raw shown text (`:1747`), and the two differ
    only for a name holding a character the escape rule touches, so the raw form
    goes to the classifier and the escaped form stays the name.
    """
    driver = subgraph.driver
    if driver is None:
        return
    subgraph.name = driver.name
    subgraph.conn_sheet = subgraph.sheet_path
    subgraph.conn_sheet_prefix = sheet_prefix
    subgraph.conn_prepend = prepend_path(driver)
    subgraph.full_name = qualified_name(driver, driver.name, sheet_prefix)
    # The driver travels down into every member, per ``SetDriver`` (`:278`), so
    # a member inherits the prepend decision rather than defaulting to carrying
    # the path. This is what leaves a GLOBAL bus label's members unqualified.
    subgraph.conn_type, subgraph.conn_members = configure_connection(
        driver.raw_name,
        sheet=subgraph.sheet_path,
        sheet_prefix=sheet_prefix,
        prepend=subgraph.conn_prepend,
        resolve_alias=resolve_alias,
    )


def run(
    subgraphs: list[Subgraph],
    *,
    sheet_prefixes: dict[tuple[str, ...], str] | None = None,
    resolve_alias: AliasResolver | None = None,
) -> tuple[list[DerivedNet], Report]:
    """Run every ported phase in KiCad's call order.

    *subgraphs* comes from the geometry stage, which ports phases 2 and 3.
    *sheet_prefixes* maps a sheet path to its escaped net-name prefix.

    *resolve_alias* looks up a bus alias by name, porting
    ``CONNECTION_GRAPH::GetBusAlias`` (`:3381-3386`), which reads a cache the
    graph fills before phase 3 from the whole design's alias list (`:1502-1507`).
    Source reaches it from every phase through the graph pointer each connection
    holds, so it is effectively ambient there. This port passes it explicitly
    instead, because every OTHER piece of state in this engine is explicit and a
    phase that silently loses it is exactly the failure this engine keeps hitting.

    A caller that passes nothing gets no alias expansion, and a bus written as a
    single alias reference then has ONE member named after the alias rather than
    the members it stands for. Its member nets still get their names from their
    own labels, so a flat sheet looks correct; what breaks is matching those
    members across a sheet boundary, because the two sides never agree on the
    member list.
    """
    report = Report(subgraphs_built=len(subgraphs))
    prefixes = sheet_prefixes or {}

    # Phase 4. resolveAllDrivers (`:2282`). Keeps only the subgraphs that got a
    # driver, which is what m_driver_subgraphs holds.
    driver_subgraphs = resolve_all(subgraphs)
    for subgraph in driver_subgraphs:
        _name_subgraph(subgraph, prefixes.get(subgraph.sheet_path, "/"), resolve_alias)
    report.driver_subgraphs = len(driver_subgraphs)

    # Phase 5. collectAllDriverValues (`:2284`). Builds the name maps that the
    # weak-name collision check in phase 8 reads, and re-arms every subgraph.
    collect_all_driver_values(driver_subgraphs)

    # Phase 6. generateGlobalPowerPinSubGraphs (`:2286`). Reaches across sheets
    # by name equality. Its inputs come from the geometry stage, so a caller that
    # has none simply contributes no extra subgraphs here.
    report.skipped_phases.append(
        "phase 6 runs from power_subgraphs.generate_global_power_pin_subgraphs, "
        "which the geometry stage feeds directly rather than this orchestrator"
    )

    # Phase 7. generateBusAliasMembers (`:2288`). A bus label expands into one
    # VIRTUAL subgraph per net member, carrying a name and no pins. The pins
    # arrive later, when phase 8 links a same-sheet net to a member name and
    # propagation names it. Without this phase there is nothing to link, which is
    # why bus propagation ran and did nothing.
    members = generate_bus_alias_members(driver_subgraphs, resolve_alias=resolve_alias)
    driver_subgraphs = [*driver_subgraphs, *members]
    report.bus_member_subgraphs = len(members)

    # Phase 8. processSubGraphs (`:2291`). Same-sheet absorption, the weak-name
    # suffix, and sheet-pin promotion. Re-arms every survivor for propagation.
    surviving = process(driver_subgraphs, resolve_alias=resolve_alias)
    report.surviving_after_absorption = len(surviving)

    # Phase 9b. The global-name equivalence closure (`:2344-2452`). MUST run
    # before the promotion loop below, because that loop is order-dependent and
    # keys off each subgraph's original driver text.
    globals_only = [s for s in surviving if _is_global(s)]
    report.globals_canonicalized = close_global_names(globals_only)

    # The net-name index, which phase 9e reads and every phase 9 rename updates.
    # Source keeps it from phase 5 onward; built here because 9e is its only
    # reader and no earlier phase consults it.
    index = NetNameIndex(surviving)

    # Phases 9c and 9d. Propagation, then the forced sweep for whatever the
    # deferral guard skipped (`:2454-2522`). Bus member propagation runs from
    # INSIDE this call, at its two source call sites, and not afterwards.
    report.propagation_renames = propagate_all(surviving, index, resolve_alias=resolve_alias)

    # Phase 9e. Two buses shorted together by a shared member net (`:2524-2597`).
    # A design with no such short passes through untouched.
    report.bus_relinks = link_multi_parent_buses(surviving, index)

    # Phase 9f. The unconnected-prefix fixup (`:2602-2665`). A SEPARATE final
    # pass. A subgraph named once by driver resolution can still be renamed here,
    # and the two passes produce different prefixes for the same pin.
    for subgraph in surviving:
        if not subgraph.needs_unconnected_prefix:
            continue
        driver = subgraph.drivers[0]
        if driver.pin is None:
            continue
        # `has_multiple` scans the other pins of the SAME symbol on this sheet
        # (`sch_pin.cpp:1481-1490`), so the siblings are the subgraph's pins that
        # share this pin's reference.
        siblings = [p for p in subgraph.pins if p.reference == driver.pin.reference]
        fixed = apply_unconnected_prefix_fixup(subgraph, driver.pin, siblings)
        if not fixed:
            continue
        # The fixup returns the name for the caller to apply, mirroring
        # ConfigureFromLabel at `:2613`. BOTH forms must move, or the stale
        # qualified form keeps winning downstream.
        subgraph.name = fixed
        subgraph.full_name = qualified_name(driver, fixed, prefixes.get(subgraph.sheet_path, "/"))
        subgraph.conn_prepend = prepend_path(driver)
        report.unconnected_fixups += 1

    # Phase 9h. Netclass label assignment (`:2679-2802`). A directive label
    # attaches a class and never names a net. The caller supplies the labels,
    # because they come from the geometry stage.
    report.skipped_phases.append(
        "phase 9h attaches net classes through netclasses.assign, which a caller "
        "drives from the directive labels the geometry stage collected"
    )

    report.resolved_subgraphs = surviving
    return _to_nets(surviving), report


def _is_global(subgraph: Subgraph) -> bool:
    """A subgraph whose driver is global, which is what the closure operates on."""
    return subgraph.driver is not None and subgraph.driver.priority >= Priority.GLOBAL_POWER_PIN


def _to_nets(subgraphs: list[Subgraph]) -> list[DerivedNet]:
    """Collapse subgraphs into nets by resolved name.

    Two subgraphs that ended on the same name are the same net. That is the
    result of the phases above rather than a rule of its own, which is why this
    happens here and not inside any phase.
    """
    merged: dict[str, set[tuple[str, str]]] = {}
    for subgraph in subgraphs:
        if subgraph.absorbed:
            continue
        name = subgraph.full_name or subgraph.name
        if not name:
            continue
        merged.setdefault(name, set()).update(subgraph.members)
    return [
        DerivedNet(name=name, members=frozenset(members))
        for name, members in merged.items()
        if members
    ]


def derive_from_sheet(root_sheet: Path) -> tuple[list[DerivedNet], Report]:
    """Derive every net of a design, from its root sheet. Phase 1.

    Ports ``CONNECTION_GRAPH::Recalculate`` (`connection_graph.cpp:750-899`): walk
    every sheet path, run the per-sheet phases on each, then run the whole-design
    phases once over the collected subgraphs.

    The per-sheet part is phases 2 and 3. Everything after is whole-design, which
    is why a global net can cross a sheet without a wire.
    """
    from eedesigncore.common.text_variables import resolve as resolve_text_variables
    from eedesigncore.connectivity.build import build_subgraphs
    from eedesigncore.connectivity.geometry import collect
    from eedesigncore.connectivity.netname import escape_netname
    from eedesigncore.sch.models import Schematic

    def field(item: object, name: str) -> str:
        for prop in getattr(item, "fields", []) or []:
            if getattr(prop, "name", None) == name:
                return str(getattr(prop, "value", ""))
        return ""

    all_subgraphs: list[Subgraph] = []
    prefixes: dict[tuple[str, ...], str] = {}
    seen_files: set[Path] = set()

    # Project-level text variables sit at the outermost scope. A sheet field
    # overrides them for that sheet and everything below it.
    project_vars: dict[str, str] = {}
    # Bus aliases live in the PROJECT file, not in any schematic
    # (`common/project/project_file.cpp:117-165`, key "schematic.bus_aliases").
    # A design whose buses are written as a single alias reference cannot be
    # resolved without them.
    bus_aliases: dict[str, list[str]] = {}
    project_file = root_sheet.with_suffix(".kicad_pro")
    if project_file.exists():
        try:
            from eedesigncore.pro.models import ProjectFile

            project = ProjectFile.from_file(project_file)
            project_vars = dict(project.text_variables)
            bus_aliases = dict(project.bus_aliases)
        except Exception:
            # A project file this library cannot read must not stop connectivity.
            project_vars = {}
            bus_aliases = {}

    def resolve_alias(name: str) -> list[str] | None:
        """``GetBusAlias``: the alias's members, or None when it is not an alias."""
        return bus_aliases.get(name)

    def visit(
        path: Path,
        names: tuple[str, ...],
        uuids: tuple[str, ...],
        scope: dict[str, str],
        depth: int,
        excluded_from_board: bool = False,
    ) -> None:
        # Bounded, because a cyclic sheet reference would otherwise walk forever
        # (`sch_sheet_path.cpp:665`, TestForRecursion).
        if depth > 32 or not path.exists():
            return
        schematic = Schematic.from_file(path)

        # The net-name prefix for this sheet. Each component escapes, so a sheet
        # name holding a slash cannot corrupt the path.
        prefixes[names] = (
            "/" if not names else "/" + "/".join(escape_netname(n) for n in names) + "/"
        )

        # Each child sheet's scope and resolved name, computed BEFORE the
        # geometry pass, because a sheet pin records the child sheet it opens
        # onto and propagation matches that against a resolved path component.
        child_scopes: dict[str, dict[str, str]] = {}
        child_names: dict[str, str] = {}
        for sheet in schematic.sheets:
            scope_for_child = dict(scope)
            for prop in getattr(sheet, "fields", []) or []:
                prop_name = getattr(prop, "name", "")
                if prop_name not in ("Sheetname", "Sheetfile"):
                    scope_for_child[prop_name] = str(getattr(prop, "value", ""))
            key = str(sheet.uuid or "")
            child_scopes[key] = scope_for_child
            child_names[key] = resolve_text_variables(field(sheet, "Sheetname"), scope_for_child)

        # Phases 2 and 3, per sheet.
        # The instance path: the ROOT schematic identifier, then each sheet
        # identifier down to here. A reference resolves against this.
        uuid_path = "/" + ("/".join(uuids) if uuids else str(schematic.uuid or ""))
        all_subgraphs.extend(
            build_subgraphs(
                collect(schematic, uuid_path, child_names, excluded_from_board),
                names,
                resolve_alias=resolve_alias,
            )
        )

        for sheet in schematic.sheets:
            child_file = field(sheet, "Sheetfile")
            if not child_file:
                continue
            child_path = path.parent / child_file
            # The same FILE may appear at several places in the tree, and each
            # place is its own instance. Recursion is bounded by depth, not by
            # file identity, so a repeated sheet is walked once per instance.
            # A sheet's own fields join the scope for that sheet and below, which
            # is why two instances of one file can resolve to different names.
            key = str(sheet.uuid or "")
            child_scope = child_scopes[key]
            resolved_name = child_names[key]

            root_uuid = uuids[0] if uuids else str(schematic.uuid or "")
            # A sheet excluded from the board excludes everything below it
            # (`netlist_exporter_xml.cpp:316`), so the flag accumulates downward
            # and is never re-read from a child.
            visit(
                child_path,
                (*names, resolved_name),
                (root_uuid, *uuids[1:], str(sheet.uuid or "")),
                child_scope,
                depth + 1,
                excluded_from_board or getattr(sheet, "on_board", None) is False,
            )

    visit(root_sheet, (), (), project_vars, 0)
    seen_files.clear()
    return run(all_subgraphs, sheet_prefixes=prefixes, resolve_alias=resolve_alias)
