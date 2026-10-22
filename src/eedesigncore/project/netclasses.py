"""Read net class membership from a synchronized, saved board and project."""

from __future__ import annotations

from eedesigncore.connectivity.bus import parse_bus_group, parse_bus_vector
from eedesigncore.exceptions import KicadParseError
from eedesigncore.pcb import Board
from eedesigncore.pro import ProjectFile

_MAX_PATTERN_LENGTH = 4096
_MAX_EXPANDED_PATTERNS = 10000
_MAX_BUS_DEPTH = 32


def _expand_pattern(pattern: str, *, depth: int = 0) -> list[str]:
    if depth > _MAX_BUS_DEPTH:
        raise KicadParseError("Net class bus pattern exceeds the nesting limit")
    try:
        vector = parse_bus_vector(pattern, max_members=_MAX_EXPANDED_PATTERNS)
    except ValueError as exc:
        raise KicadParseError("Net class bus pattern exceeds the member limit") from exc
    if vector is not None:
        return vector.members
    group = parse_bus_group(pattern)
    if group is None:
        return [pattern]
    members: list[str] = []
    for member in group.members:
        members.extend(_expand_pattern(member, depth=depth + 1))
        if len(members) > _MAX_EXPANDED_PATTERNS:
            raise KicadParseError("Net class bus pattern exceeds the member limit")
    return members


def _saved_patterns(project: ProjectFile) -> list[tuple[str, str]]:
    settings = project.section("net_settings")
    if settings is None or "netclass_patterns" not in settings:
        raise KicadParseError("The project has no saved netclass_patterns field")
    rows = settings.get("netclass_patterns")
    if not isinstance(rows, list):
        raise KicadParseError("Expected a JSON array at 'net_settings.netclass_patterns'")
    patterns: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise KicadParseError(f"Expected a net class pattern object at row {index}")
        pattern, name = row.get("pattern"), row.get("netclass")
        if not isinstance(pattern, str) or not isinstance(name, str):
            raise KicadParseError(f"Expected pattern and netclass strings at row {index}")
        if len(pattern) > _MAX_PATTERN_LENGTH:
            raise KicadParseError(f"Net class pattern at row {index} exceeds the length limit")
        patterns.extend((member, name) for member in _expand_pattern(pattern))
        if len(patterns) > _MAX_EXPANDED_PATTERNS:
            raise KicadParseError("Net class patterns exceed the expanded pattern limit")
    return list(dict.fromkeys(patterns))


def read_netclass_assignments(board: Board, project: ProjectFile) -> dict[str, tuple[str, ...]]:
    """Return each board net's saved and pattern-assigned class names.

    Inputs must be synchronized and saved. These files cannot prove freshness.
    Missing assignment fields, malformed data, and matching limits raise
    KicadParseError. Names without class definitions remain in the result.
    Names are sorted, not ranked by class priority. Unassigned nets and the
    empty net name return Default. This does not calculate class properties,
    Default property contributions, bus display classes, or design-rule results.
    Neither input changes. Patterns compile once per call, without a stale cache.
    """
    saved = project.saved_netclass_assignments
    if saved is None:
        raise KicadParseError("The project has no saved netclass_assignments field")
    patterns = _saved_patterns(project)
    from eedesigncore.project.netclass_patterns import NetclassPattern

    compiled = [(NetclassPattern(pattern), name) for pattern, name in patterns]
    result: dict[str, tuple[str, ...]] = {}
    for net_name in sorted(board.net_names):
        if not net_name:
            result[net_name] = ("Default",)
            continue
        names = set(saved.get(net_name, ()))
        for matcher, name in compiled:
            if matcher.matches(net_name):
                names.add(name)
        result[net_name] = tuple(sorted(names)) if names else ("Default",)
    return result
