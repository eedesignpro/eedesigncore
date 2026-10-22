"""Generate a probe that isolates WHEN a label may bridge two wires.

The one rule in this engine that is not a port is the permissive mid-wire label:
a label strictly inside a single unbroken wire connects, and no source path
explains why. A rule that ADDS connections is the natural suspect for an
over-connection, and one design still has two nets joined that KiCad keeps apart.

So this fixture asks the bridging question directly. Every case puts a label at a
point where two or more lines pass, varying only what those lines are and how
they meet. If the label bridges, the netlist shows both resistors on one net; if
it does not, they sit on separate nets. There is nothing to interpret either way.

Same discipline as the other two fixtures. One case per cell of a grid, pin 2 of
every resistor on ground, ground the only net two cases share.

Run this, then export with KiCad's own tool and compare:

    kicad-cli sch export netlist --format kicadsexpr -o probe.net <root>.kicad_sch
"""

from __future__ import annotations

import argparse
from pathlib import Path

from make_connectivity_probe import (
    PIN2_DY,
    Result,
    Sheet,
    Uuids,
    intent,
    render,
)

PROJECT = "probe-bridge-rules"
ROOT_UUID = "30000000-0000-4000-8000-000000000000"

CELL_W = 63.5
CELL_H = 76.2
COLS = 5
ORIGIN = (63.5, 63.5)

# Half the span of a crossing pair, so the crossing point sits in the middle.
ARM = 20.32


class Probe:
    """Grid cells, references, and one grounded resistor per probe point."""

    def __init__(self, sheet: Sheet) -> None:
        self.sheet = sheet
        self.cell = 0
        self.next_r = 0
        self.next_pwr = 0

    def take(self) -> tuple[float, float]:
        col, row = self.cell % COLS, self.cell // COLS
        self.cell += 1
        return (ORIGIN[0] + col * CELL_W, ORIGIN[1] + row * CELL_H)

    def leg(self, at: tuple[float, float], *, down: bool) -> tuple[str, tuple[float, float]]:
        """A grounded resistor whose pin 1 is the returned probe point."""
        self.next_r += 1
        self.next_pwr += 1
        ref = f"R{self.next_r}"
        pin1 = self.sheet.resistor(ref, at)
        self.sheet.ground(f"#PWR{self.next_pwr:02d}", (at[0], at[1] + PIN2_DY))
        del down
        return ref, pin1


def build(sheet: Sheet) -> list[Result]:  # noqa: C901, PLR0915
    p = Probe(sheet)
    out: list[Result] = []

    def add(key: str, question: str, drawn: str, predicted: str) -> None:
        out.append(Result(key=key, question=question, drawn=drawn, predicted=predicted))

    def crossing(cell: tuple[float, float]) -> tuple[str, str, tuple[float, float]]:
        """Two wires crossing at a point neither one ends at.

        Wire A runs left to right THROUGH the point. Wire B runs top to bottom
        through the same point. Each carries a grounded resistor at one end, well
        clear of the crossing.
        """
        cx, cy = cell[0], cell[1] - 33.02
        ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
        sheet.wire(pin_a, (cx - ARM, cy))
        sheet.wire((cx - ARM, cy), (cx + ARM, cy))
        ref_b, pin_b = p.leg((cell[0] + ARM, cell[1]), down=False)
        sheet.wire(pin_b, (cx, cy + ARM))
        sheet.wire((cx, cy + ARM), (cx, cy - ARM))
        return ref_a, ref_b, (cx, cy)

    # ---- 1. the key case -------------------------------------------------
    ref_a, ref_b, point = crossing(p.take())
    sheet.label("X_CROSS", point)
    add(
        "X_CROSS",
        "Does a label at a CROSSING bridge two wires that neither end there?",
        f"{ref_a}'s wire runs across the point. {ref_b}'s wire runs down through"
        f" the same point. Neither wire ENDS there and there is no junction."
        f" A label sits exactly on the crossing.",
        "UNKNOWN, and this is the case the fixture exists for. The ported rule"
        " pushes every line overlapping a label point once two or more overlap,"
        " which would bridge these. If KiCad keeps them apart, that rule is too"
        " permissive and is the likely cause of the one over-connection left.",
    )

    # ---- 2. the control for it ------------------------------------------
    ref_a, ref_b, point = crossing(p.take())
    sheet.label("X_NEAR", (point[0] + 0.01, point[1]))
    add(
        "X_NEAR",
        "Same crossing, label 0.01 mm off it.",
        f"{ref_a} and {ref_b} crossing as above, label ten thousand nanometres"
        f" to the side of the crossing.",
        "does NOT bridge. Control: it isolates the label's position from every"
        " other difference between this case and the one above.",
    )

    # ---- 3. a junction present ------------------------------------------
    cell = p.take()
    cy = cell[1] - 33.02
    ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
    sheet.wire(pin_a, (cell[0] - ARM, cy))
    sheet.wire((cell[0] - ARM, cy), (cell[0] + ARM, cy))
    ref_b, pin_b = p.leg((cell[0] + ARM, cell[1]), down=False)
    sheet.wire(pin_b, (cell[0], cy))
    sheet.junction((cell[0], cy))
    sheet.label("X_DOT", (cell[0] - 7.62, cy))
    add(
        "X_DOT",
        "Does a junction bridge a wire END landing on another wire's middle?",
        f"{ref_a}'s wire runs across. {ref_b}'s wire ENDS on that wire's middle,"
        f" with a junction on the meeting point. A label sits 7.62 mm away,"
        f" interior to the horizontal wire.",
        f"bridges. {ref_a} and {ref_b} share one net named X_DOT. This is the"
        " positive control: it proves a resistor pair in this fixture CAN be"
        " joined, so a case that stays apart says something about the rule rather"
        " than about the drawing."
        " Drawn as a tee rather than a crossing on purpose. A junction placed on"
        " a pure crossing is removed by KiCad's own cleanup the first time the"
        " file is saved, so the earlier version of this case lost its dot as soon"
        " as it was opened.",
    )

    # ---- 4. three wires through one point --------------------------------
    cell = p.take()
    cx, cy = cell[0], cell[1] - 33.02
    ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
    sheet.wire(pin_a, (cx - ARM, cy))
    sheet.wire((cx - ARM, cy), (cx + ARM, cy))
    ref_b, pin_b = p.leg((cell[0], cell[1]), down=False)
    sheet.wire(pin_b, (cx, cy + ARM))
    sheet.wire((cx, cy + ARM), (cx, cy - ARM))
    ref_c, pin_c = p.leg((cell[0] + ARM, cell[1]), down=False)
    sheet.wire(pin_c, (cx + ARM, cy + ARM))
    sheet.wire((cx + ARM, cy + ARM), (cx - ARM, cy - ARM))
    sheet.label("X_THREE", (cx, cy))
    add(
        "X_THREE",
        "Does the count of crossing wires change the answer?",
        f"Three wires all passing through one point, none ending there:"
        f" {ref_a} across, {ref_b} down, {ref_c} diagonally. A label on the point.",
        "same answer as X_CROSS, whatever that turns out to be. If the two"
        " disagree, the rule counts lines rather than testing how they meet.",
    )

    # ---- 5. collinear overlap -------------------------------------------
    cell = p.take()
    cy = cell[1] - 33.02
    ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
    sheet.wire(pin_a, (cell[0] - ARM, cy))
    sheet.wire((cell[0] - ARM, cy), (cell[0] + 5.08, cy))
    ref_b, pin_b = p.leg((cell[0] + ARM, cell[1]), down=False)
    sheet.wire(pin_b, (cell[0] + ARM, cy))
    sheet.wire((cell[0] + ARM, cy), (cell[0] - 5.08, cy))
    sheet.label("X_OVER", (cell[0], cy))
    add(
        "X_OVER",
        "Two COLLINEAR wires overlapping along a shared stretch, label inside both.",
        f"{ref_a}'s wire runs right to x+5.08. {ref_b}'s runs left to x-5.08, so"
        f" the two overlap along the middle. The label sits inside BOTH, at a"
        f" point that is an endpoint of neither.",
        "UNKNOWN. Two lines genuinely overlap the point, so the ported rule"
        " applies on its own terms rather than by accident. Read beside X_CROSS:"
        " if they differ, overlapping and crossing are not one rule."
        " Note that a collinear overlap necessarily puts each wire's end INSIDE"
        " the other. That cannot be what joins them: the flat fixture already"
        " showed an end landing inside another wire does not connect on its own."
        " So any join here is the label's doing.",
    )

    # ---- 6. a wire and a bus --------------------------------------------
    cell = p.take()
    cx, cy = cell[0], cell[1] - 33.02
    ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
    sheet.wire(pin_a, (cx - ARM, cy))
    sheet.wire((cx - ARM, cy), (cx + ARM, cy))
    sheet.bus((cx, cy + ARM), (cx, cy - ARM))
    sheet.label("X_BUS[0..1]", (cx, cy - ARM))
    sheet.label("X_WIRE", (cx, cy))
    # A real member leg off the bus, through an entry, so the bus is alive and
    # the case cannot be read as a bus that simply reaches nothing. An earlier
    # version ran this resistor's wire into empty space.
    ref_b, pin_b = p.leg((cell[0] + ARM, cell[1]), down=False)
    entry_at = (cx, cy + 10.16)
    sheet.bus_entry(entry_at, (2.54, 2.54))
    sheet.wire((entry_at[0] + 2.54, entry_at[1] + 2.54), (pin_b[0], entry_at[1] + 2.54))
    sheet.wire((pin_b[0], entry_at[1] + 2.54), pin_b)
    sheet.label("X_BUS0", pin_b)
    add(
        "X_BUS_CROSS",
        "Can a label bridge a WIRE to a BUS that crosses it?",
        f"{ref_a}'s wire runs across the point. A bus labelled X_BUS[0..1] runs"
        f" down through the same point. A label X_WIRE sits on the crossing."
        f" {ref_b} reaches the bus properly, through a bus entry and a wire"
        f" labelled X_BUS0, well clear of the crossing.",
        "must NOT bridge. A wire and a bus never join directly, and that gate is"
        " two-way and independent of any label. If they join here, the label rule"
        " is bypassing the gate. "
        f"{ref_b} lands on X_BUS0, which proves the bus itself is connected and"
        " the separation is not just a dead bus.",
    )

    # ---- 7. two labels at one interior point -----------------------------
    cell = p.take()
    ref_a, pin_a = p.leg(cell, down=False)
    end = (pin_a[0], pin_a[1] - 20.32)
    mid = (pin_a[0], pin_a[1] - 10.16)
    sheet.wire(pin_a, end)
    sheet.label("X_ZULU", mid)
    sheet.label("X_ALPHA", mid)
    add(
        "X_TWO_AT_ONE_POINT",
        "Two labels of different names at the SAME interior point of one wire.",
        f"{ref_a}'s wire is one unbroken segment. Both X_ALPHA and X_ZULU sit at"
        f" its midpoint, on top of each other.",
        "one net named X_ALPHA, alphabetically. Also a second reading: two labels"
        " already make two items at that point, so if the ported two-line rule"
        " were what connects a mid-wire label, this case would connect where a"
        " single label does not.",
    )

    # ---- 8. an endpoint meeting an interior point ------------------------
    cell = p.take()
    cy = cell[1] - 33.02
    ref_a, pin_a = p.leg((cell[0] - ARM, cell[1]), down=False)
    sheet.wire(pin_a, (cell[0] - ARM, cy))
    sheet.wire((cell[0] - ARM, cy), (cell[0] + ARM, cy))
    ref_b, pin_b = p.leg((cell[0] + ARM, cell[1]), down=False)
    sheet.wire(pin_b, (cell[0], cy))
    sheet.label("X_TOUCH", (cell[0] - 7.62, cy))
    add(
        "X_TOUCH_LABEL_ELSEWHERE",
        "A wire END touching another wire's middle, with the label somewhere ELSE.",
        f"{ref_a}'s wire runs across. {ref_b}'s wire ENDS on that wire's middle,"
        f" with no junction. The label sits 7.62 mm away, interior to the"
        f" horizontal wire only, NOT at the touch point.",
        "does NOT bridge. The label is nowhere near the touch, so nothing"
        " contributes that interior point. If they join, the permissive rule is"
        " reaching beyond the label's own position.",
    )

    # ---- 9. a global label at a crossing ---------------------------------
    ref_a, ref_b, point = crossing(p.take())
    sheet.label("X_GLOB", point, kind="global_label")
    add(
        "X_GLOB_CROSS",
        "Does the label KIND change whether it bridges?",
        f"{ref_a} and {ref_b} crossing as in X_CROSS, with a GLOBAL label on the"
        f" crossing instead of a local one.",
        "same answer as X_CROSS. The overlap rule tests the base label type, so"
        " kind should not matter. A difference here would mean it does.",
    )

    # ---- 10. a directive at a crossing -----------------------------------
    ref_a, ref_b, point = crossing(p.take())
    sheet.label("X_DIRECTIVE", point, kind="netclass_flag")
    add(
        "X_DIRECTIVE_CROSS",
        "Does a netclass DIRECTIVE at a crossing bridge, even though it never names a net?",
        f"{ref_a} and {ref_b} crossing as in X_CROSS, with a netclass directive"
        f" on the crossing and no label at all.",
        "bridges if X_CROSS bridges. A directive is a label for connectivity"
        " purposes and only declines to NAME the net. Both resistors would then"
        " share one derived name. If it bridges where a plain label does not, the"
        " two are on different code paths.",
    )

    return out


def verify(sheet: Sheet) -> None:
    """Check that the sheet DRAWS what the intent claims. Raises if it does not.

    A fixture is only worth a reviewer's time if its geometry is what its text
    says. Three invariants cover every way this fixture has gone wrong so far,
    and each one was a real defect caught after the fact rather than before:

    1. Every resistor pin sits on a wire endpoint. A leg wired into empty space
       proves nothing, and one was.
    2. Every label sits ON a line, as an endpoint or strictly inside one. A
       dangling label tests nothing.
    3. Every junction sits where at least two lines pass. A junction floating
       clear of the wires is invisible in the result.

    The one deliberate exception is a control whose whole point is to miss: a
    label placed a hair off a line. Those are named, so the exception cannot
    quietly widen to cover a mistake.
    """
    ALLOWED_OFF_LINE = {"X_NEAR"}
    segments = [(a, b) for _kind, a, b in sheet.segments]

    def touches(
        point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
    ) -> bool:
        if point in (a, b):
            return True
        # Colinear and within the span, in exact arithmetic on millimetres
        # scaled to integers, so no floating-point slack creeps in.
        px, py = round(point[0] * 1e6), round(point[1] * 1e6)
        ax, ay = round(a[0] * 1e6), round(a[1] * 1e6)
        bx, by = round(b[0] * 1e6), round(b[1] * 1e6)
        if (bx - ax) * (py - ay) != (by - ay) * (px - ax):
            return False
        return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)

    problems: list[str] = []
    for kind, point, text in sheet.anchors:
        hits = sum(1 for a, b in segments if touches(point, a, b))
        ends = sum(1 for a, b in segments if point in (a, b))
        if kind == "pin":
            # A pin is served either by a wire ENDING on it or by another pin
            # sitting on the same point. Every resistor's lower pin meets its
            # ground symbol's pin directly, with no wire between them, so
            # demanding a wire end here reports forty healthy pins as broken.
            pin_mates = sum(1 for k, q, _t in sheet.anchors if k == "pin" and q == point) - 1
            if ends == 0 and pin_mates == 0:
                problems.append(f"pin {text} at {point} reaches nothing")
        elif kind == "junction":
            if hits < 2:
                problems.append(f"junction at {point} has {hits} lines, needs 2")
        elif hits == 0 and text not in ALLOWED_OFF_LINE:
            problems.append(f"{kind} {text!r} at {point} touches no line")

    if problems:
        joined = (chr(10) + "  ").join(problems)
        raise SystemExit("the fixture does not draw what it claims:" + chr(10) + "  " + joined)


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
    sheet = Sheet(uuids=Uuids(), inst_path=f"/{ROOT_UUID}", project=PROJECT)
    results = build(sheet)
    verify(sheet)

    (args.out / f"{PROJECT}.kicad_sch").write_text(
        render(sheet, results, uuid=ROOT_UUID, title="label bridging probe", root=True),
        encoding="utf-8",
    )
    # Never overwrite a project file that already exists. Once the operator has
    # opened the fixture and created a board, KiCad has filled that file with
    # settings and a board association; regenerating a stub over the top of it
    # throws all of that away and the fixture stops matching its own board.
    pro = args.out / f"{PROJECT}.kicad_pro"
    if not pro.exists():
        pro.write_text(project_file(), encoding="utf-8")
    (args.out / "INTENT.md").write_text(intent(results), encoding="utf-8")
    print(f"{len(results)} cases, {sheet.uuids._next} items -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
