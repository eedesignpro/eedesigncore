"""Unit tests for the sheet node and sheet-instance blocks (T4).

Assertions trace to the A1 sheet-instance-grammar report (root `saveInstances`
:1646-1666 vs per-sheet `saveSheet` :1127-1238) and the A2 sch-root-grammar
report (saveSheet token order :1069-1241; item-stream sort Part 3 /
typeinfo.h:179 SCH_SHEET_T). Sheet-variant grammar is source-cited only (no
corpus file carries a `(variant …)`; A1 open-question 1).
"""

from eedesigncore.common.models import Color, Property, Stroke, TextAttributes
from eedesigncore.sch.models import (
    PlacedSymbol,
    RootInstancePath,
    Schematic,
    Sheet,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
    SheetPin,
    SheetVariant,
    SheetVariantField,
)
from eedesigncore.sch.writer import (
    _dump_per_sheet_instances,
    _dump_root_sheet_instances,
    _dump_sheet,
    write_sch,
)

# --- Sheet node token order (A2 saveSheet :1069-1241) -----------------------


def test_sheet_token_order() -> None:
    sheet = Sheet(
        position=(10.0, 20.0),
        size=(30.0, 40.0),
        fields_autoplaced=True,
        stroke=Stroke(width=0.1524),
        fill_color=Color(r=0, g=0, b=0, a=0.0),
        uuid="sheet-uuid",
        fields=[Property(name="Sheetname", value="GNSS")],
        pins=[
            SheetPin(name="TX", shape="output", position=(1.0, 2.0), angle=0.0, uuid="pin-uuid")
        ],
    )
    out = _dump_sheet(sheet)
    # at -> size -> exclude_from_sim -> in_bom -> on_board -> dnp ->
    # fields_autoplaced -> stroke -> fill -> uuid -> property -> pin.
    order = [
        "(at ",
        "(size ",
        "(exclude_from_sim ",
        "(in_bom ",
        "(on_board ",
        "(dnp ",
        "(fields_autoplaced yes)",
        "(stroke",
        "(fill (color ",
        "(uuid ",
        '(property "Sheetname"',
        '(pin "TX" output',
    ]
    positions = [out.index(tok) for tok in order]
    assert positions == sorted(positions)


def test_sheet_fill_is_color_only() -> None:
    # Sheet background fill is (fill (color …)) — NOT the shape (fill (type …)) grammar.
    out = _dump_sheet(Sheet(fill_color=Color(r=1, g=2, b=3, a=0.5)))
    assert "(fill (color 1 2 3 0.5))" in out
    assert "(fill (type" not in out


def test_sheet_sorts_after_symbol_in_item_stream() -> None:
    # SCH_SHEET_T (typeinfo.h:179) sorts after SCH_SYMBOL_T (176) / SCH_GROUP_T (177).
    sch = Schematic(
        symbols=[PlacedSymbol(lib_id="Device:R", uuid="zzzz")],
        sheets=[Sheet(uuid="aaaa")],
    )
    out = write_sch(sch)
    assert out.index("(symbol") < out.index("(sheet")


# --- Root (sheet_instances) vs per-sheet (instances) (A1 §1 vs §3) ----------


def test_root_sheet_instances_flat_no_project() -> None:
    inst = SheetInstances(paths=[RootInstancePath(path="/", page="1")])
    out = _dump_root_sheet_instances(inst)
    assert '(sheet_instances (path "/" (page "1"))' in out
    # The root block never wraps in (project …) nor carries (variant …) (A1 §1).
    assert "(project" not in out
    assert "(variant" not in out


def test_root_empty_path_fallback_literal_slash() -> None:
    # The root sheet's own instance serializes its empty KIID path as "/" (:1656).
    inst = SheetInstances(paths=[RootInstancePath(path="/", page="3")])
    assert '(path "/"' in _dump_root_sheet_instances(inst)


def test_per_sheet_instances_wrapped_in_project_full_path() -> None:
    projects = [
        SheetInstanceProject(
            name="MyProject",
            paths=[SheetInstancePath(path="/e8858e08-1446", page="12")],
        )
    ]
    out = _dump_per_sheet_instances(projects)
    # Per-sheet block: (project "name" …), full KIID path (NOT "/"), no fallback.
    assert '(instances (project "MyProject" (path "/e8858e08-1446" (page "12")' in out
    assert '(path "/"' not in out


def test_root_and_per_sheet_are_distinct_grammars() -> None:
    # Same path/page vocabulary, structurally different output (A1 classification).
    root = _dump_root_sheet_instances(
        SheetInstances(paths=[RootInstancePath(path="/p", page="1")])
    )
    per = _dump_per_sheet_instances(
        [SheetInstanceProject(name="P", paths=[SheetInstancePath(path="/p", page="1")])]
    )
    assert "(project" not in root
    assert "(project" in per


# --- Sheet-variant grammar (A1 §3d, source-cited only) ----------------------


def test_variant_emits_only_present_bools_and_fields() -> None:
    variant = SheetVariant(
        name="Assembly",
        dnp=True,
        in_bom=False,
        fields=[SheetVariantField(name="MPN", value="ABC123")],
    )
    path = SheetInstancePath(path="/p", page="2", variants=[variant])
    out = _dump_per_sheet_instances([SheetInstanceProject(name="P", paths=[path])])
    # dnp + in_bom present; exclude_from_sim omitted (None); on_board/in_pos_files
    # never emitted by the 10.0.3 writer (A1 §4c).
    assert '(variant (name "Assembly") (dnp yes) (in_bom no)' in out
    assert '(field (name "MPN") (value "ABC123"))' in out
    assert "(exclude_from_sim" not in out
    assert "(on_board" not in out
    assert "(in_pos_files" not in out


def test_variant_in_bom_is_positive_on_disk_value() -> None:
    # in_bom is the literal on-disk value (no ExcludedFromBOM inversion in this layer).
    variant = SheetVariant(name="V", in_bom=True)
    out = _dump_per_sheet_instances(
        [
            SheetInstanceProject(
                name="P",
                paths=[SheetInstancePath(path="/p", page="1", variants=[variant])],
            )
        ]
    )
    assert "(in_bom yes)" in out


# --- Round-trip through the full writer + loader ----------------------------


def test_sheet_roundtrip_via_dump_and_load() -> None:
    sheet = Sheet(
        position=(44.45, 158.75),
        size=(38.1, 25.4),
        stroke=Stroke(width=0.1524),
        uuid="218c6744-2971-47e2-8d2b-7028f4645a86",
        fields=[Property(name="Sheetfile", value="gnss.kicad_sch")],
        pins=[
            SheetPin(
                name="TX",
                shape="output",
                position=(82.55, 161.29),
                angle=0.0,
                uuid="3ff1ed30",
                attributes=TextAttributes(),
            )
        ],
        instances=[
            SheetInstanceProject(
                name="Proj",
                paths=[SheetInstancePath(path="/e8858e08", page="3")],
            )
        ],
    )
    sch = Schematic(uuid="root", paper=None, sheets=[sheet])
    text = write_sch(sch)
    reloaded = Schematic.from_string(text)
    assert len(reloaded.sheets) == 1
    rs = reloaded.sheets[0]
    assert rs.position == (44.45, 158.75)
    assert rs.size == (38.1, 25.4)
    assert rs.uuid == "218c6744-2971-47e2-8d2b-7028f4645a86"
    assert rs.fields[0].name == "Sheetfile"
    assert rs.fields[0].value == "gnss.kicad_sch"
    assert rs.pins[0].name == "TX"
    assert rs.pins[0].shape == "output"
    assert rs.instances[0].name == "Proj"
    assert rs.instances[0].paths[0].path == "/e8858e08"
    assert rs.instances[0].paths[0].page == "3"
    # Idempotent re-emit.
    assert write_sch(reloaded) == text


def test_sheetfile_reference_preserved_as_data() -> None:
    # Single-file scope (ADR-040): the sub-sheet file reference is stored, not resolved.
    sheet = Sheet(fields=[Property(name="Sheetfile", value="child.kicad_sch")], uuid="s")
    sch = Schematic(sheets=[sheet])
    reloaded = Schematic.from_string(write_sch(sch))
    assert reloaded.sheets[0].fields[0].value == "child.kicad_sch"


def test_variant_parser_tolerance_on_board_in_pos_files() -> None:
    # Parser accepts on_board / in_pos_files inside a variant (A1 §4b) even though
    # the writer never emits them; loading must not raise.
    text = (
        "(kicad_sch\n"
        "\t(version 20260306)\n"
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        "\t(lib_symbols)\n"
        "\t(sheet\n"
        "\t\t(at 0 0)\n"
        "\t\t(size 1 1)\n"
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(dnp no)\n"
        "\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(color 0 0 0 0)\n\t\t)\n"
        '\t\t(uuid "s")\n'
        "\t\t(instances\n"
        '\t\t\t(project "P"\n'
        '\t\t\t\t(path "/p"\n'
        '\t\t\t\t\t(page "1")\n'
        '\t\t\t\t\t(variant (name "V")\n'
        "\t\t\t\t\t\t(on_board yes)\n"
        "\t\t\t\t\t\t(in_pos_files no)\n"
        "\t\t\t\t\t)\n"
        "\t\t\t\t)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )
    sch = Schematic.from_string(text)
    variant = sch.sheets[0].instances[0].paths[0].variants[0]
    assert variant.name == "V"
    assert variant.on_board is True
    assert variant.in_pos_files is False
