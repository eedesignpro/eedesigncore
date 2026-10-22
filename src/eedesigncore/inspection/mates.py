"""Connector mating hypotheses from stored electrical and placement evidence."""

from __future__ import annotations

import math
from typing import Protocol, cast

from eedesigncore.inspection.portable import (
    DesignInspector,
    Json,
    Object,
    _array,
    _number,
    _object,
    _text,
)


class _ConnectorReader(Protocol):
    _comparison_names: dict[tuple[int, str], str]

    def _board(self, instance: str) -> DesignInspector: ...
    def _pins(self, endpoint: tuple[str, str]) -> dict[str, list[str]]: ...


def _pin_order(number: str) -> tuple[int, int, str]:
    return (
        (0, int(number), number)
        if number.isascii() and number.isdecimal() and len(number) < 8
        else (1, 0, number)
    )


def _signal_name(name: str) -> str:
    leaf = name.rsplit("/", 1)[-1].casefold()
    if leaf.startswith(("unconnected-", "net-(")):
        return ""
    return leaf


def _profile(reader: _ConnectorReader, endpoint: tuple[str, str]) -> Object:
    instance, component = endpoint
    board = reader._board(instance)
    pins = reader._pins(endpoint)
    if len(pins) > 4096:
        raise ValueError("Connector comparison exceeds the pin limit.")
    rows: Object = {}
    text_work = 0
    for number, identifiers in pins.items():
        if len(identifiers) != 1:
            raise ValueError("Connector comparison requires unambiguous pin numbers.")
        pin = identifiers[0]
        names: list[str] = []
        comparison_names: list[str] = []
        for net in board._links(pin, "nets"):
            name = _text(board._data(net).get("name", ""))
            text_work += len(name)
            if text_work > 262144:
                raise ValueError("Connector name comparison exceeds the text-work limit.")
            names.append(name)
            cache_key = id(board), net
            if cache_key not in reader._comparison_names:
                reader._comparison_names[cache_key] = _signal_name(name)
            comparison_names.append(reader._comparison_names[cache_key])
        pads = board._links(pin, "pads")
        positions: list[Json] = []
        for pad in pads:
            if board._records[pad]["kind"] != "pad":
                raise ValueError("Connector pad evidence must refer to a pad record.")
            value = board._data(pad).get("board_position")
            if value is not None:
                point = _array(value, "pad position")
                if len(point) != 2 or any(abs(_number(axis)) > 1e9 for axis in point):
                    raise ValueError("Connector pad position requires bounded XY coordinates.")
            positions.append(value)
        position = positions[0] if len(positions) == 1 else None
        rows[number] = {
            "pin_id": pin,
            "net_names": cast(Json, names),
            "comparison_names": cast(Json, comparison_names),
            "position": position,
        }
    data = board._data(component)
    return {
        "instance": instance,
        "component_id": component,
        "reference": data.get("reference"),
        "footprint": data.get("footprint"),
        "side": data.get("side"),
        "placement": data.get("placement"),
        "pins": rows,
        "_text_work": text_work,
    }


def _fit_positions(pairs: list[tuple[list[float], list[float]]]) -> Object | None:
    if len(pairs) < 2:
        return None
    count = len(pairs)
    left_center = [sum(a[axis] for a, _ in pairs) / count for axis in (0, 1)]
    right_center = [sum(b[axis] for _, b in pairs) / count for axis in (0, 1)]
    dot = cross = 0.0
    for a, b in pairs:
        x, y = b[0] - right_center[0], b[1] - right_center[1]
        u, v = a[0] - left_center[0], a[1] - left_center[1]
        dot += x * u + y * v
        cross += x * v - y * u
    if math.hypot(dot, cross) < 1e-12:
        return None
    angle = math.atan2(cross, dot)
    cosine, sine = math.cos(angle), math.sin(angle)
    offset = [
        left_center[0] - cosine * right_center[0] + sine * right_center[1],
        left_center[1] - sine * right_center[0] - cosine * right_center[1],
    ]
    residual = max(
        math.hypot(
            cosine * b[0] - sine * b[1] + offset[0] - a[0],
            sine * b[0] + cosine * b[1] + offset[1] - a[1],
        )
        for a, b in pairs
    )
    return {
        "direction": "right board coordinates to left board coordinates",
        "rotation_degrees": math.degrees(angle),
        "translation_mm": cast(Json, offset),
        "maximum_residual_mm": residual,
        "paired_pad_count": count,
        "meaning": (
            "Rigid planar fit only. Mating height and mechanical compatibility are not evaluated."
        ),
    }


def _compare_profiles(left: Object, right: Object, mapping: str) -> Object:
    left_pins, right_pins = _object(left["pins"]), _object(right["pins"])
    numbers = sorted(left_pins, key=_pin_order)
    if not numbers or set(numbers) != set(right_pins):
        raise ValueError("These connector hypotheses require identical nonempty pin-number sets.")
    if mapping == "same_number":
        targets = numbers
    elif mapping in ("reverse_numeric", "swap_pairs"):
        if set(numbers) != {str(n) for n in range(1, len(numbers) + 1)}:
            raise ValueError("Numeric mapping requires contiguous pin numbers starting at one.")
        if mapping == "swap_pairs" and len(numbers) % 2:
            raise ValueError("Pair swapping requires an even pin count.")
        targets = [
            str(len(numbers) + 1 - int(number))
            if mapping == "reverse_numeric"
            else str(int(number) + 1 if int(number) % 2 else int(number) - 1)
            for number in numbers
        ]
    else:
        raise ValueError("Unknown connector mapping hypothesis.")
    pairs: list[Json] = []
    positions: list[tuple[list[float], list[float]]] = []
    informative = common = differences = 0
    for number, target in zip(numbers, targets, strict=True):
        a, b = _object(left_pins[number]), _object(right_pins[target])
        left_names = {_text(name) for name in _array(a["comparison_names"])} - {""}
        right_names = {_text(name) for name in _array(b["comparison_names"])} - {""}
        shared = left_names & right_names
        informative += bool(shared - {"gnd", "earth", "vcc", "vdd", "vss"})
        common += bool(shared & {"gnd", "earth", "vcc", "vdd", "vss"})
        differences += bool(left_names and right_names and not shared)
        pairs.append(
            {
                "left": number,
                "right": target,
                "left_pin_id": a["pin_id"],
                "right_pin_id": b["pin_id"],
                "left_net_names": a["net_names"],
                "right_net_names": b["net_names"],
                "matching_leaf_names": cast(Json, sorted(shared)),
            }
        )
        if a["position"] is not None and b["position"] is not None:
            position_a, position_b = _array(a["position"]), _array(b["position"])
            if len(position_a) == len(position_b) == 2:
                positions.append(
                    ([_number(v) for v in position_a], [_number(v) for v in position_b])
                )
    return {
        "left": {
            key: value for key, value in left.items() if key != "pins" and not key.startswith("_")
        },
        "right": {
            key: value for key, value in right.items() if key != "pins" and not key.startswith("_")
        },
        "mapping": mapping,
        "derivation": "inferred",
        "pin_count": len(pairs),
        "named_signal_matches": informative,
        "common_name_matches": common,
        "different_named_pairs": differences,
        "coordinate_fit": _fit_positions(positions),
        "pairs": pairs,
        "meaning": (
            "A comparison hypothesis, not an established mate. "
            "Names can differ across a real connection."
        ),
    }
