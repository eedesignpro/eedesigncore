"""Zone area estimates retain holes, layer scope, and missing geometry."""

import json
import subprocess
import sys

import pytest

from eedesigncore import (
    Board,
    Footprint,
    build_netbom,
    inspect_design,
    portable_inspection_source,
)
from eedesigncore.common.models import PtsArc
from eedesigncore.common.pcb_family.board_items import (
    Zone,
    ZoneFilledPolygon,
    ZoneKeepout,
    ZoneOutlinePolygon,
    ZonePlacement,
)
from eedesigncore.inspection import DesignInspector
from eedesigncore.pcb.models import BoardLayer

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
HOLE = [(3, 3), (7, 3), (7, 7), (3, 7)]
BRIDGED = SQUARE + [(0, 0), (3, 3), (3, 7), (7, 7), (7, 3), (3, 3), (0, 0)]


def zone(**changes):
    values = dict(
        net_name="POWER",
        layer="F.Cu",
        polygons=[ZoneOutlinePolygon(pts=SQUARE), ZoneOutlinePolygon(pts=HOLE)],
        filled_polygons=[ZoneFilledPolygon(layer="F.Cu", pts=BRIDGED)],
    )
    values.update(changes)
    return Zone(**values)


def areas(board, **inputs):
    return inspect_design(board).execute("zone_areas", inputs)["data"]


@pytest.mark.parametrize("reverse", [False, True])
def test_cutouts_and_bridged_fill_holes(reverse):
    item = zone()
    if reverse:
        item.filled_polygons[0].pts.reverse()
        for polygon in item.polygons:
            polygon.pts.reverse()
    result = areas(Board(zones=[item]), name="POWER")
    assert result["units"] == "mm2"
    assert result["items"][0]["outline_area_mm2"] == 84
    assert result["items"][0]["saved_fill_area_mm2"] == 84
    assert result["items"][0]["fill_freshness"] == "unknown"


def test_overlap_is_a_sum_not_a_union_and_nets_stay_distinct():
    result = areas(Board(zones=[zone(), zone(), zone(net_name="OTHER")]))
    by_name = {row["name"]: row for row in result["items"]}
    assert by_name["POWER"]["saved_fill_area_mm2"] == 168
    assert by_name["OTHER"]["saved_fill_area_mm2"] == 84
    assert "Overlapping zones count more than once" in result["meaning"]


def test_missing_fill_does_not_become_zero_or_outline_area():
    result = areas(Board(zones=[zone(filled_polygons=[])]))["items"][0]
    assert result["outline_area_mm2"] == 84
    assert result["saved_fill_area_mm2"] is None
    assert result["known_saved_fill_area_mm2"] == 0
    assert result["zones_without_fill_estimate"] == 1


def test_partial_fill_preserves_known_subtotal():
    item = zone()
    item.filled_polygons.append(ZoneFilledPolygon(layer="F.Cu", pts=[(0, 0), (1, 0)]))
    result = areas(Board(zones=[item]))["items"][0]
    assert result["saved_fill_area_mm2"] is None
    assert result["known_saved_fill_area_mm2"] == 84


def test_layer_areas_do_not_sum_across_board_thickness():
    item = zone(layer=None, layers=["F.Cu", "B.Cu"])
    result = areas(Board(zones=[item]))
    by_layer = {row["layer"]: row for row in result["items"]}
    assert by_layer["F.Cu"]["saved_fill_area_mm2"] == 84
    assert by_layer["B.Cu"]["saved_fill_area_mm2"] is None
    assert areas(Board(zones=[item]), layer="B.Cu")["total"] == 1


def test_wildcard_layers_use_enabled_board_layers():
    board = Board(
        zones=[zone(layer=None, layers=["*.Cu"])],
        layers=[
            BoardLayer(ordinal=0, canonical_name="F.Cu"),
            BoardLayer(ordinal=2, canonical_name="B.Cu"),
        ],
    )
    assert {row["layer"] for row in areas(board)["items"]} == {"F.Cu", "B.Cu"}
    board.layers = []
    assert {row["layer"] for row in areas(board)["items"]} == {"F.Cu", None}


def test_keepout_placement_and_non_copper_zones_are_excluded():
    board = Board(
        zones=[
            zone(),
            zone(keepout=ZoneKeepout()),
            zone(placement=ZonePlacement()),
            zone(layer="F.SilkS", filled_polygons=[]),
        ]
    )
    result = areas(board)
    assert result["total"] == 1
    assert result["items"][0]["zone_count"] == 1
    assert result["excluded_rule_area_count"] == 2


def test_footprint_zones_and_unassigned_zones_are_included():
    board = Board(footprints=[Footprint(zones=[zone()])], zones=[zone(net_name=None)])
    result = areas(board)
    assert {row["name"] for row in result["items"]} == {None, "POWER"}
    document = build_netbom(board)
    embedded = [
        row
        for row in document.records.values()
        if row.kind == "zone" and row.links.get("component")
    ]
    assert len(embedded) == 1
    assert embedded[0].evidence[0].locator == "footprints/0/zones/0"


def test_arcs_are_unknown_without_claiming_a_chord_area():
    item = zone(
        polygons=[
            ZoneOutlinePolygon(
                pts=[(0, 0), (10, 0), PtsArc(start=(10, 0), mid=(10, 10), end=(0, 0))]
            )
        ]
    )
    assert areas(Board(zones=[item]))["items"][0]["outline_area_mm2"] is None


def test_old_netbom_is_unknown_and_pagination_preserves_rows():
    document = build_netbom(Board(zones=[zone(), zone(net_name="OTHER")])).model_dump(mode="json")
    for row in document["records"].values():
        if row["kind"] == "zone":
            row["data"].pop("area_estimates")
    view = DesignInspector(document)
    first = view.execute("zone_areas", {"limit": 1})["data"]
    assert first["items"][0]["outline_area_mm2"] is None
    assert first["items"][0]["saved_fill_area_mm2"] is None
    second = view.execute("zone_areas", {"limit": 1, "cursor": first["next_cursor"]})["data"]
    assert second["next_cursor"] is None
    assert second["items"][0]["name"] != first["items"][0]["name"]


def test_portable_zone_query_matches_native_and_exports_no_polygons(tmp_path):
    document = build_netbom(Board(zones=[zone()]))
    data = next(row.data for row in document.records.values() if row.kind == "zone")
    assert "pts" not in json.dumps(data)
    helper = tmp_path / "inspect_design.py"
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    source = tmp_path / "board.json"
    source.write_text(document.to_json(), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(helper), str(source), "zone_areas"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == DesignInspector(document.model_dump(mode="json")).execute(
        "zone_areas"
    )


def test_an_observed_fill_layer_does_not_assign_a_missing_layer():
    item = zone(layer=None, layers=None)
    item.filled_polygons.append(ZoneFilledPolygon(pts=BRIDGED))
    result = areas(Board(zones=[item]))
    by_layer = {row["layer"]: row for row in result["items"]}
    assert by_layer["F.Cu"]["saved_fill_area_mm2"] == 84
    assert by_layer[None]["saved_fill_area_mm2"] == 84


def test_islands_contribute_and_translation_does_not_change_area():
    item = zone()
    item.filled_polygons.append(
        ZoneFilledPolygon(layer="F.Cu", island=True, pts=[(20, 20), (22, 20), (20, 22)])
    )
    for polygon in [*item.polygons, *item.filled_polygons]:
        polygon.pts = [(x + 1000000000, y - 1000000000) for x, y in polygon.pts]
    result = areas(Board(zones=[item]))["items"][0]
    assert result["saved_fill_area_mm2"] == 86
    assert result["outline_area_mm2"] == 84


@pytest.mark.parametrize("value", [-1, True, "84"])
def test_invalid_exported_area_values_fail(value):
    document = build_netbom(Board(zones=[zone()])).model_dump(mode="json")
    record = next(row for row in document["records"].values() if row["kind"] == "zone")
    record["data"]["area_estimates"]["outline_area_mm2"] = value
    with pytest.raises(ValueError):
        DesignInspector(document).execute("zone_areas")


def test_aggregate_area_overflow_has_a_bounded_error():
    document = build_netbom(Board(zones=[zone(), zone()])).model_dump(mode="json")
    for record in document["records"].values():
        if record["kind"] == "zone":
            record["data"]["area_estimates"]["outline_area_mm2"] = 1e308
    with pytest.raises(ValueError, match="numeric range"):
        DesignInspector(document).execute("zone_areas")


def test_assembly_board_query_keeps_area_in_one_board():
    from eedesigncore import NetbomAssembly

    document = build_netbom(Board(zones=[zone()]))
    assembly = NetbomAssembly(assembly_id="area-test")
    assembly.add_board("first", document)
    assembly.add_board("second", document)
    result = assembly.inspect().execute(
        "board_query",
        {
            "instance": "first",
            "operation": "zone_areas",
            "inputs": {"name": "POWER"},
        },
    )["data"]["result"]["data"]
    assert result["items"][0]["saved_fill_area_mm2"] == 84
