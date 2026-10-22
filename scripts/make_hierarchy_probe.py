"""Generate a probe project that isolates one HIERARCHY rule per resistor.

The flat probe (``make_connectivity_probe.py``) covers what happens on one
sheet. This covers what happens between sheets: a sheet pin against a
hierarchical label, a bus crossing a boundary, a bus whose two sides carry
different group names, a global name reaching across with no wire at all, and one
sheet file instantiated twice.

Same discipline as the flat probe. Every case is one or more resistors with pin 2
grounded and pin 1 at a probe point. Ground is the only net two cases share, so
one wrong rule cannot mask another. Where a case expects two resistors to land on
ONE net, the netlist either shows them together or it does not, and there is
nothing to interpret.

Run this, then export with KiCad's own tool and compare:

    kicad-cli sch export netlist --format kicadsexpr -o probe.net <root>.kicad_sch
"""

from __future__ import annotations

import argparse
from pathlib import Path

from make_connectivity_probe import (
    PIN1_DY,
    PIN2_DY,
    Result,
    Sheet,
    Uuids,
    intent,
    num,
    render,
)

PROJECT = "probe-hierarchy-rules"
ROOT_UUID = "10000000-0000-4000-8000-000000000000"

# Root sheet layout. One column per case, wide enough for a four-member bus.
COL_W = 101.6
ORIGIN = (76.2, 63.5)
SHEET_W = 50.8
SHEET_H = 63.5

# A sheet pin's vertical pitch down the left edge of a sheet symbol.
PIN_PITCH = 12.7
FIRST_PIN_DY = 12.7

# How far left of a sheet symbol the parent-side probe geometry sits.
LEG = 25.4
# How far a bus member hangs below its bus.
DROP = 20.32


class Refs:
    """One reference counter for the whole project, so nothing collides."""

    def __init__(self) -> None:
        self.r = 0
        self.pwr = 0

    def resistor(self) -> str:
        self.r += 1
        return f"R{self.r}"

    def power(self) -> str:
        self.pwr += 1
        return f"#PWR{self.pwr:02d}"


def leg_left(sheet: Sheet, refs: Refs, anchor: tuple[float, float], label: str | None) -> str:
    """A resistor to the LEFT of *anchor*, wired across to it. Returns its reference."""
    px = anchor[0] - LEG
    ref = refs.resistor()
    pin1 = sheet.resistor(ref, (px, anchor[1] + PIN2_DY))
    sheet.ground(refs.power(), (px, anchor[1] + PIN2_DY + PIN2_DY))
    sheet.wire(pin1, anchor)
    if label:
        sheet.label(label, pin1)
    return ref


def leg_below(sheet: Sheet, refs: Refs, anchor: tuple[float, float], label: str | None) -> str:
    """A resistor BELOW *anchor*, wired up to it. Returns its reference."""
    ref = refs.resistor()
    at = (anchor[0], anchor[1] + DROP - PIN1_DY)
    pin1 = sheet.resistor(ref, at)
    sheet.ground(refs.power(), (anchor[0], at[1] + PIN2_DY))
    sheet.wire(anchor, pin1)
    if label:
        sheet.label(label, pin1)
    return ref


def bus_with_members(
    sheet: Sheet,
    refs: Refs,
    anchor: tuple[float, float],
    members: list[str | None],
    bus_label: str,
    *,
    kind: str = "label",
) -> list[str]:
    """A bus running left from *anchor*, with one hanging member leg per entry.

    A member whose label is ``None`` gets a leg and NO label, which is how a
    case asks whether the bus alone can name that wire.
    """
    left = anchor[0] - 12.7 - 7.62 * len(members)
    sheet.bus((left, anchor[1]), anchor)
    sheet.label(bus_label, (left, anchor[1]), kind=kind)
    out: list[str] = []
    for index, member in enumerate(members):
        entry_x = left + 7.62 * (index + 1)
        sheet.bus_entry((entry_x, anchor[1]), (2.54, 2.54))
        out.append(leg_below(sheet, refs, (entry_x + 2.54, anchor[1] + 2.54), member))
    return out


def sheet_symbol(
    sheet: Sheet,
    *,
    at: tuple[float, float],
    name: str,
    filename: str,
    pins: list[str],
    uuid: str,
    page: int,
) -> list[tuple[float, float]]:
    """Place a sheet symbol and return each pin's connection point, in order."""
    x, y = at
    lines = [
        "\t(sheet",
        f"\t\t(at {num(x)} {num(y)})",
        f"\t\t(size {num(SHEET_W)} {num(SHEET_H)})",
        "\t\t(exclude_from_sim no)",
        "\t\t(in_bom yes)",
        "\t\t(on_board yes)",
        "\t\t(dnp no)",
        "\t\t(fields_autoplaced yes)",
        "\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)",
        "\t\t(fill\n\t\t\t(color 0 0 0 0)\n\t\t)",
        f'\t\t(uuid "{uuid}")',
        f'\t\t(property "Sheetname" "{name}"\n'
        f"\t\t\t(at {num(x)} {num(y - 1.3366)} 0)\n"
        "\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        "\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)",
        f'\t\t(property "Sheetfile" "{filename}"\n'
        f"\t\t\t(at {num(x)} {num(y + SHEET_H + 1.3366)} 0)\n"
        "\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        "\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)",
    ]
    points: list[tuple[float, float]] = []
    for index, pin_name in enumerate(pins):
        pin_y = y + FIRST_PIN_DY + PIN_PITCH * index
        points.append((x, pin_y))
        lines.append(
            f'\t\t(pin "{pin_name}" bidirectional\n'
            f"\t\t\t(at {num(x)} {num(pin_y)} 180)\n"
            f'\t\t\t(uuid "{sheet.uuids.take()}")\n'
            "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
            "\t\t\t\t(justify left)\n\t\t\t)\n\t\t)"
        )
    lines.append(
        "\t\t(instances\n"
        f'\t\t\t(project "{PROJECT}"\n'
        f'\t\t\t\t(path "/{ROOT_UUID}"\n'
        f'\t\t\t\t\t(page "{page}")\n'
        "\t\t\t\t)\n"
        "\t\t\t)\n"
        "\t\t)"
    )
    lines.append("\t)")
    sheet.symbols.append("\n".join(lines))
    return points


class Child:
    """One child schematic under construction."""

    def __init__(self, uuids: Uuids, refs: Refs, name: str, filename: str, sheet_uuid: str):
        self.name = name
        self.filename = filename
        self.sheet_uuid = sheet_uuid
        self.uuid = f"2{sheet_uuid[1:]}"
        self.sheet = Sheet(
            uuids=uuids,
            inst_path=f"/{ROOT_UUID}/{sheet_uuid}",
            project=PROJECT,
        )
        self.refs = refs

    def anchor(self, index: int) -> tuple[float, float]:
        """Where the hierarchical label for pin *index* goes, in child space."""
        return (76.2, 63.5 + PIN_PITCH * 3 * index)

    def write(self, out: Path) -> None:
        (out / self.filename).write_text(
            render(self.sheet, [], uuid=self.uuid, title=self.name, root=False),
            encoding="utf-8",
        )


def build(out: Path) -> list[Result]:  # noqa: C901, PLR0915
    uuids = Uuids()
    refs = Refs()
    root = Sheet(uuids=uuids, inst_path=f"/{ROOT_UUID}", project=PROJECT)
    children: list[Child] = []
    results: list[Result] = []
    column = 0
    page = 1

    def new_child(name: str) -> Child:
        nonlocal page
        page += 1
        sheet_uuid = f"1{uuids.take()[1:]}"
        child = Child(uuids, refs, name, f"{name.lower().replace(' ', '_')}.kicad_sch", sheet_uuid)
        children.append(child)
        return child

    def place(name: str, pins: list[str]) -> tuple[Child, list[tuple[float, float]]]:
        nonlocal column
        child = new_child(name)
        at = (ORIGIN[0] + column * COL_W, ORIGIN[1])
        column += 1
        points = sheet_symbol(
            root,
            at=at,
            name=child.name,
            filename=child.filename,
            pins=pins,
            uuid=child.sheet_uuid,
            page=page,
        )
        return child, points

    def place_again(
        name: str, pins: list[str], existing: Child
    ) -> tuple[str, list[tuple[float, float]]]:
        """A SECOND sheet symbol pointing at an existing child's file.

        Returns the new sheet identifier, which the child's symbols need so each
        placement resolves to its own reference. No new child file is written,
        because the point of the case is that one file is placed twice.
        """
        nonlocal column, page
        page += 1
        sheet_uuid = f"1{uuids.take()[1:]}"
        at = (ORIGIN[0] + column * COL_W, ORIGIN[1])
        column += 1
        points = sheet_symbol(
            root,
            at=at,
            name=name,
            filename=existing.filename,
            pins=pins,
            uuid=sheet_uuid,
            page=page,
        )
        return sheet_uuid, points

    def add(key: str, question: str, drawn: str, predicted: str) -> None:
        results.append(Result(key=key, question=question, drawn=drawn, predicted=predicted))

    # ---- H1. one signal across one boundary ----------------------------
    child, points = place("H1 SIMPLE", ["H_SIG"])
    parent_ref = leg_left(root, refs, points[0], None)
    anchor = child.anchor(0)
    child.sheet.label("H_SIG", anchor, kind="hierarchical_label")
    child_ref = leg_below(child.sheet, refs, anchor, None)
    add(
        "H1_SIMPLE",
        "Does a sheet pin join its child's hierarchical label of the same name?",
        f"Parent: {parent_ref} wired to sheet pin H_SIG. Child: hierarchical label"
        f" H_SIG wired to {child_ref}. No label on either resistor's own wire.",
        f"one net holding {parent_ref} and {child_ref}, named /H1 SIMPLE/H_SIG."
        " The child's hierarchical label outranks the parent's sheet pin, so the"
        " child's path wins. Control case.",
    )

    # ---- H2. a bus across one boundary, same name both sides ------------
    child, points = place("H2 BUS SAME", ["PB[0..1]"])
    parent_bus = leg_left  # keep the linter honest about intent
    del parent_bus
    p_refs = bus_with_members(root, refs, points[0], ["PB0", "PB1"], "PB[0..1]")
    anchor = child.anchor(0)
    child.sheet.label("PB[0..1]", anchor, kind="hierarchical_label")
    c_refs = bus_with_members(
        child.sheet, refs, (anchor[0] + 63.5, anchor[1]), ["PB0", "PB1"], "PB[0..1]"
    )
    child.sheet.bus(anchor, (anchor[0] + 63.5, anchor[1]))
    add(
        "H2_BUS_SAME",
        "Do bus members pair up across a boundary when both sides use one name?",
        f"Parent: bus PB[0..1] on sheet pin PB[0..1], members PB0 and PB1 to"
        f" {' and '.join(p_refs)}. Child: hierarchical label PB[0..1], the same two"
        f" member names to {' and '.join(c_refs)}.",
        f"two nets. PB0 holds {p_refs[0]} and {c_refs[0]}; PB1 holds {p_refs[1]}"
        f" and {c_refs[1]}. Control case for the bus machinery.",
    )

    # ---- H3. a bus across two boundaries, DIFFERENT group names ---------
    child_a, points_a = place("H3 SIDE A", ["TP1{SCL SDA}"])
    child_b, points_b = place("H3 SIDE B", ["AK_TMP{SCL SDA}"])
    # ONE parent bus carrying BOTH sheet pins, which is the shape that fails.
    root.bus(points_a[0], (points_b[0][0], points_a[0][1]))
    root.bus((points_b[0][0], points_a[0][1]), points_b[0])
    a_refs = []
    for child, pin_name, prefix in (
        (child_a, "TP1{SCL SDA}", "TP1"),
        (child_b, "AK_TMP{SCL SDA}", "AK_TMP"),
    ):
        anchor = child.anchor(0)
        child.sheet.label(pin_name, anchor, kind="hierarchical_label")
        child.sheet.bus(anchor, (anchor[0] + 63.5, anchor[1]))
        a_refs.append(
            bus_with_members(
                child.sheet,
                refs,
                (anchor[0] + 63.5, anchor[1]),
                [f"{prefix}.SCL", f"{prefix}.SDA"],
                pin_name,
            )
        )
    add(
        "H3_BUS_DIFFERENT_NAMES",
        "Do bus MEMBERS pair up when the two buses carry different GROUP names?",
        "One parent bus wire carrying TWO sheet pins with different names,"
        " TP1{SCL SDA} to side A and AK_TMP{SCL SDA} to side B. Side A labels its"
        f" member wires TP1.SCL and TP1.SDA to {' and '.join(a_refs[0])}. Side B"
        f" labels its own AK_TMP.SCL and AK_TMP.SDA to {' and '.join(a_refs[1])}.",
        f"two nets, not four. One holds {a_refs[0][0]} and {a_refs[1][0]}, the SCL"
        f" pair; the other holds {a_refs[0][1]} and {a_refs[1][1]}, the SDA pair."
        " Group members pair by LOCAL name, so SCL finds SCL even though the group"
        " names differ. THIS IS THE FAILING CASE in production: the engine keeps"
        " them apart.",
    )

    # ---- H4. a GLOBAL bus label, reaching across with no wire -----------
    child, points = place("H4 GLOBAL BUS", [])
    g_parent = bus_with_members(
        root,
        refs,
        (ORIGIN[0] + (column - 1) * COL_W - 12.7, ORIGIN[1] + SHEET_H + 38.1),
        ["GB.SDA", "GB.SCL"],
        "GB{SDA SCL}",
        kind="global_label",
    )
    anchor = child.anchor(0)
    child.sheet.bus(anchor, (anchor[0] + 63.5, anchor[1]))
    child.sheet.label("GB{SDA SCL}", anchor, kind="global_label")
    g_child = bus_with_members(
        child.sheet, refs, (anchor[0] + 63.5, anchor[1]), ["GB.SDA", "GB.SCL"], "GB{SDA SCL}"
    )
    add(
        "H4_GLOBAL_BUS",
        "Does a GLOBAL bus label reach across sheets with no wire, and is its"
        " member name sheet-qualified?",
        "A global bus label GB{SDA SCL} on the root, and the SAME global bus label"
        " inside a child with NO sheet pin between them. Member wires labelled"
        f" GB.SDA and GB.SCL on both sides: {', '.join(g_parent)} on the root and"
        f" {', '.join(g_child)} in the child.",
        f"two nets. GB.SDA holds {g_parent[0]} and {g_child[0]}; GB.SCL holds"
        f" {g_parent[1]} and {g_child[1]}. The names carry NO leading path, because"
        " a global name is not scoped. Production evidence says unqualified; the"
        " engine qualifies them, so this is expected to FAIL.",
    )

    # ---- H5. one sheet FILE instantiated twice --------------------------
    twin_a, points_a = place("H5 TWIN A", ["T_IN"])
    # The second placement of the SAME file. Its sheet identifier is needed
    # before the child's symbols are built, because each symbol must record one
    # instance per placement.
    twin_b_uuid, points_b = place_again("H5 TWIN B", ["T_IN"], twin_a)
    twin_b_path = f"/{ROOT_UUID}/{twin_b_uuid}"

    def twin_leg(anchor: tuple[float, float], label: str | None, kind: str) -> tuple[str, str]:
        """One resistor in the twice-placed child, with a reference per placement.

        Mirrors ``leg_below``, but every symbol records TWO instances, so each
        placement resolves to its own component. A single instance would leave
        the second placement unresolved.
        """
        ref_a = refs.resistor()
        ref_b = refs.resistor()
        at = (anchor[0], anchor[1] + DROP - PIN1_DY)
        pin1 = twin_a.sheet.resistor(ref_a, at, extra_instances=[(twin_b_path, ref_b)])
        twin_a.sheet.ground(
            refs.power(),
            (anchor[0], at[1] + PIN2_DY),
            extra_instances=[(twin_b_path, refs.power())],
        )
        twin_a.sheet.wire(anchor, pin1)
        if label:
            twin_a.sheet.label(label, pin1, kind=kind)
        return ref_a, ref_b

    # Leg one: reached from the parent through the sheet pin.
    up_anchor = twin_a.anchor(0)
    twin_a.sheet.label("T_IN", up_anchor, kind="hierarchical_label")
    up_a, up_b = twin_leg(up_anchor, None, "label")

    # Leg two: a LOCAL label on its own net, touching nothing else. This is what
    # tests scoping, and keeping it off the first net means the two questions do
    # not interfere.
    local_anchor = (up_anchor[0] + 50.8, up_anchor[1])
    local_a, local_b = twin_leg(local_anchor, "T_LOCAL", "label")

    p_a = leg_left(root, refs, points_a[0], None)
    p_b = leg_left(root, refs, points_b[0], None)
    add(
        "H5_REPEATED_SHEET",
        "Does one sheet FILE instantiated twice give two nets or one?",
        f"Two sheet symbols, H5 TWIN A and H5 TWIN B, both pointing at the SAME"
        f" file {twin_a.filename}. Inside it, two separate nets: a hierarchical"
        f" label T_IN on one, and a LOCAL label T_LOCAL on the other, touching"
        f" nothing else. Each of the two resistors inside resolves to a different"
        f" reference per placement, {up_a} and {up_b} on the first net and"
        f" {local_a} and {local_b} on the second. On the root, {p_a} feeds one"
        f" placement and {p_b} the other.",
        f"FOUR nets, not two. The sheet-pin net splits per placement: one holds"
        f" {up_a} with {p_a}, the other {up_b} with {p_b}. The local-label net"
        f" splits too: one holds {local_a} alone, the other {local_b} alone. Any"
        f" merging here would mean placement identity was lost.",
    )

    # ---- H6. two sheets sharing a sheet pin NAME ------------------------
    dup_a, points_a = place("H6 DUP A", ["DUP"])
    dup_b, points_b = place("H6 DUP B", ["DUP"])
    dup_refs = []
    for child, points in ((dup_a, points_a), (dup_b, points_b)):
        anchor = child.anchor(0)
        child.sheet.label("DUP", anchor, kind="hierarchical_label")
        dup_refs.append(leg_below(child.sheet, refs, anchor, None))
        dup_refs.append(leg_left(root, refs, points[0], None))
    add(
        "H6_SHARED_PIN_NAME",
        "Do two different sheets with the same sheet pin name stay separate?",
        "Two sheet symbols, each with a sheet pin named DUP on its own parent wire,"
        " and each child with a hierarchical label DUP on its own wire. Four"
        f" resistors in all: {', '.join(dup_refs)}.",
        "TWO nets of two resistors each, kept apart by which sheet each pin opens"
        " onto. One net of four would mean the child link was matched by name"
        " alone.",
    )

    # ---- H7. a global LABEL crossing with no wire -----------------------
    glob_a, _ = place("H7 GLOB A", [])
    glob_b, _ = place("H7 GLOB B", [])
    glob_refs = []
    for child in (glob_a, glob_b):
        anchor = child.anchor(0)
        child.sheet.label("H_GLOB", anchor, kind="global_label")
        glob_refs.append(leg_below(child.sheet, refs, anchor, None))
    add(
        "H7_GLOBAL_LABEL",
        "Does a global label join two sheets with no wire and no sheet pin?",
        f"Two sheets with NO sheet pins at all. Each holds a global label H_GLOB"
        f" wired to a resistor: {' and '.join(glob_refs)}.",
        f"one net named H_GLOB, unqualified, holding both {glob_refs[0]} and {glob_refs[1]}.",
    )

    # ---- H8. a power symbol inside a child, seen from the parent --------
    child, points = place("H8 POWER UP", ["PWR_UP"])
    anchor = child.anchor(0)
    child.sheet.label("PWR_UP", anchor, kind="hierarchical_label")
    pwr_child = leg_below(child.sheet, refs, anchor, None)
    # Ground the child net directly, so a power name has to win the whole chain.
    child.sheet.ground(refs.power(), anchor)
    pwr_parent = leg_left(root, refs, points[0], None)
    add(
        "H8_POWER_WINS_UPWARD",
        "Does a power name found in a child rename the parent's net too?",
        f"Child: hierarchical label PWR_UP, a ground symbol on the same wire, and"
        f" {pwr_child}. Parent: {pwr_parent} wired to sheet pin PWR_UP.",
        "both resistors join GND. A global power pin outranks everything, so it"
        " wins the chain and the name carries no path.",
    )

    root_file = f"{PROJECT}.kicad_sch"
    (out / root_file).write_text(
        render(root, results, uuid=ROOT_UUID, title="hierarchy rule probe", root=True),
        encoding="utf-8",
    )
    for child in children:
        child.write(out)
    return results


def project_file() -> str:
    return (
        "{\n"
        '  "board": {},\n'
        '  "boards": [],\n'
        '  "meta": {\n'
        '    "filename": "' + PROJECT + '.kicad_pro",\n'
        '    "version": 3\n'
        "  },\n"
        '  "sheets": [\n'
        "    [\n"
        '      "' + ROOT_UUID + '",\n'
        '      "Root"\n'
        "    ]\n"
        "  ],\n"
        '  "text_variables": {}\n'
        "}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results = build(args.out)
    (args.out / f"{PROJECT}.kicad_pro").write_text(project_file(), encoding="utf-8")
    (args.out / "INTENT.md").write_text(intent(results), encoding="utf-8")
    print(f"{len(results)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
