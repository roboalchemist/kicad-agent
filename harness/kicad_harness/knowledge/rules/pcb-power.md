# PCB Power Distribution

## Planes
- Use dedicated internal power and ground planes — far lower impedance than trace routing
- Minimize plane spacing (4mil ideal) for inter-plane capacitance
- Connect all ground planes with multiple vias (at least one per 0.5 inch)
- Don't split planes unless required for mixed-signal isolation

## Decoupling Strategy (3-stage)
1. **Bulk** (100uF) — near main power entry point
2. **Intermediate** (10uF) — one per functional region
3. **High-frequency** (0.1uF) — one per 1-2 IC power pins, within 5mm of pin
- Use 2-4 vias per capacitor pad to reduce via inductance
- Match capacitor resonance frequency to IC switching frequency

## Current Handling
- Size power traces for ~1A per mil of width (external layer, 1oz copper)
- Use wider traces for power than signals — 25-50+ mil common
- Avoid bottlenecks — power traces should never pinch down
- Duplicate power feeds on multiple layers for high-current loads

## Mixed-Signal Grounding
- Star ground topology — analog and digital grounds meet at single point
- Route analog ground returns separately from digital
- 10-100 ohm resistor between analog and digital ground at star point

## PDN Impedance Profiling
- **Target impedance**: Ztarget = Vripple / Imax (e.g., 3.3V, 5% ripple, 10A → 5mΩ)
- Apply 0.5× safety margin for manufacturing tolerance and aging
- Frequency bands: DC-1MHz bulk caps (10-20mΩ), 1-100MHz ceramics (1-5mΩ), 100MHz-1GHz planes (<1mΩ)
- Profile Z(f) with VNA or SPICE — must stay below target curve across entire range
- Impedance peaks = resonances requiring mitigation (add caps or reduce loop inductance)

## Anti-Resonance Between Capacitors
- Multiple caps create resonance peaks where Z exceeds target
- **Cascading values**: 100µF → 10µF → 1µF → 100nF → 10nF → 1nF, each ~1 octave apart
- Choose ESR >5mΩ to dampen resonances; stagger ESR 5-30mΩ across stack
- High Q (low ESR) = sharp tall peaks (bad); low Q = broad shallow peaks (acceptable)
- Minimum 2 vias per cap pad; trace from cap to IC <10mm

## Voltage Regulator Layout
- VRM output pad to IC power pin: **<5mm routing distance** for high-current supplies
- Keep VRM ≥20mm from high-heat devices; provide 2×2 thermal via array under VRM
- **Output decoupling**: 10µF total within 5mm of output pad (1-2× 10µF ceramic + 4-6× 1µF)
- **Input decoupling**: 100µF bulk + 10µF ceramic at VRM input
- Parallel VRMs: equal trace lengths within 5mm; current-sense resistors (0.5-1mΩ) for monitoring

## Power Sequencing
- Sequential startup order per IC datasheet: typically core → I/O → analog
- Minimum 5-10ms delay between rail enables; verify in datasheet
- Shutdown in reverse order; allow 10-50ms ramp per rail
- Prevent reverse voltage: ensure lower rail doesn't exceed higher by >0.3V during transient
- Size input caps for inrush: Cinput = Iinrush × tsoft-start / ΔV

## Inrush Current Protection
- Soft-start limits ramp to <10A/ms; typical duration 2-5ms
- Calculation: tsoft-start = Cout × ΔVramp / Imax
- Input-side limiting: series resistor (10-100mΩ) with bypass relay after power stable
- Voltage dip during inrush: Vmin = Vnominal − (ZPDN × Iinrush) — must exceed IC minimum

## Trace Width (IPC-2152, 1oz copper, 10°C rise)
| Width | External | Internal |
|-------|----------|----------|
| 10mil | ~6A | ~4A |
| 20mil | ~10A | ~6A |
| 50mil | ~20A | ~12A |
| 100mil | ~35A | ~21A |
- Derate: size for 1.5× continuous current; use 5°C rise for high-reliability (1.25× wider)
- Place return vias every 0.5" along long power traces
- Wide return path parallel to power trace, <0.5mm separation

## Copper Weight
- 0.5oz = 17µm, 1oz = 35µm (standard), 2oz = 70µm, 4oz = 140µm
- 2oz adds ~15-25% cost; 4oz adds 40-50%
- Power/ground planes: 2oz minimum for high-current boards
- Current capacity (10mil, 10°C rise): 0.5oz=1A, 1oz=6A, 2oz=12A, 4oz=24A

## Power Integrity Simulation
- Model: VRM (Z(s) output impedance) + caps (R-L-C series) + planes (distributed R-L) + load (current source)
- Run AC sweep 10Hz-10GHz → Z(f) curve; overlay against Ztarget
- Transient analysis: step current, verify overshoot ≤10% Vnominal, undershoot ≥Vmin rated
- Monte Carlo: ±10% capacitance, ±20% ESR to verify worst case
- Tools: LTspice (free), Keysight ADS, ANSYS SIwave, vendor tools (TI WEBENCH, ADI SwitcherCAD)
