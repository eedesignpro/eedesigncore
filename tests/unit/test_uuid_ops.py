"""Tests for group-aware strip_uuids / generate_uuids / derive_uuids (ADR-019, T19).

The three transforms walk a model tree and match on field NAME, so they are
format-agnostic by construction. The board and footprint cases are below, and
so are the schematic cases, which iteration 14 fixed and this module now
covers.
"""

from __future__ import annotations

from eedesigncore.common import derive_uuids, generate_uuids, strip_uuids
from eedesigncore.common.models import Property
from eedesigncore.common.pcb_family.shapes import PcbGroup, PcbLine
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.mod.writer import write_mod
from eedesigncore.sch.models import Group, Line, LineType, Schematic
from eedesigncore.sym.models import SymbolLib


def _grouped_footprint() -> Footprint:
    """A footprint whose group references a pad + a line by uuid."""
    return Footprint(
        fp_id="G",
        uuid="fp-uuid",
        properties=[Property(name="Reference", value="U1", id="prop-uuid")],
        drawings=[PcbLine(id="line-uuid")],
        pads=[Pad(number="1", layers=["F.Cu"], id="pad-uuid")],
        groups=[
            PcbGroup(name="g1", id="grp-uuid", members=["line-uuid", "pad-uuid"]),
        ],
    )


def _ungrouped_footprint() -> Footprint:
    return Footprint(
        fp_id="U",
        uuid="fp-uuid",
        properties=[Property(name="Reference", value="U1", id="prop-uuid")],
        drawings=[PcbLine(id="line-uuid")],
        pads=[Pad(number="1", layers=["F.Cu"], id="pad-uuid")],
    )


class TestStripGrouped:
    def test_group_referenced_uuids_preserved(self) -> None:
        fp = _grouped_footprint()
        strip_uuids(fp)
        group = fp.groups[0]
        # Group's own uuid + both members preserved.
        assert group.id == "grp-uuid"
        assert set(group.members) == {"line-uuid", "pad-uuid"}
        assert fp.drawings[0].id == "line-uuid"
        assert fp.pads[0].id == "pad-uuid"
        # Non-referenced uuids stripped.
        assert fp.uuid is None
        assert fp.properties[0].id is None

    def test_group_integrity_after_byte_round_trip(self) -> None:
        fp = _grouped_footprint()
        strip_uuids(fp)
        text = write_mod(fp)
        # Members still resolve to present child uuids -> KiCad would not drop
        # the group; re-parse + re-emit is byte-stable.
        reparsed = Footprint.from_string(text)
        assert write_mod(reparsed) == text
        assert "line-uuid" in text and "pad-uuid" in text and "grp-uuid" in text

    def test_group_aware_false_strips_everything(self) -> None:
        fp = _grouped_footprint()
        strip_uuids(fp, group_aware=False)
        # Loud warning documented; members list retained but now dangling.
        assert fp.groups[0].id is None
        assert fp.drawings[0].id is None
        assert fp.pads[0].id is None


class TestStripUngrouped:
    def test_all_uuids_stripped(self) -> None:
        fp = _ungrouped_footprint()
        strip_uuids(fp)
        assert fp.uuid is None
        assert fp.properties[0].id is None
        assert fp.drawings[0].id is None
        assert fp.pads[0].id is None


class TestGenerate:
    def test_only_missing_fills_gaps_and_keeps_existing(self) -> None:
        fp = _grouped_footprint()
        fp.properties[0].id = None  # a gap
        generate_uuids(fp)  # only_missing default
        assert fp.properties[0].id is not None
        # Existing group-referenced uuids untouched -> integrity preserved.
        assert fp.groups[0].members == ["line-uuid", "pad-uuid"]
        assert fp.drawings[0].id == "line-uuid"

    def test_regenerate_all_remaps_group_members(self) -> None:
        fp = _grouped_footprint()
        old_members = set(fp.groups[0].members)
        generate_uuids(fp, only_missing=False)
        new_members = set(fp.groups[0].members)
        # Members were remapped (no longer the old strings) ...
        assert new_members.isdisjoint(old_members)
        # ... and still point at the regenerated child uuids.
        assert set(fp.groups[0].members) == {fp.drawings[0].id, fp.pads[0].id}

    def test_missing_uuids_are_unique(self) -> None:
        fp = _ungrouped_footprint()
        strip_uuids(fp)
        generate_uuids(fp)
        ids = [fp.uuid, fp.properties[0].id, fp.drawings[0].id, fp.pads[0].id]
        assert all(i is not None for i in ids)
        assert len(set(ids)) == len(ids)


class TestSymbolNoOp:
    def test_strip_and_generate_are_noops_on_symbol_lib(self) -> None:
        lib = SymbolLib()
        # Must not raise and must leave the object unchanged (uuid-free format).
        strip_uuids(lib)
        generate_uuids(lib)
        generate_uuids(lib, only_missing=False)


class TestSchematicGroupRemap:
    """The remap covers a SCHEMATIC group, not only a board or footprint one.

    `generate_uuids(only_missing=False)` used to remap `PcbGroup` and nothing
    else, so a schematic `Group` kept references to the old uuids. KiCad drops a
    member whose uuid resolves to nothing, then drops the emptied group on the
    next save. Iteration 14 widened the remap to match on the `members` field.
    """

    def _grouped_schematic(self) -> Schematic:
        return Schematic(
            lines=[
                Line(line_type=LineType.wire, start=(0.0, 0.0), end=(1.0, 0.0), uuid="wire-a"),
                Line(line_type=LineType.wire, start=(1.0, 0.0), end=(2.0, 0.0), uuid="wire-b"),
            ],
            groups=[Group(name="g1", uuid="grp-uuid", members=["wire-a", "wire-b"])],
        )

    def test_regeneration_keeps_the_group_pointing_at_its_members(self) -> None:
        sch = self._grouped_schematic()

        generate_uuids(sch, only_missing=False)

        members = sch.groups[0].members
        assert members == [line.uuid for line in sch.lines]
        assert "wire-a" not in members
        assert "wire-b" not in members

    def test_derivation_keeps_the_group_pointing_at_its_members(self) -> None:
        sch = self._grouped_schematic()

        derive_uuids(sch, key="instance-1")

        assert sch.groups[0].members == [line.uuid for line in sch.lines]

    def test_a_strip_protects_a_schematic_group_member(self) -> None:
        sch = self._grouped_schematic()

        strip_uuids(sch)

        assert sch.groups[0].members == ["wire-a", "wire-b"]
        assert [line.uuid for line in sch.lines] == ["wire-a", "wire-b"]


class TestDerivationIsFormatAgnostic:
    """`derive_uuids` walks any model tree, so it reaches a schematic too.

    Nothing in the signature limits it to a board. The board work of iteration 14
    is what needed it, and this test states the wider reach so a reader does not
    have to infer it.
    """

    def test_the_same_key_gives_the_same_schematic_uuids(self) -> None:
        first = Schematic(
            lines=[Line(line_type=LineType.wire, start=(0.0, 0.0), end=(1.0, 0.0), uuid="w")]
        )
        second = first.model_copy(deep=True)

        derive_uuids(first, key="A")
        derive_uuids(second, key="A")

        assert first.lines[0].uuid == second.lines[0].uuid
        assert first.lines[0].uuid != "w"

    def test_a_different_key_gives_different_schematic_uuids(self) -> None:
        first = Schematic(
            lines=[Line(line_type=LineType.wire, start=(0.0, 0.0), end=(1.0, 0.0), uuid="w")]
        )
        second = first.model_copy(deep=True)

        derive_uuids(first, key="A")
        derive_uuids(second, key="B")

        assert first.lines[0].uuid != second.lines[0].uuid

    def test_it_stays_a_no_op_on_a_symbol_library(self) -> None:
        lib = SymbolLib()

        derive_uuids(lib, key="A")  # must not raise; .kicad_sym is uuid-free
