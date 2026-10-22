"""Prepare native sheets through the same page contract used by assembly roots."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from eedesigncore.common.paths import is_remote_uri
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.common.text_variables import has_reference, resolve
from eedesigncore.connectivity.netname import escape_netname
from eedesigncore.inspection.portable import _hash
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode, load_hierarchy
from eedesigncore.render import ViewBox
from eedesigncore.render.document import build_document
from eedesigncore.render.schematic_metadata import DrawingMetadata
from eedesigncore.render.schematics import _render_schematic_svg
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.electrical import ElectricalPreparation
from eedesigncore.schematic_view.models import (
    SchematicBus,
    SchematicCoverage,
    SchematicIssue,
    SchematicNet,
    SchematicPage,
    SchematicView,
    ViewBounds,
    ViewItem,
    ViewPinSelection,
    ViewSource,
    view_identity,
)
from eedesigncore.text_resolution import (
    TextResolutionContext,
    context_for_sheet,
    resolve_document_text,
)

_PAPER = {
    "A0": (1189, 841),
    "A1": (841, 594),
    "A2": (594, 420),
    "A3": (420, 297),
    "A4": (297, 210),
    "A5": (210, 148),
    "A": (279.4, 215.9),
    "B": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9),
    "USLegal": (355.6, 215.9),
    "USLedger": (431.8, 279.4),
}


def _bounds(sheet: Schematic) -> ViewBounds:
    paper = sheet.paper
    if paper is None:
        raise ValueError("A native sheet without paper settings requires explicit bounds.")
    if paper.paper_type == "User" and paper.width is not None and paper.height is not None:
        width, height = paper.width, paper.height
    elif paper.paper_type in _PAPER:
        width, height = _PAPER[paper.paper_type]
        if paper.portrait:
            width, height = height, width
    else:
        raise ValueError("Unknown paper dimensions. Supply explicit sheet bounds.")
    return ViewBounds(x=0, y=0, width=width, height=height)


def _electrical_text_paths(sheet: Schematic) -> set[str]:
    paths = {
        f"$.labels[{index}].content"
        for index, label in enumerate(sheet.labels)
        if label.kind != "netclass_flag"
    }
    for index, child in enumerate(sheet.sheets):
        paths.update(f"$.sheets[{index}].pins[{pin}].name" for pin in range(len(child.pins)))
        paths.update(
            f"$.sheets[{index}].fields[{field_index}].value"
            for field_index, field in enumerate(child.fields)
            if field.name == "Sheetname"
        )
    definitions = {symbol.name: symbol for symbol in sheet.lib_symbols}
    for index, symbol in enumerate(sheet.symbols):
        definition = definitions.get(symbol.lib_name or "") or definitions.get(symbol.lib_id)
        if definition is not None:
            definition, _ = resolve_symbol_definition(definition, definitions)
        power = bool(definition and definition.power_type)
        paths.update(
            f"$.symbols[{index}].fields[{field_index}].value"
            for field_index, field in enumerate(symbol.fields)
            if field.name == "Reference" or (power and field.name == "Value")
        )
    return paths


def prepare_schematic_view(
    source: str | Path | Schematic | SheetHierarchy,
    *,
    document_id: str,
    instance: str = "design",
    sheet_bounds: dict[str, ViewBounds] | None = None,
    strict: bool = True,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
) -> SchematicView:
    """Copy native sheets into portable pages with occurrence-qualified navigation."""
    if isinstance(source, str | Path):
        if is_remote_uri(str(source)):
            raise ValueError("Schematic preparation requires a local source path.")
        source = load_hierarchy(
            source, strict=strict, max_occurrences=4096, max_total_bytes=256 * 1024 * 1024
        )
    source = source.model_copy(deep=True)
    project = source.project if isinstance(source, SheetHierarchy) else None
    electrical = ElectricalPreparation(
        document_id, instance, dict(project.bus_aliases) if project else {}
    )
    scopes: dict[str, dict[str, str]] = {}
    numbers: dict[str, str | None] = {}
    prefixes: dict[str, str] = {}
    excluded: dict[str, bool] = {}
    entries: list[tuple[str, Schematic, str, str | None, list[str]]] = []
    notes: list[str] = []

    visited_paths: set[str] = set()

    def visit(node: SheetNode, parent: str | None, depth: int = 0) -> None:
        if depth > 64 or len(entries) >= 4096 or node.kiid_path in visited_paths:
            raise ValueError("Schematic hierarchy exceeds limits or repeats an occurrence path.")
        visited_paths.add(node.kiid_path)
        scope = dict(scopes.get(parent or "", dict(project.text_variables) if project else {}))
        if node.sheet is not None:
            scope.update(
                {
                    field.name: field.value
                    for field in node.sheet.fields
                    if field.name not in {"Sheetname", "Sheetfile"}
                }
            )
        scopes[node.kiid_path] = scope
        numbers[node.kiid_path] = node.page
        excluded[node.kiid_path] = excluded.get(parent or "", False) or (
            node.sheet is not None and node.sheet.on_board is False
        )
        name = resolve(node.name, scope)
        prefixes[node.kiid_path] = prefixes[parent] + escape_netname(name) + "/" if parent else "/"
        entries.append(
            (
                node.kiid_path,
                node.schematic,
                name,
                parent,
                [child.kiid_path for child in node.children],
            )
        )
        for child in node.children:
            visit(child, node.kiid_path, depth + 1)

    if isinstance(source, SheetHierarchy):
        notes.extend(source.errors)
        for root in source.roots:
            visit(root, None)
    else:
        if source.sheets:
            notes.append(
                "Only one native sheet was supplied. Child sheet drawings are unavailable."
            )
        entries.append(("/" + (source.uuid or "root"), source, instance, None, []))
    if not entries:
        raise ValueError("No native schematic sheets are available.")
    if len(entries) > 4096 or len({entry[0] for entry in entries}) != len(entries):
        raise ValueError("Schematic occurrence paths are duplicated or exceed the page limit.")
    if not prefixes:
        prefixes[entries[0][0]] = "/"
    repeated_prefixes = {
        prefix for prefix in prefixes.values() if list(prefixes.values()).count(prefix) > 1
    }
    for path, prefix in prefixes.items():
        if prefix in repeated_prefixes:
            prefixes[path] = prefix + "@" + path.strip("/").replace("/", "_") + "/"
    pages: dict[str, SchematicPage] = {}
    roots: list[str] = []
    for path, sheet, name, parent, children in entries:
        sheet = sheet.model_copy(deep=True)
        text_scope = text_context or TextResolutionContext()
        if isinstance(source, SheetHierarchy):
            text_scope = context_for_sheet(source, path, context=text_scope)
        else:
            text_scope = replace(text_scope, instance_path=path)
        resolved_text = resolve_document_text(sheet, text_scope)
        sheet = resolved_text.document
        page_id = view_identity(document_id, instance, "sheet", path)
        items: dict[str, ViewItem] = {}
        symbol_ids: dict[int, str] = {}
        sheet_ids: dict[int, str] = {}
        for index, symbol in enumerate(sheet.symbols):
            reference = next(
                (field.value for field in symbol.fields if field.name == "Reference"),
                symbol.lib_id,
            )
            key = view_identity(
                document_id,
                instance,
                path,
                "symbol",
                symbol.uuid or _hash(symbol.model_dump(mode="json")),
            )
            if key in items:
                raise ValueError("Duplicate symbol identity within a schematic occurrence.")
            symbol_ids[index] = key
            items[key] = ViewItem(
                id=key,
                kind="symbol",
                label=reference,
                reference=reference,
                unit=symbol.unit,
                position=symbol.position,
                source=ViewSource(instance=instance, sheet_path=path, source_uuid=symbol.uuid),
                pins=[
                    ViewPinSelection(
                        number=pin.number, source_uuid=pin.uuid, alternate=pin.alternate
                    )
                    for pin in symbol.pins
                ],
            )
        for child in children:
            key = view_identity(document_id, instance, "sheet-link", child)
            items[key] = ViewItem(
                id=key,
                kind="sheet",
                label=next(entry[2] for entry in entries if entry[0] == child),
                source=ViewSource(instance=instance, sheet_path=child),
                target_pages=[view_identity(document_id, instance, "sheet", child)],
            )
        for index, child_sheet in enumerate(sheet.sheets):
            child_path = path.rstrip("/") + "/" + (child_sheet.uuid or "")
            if child_path in children:
                key = view_identity(document_id, instance, "sheet-link", child_path)
                sheet_ids[index] = key
                link_source = items[key].source
                if link_source is not None:
                    link_source.source_uuid = child_sheet.uuid
                items[key].position = child_sheet.position
                if child_sheet.size[0] > 0 and child_sheet.size[1] > 0:
                    items[key].bounds = ViewBounds(
                        x=child_sheet.position[0],
                        y=child_sheet.position[1],
                        width=child_sheet.size[0],
                        height=child_sheet.size[1],
                    )
        bounds = (sheet_bounds or {}).get(path) or _bounds(sheet)
        window = ViewBox(bounds.x, bounds.y, bounds.width, bounds.height)
        svg = build_document(window, [], 4)
        if parent is None:
            roots.append(page_id)
        pages[page_id] = SchematicPage(
            id=page_id,
            title=name,
            page_number=numbers.get(path),
            resolved_texts=dict(resolved_text.texts),
            coverage=SchematicCoverage(drawing="complete", electrical="complete"),
            kind="native_sheet",
            bounds=bounds,
            svg=svg,
            parent=view_identity(document_id, instance, "sheet", parent) if parent else None,
            source=ViewSource(instance=instance, sheet_path=path, source_uuid=sheet.uuid),
            items=items,
            capabilities=[
                "svg",
                "symbol_selection",
                "hierarchy_navigation",
                "source_item_selection",
                "net_highlighting",
                "symbol_bounds",
                "bus_membership",
            ],
            notes=[
                "Page bounds use paper dimensions unless explicitly supplied.",
                "Missing source UUIDs use content identities that change with the item.",
            ],
        )
        page = pages[page_id]
        electrical_paths = _electrical_text_paths(sheet)
        for text_path, result in resolved_text.texts.items():
            for diagnostic in result.diagnostics:
                page.coverage.issues.append(
                    SchematicIssue(
                        code="unresolved_text_variable",
                        scope="electrical" if text_path in electrical_paths else "drawing",
                        status="uncertain",
                        message=f"{text_path}: {diagnostic.message}",
                        sheet_path=path,
                    )
                )
        metadata = DrawingMetadata(
            ids={
                **{f"symbol:{index}": key for index, key in symbol_ids.items()},
                **{f"sheet:{index}": key for index, key in sheet_ids.items()},
            }
        )
        for index, child_sheet in enumerate(sheet.sheets):
            if f"sheet:{index}" not in metadata.ids:
                key = view_identity(
                    document_id, instance, path, "sheet", child_sheet.uuid or str(index)
                )
                page.items[key] = ViewItem(
                    id=key,
                    kind="sheet",
                    label=next(
                        (field.value for field in child_sheet.fields if field.name == "Sheetname"),
                        "",
                    ),
                    position=child_sheet.position,
                    source=ViewSource(
                        instance=instance, sheet_path=path, source_uuid=child_sheet.uuid
                    ),
                )
                metadata.ids[f"sheet:{index}"] = key
        definitions = {definition.name for definition in sheet.lib_symbols}
        for symbol_index, symbol in enumerate(sheet.symbols):
            if symbol.lib_id not in definitions and symbol.lib_name not in definitions:
                page.coverage.electrical = "partial"
                page.coverage.issues.append(
                    SchematicIssue(
                        code="missing_symbol_definition",
                        scope="electrical",
                        status="omitted",
                        message=(
                            "The embedded symbol definition is missing. "
                            "Its electrical pins are unavailable."
                        ),
                        item_ids=[symbol_ids[symbol_index]],
                        sheet_path=path,
                    )
                )
            if symbol.uuid is None:
                page.coverage.issues.append(
                    SchematicIssue(
                        code="missing_source_uuid",
                        scope="identity",
                        status="uncertain",
                        message="Content-derived symbol identity changes with the symbol.",
                        item_ids=[symbol_ids[symbol_index]],
                        sheet_path=path,
                    )
                )
        electrical.add_sheet(
            sheet,
            page,
            metadata,
            path,
            prefixes[path],
            {child.rsplit("/", 1)[-1]: child.rsplit("/", 1)[-1] for child in children},
            excluded.get(path, False),
        )
        if any(
            has_reference(label.content) for label in sheet.labels if label.kind != "netclass_flag"
        ):
            page.coverage.electrical = "partial"
            page.coverage.issues.append(
                SchematicIssue(
                    code="unresolved_text_variable",
                    scope="electrical",
                    status="uncertain",
                    message="Some label variables remain unresolved.",
                    sheet_path=path,
                )
            )
        if sheet.embedded_fonts:
            page.coverage.issues.append(
                SchematicIssue(
                    code="embedded_font_substitution",
                    scope="drawing",
                    status="unsupported",
                    message="The drawing uses the selected export font instead of embedded fonts."
                    if font is not None
                    else "The drawing uses the KiCad stroke font instead of embedded fonts.",
                    sheet_path=path,
                )
            )
        page.coverage.issues.append(
            SchematicIssue(
                code="drawing_sheet_not_rendered",
                scope="drawing",
                status="unsupported",
                message="Page borders and drawing-sheet title blocks are not included.",
                sheet_path=path,
            )
        )
        page.coverage.drawing = "partial"
        try:
            page.svg = _render_schematic_svg(sheet, view_box=window, font=font, metadata=metadata)
        except ValueError as error:
            page.coverage.drawing = "unavailable"
            page.coverage.issues.append(
                SchematicIssue(
                    code="drawing_unavailable",
                    scope="drawing",
                    status="unsupported",
                    message=str(error),
                    sheet_path=path,
                )
            )
            metadata.bounds.clear()
            metadata.bodies.clear()
        for key, box in metadata.bounds.items():
            page.items[key].bounds = ViewBounds(
                x=box.min_x,
                y=box.min_y,
                width=max(box.width, 0.0002),
                height=max(box.height, 0.0002),
            )
            page.items[key].drawn = True
        for key, box in metadata.bodies.items():
            page.items[key].body_bounds = ViewBounds(
                x=box.min_x,
                y=box.min_y,
                width=max(box.width, 0.0002),
                height=max(box.height, 0.0002),
            )
    symbol_references: dict[tuple[str, int | None], list[str]] = {}
    ambiguous: list[str] = []
    for page in pages.values():
        for item in page.items.values():
            if item.kind == "symbol" and any(
                pin.parent_item == item.id for pin in page.items.values()
            ):
                symbol_references.setdefault((item.reference or "", item.unit), []).append(item.id)
                if (
                    not item.reference
                    or item.reference.endswith("?")
                    or any(
                        not pin.reference
                        for pin in page.items.values()
                        if pin.parent_item == item.id and pin.kind == "pin"
                    )
                ):
                    ambiguous.append(item.id)
    ambiguous.extend(key for keys in symbol_references.values() if len(keys) > 1 for key in keys)
    nets: dict[str, SchematicNet]
    buses: dict[str, SchematicBus]
    unresolved_variables = any(
        issue.code == "unresolved_text_variable" and issue.scope == "electrical"
        for page in pages.values()
        for issue in page.coverage.issues
    )
    if ambiguous or unresolved_variables:
        nets, buses = {}, {}
        for page in pages.values():
            page.coverage.electrical = "unavailable"
            if not ambiguous:
                continue
            page.coverage.issues.append(
                SchematicIssue(
                    code="ambiguous_annotation",
                    scope="electrical",
                    status="uncertain",
                    message=(
                        "Missing or duplicate references prevent "
                        "proven project-wide electrical identities."
                    ),
                    item_ids=sorted(set(ambiguous)),
                )
            )
    else:
        nets, buses = electrical.finish(pages)
    if notes:
        for page in pages.values():
            if page.coverage.electrical != "unavailable":
                page.coverage.electrical = "partial"
            page.coverage.issues.extend(
                SchematicIssue(
                    code="incomplete_hierarchy", scope="hierarchy", status="omitted", message=note
                )
                for note in notes
            )
    coverage = SchematicCoverage(
        drawing="complete"
        if all(page.coverage.drawing == "complete" for page in pages.values())
        else "partial",
        electrical="unavailable"
        if ambiguous or unresolved_variables
        else "complete"
        if all(page.coverage.electrical == "complete" for page in pages.values())
        else "partial",
        issues=[issue for page in pages.values() for issue in page.coverage.issues],
    )
    return SchematicView(
        document_id=document_id,
        source_revision=_hash(
            {
                "schematics": source.model_dump(mode="json", exclude={"project"}),
                "project": project.to_string() if project else None,
            }
        ),
        roots=roots,
        nets=nets,
        buses=buses,
        coverage=coverage,
        pages=pages,
        notes=notes,
    )
