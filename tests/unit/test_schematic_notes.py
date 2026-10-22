from pathlib import Path

import pytest

from eedesigncore import (
    Schematic,
    SchTextBox,
    create_layout_note,
    create_schematic_note,
    create_todo_note,
    parse_note_block,
    parse_schematic_notes,
    update_note_block,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/schematic_notes/template_notes.kicad_sch"


@pytest.mark.parametrize(
    "create,kind",
    [
        (create_schematic_note, "schematic"),
        (create_layout_note, "layout"),
        (create_todo_note, "todo"),
    ],
)
def test_template_style_grid_and_native_round_trip(create, kind):
    templates = Schematic.from_file(FIXTURE)
    original = next(b for b in templates.text_boxes if parse_note_block(b).kind == kind)
    box = create(
        "Human title",
        "Keep ${PROJECT_TITLE}\nAI and human text\n",
        at=(-0.635, 10.1),
        size=(50.1, 31.1),
    )
    assert box.stroke == original.stroke
    assert box.fill == original.fill
    assert box.attributes == original.attributes
    assert box.margins == original.margins
    assert box.position == (-1.27, 10.16)
    assert all(v / 1.27 == pytest.approx(round(v / 1.27)) for v in (*box.position, *box.size))
    loaded = Schematic.from_string(Schematic(text_boxes=[box]).to_string()).text_boxes[0]
    note = parse_note_block(loaded)
    assert note.kind == kind
    assert note.title == "Human title"
    assert note.text == "Keep ${PROJECT_TITLE}\nAI and human text\n"
    assert not note.diagnostics
    assert note.uuid == box.uuid
    with pytest.raises(TypeError):
        create("Title", "Text", at=(0, 0), fill_color="red")


def test_legacy_templates_remain_readable_without_invented_titles():
    sheet = Schematic.from_file(FIXTURE)
    before = sheet.model_dump()
    notes = parse_schematic_notes(sheet)
    assert {n.kind for n in notes} == {"schematic", "layout", "todo"}
    assert all(n.title == "" and n.text.startswith("<<TEMPLATE>>") for n in notes)
    assert all(not n.diagnostics for n in notes)
    assert sheet.model_dump() == before


def test_human_edits_are_reported_and_update_is_detached():
    box = create_layout_note("Place", "Keep near connector.", at=(0, 0))
    box.fill.color.r = 1
    box.position = (0.2, 0.3)
    box.exclude_from_sim = True
    before = box.model_dump()
    note = parse_note_block(box)
    assert len(note.diagnostics) == 2
    updated = update_note_block(box, title="Updated", text="New text")
    assert updated.uuid == box.uuid
    assert updated.exclude_from_sim
    assert not parse_note_block(updated).diagnostics
    assert box.model_dump() == before


@pytest.mark.parametrize(
    "content",
    [
        "TODO:\n\nMarker removed",
        "${ERC_ERROR TODO ### broken}TODO:\n\nText",
    ],
)
def test_damaged_markers_are_not_silently_omitted(content):
    with pytest.raises(ValueError, match="marker"):
        parse_schematic_notes(Schematic(text_boxes=[SchTextBox(content=content)]))
    assert parse_note_block(SchTextBox(content="Ordinary note")) is None


@pytest.mark.parametrize(
    "options",
    [
        {"at": (float("nan"), 0)},
        {"at": (0, 0), "size": (0, 10)},
        {"at": (0, 0), "size": (float("inf"), 10)},
    ],
)
def test_invalid_geometry(options):
    with pytest.raises(ValueError):
        create_todo_note("Title", "Text", **options)


def test_small_positive_dimensions_keep_one_grid_cell():
    box = create_todo_note("Title", "Text", at=(0, 0), size=(1e-12, 1e-12))
    assert box.size == (1.27, 1.27)
