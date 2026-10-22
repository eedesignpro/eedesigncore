"""Plan and apply copper layer changes without file access or routing decisions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel

from eedesigncore.common.layers import CANONICAL_LAYER_NAME_SET, LAYER_WILDCARDS
from eedesigncore.common.pcb_family.board_items import Zone
from eedesigncore.common.pcb_family.footprint_scoped import (
    BackdrillInfo,
    Padstack,
    PadstackLayer,
    StackupLayer,
)
from eedesigncore.mod.models import Pad
from eedesigncore.pcb.models import (
    Board,
    BoardLayer,
    BoardStackup,
    Setup,
    Via,
    ViaPadstack,
    ViaPadstackLayer,
)

_SCALAR_FIELDS = {"layer", "layer_name", "start_layer", "end_layer"}
_SET_FIELDS = {"layers", "private_layers", "zone_layer_connections"}
_COPPER = re.compile(r"^(F|B|In\d+)\.Cu$")


@dataclass(frozen=True)
class CopperLayerReference:
    path: str
    layer: str
    owner: str | None


@dataclass(frozen=True)
class CopperLayerChangePlan:
    source_digest: str
    old_layers: tuple[str, ...]
    new_layers: tuple[str, ...]
    mapping: tuple[tuple[str, str | None], ...]
    references: tuple[CopperLayerReference, ...]
    blockers: tuple[str, ...]


def _digest(board: Board) -> str:
    data = json.dumps(board.model_dump(mode="json"), sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def _fields(
    value: object, path: str = "", owner: str | None = None
) -> Iterator[tuple[BaseModel, str, str, str | None]]:
    if isinstance(value, BaseModel):
        owner = getattr(value, "uuid", None) or getattr(value, "id", None) or owner
        for key in type(value).model_fields:
            if isinstance(value, Board) and key == "layers":
                continue
            if isinstance(value, Setup) and key in {"stackup", "pcbplotparams"}:
                continue
            if key == "filled_polygons":
                continue
            name = f"{path}.{key}" if path else key
            yield value, key, name, owner
            yield from _fields(getattr(value, key), name, owner)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            yield from _fields(child, f"{path}[{index}]", owner)
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _fields(child, f"{path}[{key}]", owner)


def inspect_copper_layer_usage(board: Board) -> tuple[CopperLayerReference, ...]:
    """Inventory native content references, excluding stackup and stale zone fills."""
    found = []
    for model, key, path, owner in _fields(board):
        value = getattr(model, key)
        if _scalar_field(model, key) and isinstance(value, str) and _copper_reference(value):
            found.append(CopperLayerReference(path, value, owner))
        elif key in _SET_FIELDS and isinstance(value, list | tuple):
            for index, layer in enumerate(value):
                if isinstance(layer, str) and _copper_reference(layer):
                    found.append(CopperLayerReference(f"{path}[{index}]", layer, owner))
    return tuple(found)


def _names(board: Board) -> tuple[str, ...]:
    names = {
        layer.canonical_name for layer in board.layers if _COPPER.fullmatch(layer.canonical_name)
    }
    return tuple(
        sorted(names, key=lambda n: -1 if n == "F.Cu" else 100 if n == "B.Cu" else int(n[2:-3]))
    )


def plan_copper_layer_change(
    board: Board, *, new_layers: Sequence[str], mapping: Mapping[str, str | None]
) -> CopperLayerChangePlan:
    """Require an explicit, order-preserving map. Occupied removed layers block apply."""
    old, new = _names(board), tuple(new_layers)
    canonical = ("F.Cu", *(f"In{i}.Cu" for i in range(1, len(new) - 1)), "B.Cu")
    if len(new) < 2 or len(new) > 32 or len(new) % 2 or new != canonical:
        raise ValueError("Use an even copper count from 2 through 32 in native order.")
    if set(mapping) != set(old):
        raise ValueError("Map every original copper layer exactly once.")
    targets = [mapping[layer] for layer in old if mapping[layer] is not None]
    if len(set(targets)) != len(targets) or any(layer not in new for layer in targets):
        raise ValueError("Keep mapped copper layers distinct and enabled.")
    if mapping.get("F.Cu") != "F.Cu" or mapping.get("B.Cu") != "B.Cu":
        raise ValueError("Keep the existing outer copper layers on the outer faces.")
    indices = [new.index(layer) for layer in targets if layer is not None]
    if indices != sorted(indices):
        raise ValueError("Keep the physical order of retained copper layers.")
    references = inspect_copper_layer_usage(board)
    blockers = list(validate_copper_layer_references(board)) + [
        f"{ref.path}: {ref.layer} contains content or a referenced endpoint."
        for ref in references
        if ref.layer in mapping and mapping[ref.layer] is None
    ]
    for index, via in enumerate(board.tracks):
        if not isinstance(via, Via):
            continue
        ends = [mapping.get(layer) for layer in via.layers]
        if len(ends) != 2 or any(layer not in new for layer in ends):
            continue
        first, last = (new.index(layer) for layer in ends if layer is not None)
        if first >= last or (via.via_type == "micro" and last - first != 1):
            blockers.append(
                f"tracks[{index}]: via endpoints require an explicit routing decision."
            )
    return CopperLayerChangePlan(
        _digest(board),
        old,
        new,
        tuple((name, mapping[name]) for name in old),
        references,
        tuple(blockers),
    )


def _scalar_field(model: BaseModel, key: str) -> bool:
    return key in _SCALAR_FIELDS or isinstance(model, StackupLayer) and key == "name"


def _copper_reference(value: str) -> bool:
    return value.endswith(".Cu") or value == "Inner"


def _canonical(count: int) -> tuple[str, ...]:
    return ("F.Cu", *(f"In{i}.Cu" for i in range(1, count - 1)), "B.Cu")


def _stackup_problems(stackup: BoardStackup, names: tuple[str, ...]) -> list[str]:
    problems = []
    copper = tuple(layer.name for layer in stackup.layers if layer.layer_type.lower() == "copper")
    if copper != names:
        problems.append("The stackup must match the planned copper order.")
    seen: set[str] = set()
    structural = []
    for index, layer in enumerate(stackup.layers):
        path = f"stackup.layers[{index}]"
        if layer.name in seen:
            problems.append(f"{path}: duplicate stackup layer name.")
        seen.add(layer.name)
        kind = layer.layer_type.lower()
        if kind in {"copper", "core", "prepreg"}:
            structural.append(kind == "copper")
            if not layer.sublayers or any(
                sub.thickness is None or sub.thickness <= 0 for sub in layer.sublayers
            ):
                problems.append(f"{path}: supply positive structural thickness.")
        for sub in layer.sublayers:
            if sub.thickness is not None and (
                not math.isfinite(sub.thickness) or sub.thickness < 0
            ):
                problems.append(f"{path}: supply finite, nonnegative thickness.")
        if _copper_reference(layer.name) and kind != "copper":
            problems.append(f"{path}: copper names require copper layers.")
    total = sum(sub.thickness or 0 for layer in stackup.layers for sub in layer.sublayers)
    if not math.isfinite(total):
        problems.append("The total stackup thickness must be finite.")
    if structural != [i % 2 == 0 for i in range(2 * len(names) - 1)]:
        problems.append("Alternate copper and dielectric layers in the stackup.")
    return problems


def validate_copper_layer_references(board: Board) -> tuple[str, ...]:
    """Report invalid board references and explicitly unsupported layer-bearing data."""
    names = _names(board)
    problems: list[str] = []
    if len(names) < 2 or len(names) > 32 or len(names) % 2 or names != _canonical(len(names)):
        problems.append("Use an even copper count from 2 through 32 in native order.")
    table_names = [layer.canonical_name for layer in board.layers]
    ordinals = [layer.ordinal for layer in board.layers]
    if len(set(table_names)) != len(table_names) or len(set(ordinals)) != len(ordinals):
        problems.append("The board layer table contains duplicate names or ordinals.")
    metadata = {layer.canonical_name: layer for layer in board.layers}
    modern = "B.Cu" in metadata and metadata["B.Cu"].ordinal == 2
    for index, name in enumerate(names):
        expected = (
            0
            if name == "F.Cu"
            else (2 if modern else 31)
            if name == "B.Cu"
            else 2 * (index + 1)
            if modern
            else index
        )
        if metadata[name].ordinal != expected:
            problems.append(f"layers: invalid ordinal for {name}.")
    for name in table_names:
        if name not in CANONICAL_LAYER_NAME_SET:
            problems.append(f"layers: unsupported layer name {name}.")
    for index, footprint in enumerate(board.footprints):
        if footprint.stackup is not None:
            problems.append(
                f"footprints[{index}].stackup: fixed stackup migration is unsupported."
            )
    if board.generators:
        problems.append("Generated board objects require a supported generator layer migration.")
    for model, key, path, _owner in _fields(board):
        value = getattr(model, key)
        if _scalar_field(model, key) and isinstance(value, str):
            candidates = [value]
        elif key in _SET_FIELDS and isinstance(value, list | tuple):
            candidates = [item for item in value if isinstance(item, str)]
        else:
            if "layer" in key.lower() and isinstance(value, str) and _copper_reference(value):
                problems.append(f"{path}: unsupported native layer reference.")
            continue
        for name in candidates:
            if name in LAYER_WILDCARDS and key in _SET_FIELDS:
                if name.endswith(".Cu") and not (isinstance(model, Pad) and key == "layers"):
                    problems.append(
                        f"{path}: copper wildcards outside pad layers are unsupported."
                    )
                continue
            if name == "Inner" and isinstance(model, PadstackLayer | ViaPadstackLayer):
                continue
            if not name and not isinstance(model, BackdrillInfo):
                continue
            if name not in CANONICAL_LAYER_NAME_SET:
                problems.append(f"{path}: unsupported layer name {name}.")
            elif _COPPER.fullmatch(name) and name not in names:
                problems.append(f"{path}: disabled copper layer {name}.")
    for model, key, path, _owner in _fields(board):
        value = getattr(model, key)
        if isinstance(value, BackdrillInfo) and (
            value.start_layer == value.end_layer
            or value.start_layer not in names
            or value.end_layer not in names
        ):
            problems.append(f"{path}: invalid secondary drill endpoints.")
        if isinstance(value, Padstack | ViaPadstack):
            entries = [entry.layer_name for entry in value.layers]
            if len(set(entries)) != len(entries):
                problems.append(f"{path}: duplicate padstack layer names.")
            allowed = (
                {"Inner", "B.Cu"} if value.mode == "front_inner_back" else set(names) - {"F.Cu"}
            )
            if any(name not in allowed for name in entries):
                problems.append(f"{path}: unsupported padstack layer or mode.")
    for index, via in enumerate(board.tracks):
        if not isinstance(via, Via):
            continue
        if len(via.layers) != 2 or any(layer not in names for layer in via.layers):
            problems.append(f"tracks[{index}]: invalid via endpoints.")
            continue
        first, last = (names.index(layer) for layer in via.layers)
        if first >= last or (via.via_type == "micro" and last - first != 1):
            problems.append(f"tracks[{index}]: invalid via endpoints.")
        if via.via_type == "through" and via.layers != ("F.Cu", "B.Cu"):
            problems.append(f"tracks[{index}]: through via must span the outer layers.")
    if board.setup is not None and board.setup.stackup is not None:
        problems.extend(_stackup_problems(board.setup.stackup, names))
    return tuple(dict.fromkeys(problems))


def _remap_mask(
    value: str, old: dict[str, int], new: dict[str, int], mapping: dict[str, str | None]
) -> str:
    bits = int(value.replace("_", ""), 16)
    if bits < 0 or bits >= 1 << 128:
        raise ValueError("Plot layer selections must fit an unsigned 128-bit mask.")
    result = bits
    for ordinal in old.values():
        result &= ~(1 << ordinal)
    for name, ordinal in old.items():
        target = mapping[name]
        if bits & (1 << ordinal) and target is not None:
            result |= 1 << new[target]
    width = max(32, len(value.removeprefix("0x").replace("_", "")))
    encoded = f"{result:0{width}x}"
    return "0x" + "_".join(encoded[i : i + 8] for i in range(0, len(encoded), 8))


def apply_copper_layer_change(
    board: Board, *, plan: CopperLayerChangePlan, stackup: BoardStackup
) -> Board:
    """Return a detached board. The caller supplies the complete resulting stackup."""
    if plan.source_digest != _digest(board):
        raise ValueError("The board changed after the layer plan was prepared.")
    verified = plan_copper_layer_change(
        board, new_layers=plan.new_layers, mapping=dict(plan.mapping)
    )
    if verified != plan or plan.blockers:
        raise ValueError("Resolve layer-change blockers before applying the plan.")
    stackup_problems = _stackup_problems(stackup, plan.new_layers)
    if stackup_problems:
        raise ValueError(" ".join(stackup_problems))
    result = deepcopy(board)
    if result.setup is None or result.general is None:
        raise ValueError("Supply native board setup and general settings before changing layers.")
    mapping = dict(plan.mapping)
    for model, key, _path, _owner in _fields(result):
        value = getattr(model, key)
        if _scalar_field(model, key) and isinstance(value, str) and value in mapping:
            setattr(model, key, mapping[value])
        elif key in _SET_FIELDS and isinstance(value, list | tuple):
            if all(isinstance(layer, str) for layer in value):
                mapped = [mapping.get(layer, layer) for layer in value]
                setattr(model, key, tuple(mapped) if isinstance(value, tuple) else mapped)
        elif key == "fill" and isinstance(model, Zone):
            model.filled_polygons = []
            model.fill.filled = False
    metadata = {layer.canonical_name: layer for layer in board.layers}
    modern = metadata["B.Cu"].ordinal == 2
    inverse = {target: source for source, target in mapping.items() if target is not None}
    copper = []
    for index, name in enumerate(plan.new_layers):
        layer = (
            deepcopy(metadata[inverse[name]])
            if name in inverse
            else BoardLayer(canonical_name=name)
        )
        layer.canonical_name = name
        layer.ordinal = (
            0
            if name == "F.Cu"
            else (2 if modern else 31)
            if name == "B.Cu"
            else 2 * (index + 1)
            if modern
            else index
        )
        copper.append(layer)
    result.layers = copper + [
        deepcopy(layer) for layer in board.layers if layer.canonical_name not in plan.old_layers
    ]
    plot = result.setup.pcbplotparams
    if plot is not None:
        old_ids = {name: metadata[name].ordinal for name in plan.old_layers}
        new_ids = {layer.canonical_name: layer.ordinal for layer in copper}
        for key in ("layerselection", "plot_on_all_layers_selection"):
            setattr(plot, key, _remap_mask(getattr(plot, key), old_ids, new_ids, mapping))
    result.setup.stackup = deepcopy(stackup)
    result.general.thickness = sum(
        sub.thickness or 0
        for layer in stackup.layers
        if layer.layer_type.lower() in {"copper", "core", "prepreg"}
        or "solder mask" in layer.layer_type.lower()
        for sub in layer.sublayers
    )
    problems = validate_copper_layer_references(result)
    if problems:
        raise ValueError(" ".join(problems))
    return result
