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
    "design_summary": ("global", "Start every session here: outline, parts, nets, DRC and goal state."),
    "set_goal": ("global", "Turn the user's words into machine-checkable criteria before editing."),
    "list_versions": ("global", "Use before restoring, to find the version you want."),
    "restore_version": ("global", "Restore a known-good state instead of hand-reverting many edits."),
    "list_feedback": ("global", "Read the user's review comments before planning the next edit."),
    "reply_to_thread": ("global", "Answer a review comment; include the reason, not just 'done'."),
    "mark_addressed": ("global", "Mark a review comment handled once the change is committed."),
    # -- BOM / sourcing -------------------------------------------------------------------
    "find_parts": ("bom", "Search by part number or category; prefer basic parts with stock."),
    "part_categories": ("bom", "Browse when a keyword search returns nothing."),
    "check_part": ("bom", "Check stock, price, tier and datasheet availability before committing to a part."),
    "part_datasheet": ("bom", "Confirm a pinout or rating; never invent pin numbers."),
    # -- schematic ------------------------------------------------------------------------
    "generate_schematic_skidl": ("schematic", "Draw a whole design from a parts/nets list instead of placing symbols one by one."),
    "add_symbol": ("schematic", "Place one symbol when extending or fixing a sheet."),
    "remove_symbol": ("schematic", "Delete a part and the wires on its pins."),
    "connect_pins": ("schematic", "Wire pin to pin and name the net; the router avoids crossings."),
    "add_schematic_label": ("schematic", "Name a net at a point; use label_pin for a pin without a partner."),
    "add_schematic_text": ("schematic", "Annotate the sheet for the reader; notes connect nothing."),
    "label_pin": ("schematic", "Attach a net to a single pin with a stub and label."),
    "disconnect_schematic": ("schematic", "Clear a wrong net before redoing it; takes a net or a ref."),
    "draw_schematic_wires": ("schematic", "Make generator labels visible with stubs from each pin."),
    "move_schematic_symbol": ("schematic", "Move/rotate a symbol; its reference/value text follows."),
    "set_symbol_value": ("schematic", "Change a value; on a power symbol this changes the net it names."),
    "schematic_summary": ("schematic", "Read symbols, nets, floating pins, ERC and layout findings."),
    "check_schematic": ("schematic", "Run ERC + legibility + the goal in one call."),
    "check_schematic_layout": ("schematic", "See the score, findings and the rendered sheet."),
    "tidy_schematic": ("schematic", "Relayout by circuit flow with passives beside their owner."),
    "route_schematic": ("schematic", "Draw direct wires for point-to-point nets, ERC-checked and reversible."),
    "render_schematic": ("schematic", "Look at the sheet: scores miss ugly-but-clean layouts."),
    "start_board_layout": ("schematic", "Only after ERC 0 and legible 0: create the board from the netlist."),
    # -- board ----------------------------------------------------------------------------
    "set_board_outline": ("board", "Set the mechanical outline first; everything else depends on it."),
    "move_footprint": ("board", "Place by function, then route; coordinates are absolute mm."),
    "rotate_footprint": ("board", "Rotate about the anchor to face pins toward their partners."),
    "flip_footprint": ("board", "Move a part to the other side when the top is congested."),
    "move_reference_text": ("board", "Uncover a silk reference after placement."),
    "add_footprint": ("board", "Add a mechanical or test part not in the schematic."),
    "remove_footprint": ("board", "Delete a part the board should not carry."),
    "set_pad_net": ("board", "Change connectivity only when the schematic cannot express it; re-route after."),
    "find_footprints": ("board", "Find the footprint a placed part needs."),
    "add_track": ("board", "Hand-route short or critical nets; prefer 45-degree bends."),
    "add_via": ("board", "Change layer where a track must cross."),
    "autoroute": ("board", "Route the bulk automatically; rip up first to re-route cleanly."),
    "delete_tracks": ("board", "Rip up before moving parts or re-routing."),
    "delete_items": ("board", "Remove specific copper by UUID (stale stubs, keepouts)."),
    "add_copper_zone": ("board", "Pour a plane (GND, power) after placement."),
    "refill_zones": ("board", "Refill every pour after moving parts or tracks."),
    "render_view": ("board", "Look at the board; numbers miss crowded silkscreen and stray copper."),
    "measure": ("board", "Measure edge-to-edge copper distances instead of guessing."),
    "query": ("board", "Read pads/tracks/zones/vias by ref or net for exact facts."),
    "check": ("board", "DRC + all goal criteria + the rendered board in one call."),
}


WORKFLOWS: dict[str, list[dict[str, str]]] = {
    "bom": [
        {"step": "Read the brief", "detail": "List every part, value and rating the circuit needs before searching."},
        {"step": "Search the catalogue", "detail": "find_parts / part_categories. Prefer basic parts with deep stock."},
        {"step": "Verify each part", "detail": "check_part for stock, price and tier; part_datasheet for pinout and ratings."},
        {"step": "Record the part on the symbol", "detail": "LCSC + MPN properties, so BOM, schematic and board agree."},
        {"step": "Resolve and audit the BOM", "detail": "Resolve missing codes; check basic coverage and why the rest is extended."},
        {"step": "Keep it orderable", "detail": "Every line names a real JLCPCB part; the sync criterion enforces BOM = schematic = board."},
    ],
    "schematic": [
        {"step": "Set the goal", "detail": "set_goal with the user's intent and measurable criteria."},
        {"step": "Source the parts", "detail": "Run the BOM workflow first; a part without a footprint cannot reach the board."},
        {"step": "Draw the circuit", "detail": "generate_schematic_skidl for a new design, add_symbol/connect_pins to extend it."},
        {"step": "Make ERC clean", "detail": "check_schematic; fix errors and floating pins before anything else."},
        {"step": "Make it legible", "detail": "check_schematic_layout -> tidy_schematic -> route_schematic; act on findings."},
        {"step": "Look at it", "detail": "render_schematic: fix rows out of sequence, colliding text, marooned parts."},
        {"step": "Hand over to layout", "detail": "start_board_layout only when ERC 0 and legible 0."},
    ],
    "board": [
        {"step": "Set the outline", "detail": "set_board_outline to the mechanical size before placing parts."},
        {"step": "Place by function", "detail": "move/rotate/flip; keep a stage in a row and its parts near the nets they serve."},
        {"step": "Route", "detail": "autoroute for the bulk, add_track/via for short or critical nets."},
        {"step": "Pour and refill", "detail": "add_copper_zone then refill_zones after the last move."},
        {"step": "Check the rules", "detail": "check: DRC, unconnected, size, edge and every goal criterion."},
        {"step": "Look at it", "detail": "check/render_view returns the image; fix what DRC cannot see."},
    ],
}


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
    return {
        "model": model,
        "levels": LEVELS,
        "tools": describe_tools(descriptions),
        "workflows": WORKFLOWS,
        "guidance": GUIDANCE,
    }
