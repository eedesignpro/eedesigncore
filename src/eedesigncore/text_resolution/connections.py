"""Reuse the schematic connectivity engine for pin and label text variables."""

from __future__ import annotations

from dataclasses import replace

from eedesigncore.connectivity.build import build_subgraphs
from eedesigncore.connectivity.geometry import collect
from eedesigncore.connectivity.pipeline import run
from eedesigncore.connectivity.subgraph import ItemKind, Subgraph
from eedesigncore.sch.models import Schematic
from eedesigncore.text_resolution.models import TextResolutionContext


def with_connections(
    schematic: Schematic, context: TextResolutionContext
) -> TextResolutionContext:
    from eedesigncore.text_resolution.documents import context_for_sheet, resolve_document_text

    base = replace(context, resolve_connections=False)
    entries = [(schematic, base)]
    aliases: dict[str, list[str]] = {}
    if context.hierarchy:
        entries = [
            (node.schematic, context_for_sheet(context.hierarchy, node.kiid_path, context=base))
            for node in context.hierarchy.walk()
        ]
        if context.hierarchy.project:
            aliases = dict(context.hierarchy.project.bus_aliases)
    groups: list[Subgraph] = []
    prefixes: dict[tuple[str, ...], str] = {}
    pin_sources: dict[int, tuple[str, str, str]] = {}
    label_sources: dict[str, tuple[str, str, str]] = {}
    label_classes: dict[str, set[str]] = {}
    for source, ctx in entries:
        shown = resolve_document_text(source, ctx).document
        instance = ctx.instance_path or "/" + (source.uuid or "root")
        names = tuple(instance.strip("/").split("/"))
        prefixes[names] = ctx.sheet_path or "/"
        child_names = {sheet.uuid: sheet.uuid for sheet in shown.sheets if sheet.uuid}
        geometry = collect(shown, instance, child_names)
        for pin in geometry.pins:
            symbol = source.symbols[pin.symbol_index]
            pin_sources[id(pin)] = (
                instance,
                symbol.uuid or f"symbol:{pin.symbol_index}",
                pin.pin_number,
            )
        for index, label in enumerate(shown.labels):
            label_sources[f"{instance}:{index}"] = (instance, label.uuid or f"label:{index}", "")
            label_classes[f"{instance}:{index}"] = {
                field.value for field in label.fields if field.name == "Netclass" and field.value
            }
        if any(not pin.is_annotated for pin in geometry.pins):
            continue
        identities: dict[tuple[ItemKind, int], str] = {}
        counters = {"label": 0, "directive": 0}
        for index, label in enumerate(shown.labels):
            kind = {
                "label": ItemKind.LABEL,
                "global_label": ItemKind.GLOBAL_LABEL,
                "hierarchical_label": ItemKind.HIER_LABEL,
                "netclass_flag": ItemKind.DIRECTIVE_LABEL,
            }[label.kind.value]
            counter = "directive" if kind == ItemKind.DIRECTIVE_LABEL else "label"
            identities[kind, counters[counter]] = f"{instance}:{index}"
            counters[counter] += 1
        pin_index = 0
        for sheet_index, child in enumerate(shown.sheets):
            for local_index, sheet_pin in enumerate(child.pins):
                identity = f"{instance}:sheet_pin:{sheet_index}:{local_index}"
                identities[ItemKind.SHEET_PIN, pin_index] = identity
                label_sources[identity] = (
                    instance,
                    sheet_pin.uuid or f"sheet_pin:{sheet_index}:{local_index}",
                    "",
                )
                pin_index += 1
        groups.extend(
            build_subgraphs(
                geometry,
                names,
                resolve_alias=aliases.get,
                source_items=identities,
                retain_undriven=True,
            )
        )
    _, report = run(groups, sheet_prefixes=prefixes, resolve_alias=aliases.get)
    connections = dict(context.connections)
    assignments: dict[str, set[str]] = {}
    bus_names: set[str] = set()
    for group in report.resolved_subgraphs:
        name = group.full_name or group.name
        if group.is_bus:
            bus_names.add(name)
        for pin in group.pins:
            if id(pin) in pin_sources:
                connections[pin_sources[id(pin)]] = name
        for identity in group.source_items:
            if identity in label_sources:
                connections[label_sources[identity]] = name
                assignments.setdefault(name, set()).update(label_classes.get(identity, set()))
    from eedesigncore.text_resolution.netclasses import names_for_nets

    project = context.project or (context.hierarchy.project if context.hierarchy else None)
    classes = names_for_nets(connections.values(), project, assignments)
    if any(source.rule_areas for source, _ in entries) and (
        project is None or project.saved_netclass_assignments is None
    ):
        classes = {}
    for name in bus_names:
        classes.pop(name, None)
    return replace(
        context,
        connections={**connections, **context.connections},
        resolve_connections=False,
        net_classes={**classes, **context.net_classes},
    )
