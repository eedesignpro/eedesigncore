"""Generate a probe schematic that isolates one connectivity rule per resistor.

Every case is ONE resistor. Its pin 2 goes to ground. Its pin 1 sits at the
probe point, and the case decides what touches that point: a wire, a label at a
particular place on that wire, a bus entry, a junction, or nothing.

That shape is what makes the result unambiguous. If the case's label reaches the
resistor, KiCad's netlist shows a net with the label's name holding that
resistor's pin 1. If it does not reach, the same pin turns up under a derived
name instead, and the label's name is absent from the netlist entirely. No case
shares a net with any other case, so one wrong rule cannot mask another.

Run this, then export the netlist with KiCad's own tool and compare:

    kicad-cli sch export netlist --format kicadsexpr -o probe.net probe.kicad_sch

The generated INTENT.md states, per case, what is drawn and what this engine
predicts. Read it beside the netlist: a disagreement names the rule to fix.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

# Library pin offsets for Device:R, from the installed library. Library Y is
# negated when a schematic loads, so pin 1 sits ABOVE the symbol origin.
PIN1_DY = -3.81
PIN2_DY = 3.81

KICAD_SYMBOLS = Path(r"C:\Program Files\KiCad\10.0\share\kicad\symbols")

PROJECT = "probe-connectivity-rules"


def num(value: float) -> str:
    """Format a coordinate the way KiCad does, trimming a trailing zero."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


class Uuids:
    """Deterministic identifiers, so a rerun produces the same file."""

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> str:
        self._next += 1
        return f"00000000-0000-4000-8000-{self._next:012d}"


@dataclass
class Sheet:
    """Accumulates the schematic body, one item kind per list."""

    uuids: Uuids
    # The instance path every symbol on this sheet records. The root sheet is the
    # root identifier alone; a child appends its own sheet identifier. A
    # reference resolves against this, so a child that reuses the root's path
    # would resolve every reference to the wrong instance.
    inst_path: str = "/00000000-0000-4000-8000-000000000000"
    project: str = PROJECT
    # The same geometry as the rendered text, kept as numbers so a
    # generator can CHECK what it drew instead of asserting it. Every
    # claim a fixture makes has to be verifiable from here, or the
    # fixture can ship testing something other than what it says.
    segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = field(
        default_factory=list
    )
    anchors: list[tuple[str, tuple[float, float], str]] = field(default_factory=list)
    wires: list[str] = field(default_factory=list)
    buses: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)
    junctions: list[str] = field(default_factory=list)
    no_connects: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def wire(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.segments.append(("wire", a, b))
        self.wires.append(
            f"\t(wire\n"
            f"\t\t(pts\n"
            f"\t\t\t(xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])})\n"
            f"\t\t)\n"
            f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def bus(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.segments.append(("bus", a, b))
        self.buses.append(
            f"\t(bus\n"
            f"\t\t(pts\n"
            f"\t\t\t(xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])})\n"
            f"\t\t)\n"
            f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def bus_entry(self, at: tuple[float, float], size: tuple[float, float]) -> None:
        self.entries.append(
            f"\t(bus_entry\n"
            f"\t\t(at {num(at[0])} {num(at[1])})\n"
            f"\t\t(size {num(size[0])} {num(size[1])})\n"
            f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def junction(self, at: tuple[float, float]) -> None:
        self.anchors.append(("junction", at, ""))
        self.junctions.append(
            f"\t(junction\n"
            f"\t\t(at {num(at[0])} {num(at[1])})\n"
            f"\t\t(diameter 0)\n"
            f"\t\t(color 0 0 0 0)\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def no_connect(self, at: tuple[float, float]) -> None:
        self.no_connects.append(
            f"\t(no_connect\n"
            f"\t\t(at {num(at[0])} {num(at[1])})\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def label(
        self, text: str, at: tuple[float, float], angle: float = 0, kind: str = "label"
    ) -> None:
        self.anchors.append((kind, at, text))
        shape = "\t\t(shape input)\n" if kind in ("global_label", "hierarchical_label") else ""
        self.labels.append(
            f'\t({kind} "{text}"\n'
            f"{shape}"
            f"\t\t(at {num(at[0])} {num(at[1])} {num(angle)})\n"
            f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n"
            f"\t\t\t(justify left bottom)\n\t\t)\n"
            f'\t\t(uuid "{self.uuids.take()}")\n'
            f"\t)"
        )

    def resistor(
        self,
        ref: str,
        at: tuple[float, float],
        extra_instances: list[tuple[str, str]] | None = None,
    ) -> tuple[float, float]:
        """Place a resistor and return its pin 1 point.

        *extra_instances* adds further (sheet path, reference) pairs. A sheet file
        placed twice needs one pair per placement, because each placement is a
        SEPARATE component with its own reference. Without them KiCad cannot
        resolve the second instance.
        """
        x, y = at
        self.symbols.append(
            _symbol_block(
                lib_id="Device:R",
                at=(x, y, 0),
                ref=ref,
                value="10k",
                footprint="Resistor_SMD:R_0603_1608Metric",
                pins=("1", "2"),
                uuids=self.uuids,
                ref_at=(x + 2.54, y - 1.27, 0),
                value_at=(x - 2.54, y - 1.27, 0),
                inst_path=self.inst_path,
                project=self.project,
                extra_instances=extra_instances,
            )
        )
        self.anchors.append(("pin", (x, y + PIN1_DY), f"{ref}-1"))
        self.anchors.append(("pin", (x, y + PIN2_DY), f"{ref}-2"))
        return (x, y + PIN1_DY)

    def ground(
        self,
        ref: str,
        at: tuple[float, float],
        extra_instances: list[tuple[str, str]] | None = None,
    ) -> None:
        """Place a ground symbol whose pin sits exactly at *at*."""
        self.symbols.append(
            _symbol_block(
                lib_id="power:GND",
                at=(at[0], at[1], 0),
                ref=ref,
                value="GND",
                footprint="",
                pins=("1",),
                uuids=self.uuids,
                ref_at=(at[0], at[1] + 6.35, 0),
                value_at=(at[0], at[1] + 3.81, 0),
                hide_ref=True,
                inst_path=self.inst_path,
                project=self.project,
                extra_instances=extra_instances,
            )
        )
        self.anchors.append(("pin", at, f"{ref}-1"))


def _prop(name: str, value: str, at: tuple[float, float, float], *, hide: bool) -> str:
    hidden = "\t\t\t(hide yes)\n" if hide else ""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'\t\t(property "{name}" "{escaped}"\n'
        f"\t\t\t(at {num(at[0])} {num(at[1])} {num(at[2])})\n"
        f"{hidden}"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        f"\t\t)"
    )


def _symbol_block(
    *,
    lib_id: str,
    at: tuple[float, float, float],
    ref: str,
    value: str,
    footprint: str,
    pins: tuple[str, ...],
    uuids: Uuids,
    ref_at: tuple[float, float, float],
    value_at: tuple[float, float, float],
    hide_ref: bool = False,
    inst_path: str = "/00000000-0000-4000-8000-000000000000",
    project: str = PROJECT,
    extra_instances: list[tuple[str, str]] | None = None,
) -> str:
    lines = [
        "\t(symbol",
        f'\t\t(lib_id "{lib_id}")',
        f"\t\t(at {num(at[0])} {num(at[1])} {num(at[2])})",
        "\t\t(unit 1)",
        "\t\t(body_style 1)",
        "\t\t(exclude_from_sim no)",
        "\t\t(in_bom yes)",
        "\t\t(on_board yes)",
        "\t\t(in_pos_files yes)",
        "\t\t(dnp no)",
        f'\t\t(uuid "{uuids.take()}")',
        _prop("Reference", ref, ref_at, hide=hide_ref),
        _prop("Value", value, value_at, hide=False),
        _prop("Footprint", footprint, (at[0], at[1], 0), hide=True),
        _prop("Datasheet", "", (at[0], at[1], 0), hide=True),
        _prop("Description", "", (at[0], at[1], 0), hide=True),
    ]
    for number in pins:
        lines.append(f'\t\t(pin "{number}"\n\t\t\t(uuid "{uuids.take()}")\n\t\t)')
    paths = [(inst_path, ref), *(extra_instances or [])]
    instance_lines = "".join(
        f'\t\t\t\t(path "{path}"\n'
        f'\t\t\t\t\t(reference "{reference}")\n'
        "\t\t\t\t\t(unit 1)\n"
        "\t\t\t\t)\n"
        for path, reference in paths
    )
    lines.append(f'\t\t(instances\n\t\t\t(project "{project}"\n{instance_lines}\t\t\t)\n\t\t)')
    lines.append("\t)")
    return "\n".join(lines)


def lib_symbol(library: str, name: str) -> str:
    """Lift one symbol definition out of an installed library, verbatim.

    Copied rather than authored, so the probe cannot fail for a reason that has
    nothing to do with connectivity.
    """
    text = (KICAD_SYMBOLS / f"{library}.kicad_sym").read_text(encoding="utf-8")
    opening = f'\t(symbol "{name}"\n'
    start = text.index(opening)
    end = text.index("\n\t)\n", start) + len("\n\t)")
    block = text[start:end]
    # Re-key the definition under "Library:Name" and indent it one level deeper,
    # which is the form a schematic's lib_symbols block uses.
    block = block.replace(opening, f'\t(symbol "{library}:{name}"\n', 1)
    return "\n".join("\t" + line if line else line for line in block.split("\n"))


# --------------------------------------------------------------------------
# The cases.
#
# One resistor per case unless the question needs two nets compared. A case gets
# a cell of the grid and never touches a neighbour, so one wrong rule cannot mask
# another.
# --------------------------------------------------------------------------

CELL_W = 50.8
CELL_H = 63.5
COLS = 8
ORIGIN = (50.8, 50.8)

# The wire this probe draws from pin 1, and the points along it.
RUN = 10.16


@dataclass
class Result:
    key: str
    question: str
    drawn: str
    predicted: str


class Placer:
    """Hands out grid cells and reference designators."""

    def __init__(self, sheet: Sheet) -> None:
        self.sheet = sheet
        self.cell = 0
        self.next_r = 1
        self.next_pwr = 1

    def take(self, width: int = 1) -> tuple[float, float]:
        # Never straddle a row boundary, so a wide case stays in one band.
        col = self.cell % COLS
        if col + width > COLS:
            self.cell += COLS - col
            col = 0
        row = self.cell // COLS
        self.cell += width
        return (ORIGIN[0] + col * CELL_W, ORIGIN[1] + row * CELL_H)

    def probe(self, at: tuple[float, float]) -> tuple[str, tuple[float, float]]:
        """A resistor with pin 2 grounded. Returns its reference and pin 1 point."""
        ref = f"R{self.next_r}"
        self.next_r += 1
        pin1 = self.sheet.resistor(ref, at)
        pwr = f"#PWR{self.next_pwr:02d}"
        self.next_pwr += 1
        self.sheet.ground(pwr, (at[0], at[1] + PIN2_DY))
        return ref, pin1


def place_all(sheet: Sheet) -> list[Result]:  # noqa: C901, PLR0915
    """Place every case and return what each one asks and predicts."""
    p = Placer(sheet)
    out: list[Result] = []

    def add(key: str, question: str, drawn: str, predicted: str) -> None:
        out.append(Result(key=key, question=question, drawn=drawn, predicted=predicted))

    # ---- A. one label against one wire ---------------------------------
    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("L_END", end)
    add(
        "L_END",
        "Does a label at a wire's free END name the net?",
        f"{ref} pin 1 wired up {RUN} mm. Label anchor exactly on the free end.",
        "connects. L_END holds this pin. This is the control case.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    mid = (pin1[0], pin1[1] - RUN / 2)
    sheet.wire(pin1, end)
    sheet.label("L_MID", mid)
    add(
        "L_MID",
        "Does a label strictly INSIDE one unbroken wire name the net?",
        f"{ref} pin 1 wired up {RUN} mm, ONE segment. Label anchor at its midpoint.",
        "THE OPEN QUESTION. This engine connects it. Source shows no path that does.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    mid = (pin1[0], pin1[1] - RUN / 2)
    sheet.wire(pin1, mid)
    sheet.wire(mid, end)
    sheet.label("L_SPLIT", mid)
    add(
        "L_SPLIT",
        "Does the two-overlapping-lines rule fire where two segments MEET?",
        f"{ref} pin 1 wired up in TWO segments meeting at the midpoint. Label there.",
        "connects. Two lines hit that point, both as endpoints, so the rule fires.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("L_OFF", (pin1[0] + 2.54, pin1[1] - RUN / 2))
    add(
        "L_OFF",
        "Does a label beside a wire connect?",
        f"{ref} pin 1 wired up. Label anchor 2.54 mm to the side, touching nothing.",
        "does NOT connect. L_OFF is absent from the netlist. Control case.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("L_NEAR", (pin1[0] + 0.01, pin1[1] - RUN / 2))
    add(
        "L_NEAR",
        "How much tolerance does the overlap test allow?",
        f"{ref} pin 1 wired up. Label anchor 0.01 mm to the side, ten thousand nm off.",
        "does NOT connect. The hit test allows one nm, not ten thousand.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    sheet.label("L_ON_PIN", pin1)
    add(
        "L_ON_PIN",
        "Does a label sitting on a pin, with no wire at all, name the net?",
        f"{ref} pin 1 has NO wire. Label anchor exactly on the pin.",
        "connects. Both sit at one point, so the ordinary path pairs them.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    mid = (pin1[0], pin1[1] - RUN / 2)
    sheet.wire(pin1, end)
    sheet.label("L_MID_ROT", mid, angle=90)
    add(
        "L_MID_ROT",
        "Does rotating a label move the point it connects at?",
        f"{ref} pin 1 wired up, one segment. Label at the midpoint, rotated 90.",
        "same as L_MID. Rotation turns the text and not the anchor.",
    )

    # ---- B. a label bridging more than one wire -------------------------
    at = p.take(2)
    ref_a, pin_a = p.probe(at)
    right = (at[0] + CELL_W, at[1])
    ref_b, pin_b = p.probe(right)
    cross = (pin_a[0] + (pin_b[0] - pin_a[0]) / 2, pin_a[1] - RUN / 2)
    # Wire A runs up then right THROUGH the crossing point without stopping.
    sheet.wire(pin_a, (pin_a[0], cross[1] - RUN / 2))
    sheet.wire((pin_a[0], cross[1] - RUN / 2), (pin_b[0], cross[1] - RUN / 2))
    # Wire B runs up past that horizontal wire, crossing it, stopping above.
    sheet.wire(pin_b, (pin_b[0], cross[1] - RUN))
    add(
        "L_CROSS",
        "Do two wires that merely CROSS connect, with no junction and no label?",
        f"{ref_a} runs up and across. {ref_b} runs up and crosses it at a right angle.",
        f"does NOT connect. Neither wire contributes the crossing point, so {ref_a}"
        f" and {ref_b} land on different nets.",
    )

    at = p.take(2)
    ref_a, pin_a = p.probe(at)
    right = (at[0] + CELL_W, at[1])
    ref_b, pin_b = p.probe(right)
    bar_y = pin_a[1] - RUN
    sheet.wire(pin_a, (pin_a[0], bar_y))
    sheet.wire((pin_a[0], bar_y), (pin_b[0] + 12.7, bar_y))
    # B's endpoint lands in the MIDDLE of that horizontal wire, no junction.
    sheet.wire(pin_b, (pin_b[0], bar_y))
    sheet.label("L_TEE", (pin_b[0], bar_y))
    add(
        "L_TEE",
        "Does a label bridge a wire END that lands mid-way along another wire?",
        f"{ref_a} runs up and across, past {ref_b}. {ref_b} runs up and its END"
        f" lands inside that horizontal wire. No junction. Label at that point.",
        "connects both, because the point is an endpoint of one wire and inside the"
        " other, which is two overlapping lines.",
    )

    at = p.take(2)
    ref_a, pin_a = p.probe(at)
    right = (at[0] + CELL_W, at[1])
    ref_b, pin_b = p.probe(right)
    bar_y = pin_a[1] - RUN
    sheet.wire(pin_a, (pin_a[0], bar_y))
    sheet.wire((pin_a[0], bar_y), (pin_b[0] + 12.7, bar_y))
    sheet.wire(pin_b, (pin_b[0], bar_y))
    add(
        "L_TEE_BARE",
        "Same tee with NO label and NO junction. Does it connect?",
        f"{ref_a} runs up and across. {ref_b}'s wire END lands inside it. Nothing else.",
        f"does NOT connect. The horizontal wire never contributes that interior"
        f" point, so {ref_b} is separate.",
    )

    at = p.take(2)
    ref_a, pin_a = p.probe(at)
    right = (at[0] + CELL_W, at[1])
    ref_b, pin_b = p.probe(right)
    bar_y = pin_a[1] - RUN
    sheet.wire(pin_a, (pin_a[0], bar_y))
    sheet.wire((pin_a[0], bar_y), (pin_b[0] + 12.7, bar_y))
    sheet.wire(pin_b, (pin_b[0], bar_y))
    sheet.junction((pin_b[0], bar_y))
    sheet.label("L_TEE_DOT", (pin_b[0] + 2.54, bar_y))
    add(
        "L_TEE_DOT",
        "Does a junction bridge that same tee?",
        "The L_TEE_BARE geometry plus a junction at the tee. Label 2.54 mm away"
        " on the horizontal wire, so it also tests a label mid-wire.",
        "connects both. A junction pulls in every line passing through its point.",
    )

    # ---- C. which name wins --------------------------------------------
    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("N_ZULU", end)
    sheet.label("N_ALPHA", pin1)
    add(
        "N_TWO_LOCAL",
        "Between two local labels on one net, which name wins?",
        f"{ref} pin 1 wired up. Label N_ALPHA on the pin end, N_ZULU on the far end.",
        "N_ALPHA wins. Equal rank breaks alphabetically.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("N_LOCAL", pin1)
    sheet.label("N_GLOBAL", end, kind="global_label")
    add(
        "N_GLOBAL_BEATS_LOCAL",
        "Does a global label outrank a local one, and is its name unqualified?",
        f"{ref} pin 1 wired up. Local label N_LOCAL on the pin, global N_GLOBAL on the far end.",
        "N_GLOBAL wins, with NO leading slash, because a global name is not scoped.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("N_HIER", end, kind="hierarchical_label")
    add(
        "N_HIER_ON_ROOT",
        "What does a hierarchical label do on the root sheet, with no parent above?",
        f"{ref} pin 1 wired up. A hierarchical label on the far end.",
        "N_HIER names the net, path-qualified as /N_HIER, and reaches nowhere.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("N_MID_ONLY", (pin1[0], pin1[1] - RUN / 2))
    sheet.label("N_END_ONLY", end)
    add(
        "N_MID_VS_END",
        "Does a mid-wire label compete on equal terms with an end label?",
        f"{ref} pin 1 wired up, one segment. N_MID_ONLY at the midpoint,"
        f" N_END_ONLY at the far end.",
        "N_END_ONLY wins alphabetically if BOTH connect. If only the end label"
        " connects, the answer is the same, so read this case beside L_MID.",
    )

    # ---- D. buses -------------------------------------------------------
    at = p.take(4)
    bus_y = at[1] - RUN * 2
    bus_left = at[0] - 5.08
    bus_right = at[0] + 3 * CELL_W + 5.08
    sheet.bus((bus_left, bus_y), (bus_right, bus_y))
    sheet.label("B_VEC[0..3]", (bus_left, bus_y))
    vec_refs = []
    for slot in range(4):
        cell_x = at[0] + slot * CELL_W
        ref, pin1 = p.probe((cell_x, at[1]))
        vec_refs.append(ref)
        # Wire up from the pin, then a bus entry from that wire end onto the bus.
        wire_top = (pin1[0], bus_y + 2.54)
        sheet.wire(pin1, wire_top)
        sheet.bus_entry(wire_top, (2.54, -2.54))
        if slot < 3:
            sheet.label(f"B_VEC{slot}", pin1)
    add(
        "B_VEC_MEMBERS",
        "Do bus members reach their wires by NAME, and does a bare member wire get a name at all?",
        f"A bus labelled B_VEC[0..3]. Four resistors {', '.join(vec_refs)} wired up"
        f" to it through bus entries. The first three wires carry labels B_VEC0,"
        f" B_VEC1, B_VEC2. The FOURTH carries no label.",
        "the three labelled wires land on B_VEC0, B_VEC1 and B_VEC2. The fourth"
        f" gets a derived name, NOT B_VEC3, because a bus entry does not connect"
        f" to the bus. That last resistor, {vec_refs[3]}, is the whole point.",
    )

    at = p.take(2)
    bus_y = at[1] - RUN * 2
    bus_left = at[0] - 5.08
    bus_right = at[0] + CELL_W + 5.08
    sheet.bus((bus_left, bus_y), (bus_right, bus_y))
    sheet.label("B_GRP{ALPHA BETA}", (bus_left, bus_y))
    grp_refs = []
    for slot, member in enumerate(("ALPHA", "BETA")):
        cell_x = at[0] + slot * CELL_W
        ref, pin1 = p.probe((cell_x, at[1]))
        grp_refs.append(ref)
        wire_top = (pin1[0], bus_y + 2.54)
        sheet.wire(pin1, wire_top)
        sheet.bus_entry(wire_top, (2.54, -2.54))
        sheet.label(f"B_GRP.{member}", pin1)
    add(
        "B_GROUP_MEMBERS",
        "Does a named bus group prefix its member names with a dot?",
        f"A bus labelled B_GRP{{ALPHA BETA}}. Two resistors {' and '.join(grp_refs)}"
        f" wired to it, their wires labelled B_GRP.ALPHA and B_GRP.BETA.",
        "the two nets are named B_GRP.ALPHA and B_GRP.BETA, path-qualified.",
    )

    at = p.take(2)
    bus_y = at[1] - RUN * 2
    bus_left = at[0] - 5.08
    bus_right = at[0] + CELL_W + 5.08
    sheet.bus((bus_left, bus_y), (bus_right, bus_y))
    sheet.label("B_ODD[0..1]", (bus_left, bus_y))
    ref_a, pin_a = p.probe(at)
    wire_top = (pin_a[0], bus_y + 2.54)
    sheet.wire(pin_a, wire_top)
    sheet.bus_entry(wire_top, (2.54, -2.54))
    sheet.label("B_ODD0", pin_a)
    # A member label placed ON the bus line itself, not on a member wire.
    sheet.label("B_ODD1", ((bus_left + bus_right) / 2, bus_y))
    ref_b, pin_b = p.probe((at[0] + CELL_W, at[1]))
    add(
        "B_MEMBER_ON_BUS",
        "What happens when a member name is written directly ON the bus line?",
        f"A bus labelled B_ODD[0..1]. {ref_a} reaches it normally through a labelled"
        f" wire. A second label B_ODD1 sits on the BUS line itself. {ref_b} is left"
        f" floating as a reference point.",
        f"{ref_a} lands on B_ODD0. B_ODD1 on the bus is a name collision between a"
        f" bus and a net on one sheet, and the interesting part is what KiCad calls"
        f" the result. {ref_b} is unconnected.",
    )

    at = p.take(2)
    bus_y = at[1] - RUN * 2
    bus_left = at[0] - 5.08
    bus_right = at[0] + CELL_W + 5.08
    sheet.bus((bus_left, bus_y), (bus_right, bus_y))
    sheet.label("B_MID[0..1]", (bus_left, bus_y))
    ref_a, pin_a = p.probe(at)
    # The wire runs PAST the entry point, so the entry lands in its middle.
    sheet.wire(pin_a, (pin_a[0], bus_y - RUN))
    sheet.bus_entry((pin_a[0], bus_y + 2.54), (2.54, -2.54))
    sheet.label("B_MID0", pin_a)
    ref_b, pin_b = p.probe((at[0] + CELL_W, at[1]))
    sheet.wire(pin_b, (pin_b[0], bus_y + 2.54))
    sheet.bus_entry((pin_b[0], bus_y + 2.54), (2.54, -2.54))
    sheet.label("B_MID1", pin_b)
    add(
        "B_ENTRY_MID_WIRE",
        "Does a bus entry whose wire end lands mid-wire still connect?",
        f"A bus labelled B_MID[0..1]. {ref_b} reaches it the ordinary way."
        f" {ref_a}'s wire runs PAST the entry, so the entry touches its middle"
        f" rather than its end.",
        "both connect. Source gives a bus entry virtual connection points to the"
        " segments it merely touches, for exactly this drawing.",
    )

    # ---- E. pins with nothing attached ----------------------------------
    at = p.take()
    ref, pin1 = p.probe(at)
    sheet.no_connect(pin1)
    add(
        "P_NO_CONNECT",
        "What is a no-connect pin's net called, and is it in the netlist at all?",
        f"{ref} pin 1 carries a no-connect marker and nothing else.",
        "the pin forms its own net. It is isolated from every edge and NOT excluded"
        " from the netlist.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    add(
        "P_FLOATING",
        "What is a pin with nothing attached called?",
        f"{ref} pin 1 has no wire, no label and no marker.",
        f"unconnected-({ref}-Pad1). A separate final pass adds that prefix.",
    )

    at = p.take(2)
    ref_a, pin_a = p.probe(at)
    ref_b, pin_b = p.probe((at[0] + CELL_W, at[1]))
    bar_y = pin_a[1] - RUN
    sheet.wire(pin_a, (pin_a[0], bar_y))
    sheet.wire((pin_a[0], bar_y), (pin_b[0], bar_y))
    sheet.wire((pin_b[0], bar_y), pin_b)
    add(
        "P_DERIVED_NAME",
        "With two pins on an unlabelled net, which pin names it?",
        f"{ref_a} and {ref_b} joined pin 1 to pin 1 by wires, no label anywhere.",
        "one derived name from ONE of the two pins. Which one is the rule under"
        " test: the pin sort is by sheet path length, then by what follows.",
    )

    at = p.take()
    ref, pin1 = p.probe(at)
    end = (pin1[0], pin1[1] - RUN)
    sheet.wire(pin1, end)
    sheet.label("D_CLASS", end, kind="netclass_flag")
    add(
        "P_DIRECTIVE",
        "Does a netclass directive name a net?",
        f"{ref} pin 1 wired up, with a netclass directive on the far end.",
        f"NO. The directive attaches a class and drives no name, so the net keeps"
        f" a derived name from {ref}.",
    )

    return out


def render(
    sheet: Sheet,
    results: list[Result],
    *,
    uuid: str = "00000000-0000-4000-8000-000000000000",
    title: str = "connectivity rule probe",
    extra: list[str] | None = None,
    root: bool = True,
) -> str:
    """Render one schematic file.

    *root* controls the sheet_instances block, which only a root sheet carries;
    a child schematic in a real project has none.
    """
    body = [
        "(kicad_sch",
        "\t(version 20260306)",
        '\t(generator "eeschema")',
        '\t(generator_version "10.0")',
        f'\t(uuid "{uuid}")',
        '\t(paper "A0")',
        "\t(title_block",
        f'\t\t(title "{title}")',
        "\t)",
        "\t(lib_symbols",
        lib_symbol("Device", "R"),
        lib_symbol("power", "GND"),
        "\t)",
    ]
    for group in (
        sheet.junctions,
        sheet.no_connects,
        sheet.wires,
        sheet.buses,
        sheet.entries,
        sheet.labels,
        sheet.symbols,
    ):
        body.extend(group)
    body.extend(extra or [])
    if root:
        body.extend(
            [
                "\t(sheet_instances",
                '\t\t(path "/"',
                '\t\t\t(page "1")',
                "\t\t)",
                "\t)",
            ]
        )
    body.extend(["\t(embedded_fonts no)", ")"])
    return "\n".join(body) + "\n"


def intent(results: list[Result]) -> str:
    lines = [
        "# Probe intent",
        "",
        "Generated by `scripts/make_connectivity_probe.py`. One case per resistor.",
        "Pin 2 of every resistor goes to ground, so ground is the only net any two",
        "cases share. Pin 1 is the probe point.",
        "",
        "Read this beside the exported netlist. Where the netlist disagrees with a",
        "prediction, the prediction names the rule to go and read.",
        "",
        f"Cases: {len(results)}.",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result.key}",
                "",
                f"**Asks:** {result.question}",
                "",
                f"**Drawn:** {result.drawn}",
                "",
                f"**This engine predicts:** {result.predicted}",
                "",
            ]
        )
    return "\n".join(lines)


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
        '      "00000000-0000-4000-8000-000000000000",\n'
        '      "Root"\n'
        "    ]\n"
        "  ],\n"
        '  "text_variables": {}\n'
        "}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="project directory")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    sheet = Sheet(uuids=Uuids())
    results = place_all(sheet)

    (args.out / f"{PROJECT}.kicad_sch").write_text(render(sheet, results), encoding="utf-8")
    (args.out / f"{PROJECT}.kicad_pro").write_text(project_file(), encoding="utf-8")
    (args.out / "INTENT.md").write_text(intent(results), encoding="utf-8")

    print(f"{len(results)} cases, {sheet.uuids._next} items -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
