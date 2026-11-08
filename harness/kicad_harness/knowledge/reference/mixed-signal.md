---
scope: both
---

# PCB Mixed-Signal Design Rules

## Contents

- [Ground Architecture](#ground-architecture)
- [Star Grounding (Low-Frequency, Single-Supply Designs Only)](#star-grounding-low-frequency-single-supply-designs-only)
- [Guard Rings and Guard Traces](#guard-rings-and-guard-traces)
- [ADC/DAC Layout Rules](#adcdac-layout-rules)
- [Power Supply Filtering Between Domains](#power-supply-filtering-between-domains)
- [Component Placement Strategy](#component-placement-strategy)
- [Clock Isolation from Analog Circuits](#clock-isolation-from-analog-circuits)
- [Return Current Management Across Domain Boundaries](#return-current-management-across-domain-boundaries)
- [Noise Coupling Mechanisms](#noise-coupling-mechanisms)
- [Trace Routing Guidelines](#trace-routing-guidelines)
- [Layout Verification Checklist](#layout-verification-checklist)

## Ground Architecture

### Unified Ground Plane Strategy (Recommended Modern Approach)
- Use a single, **unbroken ground reference plane** for all circuits (analog and digital)
- Do NOT split ground planes in modern PCBs—splitting is unnecessary and bad practice
- Single plane works for all frequency ranges when combined with proper routing and layout
- Splitting was an older workaround that creates more problems than it solves in modern designs

### Return Path Integrity
- Maintain clear, unobstructed return paths on the reference plane beneath all traces
- High-frequency signals follow **path of least impedance**, not shortest path
- Return currents from digital traces over analog ground plane prevent capacitive noise coupling
- Ensure both forward and return paths are tightly coupled to minimize loop area
- Bridge domain boundaries (if needed) with traces passing directly over the unified ground

## Star Grounding (Low-Frequency, Single-Supply Designs Only)

### When to Use
- Single-supply analog circuits under 100 kHz
- Low-current, low-noise audio or precision measurement systems
- NOT recommended for modern mixed-signal ICs or high-frequency designs

### Implementation
- Connect multiple ground return paths back to **single point at power supply**
- Separate ground regions connected via bridge near the power supply
- Analog ground traces minimum **1 mm (39 mils) wide** to minimize impedance
- Requires careful trace routing to minimize impedance differences

### Typical Setups
- Precision analog: one return path → one point → power supply
- Multi-supply systems: one star point per supply rail
- Modern approach: use unified plane with proper routing instead

## Guard Rings and Guard Traces

### Guard Ring Placement
- Place **continuous guard ring of copper** around sensitive analog circuits
- Connect guard ring to **analog ground every 0.5–1.0 mm** (0.02–0.04 inches) via vias
- Ring diameter = minimum **2 mm (79 mils) clearance** from analog circuit boundary
- Use outer perimeter ring to define analog region boundary

### Guard Trace Usage
- Route **grounded guard traces** between analog and digital signal traces (parallel routing)
- Guard trace width = **same or wider than signal trace** (min 0.15 mm / 6 mils)
- Spacing: **0.1–0.15 mm (4–6 mils)** on each side of sensitive analog traces
- Via spacing in guard trace = **0.5 mm (20 mils)** max to maintain continuity

### Sensitive Nodes Requiring Guards
- High-impedance analog inputs (ADC input lines)
- Reference voltage nets (VREF, VDAC)
- Weak-signal amplifier outputs
- Precision measurement circuits (sub-100 mV signals)

### Shielding Strategy
- Faraday cage with copper pour around crystal oscillators
- Guard rings around clock distribution circuits
- Separate via fences (rings of vias, spaced 0.5 mm) for additional isolation

## ADC/DAC Layout Rules

### Reference Voltage Routing
- Route **VREF+ and VREF-** as tightly coupled pair (together, not separately)
- VREF traces = **minimum 0.25 mm (10 mils) wide** to reduce impedance
- Keep VREF traces **away from digital switching signals**
- Reference should come from **dedicated low-noise voltage source**, not digital supply

### Decoupling Strategy
- Place **100 nF (0.1 µF) ceramic** capacitor directly adjacent to each ADC/DAC power pin
  - Distance: **< 2 mm (79 mils)** from pin to capacitor pad
  - Via from capacitor to ground: **< 1 mm (39 mils)** distance, 2 vias minimum
- Add **10 µF ceramic or 1 µF film** capacitor on supply rail for secondary filtering
  - Within **10 mm (394 mils)** of power pin
- Add **1 µF capacitor specifically on VREF pin** (in addition to power pin capacitors)
- For data-dependent DAC reference current: capacitor must be < 2 mm from DAC

### Analog Input Protection
- Use **Schottky diodes (BAT54 series or equivalent)** for input clamping
- Diodes clamp to ADC supply rail with **forward voltage drop < 0.3 V**
- Series protection resistor: **100–1000 Ω** after Schottky diodes
  - Limits current into ADC to safe level (< 10 mA typical)
  - Use **1 kΩ for high-impedance sources** (> 10 kΩ source impedance)
  - Use **100 Ω for low-impedance sources** (< 1 kΩ source impedance)
- Layout: Schottky diodes immediately before ADC input, series resistor before diodes

### Channel-to-Channel Matching
- **Symmetrical layout** around ADC/DAC devices is critical
- Position decoupling capacitors **symmetrically relative to all channels**
- Trace lengths for corresponding channels should be **equal (±5% tolerance)**
- Ground connections for each channel should be **equidistant from main ground**

## Power Supply Filtering Between Domains

### Ferrite Bead Filtering
- Use ferrite bead in **series with power supply rail** between analog and digital domains
- Typical impedance: **100–600 Ω** at intended frequency range (1–100 MHz)
- DC current rating must **exceed worst-case supply current by 20%** to avoid saturation
- Select bead with **minimal DC resistance** (< 0.1 Ω) to avoid voltage drop

### LC Filter Configuration
- Ferrite bead + capacitor forms **low-pass filter** for power rails
- Typical network: Bead in series, **100 nF capacitor to ground on each side**
- LC resonant frequency: **0.1–10 MHz range** for switching supplies (300 kHz–5 MHz)
- For high-Q resonance peaks, add **damping resistor (1–10 Ω)** in parallel with capacitor

### Multi-Rail Filtering Strategy
- **Analog power (AVDD/AGND)**: Dedicated ferrite bead + LC filter, no sharing
- **Digital power (VDD/GND)**: Separate ferrite bead + LC filter
- **Reference supply (VREF)**: Ultra-low-noise regulator with ferrite bead + additional LC stage
- Island ferrite beads at IC power pins for extra local filtering

### Decoupling Sequence (Distance from Pin)
1. **< 2 mm**: 100 nF ceramic (high-frequency response)
2. **2–5 mm**: 10 µF ceramic (mid-frequency response)
3. **5–20 mm**: 100 µF bulk capacitor (low-frequency supply ripple)
4. **20+ mm**: Ferrite bead + LC filter (domain isolation)

## Component Placement Strategy

### Functional Grouping
- Group components by **functional circuit block**, not by signal type
- Place analog, ADC/DAC, and digital circuits in **separate physical regions**
- Minimum spacing between regions: **5–10 mm (200–400 mils)**
- Related components that communicate should be **close to each other** (< 25 mm)

### Power Distribution Placement
- Power supply components (inductors, bulk caps) as **close as possible to entry point**
- Voltage regulators positioned **nearest to their load circuits**
- Decoupling capacitors **directly adjacent to IC power pins** (< 2 mm)
- Ground vias for capacitors placed **immediately beside capacitor pads**

### Clock Distribution Isolation
- **Crystal oscillators isolated in own region** with guard ring
- Clock distribution traces **shielded with guard traces or guard ring** (0.5–1 mm clearance)
- Clock lines should NOT cross above analog circuit areas
- Clock return current path should be **separate from analog return paths** if possible

### Sensitive Analog Placement
- High-impedance nodes positioned **farthest from digital switching circuits**
- Analog inputs grouped together near **single analog input connector or rail**
- Keep noise-sensitive amplifiers > **20 mm (787 mils) away from digital logic**
- Precision measurement circuits in **corner or edge of board** away from digital activity

## Clock Isolation from Analog Circuits

### Routing Rules
- Clock traces routed in **separate layer from analog signal traces** when possible
- Clock distribution **never shared layer** with ADC/DAC input/output traces
- Clock traces placed **minimum 5 mm (197 mils) away** from analog signal traces
- If same layer unavoidable: use **guard traces with 0.1 mm spacing** on both sides

### Ground Reference for Clocks
- Digital clock returns through **digital ground region**, not across analog area
- Crystal oscillator ground return → dedicated digital ground island → main plane
- Separate return path prevents clock return current from crossing analog ground

### Frequency Considerations
- Below 10 MHz: guard traces often sufficient
- 10–50 MHz: guard ring + separate layer + spaced routing recommended
- Above 50 MHz: dedicated clock layer + Faraday cage (guard ring) required

## Return Current Management Across Domain Boundaries

### Crossing Points
- Limit number of **domain boundary crossing points to 1–2 locations** per circuit block
- Route all domain-crossing signals through **concentrated boundary zone** (< 5 mm wide)
- Each crossing point should have **dedicated return vias to main ground plane** (2–4 vias)
- Via spacing at boundary: **1–2 mm (39–79 mils)** apart

### Return Path Topology
- Digital return currents should **form closed loops within digital region**
- Analog return currents should **form closed loops within analog region**
- Minimize return current **forced to cross domain boundary**
- Use **star point or ferrite bead** to single point crossing if unavoidable

### Via Stitching Across Boundaries
- Place return vias in tight grid (**0.5 mm / 20 mils spacing**) near boundary
- Via grid width: **2–3 mm (79–118 mils)** across entire boundary region
- Via arrangement: offset pattern (staggered rows) to cover ground plane uniformly
- Multiple via configurations prevent EMI radiation from crossing currents

## Noise Coupling Mechanisms

### Capacitive Coupling Prevention
- **Displacement current** from digital traces to chassis creates common-mode noise
- Solution: **ground plane beneath all traces** to provide return path
- Digital traces over analog ground plane → prevents capacitive coupling to analog circuits
- Analog traces over analog ground plane → maintains analog return path integrity

### Inductive Coupling Prevention
- Loop area formed by signal trace + return path acts as **magnetic antenna**
- Minimize loop area by keeping **signal and return paths tightly coupled**
- Via to ground should be **adjacent (within 1 mm) to signal trace**
- Avoid long return paths that create large loop areas

### Cross-Talk Coupling
- Guard traces between parallel signal traces reduce **capacitive coupling** by 50–75%
- Trace spacing for sensitive analog signals: **minimum 0.2 mm (8 mils)**
- Digital-to-analog cross-talk worse than analog-to-digital (due to slew rate differences)
- Solution: **separate routing layers** or **guard traces on both sides**

### Power Supply Noise Coupling
- Switching current in digital supply creates voltage ripple: **ΔV = ΔI × Z_supply**
- Ripple couples to analog circuits through shared impedance path
- Solution: **ferrite bead + LC filter** breaks common impedance
- Alternative: **separate power regulators** for each domain (preferred for high performance)

## Trace Routing Guidelines

### Signal Trace Widths
- **Analog signals**: 0.15–0.25 mm (6–10 mils) with guard traces
- **Digital signals**: 0.1–0.15 mm (4–6 mils), no special requirement
- **Power traces**: 0.4–0.8 mm (16–31 mils) depending on current
- **Ground return traces**: 0.2–0.4 mm (8–16 mils) minimum

### Via Sizing
- Pad diameter: **0.3–0.4 mm (12–16 mils)** typical
- Hole diameter: **0.15–0.2 mm (6–8 mils)**
- Spacing between vias: **1–2 mm (39–79 mils)** in tightly coupled pairs
- Via stitching (grid): **0.5 mm (20 mils)** spacing for impedance control

### Trace Routing Layers
- **Layer 1 (top)**: Analog signal traces, component placement
- **Layer 2**: Ground plane (continuous, unbroken)
- **Layer 3**: Digital signal traces, power distribution
- **Layer 4 (bottom)**: Ground plane or power distribution
- **Multi-layer boards**: Insert additional ground planes between analog and digital layers

## Layout Verification Checklist

### Pre-Fabrication Review
- [ ] Single, unbroken ground plane confirmed (no splits visible)
- [ ] All IC power pins have 100 nF capacitor within 2 mm
- [ ] VREF traces routed as tightly coupled pair, width ≥ 0.25 mm
- [ ] Ferrite bead + LC filter present on power rail crossing domain boundary
- [ ] Guard rings/traces present around high-impedance analog nodes
- [ ] Clock distribution separated from analog signal traces (5 mm spacing minimum)
- [ ] Return current vias (2–4) placed at each domain boundary crossing
- [ ] Trace lengths for ADC channels matched (±5%)
- [ ] No digital traces routed over analog circuit region
- [ ] Crystal oscillator surrounded by guard ring with 0.5 mm via spacing
- [ ] Power supply current capacity margins verified (worst-case + 20%)
- [ ] Ferrite bead DC current rating exceeds supply current
- [ ] Decoupling capacitor ESR/ESL acceptable for IC frequency range

---

**Sources:**
- [Analog Devices: Basic Guidelines for Mixed-Signal PCB Layout Design](https://www.analog.com/en/resources/analog-dialogue/articles/what-are-the-basic-guidelines-for-layout-design-of-mixed-signal-pcbs.html)
- [NWES: Should You Split Ground Planes in Mixed-Signal PCB Design?](https://www.nwengineeringllc.com/article/should-you-split-ground-planes-in-mixed-signal-pcb-design.php)
- [Altium: Star Grounding for Analog and Digital Ground Separation](https://resources.altium.com/p/how-to-use-a-star-point-for-analog-ground-digital-ground-connection)
- [Cadence: Tips for Separating Analog and Digital Ground Planes](https://resources.pcb.cadence.com/blog/2021-should-you-ever-separate-analog-and-digital-ground-planes)
- [Analog Devices: Successful PCB Grounding with Mixed-Signal Chips](https://www.analog.com/en/resources/technical-articles/successful-pcb-grounding-with-mixedsignal-chips--follow-the-path-of-least-impedance.html)
- [Cadence: Mixed-Signal PCB Design Guidelines](https://resources.pcb.cadence.com/blog/2022-mixed-signal-pcb-design-guidelines-for-circuit-board-layout)
- [Sierra Circuits: How to Design a Mixed-Signal PCB](https://www.protoexpress.com/blog/how-to-design-mixed-signal-pcb-with-signal-integrity/)
- [AMD: ADC and DAC PCB Guidelines](https://docs.amd.com/r/en-US/ug583-ultrascale-pcb-design/ADC-and-DAC-PCB-Guidelines)
- [Analog Devices: Protecting ADC Inputs](https://www.analog.com/en/resources/technical-articles/protecting-adc-inputs.html)
- [Analog Devices: Ferrite Beads Demystified](https://www.analog.com/en/resources/analog-dialogue/articles/ferrite-beads-demystified.html)
- [All About Circuits: Choosing and Using Ferrite Beads](https://www.allaboutcircuits.com/technical-articles/choosing-and-using-ferrite-beads/)
- [Arrow.com: Principles of Grounding for Mixed-Signal Designs](https://www.arrow.com/en/research-and-events/articles/principles-of-grounding-for-mixed-signal-designs)
- [PCBMaster: Star Grounding in PCB Design](https://www.pcbmaster.com/news/star-grounding.html)
