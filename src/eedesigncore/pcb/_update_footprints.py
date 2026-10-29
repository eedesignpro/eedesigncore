"""Footprint placement and identity preservation for schematic updates."""

from __future__ import annotations

from collections import Counter
from uuid import UUID, uuid5

from eedesigncore.common.uuid_ops import _iter_models, _remap_members
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.operations import inject_footprint
from eedesigncore.pcb.transform import flip_footprint, set_footprint_orientation
from eedesigncore.project._update_common import issue
from eedesigncore.project.update_models import DesignUpdateIssue


def stable_ids(footprint: Footprint, seed: str, key: str) -> None:
    models = list(_iter_models(footprint))
    remap: dict[str, str] = {}
    for index, model in enumerate(models):
        for name in ("uuid", "id"):
            if name in type(model).model_fields:
                old = getattr(model, name)
                new = str(uuid5(UUID(seed), f"{key}:{index}:{name}"))
                if old:
                    remap[old] = new
                setattr(model, name, new)
    _remap_members(models, remap)


def replace_or_add(
    board: Board,
    definition: Footprint,
    old: Footprint | None,
    placement: FootprintPlacement,
    seed: str,
    reference: str,
    issues: list[DesignUpdateIssue],
) -> Footprint:
    library = definition.model_copy(deep=True)
    for embedded in library.embedded_files:
        for current in board.embedded_files:
            if current.name == embedded.name and current.checksum != embedded.checksum:
                issue(
                    issues,
                    "embedded_conflict",
                    "Embedded files have the same name and different checksums.",
                    embedded.name,
                )
    stable_ids(library, seed, reference)
    new = inject_footprint(board, library, at=placement.position, regenerate_uuids=False)
    new.uuid = library.uuid
    if old is not None and old.layer == "B.Cu":
        flip_footprint(new, direction="top_bottom", board=board)
    set_footprint_orientation(new, placement.angle)
    if old is None:
        return new
    new.uuid = old.uuid
    new.locked = old.locked
    new.placed = old.placed
    new.path = old.path
    new.variants = [variant.model_copy(deep=True) for variant in old.variants]
    remap: dict[str, str] = {}
    old_counts = Counter(pad.number for pad in old.pads)
    new_counts = Counter(pad.number for pad in new.pads)
    for pad in new.pads:
        candidates = [item for item in old.pads if item.number == pad.number]
        if len(candidates) > 1:
            candidates = [
                item
                for item in candidates
                if item.position == pad.position and item.layers == pad.layers
            ]
        if len(candidates) == 1 and (
            old_counts[pad.number] == new_counts[pad.number] == 1
            or sum(
                item.number == pad.number
                and item.position == pad.position
                and item.layers == pad.layers
                for item in new.pads
            )
            == 1
        ):
            if candidates[0].id and pad.id:
                remap[pad.id] = candidates[0].id
                pad.id = candidates[0].id
            pad.net_name = candidates[0].net_name
            pad.net_code = candidates[0].net_code
            pad.pinfunction = candidates[0].pinfunction
            pad.pintype = candidates[0].pintype
        elif candidates:
            issue(
                issues,
                "ambiguous_pad_identity",
                "Duplicate pads cannot be matched uniquely for footprint replacement.",
                reference,
            )
    for field in new.properties:
        previous = [item for item in old.properties if item.name == field.name]
        if len(previous) == 1 and previous[0].id and field.id:
            remap[field.id] = previous[0].id
            field.id = previous[0].id
        if field.name in {"Reference", "Value"} and len(previous) == 1:
            field.value = previous[0].value
    _remap_members(list(_iter_models(new)), remap)
    old_ids = {
        str(getattr(model, name))
        for model in _iter_models(old)
        for name in ("uuid", "id")
        if name in type(model).model_fields and getattr(model, name)
    }
    new_ids = {
        str(getattr(model, name))
        for model in _iter_models(new)
        for name in ("uuid", "id")
        if name in type(model).model_fields and getattr(model, name)
    }
    removed = old_ids - new_ids
    for owner in board.groups:
        if removed.intersection(owner.members):
            issue(
                issues,
                "group_member_replacement",
                "A board group or generator references a replaced footprint child.",
                reference,
            )
    for generator in board.generators:
        if removed.intersection(generator.members):
            issue(
                issues,
                "generator_member_replacement",
                "A generator references a replaced footprint child.",
                reference,
            )
    index = next(index for index, item in enumerate(board.footprints) if item is old)
    board.footprints.pop()
    board.footprints[index] = new
    issue(
        issues,
        "identity_matching",
        "Replacement preserves matched pad and field identities. "
        "Native drawing similarity matching is not reproduced.",
        reference,
        warning=True,
    )
    return new
