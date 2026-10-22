"""Unit tests for zone round-trips in mod loader/writer (iter-6 coverage)."""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
"""


def _make(extra: str) -> str:
    return _HEAD + extra + "\n)"


def _round_trip(sexpr: str) -> Footprint:
    fp = Footprint.from_string(sexpr)
    out = fp.to_string()
    fp2 = Footprint.from_string(out)
    return fp2


# ---------------------------------------------------------------------------
# Rule-area zone with placement source types
# ---------------------------------------------------------------------------

_RULE_AREA_SHEETNAME = _make(
    """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks not_allowed)
\t\t\t(vias not_allowed)
\t\t\t(pads not_allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints not_allowed)
\t\t)
\t\t(placement
\t\t\t(enabled no)
\t\t\t(sheetname "")
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
)

_RULE_AREA_COMPONENT_CLASS = _make(
    """\
\t(zone
\t\t(layer "B.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks allowed)
\t\t\t(vias allowed)
\t\t\t(pads allowed)
\t\t\t(copperpour allowed)
\t\t\t(footprints allowed)
\t\t)
\t\t(placement
\t\t\t(enabled yes)
\t\t\t(component_class "MyClass")
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
)

_RULE_AREA_GROUP = _make(
    """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks not_allowed)
\t\t\t(vias not_allowed)
\t\t\t(pads not_allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints not_allowed)
\t\t)
\t\t(placement
\t\t\t(enabled yes)
\t\t\t(group "mygroup")
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
)


class TestZonePlacementSourceTypes:
    def test_sheetname_empty_parses(self) -> None:
        fp = Footprint.from_string(_RULE_AREA_SHEETNAME)
        assert len(fp.zones) == 1
        z = fp.zones[0]
        assert z.placement is not None
        assert z.placement.source_type == "sheetname"
        assert z.placement.source == ""
        assert z.placement.enabled is False

    def test_sheetname_round_trip_stable(self) -> None:
        fp1 = Footprint.from_string(_RULE_AREA_SHEETNAME)
        fp2 = _round_trip(_RULE_AREA_SHEETNAME)
        assert fp1.zones[0].placement == fp2.zones[0].placement

    def test_sheetname_empty_survives_write(self) -> None:
        fp = Footprint.from_string(_RULE_AREA_SHEETNAME)
        out = fp.to_string()
        # (sheetname "") must appear byte-for-byte
        assert '(sheetname "")' in out

    def test_component_class_parses(self) -> None:
        fp = Footprint.from_string(_RULE_AREA_COMPONENT_CLASS)
        z = fp.zones[0]
        assert z.placement is not None
        assert z.placement.source_type == "component_class"
        assert z.placement.source == "MyClass"
        assert z.placement.enabled is True

    def test_component_class_round_trip(self) -> None:
        fp1 = Footprint.from_string(_RULE_AREA_COMPONENT_CLASS)
        fp2 = _round_trip(_RULE_AREA_COMPONENT_CLASS)
        assert fp1.zones[0].placement == fp2.zones[0].placement

    def test_group_parses(self) -> None:
        fp = Footprint.from_string(_RULE_AREA_GROUP)
        z = fp.zones[0]
        assert z.placement is not None
        assert z.placement.source_type == "group"
        assert z.placement.source == "mygroup"

    def test_group_round_trip(self) -> None:
        fp1 = Footprint.from_string(_RULE_AREA_GROUP)
        fp2 = _round_trip(_RULE_AREA_GROUP)
        assert fp1.zones[0].placement == fp2.zones[0].placement

    def test_bogus_placement_token_raises(self) -> None:
        bad = _make(
            """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks not_allowed)
\t\t\t(vias not_allowed)
\t\t\t(pads not_allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints not_allowed)
\t\t)
\t\t(placement
\t\t\t(enabled no)
\t\t\t(bogus 1)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in zone placement"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# Zone fill options
# ---------------------------------------------------------------------------

_HATCH_ZONE = _make(
    """\
\t(zone
\t\t(net 1)
\t\t(net_name "GND")
\t\t(layer "F.Cu")
\t\t(uuid "aaaa-1111")
\t\t(name "myfill")
\t\t(hatch full 0.508)
\t\t(priority 2)
\t\t(connect_pads thru_hole_only
\t\t\t(clearance 0.25)
\t\t)
\t\t(min_thickness 0.254)
\t\t(filled_areas_thickness no)
\t\t(fill yes
\t\t\t(mode hatch)
\t\t\t(thermal_gap 0.5)
\t\t\t(thermal_bridge_width 0.5)
\t\t\t(smoothing chamfer)
\t\t\t(radius 1.0)
\t\t\t(island_removal_mode 1)
\t\t\t(island_area_min 10.0)
\t\t\t(hatch_thickness 0.3)
\t\t\t(hatch_gap 0.5)
\t\t\t(hatch_orientation 45)
\t\t\t(hatch_smoothing_level 1)
\t\t\t(hatch_smoothing_value 0.1)
\t\t\t(hatch_border_algorithm hatch_thickness)
\t\t\t(hatch_min_hole_area 0.3)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 0 0)
\t\t\t\t(xy 10 0)
\t\t\t\t(xy 10 10)
\t\t\t\t(xy 0 10)
\t\t\t)
\t\t)
\t)
"""
)


class TestZoneFillOptions:
    def test_hatch_zone_fill_mode(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        z = fp.zones[0]
        assert z.fill.filled is True
        assert z.fill.mode == "hatch"

    def test_hatch_zone_thermal_gap(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        z = fp.zones[0]
        assert z.fill.thermal_gap == pytest.approx(0.5)
        assert z.fill.thermal_bridge_width == pytest.approx(0.5)

    def test_hatch_zone_hatch_fields(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        z = fp.zones[0]
        assert z.fill.hatch_thickness == pytest.approx(0.3)
        assert z.fill.hatch_gap == pytest.approx(0.5)
        assert z.fill.hatch_orientation == pytest.approx(45)
        assert z.fill.hatch_smoothing_level == 1
        assert z.fill.hatch_smoothing_value == pytest.approx(0.1)
        assert z.fill.hatch_border_algorithm == "hatch_thickness"
        assert z.fill.hatch_min_hole_area == pytest.approx(0.3)

    def test_hatch_zone_polygon(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        z = fp.zones[0]
        assert len(z.polygons) == 1
        assert len(z.polygons[0].pts) == 4

    def test_hatch_zone_round_trip(self) -> None:
        fp1 = Footprint.from_string(_HATCH_ZONE)
        fp2 = _round_trip(_HATCH_ZONE)
        assert fp1.zones[0].fill == fp2.zones[0].fill
        assert fp1.zones[0].polygons == fp2.zones[0].polygons

    def test_zone_net_and_name(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        z = fp.zones[0]
        assert z.net == 1
        assert z.net_name == "GND"
        assert z.name == "myfill"
        assert z.id == "aaaa-1111"

    def test_zone_meta_survives_write(self) -> None:
        fp = Footprint.from_string(_HATCH_ZONE)
        out = fp.to_string()
        assert "(net 1)" in out
        assert '(net_name "GND")' in out


# ---------------------------------------------------------------------------
# Keepout zone flags
# ---------------------------------------------------------------------------

_KEEPOUT_ZONE_PLACEMENT_ALLOWED = _make(
    """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks not_allowed)
\t\t\t(vias not_allowed)
\t\t\t(pads allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints allowed)
\t\t\t(zone_placement allowed)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
)


class TestZoneKeepoutFlags:
    def test_keepout_flags_parse(self) -> None:
        fp = Footprint.from_string(_KEEPOUT_ZONE_PLACEMENT_ALLOWED)
        z = fp.zones[0]
        assert z.keepout is not None
        assert z.keepout.tracks is False
        assert z.keepout.vias is False
        assert z.keepout.pads is True
        assert z.keepout.copper_pour is False
        assert z.keepout.footprints is True
        assert z.keepout.zone_placement is True

    def test_keepout_round_trip(self) -> None:
        fp1 = Footprint.from_string(_KEEPOUT_ZONE_PLACEMENT_ALLOWED)
        fp2 = _round_trip(_KEEPOUT_ZONE_PLACEMENT_ALLOWED)
        assert fp1.zones[0].keepout == fp2.zones[0].keepout

    def test_bogus_keepout_token_raises(self) -> None:
        bad = _make(
            """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(keepout
\t\t\t(tracks not_allowed)
\t\t\t(vias not_allowed)
\t\t\t(pads not_allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints not_allowed)
\t\t\t(bogus_flag allowed)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in zone keepout"):
            Footprint.from_string(bad)

    def test_bogus_zone_fill_token_raises(self) -> None:
        bad = _make(
            """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t\t(bogus_field 1)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in zone fill"):
            Footprint.from_string(bad)

    def test_bogus_zone_token_raises(self) -> None:
        bad = _make(
            """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch none 0.508)
\t\t(connect_pads
\t\t\t(clearance 0)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(unknown_zone_token 99)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in zone"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# Multi-layer zone + filled_polygon
# ---------------------------------------------------------------------------

_MULTILAYER_ZONE = _make(
    """\
\t(zone
\t\t(layers "F.Cu" "B.Cu")
\t\t(hatch edge 0.508)
\t\t(connect_pads yes
\t\t\t(clearance 0.1)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 0 0)
\t\t\t\t(xy 5 0)
\t\t\t\t(xy 5 5)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "F.Cu")
\t\t\t(pts
\t\t\t\t(xy 0.1 0.1)
\t\t\t\t(xy 4.9 0.1)
\t\t\t\t(xy 4.9 4.9)
\t\t\t)
\t\t)
\t)
"""
)


class TestZoneMultiLayer:
    def test_layers_list_parsed(self) -> None:
        fp = Footprint.from_string(_MULTILAYER_ZONE)
        z = fp.zones[0]
        assert z.layers is not None
        assert "F.Cu" in z.layers
        assert "B.Cu" in z.layers
        assert z.layer is None

    def test_filled_polygon_parsed(self) -> None:
        fp = Footprint.from_string(_MULTILAYER_ZONE)
        z = fp.zones[0]
        assert len(z.filled_polygons) == 1
        assert z.filled_polygons[0].layer == "F.Cu"
        assert len(z.filled_polygons[0].pts) == 3

    def test_multilayer_round_trip(self) -> None:
        fp1 = Footprint.from_string(_MULTILAYER_ZONE)
        fp2 = _round_trip(_MULTILAYER_ZONE)
        assert fp1.zones[0].layers == fp2.zones[0].layers
        assert fp1.zones[0].filled_polygons == fp2.zones[0].filled_polygons

    def test_connect_pads_mode_survives(self) -> None:
        fp = Footprint.from_string(_MULTILAYER_ZONE)
        z = fp.zones[0]
        assert z.connect_pads_mode == "yes"
        assert z.connect_pads_clearance == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Zone per-layer property (GAP-006): (property (layer "...") (hatch_position ...))
# ---------------------------------------------------------------------------

_LAYER_PROPERTY_ZONE = _make(
    """\
\t(zone
\t\t(layers "F.Cu" "B.Cu")
\t\t(hatch edge 0.508)
\t\t(connect_pads
\t\t\t(clearance 0.1)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(property
\t\t\t(layer "F.Cu")
\t\t\t(hatch_position (xy 1.5 2.5))
\t\t)
\t\t(property
\t\t\t(layer "B.Cu")
\t\t\t(hatch_position (xy -3 4))
\t\t)
\t)
"""
)


class TestZoneLayerProperty:
    def test_layer_property_parsed(self) -> None:
        fp = Footprint.from_string(_LAYER_PROPERTY_ZONE)
        props = fp.zones[0].layer_properties
        assert len(props) == 2
        assert props[0].layer == "F.Cu"
        assert props[0].hatch_position == (1.5, 2.5)
        assert props[1].layer == "B.Cu"
        assert props[1].hatch_position == (-3.0, 4.0)

    def test_layer_property_emits_new_grammar(self) -> None:
        fp = Footprint.from_string(_LAYER_PROPERTY_ZONE)
        out = fp.to_string()
        assert '(property\n\t\t\t(layer "F.Cu")' in out or '(property (layer "F.Cu")' in out
        assert "(hatch_position" in out
        assert "filled_areas_thickness" not in out

    def test_layer_property_round_trip(self) -> None:
        fp1 = Footprint.from_string(_LAYER_PROPERTY_ZONE)
        fp2 = _round_trip(_LAYER_PROPERTY_ZONE)
        assert fp1.zones[0].layer_properties == fp2.zones[0].layer_properties

    def test_unknown_zone_property_token_raises(self) -> None:
        bad = _make(
            """\
\t(zone
\t\t(layer "F.Cu")
\t\t(hatch edge 0.508)
\t\t(connect_pads
\t\t\t(clearance 0.1)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0)
\t\t\t(thermal_bridge_width 0)
\t\t\t(island_removal_mode 0)
\t\t)
\t\t(property
\t\t\t(layer "F.Cu")
\t\t\t(bogus_prop_token 1)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in zone property"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# GAP-028: bare-symbol zone fields are Literal-constrained
# ---------------------------------------------------------------------------


class TestZoneBareSymbolLiterals:
    def test_illegal_hatch_style_rejected(self) -> None:
        from pydantic import ValidationError

        from eedesigncore.common.pcb_family import Zone

        with pytest.raises(ValidationError):
            Zone(hatch_style="bogus")  # type: ignore[arg-type]

    def test_illegal_connect_pads_mode_rejected(self) -> None:
        from pydantic import ValidationError

        from eedesigncore.common.pcb_family import Zone

        with pytest.raises(ValidationError):
            Zone(connect_pads_mode="sometimes")  # type: ignore[arg-type]

    def test_illegal_teardrop_type_rejected(self) -> None:
        from pydantic import ValidationError

        from eedesigncore.common.pcb_family import Zone

        with pytest.raises(ValidationError):
            Zone(teardrop_type="round")  # type: ignore[arg-type]

    def test_illegal_smoothing_rejected(self) -> None:
        from pydantic import ValidationError

        from eedesigncore.common.pcb_family import ZoneFill

        with pytest.raises(ValidationError):
            ZoneFill(smoothing="bevel")  # type: ignore[arg-type]

    def test_illegal_hatch_border_algorithm_rejected(self) -> None:
        from pydantic import ValidationError

        from eedesigncore.common.pcb_family import ZoneFill

        with pytest.raises(ValidationError):
            ZoneFill(hatch_border_algorithm="thick")  # type: ignore[arg-type]

    def test_legal_values_accepted(self) -> None:
        from eedesigncore.common.pcb_family import Zone, ZoneFill

        Zone(hatch_style="full", connect_pads_mode="thru_hole_only", teardrop_type="padvia")
        ZoneFill(smoothing="fillet", hatch_border_algorithm="min_thickness")
