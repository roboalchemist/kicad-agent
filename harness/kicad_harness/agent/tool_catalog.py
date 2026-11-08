"""Global (per-harness, not per-project) description of the tools and the design workflow.

The Settings area in the UI is a reference for a human and for the agent: every tool with what it
does, which stage it belongs to and how to use it, plus the workflow and guidance for the BOM,
schematic and board phases. Keeping it here means the UI, the API and any prompt share one source
of truth rather than drifting apart.

Descriptions are taken from the live agent tool docstrings (so they cannot go stale); this module
only adds the stage and the short "when to use it" line.
"""

from __future__ import annotations

from typing import Any

LEVELS: list[dict[str, str]] = [
    {"id": "global", "label": "Global"},
    {"id": "bom", "label": "BOM / sourcing"},
    {"id": "schematic", "label": "Schematic"},
    {"id": "board", "label": "Board layout"},
]

# tool name -> (level, guidance)
TOOLS: dict[str, tuple[str, str]] = {
    # -- global ---------------------------------------------------------------------------
    "get_design": ("global", "Start every session here: outline, parts, nets, DRC and goal state."),
    "set_goal": ("global", "Turn the user's words into machine-checkable criteria before editing."),
    "list_versions": ("global", "Use before restoring, to find the version you want."),
    "diff_versions": ("global", "Compare netlists or BOMs between two revisions before trusting a change."),
    "restore_version": ("global", "Restore a known-good state instead of hand-reverting many edits."),
    "list_reviews": ("global", "Read the user's review comments before planning the next edit."),
    "reply_review": ("global", "Answer a review comment; include the reason, not just 'done'."),
    "mark_review_addressed": ("global", "Mark a review comment handled once the change is committed."),
    "read_reference": ("global", "Pull the full PCB rule/reference for a topic when the decision is not obvious."),
    # -- BOM / sourcing -------------------------------------------------------------------
    "find_parts": ("bom", "Search by part number or category; prefer basic parts with stock."),
    "read_part_datasheet": ("bom", "Confirm a pinout or rating; never invent pin numbers."),
    "set_bom_alternates": ("bom", "Record second-source LCSC codes; they appear in the manufacturing BOM."),
    # -- schematic ------------------------------------------------------------------------
    "generate_schematic": ("schematic", "Draw a whole design from a parts/nets list instead of placing symbols one by one."),
    "add_symbol": ("schematic", "Place one symbol when extending or fixing a sheet."),
    "connect_pins": ("schematic", "Wire pin to pin and name the net; the router avoids crossings."),
    "move_symbol": ("schematic", "Move/rotate a symbol; its reference/value text follows."),
    "set_symbol_value": ("schematic", "Change a value; on a power symbol this changes the net it names."),
    "tidy_schematic": ("schematic", "Relayout by circuit flow with passives beside their owner."),
    "route_schematic": ("schematic", "Draw direct wires for point-to-point nets, ERC-checked and reversible."),
    "start_board": ("schematic", "Only after ERC 0 and legible 0: create the board from the netlist."),
    # -- board ----------------------------------------------------------------------------
    "set_board_outline": ("board", "Set the mechanical outline first; everything else depends on it."),
    "set_net_class": ("board", "Declare width/clearance/impedance per net group; new tracks use it and check flags narrower ones."),
    "set_stackup": ("board", "Declare copper layers, material and thickness; check compares it with the board."),
    "export_manufacturing": ("board", "Write Gerbers, drill, pick-and-place and BOM plus a manifest for ordering."),
    "move_footprint": ("board", "Place by function, then route; coordinates are absolute mm."),
    "rotate_footprint": ("board", "Rotate about the anchor to face pins toward their partners."),
    "flip_footprint": ("board", "Move a part to the other side when the top is congested."),
    "add_footprint": ("board", "Add a mechanical or test part not in the schematic."),
    "find_footprints": ("board", "Find the footprint a placed part needs."),
    "add_track": ("board", "Hand-route short or critical nets; prefer 45-degree bends."),
    "add_via": ("board", "Change layer where a track must cross."),
    "add_thermal_vias": ("board", "Ring vias around a power pad so heat reaches the plane."),
    "route_diff_pair": ("board", "Route a differential pair as two parallel tracks with one spacing."),
    "match_length": ("board", "Measure routed lengths and check a group matches within tolerance."),
    "place_silkscreen": ("board", "Move reference texts to collision-free spots after placement."),
    "autoroute": ("board", "Route the bulk automatically; rip up first to re-route cleanly."),
    "add_copper_zone": ("board", "Pour a plane (GND, power) after placement."),
    "refill_zones": ("board", "Refill every pour after moving parts or tracks."),
    "measure": ("board", "Measure edge-to-edge copper distances instead of guessing."),
    "query": ("board", "Read pads/tracks/zones/vias by ref or net for exact facts."),
    "check": ("global", "Phase-aware check (ERC + legibility + goal, or DRC + goal) with the image."),
    "render": ("global", "Look at the board or the schematic; numbers miss crowded or marooned layouts."),
    "delete_board_items": ("board", "Delete by selector: nets rip up copper, refs remove parts, uuids any item."),
    "delete_schematic_items": ("schematic", "Remove a net's wires/labels, or a symbol and the wires on it."),
    "label_net": ("schematic", "Attach a net to one pin or a point; use connect_pins for pin-to-pin."),
}


WORKFLOWS: dict[str, list[dict[str, object]]] = {
    "bom": [
        {"step": "Requirements & constraints",
         "detail": "Performance targets, power budget, thermal limits, form factor and compliance "
                   "before choosing a single part.",
         "tools": ["set_goal", "get_design"], "gate": "brief captured as measurable criteria",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Component selection & footprints",
         "detail": "Every part needs a real KiCad footprint and a net class; a symbol without a "
                   "footprint cannot reach the board.",
         "tools": ["find_parts", "find_footprints", "find_parts"],
         "gate": "footprint exists for every symbol", "ref": "pcb-bom-components#Footprint Verification Process"},
        {"step": "Availability & lifecycle",
         "detail": "Stock, price, tier, lead time and lifecycle state; second sources for any part "
                   "on the critical path.",
         "tools": ["find_parts", "find_parts", "find_parts"],
         "gate": "every part in stock; alternates identified",
         "ref": "pcb-bom-components#Component Availability & Lead Time Planning"},
        {"step": "Ratings & derating",
         "detail": "Confirm voltage/current/temperature margins from the datasheet - not from the "
                   "catalogue title.",
         "tools": ["read_part_datasheet", "find_parts"], "gate": "derating rules met",
         "ref": "pcb-bom-components#Component Derating Guidelines"},
        {"step": "Package & assembly",
         "detail": "Package choice, MSL, ESD and pick-and-place compatibility for the assembler.",
         "tools": ["find_parts"], "gate": "assembler can place and handle every package",
         "ref": "pcb-bom-components#Component Package Selection"},
        {"step": "Record the part identity",
         "detail": "LCSC + MPN on the symbol; identical parts collapse to one line by part number, "
                   "not by schematic label.",
         "tools": ["get_design"], "gate": "every BOM line names an orderable part",
         "ref": "pcb-bom-components#BOM Documentation Requirements"},
        {"step": "Document alternates",
         "detail": "Second sources, supplier notes and handling constraints travel with the BOM.",
         "tools": ["list_reviews", "reply_review"], "gate": "alternates recorded where needed",
         "ref": "pcb-bom-components#Alternate/Second Source Strategy"},
        {"step": "Audit & freeze",
         "detail": "Resolve unassigned codes, check basic coverage and the measured reason for every "
                   "extended part.",
         "tools": ["check", "check"], "gate": "BOM = schematic = board (sync criterion)",
         "ref": "pcb-design-process#Phase Details"},
    ],
    "schematic": [
        {"step": "Requirements & concept",
         "detail": "Turn the request into criteria: function, rails, power budget, thermal and "
                   "mechanical envelope, compliance.",
         "tools": ["set_goal", "get_design"], "gate": "criteria saved",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Source the parts first",
         "detail": "Run the sourcing workflow; a part without a stocked, footprinted identity will "
                   "fail later.",
         "tools": ["find_parts", "find_parts", "read_part_datasheet"],
         "gate": "all parts stocked and footprinted",
         "ref": "pcb-bom-components#Footprint Verification Process"},
        {"step": "Net classes & naming",
         "detail": "Name power, ground and high-speed nets before drawing so layout can route by "
                   "class later.",
         "tools": ["label_net", "label_net"], "gate": "rails and critical nets named",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Draw the circuit",
         "detail": "generate_schematic for a whole design, add_symbol/connect_pins to extend or "
                   "repair it.",
         "tools": ["generate_schematic", "add_symbol", "connect_pins"],
         "gate": "every part placed, every pin connected or no-connect",
         "ref": "pcb-design-process#Strict Dependencies"},
        {"step": "Make ERC clean",
         "detail": "Fix errors and floating pins before anything cosmetic; power flags for every "
                   "supplied rail.",
         "tools": ["check", "get_design"], "gate": "ERC errors 0",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Arrange by circuit",
         "detail": "Stages left to right, passives beside the part they serve, power symbols on the "
                   "nets they actually name.",
         "tools": ["check", "tidy_schematic", "move_symbol"],
         "gate": "findings empty; legible score 0",
         "ref": "pcb-placement#Grouping"},
        {"step": "Draw connections",
         "detail": "Wires for point-to-point nets; labels only for nets that cross stages, so the "
                   "sheet reads without chasing names.",
         "tools": ["route_schematic", "label_net"], "gate": "router reports no netlist change",
         "ref": "pcb-routing#Trace Geometry"},
        {"step": "Review the picture",
         "detail": "Look at the rendered sheet: rows out of sequence, colliding reference text and "
                   "marooned parts do not show up in the score.",
         "tools": ["render", "check"],
         "gate": "no obvious visual fault", "ref": "pcb-silkscreen#Text Sizing"},
        {"step": "Hand over to layout",
         "detail": "Create the board from the finished netlist only when the schematic is correct, "
                   "readable and sourced.",
         "tools": ["start_board"], "gate": "ERC 0, legible 0, netlist complete",
         "ref": "pcb-design-process#Strict Dependencies"},
    ],
    "board": [
        {"step": "Stackup & design rules first",
         "detail": "Layer count, materials, impedance and design rules must exist before placement so "
                   "violations surface immediately.",
         "tools": ["set_stackup", "set_net_class", "get_design"], "gate": "stackup and rules approved",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Outline, holes & keepouts",
         "detail": "Board outline, mounting holes, connector locations and mechanical keepouts from "
                   "the enclosure.",
         "tools": ["set_board_outline"], "gate": "outline and holes match the mechanics",
         "ref": "pcb-mechanical#Board Outline"},
        {"step": "Place fixed parts first",
         "detail": "Connectors, mounting holes and test points are anchors; they define the routing "
                   "topology.",
         "tools": ["move_footprint", "rotate_footprint", "flip_footprint"],
         "gate": "every fixed part within 0.1 mm of its mechanical position",
         "ref": "pcb-placement#Priority Order"},
        {"step": "Critical ICs & decoupling",
         "detail": "ICs next, decoupling capacitors within 3-5 mm of their power pins, timing parts "
                   "adjacent to their IC.",
         "tools": ["move_footprint", "rotate_footprint", "measure"],
         "gate": "decoupling and timing parts at their IC",
         "ref": "pcb-placement#Priority Order"},
        {"step": "Group, orient, plan silkscreen",
         "detail": "Keep analog/digital/power regions distinct; orient similar parts consistently; "
                   "leave room for readable references.",
         "tools": ["move_footprint", "move_footprint"],
         "gate": "placement review clean", "ref": "pcb-placement#Orientation"},
        {"step": "Route power and planes",
         "detail": "Power distribution and ground planes first - wide traces and planes before "
                   "signals.",
         "tools": ["autoroute", "add_track", "add_copper_zone", "refill_zones"],
         "gate": "rails and planes continuous", "ref": "pcb-routing#Priority Order"},
        {"step": "Route critical, then general",
         "detail": "Clocks and differential pairs before standard signals; 45-degree bends, 3W "
                   "spacing, continuous return paths, minimal vias.",
         "tools": ["autoroute", "add_track", "add_via", "delete_board_items"],
         "gate": "unconnected 0", "ref": "pcb-routing#Signal Integrity"},
        {"step": "Pours, stitching & thermal",
         "detail": "Refill after the last move; thermal relief on pads, stitching vias, no floating "
                   "islands.",
         "tools": ["add_copper_zone", "refill_zones", "add_via", "add_thermal_vias"],
         "gate": "pours refilled, copper balanced",
         "ref": "pcb-copper-pours#Thermal Relief"},
        {"step": "Verify like a reviewer",
         "detail": "DRC zero, netlist compare, edge/mechanical clearance, silkscreen legibility, DFM "
                   "for the assembler.",
         "tools": ["check", "measure", "render"],
         "gate": "DRC 0, unconnected 0, size/edge criteria pass",
         "ref": "pcb-design-process#Phase Details"},
        {"step": "Manufacturing files & handoff",
         "detail": "Gerbers, drill, pick-and-place and the final BOM; freeze the version and record "
                   "the review.",
         "tools": ["export_manufacturing", "list_versions", "restore_version"],
         "gate": "outputs generated and version tagged",
         "ref": "pcb-design-process#Phase Details"},
    ],
}


def workflow_issues() -> list[str]:
    """Every workflow step must cite a real rule section; drift is a test failure."""
    from . import knowledge

    headings: dict[str, set[str]] = {}
    for doc in knowledge.index():
        headings.setdefault(doc["id"], set()).update(doc["headings"])
    issues: list[str] = []
    for phase, steps in WORKFLOWS.items():
        if len(steps) < 6:
            issues.append(f"{phase}: only {len(steps)} steps")
        for step in steps:
            label = f"{phase} / {step.get('step')}"
            for field in ("detail", "tools", "gate", "ref"):
                if not step.get(field):
                    issues.append(f"{label}: missing {field}")
            ref = str(step.get("ref") or "")
            topic, _, heading = ref.partition("#")
            if topic not in headings:
                issues.append(f"{label}: unknown source {topic!r}")
            elif heading and heading not in headings[topic]:
                issues.append(f"{label}: unknown section {heading!r} in {topic}")
    return issues

GUIDANCE: dict[str, str] = {
    "bom": (
        "The BOM is the orderable truth of the design. A part only exists if JLCPCB stocks it, "
        "so source before drawing: prefer basic parts, verify stock and tier, confirm pinouts from "
        "a datasheet, and record the LCSC code on the symbol. Identical parts must collapse to one "
        "line; a part number is the identity, not the schematic's label. Extended parts are allowed "
        "only when no basic part meets the value, package and rating, and the reason must be a "
        "measured number against the datasheet limit."
    ),
    "schematic": (
        "A schematic is finished when it is electrically correct AND readable. ERC comes first "
        "(errors, floating pins, power flags). Then arrange by circuit, not by reference: stages "
        "flow left to right from controller to output, each passive sits beside the part it "
        "serves, and each power symbol names the net it is actually on. Draw wires where two pins "
        "connect; labels are for nets that cross stages. Tidy and route are tools, not a substitute "
        "for judgement - look at the rendered sheet and fix what a reader would trip over."
    ),
    "board": (
        "The board is finished when DRC is clean, every net is routed, the mechanical constraints "
        "hold and the picture looks right. Place by function before routing, rip up rather than "
        "route around a bad placement, refill pours after any move, and use the goal criteria as "
        "the contract: DRC errors, unconnected items, size, edge clearance and any user-specified "
        "regions or distances. A clean DRC with a crushed, unreadable layout is not finished."
    ),
}


def tool_level(name: str) -> str:
    return TOOLS.get(name, ("global", ""))[0]


def tool_guidance(name: str) -> str:
    return TOOLS.get(name, ("global", ""))[1]


def describe_tools(descriptions: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every known tool with its stage, description and guidance.

    ``descriptions`` is the live agent's ``{name: docstring}``; unknown tools are still listed
    (defaulting to global) so a new tool can never disappear from the settings page.
    """
    descriptions = descriptions or {}
    names = set(descriptions) | set(TOOLS)
    out: list[dict[str, Any]] = []
    for name in sorted(names):
        level, guidance = TOOLS.get(name, ("global", ""))
        text = (descriptions.get(name) or "").strip()
        summary = text.split("\n", 1)[0]
        if len(summary) > 220:
            summary = summary[:217].rstrip() + "..."
        out.append({
            "name": name,
            "level": level,
            "summary": summary,
            "guidance": guidance,
            "description": text,
        })
    return out


def settings_payload(descriptions: dict[str, str] | None = None, model: str | None = None) -> dict[str, Any]:
    from . import knowledge

    return {
        "model": model,
        "levels": LEVELS,
        "tools": describe_tools(descriptions),
        "workflows": WORKFLOWS,
        "guidance": GUIDANCE,
        "playbooks": {phase: knowledge.digest(phase) for phase in ("bom", "schematic", "board")},
        "references": knowledge.index(),
    }

def phase_instructions(phase: str) -> str:
    """The workflow + standard for the design phase, ready to put in the run's instructions.

    Sourcing is cross-cutting (it applies while drawing and again when the BOM is audited), so it
    is always included; the current phase's own workflow follows. This is injected on every step,
    not just shown in the Settings page, so a run is steered by the same text a human reads.
    """
    lines: list[str] = ["Sourcing workflow (applies in every phase):"]
    for index, step in enumerate(WORKFLOWS["bom"], start=1):
        lines.append(f"{index}. {step['step']} — {step['detail']}")
    lines.append(f"Sourcing standard: {GUIDANCE['bom']}")

    key = "board" if phase == "board" else "schematic"
    lines.append("")
    lines.append(f"{key.title()} workflow (each step has a gate; the source is a rule section):")
    for index, step in enumerate(WORKFLOWS[key], start=1):
        lines.append(
            f"{index}. {step['step']} — {step['detail']} "
            f"[gate: {step['gate']}; tools: {', '.join(step['tools'])}; {step['ref']}]"
        )
    lines.append(f"{key.title()} standard: {GUIDANCE[key]}")

    # The repo's own rules, not a paraphrase: a cited digest per phase plus the tool to read more.
    from . import knowledge

    lines.append("")
    lines.append(knowledge.digest(phase))
    return "\n".join(lines)

