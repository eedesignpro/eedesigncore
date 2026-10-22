"""Copper references move together while routed identities remain unchanged."""

from copy import deepcopy

import pytest

from eedesigncore import (
    BackdrillInfo,
    Board,
    BoardGeneral,
    BoardLayer,
    BoardStackup,
    BoardStackupLayer,
    BoardStackupSubLayer,
    Footprint,
    Pad,
    Setup,
    TrackSegment,
    Via,
    Zone,
    apply_copper_layer_change,
    inspect_copper_layer_usage,
    plan_copper_layer_change,
    validate_copper_layer_references,
)


def stack(count):
    names = ["F.Cu", *(f"In{i}.Cu" for i in range(1, count - 1)), "B.Cu"]
    layers = []
    for index, name in enumerate(names):
        layers.append(
            BoardStackupLayer(
                name=name, layer_type="copper", sublayers=[BoardStackupSubLayer(thickness=0.035)]
            )
        )
        if index != count - 1:
            layers.append(
                BoardStackupLayer(
                    name=f"dielectric {index + 1}",
                    layer_type="core",
                    sublayers=[BoardStackupSubLayer(thickness=0.1)],
                )
            )
    return names, BoardStackup(layers=layers)


def board():
    names, construction = stack(6)
    result = Board(
        general=BoardGeneral(thickness=0.71),
        setup=Setup(stackup=construction),
        layers=[
            BoardLayer(
                canonical_name=name,
                ordinal=0 if i == 0 else 2 if name == "B.Cu" else 2 * (i + 1),
                user_name=f"user {name}",
            )
            for i, name in enumerate(names)
        ],
        tracks=[
            TrackSegment(start=(1, 2), end=(3, 4), layer="In4.Cu", net="N", uuid="track"),
            Via(
                via_type="buried",
                layers=("In1.Cu", "In4.Cu"),
                at=(3, 4),
                net="N",
                uuid="via",
                backdrill=BackdrillInfo(start_layer="B.Cu", end_layer="In3.Cu"),
            ),
        ],
        zones=[Zone(layer="In4.Cu", id="zone")],
        footprints=[
            Footprint(
                pads=[
                    Pad(
                        number="1",
                        pad_type="smd",
                        shape="rect",
                        layers=["In4.Cu"],
                        zone_layer_connections=["In4.Cu"],
                    )
                ]
            )
        ],
    )
    result.setup.pcbplotparams.layerselection = "0x00000000_00000000_00000000_00000400"
    return result


def plan(original):
    names, construction = stack(8)
    mapping = dict(
        zip(stack(6)[0], ["F.Cu", "In1.Cu", "In2.Cu", "In5.Cu", "In6.Cu", "B.Cu"], strict=False)
    )
    return plan_copper_layer_change(original, new_layers=names, mapping=mapping), construction


def test_content_metadata_backdrill_and_plot_mask_follow_map():
    original = board()
    before = deepcopy(original.model_dump())
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before
    assert changed.tracks[0].layer == "In6.Cu"
    assert changed.tracks[0].uuid == "track"
    assert changed.tracks[0].net == "N"
    assert changed.tracks[0].start == (1, 2)
    assert changed.tracks[1].layers == ("In1.Cu", "In6.Cu")
    assert changed.tracks[1].backdrill.end_layer == "In5.Cu"
    assert changed.footprints[0].pads[0].layers == ["In6.Cu"]
    assert changed.footprints[0].pads[0].zone_layer_connections == ["In6.Cu"]
    assert changed.zones[0].layer == "In6.Cu"
    assert changed.layers[6].user_name == "user In4.Cu"
    assert int(changed.setup.pcbplotparams.layerselection.replace("_", ""), 16) == 1 << 14
    assert not validate_copper_layer_references(changed)


def test_deletion_reports_every_occupied_reference():
    original = board()
    names, construction = stack(4)
    mapping = dict(
        zip(stack(6)[0], ["F.Cu", "In1.Cu", "In2.Cu", None, None, "B.Cu"], strict=False)
    )
    operation = plan_copper_layer_change(original, new_layers=names, mapping=mapping)
    assert any("footprints[0].pads[0].layers" in item for item in operation.blockers)
    assert any("backdrill.end_layer" in item for item in operation.blockers)
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)


def test_plan_is_bound_to_source_content():
    original = board()
    operation, construction = plan(original)
    original.tracks[0].net = "CHANGED"
    with pytest.raises(ValueError, match="changed after"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)


def test_layer_map_cannot_merge_or_reverse_content():
    original = board()
    names = stack(6)[0]
    mapping = dict(zip(names, names, strict=False))
    mapping["In4.Cu"] = "In3.Cu"
    with pytest.raises(ValueError, match="distinct"):
        plan_copper_layer_change(original, new_layers=names, mapping=mapping)


def test_wildcard_pad_remains_wildcard():
    original = board()
    original.footprints[0].pads[0].layers = ["*.Cu"]
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.footprints[0].pads[0].layers == ["*.Cu"]
    assert inspect_copper_layer_usage(changed)


@pytest.mark.parametrize("name", ["Bogus.Cu", "In99.Cu", "In0.Cu", "In01.Cu"])
def test_unknown_or_disabled_reference_blocks_without_source_change(name):
    original = board()
    original.tracks[0].layer = name
    before = original.model_dump()
    operation, construction = plan(original)
    assert operation.blockers
    assert validate_copper_layer_references(original)
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before


def test_setup_zone_defaults_follow_content_and_block_deletion():
    from eedesigncore import ZoneLayerProperty

    original = board()
    original.setup.zone_defaults = [ZoneLayerProperty(layer="In4.Cu", hatch_position=(1, 2))]
    operation, construction = plan(original)
    assert any(ref.path == "setup.zone_defaults[0].layer" for ref in operation.references)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.setup.zone_defaults[0].layer == "In6.Cu"
    assert changed.setup.zone_defaults[0].hatch_position == (1, 2)
    original.tracks = []
    original.zones = []
    original.footprints = []
    names, _ = stack(4)
    mapping = dict(
        zip(stack(6)[0], ["F.Cu", "In1.Cu", "In2.Cu", None, None, "B.Cu"], strict=False)
    )
    assert any(
        "zone_defaults" in item
        for item in plan_copper_layer_change(original, new_layers=names, mapping=mapping).blockers
    )


@pytest.mark.parametrize("field", ["backdrill", "tertiary_drill"])
@pytest.mark.parametrize("ends", [("B.Cu", "B.Cu"), ("F.Cu", "In99.Cu"), ("", "")])
def test_secondary_drill_invalid_endpoints_block(field, ends):
    original = board()
    setattr(original.tracks[1], field, BackdrillInfo(start_layer=ends[0], end_layer=ends[1]))
    before = original.model_dump()
    operation, construction = plan(original)
    assert any("secondary drill" in item for item in operation.blockers)
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before


@pytest.mark.parametrize(
    "via_type,ends",
    [
        ("through", ("F.Cu", "B.Cu")),
        ("blind", ("F.Cu", "In2.Cu")),
        ("buried", ("In1.Cu", "In4.Cu")),
        ("micro", ("F.Cu", "In1.Cu")),
        ("micro", ("In4.Cu", "B.Cu")),
    ],
)
def test_supported_via_spans_follow_explicit_map(via_type, ends):
    original = board()
    original.tracks[1].via_type = via_type
    original.tracks[1].layers = ends
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.tracks[1].layers == tuple(dict(operation.mapping)[name] for name in ends)
    assert changed.tracks[1].via_type == via_type
    assert not validate_copper_layer_references(changed)


@pytest.mark.parametrize(
    "via_type,ends",
    [
        ("through", ("F.Cu", "In4.Cu")),
        ("blind", ("In4.Cu", "In1.Cu")),
        ("buried", ("In1.Cu", "In1.Cu")),
        ("micro", ("In2.Cu", "In3.Cu")),
        ("micro", ("F.Cu", "In2.Cu")),
    ],
)
def test_invalid_or_split_via_spans_block(via_type, ends):
    original = board()
    original.tracks[1].via_type = via_type
    original.tracks[1].layers = ends
    operation, construction = plan(original)
    assert operation.blockers
    before = original.model_dump()
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before


@pytest.mark.parametrize("thickness", [-0.1, 0, None, float("nan"), float("inf")])
def test_invalid_supplied_thickness_fails_without_mutation(thickness):
    original = board()
    operation, construction = plan(original)
    construction.layers[0].sublayers[0].thickness = thickness
    before = original.model_dump()
    with pytest.raises(ValueError, match="thickness"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before


def test_duplicate_layer_table_and_wrong_ordinals_block():
    original = board()
    original.layers.append(deepcopy(original.layers[1]))
    assert any("duplicate" in item for item in validate_copper_layer_references(original))
    operation, construction = plan(original)
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    original.layers.pop()
    original.layers[1].ordinal = 75
    assert any("ordinal" in item for item in validate_copper_layer_references(original))


def test_custom_padstack_and_via_padstack_remap():
    from eedesigncore import Padstack, PadstackLayer, ViaPadstack, ViaPadstackLayer

    original = board()
    original.footprints[0].pads[0].padstack = Padstack(
        mode="custom", layers=[PadstackLayer(layer_name="In4.Cu", size=(2, 3))]
    )
    original.tracks[1].padstack = ViaPadstack(
        mode="custom", layers=[ViaPadstackLayer(layer_name="In4.Cu", size=0.8)]
    )
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.footprints[0].pads[0].padstack.layers[0].layer_name == "In6.Cu"
    assert changed.footprints[0].pads[0].padstack.layers[0].size == (2, 3)
    assert changed.tracks[1].padstack.layers[0].layer_name == "In6.Cu"
    assert changed.tracks[1].padstack.layers[0].size == 0.8


def test_inner_padstack_semantics_remain_grouped():
    from eedesigncore import Padstack, PadstackLayer

    original = board()
    original.footprints[0].pads[0].padstack = Padstack(
        mode="front_inner_back", layers=[PadstackLayer(layer_name="Inner", size=(2, 3))]
    )
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.footprints[0].pads[0].padstack.layers[0].layer_name == "Inner"


def test_fixed_footprint_stackup_and_generator_refused():
    from eedesigncore import PcbGenerator, Stackup, StackupLayer

    original = board()
    original.footprints[0].stackup = Stackup(
        layers=[StackupLayer(name="F.Cu"), StackupLayer(name="B.Cu")]
    )
    operation, _ = plan(original)
    assert any("fixed stackup" in item for item in operation.blockers)
    original.footprints[0].stackup = None
    original.generators = [PcbGenerator(layer="F.Cu")]
    operation, _ = plan(original)
    assert any("generator" in item for item in operation.blockers)


def test_added_empty_layers_delete_and_preserve_unrelated_fields():
    original = board()
    original.setup.aux_axis_origin = (19, 27)
    operation, construction = plan(original)
    enlarged = apply_copper_layer_change(original, plan=operation, stackup=construction)
    mapping = {name: None for name in stack(8)[0]}
    mapping.update({target: source for source, target in operation.mapping})
    names, construction = stack(6)
    reverse = plan_copper_layer_change(enlarged, new_layers=names, mapping=mapping)
    restored = apply_copper_layer_change(enlarged, plan=reverse, stackup=construction)
    assert restored.tracks == original.tracks
    assert restored.footprints == original.footprints
    assert restored.setup.aux_axis_origin == (19, 27)
    assert restored.layers == original.layers


def test_plan_cannot_be_forged_or_reused_after_change():
    from dataclasses import replace

    original = board()
    operation, construction = plan(original)
    before = original.model_dump()
    altered = replace(operation, references=())
    with pytest.raises(ValueError, match="blockers"):
        apply_copper_layer_change(original, plan=altered, stackup=construction)
    assert original.model_dump() == before


def test_legacy_layer_ordinals_and_plot_masks():
    original = board()
    for index, layer in enumerate(original.layers):
        layer.ordinal = 31 if layer.canonical_name == "B.Cu" else index
    original.setup.pcbplotparams.layerselection = hex((1 << 4) | (1 << 31) | (1 << 44))
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.layers[-1].ordinal == 31
    assert int(changed.setup.pcbplotparams.layerselection.replace("_", ""), 16) == (1 << 6) | (
        1 << 31
    ) | (1 << 44)


def test_nonpad_copper_wildcards_require_explicit_layers():
    original = board()
    original.zones[0].layer = None
    original.zones[0].layers = ["*.Cu"]
    operation, _ = plan(original)
    assert any("wildcards" in item for item in operation.blockers)


def test_overflow_thickness_is_rejected():
    original = board()
    operation, construction = plan(original)
    for layer in construction.layers:
        layer.sublayers[0].thickness = 1e308
    with pytest.raises(ValueError, match="finite"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)


def test_late_apply_failure_leaves_both_inputs_unchanged():
    original = board()
    original.setup.pcbplotparams.layerselection = "not a bit mask"
    operation, construction = plan(original)
    before = original.model_dump()
    stack_before = construction.model_dump()
    with pytest.raises(ValueError):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before
    assert construction.model_dump() == stack_before


def test_result_is_detached_from_board_and_supplied_stackup():
    original = board()
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    before = original.model_dump()
    stack_before = construction.model_dump()
    changed.tracks[0].start = (100, 200)
    changed.setup.stackup.layers[0].sublayers[0].thickness = 99
    assert original.model_dump() == before
    assert construction.model_dump() == stack_before


def test_footprint_private_layers_and_trailing_drawings_follow_mapping():
    from eedesigncore import PcbLine

    original = board()
    original.footprints[0].private_layers = ["In4.Cu", "F.Fab"]
    original.trailing_drawings = [PcbLine(start=(1, 2), end=(3, 4), layer="In4.Cu")]
    operation, construction = plan(original)
    changed = apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert changed.footprints[0].private_layers == ["In6.Cu", "F.Fab"]
    assert changed.trailing_drawings[0].layer == "In6.Cu"
    assert changed.trailing_drawings[0].start == (1, 2)


def test_stackup_wrong_order_and_duplicate_entries_fail():
    original = board()
    operation, construction = plan(original)
    construction.layers[0], construction.layers[2] = construction.layers[2], construction.layers[0]
    with pytest.raises(ValueError, match="order"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    _, construction = stack(8)
    construction.layers[3].name = construction.layers[1].name
    with pytest.raises(ValueError, match="duplicate"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)


@pytest.mark.parametrize("mask", ["-1", hex(1 << 128)])
def test_plot_mask_range_failure_preserves_source(mask):
    original = board()
    original.setup.pcbplotparams.layerselection = mask
    operation, construction = plan(original)
    before = original.model_dump()
    with pytest.raises(ValueError, match="unsigned 128-bit"):
        apply_copper_layer_change(original, plan=operation, stackup=construction)
    assert original.model_dump() == before
