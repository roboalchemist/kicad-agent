---
scope: both
---

# PCB Conformal Coatings

## Contents

- [Conformal Coating Types & Selection](#conformal-coating-types--selection)
- [Conformal Coating Thickness & Application](#conformal-coating-thickness--application)
- [Conformal Coating Keep-Out Areas](#conformal-coating-keep-out-areas)

## Conformal Coating Types & Selection

### Acrylic
- **Application**: General moisture protection, consumer electronics
- **Thickness**: 25–75 µm (0.7–2.0 mils)
- **Advantages**: Low cost, easy rework, good moisture barrier, fast cure
- **Disadvantages**: Lowest chemical resistance, lowest thermal stability, poor salt spray rating
- **Temperature range**: –20°C to +80°C (–4°F to +176°F)
- **Moisture protection**: IP67 with proper cure
- **Best for**: Cost-sensitive, non-harsh environments

### Silicone
- **Application**: High temperature, high-humidity electronics, automotive
- **Thickness**: 50–150 µm (2.0–5.9 mils)
- **Advantages**: Excellent flexibility, wide temperature range, good thermal stability
- **Disadvantages**: Higher cost than acrylic, softer coating (less mechanical protection), absorbs moisture slightly
- **Temperature range**: –55°C to +150°C (–67°F to +302°F)
- **Moisture protection**: IP67 after full cure (slow cure, 7+ days for optimal properties)
- **Best for**: Automotive, aerospace, wide thermal cycling environments

### Urethane (2-Part Polyurethane)
- **Application**: Industrial, automotive, demanding environments
- **Thickness**: 50–125 µm (2.0–4.9 mils)
- **Advantages**: Excellent chemical resistance, strong mechanical protection, good thermal stability
- **Disadvantages**: Longer cure time, moisture-sensitive during application, higher cost
- **Temperature range**: –40°C to +120°C (–40°F to +248°F)
- **Moisture protection**: IP68 when fully cured, excellent salt spray resistance
- **Best for**: Harsh industrial, chemical exposure, marine environments

### Epoxy
- **Application**: Maximum protection, specialized military/aerospace
- **Thickness**: 75–250 µm (3.0–10 mils)
- **Advantages**: Best chemical resistance, best mechanical protection, superior thermal cycling resistance
- **Disadvantages**: Extremely difficult rework, highest cost, brittle if over-thick, requires careful moisture control during application
- **Temperature range**: –55°C to +130°C (–67°F to +266°F)
- **Moisture protection**: IP68, excellent for humidity and salt spray (>1000h ASTM B117)
- **Best for**: Aerospace, military, high-reliability medical, oil/gas submersible electronics

### Parylene (Vapor-Deposited)
- **Application**: Ultra-thin uniform conformal coat, medical devices, precision electronics
- **Thickness**: 5–25 µm (0.2–1.0 mils) — cannot be manually thickened beyond 25 µm
- **Advantages**: Uniform coverage (even inside cavities), pinhole-free, excellent dielectric strength, best moisture barrier at thin gauges
- **Disadvantages**: Highest cost, requires specialized equipment, no manual rework possible, slow deposition rate
- **Temperature range**: –200°C to +120°C (–328°F to +248°F) depending on type (Parylene C typical)
- **Moisture protection**: Exceptional IP68 (water absorption <0.1%)
- **Types**:
  - Parylene C: Most common, best moisture barrier, up to 120°C
  - Parylene N: Slightly lower moisture barrier, higher temperature capability
  - Parylene D: Enhanced chemical/moisture resistance
- **Best for**: High-reliability medical implants, aerospace, underwater electronics, precision optics

### Selection Matrix

| Environment | Moisture | Chemical | Thermal | Cost | Rework | Recommendation |
|---|---|---|---|---|---|---|
| Consumer indoor | Moderate | Low | Low (–10 to +60°C) | Critical | Easy | Acrylic |
| Automotive underhood | High | High (oil, salt) | High (–40 to +120°C) | Medium | Moderate | Silicone or Urethane |
| Industrial plant | High | Very High | Wide cycling | Medium | Hard | Epoxy |
| Marine/offshore | Very High | Extreme (salt spray) | Cycling | Medium | Hard | Epoxy (2-part) |
| Medical implant | Very High | Extreme (bodily fluids) | Stable | High | Impossible | Parylene |
| Aerospace | High | Extreme | Wide cycling (–55 to +125°C) | High | Hard | Epoxy or Parylene |

## Conformal Coating Thickness & Application

### Recommended Thickness Values
- **Acrylic**: 50 µm nominal (25–75 µm tolerance)
- **Silicone**: 100 µm nominal (50–150 µm tolerance)
- **Urethane**: 75 µm nominal (50–125 µm tolerance)
- **Epoxy**: 150 µm nominal (75–250 µm tolerance) — do NOT exceed 250 µm (risk of cracking on thermal cycling)
- **Parylene**: 10–15 µm for most electronics (5–25 µm limit)

### Thickness Measurement
- Measure with wet-film thickness gauge (before cure) or dry-film gauge (after cure)
- Typical cure shrinkage: Acrylic 5–10%, Silicone 2–5%, Urethane 3–8%, Epoxy 5–12%
- Account for shrinkage when targeting final thickness (e.g., spray 60 µm acrylic to achieve 55 µm final)

### Application Rules
- **Spray application**: Multiple thin passes (2–4 coats) preferred over single thick coat
- **Brush application**: One-pass maximum, risk of uneven thickness, avoid on complex boards
- **Dipping**: For simple geometries only, tends to create thick pools in recesses
- **Partial coating**: Must paint keep-out areas (see next section) to establish clear boundary; avoid feathering edges

## Conformal Coating Keep-Out Areas

### Never Coat (Clear Boundary Required)
1. **Connectors** (all types)
   - 5 mm minimum clearance from connector opening/mating surface
   - Include backshell, strain relief — leave completely clear
   - Reason: Coating prevents proper mating, increases wear, traps moisture in connector pocket

2. **Test points / pads** (if field-accessible)
   - 3 mm clearance from test pad edge (both sides of board)
   - Label kept areas with silkscreen marking "NO COAT"
   - Reason: Test probes cannot make reliable contact through coating

3. **Adjustable potentiometers, trimmer caps** (if user-accessible)
   - 10 mm clearance from adjustment screw/slot
   - Reason: Coating gums up adjustment mechanism, prevents fine tuning

4. **Battery contacts / pogo pins**
   - 3 mm minimum clearance from contact surface
   - Reason: Coating prevents electrical contact, increases contact resistance

5. **Crystal/resonator openings** (if tuning-fork design)
   - 5 mm clearance from opening
   - Reason: Coating dampens oscillator frequency

6. **Switches, buttons** (if present on PCB surface)
   - 5 mm clearance from actuation point
   - Reason: Coating prevents tactile feedback, causes stiction

7. **Sensor windows** (optical, proximity)
   - Clear 10 mm minimum around window
   - Reason: Coating reduces optical transmission or sensitivity

8. **Heat sink mounting surfaces** (if thermal paste used)
   - 5 mm clearance from paste interface
   - Reason: Coating interferes with thermal contact

### OK to Coat (Standard Coverage)
- IC packages (DIP, SOIC, BGA, QFP) — coat fully
- Resistors, capacitors, inductors — coat fully
- Solder joints (most critical area) — coat fully
- Via fields — coat fully
- Ground planes — coat fully (excellent protection against moisture)
- Non-mating connector pads (e.g., header pads) — coat fully

### Partial Coating Strategy
If keeping large areas clear is impractical:
- Use masking tape / lacquer before spraying (reusable silicone masks available)
- Apply liquid mask (maskant) to protected areas, peel after cure
- Coat first, then mask and remove conformal coat from keep-out areas (mechanical or selective stripper)
