"""Explicitly selected existing-footprint association and pad-net updates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.pcb.models import Board
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


class BoardUpdateSelection(KicadModel):
    footprint_uuid: str
    symbol_id: str
    update_pad_nets: bool = True
    update_reference: bool = False
    footprint_policy: Literal["require_match", "preserve_existing"] = "require_match"


class BoardPadNetUpdate(KicadModel):
    pad_index: int
    pad_uuid: str | None
    number: str
    before_net: str | None
    after_net: str


class BoardFootprintUpdate(KicadModel):
    footprint_uuid: str
    symbol_id: str
    board_reference: str | None
    schematic_reference: str | None
    board_footprint: str
    schematic_footprint: str | None
    before_path: str | None
    after_path: str
    position: tuple[float, float] | None
    angle: float | None
    side: str
    locked: bool
    update_reference: bool = False
    no_connect_pad_numbers: list[str] = Field(default_factory=list)
    pad_updates: list[BoardPadNetUpdate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SchematicBoardUpdatePlan(KicadModel):
    scope: Literal["existing_footprint_associations_and_pad_nets"] = (
        "existing_footprint_associations_and_pad_nets"
    )
    board_signature: str
    inspection_signature: str
    selections: list[BoardUpdateSelection]
    updates: list[BoardFootprintUpdate]
    blockers: list[str]
    preserved_footprint_uuids: list[str | None]
    net_changes: dict[str, list[str]]


def _board_signature(board: Board) -> str:
    return hashlib.sha256(board.to_string().encode()).hexdigest()


def _inspection_signature(inspection: SchematicElectricalSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            inspection.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def plan_schematic_board_update(
    board: Board,
    inspection: SchematicElectricalSnapshot,
    *,
    selections: Sequence[BoardUpdateSelection],
) -> SchematicBoardUpdatePlan:
    """Plan only explicitly selected associations and pad assignments, without writes.

    No reference fallback, footprint replacement, addition, deletion, or metadata
    propagation occurs. Existing geometry and copper remain unchanged. Changes to
    nets referenced by tracks, zones, or copper graphics require another operation.
    """
    blockers: list[str] = []
    if inspection.coverage.electrical != "complete":
        blockers.append("Electrical inspection coverage is incomplete.")
    footprint_counts = Counter(fp.uuid for fp in board.footprints)
    selected_counts = Counter(selection.footprint_uuid for selection in selections)
    symbol_counts = Counter(selection.symbol_id for selection in selections)
    for selection in selections:
        if (
            selected_counts[selection.footprint_uuid] != 1
            or symbol_counts[selection.symbol_id] != 1
        ):
            blockers.append(
                "Selections must map distinct footprints to distinct symbol occurrences."
            )
    footprints = {fp.uuid: fp for fp in board.footprints if fp.uuid}
    without_pads = board.model_copy(deep=True)
    for copper_footprint in without_pads.footprints:
        copper_footprint.pads = []
    copper_nets = without_pads.net_names
    updates: list[BoardFootprintUpdate] = []
    net_targets: dict[str, set[str]] = defaultdict(set)
    for selection in selections:
        fp = footprints.get(selection.footprint_uuid)
        part = inspection.parts.get(selection.symbol_id)
        item = inspection.items.get(selection.symbol_id)
        if fp is None or footprint_counts[selection.footprint_uuid] != 1:
            blockers.append(f"Missing or ambiguous footprint UUID: {selection.footprint_uuid}")
            continue
        if (
            part is None
            or item is None
            or item.source is None
            or not item.source.source_uuid
            or not item.source.sheet_path
        ):
            blockers.append(f"Missing symbol occurrence identity: {selection.symbol_id}")
            continue
        if not part.on_board:
            blockers.append(f"Selected symbol is excluded from the board: {selection.symbol_id}")
        if sum(other.reference == part.reference for other in inspection.parts.values()) != 1:
            blockers.append(
                f"Multi-unit or ambiguous reference requires another planner: {part.reference}"
            )
        desired_footprint = part.fields.get("Footprint")
        if not desired_footprint:
            blockers.append(f"Selected symbol has no footprint identifier: {part.reference}")
        elif desired_footprint != fp.fp_id and selection.footprint_policy == "require_match":
            blockers.append(
                f"Footprint identifier differs for {part.reference}: "
                f"{fp.fp_id} versus {desired_footprint}"
            )
        reference = next(
            (field.value for field in fp.properties if field.name == "Reference"), None
        )
        if reference != part.reference and not selection.update_reference:
            blockers.append(
                f"Reference change is outside this planner: {reference} versus {part.reference}"
            )
        if (
            selection.update_reference
            and sum(field.name == "Reference" for field in fp.properties) != 1
        ):
            blockers.append("Reference update requires one existing Reference field.")
        update = BoardFootprintUpdate(
            footprint_uuid=selection.footprint_uuid,
            symbol_id=part.id,
            board_reference=reference,
            schematic_reference=part.reference,
            board_footprint=fp.fp_id,
            schematic_footprint=desired_footprint,
            before_path=fp.path,
            after_path=item.source.sheet_path.rstrip("/") + "/" + item.source.source_uuid,
            position=fp.at.position if fp.at else None,
            angle=fp.at.angle if fp.at else None,
            side=fp.layer,
            locked=fp.locked,
            update_reference=selection.update_reference,
        )
        if desired_footprint != fp.fp_id and selection.footprint_policy == "preserve_existing":
            update.notes.append(
                "Temporary geometry preservation only. Selected library parity is not established."
            )
        update.notes.append("Value, population flags, and library metadata remain unchanged.")
        if not selection.update_pad_nets:
            update.notes.append(
                "The caller selected association-only mode. Pad assignments remain unchanged."
            )
            updates.append(update)
            continue
        no_connect_numbers: set[str] = set()
        connected_numbers: set[str] = set()
        wanted: dict[str, set[str | None]] = defaultdict(set)
        for pin in inspection.items.values():
            if pin.kind == "pin" and pin.parent_item == part.id and pin.netlist_member:
                if pin.pin_number is not None and (
                    pin.id in inspection.no_connect_pins or pin.electrical_type == "no_connect"
                ):
                    no_connect_numbers.add(pin.pin_number)
                    continue
                if pin.pin_number is not None:
                    connected_numbers.add(pin.pin_number)
                if pin.pin_number is None or not pin.net_ids:
                    blockers.append(f"Selected pin lacks a resolved scalar net: {pin.id}")
                    continue
                for key in pin.net_ids:
                    if key not in inspection.nets:
                        blockers.append(f"Selected pin references an absent net: {pin.id}")
                    else:
                        wanted[pin.pin_number].add(inspection.nets[key].name)
        if no_connect_numbers & connected_numbers:
            blockers.append("A pad number has conflicting connected and no-connect pin evidence.")
        if not wanted and not no_connect_numbers:
            blockers.append(f"No board-member pins resolved for {part.reference}")
        numbers = {pad.number for pad in fp.pads if pad.number}
        for number in sorted((wanted.keys() | no_connect_numbers) - numbers):
            blockers.append(f"Schematic pin has no PCB pad: {part.reference}.{number}")
        for index, pad in enumerate(fp.pads):
            if not pad.number:
                continue
            if pad.number in no_connect_numbers:
                update.no_connect_pad_numbers.append(pad.number)
                if pad.net_name:
                    blockers.append(
                        f"No-connect pad already has a saved net: {part.reference}.{pad.number}"
                    )
                continue
            names = wanted.get(pad.number, set())
            if len(names) != 1 or not next(iter(names), None):
                blockers.append(
                    f"Missing or ambiguous assignment for PCB pad: {part.reference}.{pad.number}"
                )
                continue
            name = next(iter(names))
            assert name is not None
            if name.startswith("unconnected-("):
                blockers.append(
                    f"Isolated pin needs no-connect evidence: {part.reference}.{pad.number}"
                )
                continue
            net_targets[pad.net_name or ""].add(name)
            if pad.net_name != name:
                if (pad.net_name or "") in copper_nets:
                    blockers.append(
                        f"Net change affects copper: {part.reference}.{pad.number}, "
                        f"{pad.net_name!r}"
                    )
                update.pad_updates.append(
                    BoardPadNetUpdate(
                        pad_index=index,
                        pad_uuid=pad.id,
                        number=pad.number,
                        before_net=pad.net_name,
                        after_net=name,
                    )
                )
        updates.append(update)
    return SchematicBoardUpdatePlan(
        board_signature=_board_signature(board),
        inspection_signature=_inspection_signature(inspection),
        selections=[selection.model_copy(deep=True) for selection in selections],
        updates=updates,
        blockers=sorted(set(blockers)),
        preserved_footprint_uuids=[
            fp.uuid for fp in board.footprints if fp.uuid not in selected_counts
        ],
        net_changes={
            old: sorted(names) for old, names in sorted(net_targets.items()) if names != {old}
        },
    )


def apply_schematic_board_update(
    board: Board,
    inspection: SchematicElectricalSnapshot,
    plan: SchematicBoardUpdatePlan,
) -> Board:
    """Revalidate a plan and return a detached board. Save it with exact-source guards."""
    current = plan_schematic_board_update(board, inspection, selections=plan.selections)
    if current != plan:
        raise ValueError("The board, inspection, or update plan changed. Plan the update again.")
    if plan.blockers:
        raise ValueError(
            "The schematic board update plan has blockers: " + "; ".join(plan.blockers)
        )
    result = board.model_copy(deep=True)
    footprints = {fp.uuid: fp for fp in result.footprints}
    for update in plan.updates:
        fp = footprints[update.footprint_uuid]
        fp.path = update.after_path
        if update.update_reference:
            field = next(field for field in fp.properties if field.name == "Reference")
            field.value = update.schematic_reference or ""
        for pad_update in update.pad_updates:
            pad = fp.pads[pad_update.pad_index]
            pad.net_name = pad_update.after_net
            pad.net_code = None
    return result
