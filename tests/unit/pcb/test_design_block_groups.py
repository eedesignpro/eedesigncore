"""Design-block links follow KiCad PCB_GROUP read and write order."""

import pytest

from eedesigncore.common.pcb_family.loaders import _load_group
from eedesigncore.mod.writer import _dump_group
from eedesigncore.pcb.loader import load_pcb
from eedesigncore.pcb.writer import write_pcb
from eedesigncore.sexpr.parser import parse


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("lib_id", [None, "Design_Blocks:DART-MX91_Baseboard_800-001-0026"])
def test_group_link_roundtrip_and_edit(locked: bool, lib_id: str | None) -> None:
    fields = ['(group "DART"', '(uuid "group-uuid")']
    if locked:
        fields.append("(locked yes)")
    if lib_id is not None:
        fields.append(f'(lib_id "{lib_id}")')
    fields.extend(['(members "member-a" "member-b")', ")"])
    source = " ".join(fields)
    group = _load_group(parse(source))
    assert group.lib_id == lib_id
    assert _dump_group(group) == source
    group.name = "Renamed block"
    reloaded = _load_group(parse(_dump_group(group)))
    assert reloaded.lib_id == lib_id
    assert reloaded.name == "Renamed block"
    assert reloaded.locked == locked
    assert reloaded.members == ["member-a", "member-b"]


def test_board_edit_preserves_design_block_link() -> None:
    source = """(kicad_pcb (version 20260206) (generator pcbnew)
      (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
      (group "DART" (uuid "group-uuid")
        (lib_id "Design_Blocks:DART-MX91_Baseboard_800-001-0026")
        (members "member-a")))"""
    board = load_pcb(parse(source))
    board.groups[0].name = "Renamed block"
    saved = write_pcb(board)
    reloaded = load_pcb(parse(saved))
    assert reloaded.groups[0].lib_id == board.groups[0].lib_id
    assert reloaded.groups[0].name == "Renamed block"
    assert write_pcb(reloaded) == saved


def test_link_uses_string_escaping_and_can_be_removed() -> None:
    group = _load_group(parse('(group "DART" (members "member-a"))'))
    group.lib_id = 'Blocks:quoted"name\\suffix'
    assert _load_group(parse(_dump_group(group))).lib_id == group.lib_id
    group.lib_id = None
    assert "lib_id" not in _dump_group(group)
