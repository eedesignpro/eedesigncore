"""Prove that installed KiCad reads and executes generated custom rules."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eedesigncore import (
    DrcBound,
    DrcClause,
    DrcConstraint,
    DrcConstraintType,
    DrcFile,
    DrcRule,
)


def test_generated_rules_execute_in_kicad(tmp_path: Path) -> None:
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if not cli:
        pytest.skip("Set KICAD_CLI to run the installed KiCad comparison")
    board = tmp_path / "probe.kicad_pcb"
    fixture = Path(__file__).parents[1] / "fixtures/pcb/pad_shapes_probe.kicad_pcb"
    shutil.copyfile(fixture, board)
    board.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    environment = dict(os.environ)
    environment["KICAD_CONFIG_HOME"] = str(tmp_path / "settings")
    environment["KICAD_DOCUMENTS_HOME"] = str(tmp_path / "documents")
    arguments = {
        DrcConstraintType.DISALLOW: ["track"],
        DrcConstraintType.ZONE_CONNECTION: ["thermal_reliefs"],
        DrcConstraintType.MIN_RESOLVED_SPOKES: ["2"],
        DrcConstraintType.ASSERTION: ["A.NetName != 'impossible'"],
    }
    unitless = {
        DrcConstraintType.VIA_COUNT,
        DrcConstraintType.VIA_DANGLING,
        DrcConstraintType.TRACK_ANGLE,
        DrcConstraintType.BRIDGED_MASK,
    }
    rules = []
    for kind in DrcConstraintType:
        constraint = DrcConstraint(
            type=kind,
            arguments=arguments.get(kind, []),
            bounds=[]
            if kind in arguments
            else [DrcBound(kind="min", expression="1" if kind in unitless else "0.1mm")],
        )
        rules.append(
            DrcRule(
                name=kind.value,
                clauses=[
                    DrcClause(kind="condition", value="A.NetName == 'unused-probe-net'"),
                    constraint,
                ],
            )
        )
    rules.append(
        DrcRule(
            name="reader-proof",
            clauses=[
                DrcClause(kind="condition", value="A.Type == 'Pad'"),
                DrcConstraint(type=DrcConstraintType.ASSERTION, arguments=["0"]),
            ],
        )
    )
    path = board.with_suffix(".kicad_dru")
    DrcFile(rules=rules).to_file(path)
    for valid in [True, False]:
        if not valid:
            path.write_text(
                "(version 1)\n(rule broken (constraint not_a_real_constraint))\n",
                encoding="utf-8",
            )
        output = tmp_path / ("valid.json" if valid else "invalid.json")
        subprocess.run(
            [cli, "pcb", "drc", "--format", "json", "-o", str(output), str(board)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        proof = [
            violation
            for violation in report["violations"]
            if violation["type"] == "assertion_failure"
            and "reader-proof" in violation["description"]
        ]
        assert bool(proof) is valid
