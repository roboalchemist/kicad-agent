# PCB Design Process — Order of Operations

## Contents

- [The Sequence](#the-sequence)
- [Phase Details](#phase-details)
- [Strict Dependencies](#strict-dependencies)
- [What Can Be Parallelized](#what-can-be-parallelized)
- [Critical Sequencing Mistakes](#critical-sequencing-mistakes)

## The Sequence

```
1. Requirements & Concept
2. Schematic Capture + Component Selection
3. Pre-Layout Preparation (stackup, design rules, board outline)
4. Component Placement
5. Routing
6. Design Verification (DRC, ERC, signal integrity)
7. Manufacturing File Generation
8. Review & Handoff
```

## Phase Details

### 1. Requirements & Concept
- Define performance targets, power budgets, thermal constraints, form factor
- Identify compliance requirements (FCC/CE, safety standards)
- Establish manufacturing constraints and budget
- Coordinate mechanical integration with MCAD team
- **Output**: requirements spec, preliminary board dimensions, constraints list

### 2. Schematic Capture + Component Selection
- Draw schematics, select components, assign reference designators
- Verify footprints exist and are correct for every part
- Generate netlist (drives all downstream layout work)
- Validate BOM for availability, obsolescence, lead times
- Assign net classes (power, ground, high-speed, etc.)
- **Checkpoint**: schematic review sign-off
- **Output**: schematic, netlist, validated BOM, component library

### 3. Pre-Layout Preparation
**MUST happen before placement or routing begins.**

- **Define stackup** — layer count, materials, dielectric thicknesses, impedance targets. Get fabricator approval.
- **Set design rules** — trace widths, spacing, via sizes, impedance targets, net classes. Rules must be in the tool before placement so violations are caught in real-time.
- **Define board outline** — dimensions, mounting holes, connector locations from mechanical constraints
- **Checkpoint**: stackup approved by fab house, design rules locked
- **Output**: stackup spec, design rule file, board outline

### 4. Component Placement
1. Fixed components first (connectors, mounting holes, test points)
2. Critical ICs next (processors, FPGAs, major analog)
3. Decoupling caps within 3-5mm of their IC power pins
4. Timing components (crystals, RC networks) adjacent to their IC
5. Remaining passives/discretes, optimizing for signal flow and routing
- **Checkpoint**: placement review (thermal, DFM, routing feasibility)
- **Output**: placed layout, DFM notes, thermal analysis

### 5. Routing
1. Power planes and ground planes first
2. Critical/high-speed signals next (clocks, differential pairs, impedance-controlled)
3. General signals
4. Copper pours, stitching vias, thermal vias
- Placement and routing iterate — routing problems may force placement adjustments
- **Checkpoint**: all nets routed, interim DRC clean
- **Output**: routed layout, signal integrity notes, via specs

### 6. Design Verification
- Full DRC — **zero violations required**
- Net list comparison against schematic (ERC)
- Impedance verification for controlled traces
- Mechanical clearance check (enclosure fit)
- Silkscreen legibility and placement review
- DFM review with manufacturer
- **Checkpoint**: all checks pass, sign-off from stakeholders
- **Output**: DRC report, verification checklists, assembly drawing

### 7. Manufacturing File Generation
- Gerber files (one per layer), NC drill files
- Pick-and-place file, solder paste stencil data
- Final BOM with part numbers and suppliers
- Fabrication notes (stackup, materials, special processes)
- Assembly drawings
- **Output**: complete manufacturing file package

### 8. Review & Handoff
- Final review with design, manufacturing, test, and mechanical teams
- Transmit file package to fabricator/assembler
- Establish quality acceptance criteria and test plan

## Strict Dependencies

| Step | Requires |
|------|----------|
| Netlist generation | Schematic complete |
| Layout starts | Netlist + BOM validated |
| Stackup design | Signal types, power requirements, BGA pin counts known |
| Placement & routing | Stackup locked, design rules configured |
| Detailed routing | Placement substantially complete |
| Manufacturing files | DRC passing with zero violations |

## What Can Be Parallelized

- Footprint verification during schematic capture
- Vendor capability assessment during schematic design
- Impedance/PDN analysis planning during stackup documentation
- Multiple engineers on different board regions once stackup + rules are locked
- Real-time DRC during placement/routing

## Critical Sequencing Mistakes

1. **Routing before placement is finalized** — excessive crossovers, congestion, poor signal integrity, multiple rework iterations
2. **Not locking stackup before routing** — impedance mismatches, crosstalk issues, expensive layer reassignment rework
3. **Deferring DFM to the end** — insufficient annular rings, copper too close to edges, starved thermal pads, floating copper slivers. Run DFM checks iteratively throughout design.
4. **Starting layout before schematic sync** — outdated footprints or wrong parts force late-stage redesign
5. **Skipping design reviews** — issues found after fabrication cost 10-100x more to fix
6. **Not involving the fabricator early** — their stackup constraints and capabilities affect design rules, layer count, and impedance targets
