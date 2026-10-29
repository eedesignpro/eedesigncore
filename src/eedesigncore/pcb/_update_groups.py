"""Transfer schematic group membership without changing unrelated board members."""

from __future__ import annotations

from collections import defaultdict

from eedesigncore.common.pcb_family import PcbGroup
from eedesigncore.common.uuid_ops import _iter_models
from eedesigncore.pcb.models import Board
from eedesigncore.project._update_common import issue, sources, symbol_path
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import DesignUpdateChange, DesignUpdateIssue
from eedesigncore.sch.models import Group, Schematic
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def transfer_groups(
    board: Board,
    source: Schematic | SheetHierarchy,
    inspection: SchematicElectricalSnapshot,
    parts: dict[str, list[str]],
    changes: list[DesignUpdateChange],
    issues: list[DesignUpdateIssue],
) -> None:
    non_group_ids = {
        str(getattr(model, name))
        for model in _iter_models(board)
        if all(model is not group for group in board.groups)
        for name in ("uuid", "id")
        if name in type(model).model_fields and getattr(model, name)
    }
    membership: dict[str, list[Group]] = defaultdict(list)
    for path, document, schematic in sources(source):
        for source_group in schematic.groups:
            if source_group.uuid in non_group_ids:
                issue(
                    issues,
                    "group_identity_collision",
                    "The source group UUID belongs to another board item.",
                    source_group.uuid or "",
                )
                continue
            if not source_group.uuid:
                issue(issues, "group_identity", "A source group has no UUID.", document)
                continue
            if sum(key == document for _, key, _ in sources(source)) > 1:
                issue(
                    issues,
                    "shared_group",
                    "Repeated sheets need occurrence-qualified group identities.",
                    document,
                )
                continue
            for member in source_group.members:
                membership[path.rstrip("/") + "/" + member].append(source_group)
    touched: set[str] = set()
    for reference, ids in parts.items():
        paths = {symbol_path(inspection, key) for key in ids}
        footprint = next((fp for fp in board.footprints if fp.path in paths), None)
        if footprint is None or footprint.uuid is None:
            continue
        groups = {group.uuid: group for path in paths for group in membership[path]}
        if len(groups) > 1:
            issue(
                issues,
                "unit_group_conflict",
                "Units of one physical part belong to different source groups.",
                reference,
            )
            continue
        target = next(iter(groups.values()), None)
        for group in board.groups:
            if footprint.uuid in group.members and (target is None or group.id != target.uuid):
                group.members.remove(footprint.uuid)
                if group.id:
                    touched.add(group.id)
        if target is not None:
            existing = [group for group in board.groups if group.id == target.uuid]
            if len(existing) > 1:
                issue(
                    issues,
                    "duplicate_group",
                    "Board group UUIDs are not unique.",
                    target.uuid or "",
                )
                continue
            if existing:
                destination = existing[0]
                destination.name = target.name
                destination.lib_id = target.lib_id
            else:
                destination = PcbGroup(name=target.name, id=target.uuid, lib_id=target.lib_id)
                board.groups.append(destination)
            if footprint.uuid not in destination.members:
                destination.members.append(footprint.uuid)
                changes.append(
                    DesignUpdateChange(
                        kind="group_footprint",
                        reference=reference,
                        identity=footprint.uuid,
                        after=target.uuid,
                    )
                )
    for group in list(board.groups):
        if group.id in touched and len(group.members) < 2:
            if any(group.id in owner.members for owner in board.groups) or any(
                group.id in owner.members for owner in board.generators
            ):
                issue(
                    issues,
                    "nested_group_removal",
                    "A parent group or generator references the source group to remove.",
                    group.id or "",
                )
                continue
            board.groups.remove(group)
            changes.append(
                DesignUpdateChange(
                    kind="remove_group", reference="", identity=group.id or "", before=group.name
                )
            )
