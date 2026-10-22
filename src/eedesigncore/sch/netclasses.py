"""Append explicit class memberships without replacing existing label fields."""

from __future__ import annotations

from collections.abc import Sequence

from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.text_variables import has_reference
from eedesigncore.sch.models import Label
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def append_global_label_netclasses(
    label: Label,
    class_names: Sequence[str],
    *,
    inspection: SchematicElectricalSnapshot,
    label_id: str,
) -> list[str]:
    """Append missing literal Netclass fields to one explicitly selected label.

    Existing fields, graphics, and project patterns remain unchanged. New fields
    are hidden. Inspection must describe the current label and complete class
    membership. The caller owns engineering choices and repeated-file conflicts.
    Reinspect after any name, connection, or class edit and before saving.
    """
    if isinstance(class_names, str | bytes) or len(class_names) > 256:
        raise ValueError("Supply at most 256 explicit class names.")
    for name in class_names:
        if not isinstance(name, str) or not name or len(name) > 4096 or name != name.strip():
            raise ValueError("Class names require nonempty literal text without outer whitespace.")
        if any(ord(char) < 32 for char in name) or has_reference(name):
            raise ValueError(
                "Class names cannot contain control characters or unresolved variables."
            )
    requested = sorted(set(class_names))
    if inspection.coverage.electrical != "complete" or inspection.class_coverage != "complete":
        raise ValueError("Class edits require complete electrical and class coverage.")
    record = inspection.labels.get(label_id)
    if record is None or label.kind != "global_label" or record.kind != "global_label":
        raise ValueError("Select a resolved global label from the inspection.")
    if not label.uuid or label.uuid != record.source_uuid or label.content != record.source_text:
        raise ValueError("The label identity or text differs from the inspection.")
    fields = [(field.name, field.value) for field in label.fields]
    if fields != record.source_field_values or has_reference(label.content):
        raise ValueError("The label fields differ from the inspection or use unresolved text.")
    if record.bus_ids or len(record.net_ids) != 1 or record.net_ids[0] not in inspection.nets:
        raise ValueError("Class edits require one resolved scalar net.")
    if any(
        has_reference(field.value)
        for field in label.fields
        if field.name in {"Netclass", "Net Class"}
    ):
        raise ValueError("Existing class fields contain unresolved variables.")
    existing = {field.value for field in label.fields if field.name in {"Netclass", "Net Class"}}
    added = [name for name in requested if name not in existing]
    new_fields = [
        Property(
            name="Netclass",
            value=name,
            position=label.position,
            visible=False,
            attributes=TextAttributes(),
        )
        for name in added
    ]
    label.fields.extend(new_fields)
    return added
