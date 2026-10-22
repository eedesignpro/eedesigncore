"""Retain source membership through the existing electrical calculation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from eedesigncore.connectivity.build import build_subgraphs
from eedesigncore.connectivity.connection import MemberConn
from eedesigncore.connectivity.geometry import collect
from eedesigncore.connectivity.pipeline import run
from eedesigncore.connectivity.subgraph import ItemKind, Subgraph
from eedesigncore.inspection.portable import _hash
from eedesigncore.render.schematic_metadata import DrawingMetadata
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.models import (
    SchematicBus,
    SchematicIssue,
    SchematicNet,
    SchematicPage,
    ViewItem,
    ViewSource,
    view_identity,
)


@dataclass
class ElectricalPreparation:
    document_id: str
    instance: str
    aliases: dict[str, list[str]] = field(default_factory=dict)
    groups: list[Subgraph] = field(default_factory=list)
    prefixes: dict[tuple[str, ...], str] = field(default_factory=dict)

    def add_sheet(
        self,
        sheet: Schematic,
        page: SchematicPage,
        metadata: DrawingMetadata,
        path: str,
        prefix: str,
        children: dict[str, str],
        excluded_from_board: bool = False,
    ) -> None:
        identities: dict[tuple[ItemKind, int], str] = {}
        geo = collect(sheet, path, children, excluded_from_board)
        kinds = {
            "label": ItemKind.LABEL,
            "global_label": ItemKind.GLOBAL_LABEL,
            "hierarchical_label": ItemKind.HIER_LABEL,
            "netclass_flag": ItemKind.DIRECTIVE_LABEL,
        }

        def add(
            key: str,
            kind: Literal[
                "wire", "bus", "pin", "label", "junction", "sheet_pin", "bus_entry", "no_connect"
            ],
            uuid: str | None,
            fallback: str,
            label: str,
            position: tuple[float, float],
            engine_kind: ItemKind,
            engine_index: int,
            parent: str | None = None,
        ) -> ViewItem:
            identity = view_identity(
                self.document_id,
                self.instance,
                path,
                kind,
                parent or "",
                ((uuid or "") + ":" + fallback) if kind == "pin" else uuid or fallback,
            )
            if identity in page.items and kind == "pin":
                metadata.ids[key] = identity
                identities[engine_kind, engine_index] = identity
                return page.items[identity]
            if identity in page.items:
                raise ValueError(
                    "Duplicate electrical source identity within one sheet occurrence."
                )
            item = ViewItem(
                id=identity,
                kind=kind,
                label=label,
                position=position,
                source=ViewSource(instance=self.instance, sheet_path=path, source_uuid=uuid),
                parent_item=parent,
            )
            page.items[identity] = item
            metadata.ids[key] = identity
            identities[engine_kind, engine_index] = identity
            if uuid is None and kind != "pin":
                page.coverage.issues.append(
                    SchematicIssue(
                        code="missing_source_uuid",
                        scope="identity",
                        status="uncertain",
                        message="Content-derived identity changes when this item changes.",
                        item_ids=[identity],
                        sheet_path=path,
                    )
                )
            return item

        counts: dict[str, int] = defaultdict(int)
        for index, line in enumerate(sheet.lines):
            kind = line.line_type.value
            if kind not in {"wire", "bus"}:
                continue
            add(
                f"line:{index}",
                "wire" if kind == "wire" else "bus",
                line.uuid,
                _hash(line.model_dump(mode="json")),
                "",
                line.start,
                ItemKind.WIRE if kind == "wire" else ItemKind.BUS,
                counts[kind],
            )
            counts[kind] += 1
        for index, junction in enumerate(sheet.junctions):
            add(
                f"junction:{index}",
                "junction",
                junction.uuid,
                _hash(junction.model_dump(mode="json")),
                "",
                junction.position,
                ItemKind.JUNCTION,
                index,
            )
        for index, mark in enumerate(sheet.no_connects):
            add(
                f"no_connect:{index}",
                "no_connect",
                mark.uuid,
                _hash(mark.model_dump(mode="json")),
                "",
                mark.position,
                ItemKind.NO_CONNECT,
                index,
            )
        for index, entry in enumerate(sheet.bus_entries):
            add(
                f"bus_entry:{index}",
                "bus_entry",
                entry.uuid,
                _hash(entry.model_dump(mode="json")),
                "",
                entry.position,
                ItemKind.BUS_WIRE_ENTRY,
                index,
            )
        for index, label in enumerate(sheet.labels):
            directive = label.kind.value == "netclass_flag"
            counter = "directive" if directive else "label"
            add(
                f"label:{index}",
                "label",
                label.uuid,
                _hash(label.model_dump(mode="json")),
                label.content,
                label.position,
                kinds[label.kind.value],
                counts[counter],
            )
            counts[counter] += 1
        pin_index = 0
        for index, child in enumerate(sheet.sheets):
            for local_index, pin in enumerate(child.pins):
                add(
                    f"sheet_pin:{index}:{local_index}",
                    "sheet_pin",
                    pin.uuid,
                    _hash(pin.model_dump(mode="json")),
                    pin.name,
                    pin.position,
                    ItemKind.SHEET_PIN,
                    pin_index,
                    metadata.ids.get(f"sheet:{index}"),
                )
                pin_index += 1
        for index, pin_ref in enumerate(geo.pins):
            placed = sheet.symbols[pin_ref.symbol_index]
            saved = next(
                (value for value in placed.pins if value.number == pin_ref.pin_number), None
            )
            item = add(
                f"pin:{pin_ref.symbol_index}:{pin_ref.pin_index}",
                "pin",
                saved.uuid if saved else None,
                pin_ref.source_pin_key,
                pin_ref.pin_name,
                (pin_ref.point[0] / 1e6, pin_ref.point[1] / 1e6),
                ItemKind.PIN,
                index,
                metadata.ids[f"symbol:{pin_ref.symbol_index}"],
            )
            item.reference, item.pin_number, item.pin_name = (
                pin_ref.reference,
                pin_ref.pin_number,
                pin_ref.pin_name,
            )
            item.electrical_type, item.unit = pin_ref.electrical_type, placed.unit
            item.netlist_member = not pin_ref.from_power_symbol and not pin_ref.excluded_from_board
        if any(not pin.is_annotated for pin in geo.pins):
            return
        names = tuple(path.strip("/").split("/"))
        self.prefixes[names] = prefix
        self.groups.extend(
            build_subgraphs(
                geo,
                names,
                resolve_alias=self.aliases.get,
                source_items=identities,
                retain_undriven=True,
            )
        )

    def finish(
        self, pages: dict[str, SchematicPage]
    ) -> tuple[dict[str, SchematicNet], dict[str, SchematicBus]]:
        _, report = run(self.groups, sheet_prefixes=self.prefixes, resolve_alias=self.aliases.get)
        items = {key: item for page in pages.values() for key, item in page.items.items()}
        named: dict[str, set[str]] = defaultdict(set)
        buses_by_name: dict[str, list[Subgraph]] = defaultdict(list)
        for group in report.resolved_subgraphs:
            if group.is_bus:
                buses_by_name[group.full_name or group.name].append(group)
            else:
                named[group.full_name or group.name].update(group.source_items)
        nets: dict[str, SchematicNet] = {}
        names: dict[str, str] = {}
        for name, members in sorted(named.items()):
            if not members:
                continue
            identity = view_identity(self.document_id, self.instance, "net", *sorted(members))
            names[name] = identity
            nets[identity] = SchematicNet(id=identity, name=name or None, item_ids=sorted(members))
        unnamed_buses: list[Subgraph] = []
        for group in self.groups:
            if group.driver is None and not group.absorbed and group.source_items:
                if any(items[key].kind == "bus" for key in group.source_items):
                    unnamed_buses.append(group)
                    continue
                identity = view_identity(
                    self.document_id, self.instance, "net", *sorted(group.source_items)
                )
                nets[identity] = SchematicNet(id=identity, item_ids=sorted(group.source_items))
        for net in nets.values():
            for key in net.item_ids:
                items[key].net_ids.append(net.id)
        buses: dict[str, SchematicBus] = {}

        def leaves(members: list[MemberConn]) -> set[str]:
            result: set[str] = set()
            for member in members:
                if member.is_bus:
                    result.update(leaves(member.members))
                else:
                    result.add(member.qualified_name)
            return result

        for name, groups in sorted(buses_by_name.items()):
            bus_items = sorted({key for group in groups for key in group.source_items})
            if not bus_items:
                continue
            wanted = {key for group in groups for key in leaves(group.conn_members)}
            identity = view_identity(self.document_id, self.instance, "bus", *bus_items)
            bus = SchematicBus(
                id=identity,
                name=name or None,
                item_ids=bus_items,
                net_ids=sorted({names[key] for key in wanted if key in names}),
                unresolved_members=sorted(wanted - names.keys()),
            )
            buses[identity] = bus
            for key in bus_items:
                items[key].bus_ids.append(identity)
            if bus.unresolved_members:
                for page in pages.values():
                    if set(bus_items) & page.items.keys():
                        page.coverage.electrical = "partial"
                        page.coverage.issues.append(
                            SchematicIssue(
                                code="bus_member_without_source_net",
                                scope="electrical",
                                status="uncertain",
                                message="Some calculated bus members have no resolved source net.",
                                item_ids=bus_items,
                            )
                        )
        for group in unnamed_buses:
            bus_items = sorted(group.source_items)
            identity = view_identity(self.document_id, self.instance, "bus", *bus_items)
            buses[identity] = SchematicBus(id=identity, item_ids=bus_items)
            for key in bus_items:
                items[key].bus_ids.append(identity)
            for page in pages.values():
                if set(bus_items) & page.items.keys():
                    page.coverage.electrical = "partial"
                    page.coverage.issues.append(
                        SchematicIssue(
                            code="unnamed_bus",
                            scope="electrical",
                            status="uncertain",
                            message="This bus has no resolved member definition.",
                            item_ids=bus_items,
                        )
                    )
        for page in pages.values():
            bus_pins = [
                item.id for item in page.items.values() if item.kind == "pin" and item.bus_ids
            ]
            if bus_pins:
                page.coverage.electrical = "partial"
                page.coverage.issues.append(
                    SchematicIssue(
                        code="pin_connected_to_bus",
                        scope="electrical",
                        status="unsupported",
                        message=(
                            "Pins on a bus retain bus membership. "
                            "No scalar electrical net is inferred."
                        ),
                        item_ids=bus_pins,
                    )
                )
        return nets, buses
