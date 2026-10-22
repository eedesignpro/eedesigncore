"""Compare two SVG documents by geometry, not by pixels.

This module reads an SVG back into a multiset of primitives and compares two
such multisets inside a tolerance. It exists to check this renderer against
``kicad-cli`` without rasterizing anything.

Rasterizing hides the answer. Two documents that disagree by one micron produce
the same picture at any sane resolution, and two that agree perfectly can differ
in a raster through anti-aliasing alone. A numeric comparison says which
primitive moved and by how far.

The comparison is deliberately blind to three things that carry no geometry:

1. **Element decomposition.** This renderer draws a rectangle as one closed
   path. KiCad's board plot path draws it as four thick segments. Both give the
   same four segments after normalization.
2. **Direction.** A segment from A to B equals one from B to A. An arc from A to
   B with sweep 0 is the arc from B to A with sweep 1. Both fold to one form.
3. **Order.** Paint order fidelity is out of scope (ADR-098), so the comparison
   works on multisets.

It is NOT blind to position, radius, or stroke width. Those are geometry.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from eedesigncore.render.geometry import Point

#: Default agreement, in millimeters. One micron is ten times the 0.1 micron
#: resolution that a 4-place millimeter value can carry, so a rounding
#: difference in the last written digit never fails a comparison.
DEFAULT_TOLERANCE_MM = 0.001

_ELEMENT_RE = re.compile(r"<(path|circle|rect)\b([^>]*)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_COMMAND_RE = re.compile(r"([MmLlHhVvAaCcQqSsTtZz])")

#: One primitive: a kind, then the numbers that place it.
Primitive = tuple[str | int | float, ...]


@dataclass
class Difference:
    """What one comparison found."""

    matched: int = 0
    only_in_left: list[Primitive] = field(default_factory=list)
    only_in_right: list[Primitive] = field(default_factory=list)

    @property
    def agrees(self) -> bool:
        """True when neither side holds a primitive the other lacks."""
        return not self.only_in_left and not self.only_in_right

    def summary(self) -> str:
        """Return one line describing the result."""
        if self.agrees:
            return f"agree: {self.matched} primitives matched"
        return (
            f"differ: {self.matched} matched, "
            f"{len(self.only_in_left)} only on the left, "
            f"{len(self.only_in_right)} only on the right"
        )


def _quantize(value: float, tolerance: float) -> float:
    """Snap *value* to the tolerance grid, so near values compare equal."""
    snapped = round(value / tolerance) * tolerance
    return snapped + 0.0


def _segment(start: Point, end: Point, tolerance: float) -> Primitive:
    """Return a segment with its endpoints in a fixed order."""
    a = (_quantize(start[0], tolerance), _quantize(start[1], tolerance))
    b = (_quantize(end[0], tolerance), _quantize(end[1], tolerance))
    low, high = sorted((a, b))
    return ("seg", low[0], low[1], high[0], high[1])


def _arc(start: Point, end: Point, radius: float, sweep: int, tolerance: float) -> Primitive:
    """Return an arc folded to one direction.

    An arc from A to B with sweep 0 draws the curve that an arc from B to A with
    sweep 1 draws. Folding both to sweep 1 lets this renderer and KiCad agree
    even though they order the endpoints differently.
    """
    a = (_quantize(start[0], tolerance), _quantize(start[1], tolerance))
    b = (_quantize(end[0], tolerance), _quantize(end[1], tolerance))
    if not sweep:
        a, b = b, a
    return ("arc", a[0], a[1], b[0], b[1], _quantize(radius, tolerance))


def _circle(center: Point, radius: float, tolerance: float) -> Primitive:
    return (
        "circle",
        _quantize(center[0], tolerance),
        _quantize(center[1], tolerance),
        _quantize(radius, tolerance),
    )


def _numbers(text: str) -> list[float]:
    return [float(match) for match in _NUMBER_RE.findall(text)]


def _split_commands(data: str) -> list[tuple[str, list[float]]]:
    """Split path data into command letters and their numbers."""
    parts = _COMMAND_RE.split(data)
    commands: list[tuple[str, list[float]]] = []
    index = 1
    while index < len(parts):
        letter = parts[index]
        payload = parts[index + 1] if index + 1 < len(parts) else ""
        commands.append((letter, _numbers(payload)))
        index += 2
    return commands


def _walk_path(data: str, tolerance: float) -> list[Primitive]:  # noqa: C901
    """Decompose one ``d`` attribute into segments and arcs.

    Relative commands, horizontal and vertical shorthands, and closing are all
    handled. Bezier commands are read and skipped, because this renderer does not
    emit them yet (BS-15-5) and a silent misread would be worse than a gap.
    """
    primitives: list[Primitive] = []
    current: Point = (0.0, 0.0)
    start_of_subpath: Point = (0.0, 0.0)

    for letter, values in _split_commands(data):
        upper = letter.upper()
        relative = letter.islower()

        if upper == "Z":
            if current != start_of_subpath:
                primitives.append(_segment(current, start_of_subpath, tolerance))
            current = start_of_subpath
            continue

        if upper in ("M", "L", "T"):
            for index in range(0, len(values) - 1, 2):
                target = (values[index], values[index + 1])
                if relative:
                    target = (current[0] + target[0], current[1] + target[1])
                if upper == "M" and index == 0:
                    start_of_subpath = target
                else:
                    primitives.append(_segment(current, target, tolerance))
                current = target
            continue

        if upper in ("H", "V"):
            for value in values:
                if upper == "H":
                    target = (current[0] + value if relative else value, current[1])
                else:
                    target = (current[0], current[1] + value if relative else value)
                primitives.append(_segment(current, target, tolerance))
                current = target
            continue

        if upper == "A":
            for index in range(0, len(values) - 6, 7):
                radius = values[index]
                sweep = int(values[index + 4])
                target = (values[index + 5], values[index + 6])
                if relative:
                    target = (current[0] + target[0], current[1] + target[1])
                primitives.append(_arc(current, target, radius, sweep, tolerance))
                current = target
            continue

        # A Bezier command moves the pen but contributes no primitive yet.
        stride = {"C": 6, "S": 4, "Q": 4}.get(upper, 0)
        if stride:
            for index in range(0, len(values) - stride + 1, stride):
                target = (values[index + stride - 2], values[index + stride - 1])
                if relative:
                    target = (current[0] + target[0], current[1] + target[1])
                current = target

    return primitives


def read_primitives(svg: str, tolerance: float = DEFAULT_TOLERANCE_MM) -> Counter[Primitive]:
    """Return the geometric primitives of *svg*, as a multiset.

    A ``rect`` element becomes four segments, so it compares equal to the closed
    path this renderer emits and to the four thick segments KiCad emits.
    """
    found: Counter[Primitive] = Counter()

    for match in _ELEMENT_RE.finditer(svg):
        tag = match.group(1).lower()
        attrs = dict(_ATTR_RE.findall(match.group(2)))

        if tag == "path" and "d" in attrs:
            found.update(_walk_path(attrs["d"], tolerance))
        elif tag == "circle":
            center = (float(attrs.get("cx", 0.0)), float(attrs.get("cy", 0.0)))
            found[_circle(center, float(attrs.get("r", 0.0)), tolerance)] += 1
        elif tag == "rect":
            x, y = float(attrs.get("x", 0.0)), float(attrs.get("y", 0.0))
            width, height = float(attrs.get("width", 0.0)), float(attrs.get("height", 0.0))
            corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
            for index in range(4):
                found[_segment(corners[index], corners[(index + 1) % 4], tolerance)] += 1

    return found


def _within(left: Primitive, right: Primitive, limit: float) -> bool:
    """True when two primitives of one kind differ by no more than *limit*."""
    if left[0] != right[0] or len(left) != len(right):
        return False
    return all(abs(float(a) - float(b)) <= limit for a, b in zip(left[1:], right[1:], strict=True))


def _reconcile(
    stray_left: list[Primitive], stray_right: list[Primitive], tolerance: float
) -> tuple[int, list[Primitive], list[Primitive]]:
    """Pair leftovers that sit on opposite sides of the tolerance grid.

    Snapping to a grid puts a value of 252.6495 on one side for one document and
    the other side for the other, even though the two agree to a thousandth of a
    millimeter. A real board hit exactly that. Without this pass the comparison
    reports a defect that is an artifact of its own rounding.

    The pass is greedy, which is enough: two primitives this close never compete
    for a third partner.
    """
    limit = tolerance * 2.0
    taken: set[int] = set()
    matched = 0
    unmatched_left: list[Primitive] = []

    for candidate in stray_left:
        partner = next(
            (
                index
                for index, other in enumerate(stray_right)
                if index not in taken and _within(candidate, other, limit)
            ),
            None,
        )
        if partner is None:
            unmatched_left.append(candidate)
        else:
            taken.add(partner)
            matched += 1

    unmatched_right = [item for index, item in enumerate(stray_right) if index not in taken]
    return matched, unmatched_left, unmatched_right


def compare_svg(left: str, right: str, tolerance: float = DEFAULT_TOLERANCE_MM) -> Difference:
    """Compare two SVG documents by geometry.

    :param left: the first document.
    :param right: the second document.
    :param tolerance: agreement in millimeters. See
        :data:`DEFAULT_TOLERANCE_MM`. A pair that survives the grid is still
        paired when it agrees to twice this value, so the effective agreement is
        ``2 * tolerance``.
    """
    first = read_primitives(left, tolerance)
    second = read_primitives(right, tolerance)

    shared = first & second
    near, stray_left, stray_right = _reconcile(
        sorted((first - second).elements(), key=repr),
        sorted((second - first).elements(), key=repr),
        tolerance,
    )

    return Difference(
        matched=sum(shared.values()) + near,
        only_in_left=stray_left,
        only_in_right=stray_right,
    )


def nearest_gap(primitive: Primitive, candidates: Counter[Primitive]) -> float | None:
    """Return the distance from *primitive* to its closest same-kind candidate.

    A caller uses this to tell a real disagreement from a value that fell on the
    wrong side of the tolerance grid.
    """
    kind = primitive[0]
    numbers = [float(value) for value in primitive[1:]]
    best: float | None = None

    for other in candidates:
        if other[0] != kind or len(other) != len(primitive):
            continue
        gap = math.dist(numbers, [float(value) for value in other[1:]])
        if best is None or gap < best:
            best = gap

    return best


__all__ = [
    "DEFAULT_TOLERANCE_MM",
    "Difference",
    "Primitive",
    "Stadium",
    "compare_holes",
    "compare_svg",
    "nearest_gap",
    "read_holes",
    "read_primitives",
]


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------

_GROUP_OPEN_RE = re.compile(r"<g\b([^>]*)>", re.IGNORECASE)
_GROUP_CLOSE_RE = re.compile(r"</g\s*>", re.IGNORECASE)
_STROKE_WIDTH_RE = re.compile(r"stroke-width\s*:\s*([-+0-9.eE]+)")

#: One hole: two cap centers and a radius. A round hole has both centers equal.
Stadium = tuple[str, float, float, float, float, float]


def _stadium(first: Point, second: Point, radius: float, tolerance: float) -> Stadium:
    """Return a hole with its two cap centers in a fixed order."""
    low, high = sorted(
        (
            (_quantize(first[0], tolerance), _quantize(first[1], tolerance)),
            (_quantize(second[0], tolerance), _quantize(second[1], tolerance)),
        )
    )
    return ("stadium", low[0], low[1], high[0], high[1], _quantize(radius, tolerance))


def _stroke_width_of(attrs: str) -> float | None:
    found = _STROKE_WIDTH_RE.search(attrs)
    return float(found.group(1)) if found else None


def _path_stadium(data: str, stroke_width: float | None, tolerance: float) -> Stadium | None:
    """Recognise a hole in either of the two shapes a plotter writes for one.

    KiCad draws a slot as one thick round-capped SEGMENT: the centre line, with
    the stroke width carrying the slot width. This renderer draws the OUTLINE
    instead, as two straight sides and two true arc caps, so that every hole in
    the document is one solid area a caller styles one way.

    Both are the same stadium. This function reduces either to two cap centres
    and a radius, so the two can be compared. Nothing else here can, because a
    primitive comparison sees a segment on one side and four pieces on the other.
    """
    commands = _split_commands(data)
    kinds = [letter.upper() for letter, _ in commands]

    if kinds == ["M", "L"] and stroke_width:
        start = (commands[0][1][0], commands[0][1][1])
        end = (commands[1][1][0], commands[1][1][1])
        return _stadium(start, end, stroke_width / 2.0, tolerance)

    if kinds == ["M", "L", "A", "L", "A", "Z"]:
        caps: list[Point] = []
        radius = 0.0
        current = (commands[0][1][0], commands[0][1][1])
        for letter, values in commands:
            upper = letter.upper()
            if upper == "M" or upper == "L":
                current = (values[0], values[1])
            elif upper == "A":
                radius = values[0]
                end = (values[5], values[6])
                caps.append(((current[0] + end[0]) / 2.0, (current[1] + end[1]) / 2.0))
                current = end
        if len(caps) == 2:
            return _stadium(caps[0], caps[1], radius, tolerance)

    return None


def read_holes(svg: str, tolerance: float = DEFAULT_TOLERANCE_MM) -> Counter[Stadium]:
    """Return the holes of *svg*, as a multiset of cap centres and radii.

    A ``circle`` element is a hole whose two cap centres coincide. A ``path`` is a
    hole when it reduces to a stadium.

    Read against a full copper export this returns MORE than the holes, because
    an oval pad and a track segment are stadiums too, and nothing in the geometry
    says which is a hole. The useful assertion over a copper export is therefore
    one-way: every hole the renderer draws must appear on the other side. A count
    that matches exactly means the other document holds holes and nothing else.

    A stroke width may sit on the element or on an enclosing group. KiCad puts it
    on the group, so the group stack is tracked rather than assumed away.
    """
    found: Counter[Stadium] = Counter()
    group_widths: list[float | None] = []

    events = sorted(
        [(m.start(), "g", m) for m in _GROUP_OPEN_RE.finditer(svg)]
        + [(m.start(), "/g", m) for m in _GROUP_CLOSE_RE.finditer(svg)]
        + [(m.start(), "el", m) for m in _ELEMENT_RE.finditer(svg)]
    )

    for _position, kind, match in events:
        if kind == "g":
            group_widths.append(_stroke_width_of(match.group(1)))
            continue
        if kind == "/g":
            if group_widths:
                group_widths.pop()
            continue

        tag = match.group(1).lower()
        attrs_text = match.group(2)
        attrs = dict(_ATTR_RE.findall(attrs_text))

        if tag == "circle":
            center = (float(attrs.get("cx", 0.0)), float(attrs.get("cy", 0.0)))
            found[_stadium(center, center, float(attrs.get("r", 0.0)), tolerance)] += 1
        elif tag == "path" and "d" in attrs:
            width = _stroke_width_of(attrs_text)
            if width is None:
                width = next((w for w in reversed(group_widths) if w is not None), None)
            if attrs.get("stroke-width"):
                width = float(attrs["stroke-width"])
            stadium = _path_stadium(attrs["d"], width, tolerance)
            if stadium is not None:
                found[stadium] += 1

    return found


def compare_holes(left: str, right: str, tolerance: float = DEFAULT_TOLERANCE_MM) -> Difference:
    """Compare the holes of two SVG documents, whichever shape each one uses."""
    first = read_holes(left, tolerance)
    second = read_holes(right, tolerance)
    shared = first & second
    near, stray_left, stray_right = _reconcile(
        sorted((first - second).elements(), key=repr),
        sorted((second - first).elements(), key=repr),
        tolerance,
    )
    return Difference(
        matched=sum(shared.values()) + near,
        only_in_left=stray_left,
        only_in_right=stray_right,
    )
