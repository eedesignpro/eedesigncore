from pathlib import Path

import pytest

from eedesigncore import (
    NoConnect,
    Property,
    compare_schematic_electrical,
    inspect_schematic_electrical,
    load_hierarchy,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "schematic_preparation" / "root.kicad_pro"


def inspect(source):
    return inspect_schematic_electrical(source, document_id="test")


def test_wire_segmentation_does_not_change_electrical_facts():
    tree = load_hierarchy(FIXTURE)
    before = inspect(tree)
    sheet = tree.roots[0].schematic
    wire = next(line for line in sheet.lines if line.line_type == "wire")
    end = wire.end
    middle = tuple((a + b) / 2 for a, b in zip(wire.start, wire.end, strict=True))
    wire.end = middle
    second = wire.model_copy(deep=True)
    second.start, second.end, second.uuid = middle, end, "extra-wire-segment"
    sheet.lines.append(second)
    result = compare_schematic_electrical(before, inspect(tree))
    assert {change.category for change in result.changes} == {"drawing"}


def test_names_and_parts_are_explicit():
    tree = load_hierarchy(FIXTURE)
    before = inspect(tree)
    sheet = tree.roots[0].children[0].schematic
    label = next(label for label in sheet.labels if label.kind == "global_label")
    label.content = "RENAMED"
    sheet.symbols[0].dnp = not sheet.symbols[0].dnp
    after = inspect(tree)
    result = compare_schematic_electrical(before, after)
    assert {"net_names", "parts", "labels"} <= {change.category for change in result.changes}
    assert result.equivalent is False
    assert after.labels
    assert all(label.source_path for label in after.labels.values())


def test_current_classes_ignore_saved_assignments():
    tree = load_hierarchy(FIXTURE)
    sheet = tree.roots[0].children[0].schematic
    label = next(label for label in sheet.labels if label.kind == "global_label")
    label.fields.append(Property(name="Netclass", value="CurrentClass"))
    tree.project.root.set("net_settings", {})
    settings = tree.project.section("net_settings")
    settings.set("netclass_assignments", {label.content: ["StaleClass"]})
    settings.set("netclass_patterns", [{"pattern": "*", "netclass": "PatternClass"}])
    result = inspect(tree)
    attached = next(item for item in result.labels.values() if item.source_uuid == label.uuid)
    assert attached.net_ids
    for key in attached.net_ids:
        assert result.nets[key].class_names == ["CurrentClass", "PatternClass"]
        assert {e.source for e in result.nets[key].class_evidence} == {"label", "project_pattern"}


def test_no_connect_records_attached_pin_not_mark_identity():
    tree = load_hierarchy(FIXTURE)
    before = inspect(tree)
    pin = next(item for item in before.items.values() if item.kind == "pin")
    node = next(node for node in tree.walk() if node.kiid_path == pin.source.sheet_path)
    node.schematic.no_connects.append(NoConnect(position=pin.position, uuid="new-mark"))
    after = inspect(tree)
    assert pin.id in after.no_connect_pins
    assert any(
        change.category == "no_connect"
        for change in compare_schematic_electrical(before, after).changes
    )


def test_file_sources_and_repeated_occurrences_are_qualified():
    result = inspect(FIXTURE)
    assert result.source_hashes
    pins = [item for item in result.items.values() if item.kind == "pin"]
    assert len({item.id for item in pins}) == len(pins)
    assert len(result.sheet_files) == 3


def test_in_memory_sheet_does_not_claim_project_class_coverage():
    result = inspect(load_hierarchy(FIXTURE).roots[0].schematic)
    assert result.class_coverage == "partial"
    assert result.source_hashes == {}
    assert compare_schematic_electrical(result, result).equivalent is None


def test_bus_label_class_reaches_scalar_members_in_both_occurrences():
    tree = load_hierarchy(FIXTURE)
    sheet = tree.roots[0].children[0].schematic
    bus = next(label for label in sheet.labels if label.content == "DATA[0..1]")
    bus.fields.append(Property(name="Netclass", value="BusClass"))
    tree.project.root.set("net_settings", {"netclass_patterns": []})
    result = inspect(tree)
    member_nets = [
        net for net in result.nets.values() if (net.name or "").endswith(("/DATA0", "/DATA1"))
    ]
    assert len(member_nets) == 4
    assert all("BusClass" in net.class_names for net in member_nets)
    assert all(any(e.source == "bus_label" for e in net.class_evidence) for net in member_nets)


def test_wire_disconnection_changes_pin_groups():
    tree = load_hierarchy(FIXTURE)
    before = inspect(tree)
    sheet = tree.roots[0].children[0].schematic
    sheet.lines.clear()
    after = inspect(tree)
    assert any(
        change.category == "connectivity"
        for change in compare_schematic_electrical(before, after).changes
    )


def test_rule_area_class_coverage_is_partial():
    from eedesigncore import Schematic

    sheet = Schematic.from_file(
        Path(__file__).parents[1] / "fixtures" / "sch" / "t3b_rule_area.kicad_sch"
    )
    result = inspect(sheet)
    assert result.class_coverage == "partial"
    assert any("Rule-area" in issue for issue in result.class_issues)


def test_snapshot_json_round_trip():
    from eedesigncore import SchematicElectricalSnapshot

    result = inspect(FIXTURE)
    assert SchematicElectricalSnapshot.model_validate_json(result.model_dump_json()) == result


def test_append_classes_preserves_existing_fields_and_is_repeatable():
    from eedesigncore import Schematic, append_global_label_netclasses

    tree = load_hierarchy(FIXTURE)
    tree.project.root.set("net_settings", {"netclass_patterns": []})
    sheet = tree.roots[0].children[0].schematic
    label = next(label for label in sheet.labels if label.kind == "global_label")
    label.fields.append(Property(name="Netclass", value="DOC_existing", visible=True))
    label.fields.append(Property(name="Intersheetrefs", value="${INTERSHEET_REFS}"))
    before = label.model_dump(mode="json")
    snapshot = inspect(tree)
    record = next(
        record for record in snapshot.labels.values() if record.source_uuid == label.uuid
    )
    assert append_global_label_netclasses(
        label, ["RequiredB", "RequiredA", "DOC_existing"], inspection=snapshot, label_id=record.id
    ) == ["RequiredA", "RequiredB"]
    after = label.model_dump(mode="json")
    assert after["fields"][: len(before["fields"])] == before["fields"]
    assert {key: value for key, value in before.items() if key != "fields"} == {
        key: value for key, value in after.items() if key != "fields"
    }
    assert all(not field.visible for field in label.fields[-2:])
    reread = Schematic.from_string(sheet.to_string())
    saved_label = next(item for item in reread.labels if item.uuid == label.uuid)
    assert [field.value for field in saved_label.fields if field.name == "Netclass"] == [
        "DOC_existing",
        "RequiredA",
        "RequiredB",
    ]
    current = inspect(tree)
    assert (
        append_global_label_netclasses(
            label, ["RequiredA", "RequiredB"], inspection=current, label_id=record.id
        )
        == []
    )
    for net_id in current.labels[record.id].net_ids:
        assert {"DOC_existing", "RequiredA", "RequiredB"} <= set(current.nets[net_id].class_names)


def test_append_classes_rejects_stale_and_incomplete_inputs_without_mutation():
    import pytest

    from eedesigncore import append_global_label_netclasses

    tree = load_hierarchy(FIXTURE)
    tree.project.root.set("net_settings", {"netclass_patterns": []})
    label = next(
        label
        for label in tree.roots[0].children[0].schematic.labels
        if label.kind == "global_label"
    )
    snapshot = inspect(tree)
    record = next(
        record for record in snapshot.labels.values() if record.source_uuid == label.uuid
    )
    original = label.to_string() if hasattr(label, "to_string") else label.model_dump_json()
    with pytest.raises(ValueError, match="unresolved"):
        append_global_label_netclasses(
            label, ["${CLASS}"], inspection=snapshot, label_id=record.id
        )
    snapshot.class_coverage = "partial"
    with pytest.raises(ValueError, match="coverage"):
        append_global_label_netclasses(
            label, ["Required"], inspection=snapshot, label_id=record.id
        )
    snapshot.class_coverage = "complete"
    label.content = "changed"
    with pytest.raises(ValueError, match="differs"):
        append_global_label_netclasses(
            label, ["Required"], inspection=snapshot, label_id=record.id
        )
    label.content = record.text
    assert label.model_dump_json() == original


def test_appended_class_membership_matches_captured_native_export():
    import json

    from eedesigncore import append_global_label_netclasses

    oracle = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "schematic_classes" / "native_membership.json"
        ).read_text()
    )
    tree = load_hierarchy(FIXTURE)
    tree.project.root.set("net_settings", {"netclass_patterns": []})
    label = next(
        label
        for label in tree.roots[0].children[0].schematic.labels
        if label.kind == "global_label"
    )
    before = inspect(tree)
    record = next(record for record in before.labels.values() if record.source_uuid == label.uuid)
    append_global_label_netclasses(
        label, ["DOC_existing", "RequiredA", "RequiredB"], inspection=before, label_id=record.id
    )
    after = inspect(tree)
    classes = next(net.class_names for net in after.nets.values() if net.name == "GND")
    assert set(classes) == set(oracle["classes"]["GND"]) - {"Default"}


@pytest.mark.parametrize("lone_local_first", [True, False])
def test_a_global_label_names_a_net_it_shares_with_a_local_label(lone_local_first):
    """A local label mid-wire and a global label at its end make one global net.

    A second local label of the same name elsewhere on the sheet joins it, and
    the global label of that name on another sheet joins it too. KiCad names the
    whole net after the global label. In 0.39.0 the merge kept the local
    label's sheet-qualified name, so the net split from the other sheet's
    global label while coverage still read complete.
    """
    from eedesigncore import Label, LabelKind, Line, LineType

    tree = load_hierarchy(FIXTURE)
    root = tree.roots[0].schematic
    child = tree.roots[0].children[0].schematic

    def wire(start, end, uid):
        return Line(line_type=LineType.wire, start=start, end=end, uuid=uid)

    def label(kind, position, uid):
        return Label(kind=kind, content="ESP_EN", shape="passive", position=position, uuid=uid)

    # With the lone local label first, its net absorbs the other one. The
    # result must not depend on that order.
    lone = (
        [wire((200, 40), (210, 40), "w-b")],
        [label(LabelKind.local, (210, 40), "local-elsewhere")],
    )
    shared = (
        [wire((200, 20), (220, 20), "w-a")],
        [
            label(LabelKind.local, (210, 20), "local-mid-wire"),
            label(LabelKind.global_, (220, 20), "global-wire-end"),
        ],
    )
    first, second = (lone, shared) if lone_local_first else (shared, lone)
    root.lines[:0] = first[0] + second[0]
    root.labels[:0] = first[1] + second[1]
    child.lines.append(wire((200, 60), (210, 60), "w-c"))
    child.labels.append(label(LabelKind.global_, (210, 60), "global-other-sheet"))

    result = inspect(tree)
    names = {
        result.nets[net_id].name
        for item in result.labels.values()
        if item.source_uuid
        in ("local-mid-wire", "global-wire-end", "local-elsewhere", "global-other-sheet")
        for net_id in item.net_ids
    }
    assert names == {"ESP_EN"}
