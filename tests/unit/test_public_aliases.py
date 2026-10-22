"""The named aliases the root list exports (R4, iteration 11.11).

A union alias and a ``Literal`` alias are what the type stub SHOWS a consumer,
and the reachability walk in ``test_export_surface.py`` cannot collect either,
because neither is a runtime type. The rule that governs the list is therefore
not a walk but a grep, and ADR-085 records it:

    An alias is exported when a public field annotation of a root-exported
    model spells it.

No test can enforce that rule, because an alias leaves no runtime trace on the
model it annotates. This module does the next-best two things. It pins each
alias in an annotation position, so ``mypy --strict`` proves the name is usable
as a type. And it pins each alias in the root ``__all__``, so a silent removal
fails the build.
"""

from __future__ import annotations

import pytest

import eedesigncore
from eedesigncore import (
    GeneratorValue,
    GraphicShape,
    JsonValue,
    JumperPadGroups,
    LayerTypeToken,
    LibTableKind,
    LibTableScope,
    PadPrimitive,
    PcbDrawItem,
    PtsElement,
    SymbolItem,
    TrackItem,
)

# Every alias the grep of R4 found spelled in a public field annotation, plus
# the two the plan named that no field spells (see ADR-085).
ALIASES = (
    "SymbolItem",
    "PadPrimitive",
    "GeneratorValue",
    "TrackItem",
    "PcbDrawItem",
    "GraphicShape",
    "PtsElement",
    "LayerTypeToken",
    "JsonValue",
    "LibTableKind",
    "LibTableScope",
    "JumperPadGroups",
)


def test_every_alias_sits_in_the_root_all() -> None:
    missing = [name for name in ALIASES if name not in eedesigncore.__all__]
    assert missing == [], f"an exported alias left the root list: {missing}"


@pytest.mark.parametrize("name", ALIASES)
def test_every_alias_resolves_from_the_package_root(name: str) -> None:
    assert getattr(eedesigncore, name) is not None


def test_every_alias_is_usable_in_an_annotation_position() -> None:
    """``mypy --strict`` is the real assertion here. The runtime check only
    proves the annotations were evaluated."""
    symbol_item: SymbolItem | None = None
    pad_primitive: PadPrimitive | None = None
    generator_value: GeneratorValue | None = None
    track_item: TrackItem | None = None
    draw_item: PcbDrawItem | None = None
    graphic_shape: GraphicShape | None = None
    pts_element: PtsElement | None = None
    layer_type: LayerTypeToken = "signal"
    json_value: JsonValue = None
    table_kind: LibTableKind = "fp"
    table_scope: LibTableScope = "global"
    jumper_groups: JumperPadGroups = []

    assert symbol_item is None
    assert pad_primitive is None
    assert generator_value is None
    assert track_item is None
    assert draw_item is None
    assert graphic_shape is None
    assert pts_element is None
    assert layer_type == "signal"
    assert json_value is None
    assert table_kind == "fp"
    assert table_scope == "global"
    assert jumper_groups == []
