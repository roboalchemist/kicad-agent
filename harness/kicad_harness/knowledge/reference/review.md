# PCB Design Review Process

## Overview

Run a comprehensive design review by checking the board against every rule file in `.agents/rules/pcb-*.md` using parallel sub-agents. Each agent reviews one domain and reports PASS, VIOLATIONS, and SUGGESTIONS.

## Review Domains

| Domain | Rule File | Key Checks |
|--------|-----------|------------|
| Placement | `pcb-placement.md` | IC priority, decoupling distance, grouping, orientation, signal flow |
| Routing | `pcb-routing.md` | 45-degree bends, 3W spacing, return vias, via count, return paths |
| Power | `pcb-power.md` | Trace widths, 3-stage decoupling, current handling, VRM distance |
| DFM | `pcb-dfm.md` | Trace/space minimums, copper balance, fiducials, tombstoning, edge clearance |
| EMC | `pcb-emc.md` | Return path continuity, loop area, via stitching, board edge treatment |
| Thermal | `pcb-thermal.md` | Power dissipation calc, derating, thermal vias, copper spreading |
| Silkscreen | `pcb-silkscreen.md` | Text sizing, pad clearance, text-to-text overlap, polarity marks |
| Copper Pours | `pcb-copper-pours.md` | When to pour, stitching vias, dead copper, thermal relief |
| Test/Debug | `pcb-test-debug.md` | Test points, probe access, power indicators, debug headers |
| Mechanical | `pcb-mechanical.md` | Mounting holes, corner radii, board dimensions, keepouts |
| BOM | `pcb-bom-components.md` | Derating, lifecycle, alternates, MSL, ESD |
| Stackup | `pcb-stackup.md` | Layer count decision, impedance, material selection |

## How to Run

Use `/pcb-review` or invoke manually with the divide-and-conquer pattern:

1. Identify board files (render data JSON, layout script, PCB file)
2. Create one task per domain
3. Launch all agents in parallel (`run_in_background: true`)
4. Each agent reads its rule file + the board data, reports findings
5. Consolidate and deduplicate into a prioritized table

## Agent Prompt Template

Each sub-agent receives:

```
You are reviewing a PCB layout for compliance with [DOMAIN] rules.
DO NOT write any code — research only.

Read these files:
1. .agents/rules/pcb-[rule-file] — the design rules
2. [layout script path] — component positions and routing code
3. [render data JSON path] — board data (components, tracks, vias, outline)

Context: [brief circuit description — frequency, layer count, component count]

Check ALL applicable rules. For inapplicable rules (e.g., RF on a DC circuit),
note as N/A with reason.

Report:
- PASS: rules satisfied (brief note)
- VIOLATIONS: rules broken (coordinates, values, what rule requires)
- IMPROVEMENT SUGGESTIONS: could be better (essential / moderate / nice-to-have)
```

## Output Format

Consolidate all agent results into a single prioritized table:

```markdown
### Critical (should fix)
| # | Issue | Source | Fix |
|---|-------|--------|-----|

### Moderate (should address)
| # | Issue | Source | Fix |
|---|-------|--------|-----|

### Minor / Nice-to-have
| # | Issue | Source |
|---|-------|--------|
```

Deduplicate findings that appear in multiple reviews (e.g., "missing bypass cap" from both Power and EMC). Credit all sources.
