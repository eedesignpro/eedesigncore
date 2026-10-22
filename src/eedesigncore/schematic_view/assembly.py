"""Generate a flat board-level assembly root without changing electrical truth."""

from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Literal

from pydantic import Field

from eedesigncore.assembly.models import AssemblyEvidence, NetbomAssembly
from eedesigncore.common.base import KicadModel
from eedesigncore.inspection.portable import _object, _text
from eedesigncore.schematic_view.models import (
    SchematicPage,
    SchematicView,
    ViewBounds,
    ViewContact,
    ViewItem,
    ViewSource,
    view_identity,
)


class AssemblyStack(KicadModel):
    """Caller-declared ordered board stack, displayed spread out rather than overlapped."""

    name: str = Field(min_length=1, max_length=256)
    instances: list[str] = Field(min_length=1, max_length=1024)
    evidence: AssemblyEvidence


def _label(value: str) -> list[str]:
    return textwrap.wrap(" ".join(value.split()), width=52) or [""]


def prepare_assembly_view(
    assembly: NetbomAssembly,
    *,
    native_views: dict[str, SchematicView] | None = None,
    stacks: list[AssemblyStack] | None = None,
) -> SchematicView:
    """Build one root containing every board and all declared connector bundles."""
    assembly = assembly.model_copy(deep=True)
    inspector = assembly.inspect()

    def net_name(instance: str, net: str) -> str | None:
        value = inspector._board(instance)._data(net).get("name")
        return value if isinstance(value, str) else None

    supplied = {key: value.model_copy(deep=True) for key, value in (native_views or {}).items()}
    if set(supplied) - set(assembly.instances):
        raise ValueError("Native view names an unknown assembly board.")
    stack_names: set[str] = set()
    stacked: set[str] = set()
    ordered: list[str] = []
    stack_labels: dict[str, str] = {}
    for stack in sorted(stacks or [], key=lambda value: value.name):
        if stack.name in stack_names:
            raise ValueError("Stack names must be unique.")
        stack_names.add(stack.name)
        for index, instance in enumerate(stack.instances):
            if instance not in assembly.instances or instance in stacked:
                raise ValueError("Stack contains an unknown or repeated board occurrence.")
            stacked.add(instance)
            ordered.append(instance)
            stack_labels[instance] = (
                f"Stack {stack.name}: position {index + 1} of {len(stack.instances)}"
            )
    ordered.extend(sorted(set(assembly.instances) - stacked))
    root_id = view_identity(assembly.assembly_id, "assembly-root")
    pages: dict[str, SchematicPage] = {}
    targets: dict[str, list[str]] = {}
    native_notes: list[str] = []
    for instance, view in sorted(supplied.items()):
        view = SchematicView.model_validate(view.model_dump())
        for page in view.pages.values():
            if (
                page.kind != "native_sheet"
                or page.source is None
                or page.source.instance != instance
            ):
                raise ValueError("Native pages must belong to their selected assembly occurrence.")
            if page.id in pages or page.id == root_id:
                raise ValueError("Assembly native page identities collide.")
            if page.id in view.roots:
                page.parent = root_id
            pages[page.id] = page
        targets[instance] = view.roots
        native_notes.extend(f"{instance}: {note}" for note in view.notes)
    # Separate connector rows prevent same-component mate conflicts from sharing anchors.
    rows: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    notes = [
        "Flat board-level root. Block positions are schematic, not physical placement.",
        "Routes are connector bundles, not electrical wires. Crossings never form junctions.",
        "Mechanical placement is unspecified except for caller-declared stack order.",
    ]
    notes.extend(native_notes)
    if not ordered:
        notes.append("Assembly has no board occurrences.")
    for mate_id, mate in sorted(assembly.mates.items()):
        for side, endpoint in (("left", mate.left), ("right", mate.right)):
            if endpoint.instance in assembly.instances:
                rows[endpoint.instance].append((mate_id, side, endpoint.component_id, mate_id))
            else:
                notes.append(f"Mate {mate_id} names missing board {endpoint.instance}.")
    used = {
        (endpoint.instance, endpoint.component_id)
        for mate in assembly.mates.values()
        for endpoint in (mate.left, mate.right)
    }
    for endpoint in sorted(
        assembly.expected_connectors, key=lambda value: (value.instance, value.component_id)
    ):
        if (
            endpoint.instance in assembly.instances
            and (endpoint.instance, endpoint.component_id) not in used
        ):
            rows[endpoint.instance].append(("", "expected", endpoint.component_id, "Unmated"))
        elif endpoint.instance not in assembly.instances:
            notes.append(f"Expected connector names missing board {endpoint.instance}.")
    drawing = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg"})
    ET.SubElement(drawing, "rect", {"width": "100%", "height": "100%", "fill": "#ffffff"})
    items: dict[str, ViewItem] = {}
    anchors: dict[tuple[str, str], tuple[float, float]] = {}
    connector_items: dict[tuple[str, str], ViewItem] = {}

    def text(parent: ET.Element, lines: list[str], x: float, y: float) -> None:
        for index, line in enumerate(lines):
            node = ET.SubElement(
                parent,
                "text",
                {
                    "x": str(x),
                    "y": str(y + index * 5),
                    "font-size": "3.5",
                    "font-family": "monospace",
                    "fill": "#182b3a",
                },
            )
            if line:
                node.set("textLength", str(len(line) * 2.1))
                node.set("lengthAdjust", "spacingAndGlyphs")
            node.text = line

    y = 32.0
    text(drawing, _label(assembly.name), 12, 10)
    title_extra = max(0, len(_label(assembly.name)) - 1) * 5
    text(drawing, ["Bundles: dashed = inferred; red = unresolved"], 12, 19 + title_extra)
    y += title_extra
    for instance in ordered:
        key = view_identity(assembly.assembly_id, "board", instance)
        title = _label(instance)
        subtitle = _label(stack_labels.get(instance, "Mechanical placement unspecified"))
        header_height = (len(title) + len(subtitle)) * 5 + 12
        prepared: list[tuple[str, str, str, list[str], float]] = []
        row_height = 0.0
        design = assembly.designs[assembly.instances[instance]]
        for mate_id, side, component_id, mate_label in rows[instance]:
            record = design.records.get(component_id)
            reference = str(record.data.get("reference", component_id)) if record else component_id
            lines = _label(f"{reference} | {mate_label}")
            height = float(max(10, len(lines) * 5 + 5))
            prepared.append((mate_id, side, component_id, lines, float(height)))
            row_height += height
        bounds = ViewBounds(x=12, y=y, width=120, height=header_height + row_height + 7)
        group = ET.SubElement(drawing, "g", {"data-item-id": key})
        ET.SubElement(
            group,
            "rect",
            {
                "x": "12",
                "y": str(y),
                "width": "120",
                "height": str(bounds.height),
                "fill": "#f3f7fa",
                "stroke": "#36566c",
            },
        )
        text(group, title + subtitle, 16, y + 6)
        items[key] = ViewItem(
            id=key,
            kind="board",
            label=instance,
            bounds=bounds,
            source=ViewSource(instance=instance),
            target_pages=targets.get(instance, []),
        )
        if instance in stacked:
            declared_stack = next(stack for stack in stacks or [] if instance in stack.instances)
            items[key].stack_name = declared_stack.name
            items[key].stack_position = declared_stack.instances.index(instance) + 1
            items[key].evidence = [
                stack.evidence.model_copy(deep=True)
                for stack in stacks or []
                if instance in stack.instances
            ]
        row_y = y + header_height
        for mate_id, side, component_id, lines, height in prepared:
            cid = view_identity(
                assembly.assembly_id, "connector", instance, component_id, mate_id, side
            )
            status: Literal["resolved", "unresolved", "unmated"] = (
                "unmated"
                if not mate_id
                else "resolved"
                if inspector._mate_rows[mate_id]["status"] == "resolved"
                else "unresolved"
            )
            connector = ViewItem(
                id=cid,
                kind="connector",
                label=" ".join(lines),
                source=ViewSource(instance=instance, record_id=component_id),
                bounds=ViewBounds(x=14, y=row_y - 4, width=118, height=height),
                status=status,
                mate_id=mate_id or None,
            )
            items[cid] = connector
            cg = ET.SubElement(group, "g", {"data-item-id": cid})
            text(cg, lines, 16, row_y)
            ET.SubElement(
                cg,
                "rect",
                {
                    "x": "130",
                    "y": str(row_y - 2),
                    "width": "2",
                    "height": "2",
                    "fill": "#ac2932" if status != "resolved" else "#36566c",
                },
            )
            if mate_id:
                anchors[mate_id, side] = (132, row_y - 1)
                connector_items[mate_id, side] = connector
            row_y += height
        y += bounds.height + 15
    for lane, (mate_id, mate) in enumerate(sorted(assembly.mates.items())):
        key = view_identity(assembly.assembly_id, "mate", mate_id)
        resolved = inspector._mate_rows[mate_id]["status"] == "resolved"
        definition = assembly.mate_definitions.get(mate.definition)
        inferred = mate.evidence.derivation == "inferred" or (
            definition is not None and definition.evidence.derivation == "inferred"
        )
        contacts: list[ViewContact] = []
        for pair_value in inspector._derived_pairs[mate_id]:
            pair = _object(pair_value)
            left, right = _object(pair["left"]), _object(pair["right"])
            contacts.append(
                ViewContact(
                    left_pin=ViewSource(
                        instance=mate.left.instance, record_id=_text(left["pin_id"])
                    ),
                    right_pin=ViewSource(
                        instance=mate.right.instance, record_id=_text(right["pin_id"])
                    ),
                    left_number=_text(left["number"]),
                    right_number=_text(right["number"]),
                    left_net=_text(left["net_id"]),
                    left_net_name=net_name(mate.left.instance, _text(left["net_id"])),
                    right_net_name=net_name(mate.right.instance, _text(right["net_id"])),
                    right_net=_text(right["net_id"]),
                    system_net=inspector._system_ids[(mate.left.instance, _text(left["net_id"]))],
                )
            )
        route: list[tuple[float, float]] = []
        if (mate_id, "left") in anchors and (mate_id, "right") in anchors:
            start, end = anchors[mate_id, "left"], anchors[mate_id, "right"]
            lane_x = 144.0 + lane * 8
            route = [start, (lane_x, start[1]), (lane_x, end[1]), end]
        item = ViewItem(
            id=key,
            kind="bundle",
            label=mate_id,
            mate_id=mate_id,
            status="resolved" if resolved else "unresolved",
            contacts=contacts,
            declared_pairs=definition.pin_pairs if definition else [],
            route=route,
            evidence=[mate.evidence] + ([definition.evidence] if definition else []),
            evidence_derivation="inferred" if inferred else "specified",
        )
        items[key] = item
        for side in ("left", "right"):
            if (mate_id, side) in connector_items:
                connector_items[mate_id, side].contacts = [
                    contact.model_copy(deep=True) for contact in contacts
                ]
        if route:
            group = ET.SubElement(drawing, "g", {"data-item-id": key})
            path = "M " + " L ".join(f"{x},{py}" for x, py in route)
            ET.SubElement(
                group,
                "path",
                {"d": path, "fill": "none", "stroke": "#ffffff", "stroke-width": "1.6"},
            )
            attributes = {
                "d": path,
                "fill": "none",
                "stroke": "#36566c" if resolved else "#ac2932",
                "stroke-width": "0.6",
            }
            if not resolved or inferred:
                attributes["stroke-dasharray"] = "2 1"
            ET.SubElement(group, "path", attributes)
            ET.SubElement(
                group, "title"
            ).text = f"{mate_id}: {item.status}, {len(contacts)} contact pairs"
    bounds = ViewBounds(x=0, y=0, width=160 + len(assembly.mates) * 8, height=max(40, y))
    drawing.set("viewBox", f"0 0 {bounds.width} {bounds.height}")
    drawing.set("width", f"{bounds.width}mm")
    drawing.set("height", f"{bounds.height}mm")
    pages[root_id] = SchematicPage(
        id=root_id,
        title=assembly.name,
        kind="assembly_root",
        bounds=bounds,
        svg=ET.tostring(drawing, encoding="unicode"),
        items=items,
        capabilities=[
            "svg",
            "item_selection",
            "mate_contacts",
            "system_net_selection",
            "hierarchy_navigation",
        ],
        notes=notes,
    )
    return SchematicView(
        document_id=assembly.assembly_id,
        source_revision=inspector._revision,
        roots=[root_id],
        pages=pages,
        nets={key: net for view in supplied.values() for key, net in view.nets.items()},
        buses={key: bus for view in supplied.values() for key, bus in view.buses.items()},
    )
