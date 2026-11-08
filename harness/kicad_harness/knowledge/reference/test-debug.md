# PCB Test & Debug Design

## Test Points
- **Standard size**: 1.0mm diameter (0.9mm acceptable, 0.8mm minimum)
- **Spacing**: 2.5mm center-to-center ideal, 1.8mm minimum for ICT
- **Board edge clearance**: 3.2mm minimum (3.8mm preferred for vacuum fixtures)
- **High-speed isolation**: 1mm minimum from traces ≥50MHz
- **Max density**: 12 test points per square inch (bed-of-nails)
- Distribute evenly across board — uneven density causes board flex and solder damage
- Pad surface: bare copper or gold-plated, free from solder mask

## Bed-of-Nails Fixtures
- Pad diameter: 0.015-0.040" (0.035" nominal)
- Via pads must be untented if probed
- Component clearance: 1mm minimum vertical
- Vacuum fixtures preferred over press-down (better signal, lower board stress)
- 3mm clear border along two opposite sides for machine holding

## Flying Probe Testing
- Minimum pad size: 6mil (20mil recommended)
- Via diameter: 8-20mil (10mil recommended), must be untented
- Component height limit: 40-90mm
- Maximize probeable points on single side to reduce flip cost

## Debug Headers (JTAG/SWD)
- Place close to MCU (proximity more critical than board edge)
- **ARM 20-pin**: 2×10, 0.1" pitch (standard)
- **ARM 10-pin**: 2×5, 0.05" pitch (compact, Cortex preferred)
- SWCLK/SWDIO: keep short, matched length, multiple ground pins
- **Pull-up/pull-down**: 10kΩ standard (1-47kΩ acceptable) — defines signal when debugger disconnected
- Always mark polarity on silkscreen, use keyed/shrouded headers for production

## JTAG Boundary Scan Chain
- Devices in serial daisy chain: TDO→TDI between devices
- TCK, TMS, nTRST connected in parallel to all devices
- Clock: 10-30MHz typical
- **nTRST: NEVER connect to ground** (disables entire JTAG chain)
- Route TAP signals away from other active signals

## In-Circuit Test (ICT) Pads
- Same sizing as test points (1.0mm preferred, 1.8-2.5mm spacing)
- **Tooling holes**: 2 minimum, 3.2mm diameter, unplated, opposite corners, 3.2mm keepout around each
- **Power test points**: 2-10 per primary power rail, 2 minimum per isolated net
- **Ground test points**: 1 per 20 ground connections (or 1 per square inch grid)
- Probe from bottom preferred (most ICT fixtures probe from below)

## Power & Status Indicators
- Power-on LED on main power traces (not at switch — confirms power at PCB)
- Heartbeat LED driven by MCU output pin
- Color convention: red=power, green=status, yellow=caution, red=fault
- Always mark LED polarity on silkscreen

## Current Measurement Points
- **Kelvin (4-wire) sensing**: separate current pads from sense pads
- Place shunt close to ground for passive probe use
- Low current (≤10A): SMT packages (2512, 1206)
- High current (≥20A): through-hole (TO-220, bolt-type)

## Test Coverage
- Target 95%+ node coverage
- Power nets: multiple test points per rail
- Ground nets: grid of test points (1 per square inch minimum)
- Document: test point list with netlist mapping, test plan, ICT coordinates, JTAG chain config
- Involve manufacturing engineer in test point placement early
