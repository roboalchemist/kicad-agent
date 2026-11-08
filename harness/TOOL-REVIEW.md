# Tool review — against the full PCB knowledge base

Scope: the 50 agent tools exposed at `GET /api/settings` (8 global, 4 BOM, 18 schematic,
20 board) and the 53 bundled rule/reference documents (229 sections) in
`kicad_harness/knowledge/`. The question is not "does each tool work" but "is the toolset the
right shape for the work the rules describe".

## Verdict at a glance

| Category | Count (approx.) | Examples |
|---|---|---|
| Sufficient | 24 | find_parts / check_part / read_part_datasheet; set_goal / check; tidy_schematic / route_schematic / check_schematic_layout; render_view / render_schematic; review trio; versions |
| Overlapping (merge) | 14 | part_categories→find_parts; delete_items→delete_tracks; check_schematic + check_schematic_layout + check; label_pin + add_schematic_label; render_view + render_schematic |
| Overkill / risky | 5 | set_pad_net, delete_items, move_reference_text, draw_schematic_wires, add_schematic_text |
| Missing capabilities | 12 areas | net classes, stackup, differential pairs, mechanical outline import, manufacturing export, BOM alternates/lifecycle, silkscreen automation, test points, thermal, keepouts, via types, knowledge search |

The rules describe a professional workflow (stackup → placement → routing → verification →
manufacturing). The toolset implements the middle third of it well; the front (rules/stackup) and
back (manufacturing/test) are largely absent.

## 1. Duplicates and overlaps

| Cluster | Tools | Overlap | Recommendation |
|---|---|---|---|
| Sourcing lookup | `find_parts`, `part_categories`, `check_part` | `find_parts` already takes `category`; `check_part` reuses the same catalogue row plus datasheet status | Merge `part_categories` into `find_parts`; make `check_part` a `detail=true` result of `find_parts` |
| Deleting board items | `delete_tracks`, `delete_items`, `remove_footprint` | three ways to remove copper; UUIDs are hard for a model to obtain and reuse | One `delete(kind=track|via|zone|footprint, nets|refs|uuids)`; keep `remove_footprint` for schematic parity only |
| Attaching a net | `connect_pins`, `label_pin`, `add_schematic_label` | three verbs for pin↔pin, pin↔net, net-at-point | Keep `connect_pins`; merge the other two into `label_net(target, net)` where target is a pin or a point |
| Checking | `check`, `check_schematic`, `check_schematic_layout` | `check_schematic` only evaluates goal criteria while `check_schematic_layout` returns the real report + image; naming promises the same thing | One phase-aware `check`: ERC/DRC counts, criteria, findings, image; `detail` level for the full report |
| Summaries | `get_design`, `schematic_summary`, `query` | `get_design` delegates to `schematic_summary` in the schematic phase | Keep; document the delegation and make return shapes consistent |
| Rendering | `render_view`, `render_schematic` | same "look at it" action for two objects | One `render(kind=board|schematic, …)` |
| Wiring the sheet | `draw_schematic_wires`, `tidy_schematic`, `route_schematic` | `draw_schematic_wires` is a legacy stub fixer; tidy already draws stubs and route draws real wires | Fold into `tidy_schematic` |
| Schematic repair | `disconnect_schematic`, `remove_symbol`, `set_symbol_value` | all can remove/rename connectivity | Keep `remove_symbol`; fold net clearing into a selector on `disconnect_schematic` |
| Versioning | `list_versions`, `restore_version` | fine, but thin | Add `diff_versions(a,b,scope=nets|entities)` and tags |

## 2. Overkill

- **`set_pad_net`** — lets the board contradict the schematic. The parity/sync criteria exist
  precisely to prevent that. Remove it or restrict to boards with no schematic.
- **`delete_items`** — a UUID-level escape hatch; models rarely have UUIDs, and `query` is the
  only way to get them. Fold into the deletion tool.
- **`move_reference_text`** — a manual micro-fix; the rule is "silkscreen must be legible"
  (`pcb-silkscreen`), which needs a collision-aware `place_silkscreen` pass, not one label at a
  time.
- **`draw_schematic_wires`** — superseded by tidy/route; keep only if imported skidl sheets still
  need it.
- **`add_schematic_text`** — annotations are nice but low value next to the missing capabilities.

## 3. Sufficient

- **Sourcing**: `find_parts` (stock/price/tier/category), `check_part` (stock + datasheet),
  `read_part_datasheet` — grounded in the catalogue, not guesses.
- **Goal loop**: `set_goal` + the mandatory criteria (ERC, legible, DRC, routing, parity, sync,
  brief) — this is the strongest part of the harness; it cannot be talked out of.
- **Schematic quality**: `tidy_schematic` (owner placement + pin alignment), `route_schematic`
  (validated wires), `check_schematic_layout` (score/findings/image) — turned the test case from
  562 violations to 0 with 34/34 routed nets.
- **Board editing for simple boards**: move/rotate/flip, `add_track`/`add_via`, zones + refill,
  `autoroute`, `measure`, `query` — enough for the 2-layer relay board.
- **Review loop**: `list_reviews`, `reply_review`, `mark_review_addressed`; versions list/restore.
- **Knowledge**: `read_reference` + the cited digest.

## 4. Insufficient (rules demand it; no tool)

### High impact

| Gap | Rule evidence | Needed tool |
|---|---|---|
| Net classes / design rules | `pcb-routing#Spacing & Crosstalk`, `pcb-power#Trace Width`, `pcb-high-speed#Controlled Impedance` | `set_net_class(name, width, clearance, target_impedance)` |
| Stackup | `pcb-stackup#Layer Count Decision`, `#Impedance Implications` | `set_stackup(layers, material, thickness, impedance)` |
| Differential pairs / length matching | `pcb-high-speed#Differential Pairs`, `#Clock Routing` | `route_diff_pair(p, n, …)`, `length_match(group, tolerance)` |
| Mechanical outline | `pcb-mechanical#Board Outline`, `#Mounting Holes`, `#Keepout Zones` | `import_outline(dxf/step)`, `add_mounting_hole`, `add_keepout` |
| Manufacturing outputs | `pcb-design-process#Phase Details` (step 7), `pcb-dfm` | `export_manufacturing()` (Gerbers, drill, P&P, BOM, drawing) |
| BOM lifecycle / alternates | `pcb-bom-components#Alternate/Second Source Strategy`, `#Component Lifecycle Management`, `#MSL` | `bom_alternates`, `bom_export`, lifecycle fields in `find_parts` |
| Silkscreen automation | `pcb-silkscreen#Placement Rules`, `#Clearance Rules` | `place_silkscreen()` (collision-aware text) |
| Testability | `pcb-test-debug#Test Points`, `#Test Coverage` | `place_test_points(nets)`, coverage report |
| Thermal | `pcb-thermal#Thermal Vias`, `#Copper Spreading` | `add_thermal_vias(ref/pad)`, copper-balance report |

### Medium impact

- **Keepouts / zone options** (`pcb-copper-pours#Pour Priority`, `#Dead Copper`): no keepout
  zones, no island removal, no thermal-relief options.
- **Via types & stitching** (`pcb-routing#Via Usage`, `pcb-emc#Via Stitching`): only through
  vias; no blind/buried, no stitching arrays.
- **DRC configuration/waivers** (`pcb-design-process#Phase Details` step 3): rules cannot be set
  from the harness; the engine uses a fixed configuration.
- **Schematic hierarchy & annotation** (`pcb-design-process#Phase Details`): single-sheet only; no
  renumber/annotate, no variants/DNP.
- **Version management** (`pcb-version-control#Revision Numbering Schemes`): no tags, release
  notes, or netlist diff.
- **Knowledge search** (`read_reference`): lookup by id only; no query across 229 sections.

### Weak spots in existing tools

- `generate_schematic`: one agent run reported it failing on the skidl pickle cache
  ("Read-only file system"). The directory is writable now, but the failure needs a regression
  test or a replacement path — a whole-design tool that can fail silently is a liability.
- `route_schematic`: only nets with exactly two pins; buses (3+ pins) always fall back to labels.
- `check_schematic`: its name promises ERC detail but it returns only goal criteria.
- `autoroute`: ignores net classes/impedance because there are none; not constraint-aware.

## 5. Recommended target toolset (≈36 tools)

**Merge / cut (‑14)**: part_categories, check_part (as a flag), delete_items, label_pin +
add_schematic_label (→ label_net), draw_schematic_wires, check_schematic (merged into check),
render_schematic (into render), set_pad_net, move_reference_text, add_schematic_text.

**Add (high priority, +9)**: `set_net_class`, `set_stackup`, `route_diff_pair`, `length_match`,
`import_outline`, `add_keepout`, `export_manufacturing`, `bom_alternates`, `place_silkscreen`.

**Add (next, +4)**: `place_test_points`, `add_thermal_vias`, `bom_export`, `diff_versions`.

## 6. First three changes

1. **Unify the checks** (`check` phase-aware, return counts + findings + image). This removes the
   ambiguity that cost runs in this project.
2. **Fix the whole-design generator** or document it as unsupported; it is the only bulk schematic
   path and it failed once.
3. **Add net classes + stackup + manufacturing export** — the three places where the rules
   describe a workflow the harness cannot execute at all.

## Applied after this review

The five overkill tools were removed (`set_pad_net`, `delete_items`,
`move_reference_text`, `draw_schematic_wires`, `add_schematic_text`) and the duplicate clusters
were merged. The agent surface is now **36 tools**, all named `<verb>_<object>`:

- sourcing: `find_parts` absorbs `part_categories` and `check_part` (`categories=True`,
  `mpn`/`lcsc`); `read_part_datasheet` stays.
- schematic: `label_net` absorbs `label_pin` + `add_schematic_label`;
  `delete_schematic_items` absorbs `disconnect_schematic` + `remove_symbol`;
  `move_symbol`, `generate_schematic`, `start_board` replace the phase-suffixed names;
  `schematic_summary` is folded into `get_design`.
- checks: `check(detail=False)` is phase-aware and replaces `check` + `check_schematic` +
  `check_schematic_layout`; `render(kind=...)` replaces `render_view` + `render_schematic`.
- board: `delete_board_items(nets, refs, uuids)` replaces `delete_tracks` + `remove_footprint`.
- review: `list_reviews` / `reply_review` / `mark_review_addressed`.

Still open: the capability gaps in section 4 (net classes, stackup, differential pairs,
outline import, manufacturing export, BOM lifecycle, silkscreen automation, test points,
thermal), plus knowledge search.


## Implementation status (final)

| Recommendation | Status |
|---|---|
| Net classes + stackup | **done** — `set_net_class`/`set_stackup` persist to the workspace and are mirrored into `*.kicad_pro` (class clearance/width, netclass patterns, board minimum track width and clearance), so KiCad's own DRC uses the same numbers. Per-class width is additionally checked by `check` (KiCad has no per-class width rule). |
| Manufacturing export + gate | **done** — Gerbers, Excellon drill, pick-and-place, BOM and manifest; `check` reports presence/staleness; `manufacturing` goal criterion. |
| Generator regression | **done** — the bundled golden flow draws a four-part design with `generate_schematic` from scratch. |
| Connection retry | **done** — `stream_with_token_retry` retries provider connection errors with backoff. |
| High-speed | **done** — `route_diff_pair`, `match_length`, via types (through/micro/blind/buried) and `add_via_stitching`. No serpentine generator. |
| Mechanical outline | **done for wireframes** — polygon outline, `import_outline` for DXF (LWPOLYLINE/LINE) and STEP (EDGE_CURVE lines and sampled arcs), `add_keepout` rule areas, `add_cutout` circular holes and `add_mounting_holes` (four corner holes with copper keepouts). STEP B-spline edges are approximated by their endpoints. |
| Silkscreen automation | **done** — collision-aware `place_silkscreen`. |
| Test points | **done** — end-to-end test: one symbol + one footprint per net with the same reference, pad on the net. |
| Thermal vias / copper balance | **done** — `add_thermal_vias`, `add_via_stitching`, and a copper-balance estimate (front/back area and ratio) in `check`. |
| BOM lifecycle | **done** — alternates, MSL, ESD class, lead time and lifecycle status in `bom.csv`. |
| Layout metrics | **done** — owner distance, alignment, wire length, crossings, utilisation; `layout_metric` goal criterion. |
| Netlist/BOM diff | **done** — `diff_versions`. |
| Uniform tool contract | **done** — a build-time wrapper gives every tool `{ok, data|error, version}`; images are list attachments after the envelope (documented exception). |
| Playbook once per run | **done** — injected once per phase. |
| Knowledge sync + search | **done** — drift test against `.agents/rules` plus `read_reference(query=...)`. |
| Golden regression suite | **done** — bundled `tests/test_golden_flow.py` runs draw → tidy → route → board → track → manufacturing on a four-part design, plus test-point parity; no live workspace needed. |
| UI tool examples / project management | **done** — examples and failure modes on the tool page; projects page rename, duplicate and confirm-gated delete. |

### Remaining documented limits

- STEP import reads wireframe edges; splines are chained by endpoints, not evaluated.
- Impedance uses the IPC-2141 microstrip closed form (15 % tolerance), not a field solver.
- Netclass *track width* is a harness check because KiCad DRC only enforces a board-wide minimum
  (which this code lowers to the weakest declared class).
- No automatic serpentine/length tuning; `match_length` reports and `route_diff_pair` routes.
- No component height/clearance check: that needs 3D models per footprint.
