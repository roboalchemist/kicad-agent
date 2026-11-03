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
    """Every view of the design, and every disagreement between them.

    The BOM, the schematic and the board are three views of one design. They must name the same
    parts with the same values and footprints; anything else means one of them is stale. This is
    the single implementation used by the agent, the REST API and the CLI, so all three grade
    identically.
    """
    from kicad_harness.agent import requirements as requirements_mod
    from kicad_harness.engine.schematic import Schematic

    sch_parts: list[dict[str, Any]] = []
    if ws.schematic_path.is_file():
        try:
            sch_parts = [
                {"ref": p.reference, "value": p.value, "footprint": p.footprint}
                for p in Schematic.load(ws.schematic_path).placements()
                if not p.reference.startswith("#")
            ]
        except Exception:
            sch_parts = []

    brd_parts: list[dict[str, Any]] = []
    if ws.board_path.is_file():
        try:
            data = ws.board_path.read_bytes()
            board_hash, _ = svc.artifacts.materialize(data)
            brd_parts = [
                {"ref": f.get("ref", ""), "value": f.get("value", ""), "footprint": f.get("footprint", "")}
                for f in svc.artifacts.index(board_hash).get("footprints", [])
                if not str(f.get("ref", "")).startswith("#")
            ]
        except Exception:
            brd_parts = []

    # What the BOM would list: the schematic when there is one, else the board (see /api/.../bom).
    bom_parts = sch_parts or brd_parts

    wanted: list[dict[str, Any]] = []
    try:
        wanted = ws.requirements()
    except Exception:
        wanted = []
    requirement_gaps = requirements_mod.gaps(wanted, sch_parts + brd_parts) if wanted else []

    sources = {
        "bom": part_table(bom_parts),
        "schematic": part_table(sch_parts),
        "board": part_table(brd_parts),
    }
    # With no schematic the bom/board pair is the whole design; comparing an absent schematic
    # would report every part as missing from it, which is not a divergence.
    compared = {k: v for k, v in sources.items() if v}
    sync_gaps = compare_sources(compared) if len(compared) > 1 else []

    parity = part_refs([p["ref"] for p in sch_parts], [p["ref"] for p in brd_parts])
    if not sch_parts:
        parity["missing_from_schematic"] = []

    return {
        "schematic_refs": sorted(p["ref"] for p in sch_parts),
        "board_refs": sorted(p["ref"] for p in brd_parts),
        "requirements": wanted,
        "requirement_gaps": requirement_gaps,
        "sync_gaps": sync_gaps,
        **parity,
    }


def _footprint_name(footprint: str) -> str:
    """The footprint without its library nickname: the board index and the schematic differ on
    whether ``Lib:Name`` is included, and that is not a divergence."""
    return footprint.split(":")[-1].strip().lower()


def part_table(parts: Iterable[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """``{ref: (value, footprint)}`` for the parts in one source."""
    table: dict[str, tuple[str, str]] = {}
    for part in parts:
        ref = str(part.get("ref") or "")
        if not ref or ref.startswith("#"):
            continue
        table[ref] = (
            str(part.get("value") or "").strip(),
            _footprint_name(str(part.get("footprint") or "")),
        )
    return table


def compare_sources(sources: dict[str, dict[str, tuple[str, str]]]) -> list[str]:
    """Every disagreement between sources, as human-readable lines.

    The BOM, the schematic and the board must name the same parts with the same values; any
    difference is a gap, because each is a different view of one design.
    """
    names = list(sources)
    gaps: list[str] = []
    for ref in sorted({r for table in sources.values() for r in table}):
        present = {name: sources[name].get(ref) for name in names}
        holders = [name for name, entry in present.items() if entry is not None]
        if len(holders) != len(names):
            missing = [name for name in names if present[name] is None]
            gaps.append(f"{ref}: missing from {', '.join(missing)}")
            continue
        values = {name: (present[name] or ("", ""))[0] for name in names}
        if len({v for v in values.values() if v}) > 1:
            gaps.append(f"{ref}: value differs ({', '.join(f'{n}={v}' for n, v in values.items())})")
        footprints = {name: (present[name] or ("", ""))[1] for name in names}
        if len({f for f in footprints.values() if f}) > 1:
            gaps.append(f"{ref}: footprint differs ({', '.join(f'{n}={f}' for n, f in footprints.items())})")
    return gaps


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
