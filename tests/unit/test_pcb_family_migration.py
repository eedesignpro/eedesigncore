"""Guards for the common.pcb_family package after the v0.7.0 alias removal (T13).

The iter-8 ``Fp*``/``Group`` deprecated aliases and the ``common.pcb_models``
re-export shim were removed in v0.7.0 (ADR-020 one-release schedule). This module
now asserts:
1. The canonical ``common.pcb_family`` ``Pcb*`` names are exported from ``mod``.
2. The removed aliases and the ``pcb_models`` shim are no longer importable.
3. isinstance dispatch in the writer works through the canonical names.
"""

from __future__ import annotations

import importlib

import pytest

import eedesigncore.mod as mod
from eedesigncore.common import pcb_family
from eedesigncore.mod.models import Footprint
from eedesigncore.mod.writer import write_mod

_CANONICAL = [
    "PcbLine",
    "PcbRect",
    "PcbCircle",
    "PcbArc",
    "PcbPoly",
    "PcbCurve",
    "PcbText",
    "PcbTextBox",
    "PcbDrawItem",
    "PcbGroup",
]

_REMOVED_ALIASES = [
    "FpLine",
    "FpRect",
    "FpCircle",
    "FpArc",
    "FpPoly",
    "FpCurve",
    "FpText",
    "FpTextBox",
    "FpDrawItem",
    "Group",
]


class TestCanonicalNames:
    def test_canonical_names_exported_from_mod(self) -> None:
        for name in _CANONICAL:
            assert getattr(mod, name) is getattr(pcb_family, name)

    def test_canonical_names_in_mod_all(self) -> None:
        for name in _CANONICAL:
            assert name in mod.__all__


class TestRemovedInV070:
    def test_deprecated_aliases_gone(self) -> None:
        for name in _REMOVED_ALIASES:
            assert not hasattr(mod, name), name
            assert name not in mod.__all__

    def test_pcb_models_shim_unimportable(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("eedesigncore.common.pcb_models")


class TestIsinstanceDispatch:
    def _footprint(self, line: object) -> Footprint:
        return Footprint(
            fp_id="TEST",
            version=20260206,
            generator="test",
            layer="F.Cu",
            drawings=[line],  # type: ignore[list-item]
        )

    def test_canonical_line_round_trips(self) -> None:
        kwargs = {"start": (0.0, 0.0), "end": (1.0, 1.0), "layer": "F.SilkS"}
        out = write_mod(self._footprint(pcb_family.PcbLine(**kwargs)))
        assert "fp_line" in out

    def test_canonical_group_round_trips(self) -> None:
        fp = Footprint(fp_id="T", generator="test", layer="F.Cu")
        fp.groups = [pcb_family.PcbGroup(name="g", members=["1", "2"])]
        assert "group" in write_mod(fp)
