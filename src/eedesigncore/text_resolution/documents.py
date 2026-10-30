"""Owner-aware shown text on isolated display copies of loaded KiCad documents."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from pydantic import BaseModel

from eedesigncore.common.models import Property
from eedesigncore.common.page import TitleBlock
from eedesigncore.common.pcb_family.board_items import Barcode, DimensionFormat, Table
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode
from eedesigncore.sch.models import Label, PlacedSymbol, Schematic, SchTable, Sheet, SheetPin
from eedesigncore.text_resolution.engine import Resolver, restore_literals
from eedesigncore.text_resolution.models import ResolvedText, TextResolutionContext

Document = TypeVar("Document", Board, Footprint, Schematic, Symbol)
Owner = PlacedSymbol | Symbol | Footprint | Sheet | Label
_SIM_FIELDS = ("SIM.DEVICE", "SIM.TYPE", "SIM.PINS", "SIM.PARAMS", "SIM.LIBRARY", "SIM.NAME")


@dataclass(frozen=True)
class ResolvedDocumentText(Generic[Document]):
    """Display copy and records keyed by source item paths. Do not save the copy.

    The input remains the editable source. The display copy replaces shown text
    and can contain per-placement embedded definitions. Records retain raw text.
    """

    document: Document
    texts: Mapping[str, ResolvedText]

    @property
    def complete(self) -> bool:
        return all(value.complete for value in self.texts.values())


def context_for_sheet(
    hierarchy: SheetHierarchy, instance_path: str, *, context: TextResolutionContext | None = None
) -> TextResolutionContext:
    """Select one saved sheet occurrence without loading more files."""
    ancestors: list[SheetNode] = []
    visited: set[int] = set()

    def find(node: SheetNode, path: list[SheetNode]) -> None:
        if id(node) in visited or len(path) > 64 or len(visited) >= 4096:
            raise ValueError("The sheet hierarchy has a cycle or exceeds the resolution limit.")
        visited.add(id(node))
        if node.kiid_path.rstrip("/") == instance_path.rstrip("/"):
            if ancestors:
                raise ValueError("The selected sheet occurrence has duplicate paths.")
            ancestors.extend([*path, node])
        for child in node.children:
            find(child, [*path, node])

    for root in hierarchy.roots:
        find(root, [])
    if not ancestors:
        raise ValueError("The selected sheet occurrence is absent from the hierarchy.")
    node = ancestors[-1]
    base = context or TextResolutionContext()
    scopes = tuple(
        scope
        for item in reversed(ancestors)
        for scope in ([_fields(item.sheet)] if item.sheet is not None else [])
        + [_title(item.schematic.title_block)]
    )
    flags = {
        "EXCLUDE_FROM_BOM": any(
            item.sheet is not None and not item.sheet.in_bom for item in ancestors
        ),
        "EXCLUDE_FROM_BOARD": any(
            item.sheet is not None and not item.sheet.on_board for item in ancestors
        ),
        "EXCLUDE_FROM_SIM": any(
            item.sheet is not None and item.sheet.exclude_from_sim for item in ancestors
        ),
        "DNP": any(item.sheet is not None and item.sheet.dnp for item in ancestors),
    }
    variables = dict(hierarchy.project.text_variables) if hierarchy.project else {}
    variables.update(base.variables)
    return replace(
        base,
        variables=variables,
        sheet_scopes=scopes,
        sheet_flags=flags,
        hierarchy=hierarchy,
        project=hierarchy.project,
        instance_path=node.kiid_path,
        filename=str(ancestors[0].path),
        project_name=hierarchy.project_path.stem if hierarchy.project_path else base.project_name,
        sheet_name=node.name,
        sheet_path="/"
        + "/".join(item.name for item in ancestors[1:])
        + ("/" if len(ancestors) > 1 else ""),
        page=node.page,
        page_count=len(visited),
    )


def _fields(owner: Owner) -> dict[str, str]:
    values = owner.properties if isinstance(owner, Footprint) else owner.fields
    result = {prop.name: prop.value for prop in values}
    if isinstance(owner, PlacedSymbol | Symbol):
        result = {name.upper(): value for name, value in result.items()}
    elif isinstance(owner, Sheet):
        for name in ("Sheetname", "Sheetfile"):
            if name in result:
                result[name.upper()] = result.pop(name)
    elif isinstance(owner, Footprint):
        for name in ("Reference", "Value"):
            if name in result:
                result[name.upper()] = result[name]
    return result


def _title(block: TitleBlock | None) -> dict[str, str]:
    value = block or TitleBlock()
    return {
        "TITLE": value.title,
        "ISSUE_DATE": value.date,
        "REVISION": value.rev,
        "COMPANY": value.company,
        **{f"COMMENT{i}": value.comments.get(i, "") for i in range(1, 10)},
    }


def _letter(unit: int) -> str:
    value = ""
    while unit:
        unit, digit = divmod(unit - 1, 26)
        value = chr(65 + digit) + value
    return value


class DocumentResolver:
    def __init__(
        self, source: Board | Footprint | Schematic | Symbol, context: TextResolutionContext
    ) -> None:
        self.source, self.context = source, context
        self.records: dict[str, ResolvedText] = {}
        self.definitions = (
            {item.name: item for item in source.lib_symbols}
            if isinstance(source, Schematic)
            else {}
        )
        self.references: dict[str, tuple[Owner, TextResolutionContext, dict[str, Symbol]]] = {}
        # Global label name -> (sheet path, page) for every global label in the
        # hierarchy, built once per resolver and context (see ``intersheet``).
        self._intersheet: dict[int, dict[str, list[tuple[str, str]]]] = {}
        if isinstance(source, Schematic):
            self.index(source, context)
        if context.hierarchy:
            for node in context.hierarchy.walk():
                self.index(
                    node.schematic,
                    context_for_sheet(context.hierarchy, node.kiid_path, context=context),
                )
        if isinstance(source, Board):
            for fp in source.footprints:
                entry: tuple[Owner, TextResolutionContext, dict[str, Symbol]] = fp, context, {}
                if fp.uuid:
                    self.references[fp.uuid] = entry
                ref = _fields(fp).get("REFERENCE")
                if ref:
                    self.references[ref.casefold()] = entry

    def index(self, schematic: Schematic, context: TextResolutionContext) -> None:
        definitions = {item.name: item for item in schematic.lib_symbols}
        owners: list[PlacedSymbol | Sheet] = [*schematic.symbols, *schematic.sheets]
        for owner in owners:
            entry = owner, context, definitions
            if owner.uuid:
                self.references.setdefault(owner.uuid, entry)
                if context.instance_path:
                    self.references[context.instance_path.rstrip("/") + "/" + owner.uuid] = entry
            if isinstance(owner, PlacedSymbol):
                fields = self.owner_fields(owner, context)
                if ref := fields.get("REFERENCE"):
                    self.references.setdefault(ref, entry)

    def owner_fields(self, owner: Owner, context: TextResolutionContext) -> dict[str, str]:
        fields = _fields(owner)
        if isinstance(owner, Footprint):
            for footprint_variant in owner.variants:
                if (
                    footprint_variant.name == context.variant
                    and context.variant.lower() != "default"
                ):
                    for field in footprint_variant.fields:
                        fields[field.name] = field.value
                        if field.name in ("Reference", "Value"):
                            fields[field.name.upper()] = field.value
        if isinstance(owner, PlacedSymbol):
            candidates = [
                path
                for project in owner.instances
                for path in project.paths
                if context.instance_path
                and path.path.rstrip("/") == context.instance_path.rstrip("/")
                and (context.project_name is None or project.name == context.project_name)
            ]
            if len({(path.reference, path.unit) for path in candidates}) > 1:
                raise ValueError(
                    "The selected symbol occurrence has conflicting saved assignments."
                )
            if candidates:
                fields["REFERENCE"] = candidates[0].reference
                for variant in candidates[0].variants:
                    if variant.name == context.variant:
                        fields.update({item.name.upper(): item.value for item in variant.fields})
        return fields

    def lookup(
        self,
        token: str,
        owner: Owner | None,
        context: TextResolutionContext,
        definitions: dict[str, Symbol],
        path: str,
        stack: tuple[str, ...] = (),
    ) -> str | None:
        if len(stack) > context.max_depth:
            return None
        if ":" in token:
            reference, remainder = token.split(":", 1)
            entry = self.references.get(
                reference.casefold() if isinstance(self.source, Board) else reference
            )
            if entry:
                target, target_context, target_definitions = entry
                field, _, variant = remainder.partition(":")
                if variant:
                    target_context = replace(target_context, variant=variant)
                key = reference + ":" + remainder
                if key in stack:
                    return None
                target_resolver = Resolver(
                    target_context,
                    lambda name: self.lookup(
                        name, target, target_context, target_definitions, reference, (*stack, key)
                    ),
                    reference,
                )
                value = target_resolver.variable(field)
                if value is not None:
                    nested = Resolver(
                        target_context,
                        lambda name: self.lookup(
                            name,
                            target,
                            target_context,
                            target_definitions,
                            reference,
                            (*stack, key),
                        ),
                        reference,
                    )
                    result = nested.resolve(value, restore_escapes=False)
                    return result.resolved if result.complete else None
                return f"<Unresolved: {reference}:{field}>"
            return f"<Unknown reference: {reference}>"
        if isinstance(owner, PlacedSymbol):
            selected_occurrences = [
                entry
                for project in owner.instances
                for entry in project.paths
                if context.instance_path
                and entry.path.rstrip("/") == context.instance_path.rstrip("/")
                and (context.project_name is None or project.name == context.project_name)
            ]
            if selected_occurrences:
                owner = owner.model_copy(update={"unit": selected_occurrences[0].unit})
                for selected_variant in selected_occurrences[0].variants:
                    if selected_variant.name == context.variant:
                        for attribute in (
                            "dnp",
                            "in_bom",
                            "on_board",
                            "exclude_from_sim",
                            "in_pos_files",
                        ):
                            setting = getattr(selected_variant, attribute)
                            if setting is not None:
                                setattr(owner, attribute, setting)
        if owner is not None:
            fields = self.owner_fields(owner, context)
            key = token.upper() if isinstance(owner, PlacedSymbol | Symbol) else token
            if key in fields:
                if key == "REFERENCE" and isinstance(owner, PlacedSymbol):
                    definition = definitions.get(owner.lib_name or "") or definitions.get(
                        owner.lib_id
                    )
                    if (
                        definition
                        and len({u.unit_index for u in definition.units if u.unit_index}) > 1
                    ):
                        return fields[key] + _letter(owner.unit)
                return fields[key]
            if isinstance(owner, PlacedSymbol | Symbol):
                if token in _SIM_FIELDS:
                    return ""
                templates = (
                    context.project.value_at("schematic.drawing.field_names")
                    if context.project
                    else None
                )
                if isinstance(templates, list) and any(
                    isinstance(row, dict)
                    and isinstance(row.get("name"), str)
                    and token in (row["name"], str(row["name"]).upper())
                    for row in templates
                ):
                    return ""
                definition = (
                    definitions.get(owner.lib_name or "") or definitions.get(owner.lib_id)
                    if isinstance(owner, PlacedSymbol)
                    else owner
                )
                identity = owner.lib_id if isinstance(owner, PlacedSymbol) else owner.name
                library, separator, name = identity.partition(":")
                footprint = fields.get("FOOTPRINT", "").split(":")
                derived = {
                    "SYMBOL_LIBRARY": library if separator else "",
                    "SYMBOL_NAME": name if separator else library,
                    "FOOTPRINT_LIBRARY": footprint[0],
                    "FOOTPRINT_NAME": footprint[1] if len(footprint) > 1 else "",
                    "SYMBOL_DESCRIPTION": fields.get("DESCRIPTION", ""),
                    "SYMBOL_KEYWORDS": definition.keywords or "" if definition else "",
                    "SHORT_REFERENCE": fields.get("REFERENCE", ""),
                }
                if isinstance(owner, PlacedSymbol):
                    derived["UNIT"] = _letter(owner.unit)
                if token in derived:
                    return derived[token]
                if match := re.fullmatch(
                    r"(PIN_NAME|PIN_BASE_NAME|PIN_ALT_LIST|UNIT|REFERENCE|SHORT_REFERENCE|NET_NAME|SHORT_NET_NAME|NET_CLASS)\((.*)\)",
                    token,
                ):
                    operation, pin_number = match.groups()
                    pins = (
                        [
                            (unit.unit_index, pin)
                            for unit in definition.units
                            for pin in unit.items
                            if isinstance(pin, Pin)
                        ]
                        if definition
                        else []
                    )
                    if isinstance(owner, PlacedSymbol) and operation not in (
                        "UNIT",
                        "REFERENCE",
                        "SHORT_REFERENCE",
                    ):
                        pins.sort(key=lambda entry: entry[0] not in (0, owner.unit))
                    selected = next(
                        ((unit, pin) for unit, pin in pins if pin.number == pin_number), None
                    )
                    if selected:
                        unit, pin = selected
                        alternate = (
                            next(
                                (
                                    item.alternate
                                    for item in owner.pins
                                    if item.number == pin_number
                                ),
                                None,
                            )
                            if isinstance(owner, PlacedSymbol)
                            else None
                        )
                        known = {
                            "PIN_NAME": alternate or pin.name,
                            "PIN_BASE_NAME": pin.name,
                            "PIN_ALT_LIST": ", ".join(
                                sorted(item.name for item in pin.alternates)
                            ),
                            "UNIT": _letter(unit),
                            "REFERENCE": fields.get("REFERENCE", "") + _letter(unit),
                            "SHORT_REFERENCE": fields.get("REFERENCE", ""),
                        }
                        if operation in known:
                            return known[operation]
                        instance = context.instance_path or "/" + (
                            getattr(self.source, "uuid", None) or "root"
                        )
                        symbol_index = re.search(r"symbols\[(\d+)\]", path)
                        identity = getattr(owner, "uuid", None) or (
                            f"symbol:{symbol_index[1]}" if symbol_index else ""
                        )
                        net = context.connections.get((instance, identity, pin_number))
                        if net is not None:
                            if operation == "NET_NAME":
                                return net
                            if operation == "SHORT_NET_NAME":
                                return (
                                    "NC"
                                    if net.startswith("unconnected-")
                                    else net.rsplit("/", 1)[-1]
                                )
                            if operation == "NET_CLASS":
                                return context.net_classes.get(net)
                    else:
                        return f"<Unresolved: pin {pin_number}>"
            if isinstance(owner, Footprint):
                library, separator, name = owner.fp_id.partition(":")
                derived = {
                    "FOOTPRINT_LIBRARY": library if separator else "",
                    "FOOTPRINT_NAME": name if separator else library,
                    "LAYER": owner.layer,
                }
                if token in derived:
                    return derived[token]
                if match := re.fullmatch(
                    r"(NET_NAME|SHORT_NET_NAME|NET_CLASS|PIN_NAME)\((.*)\)", token
                ):
                    operation, pad_number = match.groups()
                    pad = next((item for item in owner.pads if item.number == pad_number), None)
                    if pad:
                        net = pad.net_name or ""
                        if operation == "PIN_NAME":
                            return pad.pinfunction or ""
                        if operation == "NET_NAME":
                            return net
                        if operation == "SHORT_NET_NAME":
                            return net.rsplit("/", 1)[-1]
                        if net in context.net_classes:
                            return context.net_classes[net]
            if isinstance(owner, PlacedSymbol | Symbol | Sheet):
                flags = {
                    "EXCLUDE_FROM_BOM": (not owner.in_bom, "Excluded from BOM"),
                    "EXCLUDE_FROM_BOARD": (not owner.on_board, "Excluded from board"),
                    "EXCLUDE_FROM_SIM": (owner.exclude_from_sim, "Excluded from simulation"),
                    "DNP": (getattr(owner, "dnp", False), "DNP"),
                }
                if token in flags:
                    active, value = flags[token]
                    if (
                        isinstance(self.source, Schematic)
                        and self.source.rule_areas
                        and not active
                        and not context.sheet_flags.get(token, False)
                    ):
                        return (
                            context.variable_provider(path, token)
                            if context.variable_provider
                            else None
                        )
                    return value if active or context.sheet_flags.get(token, False) else ""
            if (
                isinstance(owner, Label)
                and owner.kind.value == "netclass_flag"
                and isinstance(self.source, Schematic)
            ):
                directive_flags = {
                    "EXCLUDE_FROM_BOM": ("in_bom", False, "Excluded from BOM"),
                    "EXCLUDE_FROM_BOARD": ("on_board", False, "Excluded from board"),
                    "EXCLUDE_FROM_SIM": ("exclude_from_sim", True, "Excluded from simulation"),
                    "DNP": ("dnp", True, "DNP"),
                }
                if token in directive_flags:
                    attribute, expected, label = directive_flags[token]
                    areas = [
                        area
                        for area in self.source.rule_areas
                        if _on_border(owner.position, area.shape.pts)
                    ]
                    return (
                        label
                        if any(getattr(area, attribute) == expected for area in areas)
                        else ""
                    )
            if isinstance(owner, Label) and token in ("NET_NAME", "SHORT_NET_NAME", "NET_CLASS"):
                instance = context.instance_path or "/" + (
                    getattr(self.source, "uuid", None) or "root"
                )
                label_index = re.search(r"labels\[(\d+)\]", path)
                identity = owner.uuid or (f"label:{label_index[1]}" if label_index else "")
                net = context.connections.get((instance, identity, ""))
                if net is not None:
                    if token == "NET_NAME":
                        return net
                    if token == "SHORT_NET_NAME":
                        return net.rsplit("/", 1)[-1]
                    return context.net_classes.get(net)
            if (
                isinstance(owner, Label)
                and token == "INTERSHEET_REFS"
                and owner.kind.value == "global_label"
            ):
                return self.intersheet(owner, context)
            if (
                isinstance(owner, Label)
                and token == "CONNECTION_TYPE"
                and owner.kind.value != "label"
            ):
                return (owner.shape or "").capitalize().replace("Tri_state", "Tri-state")
        if context.variable_provider:
            value = context.variable_provider(path, token)
            if value is not None:
                return value
        return None

    def intersheet(self, label: Label, context: TextResolutionContext) -> str | None:
        if context.hierarchy is None:
            return None
        project = context.project or context.hierarchy.project
        settings = project.value_at("schematic.drawing") if project else None
        settings = settings if isinstance(settings, dict) else {}
        own = settings.get("intersheets_ref_own_page", True)
        short = settings.get("intersheets_ref_short", False)
        prefix = settings.get("intersheets_ref_prefix", "[")
        suffix = settings.get("intersheets_ref_suffix", "]")
        name = Resolver(context).resolve(label.content).resolved
        # One pass over the hierarchy per context, not one per global label: a
        # 46-sheet design has hundreds of global labels, and each pass resolves
        # every global label of every sheet.
        table = self._intersheet.get(id(context))
        if table is None:
            table = {}
            for node in context.hierarchy.walk():
                scope = context_for_sheet(context.hierarchy, node.kiid_path, context=context)
                for item in node.schematic.labels:
                    if item.kind.value == "global_label":
                        resolved = Resolver(scope).resolve(item.content).resolved
                        table.setdefault(resolved, []).append((node.kiid_path, node.page or "?"))
            self._intersheet[id(context)] = table
        pages = [
            page for path, page in table.get(name, []) if own or path != context.instance_path
        ]
        pages = sorted(
            set(pages), key=lambda page: (int(page) if page.isdigit() else 2147483647, page)
        )
        body = pages[0] + ".." + pages[-1] if short and len(pages) > 2 else ",".join(pages)
        return str(prefix) + body + str(suffix)

    def resolve(
        self,
        raw: str,
        owner: Owner | None,
        context: TextResolutionContext,
        path: str,
        local: Mapping[str, str] | None = None,
    ) -> str:
        def lookup(token: str) -> str | None:
            if local is not None and token in local:
                return local[token]
            return self.lookup(token, owner, context, self.definitions, path)

        result = Resolver(context, lookup, path).resolve(raw)
        self.records[path] = result
        return result.resolved

    def table(
        self,
        table: SchTable | Table,
        owner: Owner | None,
        context: TextResolutionContext,
        path: str,
    ) -> None:
        originals = [cell.content for cell in table.cells]
        active: set[int] = set()
        completed: dict[int, str] = {}

        def cell(index: int) -> str:
            if index in active or len(active) > context.max_depth:
                return "<Circular reference>"
            if index in completed:
                return completed[index]
            row, col = divmod(index, max(1, table.column_count))
            offset = 0 if isinstance(table, SchTable) else 1
            local = {
                "ROW": str(row + offset),
                "COL": str(col + offset),
                "ADDR": chr(65 + col % 26) + str(row + offset),
            }
            if isinstance(table, Table):
                local["LAYER"] = table.cells[index].layer or table.layer
            active.add(index)

            def lookup(token: str) -> str | None:
                if token in local:
                    return local[token]
                if (
                    isinstance(table, SchTable)
                    and token.startswith("CELL(")
                    and token.endswith(")")
                ):
                    args = token[5:-1].strip()
                    if match := re.fullmatch(r'"([A-Za-z]+)(\d+)"', args):
                        letters, digits = match.groups()
                        r, c = int(digits), 0
                        for letter in letters.upper():
                            c = c * 26 + ord(letter) - 64
                        c -= 1
                    elif match := re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", args):
                        r, c = map(int, match.groups())
                    else:
                        return f"<Unresolved: Invalid CELL() syntax: {args}>"
                    target = r * table.column_count + c
                    if (
                        r < 0
                        or not 0 <= c < table.column_count
                        or not 0 <= target < len(originals)
                    ):
                        return f"<Unresolved: Cell {args} not found>"
                    return cell(target)
                return self.lookup(
                    token, owner, context, self.definitions, path + f".cells[{index}].content"
                )

            result = Resolver(context, lookup, path).resolve(
                originals[index], restore_escapes=False
            )
            self.records[path + f".cells[{index}].content"] = result
            active.remove(index)
            completed[index] = result.resolved
            return result.resolved

        for index, item in enumerate(table.cells):
            _set_text(item, "content", restore_literals(cell(index)))
        for index in range(len(table.cells)):
            record = self.records[path + f".cells[{index}].content"]
            record.resolved = restore_literals(record.resolved)

    def walk(
        self, value: BaseModel, owner: Owner | None, context: TextResolutionContext, path: str
    ) -> None:
        if isinstance(value, TitleBlock):
            for attribute in ("title", "date", "rev", "company"):
                _set_text(
                    value,
                    attribute,
                    self.resolve(
                        _get_text(value, attribute), owner, context, path + "." + attribute
                    ),
                )
            value.comments = {
                index: self.resolve(raw, owner, context, f"{path}.comments[{index}]")
                for index, raw in value.comments.items()
            }
            return
        if isinstance(value, Table | SchTable):
            self.table(value, owner, context, path)
            return
        if isinstance(value, PlacedSymbol):
            candidates = [
                entry
                for project in value.instances
                for entry in project.paths
                if context.instance_path
                and entry.path.rstrip("/") == context.instance_path.rstrip("/")
                and (context.project_name is None or project.name == context.project_name)
            ]
            if candidates:
                value.unit = candidates[0].unit
                for variant in candidates[0].variants:
                    if variant.name == context.variant:
                        for attribute in (
                            "dnp",
                            "in_bom",
                            "on_board",
                            "exclude_from_sim",
                            "in_pos_files",
                        ):
                            setting = getattr(variant, attribute)
                            if setting is not None:
                                setattr(value, attribute, setting)
        if isinstance(value, Sheet) and context.hierarchy and value.uuid:
            child_path = (context.instance_path or "").rstrip("/") + "/" + value.uuid
            if any(node.kiid_path == child_path for node in context.hierarchy.walk()):
                context = context_for_sheet(context.hierarchy, child_path, context=context)
        if isinstance(value, Footprint):
            owner = value.model_copy(deep=True)
        elif isinstance(value, PlacedSymbol | Symbol | Sheet | Label):
            # The owner keeps the raw field text while the walk below rewrites
            # the fields in place. Only the fields change, so only they are
            # copied: a deep copy of every library symbol's graphics made up
            # most of the time of a large schematic.
            owner = value.model_copy(
                update={"fields": [field.model_copy() for field in value.fields]}
            )
        local: dict[str, str] = {}
        layer = getattr(value, "layer", None)
        if isinstance(layer, str):
            local["LAYER"] = (
                next(
                    (
                        entry.user_name or layer
                        for entry in self.source.layers
                        if entry.canonical_name == layer
                    ),
                    layer,
                )
                if isinstance(self.source, Board)
                else layer
            )
        if isinstance(value, Property):
            if (
                isinstance(owner, Label)
                and owner.kind.value == "global_label"
                and value.name in ("Intersheetrefs", "Intersheet References")
            ):
                project = context.project or (
                    context.hierarchy.project if context.hierarchy else None
                )
                show = (
                    project.value_at("schematic.drawing.intersheets_ref_show") if project else None
                )
                if isinstance(show, bool):
                    value.visible = show
            raw = value.value
            if isinstance(owner, PlacedSymbol | Footprint):
                key = value.name.upper() if isinstance(owner, PlacedSymbol) else value.name
                raw = self.owner_fields(owner, context).get(key, raw)
                if key == "REFERENCE" and isinstance(owner, PlacedSymbol):
                    raw = self.lookup("REFERENCE", owner, context, self.definitions, path) or raw
            value.value = self.resolve(raw, owner, context, path + ".value", local)
            self.records[path + ".value"].raw = getattr(self.original_property(path), "value", raw)
            return
        if isinstance(value, Barcode):
            value.text = self.resolve(value.text, owner, context, path + ".text", local)
        content = getattr(value, "content", None)
        if isinstance(content, str):
            _set_text(
                value, "content", self.resolve(content, owner, context, path + ".content", local)
            )
        if isinstance(value, DimensionFormat):
            for attribute in ("prefix", "suffix", "override_value"):
                raw_text = getattr(value, attribute)
                if raw_text is not None:
                    setattr(
                        value,
                        attribute,
                        self.resolve(raw_text, owner, context, path + "." + attribute, local),
                    )
        if isinstance(value, SheetPin):
            local["CONNECTION_TYPE"] = value.shape.capitalize().replace("Tri_state", "Tri-state")
            instance = context.instance_path or "/" + (
                getattr(self.source, "uuid", None) or "root"
            )
            if isinstance(owner, Sheet) and owner.uuid and instance.endswith("/" + owner.uuid):
                instance = instance.rsplit("/", 1)[0]
            indices = re.search(r"sheets\[(\d+)\]\.pins\[(\d+)\]", path)
            identity = getattr(value, "uuid", None) or (
                f"sheet_pin:{indices[1]}:{indices[2]}" if indices else ""
            )
            net = context.connections.get((instance, identity, ""))
            if net is not None:
                local.update(NET_NAME=net, SHORT_NET_NAME=net.rsplit("/", 1)[-1])
                if net in context.net_classes:
                    local["NET_CLASS"] = context.net_classes[net]
            _set_text(
                value,
                "name",
                self.resolve(_get_text(value, "name"), owner, context, path + ".name", local),
            )
        for name in type(value).model_fields:
            if name in {"lib_symbols", "instances", "pads", "pins", "embedded_files"} and not (
                name == "pins" and isinstance(value, Sheet)
            ):
                continue
            child = getattr(value, name)
            if isinstance(child, BaseModel):
                self.walk(child, owner, context, path + "." + name)
            elif isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, BaseModel):
                        self.walk(item, owner, context, f"{path}.{name}[{index}]")

    def original_property(self, path: str) -> object:
        value: object = self.source
        try:
            for name, index in re.findall(r"\.([A-Za-z_]+)(?:\[(\d+)\])?", path):
                value = getattr(value, name)
                if index:
                    value = value[int(index)]  # type: ignore[index]
        except (AttributeError, IndexError, TypeError):
            return None
        return value


def resolve_document_text(
    document: Document, context: TextResolutionContext | None = None
) -> ResolvedDocumentText[Document]:
    """Resolve display text while retaining source paths, raw text, and diagnostics."""
    ctx = context or TextResolutionContext()
    if ctx.project is not None:
        ctx = replace(ctx, variables={**ctx.project.text_variables, **ctx.variables})
    if isinstance(document, Board):
        from eedesigncore.text_resolution.netclasses import names_for_nets

        ctx = replace(
            ctx, net_classes={**names_for_nets(document.net_names, ctx.project), **ctx.net_classes}
        )
    if isinstance(document, Schematic) and ctx.instance_path is None:
        page = (
            next(
                (entry.page for entry in document.sheet_instances.paths if entry.path == "/"), None
            )
            if document.sheet_instances
            else None
        )
        ctx = replace(
            ctx,
            instance_path="/" + (document.uuid or "root"),
            page=ctx.page or page,
            page_count=ctx.page_count
            if ctx.page_count is not None
            else 1
            if not document.sheets
            else None,
        )
    if isinstance(document, Board | Schematic):
        title = _title(document.title_block)
        if isinstance(document, Board):
            title.update({prop.name: prop.value for prop in document.properties})
        ctx = replace(ctx, scopes=ctx.scopes + (title,))
    if isinstance(document, Schematic) and ctx.resolve_connections:
        stored = document.model_dump_json()
        if any(token in stored for token in ("NET_NAME", "NET_CLASS")):
            from eedesigncore.text_resolution.connections import with_connections

            ctx = with_connections(document, ctx)
    resolver = DocumentResolver(document, ctx)
    copy = document.model_copy(deep=True)
    resolver.walk(copy, None, ctx, "$")
    if isinstance(copy, Schematic):
        for index, placed in enumerate(copy.symbols):
            original = document.symbols[index].model_copy(update={"unit": placed.unit})
            definition = resolver.definitions.get(
                original.lib_name or ""
            ) or resolver.definitions.get(original.lib_id)
            if definition is None:
                continue
            source, fields = resolve_symbol_definition(definition, resolver.definitions)
            if not any(
                "${" in str(item) or "@{" in str(item)
                for unit in source.units
                for item in unit.items
            ):
                continue
            resolved = source.model_copy(deep=True)
            resolved.fields = list(fields)
            for unit_index, unit in enumerate(resolved.units):
                for item_index, item in enumerate(unit.items):
                    if isinstance(item, BaseModel):
                        resolver.walk(
                            item,
                            original,
                            ctx,
                            f"$.symbols[{index}].graphics[{unit_index}][{item_index}]",
                        )
            resolved.name = f"__shown_symbol_{index}"
            resolved.extends = None
            placed.lib_name = resolved.name
            copy.lib_symbols.append(resolved)
    return ResolvedDocumentText(copy, resolver.records)


def _set_text(value: BaseModel, attribute: str, text: str) -> None:
    setattr(value, attribute, text)


def _get_text(value: BaseModel, attribute: str) -> str:
    text = getattr(value, attribute)
    if not isinstance(text, str):
        raise ValueError("The selected text attribute is not a string.")
    return text


def _on_border(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = dx * dx + dy * dy
        fraction = (
            max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length))
            if length
            else 0.0
        )
        if (point[0] - first[0] - fraction * dx) ** 2 + (
            point[1] - first[1] - fraction * dy
        ) ** 2 <= 0.000005**2:
            return True
    return False
