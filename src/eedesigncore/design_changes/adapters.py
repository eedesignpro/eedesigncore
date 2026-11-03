"""Convert the library's older change records into :class:`DesignChange` records.

The older records stay as they are. These functions only translate, so one
report or filter can read all of them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from eedesigncore.design_changes.records import ChangeCategory, ChangeKind, DesignChange
from eedesigncore.project.update_models import DesignUpdateChange
from eedesigncore.schematic_view.inspection import SchematicElectricalComparison

_ELECTRICAL_CATEGORIES: dict[str, ChangeCategory] = {
    "connectivity": "electrical",
    "net_names": "electrical",
    "pins": "electrical",
    "no_connect": "electrical",
    "classes": "electrical",
    "labels": "electrical",
    "parts": "part",
    "drawing": "geometry",
}

# Each update kind: its category, and whether it adds, removes, or changes.
_UPDATE_KINDS: dict[str, tuple[ChangeCategory, ChangeKind]] = {
    "add_footprint": ("part", "added"),
    "replace_footprint": ("part", "changed"),
    "update_footprint": ("part", "changed"),
    "delete_footprint": ("part", "removed"),
    "update_symbol": ("part", "changed"),
    "rename_copper_net": ("electrical", "changed"),
    "add_net_label": ("electrical", "added"),
    "rename_net_label": ("electrical", "changed"),
    "swap_pins": ("electrical", "changed"),
    "swap_unit": ("electrical", "changed"),
    "group_footprint": ("cosmetic", "changed"),
    "remove_group": ("cosmetic", "removed"),
    "ungroup_footprint": ("cosmetic", "changed"),
    "ungroup_group": ("cosmetic", "changed"),
}


# These update kinds store a whole model as JSON text. Every other kind stores plain text.
_JSON_UPDATE_KINDS = frozenset({"update_footprint", "update_symbol"})


def _as_json(value: str | None) -> str | None:
    """Keep JSON text as it is. Quote anything that is not strict JSON."""
    if value is None:
        return None
    try:
        json.loads(value, parse_constant=_refuse)
    except ValueError:
        return json.dumps(value)
    return value


def _refuse(token: str) -> object:
    raise ValueError(f"Not strict JSON: {token}")


def _text(value: str | None) -> str | None:
    """Quote plain text as a JSON string, so a net named ``12`` stays text."""
    return None if value is None else json.dumps(value)


def _kind(before: str | None, after: str | None) -> ChangeKind:
    if before is None or before == "null":
        return "added"
    if after is None or after == "null":
        return "removed"
    return "changed"


def changes_from_schematic_electrical(
    comparison: SchematicElectricalComparison,
) -> tuple[DesignChange, ...]:
    """Translate :func:`~eedesigncore.compare_schematic_electrical` changes.

    The original category becomes the ``field``. Pin groups, net names, pins,
    no-connects, net classes, and labels are ``electrical``. Parts are
    ``part``. Drawing changes are ``geometry``.
    """
    return tuple(
        DesignChange(
            category=_ELECTRICAL_CATEGORIES.get(change.category, "other"),
            kind=_kind(change.before_json, change.after_json),
            item_type="SchematicElectrical",
            item=change.identity,
            identity=change.identity,
            field=change.category,
            before=_as_json(change.before_json),
            after=_as_json(change.after_json),
            path=("electrical", change.category, change.identity),
        )
        for change in comparison.changes
    )


def changes_from_update(changes: Iterable[DesignUpdateChange]) -> tuple[DesignChange, ...]:
    """Translate the change list of a schematic-to-board or board-to-schematic update.

    The values of ``update_footprint`` and ``update_symbol`` are model JSON. Every
    other value is plain text, such as a net name or a library identifier, and
    becomes a JSON string. An update kind this table does not know becomes an
    ``other`` change.
    """
    result: list[DesignChange] = []
    for change in changes:
        category, kind = _UPDATE_KINDS.get(change.kind, ("other", "changed"))
        value = _as_json if change.kind in _JSON_UPDATE_KINDS else _text
        result.append(
            DesignChange(
                category=category,
                kind=kind,
                item_type="DesignUpdate",
                item=change.reference or change.identity,
                identity=change.identity,
                field=change.kind,
                before=value(change.before),
                after=value(change.after),
                path=("update", change.kind, change.identity),
            )
        )
    return tuple(result)


__all__ = ["changes_from_schematic_electrical", "changes_from_update"]
