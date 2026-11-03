"""Adversarial verification: does the design actually contain what was asked for?

The agent both writes the goal and grades itself against it, so a narrow goal (ERC on a
schematic that simply omits half the parts) can pass while the brief is undelivered. Two checks
here are independent of the agent's claims:

- ``parity``: the schematic and the board must describe the same set of parts. This is purely
  mechanical and catches the common failure of a board carrying parts the schematic never had.
- ``audit``: a reviewer reads the original brief plus the *actual* artifacts and lists what is
  missing. It is deliberately instructed to be adversarial and to name concrete gaps only.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

# Reference prefixes that are not board parts.
_NON_PART = re.compile(r"^#")


def part_refs(schematic_refs: list[str], board_refs: list[str]) -> dict[str, Any]:
    """Compare the parts named by the schematic with those on the board.

    Power flags (``#PWR``/``#FLG``) are not parts and are ignored.
    """
    sch = {r for r in schematic_refs if r and not _NON_PART.match(r)}
    brd = {r for r in board_refs if r and not _NON_PART.match(r)}
    return {
        "missing_from_schematic": sorted(brd - sch),
        "missing_from_board": sorted(sch - brd),
        "schematic_count": len(sch),
        "board_count": len(brd),
    }


def design_parts(svc: Any, ws: Any) -> dict[str, Any]:
    """Refs on the schematic and on the board, plus the parity comparison.

    One implementation shared by the agent, the REST API and the CLI so every evaluator sees the
    same numbers.
    """
    from kicad_harness.engine.schematic import Schematic

    sch: list[str] = []
    if ws.schematic_path.is_file():
        try:
            sch = [p.reference for p in Schematic.load(ws.schematic_path).placements()]
        except Exception:
            sch = []
    brd: list[str] = []
    if ws.board_path.is_file():
        try:
            data = ws.board_path.read_bytes()
            board_hash, _ = svc.artifacts.materialize(data)
            brd = [f.get("ref", "") for f in svc.artifacts.index(board_hash).get("footprints", [])]
        except Exception:
            brd = []
    from kicad_harness.agent import requirements as requirements_mod

    parts: list[dict[str, Any]] = []
    if ws.schematic_path.is_file():
        try:
            from kicad_harness.engine.schematic import Schematic as _S

            parts += [{"ref": p.reference, "value": p.value, "footprint": p.footprint}
                      for p in _S.load(ws.schematic_path).placements()]
        except Exception:
            pass
    if ws.board_path.is_file():
        try:
            data = ws.board_path.read_bytes()
            h, _ = svc.artifacts.materialize(data)
            parts += [{"ref": f.get("ref", ""), "value": f.get("value", ""),
                       "footprint": f.get("footprint", "")}
                      for f in svc.artifacts.index(h).get("footprints", [])]
        except Exception:
            pass

    # Countable demands in the brief ("16 relays") — leaving them out is a failure, not silence.
    wanted = []
    try:
        wanted = ws.requirements()
    except Exception:
        wanted = []
    requirement_gaps = requirements_mod.gaps(wanted, parts) if wanted else []

    parity = part_refs(sch, brd)
    if not sch:
        # A design imported from a .kicad_pcb has no schematic at all; that is not a parity
        # failure, so nothing is reported as missing.
        parity["missing_from_schematic"] = []
    return {
        "schematic_refs": sorted(sch),
        "board_refs": sorted(brd),
        "requirements": wanted,
        "requirement_gaps": requirement_gaps,
        **parity,
    }


class AuditGap(BaseModel):
    """One thing the brief asked for that the design does not deliver."""

    requirement: str = Field(description="The requirement from the brief, quoted or paraphrased")
    problem: str = Field(description="What is missing or wrong in the design")


class AuditResult(BaseModel):
    """A reviewer's verdict on whether the design fulfils the brief."""

    complete: bool = Field(description="True only if every requirement in the brief is met")
    gaps: list[AuditGap] = Field(default_factory=list)


AUDIT_INSTRUCTIONS = """\
You are an adversarial design reviewer. A different agent claims it has finished building a
PCB from a brief. Your only job is to find what is still missing or wrong. You are NOT on the
agent's side: assume the work is incomplete until the evidence shows otherwise.

You will be given the original brief and a factual inventory of the design. Check the inventory
against every element the brief asks for: part counts, part types, connectors, orientation,
power, and anything the brief states explicitly. Treat these as requirements:

- If the brief says "16 relays", the board AND schematic must each contain 16 relays (K refs).
- If it names a connector type or a host board, that part must be present with the right count.
- A schematic missing parts that the board has is a gap, even if ERC passes.
- Do not invent requirements the brief does not state.

Report only gaps you can name concretely from the inventory. If the inventory genuinely
satisfies every requirement, answer complete=true with no gaps.
"""


def render_inventory(brief: str | None, facts: dict[str, Any]) -> str:
    """The text handed to the reviewer: the brief plus the design's real inventory."""
    lines = [
        "ORIGINAL BRIEF:",
        (brief or "(no brief recorded)").strip(),
        "",
        "DESIGN INVENTORY (facts, not claims):",
    ]
    for key, value in facts.items():
        pretty = json.dumps(value, indent=2, default=str) if not isinstance(value, str) else value
        lines.append(f"- {key}: {pretty}")
    return "\n".join(lines)


def summarise(audit: AuditResult) -> str:
    """A one-line-per-gap summary for the retry prompt."""
    return "; ".join(f"[{g.requirement}] {g.problem}" for g in audit.gaps) or "none"
