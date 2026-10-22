"""Every model in the package rejects an unknown constructor keyword.

Pydantic rejects an unknown name on attribute assignment whatever ``extra`` holds.
The default ``extra="ignore"`` therefore made construction disagree with assignment,
and a caller who misspelled a field name got a silently discarded keyword and a wrong
file (UF-010, ADR-086).

This guard collects models by walking the package, not from a hand-written list, so a
new model cannot opt out by accident. ``MODEL_FLOOR`` stops a collection bug that finds
nothing from passing.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys

import pytest
from pydantic import BaseModel, ValidationError

import eedesigncore
from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin

# The one model that cannot be constructed, so the behavior assertion cannot run on it.
# It declares `_from_text` and `_to_lf_string` as abstract and overrides neither. Any
# OTHER abstract model is a mistake, and the guard says so rather than skipping (LOW-36).
EXPECTED_ABSTRACT = frozenset({KicadFileMixin})

# The measured count at iteration 11.12 was 142. The floor guards against a walk that
# silently collects nothing. Raise it when the package gains models.
MODEL_FLOOR = 142

BOGUS_KEYWORD = "__definitely_not_a_field__"


def _collect_models() -> list[type[BaseModel]]:
    """Every Pydantic model the package owns, excluding the base class itself."""
    for module in pkgutil.walk_packages(eedesigncore.__path__, eedesigncore.__name__ + "."):
        importlib.import_module(module.name)

    found: set[type[BaseModel]] = set()
    for name, module in list(sys.modules.items()):
        if not name.startswith("eedesigncore") or module is None:
            continue
        for obj in list(vars(module).values()):
            if not inspect.isclass(obj) or not issubclass(obj, BaseModel):
                continue
            if obj is BaseModel or obj is KicadModel:
                continue
            if obj.__module__.startswith("eedesigncore"):
                found.add(obj)
    return sorted(found, key=lambda c: (c.__module__, c.__name__))


MODELS = _collect_models()


def _label(model: type[BaseModel]) -> str:
    return f"{model.__module__}.{model.__name__}"


def test_the_walk_collects_every_model() -> None:
    assert len(MODELS) >= MODEL_FLOOR, (
        f"the walk collected {len(MODELS)} models, below the floor of {MODEL_FLOOR}. "
        "Either the package shrank, or the collection is broken."
    )


def test_every_model_inherits_the_shared_base() -> None:
    strays = [_label(m) for m in MODELS if not issubclass(m, KicadModel)]
    assert not strays, (
        "these models do not inherit KicadModel, so they do not carry the rule: "
        + ", ".join(strays)
    )


@pytest.mark.parametrize("model", MODELS, ids=_label)
def test_model_config_forbids_extra(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid", (
        f"{_label(model)} resolves extra="
        f"{model.model_config.get('extra')!r}, not 'forbid'. A wrong keyword would "
        "be discarded silently."
    )


@pytest.mark.parametrize("model", MODELS, ids=_label)
def test_unknown_keyword_raises(model: type[BaseModel]) -> None:
    """The behavior, not just the config. A validator may fire first, which is fine."""
    if model.__abstractmethods__:
        # An abstract model raises TypeError before validation runs. The config
        # assertion above still covers it, and every concrete subclass is collected.
        # Name the one expected case, so a NEW abstract model fails loudly instead of
        # skipping quietly and losing this proof (LOW-36).
        assert model in EXPECTED_ABSTRACT, (
            f"{_label(model)} is abstract and is not in EXPECTED_ABSTRACT, so this "
            f"guard would silently stop proving that a wrong keyword raises for it. "
            f"Missing overrides: {sorted(model.__abstractmethods__)}"
        )
        pytest.skip(f"{_label(model)} is abstract: {sorted(model.__abstractmethods__)}")
    with pytest.raises(ValidationError) as caught:
        model(**{BOGUS_KEYWORD: 1})
    assert BOGUS_KEYWORD in str(caught.value), (
        f"{_label(model)} raised, but did not name the unexpected keyword "
        f"{BOGUS_KEYWORD!r}. A caller cannot find the misspelled field from this "
        f"message: {caught.value}"
    )


def test_assignment_and_construction_now_agree() -> None:
    """The UF-010 disagreement, pinned on the three models the report named."""
    from eedesigncore.common.models import Stroke
    from eedesigncore.common.pcb_family.shapes import PcbText
    from eedesigncore.mod.models import Pad

    for model, real_field, wrong_name in (
        (Pad, "position", "at"),
        (Stroke, "type", "stroke_type"),
        (PcbText, "content", "text"),
    ):
        assert real_field in model.model_fields
        assert wrong_name not in model.model_fields
        with pytest.raises(ValidationError):
            model(**{wrong_name: 1})


def test_the_pro_models_keep_their_own_config() -> None:
    """`extra` merges along the MRO. It does not replace a subclass config."""
    from eedesigncore.pro.models import MetaBlock, ProjectFile, ProjectLocalSettings

    assert MetaBlock.model_config["frozen"] is True
    assert MetaBlock.model_config["extra"] == "forbid"
    for model in (ProjectFile, ProjectLocalSettings):
        assert model.model_config["arbitrary_types_allowed"] is True
        assert model.model_config["extra"] == "forbid"
