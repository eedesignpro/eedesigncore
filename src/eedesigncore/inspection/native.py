"""Native KiCad inputs for the shared read-only inspection commands."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from eedesigncore.common.source_reads import _capture_reads
from eedesigncore.inspection.assembly import AssemblyInspector, open_portable_inspection
from eedesigncore.inspection.portable import (
    _MAX_REPLY,
    DesignInspector,
    Json,
    Object,
    _array,
    _number,
    _object,
    _strings,
    _text,
    _validate,
)
from eedesigncore.netbom import NetbomDocument, build_netbom
from eedesigncore.netbom.builder import _load_inputs
from eedesigncore.pcb.bbox import items_bbox
from eedesigncore.pcb.models import Board, TrackArc, TrackSegment, Via
from eedesigncore.pro.models import ProjectFile
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.render.traversal import via_layers
from eedesigncore.sch.models import Schematic


def inspect_design(
    source: NetbomDocument | Board | Schematic | SheetHierarchy,
    *,
    schematic: Schematic | SheetHierarchy | None = None,
    project: ProjectFile | None = None,
    design_name: str = "design",
) -> DesignInspector:
    """Create an inspection snapshot from loaded objects without file reads."""
    if not isinstance(source, NetbomDocument | Board | Schematic | SheetHierarchy):
        raise ValueError("Supply a netbom, board, schematic, or sheet hierarchy.")
    board = source if isinstance(source, Board) else None
    if isinstance(source, NetbomDocument):
        if schematic is not None or project is not None:
            raise ValueError("Additional native data requires a board or schematic input.")
        document = source
    elif isinstance(source, Board):
        document = build_netbom(
            source, schematic=schematic, project=project, design_name=design_name
        )
    else:
        if schematic is not None:
            raise ValueError("A schematic source cannot also have a schematic argument.")
        document = build_netbom(schematic=source, project=project, design_name=design_name)
    inspector = DesignInspector(cast(Object, document.model_dump(mode="json")))
    sources: dict[str, BaseModel] = {}
    if board is not None:
        sources["board"] = board
    source_schematic = schematic if isinstance(source, Board) else source
    if isinstance(source_schematic, Schematic):
        sources["schematic"] = source_schematic
    elif isinstance(source_schematic, SheetHierarchy):
        for node in source_schematic.walk():
            sources["sheet:" + node.kiid_path] = node.schematic
        if project is None:
            project = source_schematic.project
    if project is not None:
        sources["project"] = project
    if sources:
        snapshots = _attach_sources(inspector, sources)
        if board is not None:
            _attach_board(inspector, cast(Board, snapshots["board"]), clone=False)
    return inspector


def open_inspection(path: str | Path) -> DesignInspector | AssemblyInspector:
    """Read netbom, assembly, or native KiCad files for inspection."""
    source = Path(path)
    if source.suffix.lower() not in (".kicad_pcb", ".kicad_sch", ".kicad_pro"):
        return open_portable_inspection(source)
    with _capture_reads():
        native_path, board, hierarchy, project = _load_inputs(source)
        document = build_netbom(
            board, schematic=hierarchy, project=project, design_name=native_path.stem
        )
    inspector = inspect_design(document)
    sources: dict[str, BaseModel] = {}
    if board is not None:
        sources["board"] = board
    if hierarchy is not None:
        sources.update({"sheet:" + node.kiid_path: node.schematic for node in hierarchy.walk()})
    if project is not None:
        sources["project"] = project
    snapshots = _attach_sources(inspector, sources)
    if board is not None:
        _attach_board(inspector, cast(Board, snapshots["board"]), clone=False)
    return inspector


def portable_inspection_source() -> str:
    """Return the standalone standard-library tool for distribution beside design data."""
    import ast

    base = Path(__file__).with_name("portable.py").read_text(encoding="utf-8-sig")
    additions = []
    for filename in ("mates.py", "assembly.py"):
        source = Path(__file__).with_name(filename).read_text(encoding="utf-8-sig")
        lines = source.splitlines(keepends=True)
        for node in reversed(ast.parse(source).body):
            if isinstance(node, ast.ImportFrom) and node.module in (
                "__future__",
                "eedesigncore.inspection.portable",
                "eedesigncore.inspection.mates",
            ):
                del lines[node.lineno - 1 : node.end_lineno]
        additions.append("".join(lines))
    base = base[: base.rindex('if __name__ == "__main__":')]
    return (
        base
        + "\n"
        + "\n".join(additions)
        + '\nif __name__ == "__main__":\n    _cli(open_portable_inspection)\n'
    )


def _attach_board(inspector: DesignInspector, source: Board, *, clone: bool = True) -> None:
    board = source.model_copy(deep=True) if clone else source

    def geometry(inputs: Object) -> Object:
        net, layer, kind = inputs.get("net"), inputs.get("layer"), inputs.get("kind")
        if net is not None:
            net = _text(net, "net")
        if layer is not None:
            layer = _text(layer, "layer")
        if kind not in (None, "segment", "arc", "via", "zone", "footprint"):
            raise ValueError("Unsupported geometry kind.")
        box = None
        if "bbox" in inputs:
            values = _array(inputs["bbox"], "bbox")
            if len(values) != 4:
                raise ValueError("bbox requires min_x, min_y, max_x, max_y.")
            box = [_number(value, "bbox") for value in values]
            if box[0] > box[2] or box[1] > box[3]:
                raise ValueError("Bounding box minima must not exceed maxima.")
        matches: list[Json] = []
        objects = [
            *(("tracks", i, item) for i, item in enumerate(board.tracks)),
            *(("zones", i, item) for i, item in enumerate(board.zones)),
            *(("footprints", i, item) for i, item in enumerate(board.footprints)),
        ]
        for group, index, item in objects:
            item_kind = (
                "segment"
                if isinstance(item, TrackSegment)
                else "arc"
                if isinstance(item, TrackArc)
                else "via"
                if isinstance(item, Via)
                else "zone"
                if group == "zones"
                else "footprint"
            )
            if kind is not None and item_kind != kind:
                continue
            item_net = getattr(item, "net", getattr(item, "net_name", None))
            if group == "footprints":
                item_nets = sorted(
                    {pad.net_name for pad in board.footprints[index].pads if pad.net_name}
                )
            else:
                item_nets = [item_net] if item_net else []
            if net is not None and net not in item_nets:
                continue
            if isinstance(item, Via):
                layers = list(via_layers(item, board))
            else:
                layers = list(getattr(item, "layers", None) or [getattr(item, "layer", "")])
            if layer is not None and group != "footprints" and layer not in layers:
                continue
            measured = items_bbox(
                [item], layers={layer} if layer and group == "footprints" else None
            )
            if measured is None:
                continue
            bounds = [measured.min_x, measured.min_y, measured.max_x, measured.max_y]
            if box is not None and (
                bounds[2] < box[0]
                or bounds[0] > box[2]
                or bounds[3] < box[1]
                or bounds[1] > box[3]
            ):
                continue
            matches.append(
                {
                    "id": f"{group}/{index}",
                    "kind": item_kind,
                    "uuid": getattr(item, "uuid", getattr(item, "id", None)),
                    "nets": cast(Json, item_nets),
                    "layers": cast(Json, layers),
                    "bounds": cast(Json, bounds),
                }
            )
        page = inspector._page("geometry", inputs, matches)
        for value in _array(page["items"]):
            row = _object(value)
            group, index_text = _text(row["id"]).split("/")
            item_index = int(index_text)
            if group == "tracks":
                row["geometry"] = cast(Object, board.tracks[item_index].model_dump(mode="json"))
            elif group == "zones":
                zone = board.zones[item_index]
                row["geometry"] = {
                    "outlines": cast(Json, [p.model_dump(mode="json") for p in zone.polygons]),
                    "fill_freshness": "unknown",
                }
            else:
                footprint = board.footprints[item_index]
                row["geometry"] = {
                    "placement": cast(
                        Json, footprint.at.model_dump(mode="json") if footprint.at else None
                    ),
                    "reference": next(
                        (p.value for p in footprint.properties if p.name == "Reference"), ""
                    ),
                }
        page["units"] = "mm"
        page["meaning"] = (
            "Stored geometry and axis-aligned bounds. Clearance and continuity are not evaluated."
        )
        return page

    inspector._native_geometry = geometry


def _attach_sources(
    inspector: DesignInspector, supplied: dict[str, BaseModel]
) -> dict[str, BaseModel]:
    sources = copy.deepcopy(supplied)

    def available(inputs: Object) -> Object:
        return {
            "items": [
                {"id": key, "type": type(value).__name__} for key, value in sorted(sources.items())
            ]
        }

    def record(inputs: Object) -> Object:
        source = _text(inputs.get("source"), "source")
        path = _text(inputs.get("path", ""), "path")
        if source not in sources or len(path) > 2048 or len(path.split("/")) > 32:
            raise ValueError("Unknown source or invalid native record path.")
        value: object = sources[source]
        for part in path.split("/") if path else []:
            if isinstance(value, BaseModel) and part in type(value).model_fields:
                value = getattr(value, part)
            elif isinstance(value, dict) and part in value:
                value = value[part]
            elif (
                isinstance(value, list | tuple)
                and part.isdecimal()
                and len(part) <= 8
                and int(part) < len(value)
            ):
                value = value[int(part)]
            else:
                raise ValueError("Native record path does not identify stored data.")
        names = (
            list(type(value).model_fields)
            if isinstance(value, BaseModel)
            else list(value)
            if isinstance(value, dict)
            else []
        )
        if "fields" not in inputs:
            if names:
                return {"source": source, "path": path, "fields_available": cast(Json, names)}
            if isinstance(value, list | tuple):
                return {"source": source, "path": path, "length": len(value)}
            _validate(cast(Json, value), max_bytes=_MAX_REPLY)
            return {"source": source, "path": path, "value": cast(Json, value)}
        fields = _strings(inputs["fields"], "fields")
        if len(fields) > 32 or any(name not in names for name in fields):
            raise ValueError("Select at most 32 available native fields.")
        result: Object = {}
        for name in fields:
            item = (
                getattr(value, name)
                if isinstance(value, BaseModel)
                else cast(dict[str, object], value)[name]
            )
            result[name] = _native_json(item)
        _validate(result, max_bytes=_MAX_REPLY)
        return {"source": source, "path": path, "values": result}

    inspector._native_operations.update({"native_sources": available, "native_record": record})
    return sources


def _native_json(value: object) -> Json:
    budget = [0]

    def visit(item: object, depth: int) -> Json:
        budget[0] += 1
        if depth > 32 or budget[0] > 10000:
            raise ValueError("Native selection exceeds the structure limit. Select a child path.")
        if isinstance(item, BaseModel):
            return {
                name: visit(getattr(item, name), depth + 1) for name in type(item).model_fields
            }
        if isinstance(item, dict):
            return {str(key): visit(child, depth + 1) for key, child in item.items()}
        if isinstance(item, list | tuple):
            return [visit(child, depth + 1) for child in item]
        from enum import Enum

        if isinstance(item, Enum):
            return visit(item.value, depth + 1)
        if item is None or isinstance(item, str | int | float | bool):
            return item
        raise ValueError("Native selection has no JSON representation.")

    result = visit(value, 0)
    _validate(result, max_bytes=_MAX_REPLY)
    return result
