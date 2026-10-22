"""Signature-conformance tests for the six public operations (R1, M1, ADR-045).

The unified contract:

- Model objects are positional. Every other argument is keyword-only.
- Both extracts return a detached copy and do not mutate the source container.
- Both injects append the placed model to the container and return that object.
- Both uuid transforms change the model in place and return ``None``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

import eedesigncore
from eedesigncore import (
    extract_footprint,
    extract_symbol,
    generate_uuids,
    inject_footprint,
    inject_symbol,
    strip_uuids,
)

OPERATIONS = (
    extract_footprint,
    inject_footprint,
    extract_symbol,
    inject_symbol,
    strip_uuids,
    generate_uuids,
)

# Number of leading positional model arguments per operation.
MODEL_ARG_COUNT = {
    "extract_footprint": 1,
    "extract_symbol": 1,
    "inject_footprint": 2,
    "inject_symbol": 2,
    "strip_uuids": 1,
    "generate_uuids": 1,
}


def _params(func: Callable[..., Any]) -> list[inspect.Parameter]:
    return list(inspect.signature(func).parameters.values())


class TestPackageRootExport:
    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_operation_is_exported_from_the_package_root(self, func: Callable[..., Any]) -> None:
        assert func.__name__ in eedesigncore.__all__
        assert getattr(eedesigncore, func.__name__) is func

    def test_all_six_operations_are_present(self) -> None:
        names = {f.__name__ for f in OPERATIONS}
        assert names <= set(eedesigncore.__all__)
        assert len(names) == 6


class TestKeywordOnlyDiscipline:
    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_leading_model_arguments_are_positional(self, func: Callable[..., Any]) -> None:
        count = MODEL_ARG_COUNT[func.__name__]
        for param in _params(func)[:count]:
            assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            assert param.default is inspect.Parameter.empty

    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_every_other_argument_is_keyword_only(self, func: Callable[..., Any]) -> None:
        count = MODEL_ARG_COUNT[func.__name__]
        for param in _params(func)[count:]:
            assert param.kind is inspect.Parameter.KEYWORD_ONLY, param.name

    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_no_var_args(self, func: Callable[..., Any]) -> None:
        kinds = {p.kind for p in _params(func)}
        assert inspect.Parameter.VAR_POSITIONAL not in kinds
        assert inspect.Parameter.VAR_KEYWORD not in kinds


class TestSharedArgumentNames:
    def test_both_injects_share_the_placement_arguments(self) -> None:
        shared = {"at", "rotation", "reference", "lib_nickname", "lib_table"}
        fp_names = {p.name for p in _params(inject_footprint)}
        sym_names = {p.name for p in _params(inject_symbol)}
        assert shared <= fp_names
        assert shared <= sym_names

    def test_shared_inject_arguments_carry_the_same_defaults(self) -> None:
        fp_params = inspect.signature(inject_footprint).parameters
        sym_params = inspect.signature(inject_symbol).parameters
        for name in ("at", "rotation", "reference", "lib_nickname", "lib_table"):
            assert fp_params[name].default == sym_params[name].default, name

    def test_at_is_required_and_keyword_only_in_both_injects(self) -> None:
        for func in (inject_footprint, inject_symbol):
            param = inspect.signature(func).parameters["at"]
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
            assert param.default is inspect.Parameter.empty

    def test_second_positional_argument_names_the_library_item(self) -> None:
        assert _params(inject_footprint)[1].name == "footprint"
        assert _params(inject_symbol)[1].name == "symbol"

    def test_extract_footprint_selector_uses_the_reference_name(self) -> None:
        names = [p.name for p in _params(extract_footprint)]
        assert "reference" in names
        assert "ref" not in names

    def test_both_uuid_transforms_take_one_model_argument(self) -> None:
        for func in (strip_uuids, generate_uuids):
            assert _params(func)[0].name == "obj"


class TestReturnContracts:
    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_return_annotation_is_declared(self, func: Callable[..., Any]) -> None:
        assert inspect.signature(func).return_annotation is not inspect.Signature.empty

    def test_uuid_transforms_return_none(self) -> None:
        for func in (strip_uuids, generate_uuids):
            assert inspect.signature(func).return_annotation == "None"

    def test_extracts_return_a_library_model(self) -> None:
        assert inspect.signature(extract_footprint).return_annotation == "Footprint"
        assert inspect.signature(extract_symbol).return_annotation == "Symbol"

    def test_injects_return_the_placed_model(self) -> None:
        assert inspect.signature(inject_footprint).return_annotation == "Footprint"
        assert inspect.signature(inject_symbol).return_annotation == "PlacedSymbol"


class TestDocstrings:
    @pytest.mark.parametrize("func", OPERATIONS, ids=lambda f: f.__name__)
    def test_operation_has_a_docstring(self, func: Callable[..., Any]) -> None:
        assert func.__doc__
