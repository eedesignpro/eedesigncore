"""Unit tests for the schematic writer (T1 root skeleton, T2 lib_symbols + placed).

Assertions trace to the A2 sch-root-grammar report (Part 1 root order, Part 2.14
placed symbol, Part 2.12/lib-cache field-order delta) and the embed-context
report Part 2a (Lib:Name qualification) / Part 2b (placed field/emit order).
"""

from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import (
    InstancePath,
    InstanceProject,
    InstanceVariant,
    InstanceVariantField,
    PlacedSymbol,
    PlacedSymbolPin,
    Schematic,
)
from eedesigncore.sch.writer import (
    _dump_instances,
    _dump_lib_symbols,
    _dump_placed_symbol,
    dump_sch,
    write_sch,
)

# --- T1: header / root skeleton (A2 Part 1) ---------------------------------


def test_default_header_and_empty_skeleton() -> None:
    """An empty Schematic emits the always-present header + empty (lib_symbols)."""
    expected = (
        "(kicad_sch\n"
        "\t(version 20260306)\n"
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        "\t(lib_symbols)\n"
        ")\n"
    )
    assert write_sch(Schematic()) == expected


def test_root_order_uuid_paper_title_block_embedded_fonts() -> None:
    sch = Schematic(
        uuid="1234",
        paper=Paper(paper_type="A4"),
        title_block=TitleBlock(company="Example Co"),
        embedded_fonts=True,
    )
    out = write_sch(sch)
    # Emission order per Part 1: uuid -> paper -> title_block -> lib_symbols ->
    # (items) -> embedded_fonts.
    assert out.index("(uuid") < out.index("(paper") < out.index("(title_block")
    assert out.index("(title_block") < out.index("(lib_symbols") < out.index("(embedded_fonts")
    assert '(generator "eeschema")' in out
    assert "(embedded_fonts yes)" in out


def test_empty_title_block_omitted() -> None:
    sch = Schematic(title_block=TitleBlock())
    assert "(title_block" not in write_sch(sch)


# --- T2: Lib:Name qualification (embed-context Part 2a) ----------------------


def test_lib_symbols_top_level_name_qualified_units_bare() -> None:
    """Top-level lib_symbols entry keeps its full ``Lib:Name``; units stay bare."""
    sym = Symbol(name="Device:R", units=[SymbolUnit(name="R_0_1")])
    flat = _dump_lib_symbols(Schematic(lib_symbols=[sym]))
    assert '(symbol "Device:R"' in flat
    assert '(symbol "R_0_1"' in flat  # per-unit child name stays bare


# --- T2: placed PlacedSymbol per-field emit order (Part 2b) ------------------


def test_placed_symbol_field_emit_order() -> None:
    sym = PlacedSymbol(
        lib_id="Device:R",
        position=(10.0, 20.0),
        angle=90.0,
        mirror="y",
        unit=2,
        body_style=1,
        dnp=True,
        fields_autoplaced=True,
        uuid="abc",
        fields=[Property(name="Reference", value="R1", visible=False)],
        pins=[PlacedSymbolPin(number="1", uuid="pin-uuid")],
        instances=[
            InstanceProject(
                name="proj",
                paths=[InstancePath(path="/root", reference="R1", unit=2)],
            )
        ],
    )
    out = _dump_placed_symbol(sym)
    # Field/emit order: lib_id, at, mirror, unit, body_style, five bools,
    # fields_autoplaced, uuid, fields, pins, instances (Part 2b).
    order = [
        '(lib_id "Device:R")',
        "(at 10 20 90)",
        "(mirror y)",
        "(unit 2)",
        "(body_style 1)",
        "(exclude_from_sim no)",
        "(in_bom yes)",
        "(on_board yes)",
        "(in_pos_files yes)",
        "(dnp yes)",
        "(fields_autoplaced yes)",
        '(uuid "abc")',
        "(property",
        "(pin ",
        "(instances",
    ]
    positions = [out.index(tok) for tok in order]
    assert positions == sorted(positions), out


def test_placed_field_hide_precedes_show_name() -> None:
    """Placed saveField emits (hide) BEFORE (show_name) - unlike lib-cache order."""
    sym = PlacedSymbol(
        lib_id="Device:R",
        fields=[
            Property(name="Footprint", value="fp", visible=False, attributes=TextAttributes())
        ],
    )
    out = _dump_placed_symbol(sym)
    assert out.index("(hide yes)") < out.index("(show_name")
    assert out.index("(show_name") < out.index("(do_not_autoplace")


def test_placed_pin_and_instances_grammar() -> None:
    sym = PlacedSymbol(
        lib_id="Device:R",
        pins=[
            PlacedSymbolPin(number="1", uuid="u1"),
            PlacedSymbolPin(number="2", uuid="u2", alternate="ALT"),
        ],
        instances=[
            InstanceProject(
                name="proj",
                paths=[
                    InstancePath(
                        path="/sheet",
                        reference="R5",
                        unit=1,
                        variants=[InstanceVariant(name="02", on_board=True, in_pos_files=True)],
                    )
                ],
            )
        ],
    )
    out = _dump_placed_symbol(sym)
    assert '(pin "1" (uuid "u1") )' in out
    assert '(pin "2" (uuid "u2") (alternate "ALT") )' in out
    assert '(project "proj"' in out
    assert (
        '(path "/sheet" (reference "R5") (unit 1) '
        '(variant (name "02") (on_board yes) (in_pos_files yes) ) )'
    ) in out


# --- T6: placed-symbol instance variant (nested LIST; saveSymbol :949-977) ---


def test_instance_variant_emits_present_bools_in_order() -> None:
    """Only bools present on the model emit, in saveSymbol :953-968 order."""
    var = InstanceVariant(
        name="02",
        dnp=True,
        exclude_from_sim=False,
        in_bom=True,
        on_board=True,
        in_pos_files=False,
    )
    path = InstancePath(path="/p", reference="R1", unit=1, variants=[var])
    out = _dump_instances([InstanceProject(name="proj", paths=[path])])
    frag = '(variant (name "02") (dnp yes) (exclude_from_sim no) (in_bom yes) '
    frag += "(on_board yes) (in_pos_files no) )"
    assert frag in out


def test_instance_variant_omits_absent_bools() -> None:
    """A variant carrying only (name) emits no bool tokens (all None)."""
    path = InstancePath(path="/p", reference="R1", unit=1, variants=[InstanceVariant(name="alt")])
    out = _dump_instances([InstanceProject(name="proj", paths=[path])])
    assert '(variant (name "alt") )' in out


def test_instance_variant_multiple_per_path() -> None:
    """A path may carry more than one (variant …) block (m_Variants map)."""
    path = InstancePath(
        path="/p",
        reference="R1",
        unit=1,
        variants=[InstanceVariant(name="01"), InstanceVariant(name="02")],
    )
    out = _dump_instances([InstanceProject(name="proj", paths=[path])])
    assert out.index('(name "01")') < out.index('(name "02")')
    assert out.count("(variant") == 2


def test_instance_variant_fields_round_trip() -> None:
    """Variant (field …) entries emit as (field (name …) (value …))."""
    var = InstanceVariant(name="02", fields=[InstanceVariantField(name="MPN", value="ACME-1")])
    path = InstancePath(path="/p", reference="R1", unit=1, variants=[var])
    out = _dump_instances([InstanceProject(name="proj", paths=[path])])
    assert '(field (name "MPN") (value "ACME-1"))' in out


# --- item stream sort (A2 Part 3) -------------------------------------------


def test_symbols_emitted_uuid_sorted() -> None:
    sch = Schematic(
        symbols=[
            PlacedSymbol(lib_id="Device:R", uuid="ffff"),
            PlacedSymbol(lib_id="Device:R", uuid="0000"),
        ]
    )
    flat = dump_sch(sch)
    assert flat.index('(uuid "0000")') < flat.index('(uuid "ffff")')
