"""The root ``__all__`` is closed under reachability (R5, iteration 11.11).

**This guard SUPERSEDES the iteration-11.10 enum guard.** That guard collected
enums only, and it accepted a sub-package ``__all__`` as sufficient. Both
concessions existed for one reason: closing the ROOT list moved the support
boundary, and iteration 11.10 was not allowed to move it (ADR-083 item 6).
Iteration 11.11 moves it, so the concessions go. ADR-085 records the
supersession.

The rule, in the operator's terms: **if a public field or a public return can
hand a caller an object, the type of that object is public.** Public means the
package root ``__all__``, because ``docs/agent/README.md § Support boundaries``
defines the supported API as that list and nothing else.

The new rule strictly implies the old one. Every enum the 11.10 guard collected
is still collected, and the demand on it is now stronger, not weaker.

The walk, stated as four properties rather than as a name list:

1. Roots are every class in the root ``__all__``, plus the return annotation of
   every function in the root ``__all__``.
2. The walk crosses model fields, the return annotation of every public method,
   and the return annotation of every property. It resolves unions, optionals,
   lists, tuples, dicts, ``Annotated``, and the two declared bridges.
3. It collects a Pydantic model or an ``enum.Enum`` subclass defined inside
   ``eedesigncore``. A stdlib type, a third-party type, and a bare ``Literal``
   value are not collected.
4. A name that starts with an underscore is excluded, and so is a type whose
   module path carries a component that starts with an underscore. That is the
   ONLY written exclusion.

Everything else falls out of the walk by itself, and this module excludes
nothing by hand. ``TestTheWalkCrossesNothingItShouldNot`` turns the two
interesting negative answers into assertions, so a future annotation that opens
either door fails the build and forces a ruling.
"""

from __future__ import annotations

import enum
import importlib
import inspect
import pkgutil
import types
from dataclasses import dataclass, field
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import BaseModel, create_model

import eedesigncore
from eedesigncore.common.pcb_family.footprint_scoped import PadstackLayer
from eedesigncore.common.sch_family.symbol import SymbolItem, SymbolUnit
from eedesigncore.mod.models import PadPrimitive

PACKAGE = eedesigncore.__name__

# A field annotated ``Any`` hands out objects that no annotation names, so the
# walk cannot see through it. ``src/`` holds exactly two, and each entry below
# cites the line that declares it. Neither annotation may change: widening
# ``list[Any]`` to a union changes Pydantic validation, and the closure
# iteration changes no behavior (ADR-085).
BRIDGES: dict[tuple[type[BaseModel], str], object] = {
    # src/eedesigncore/common/sch_family/symbol.py:133
    (SymbolUnit, "items"): SymbolItem,
    # src/eedesigncore/common/pcb_family/footprint_scoped.py:85
    (PadstackLayer, "primitives"): PadPrimitive,
}


@dataclass
class WalkResult:
    """What one walk saw: the types, the ``Any`` fields, and the skipped members."""

    found: dict[type, tuple[str, str]] = field(default_factory=dict)
    any_fields: list[tuple[type[BaseModel], str]] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)


def _defined_in_package(obj: object) -> bool:
    module = getattr(obj, "__module__", "")
    return module == PACKAGE or module.startswith(f"{PACKAGE}.")


def _private_module_path(module: str) -> bool:
    return any(part.startswith("_") for part in module.split("."))


def _contains_any(annotation: object) -> bool:
    if annotation is Any:
        return True
    return any(_contains_any(argument) for argument in get_args(annotation))


def _declared_here(owner: type, name: str) -> bool:
    """True when *name* is declared by a class this package defines.

    Pydantic hangs ``model_fields`` and ``model_computed_fields`` on every
    model through ``BaseModel``. Those are framework members, not members this
    library owns, so they must not enter the skip list (LOW-33).
    """
    for klass in owner.__mro__:
        if name in vars(klass):
            return klass.__module__.startswith("eedesigncore")
    return False


def _public_returns(owner: type, skipped: list[tuple[str, str, str]]) -> list[tuple[str, object]]:
    """The return annotation of every public method and property of *owner*."""
    returns: list[tuple[str, object]] = []
    for name in dir(owner):
        if name.startswith("_"):
            continue
        try:
            raw = inspect.getattr_static(owner, name)
        except AttributeError:  # pragma: no cover - a dynamic attribute has no static form
            continue
        function: object | None = None
        if isinstance(raw, property):
            function = raw.fget
        elif isinstance(raw, staticmethod | classmethod):
            function = raw.__func__
        elif inspect.isfunction(raw):
            function = raw
        if isinstance(raw, types.MemberDescriptorType) and _declared_here(owner, name):
            # A __slots__ member hands a caller an object too. Its type comes
            # from the owner's annotations, not from a return annotation.
            slot = get_type_hints(owner, include_extras=False).get(name)
            if slot is not None:
                returns.append((name, slot))
            continue
        if function is None and hasattr(raw, "__get__") and _declared_here(owner, name):
            # LOW-33. A descriptor of an unrecognized kind, for example
            # functools.cached_property or a pydantic computed_field, would
            # otherwise leave the walk silently short of a reachable type.
            # Only a descriptor this package DECLARES counts. Pydantic gives
            # every model an inherited model_fields and model_computed_fields,
            # and neither is a member this library owns.
            skipped.append((owner.__name__, name, f"unrecognized descriptor {type(raw)!r}"))
            continue
        if function is None or not _defined_in_package(function):
            continue
        try:
            hints = get_type_hints(function, include_extras=False)
        except Exception as error:  # pragma: no cover - recorded, never swallowed
            skipped.append((owner.__name__, name, repr(error)))
            continue
        if "return" in hints:
            returns.append((name, hints["return"]))
    return returns


def walk(
    roots: list[object],
    bridges: dict[tuple[type[BaseModel], str], object] | None = None,
) -> WalkResult:
    """Collect every type a caller can reach from *roots*."""
    table = BRIDGES if bridges is None else bridges
    result = WalkResult()
    seen: set[object] = set()
    queue: list[tuple[object, str, str]] = [(root, "<root>", "") for root in roots]

    while queue:
        annotation, owner, member = queue.pop()
        try:
            if annotation in seen:
                continue
            seen.add(annotation)
        except TypeError:  # pragma: no cover - an unhashable annotation is walked again
            pass

        if not isinstance(annotation, type):
            for argument in get_args(annotation):
                queue.append((argument, owner, member))
            continue

        if not _defined_in_package(annotation):
            continue
        if annotation.__name__.startswith("_") or _private_module_path(annotation.__module__):
            continue

        if issubclass(annotation, enum.Enum):
            result.found.setdefault(annotation, (owner, member))
            continue

        if issubclass(annotation, BaseModel):
            result.found.setdefault(annotation, (owner, member))
            for name, info in annotation.model_fields.items():
                if name.startswith("_"):
                    continue
                if _contains_any(info.annotation):
                    result.any_fields.append((annotation, name))
                    bridged = table.get((annotation, name))
                    if bridged is not None:
                        queue.append((bridged, annotation.__name__, name))
                    continue
                queue.append((info.annotation, annotation.__name__, name))

        for name, returned in _public_returns(annotation, result.skipped):
            queue.append((returned, annotation.__name__, f"{name}()"))

    return result


def root_objects() -> list[object]:
    """Every class in the root ``__all__``, plus every root function return."""
    roots: list[object] = []
    skipped: list[tuple[str, str, str]] = []
    for name in eedesigncore.__all__:
        candidate = getattr(eedesigncore, name)
        if isinstance(candidate, type):
            roots.append(candidate)
        elif inspect.isfunction(candidate):
            try:
                hints = get_type_hints(candidate, include_extras=False)
            except Exception as error:  # pragma: no cover - recorded, never swallowed
                skipped.append((name, "return", repr(error)))
                continue
            if "return" in hints:
                roots.append(hints["return"])
    ROOT_SKIPS.extend(skipped)
    return roots


def package_all_names() -> dict[str, set[str]]:
    """Every module in the package mapped to its ``__all__``."""
    lists: dict[str, set[str]] = {PACKAGE: set(eedesigncore.__all__)}
    for info in pkgutil.walk_packages(eedesigncore.__path__, f"{PACKAGE}."):
        module = importlib.import_module(info.name)
        lists[info.name] = set(getattr(module, "__all__", ()))
    return lists


ROOT_SKIPS: list[tuple[str, str, str]] = []
ROOT_ALL: list[str] = list(eedesigncore.__all__)
RESULT = walk(root_objects())
ALL_LISTS = package_all_names()
COLLECTED = sorted(
    ((found, owner, member) for found, (owner, member) in RESULT.found.items()),
    key=lambda item: (item[0].__module__, item[0].__name__),
)


class TestTheWalkRecurses:
    """Positive controls. A walk that stopped at depth one would pass the guard
    vacuously, because no root model names an enum or a child model directly."""

    def test_the_walk_reaches_power_type_through_symbol_lib(self) -> None:
        from eedesigncore.common.sch_family.enums import PowerType

        assert PowerType in walk([eedesigncore.SymbolLib]).found, "the walk never left SymbolLib"

    def test_the_walk_reaches_justify_h_through_footprint(self) -> None:
        from eedesigncore.common.models import JustifyH

        assert JustifyH in walk([eedesigncore.Footprint]).found, "the walk never left Footprint"

    def test_the_walk_crosses_a_public_method_return(self) -> None:
        from eedesigncore.common.pcb_family.board_items import Barcode

        reached = walk([eedesigncore.Footprint]).found
        assert Barcode in reached, "Footprint.barcodes() was never crossed"
        assert reached[Barcode] == ("Footprint", "barcodes()")

    def test_the_walk_finds_the_whole_model_graph(self) -> None:
        assert len(RESULT.found) >= 150

    def test_no_member_annotation_was_skipped(self) -> None:
        assert RESULT.skipped == [], f"unresolved annotations were skipped: {RESULT.skipped}"
        assert ROOT_SKIPS == [], f"unresolved root returns were skipped: {ROOT_SKIPS}"


class TestTheAnyBridges:
    """R2. Two public fields are annotated ``Any``. The bridge table is the only
    reason the walk sees past them, and the table must stay complete."""

    def test_the_bridge_reaches_the_four_types_the_symbol_hole_hides(self) -> None:
        from eedesigncore.common.sch_family.pins import Pin, PinAlternate
        from eedesigncore.common.sch_family.text import SymText, SymTextBox

        reached = walk([eedesigncore.SymbolLib]).found
        for hidden in (Pin, PinAlternate, SymText, SymTextBox):
            assert hidden in reached, f"{hidden.__name__} is reachable only through the bridge"

    def test_the_bridge_reaches_the_pad_primitives(self) -> None:
        from eedesigncore.mod.models import PrimArc, PrimBbox

        reached = walk([eedesigncore.Footprint]).found
        for hidden in (PrimArc, PrimBbox):
            assert hidden in reached, f"{hidden.__name__} is reachable only through the bridge"

    def test_without_the_bridge_the_hidden_types_are_unreachable(self) -> None:
        from eedesigncore.common.sch_family.pins import Pin

        assert Pin not in walk([eedesigncore.SymbolLib], bridges={}).found

    def test_the_bridge_table_covers_every_any_field_the_walk_meets(self) -> None:
        uncovered = [pair for pair in RESULT.any_fields if pair not in BRIDGES]
        assert uncovered == [], (
            "a public model field is annotated Any and no bridge entry declares "
            f"what it holds: {[(owner.__name__, name) for owner, name in uncovered]}. "
            "Add the entry to BRIDGES with the declaring line, or narrow the annotation."
        )

    def test_the_walk_meets_exactly_the_two_declared_any_fields(self) -> None:
        assert {(owner.__name__, name) for owner, name in RESULT.any_fields} == {
            ("SymbolUnit", "items"),
            ("PadstackLayer", "primitives"),
        }

    def test_dropping_a_bridge_entry_leaves_an_any_field_uncovered(self) -> None:
        partial = {key: value for key, value in BRIDGES.items() if key[0] is not SymbolUnit}
        result = walk(root_objects(), bridges=partial)
        uncovered = [(o.__name__, n) for o, n in result.any_fields if (o, n) not in partial]
        assert ("SymbolUnit", "items") in uncovered

    def test_a_new_unbridged_any_field_fails_the_coverage_check(self) -> None:
        """The coverage check is self-checking: a model the table does not know
        about, carrying an ``Any`` field, is reported rather than walked past."""
        probe = create_model("ExportSurfaceAnyProbe", payload=(list[Any], []))
        probe.__module__ = "eedesigncore.common.models"
        result = walk([probe])
        uncovered = [(o.__name__, n) for o, n in result.any_fields if (o, n) not in BRIDGES]
        assert uncovered == [("ExportSurfaceAnyProbe", "payload")]


# The only sexpr types the operator ruled public. See ADR-087.
SEXPR_RULED_PUBLIC = ["SExprNodeType"]


class TestTheWalkCrossesNothingItShouldNot:
    """The two crossing answers the plan turns into assertions."""

    def test_only_the_ruled_sexpr_types_reach_the_public_graph(self) -> None:
        """The sexpr wall is now an allow-list, not a ban (ADR-087, v0.11.2).

        The operator ruled `parse` and `SExprNode` public, because the root exported
        five `load_*` functions that take an `SExprNode` and nothing public could
        build one. `SExprNodeType` follows by the closure rule: `parse` returns
        `SExprNode`, whose `node_type` field names it. No other sexpr type is public.
        """
        leaked = sorted(
            t.__name__ for t in RESULT.found if t.__module__.startswith(f"{PACKAGE}.sexpr")
        )
        assert leaked == SEXPR_RULED_PUBLIC, (
            f"the sexpr types in the public graph changed: {leaked}, expected "
            f"{SEXPR_RULED_PUBLIC}. The rest of the sexpr layer is an implementation "
            "detail. Take a ruling before exporting another one."
        )

    def test_every_ruled_sexpr_name_imports_from_the_root(self) -> None:
        """The ruling is only real if a consumer can act on it."""
        for name in ("parse", "SExprNode", "SExprNodeType"):
            assert name in eedesigncore.__all__, f"{name} left the root __all__"
            assert hasattr(eedesigncore, name), f"{name} is not importable from the root"

    def test_the_five_sexpr_loaders_are_now_callable_through_the_root(self) -> None:
        """The fault ADR-087 closes: exported loaders with no public way to feed them."""
        tree = eedesigncore.parse('(module "X" (layer "F.Cu"))')
        assert isinstance(tree, eedesigncore.SExprNode)
        assert tree.node_type is eedesigncore.SExprNodeType.LIST
        for loader in ("load_mod", "load_pcb", "load_sch", "load_sym", "load_lib_table"):
            assert loader in eedesigncore.__all__

    def test_no_collected_type_comes_from_a_private_module(self) -> None:
        leaked = [t.__name__ for t in RESULT.found if _private_module_path(t.__module__)]
        assert leaked == [], f"a private module leaked into the public graph: {leaked}"

    def test_no_collected_type_carries_a_private_name(self) -> None:
        leaked = [t.__name__ for t in RESULT.found if t.__name__.startswith("_")]
        assert leaked == [], f"an underscore-led type leaked into the public graph: {leaked}"


class TestEveryReachableTypeIsRootExported:
    def test_at_least_one_root_model_is_walked(self) -> None:
        models = [
            name
            for name in ROOT_ALL
            if isinstance(getattr(eedesigncore, name), type)
            and issubclass(getattr(eedesigncore, name), BaseModel)
        ]
        assert len(models) >= 7

    @pytest.mark.parametrize(
        ("found", "owner", "member"),
        COLLECTED,
        ids=[found.__name__ for found, _, _ in COLLECTED],
    )
    def test_a_reachable_type_sits_in_the_root_all(
        self, found: type, owner: str, member: str
    ) -> None:
        assert found.__name__ in ROOT_ALL, (
            f"{found.__name__} is handed to a caller by {owner}.{member}, and it is "
            f"absent from the package root __all__. It is defined in "
            f"{found.__module__}. Import it into src/eedesigncore/__init__.py from "
            f"{found.__module__.rsplit('.', 1)[0]} and add it to __all__. A "
            "sub-package export is NOT sufficient (ADR-085)."
        )

    @pytest.mark.parametrize(
        "found",
        [item[0] for item in COLLECTED],
        ids=[found.__name__ for found, _, _ in COLLECTED],
    )
    def test_the_root_attribute_is_the_same_object(self, found: type) -> None:
        assert getattr(eedesigncore, found.__name__) is found

    def test_every_root_name_matches_its_sub_package_attribute(self) -> None:
        mismatched: list[str] = []
        for name in ROOT_ALL:
            at_root = getattr(eedesigncore, name)
            for module_name, exported in ALL_LISTS.items():
                if module_name == PACKAGE or name not in exported:
                    continue
                if getattr(importlib.import_module(module_name), name) is not at_root:
                    mismatched.append(f"{module_name}.{name}")
        assert mismatched == [], (
            f"a root export is a different object than its owner: {mismatched}"
        )


class TestTheRootNamespaceIsFlat:
    """R6. The root list is one flat namespace, so a short name must be unique."""

    def test_no_two_collected_types_share_a_short_name(self) -> None:
        by_name: dict[str, list[str]] = {}
        for found in RESULT.found:
            by_name.setdefault(found.__name__, []).append(found.__module__)
        collisions = {name: sorted(mods) for name, mods in by_name.items() if len(mods) > 1}
        assert collisions == {}, (
            f"two distinct types share a short name: {collisions}. The root __all__ "
            "cannot hold both. Do not rename a type. Stop and take a ruling."
        )

    def test_the_root_list_holds_no_duplicate_entry(self) -> None:
        duplicates = sorted({name for name in ROOT_ALL if ROOT_ALL.count(name) > 1})
        assert duplicates == [], f"the root __all__ repeats a name: {duplicates}"

    def test_every_root_name_resolves(self) -> None:
        unresolved = [name for name in ROOT_ALL if not hasattr(eedesigncore, name)]
        assert unresolved == [], (
            f"the root __all__ names something it does not import: {unresolved}"
        )
