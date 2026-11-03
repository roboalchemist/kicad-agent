"""Turn a finished schematic into a netlisted board. See DESIGN.md, "Phases".

The schematic phase ends when the board phase begins: every symbol with a footprint becomes a
placed footprint on a new board, and the nets the schematic names become pad nets. Layout then
starts from a board that is already electrically correct, which is the whole point of drawing
the schematic first.

Footprints are placed on a grid roughly in schematic order, so the layout starts tidy rather
than overlapping at the origin. The agent moves them from there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .schematic import Schematic, board_netlist

DEFAULT_SPACING_MM = 12.7
DEFAULT_BOARD_MM = (60.0, 50.0)
# Keeps parts clear of the edge even before the agent places them deliberately.
EDGE_MARGIN_MM = 8.0


class BoardEngine(Protocol):
    """The parts of ``engine.client.Engine`` seeding needs."""

    def create_board(self, board: str | Path, width: float = ..., height: float = ...) -> dict: ...

    def add_footprint(
        self, board: str | Path, footprint: str, ref: str, value: str, x: float, y: float,
        rotation: float = ..., side: str = ...,
    ) -> dict: ...

    def set_pad_net(self, board: str | Path, ref: str, pad: str | int, net: str) -> dict: ...


@dataclass
class SeedResult:
    """What seeding produced, including anything it had to skip."""

    board_path: Path
    placed: list[str] = field(default_factory=list)
    nets: dict[str, int] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": str(self.board_path),
            "placed": self.placed,
            "nets": self.nets,
            "skipped": self.skipped,
        }


class SeedError(RuntimeError):
    """Seeding could not produce a board; the message says which part blocked it."""


def seed_board(
    schematic: Schematic,
    board_path: str | Path,
    engine: BoardEngine,
    *,
    netlist: dict[str, Any] | None = None,
    width: float | None = None,
    height: float | None = None,
    spacing: float = DEFAULT_SPACING_MM,
) -> SeedResult:
    """Create ``board_path`` from a schematic's components and nets.

    ``netlist`` should be KiCad's own export (``kicad_cli.export_netlist`` + ``parse_netlist``),
    which is the authority on connectivity. Without it, the schematic's own components are used
    and nets fall back to ``board_netlist``.

    Raises ``SeedError`` if the schematic has nothing to lay out.
    """
    plan = board_netlist(schematic)
    if netlist is None:
        netlist = {
            "components": plan["components"],
            "nets": {name: [(m["ref"], str(m["pin"])) for m in members] for name, members in plan["nets"].items()},
        }
    components = [c for c in netlist["components"] if c.get("footprint")]
    if not components:
        raise SeedError(
            "the schematic has no symbols with a footprint, so there is nothing to lay out; "
            "give each part a footprint first"
        )
    # Virtual parts (PWR_FLAG, power symbols) carry a "#" reference and never reach the board.
    board_refs = {c["ref"] for c in components if not str(c["ref"]).startswith("#")}
    components = [c for c in components if str(c["ref"]).startswith("#") is False and c["ref"] in board_refs]

    board_path = Path(board_path)
    cols = max(1, math.ceil(math.sqrt(len(components))))
    rows = math.ceil(len(components) / cols)
    board_w = width if width is not None else max(DEFAULT_BOARD_MM[0], cols * spacing + 2 * EDGE_MARGIN_MM)
    board_h = height if height is not None else max(DEFAULT_BOARD_MM[1], rows * spacing + 2 * EDGE_MARGIN_MM)

    engine.create_board(board_path, width=board_w, height=board_h)
    result = SeedResult(board_path=board_path)

    layout: dict[str, tuple[float, float]] = {}
    for i, component in enumerate(components):
        col, row = i % cols, i // cols
        x = round(EDGE_MARGIN_MM + col * spacing, 4)
        y = round(EDGE_MARGIN_MM + row * spacing, 4)
        try:
            engine.add_footprint(
                board_path,
                component["footprint"],
                component["ref"],
                component.get("value") or "",
                x,
                y,
            )
        except Exception as exc:  # a missing footprint must not stop the rest of the board
            result.skipped.append({"ref": component["ref"], "reason": str(exc)})
            continue
        layout[component["ref"]] = (x, y)
        result.placed.append(component["ref"])

    for net, members in netlist["nets"].items():
        assigned = 0
        for ref, pin in members:
            if ref not in layout:
                continue
            try:
                engine.set_pad_net(board_path, ref, pin, net)
            except Exception as exc:
                result.skipped.append({"ref": ref, "reason": f"net {net} pin {pin}: {exc}"})
                continue
            assigned += 1
        if assigned:
            result.nets[net] = assigned

    if not result.placed:
        raise SeedError("none of the schematic's footprints could be loaded: " + "; ".join(
            f"{s['ref']}: {s['reason']}" for s in result.skipped[:4]
        ))
    return result


@dataclass
class ReconcileResult:
    """What changed when the board was brought back in line with the schematic."""

    added: list[str] = field(default_factory=list)
    nets: dict[str, int] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    only_on_board: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "nets": self.nets,
            "skipped": self.skipped,
            "only_on_board": self.only_on_board,
        }


def reconcile_board(
    schematic: Schematic,
    board_path: str | Path,
    engine: BoardEngine,
    *,
    netlist: dict[str, Any] | None = None,
    spacing: float = DEFAULT_SPACING_MM,
) -> ReconcileResult:
    """Bring an existing board in line with the schematic without disturbing layout.

    The schematic and the board are views of one design, so editing the schematic must not throw
    the layout away. Parts the schematic gained are placed in free space; pad nets are re-applied
    from the netlist; parts present only on the board are reported (never silently deleted, since
    they may carry routing).
    """
    board_path = Path(board_path)
    index = engine.index(board_path)
    on_board = {f.get("ref"): f for f in index.get("footprints", []) if f.get("ref")}

    if netlist is None:
        plan = board_netlist(schematic)
        netlist = {
            "components": plan["components"],
            "nets": {name: [(m["ref"], str(m["pin"])) for m in members] for name, members in plan["nets"].items()},
        }
    wanted = {c["ref"]: c for c in netlist["components"] if c.get("footprint") and not str(c["ref"]).startswith("#")}
    result = ReconcileResult()

    ex, ey, ew, eh = index["board"]["edge_bbox"]
    cols = max(1, int(max(ew - 2 * EDGE_MARGIN_MM, spacing) // spacing))
    rows = max(1, int(max(eh - 2 * EDGE_MARGIN_MM, spacing) // spacing))
    slot = 0
    for ref, component in wanted.items():
        if ref in on_board:
            continue
        while slot < cols * rows:
            x = round(ex + EDGE_MARGIN_MM + (slot % cols) * spacing, 3)
            y = round(ey + EDGE_MARGIN_MM + (slot // cols) * spacing, 3)
            slot += 1
            if not any(abs((f.get("x") or 0) - x) < spacing / 2 and abs((f.get("y") or 0) - y) < spacing / 2
                       for f in on_board.values()):
                break
        try:
            engine.add_footprint(board_path, component["footprint"], ref, component.get("value") or "", x, y)
        except Exception as exc:
            result.skipped.append({"ref": ref, "reason": str(exc)})
            continue
        result.added.append(ref)

    for net, members in netlist["nets"].items():
        assigned = 0
        for ref, pin in members:
            if ref not in wanted:
                continue
            try:
                engine.set_pad_net(board_path, ref, pin, net)
            except Exception as exc:
                result.skipped.append({"ref": ref, "reason": f"net {net} pin {pin}: {exc}"})
                continue
            assigned += 1
        if assigned:
            result.nets[net] = assigned

    result.only_on_board = sorted(set(on_board) - set(wanted))
    return result
