"""The board update writes what KiCad 10's Update PCB from Schematic writes.

Each rule cites the KiCad 10.0.7 source. The cases come from reports against
eedesigncore 0.39.0 on a 500-part project, where KiCad linked no footprint after
an update (root sheet uuid in the path), power symbols blocked the update, pin
functions and pin types differed from the netlist, and a footprint keep-out
landed at board 0,0.
"""

from pathlib import Path

from eedesigncore import Board, Footprint, InstancePath, NoConnect, Paper, PlacedSymbol, Property
from eedesigncore.common.pcb_family.board_items import Zone, ZoneKeepout, ZoneOutlinePolygon
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.operations import extract_footprint, inject_footprint
from eedesigncore.pcb.update_from_schematic import (
    plan_update_pcb_from_schematic,
    update_pcb_from_schematic,
)
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode
from eedesigncore.project.update_models import PcbUpdateOptions
from eedesigncore.sch.models import Schematic
from tests.unit.test_design_updates import design, replacement, uid


def _hierarchy(tmp_path: Path, child: Schematic) -> tuple[SheetHierarchy, str]:
    """Root, then sheet COILS, then sheet PREAMP S1C1 holding *child*."""
    root = Schematic(uuid=uid("root"), paper=Paper())
    coils = Schematic(uuid=uid("coils-file"), paper=Paper())
    root_path = "/" + uid("root")
    coils_path = root_path + "/" + uid("coils")
    preamp_path = coils_path + "/" + uid("preamp")
    for symbol in child.symbols:
        for project in symbol.instances:
            for instance in project.paths:
                instance.path = preamp_path
    preamp = SheetNode(
        path=tmp_path / "coil_channel.kicad_sch",
        schematic=child,
        name="PREAMP S1C1",
        kiid_path=preamp_path,
    )
    coils_node = SheetNode(
        path=tmp_path / "coils.kicad_sch",
        schematic=coils,
        name="COILS",
        kiid_path=coils_path,
        children=[preamp],
    )
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "root.kicad_sch",
                schematic=root,
                name="root",
                kiid_path=root_path,
                children=[coils_node],
            )
        ],
    )
    return hierarchy, preamp_path


def test_root_level_path_carries_no_root_sheet_uuid():
    """PathAsString starts below the root (sch_sheet_path.cpp:445-457)."""
    board, sch = design()
    board.footprints[0].path = "/" + uid("root") + "/" + uid("symbol")  # 0.39.0 form
    result = update_pcb_from_schematic(board, sch)
    fp = result.board.footprints[0]
    assert fp.path == "/" + uid("symbol")
    assert fp.sheetname == "/"


def test_hierarchical_path_sheetname_and_sheetfile(tmp_path):
    board, child = design()
    hierarchy, preamp_path = _hierarchy(tmp_path, child)
    board.footprints[0].path = preamp_path + "/" + uid("symbol")  # 0.39.0 form
    result = update_pcb_from_schematic(board, hierarchy)
    fp = result.board.footprints[0]
    assert fp.path == "/" + uid("coils") + "/" + uid("preamp") + "/" + uid("symbol")
    assert fp.sheetname == "/COILS/PREAMP S1C1/"
    assert fp.sheetfile == "coil_channel.kicad_sch"


def _two_units(sch: Schematic, second_uuid: str) -> None:
    definition = sch.lib_symbols[0]
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid = second_uuid
    second.unit = 2
    second.instances[0].paths[0].unit = 2
    second.position = (50.8, 25.4)
    sch.symbols.append(second)
    definition.units.append(definition.units[0].model_copy(deep=True))
    definition.units[1].unit_index = 2
    definition.units[1].name = "Test:Part_2_1"


def test_two_units_take_the_non_primary_uuid():
    """KiCad's primary unit is the lowest uuid; the first tstamps entry is the other.

    ``netlist_exporter_xml.cpp:286-306, 595-614`` and
    ``board_netlist_updater.cpp:481-484``.
    """
    board, sch = design()
    higher = "ffffffff-0000-4000-8000-000000000000"
    _two_units(sch, higher)
    board.footprints[0].path = "/" + uid("root") + "/" + uid("symbol")
    result = update_pcb_from_schematic(board, sch, options=PcbUpdateOptions(update_pad_nets=False))
    assert result.board.footprints[0].path == "/" + max(uid("symbol"), higher)


def test_a_linked_unit_uuid_is_kept():
    """KiCad links any unit uuid (board_netlist_updater.cpp:2118-2131): no churn."""
    board, sch = design()
    higher = "ffffffff-0000-4000-8000-000000000000"
    _two_units(sch, higher)
    lower = min(uid("symbol"), higher)
    board.footprints[0].path = "/" + lower
    result = update_pcb_from_schematic(board, sch, options=PcbUpdateOptions(update_pad_nets=False))
    assert result.board.footprints[0].path == "/" + lower


def test_power_symbols_make_no_footprint():
    """A reference that starts with "#" stays out of the board netlist.

    ``netlist_exporter_base.cpp:95-100``.
    """
    board, sch = design()
    power = PlacedSymbol(
        uuid=uid("power"),
        lib_id=sch.lib_symbols[0].name,
        position=(76.2, 76.2),
        fields=[Property(name="Reference", value="#PWR01"), Property(name="Value", value="GND")],
        instances=[sch.symbols[0].instances[0].model_copy(deep=True)],
    )
    power.instances[0].paths = [InstancePath(path="/" + uid("root"), reference="#PWR01", unit=1)]
    sch.symbols.append(power)
    plan = plan_update_pcb_from_schematic(board, sch)
    assert not plan.blockers
    assert {change.reference for change in plan.changes} == {"U1"}


def test_pin_function_is_name_underscore_number():
    """``netlist_exporter_xml.cpp:1322-1325``."""
    board, sch = design()
    result = update_pcb_from_schematic(board, sch)
    pads = {pad.number: pad for pad in result.board.footprints[0].pads}
    assert pads["1"].pinfunction == "A_1"
    assert pads["2"].pinfunction == "B_2"
    assert pads["1"].pintype == "passive"


def test_pin_type_takes_no_connect_only_under_a_marker():
    """``netlist_exporter_xml.cpp:1200-1206, 1327-1338``."""
    board, sch = design()
    sch.labels.pop()  # pin 2 loses NET_B and stands alone
    sch.no_connects.append(NoConnect(position=(27.94, 25.4), uuid=uid("nc")))
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(preserve_routed_contacts=False)
    )
    pads = {pad.number: pad for pad in result.board.footprints[0].pads}
    assert pads["2"].pintype == "passive+no_connect"
    assert pads["1"].pintype == "passive"


def _with_keepout(footprint: Footprint) -> Footprint:
    footprint.zones.append(
        Zone(
            layers=["F.Cu"],
            keepout=ZoneKeepout(),
            polygons=[ZoneOutlinePolygon(pts=[(-0.75, -0.55), (0.75, -0.55), (0.75, 0.55)])],
        )
    )
    return footprint


def test_inject_moves_a_footprint_zone_to_the_placement():
    """``FOOTPRINT::SetPosition`` moves every zone (footprint.cpp:3002-3021)."""
    board = Board()
    placed = inject_footprint(board, _with_keepout(replacement()), at=(231.0, 77.0))
    assert placed.zones[0].polygons[0].pts == [(230.25, 76.45), (231.75, 76.45), (231.75, 77.55)]


def test_inject_turns_a_footprint_zone_about_the_placement():
    board = Board()
    placed = inject_footprint(board, _with_keepout(replacement()), at=(231.0, 77.0), rotation=90)
    points = [(round(x, 6), round(y, 6)) for x, y in placed.zones[0].polygons[0].pts]
    assert points == [(230.45, 77.75), (230.45, 76.25), (231.55, 76.25)]


def test_extract_returns_the_zone_to_library_coordinates():
    board = Board()
    library = _with_keepout(replacement())
    inject_footprint(board, library, at=(231.0, 77.0), rotation=90, reference="U7")
    extracted = extract_footprint(board, reference="U7")
    points = [(round(x, 6), round(y, 6)) for x, y in extracted.zones[0].polygons[0].pts]
    assert points == library.zones[0].polygons[0].pts


def test_replaced_footprint_zone_follows_the_placement():
    board, sch = design()
    sch.symbols[0].fields[2].value = "Test:New"
    libraries = {"Test:New": _with_keepout(replacement())}
    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints=libraries,
        options=PcbUpdateOptions(replace_footprints=True, preserve_routed_contacts=False),
    )
    fp = result.board.footprints[0]
    assert fp.at == FootprintPlacement(position=(50, 60), angle=90)
    points = [(round(x, 6), round(y, 6)) for x, y in fp.zones[0].polygons[0].pts]
    assert points == [(49.45, 60.75), (49.45, 59.25), (50.55, 59.25)]


def test_several_top_level_sheets_keep_the_top_level_uuid(tmp_path):
    """With several top-level sheets index 0 is a virtual root (sch_sheet_path.cpp:460-484)."""
    board, child = design()
    hierarchy, preamp_path = _hierarchy(tmp_path, child)
    other = hierarchy.roots[0].model_copy()
    other.kiid_path = "/" + uid("second-top")
    other.children = []
    other.schematic = Schematic(uuid=uid("second-top"), paper=Paper())
    hierarchy.roots.append(other)
    board.footprints[0].path = preamp_path + "/" + uid("symbol")
    result = update_pcb_from_schematic(board, hierarchy)
    assert result.board.footprints[0].path == preamp_path + "/" + uid("symbol")
