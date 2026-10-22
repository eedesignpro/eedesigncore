"""Names of effective net classes for text, including required Default contributions."""

from __future__ import annotations

from collections.abc import Iterable

from eedesigncore.pro.models import ProjectFile
from eedesigncore.pro.tree import JsonValue
from eedesigncore.project.netclass_patterns import NetclassPattern
from eedesigncore.project.netclasses import _saved_patterns

_REQUIRED = (
    "clearance",
    "track_width",
    "via_diameter",
    "via_drill",
    "microvia_diameter",
    "microvia_drill",
    "diff_pair_width",
    "diff_pair_gap",
    "wire_width",
    "bus_width",
)


def names_for_nets(
    nets: Iterable[str], project: ProjectFile | None, labels: dict[str, set[str]] | None = None
) -> dict[str, str]:
    saved = project.saved_netclass_assignments if project else None
    patterns = []
    classes: dict[str, dict[str, JsonValue]] = {}
    if project:
        settings = project.section("net_settings")
        if settings and "netclass_patterns" in settings:
            patterns = [
                (NetclassPattern(pattern), name) for pattern, name in _saved_patterns(project)
            ]
        rows = project.value_at("net_settings.classes")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("name"), str):
                    classes[str(row["name"])] = row

    def priority(name: str) -> tuple[int, str]:
        value = classes.get(name, {}).get("priority")
        return (
            int(value)
            if isinstance(value, int | float)
            else 2147483647
            if name == "Default"
            else 2147483646,
            name,
        )

    result = {}
    for net in nets:
        assigned = set((saved or {}).get(net, ())) | (labels or {}).get(net, set())
        assigned.update(name for matcher, name in patterns if matcher.matches(net))
        if not net or not assigned:
            result[net] = "Default"
            continue
        ordered = sorted(assigned, key=priority)
        if "Default" not in ordered:
            missing = any(
                not any(
                    isinstance(classes.get(name, {}).get(key), int | float) for name in ordered
                )
                for key in _REQUIRED
            )
            tuning = classes.get("Default", {}).get("tuning_profile")
            if missing or (
                tuning and not any(classes.get(name, {}).get("tuning_profile") for name in ordered)
            ):
                ordered.append("Default")
        result[net] = ",".join(ordered)
    return result
