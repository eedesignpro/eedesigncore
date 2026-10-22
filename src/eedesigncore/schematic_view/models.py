"""Portable drawing and identity contract shared by native and assembly schematics."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from eedesigncore.assembly.models import AssemblyEvidence, ConnectorPinPair
from eedesigncore.common.base import KicadModel
from eedesigncore.inspection.portable import _json, _read, _validate
from eedesigncore.text_resolution.models import ResolvedText

Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e8, le=1e8)]


def view_identity(*parts: str) -> str:
    """Qualify identities without separator collisions or unsafe SVG identifiers."""
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return "view-" + hashlib.sha256(raw.encode()).hexdigest()


class ViewBounds(KicadModel):
    """Millimeter bounds with X rightward and Y downward."""

    x: Coordinate
    y: Coordinate
    width: Annotated[float, Field(strict=True, gt=0, le=1e8, allow_inf_nan=False)]
    height: Annotated[float, Field(strict=True, gt=0, le=1e8, allow_inf_nan=False)]


class ViewSource(KicadModel):
    """A qualified source identity, never an instruction to open a path."""

    instance: str
    sheet_path: str | None = None
    source_uuid: str | None = None
    record_id: str | None = None


class ViewContact(KicadModel):
    """One resolved mate contact pair and its system-net identity."""

    left_pin: ViewSource
    right_pin: ViewSource
    left_number: str
    right_number: str
    left_net: str
    right_net: str
    system_net: str
    left_net_name: str | None = None
    right_net_name: str | None = None


class ViewPinSelection(KicadModel):
    """Saved symbol pin alternate selection within one schematic occurrence."""

    number: str
    source_uuid: str | None = None
    alternate: str | None = None


class SchematicIssue(KicadModel):
    """A specific omission or uncertainty with source context."""

    code: str
    scope: Literal["drawing", "electrical", "identity", "hierarchy"]
    status: Literal["unsupported", "uncertain", "omitted"]
    message: str
    item_ids: list[str] = Field(default_factory=list)
    sheet_path: str | None = None


class SchematicCoverage(KicadModel):
    """Explicit coverage of prepared drawings and electrical associations."""

    drawing: Literal["complete", "partial", "unavailable", "not_calculated"] = "not_calculated"
    electrical: Literal["complete", "partial", "unavailable", "not_calculated"] = "not_calculated"
    issues: list[SchematicIssue] = Field(default_factory=list)


class SchematicNet(KicadModel):
    """One resolved electrical net and its occurrence-qualified source items."""

    id: str
    name: str | None = None
    item_ids: list[str] = Field(default_factory=list)


class SchematicBus(KicadModel):
    """A bus drawing and the member nets proven by electrical calculation."""

    id: str
    name: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    net_ids: list[str] = Field(default_factory=list)
    unresolved_members: list[str] = Field(default_factory=list)


class ViewItem(KicadModel):
    """Selectable geometry with explicit electrical and navigation relationships."""

    id: str
    kind: Literal[
        "board",
        "connector",
        "bundle",
        "symbol",
        "sheet",
        "wire",
        "bus",
        "pin",
        "label",
        "junction",
        "sheet_pin",
        "bus_entry",
        "no_connect",
    ]
    label: str
    source: ViewSource | None = None
    position: tuple[Coordinate, Coordinate] | None = None
    bounds: ViewBounds | None = None
    body_bounds: ViewBounds | None = None
    parent_item: str | None = None
    reference: str | None = None
    pin_number: str | None = None
    pin_name: str | None = None
    electrical_type: str | None = None
    netlist_member: bool | None = None
    net_ids: list[str] = Field(default_factory=list)
    bus_ids: list[str] = Field(default_factory=list)
    drawn: bool = False
    target_pages: list[str] = Field(default_factory=list)
    stack_name: str | None = None
    stack_position: int | None = None
    unit: int | None = None
    pins: list[ViewPinSelection] = Field(default_factory=list)
    mate_id: str | None = None
    status: Literal["resolved", "unresolved", "unmated", "source"] = "source"
    evidence: list[AssemblyEvidence] = Field(default_factory=list)
    declared_pairs: list[ConnectorPinPair] = Field(default_factory=list)
    contacts: list[ViewContact] = Field(default_factory=list)
    route: list[tuple[Coordinate, Coordinate]] = Field(default_factory=list)
    evidence_derivation: Literal["specified", "inferred"] | None = None


class SchematicPage(KicadModel):
    """A complete SVG page with separately queryable identity metadata."""

    id: str
    title: str
    kind: Literal["assembly_root", "native_sheet"]
    bounds: ViewBounds
    svg: str
    page_number: str | None = None
    coverage: SchematicCoverage = Field(default_factory=SchematicCoverage)
    parent: str | None = None
    source: ViewSource | None = None
    items: dict[str, ViewItem] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    resolved_texts: dict[str, ResolvedText] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_drawing(self) -> SchematicPage:
        if len(self.svg.encode("utf-8")) > 64 * 1024 * 1024:
            raise ValueError("Schematic SVG exceeds the page size limit.")
        if "<!DOCTYPE" in self.svg.upper() or "<!ENTITY" in self.svg.upper():
            raise ValueError("Schematic SVG cannot declare document types or entities.")
        declaration_removed = re.sub(r"^\s*<\?xml\s+[^?]*\?>", "", self.svg, count=1)
        if "<?" in declaration_removed:
            raise ValueError("Schematic SVG cannot contain processing instructions.")
        try:
            root = ET.fromstring(self.svg)
        except ET.ParseError as error:
            raise ValueError("Schematic SVG is malformed.") from error
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("Schematic pages require SVG drawings.")
        allowed = {
            "svg",
            "g",
            "path",
            "rect",
            "circle",
            "ellipse",
            "line",
            "polyline",
            "polygon",
            "text",
            "tspan",
            "title",
            "desc",
            "defs",
            "clipPath",
            "image",
        }
        if any(
            node.attrib.get("data-item-id") not in self.items
            for node in root.iter()
            if "data-item-id" in node.attrib
        ):
            raise ValueError("Schematic drawing references a missing item identity.")
        for node in root.iter():
            if node.tag.removeprefix("{http://www.w3.org/2000/svg}") not in allowed:
                raise ValueError("Schematic SVG contains an unsupported element.")
            for name, value in node.attrib.items():
                local = name.rsplit("}", 1)[-1].lower()
                if (
                    local == "style"
                    and value == "fill:none; stroke-linecap:round; stroke-linejoin:round;"
                ):
                    continue
                if local.startswith("on") or local in {"style", "src"}:
                    raise ValueError("Schematic SVG contains an executable or external attribute.")
                if local == "href" and not value.startswith(
                    ("data:image/png;base64,", "data:image/jpeg;base64,")
                ):
                    raise ValueError("Schematic SVG references an external resource.")
                if local in {"fill", "stroke"} and not re.fullmatch(
                    r"none|currentColor|#[0-9a-fA-F]{3,8}|url\(#[A-Za-z0-9_-]+\)", value
                ):
                    raise ValueError("Schematic SVG paint requires a color or local reference.")
                if local in {
                    "filter",
                    "mask",
                    "clip-path",
                    "cursor",
                    "marker",
                    "marker-start",
                    "marker-mid",
                    "marker-end",
                } and not re.fullmatch(r"none|url\(#[A-Za-z0-9_-]+\)", value):
                    raise ValueError("Schematic SVG effect requires a local reference.")
                if "url(" in value.lower() and not value.startswith("url(#"):
                    raise ValueError("Schematic SVG references an external resource.")
        for key, item in self.items.items():
            if key != item.id:
                raise ValueError("Schematic item key does not match its identity.")
        return self


class SchematicView(KicadModel):
    """Standalone schematic pages. Drawings never determine electrical connectivity."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["schematic-view-1"] = "schematic-view-1"
    document_id: str
    source_revision: str | None = None
    units: Literal["mm"] = "mm"
    nets: dict[str, SchematicNet] = Field(default_factory=dict)
    buses: dict[str, SchematicBus] = Field(default_factory=dict)
    coverage: SchematicCoverage = Field(default_factory=SchematicCoverage)
    roots: list[str]
    pages: dict[str, SchematicPage]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_links(self) -> SchematicView:
        if not self.roots or len(self.pages) > 4096:
            raise ValueError("Schematic views require roots and at most 4096 pages.")
        if len(set(self.roots)) != len(self.roots) or any(
            key not in self.pages for key in self.roots
        ):
            raise ValueError("Schematic root identifiers are invalid.")
        if sum(len(page.items) for page in self.pages.values()) > 1000000:
            raise ValueError("Schematic view exceeds the item limit.")
        all_items = {key: item for page in self.pages.values() for key, item in page.items.items()}
        if len(all_items) != sum(len(page.items) for page in self.pages.values()):
            raise ValueError("Schematic source item identities collide across pages.")
        for records in (self.nets, self.buses):
            for key, record in records.items():
                if key != record.id or any(item not in all_items for item in record.item_ids):
                    raise ValueError("Electrical record references a missing source item.")
        for net in self.nets.values():
            if len(set(net.item_ids)) != len(net.item_ids) or any(
                net.id not in all_items[key].net_ids for key in net.item_ids
            ):
                raise ValueError("Net membership must agree in both directions.")
        for bus in self.buses.values():
            if len(set(bus.item_ids)) != len(bus.item_ids) or any(
                bus.id not in all_items[key].bus_ids for key in bus.item_ids
            ):
                raise ValueError("Bus membership must agree in both directions.")
        for item in all_items.values():
            if any(key not in self.nets for key in item.net_ids) or any(
                key not in self.buses for key in item.bus_ids
            ):
                raise ValueError("Source item references a missing electrical record.")
            if any(item.id not in self.nets[key].item_ids for key in item.net_ids) or any(
                item.id not in self.buses[key].item_ids for key in item.bus_ids
            ):
                raise ValueError("Source membership must agree in both directions.")
            if item.parent_item is not None and item.parent_item not in all_items:
                raise ValueError("Source item references a missing parent item.")
        for bus in self.buses.values():
            if any(key not in self.nets for key in bus.net_ids):
                raise ValueError("Bus references a missing member net.")
        for key, page in self.pages.items():
            if key != page.id:
                raise ValueError("Schematic page key does not match its identity.")
            seen = {key}
            parent = page.parent
            while parent is not None:
                if parent not in self.pages or parent in seen:
                    raise ValueError(
                        "Schematic page hierarchy is missing a parent or has a cycle."
                    )
                seen.add(parent)
                parent = self.pages[parent].parent
            for item in page.items.values():
                if any(target not in self.pages for target in item.target_pages):
                    raise ValueError("Schematic navigation target is missing.")
        return self

    def to_json(self) -> str:
        """Validate and serialize deterministic standalone page data."""
        data = self.model_dump(mode="json")
        _validate(data)
        type(self).model_validate(data)
        return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"

    @classmethod
    def from_json(cls, text: str) -> SchematicView:
        """Read bounded data and validate drawings and navigation targets."""
        return cls.model_validate(_json(text))

    def save(self, path: str | Path) -> None:
        """Write prepared data to a caller-selected local file."""
        if str(path).replace("\\", "/").startswith("//") or "://" in str(path):
            raise ValueError("Schematic output requires a local path.")
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> SchematicView:
        """Read standalone pages without fetching source designs or external assets."""
        return cls.from_json(_read(path))
