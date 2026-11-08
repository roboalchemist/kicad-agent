---
scope: both
---

# Environmental Reliability & IPC Classes

## Contents

- [Thermal Cycling Reliability](#thermal-cycling-reliability)
- [MTBF Estimation Basics](#mtbf-estimation-basics)
- [Humidity & Moisture Protection](#humidity--moisture-protection)
- [Salt Spray & Corrosion Resistance](#salt-spray--corrosion-resistance)
- [IPC Class Levels](#ipc-class-levels)
- [PCB Material Selection for Environment](#pcb-material-selection-for-environment)
- [Quick Reference: Material Selection Chart](#quick-reference-material-selection-chart)

## Thermal Cycling Reliability

### Solder Joint Fatigue (Coffin-Manson Model)
The primary failure mode in thermal cycling is solder joint crack initiation and propagation.

```
N_f = C × (ΔT)^–m × (L)^n
```

Where:
- `N_f`: Number of cycles to 50% failure
- `ΔT`: Temperature range per cycle (°C)
- `L`: Solder joint thickness or characteristic length (mm)
- `m`: Exponent ≈ 1.9–2.0 (for SAC solder: –2.0; for Pb-free SnAgCu: –1.9)
- `n`: Exponent ≈ 0.6–1.0 (length dependence: larger joints crack sooner)
- `C`: Material constant (SAC solder: C ≈ 14,000–20,000; depends on solder composition, cooling rate, stress state)

**Simplified rule-of-thumb**:
```
N_f ≈ (300 / ΔT)^2  for SAC solder, small joints (<1mm), room-temperature storage
```

Examples (typical PCB assembly with SAC305 solder):
- ΔT = 60°C (–10 to +50°C): N_f ≈ 25 cycles
- ΔT = 80°C (–20 to +60°C): N_f ≈ 14 cycles
- ΔT = 100°C (–40 to +60°C): N_f ≈ 9 cycles
- ΔT = 125°C (–40 to +85°C): N_f ≈ 6 cycles (automotive baseline)

### Acceleration Factor (Temperature Effect)
If you test at higher ΔT than field use:

```
AF = (N_field / N_test) = ((ΔT_test / ΔT_field)^m)
```

Example: If field ΔT = 60°C but test with ΔT = 100°C:
- AF = (100/60)^2 ≈ 2.8× acceleration

**Practical testing strategy**:
- Accelerated test: 100 cycles, –40°C to +85°C (roughly equivalent to 2–3 years field life)
- Design target: >500 cycles (5–10× safety margin)

### Thermal Cycling Failure Analysis
**Solder joint cracking sequence**:
1. **Cycles 0–20%**: Crack nucleation at max stress points (solder-copper interface, close to component pad edge)
2. **Cycles 20–80%**: Stable crack growth (ductile creep + brittle fracture)
3. **Cycles 80–100%**: Final rapid fracture (reduced cross-section, stress concentration)

**Prevention**:
- Use wave solder or reflow > 220°C peak (larger fillets = lower stress concentration)
- Place thermal relief away from solder joints
- Avoid sharp PCB edges near solder joints
- Use underfill for BGA/high-lead-count packages (increases solder joint fatigue life by 2–4×)
- Select Pb-free (SAC) solder formulations with higher creep resistance than pure Sn

### PCB Warping & Thermal Stress
- **PCB CTE**: FR-4 X-axis ≈ 16 ppm/°C, Z-axis ≈ 50 ppm/°C
- **Solder CTE**: SAC ≈ 24 ppm/°C
- **Thermal mismatch stress** in solder joint:
  ```
  σ ≈ ΔT × (α_solder – α_PCB) × E_solder / (1 – ν)
  ≈ ΔT × 8 ppm/°C × 50 GPa ≈ 400 MPa per 100°C
  ```
  (Exceeds solder tensile strength at large ΔT; switches to shear-dominant failure mode)

- **Mitigation**:
  - Use copper core (reduces Z-axis CTE from 50 to 20 ppm/°C)
  - Keep BGA away from board edges (warping creates ball attachment stress)
  - Use stiff substratum (aluminum backing plate for thin boards)

### Moisture + Thermal Cycling (Synergistic Failure)
- **IPC-TM-650 2.6.7.3**: Moisture-induced warping + thermal cycling is **10–50× worse** than thermal cycling alone
- **Mechanism**: Moisture in epoxy resin causes swelling (0.3–1% in Z-axis), creates internal stress; thermal cycling then amplifies delamination
- **Prevention**:
  - Conformal coating or potting (blocks moisture ingress)
  - Dry storage (keep PCB humidity <30% RH if storing >3 months)
  - Bake-out before assembly: 4 hours @ 120°C + 2 hours @ 80°C + room-temperature cool

## MTBF Estimation Basics

### MTBF (Mean Time Between Failures)
```
MTBF = 1 / λ (failure rate in failures per hour)
```

**Relationship to cycles/time**:
- If failure mode is thermal cycling: MTBF = N_cycles / hours per cycle
- If failure mode is continuous operation: MTBF = hours to failure

### Component-Level Failure Rates (MIL-HDBK-217F Baseline, 25°C)
- **Resistor**: λ ≈ 0.0001 to 0.001 FITs (failures per 10^9 hours, depends on power rating, tolerance)
- **Capacitor (ceramic)**: λ ≈ 0.001 to 0.010 FITs
- **Capacitor (electrolytic)**: λ ≈ 0.001 to 0.100 FITs (highly temperature-dependent: ~50% per 10°C rise)
- **IC (CMOS logic)**: λ ≈ 0.0001 to 0.001 FITs
- **Solder joint**: λ ≈ 0.0001 to 0.0010 FITs (under thermal cycling, much higher than static operation)

### Temperature Acceleration (Arrhenius Model)
```
λ(T) = λ_ref × exp(Ea / k × (1/T_ref – 1/T))
```

Where:
- `λ(T)`: Failure rate at temperature T
- `λ_ref`: Baseline failure rate at reference temperature
- `Ea`: Activation energy (eV; typical: 0.3–0.7 eV for solid-state failures)
- `k`: Boltzmann constant (8.617e-5 eV/K)
- `T`, `T_ref`: Absolute temperature (K)

**Simplified rule** (1 eV activation energy):
```
λ doubles for every 10°C rise (or halves for every 10°C drop)
```

### System MTBF (Series Components)
```
MTBF_system = 1 / (Σ λ_i)
```

Example: PCB with 100 resistors (0.0001 FIT each), 10 capacitors (0.005 FIT each), 5 ICs (0.001 FIT each):
```
Σ λ = (100 × 0.0001) + (10 × 0.005) + (5 × 0.001)
    = 0.01 + 0.05 + 0.005
    = 0.065 FIT

MTBF = 1 / (0.065 / 10^9) = 15.4 × 10^9 hours ≈ 1.76 million years (theoretical)
```

Note: Real-world MTBF is much lower due to:
- Field environmental stress (higher T, humidity, vibration)
- Manufacturing defects (infant mortality, solder voids)
- Overstress conditions (ESD, voltage spikes, thermal cycling)
- **Derating factor**: Apply 50–80% reduction for realistic field estimate

## Humidity & Moisture Protection

### Moisture Ingress Mechanisms
1. **Surface adsorption**: Moisture condenses on hygroscopic materials (epoxy resin, conformal coating, solder mask)
2. **Absorption into bulk**: Water molecules diffuse through polymer matrix (slower process, weeks to months)
3. **Delamination sites**: Moisture pools at layer interfaces (PCB laminate, component leads, vias)
4. **Capillary wicking**: Moisture wicks under components, connectors, along solder joints

### Moisture-Induced Failures
- **Electrochemical migration**: Water + copper → Cu2O + H2O2 + acids; forms conductive filaments between high-voltage nets (>12V DC at high humidity)
- **Solder joint corrosion**: Flux residues + moisture → local galvanic corrosion, intermittent opens
- **IC lead corrosion**: Copper leads oxidize to Cu2O (black), increases contact resistance
- **Insulation breakdown**: Moisture lowers surface insulation resistance (SIR) by 100–1000×

### Relative Humidity (RH) Critical Thresholds
- **<50% RH**: Safe for most electronics; no condensation risk
- **50–80% RH**: Moderate risk; conformal coating or pot recommended for reliability
- **80–95% RH**: High risk; potting or hermeticity required
- **>95% RH**: Critical; must use potting + hermetic enclosure or active desiccant

### Protection Strategies (in order of effectiveness)
1. **Hermeticity (best)**: Sealed metal/glass enclosure, moisture-proof connector backshells — achieves <5% RH inside
2. **Potting (very good)**: Encapsulation in epoxy/urethane/silicone — creates moisture barrier; residual RH inside ≈ 60–70% after equilibrium (weeks to months)
3. **Conformal coating (good)**: Acrylic/silicone/epoxy coat — slows moisture ingress; time to 95% RH inside ≈ days to weeks
4. **Silkscreen resist (basic)**: Solder mask on PCB — provides minimal moisture barrier; not sufficient alone

### Moisture Testing Standards
- **IPC-TM-650 2.3.28** (moisture-insensitivity level, MIL): PCB stored at 85°C/85% RH; time to moisture saturation ≈ 85 hours MIL-MSL-3 (baseline for ceramics)
- **ASTM B117** (salt spray): 1000 hours = acceptable for marine (epoxy coating), acrylic typically fails <500 hours
- **IPC-9701**: Moisture + reflow = leads to popcorning (rapid moisture vapor pressure ruptures epoxy, delaminates PCB layers)

## Salt Spray & Corrosion Resistance

### ASTM B117 Salt Spray Test
- **Setup**: 5% NaCl solution atomized in enclosed chamber at 35°C, continuous mist
- **Failure criteria**: Red rust formation on bare copper (first 24–48 hours), white corrosion products
- **Typical results** (baseline, uncoated copper):
  - Bare copper: Rust appears in <24 hours
  - Tin/lead solder: ≈100–200 hours
  - Nickel plating (2–5 µm): ≈200–500 hours
  - **Tin plating + solder resist**: ≈500–1000+ hours
  - **Acrylic conformal coating**: ≈200–500 hours (some moisture penetration at edges)
  - **Silicone conformal coating**: ≈500–1000 hours (better edge seal)
  - **Epoxy conformal coating**: ≈1000–2000 hours (excellent barrier)
  - **Urethane conformal coating**: ≈1000–2000 hours (excellent barrier)
  - **Parylene**: ≈2000+ hours (pinhole-free coverage)

### Corrosion Prevention Design Rules
1. **Avoid bare copper**: Always use solder mask, plating (Ni/Au), or conformal coating over exposed copper
2. **Via placements**: Plug via holes (copper, epoxy, or paste) to prevent moisture/salt ingress
3. **Connector backshells**: Use conformal coating OR sealed backshell (drain holes plugged with silicone)
4. **Ground plane**: Full ground pour and conformal coat significantly reduces galvanic corrosion (creates continuous low-impedance return path)
5. **Electrical clearance**: Increase spacing between high/low voltage nets if salt exposure possible (>10 mm for marine environments)

### Material Selection for Salt Spray
- **PCB finish**: ENIG (electroless nickel immersion gold) > HAL (hot air leveling) > HASL + solder resist
- **Component leads**: Tin-plated > nickel-plated > bare copper
- **Conformal coat**: Epoxy or Urethane > Silicone > Acrylic
- **Potting**: Urethane > Epoxy (silicone is hygroscopic, not ideal)

## IPC Class Levels

### IPC Assembly Class Standards (IPC-A-610)

#### Class 1: General Consumer Electronics
- **Target**: Low-cost consumer products (toys, appliances, non-critical)
- **Reliability requirement**: Minimal (product lifespan months to 1–2 years)
- **Defect threshold**: Highest tolerance for cosmetic defects, lead coplanarity, solder fillet issues
- **Conformal coating**: Optional, not required
- **Potting**: Not required
- **Environmental testing**: None required at production (may be spot-checked)
- **Visual inspection**: Basic (major defects only; microvoids/solder bridges allowed)
- **Rework**: Allowed with minimal documentation
- **Example**: Cheap consumer USB hub, budget toy, single-use device

#### Class 2: General Industrial Electronics
- **Target**: Industrial equipment, commercial products, moderate-duty applications
- **Reliability requirement**: Moderate (product lifespan 5–10 years)
- **Defect threshold**: Balanced acceptance/rejection criteria
- **Conformal coating**: Recommended for harsh environments (humidity, temperature cycling)
- **Potting**: Optional (for vibration-prone or high-humidity applications)
- **Environmental testing**: May include thermal cycling, humidity dwell (customer-defined)
- **Visual inspection**: IPC-A-610 Class 2 criteria (minor voids tolerated, lead coplanarity within spec)
- **Rework**: Allowed with documented procedures; rework parts tagged
- **Example**: Industrial control PLC, commercial HVAC controller, automotive (non-safety), telecom gateway

#### Class 3: High-Reliability / Safety-Critical Electronics
- **Target**: Aerospace, medical devices, automotive safety systems, military, mission-critical infrastructure
- **Reliability requirement**: High (product lifespan 10–20+ years, downtime = catastrophic)
- **Defect threshold**: Minimal tolerance; zero-defect mindset
- **Conformal coating**: Mandatory (epoxy or parylene); formal coating plan required
- **Potting**: Often mandatory (high-reliability medical, aerospace)
- **Environmental testing**: Extensive (thermal cycling –40 to +85°C ≥100 cycles, salt spray 500–1000 hours, humidity 85/85, vibration per MIL-STD-810, shock per MIL-STD-810)
- **Visual inspection**: IPC-A-610 Class 3 (almost no voids allowed, no bridging, lead coplanarity strict)
- **Testing requirements**: 100% electrical test, burn-in (24 hours @ elevated temp typical), HALT/HASS
- **Rework**: Severely restricted; any rework requires re-qualification testing
- **Documentation**: Full traceability (lot codes, date codes, material certs, process records)
- **Example**: Aircraft avionics, pacemaker, automotive ABS/airbag controller, submarine electronics, nuclear instrumentation

### IPC Cleanliness Classes (Flux/Solder Residue)

#### Level 1 (Most Stringent)
- **Ionic cleanliness**: <1.56 µg NaCl equivalent per cm² (cleanliness > 99.9%)
- **Residue type**: Only acceptable: fully cured flux residues
- **Cleaning requirement**: Aqueous wash + hot air dry, or aqueous + IPA rinse
- **Application**: Class 3 aerospace, medical, military
- **Cost impact**: Significant (extra wash process, extended cure time to ensure full polymerization)

#### Level 2 (Moderate)
- **Ionic cleanliness**: 1.56–3.12 µg NaCl eq/cm²
- **Residue type**: Mildly active flux allowed (small amounts)
- **Application**: Class 2 industrial, most commercial
- **Cost impact**: Standard cleaning (water wash)

#### Level 3 (Least Stringent)
- **Ionic cleanliness**: >3.12 µg NaCl eq/cm²
- **Residue type**: Active flux residues allowed
- **Application**: Class 1 consumer (often no cleaning)
- **Cost impact**: Minimal (conformal coat covers residues)

### IPC Class Selection Decision Tree
1. **Product in harsh environment?** (vibration, thermal cycling, salt spray, high humidity)
   - YES → Class 3
   - NO → Continue

2. **Safety-critical or downtime = major cost?** (aerospace, medical, automotive safety, infrastructure)
   - YES → Class 3
   - NO → Continue

3. **Field rework feasible / expected?**
   - NO (sealed enclosure, potted, difficult access) → Class 3
   - YES → Continue

4. **Expected product lifespan >7 years in field?**
   - YES → Class 2 minimum (Class 3 recommended if environmental stressors present)
   - NO → Class 1 acceptable

5. **Default**: Class 2 (balanced cost/reliability; covers 95% of real-world applications)

### IPC Testing by Class
| Test | Class 1 | Class 2 | Class 3 |
|---|---|---|---|
| Visual inspection (IPC-A-610) | Basic | Standard | Strict |
| Thermal cycling (–40 to +85°C) | Optional | Recommended (10 cycles) | Required (≥100 cycles) |
| Salt spray (ASTM B117) | Not required | Optional (250–500 h) | Required (500–1000 h) |
| Humidity (85/85 RH) | Not required | Optional (168 h) | Required (168–500 h) |
| Conformal coating | Not required | Recommended (harsh env) | Required |
| Potting | Not required | Optional | Often required |
| Electrical test | Sample (AQL 4%) | 100% if critical nets | 100% all nets |
| Burn-in | Not required | Optional | Recommended (24 h) |

## PCB Material Selection for Environment

### FR-4 vs FR-5 vs Polyimide

| Property | FR-4 | FR-5 | Polyimide |
|---|---|---|---|
| Glass transition (Tg) | 120–130°C | 150–170°C | 260–300°C |
| Max operating temp | 130°C | 170°C | 250°C |
| CTE (Z-axis) | 50 ppm/°C | 40 ppm/°C | 20–25 ppm/°C |
| Moisture absorption | 0.15% | 0.10% | 0.25–0.4% (higher!) |
| Thermal conductivity | 0.2–0.3 W/m·K | 0.3–0.4 W/m·K | 0.3–0.4 W/m·K |
| Cost | Baseline (1×) | +20% | +150–200% |
| Use cases | General purpose | High-temp industrial | Extreme temp (aerospace, downhole), flexible PCB |
| Salt spray rating | Moderate | Good | Excellent |

**Recommendation**:
- **Consumer/Industrial**: FR-4 (cost-effective, adequate to 130°C continuous)
- **Extended temp (up to 170°C continuous)**: FR-5
- **Aerospace/Extreme (>200°C, vibration, salt)**: Polyimide (also used for flex boards)

---

## Quick Reference: Material Selection Chart

| Environment | Coating | Potting | Class | PCB Material |
|---|---|---|---|---|
| Dry indoor, <10 years | Acrylic (optional) | None | 1–2 | FR-4 |
| Industrial, vibration, humidity | Silicone or Urethane | None | 2–3 | FR-4 |
| Automotive underhood, salt | Urethane | Optional | 3 | FR-4/FR-5 |
| Marine/offshore, salt spray | Epoxy | Urethane | 3 | FR-4 + Ni/Au finish |
| Medical implant | Parylene | None (or conformal only) | 3 | FR-4 or Flex |
| Aerospace, –55 to +150°C | Epoxy or Parylene | Epoxy (as needed) | 3 | FR-5 or Polyimide |
| Underwater, 500 m depth | Parylene | Potted (urethane) | 3 | FR-4 + Ni/Au |
