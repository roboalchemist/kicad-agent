# PCB EMC/EMI Design

## Grounding Strategy
- **4+ layers minimum** for high-speed designs — dedicated ground plane(s) reduce impedance by orders of magnitude
- **Solid continuous ground planes** — no large cutouts or fragmentation
- **Return path continuity** — return current must flow directly beneath signal traces
- Separate analog/digital grounds physically, converge at single point (analog power entry)
- Multi-point grounding for shielded cables and enclosure penetrations (minimum 3-4 points)

## Via Stitching
- Stitch shield edges at λ/20 spacing (wavelength at highest frequency of concern)
- **Practical spacing**: 5-10mm for <1 GHz, 2-5mm for 1-10 GHz, <2mm above 10 GHz
- Stitch power-to-ground to reduce loop inductance in power delivery

## Decoupling for EMC
- **0.1µF within 5mm** of every IC power pin (high-speed logic)
- Frequency ladder: 10-100µF bulk (DC-100kHz), 1-10µF mid (100kHz-10MHz), 0.1µF HF (10MHz+), 0.01µF UHF (100MHz+)
- **X7R/X5R dielectrics only** — avoid Y5V/Z5U (degrade with temperature)
- Mount caps on same layer as IC power pins — avoid vias between cap and pin
- Two vias per cap pad (one power, one ground) reduces ESL by 50%

## Loop Area Minimization
- Keep signal and return path parallel and adjacent (<10mil separation)
- Place return via within 5mm of signal via
- Ground plane as return path preferred over trace returns
- Use differential clock routing to reduce EMI by ~20dB

## Shielding
- Shield oscillators, RF circuits, and sensitive ICs with Faraday cages
- Ground shield cans with 4+ vias per edge (5-10mm spacing)
- Via fence (5-10mm spacing) between analog, digital, and RF compartments
- All external cables must enter through shielded connectors with 360° termination

## Board Edge Treatment
- No exposed signal traces at board edges — route signals away from edges
- Ground guard traces along board perimeter
- 50-100mil ground border on all edges
- No high-speed clock traces within 500mil of board edge
- Via stitching array every 5-10mm around perimeter (improves shielding 10-20dB)

## Cable & Connector Filtering
- All external cables carrying signals >10MHz must be shielded (>90% coverage)
- Shield termination: 360° connection at both ends
- Ferrite clamps at cable exits (50-200Ω @ 100MHz)
- Common-mode choke on input power lines (1-10mH)
- TVS diodes after CMC for ESD protection

## Split Planes
- Keep power planes solid when possible — fragmentation increases inductance
- If splitting required: narrow traces (5-10mil) between sections, not large slots
- Every plane cut increases impedance and capacitive coupling to other layers
- Provide alternative low-impedance return path adjacent to any necessary cutout

## Signal Integrity for EMC
- Route traces in controlled impedance region (between ground planes when possible)
- Stripline (between planes) has better EMI containment than microstrip (outer layer)
- Minimize via count on high-speed signals (each adds ~5-15pF + inductance)
- Ground plane via placement every 100-200mil along signal path

## Antenna Prevention
- Any trace >λ/4 at operating frequency acts as radiating element
  - 1GHz: >3" is a potential antenna
- PCB dimensions create cavity resonances: f = c/(2 × dimension)
- Suppress board resonances with edge via stitching and internal compartments

## Compliance Targets
- Design for -10 to -20dB margin below regulatory limits
- Prototype EMI testing with spectrum analyzer to identify problem frequencies early
- FCC Part 15 (USA), EN 55011 (Europe) — both require third-party lab testing

## Ferrite Beads
- Use on sensitive supply rails (analog, reference voltages) to isolate from noisy digital
- Choose impedance peak at frequency of concern (typically 100MHz-1GHz)
- Mount immediately at input point of sensitive subsystem

## ESD Protection Layout
- TVS diodes within **5-10mm** of I/O connector, cathode toward protected net
- Trace from connector to TVS <10mm — longer traces degrade surge immunity
- Via immediately adjacent to TVS cathode pad (within 0.5mm) for low ground inductance
- Use 0603+ footprints for >8kV ESD events; 0402 may be insufficient
- For differential signals: two TVS back-to-back (cathodes connected) or quad array packages
- Route protected signal traces over continuous ground plane for low-impedance return
- 45-degree bends near protection diodes, not 90-degree

## Common-Mode vs Differential-Mode Filtering
- **Common-mode chokes** on differential pairs at board entry: ferrite BJ/BL type (µi=1500-3000), 75-300Ω at problem frequency
- **Differential-mode filtering**: series resistors (10-100Ω) or LC filters after common-mode choke
- Mount CMC within 5mm of connector; differential filters within 10mm of choke output
- **CAN bus example**: CMC (100-300Ω @ 1MHz) + series 10-22Ω on CAN_H/CAN_L + Y-cap (100nF) midpoint to ground

## Pi-Filter Design for Power Entry
- Input cap (10-100µF, ESR <50mΩ) → series inductor (10-100µH) → output cap (10-100µF, ESR <10mΩ)
- Cutoff: fc = 1/(2π√(L×Cout)) — target 10-50% of switching frequency
- Both caps within 5mm of inductor; ground via (0.3mm) at each cap pad
- **3.3V example**: Cin=47µF, L=47µH, Cout=47µF → fc≈190kHz, ~40dB attenuation at 10MHz

## Spread Spectrum Clocking
- Modulates clock ±0.5-2.5% at 30-50kHz rate → **6-12dB peak reduction**, +3dB noise floor
- Triangular modulation most common; center spread best noise floor
- Apply to system/memory/display clocks; **never** to precision timing (GPS, audio master)
- Most ARM MCUs (STM32, i.MX, TI) have PLL spread spectrum via register settings
- Keep spread spectrum clock traces >5mm from analog/RF signals

## Slot Antenna Prevention (Ground Plane Gaps)
- Slot resonance: f = c/(2 × slot_length) — 50mm slot resonates at ~3GHz
- Via anti-pads: keep diameter <2× via hole; use 5-10mil anti-pads on inner planes
- Fill power plane cutouts with via fence (0.3mm vias @ 0.5mm pitch)
- Avoid ground splits >5mm; stitch with via fences across any necessary splits
- All ground features <5mm in critical signal bands (<2mm for USB 2.0 @ 480MHz)

## Near-Field vs Far-Field Emissions
- **Near-field boundary**: λ/(6π) — at 1GHz = ~1.6cm; at 10MHz = ~1.6m
- **H-field (current-driven)**: suppress with smaller loop areas, ground vias within 1mm, ferrite chokes
- **E-field (voltage-driven)**: shield high-voltage nodes with Faraday cage, ground via fences
- **Far-field (radiative)**: address dI/dt and dV/dt at source; board-level shielding, cable ferrites

## Pre-Compliance Testing
- **H-field probe**: 5-30mm loop, 9kHz-3GHz; start with 10mm loop for digital switching
- **E-field probe**: 1-2mm shielded disk, 10MHz-6GHz
- Spectrum analyzer: 10kHz-1GHz minimum, RBW=9kHz (CISPR 11), peak hold detection
- Scan 1cm grid, pause 1-2s per point, both vertical and horizontal probe orientations
- **CISPR 11 Class B limits**: 30 dBµV/m (30-230MHz), 37 dBµV/m (230-1GHz) at 10m

## Automotive EMC (CISPR 25)
- Multi-point grounding (not star), chassis as primary return, <1mΩ ground impedance
- Shield braid ≥95% coverage, 360° termination at connectors
- Power entry: 10-100µH choke + CMC (100-300µH @ 1MHz) + 47-100µF ceramic
- TVS within 5mm of every external connector, AEC-Q101 rated for 15kV air discharge
- Design for 40 V/m radiated immunity (33% margin over 30 V/m Class 3b requirement)

## Medical EMC (IEC 60601)
- Galvanic isolation between patient circuits and power (4mm creepage per IEC 60601-1)
- Power entry: IEC 60939 certified EMI filter, >60dB CMR @ 10MHz
- Patient leads: ferrite bead (200-600Ω @ 10MHz) on each signal
- Leakage current <1mA normal, <5mA fault condition
- Avoid ISM bands (2.4GHz, 900MHz); use spread spectrum at 50kHz modulation rate
