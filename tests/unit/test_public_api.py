"""Import-surface tests for the public API (arch-plan section 5 / S6).

Verifies the convenience re-exports resolve and that every name declared in each
package ``__all__`` is actually importable via ``getattr``.
"""

import importlib

import pytest


def test_top_level_convenience_imports() -> None:
    from eedesigncore import (  # noqa: F401
        Footprint,
        KicadParseError,
        SymbolLib,
        dump_mod,
        dump_sym,
        load_mod,
        load_sym,
        write_mod,
        write_sym,
    )


def test_sym_public_imports() -> None:
    from eedesigncore.sym import (  # noqa: F401
        Pin,
        PinAlternate,
        PinElectricalType,
        PinShape,
        SchArc,
        SchBezier,
        SchCircle,
        SchPolyline,
        SchRect,
        Symbol,
        SymbolLib,
        SymbolUnit,
        SymText,
        SymTextBox,
        dump_sym,
        load_sym,
        write_sym,
    )


def test_dump_sym_is_public() -> None:
    import eedesigncore.sym as sym

    assert "dump_sym" in sym.__all__
    assert callable(sym.dump_sym)


def test_mod_public_imports() -> None:
    from eedesigncore.mod import (  # noqa: F401
        Footprint,
        PcbLine,
        dump_mod,
        load_mod,
        write_mod,
    )


def test_pro_public_imports() -> None:
    from eedesigncore.pro import (  # noqa: F401
        JsonFloat,
        JsonInt,
        JsonTree,
        MetaBlock,
        ProjectFile,
        ProjectLocalSettings,
        dump_json,
        load_prl,
        load_pro,
        parse_json,
    )


def test_pro_names_reach_the_package_root() -> None:
    import eedesigncore

    for name in ("ProjectFile", "ProjectLocalSettings", "parse_json", "dump_json"):
        assert name in eedesigncore.__all__
        assert hasattr(eedesigncore, name)


def test_common_primitive_reexports() -> None:
    from eedesigncore.common import (  # noqa: F401
        Color,
        EmbeddedFile,
        Fill,
        Property,
        Stroke,
        TextAttributes,
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "eedesigncore",
        "eedesigncore.common",
        "eedesigncore.mod",
        "eedesigncore.pro",
        "eedesigncore.sym",
        "eedesigncore.common.pcb_family",
        "eedesigncore.common.sch_family",
    ],
)
def test_all_names_resolve(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, "__all__"), f"{module_name} has no __all__"
    for name in module.__all__:
        assert hasattr(module, name), f"{module_name}.{name} in __all__ but not resolvable"
