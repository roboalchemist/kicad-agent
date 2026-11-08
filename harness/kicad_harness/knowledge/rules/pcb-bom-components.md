---
scope: both
---

# PCB BOM and Component Selection

## Component Derating Guidelines

### Voltage Derating
- **Aluminum electrolytic capacitors**: Voltage + ripple < 90% of rated voltage
- **Ceramic capacitors**: Derate to ~50% of rated voltage (preferred conservative margin)
- **Tantalum capacitors**: Derate to 80% for ambient -55°C to 105°C (e.g., 35V component sees max 28V)
- **General rule**: Apply minimum 20% voltage derating for reliability; 50% preferred for harsh environments

### Current Derating
- **Resistors**: Continuous power < 50% of rated power
- **Components (general)**: Maximum operating current < 25–90% of rated current (application-dependent)
- **Semiconductor junctions**: Keep junction temperature 15°C below maximum rated operating temp

### Temperature Derating
- **ICs (general)**: Junction temperature max 85–125°C (derate 15°C before absolute max)
- **Electrolytic caps**: Derate life by 50% for every 10°C above 85°C
- **Passive components**: Verify derating curves in datasheet; temperature affects resistance, capacitance, and inductance

### Power Derating
- **Resistors**: < 50% of rated power dissipation
- **Power semiconductors**: Derate per thermal resistance and heatsink design
- **Rule of thumb**: Design for 2x maximum expected stress initially; apply derating curves as workload increases

## Component Package Selection

### Preferred Packages by Application
- **Hand soldering/prototyping**: Through-hole, large SMD (0805, 1206+), SOIC-8, DIP
- **High-density boards**: 0402, 0201 passives; BGA, LFCSP for ICs
- **Cost optimization**: Use fewest package types; standardize on 0603/0805 for passives if possible
- **Thermal management**: Expose pad packages (LFCSP, QFN) for high-power ICs
- **Rework accessibility**: Avoid BGA on single-layer designs; prefer QFP/LQFP for manual rework
- **Vibration/shock**: Through-hole or large SMD (≥0603) preferred over 0402/0201

### IPC Density Levels (IPC-7351)
- **Level A (Maximum): Most spacing** — Hand assembly, low-volume, relaxed tolerances
- **Level B (Nominal): Balanced** — Standard PCB manufacturing, most production runs
- **Level C (Least spacing): High density** — Fine-pitch, automated high-volume, ≥0.4mm pitch

### SMD Footprint Standards
- **NSMD pads (non-solder mask defined)**: Solder mask opening > metal pad; better solder joint reliability (recommended)
- **SMD pads (solder mask defined)**: Solder mask opening < metal pad; smaller footprint, higher risk
- **Pad sizing**: Per IPC-7351 formulas; verify datasheet land pattern matches density level
- **Trace clearance**: Minimum 0.15mm (6 mil) from pad edge; increase to 0.25mm (10 mil) for signal integrity

## Component Lifecycle Management

### Component Status Classification
- **Active**: Current production, full availability, no end-of-life announced
- **NRND (Not Recommended for New Designs)**: Available but manufacturer discourages use; replace before design freeze
- **EOL (End of Life)**: Production ending soon; set last-time-buy (LTB) deadline
- **LAST TIME BUY (LTB) phase**: Final procurement window; typically 3–6 months before discontinuation
- **Obsolete (OBS)**: No longer manufactured; procurement only via gray market (higher cost, lower quality risk)

### Lifecycle Tracking Rules
- Check component status before design commit (use vendor datasheets, parametric search)
- Identify NRND/EOL parts during design review; plan replacement path early
- Track LTB deadlines in BOM comments; procurement must act 30–60 days before expiration
- Document alternate parts for EOL items before they become obsolete
- For safety-critical designs: avoid EOL parts unless full supply procurement is secured at design start

### End-of-Life Response
1. Trigger: Manufacturer announces EOL with date
2. LTB window: Typically 30–180 days before final shipment
3. Action: Secure full 10+ year supply if part is unique; otherwise design replacement
4. Documentation: Note "REPLACED BY X effective DATE" in BOM

## Alternate/Second Source Strategy

### Primary vs Alternate Parts
- **Maintain 2–3 alternates minimum** per unique component function (voltage regulation, oscillator, connector, etc.)
- **Alternates must**: Match or exceed electrical specs, fit same footprint (or require documented rework), be available from multiple distributors
- **Cross-reference tables**: Document substitution rules (e.g., "STM32F407 → STM32H743 requires firmware change") in BOM or design notes
- **Pin compatibility**: Verify pinout, voltage levels, thermal behavior, frequency response

### Parametric Search Strategy
- Use tools: Octopart, FindChips, Sourcengine, parametric database from distributors (Digi-Key, Mouser, Arrow)
- **Search filters**: Voltage, current, package, temperature range, price, availability (stock count), lead time
- **Comparison workflow**: 1) Define specs, 2) Search all distributors, 3) Filter by cost + availability, 4) Verify datasheets match, 5) Add to BOM
- **Single-source risk**: Flag any part with only one manufacturer or distributor; require alternate or justify exception

### Availability Planning
- **Lead time thresholds**: < 4 weeks (low risk), 4–8 weeks (medium), > 8 weeks (high)
- **For high-risk components**: Procure 50% surplus stock at production or negotiate consignment
- **Supply chain diversity**: Use parts from ≥2 different manufacturers when possible
- **Allocation awareness**: During shortages, limit per-order quantities to ensure fair allocation from distributors

## Footprint Verification Process

### Design Verification Checklist
- [ ] Footprint name matches component package in datasheet exactly (e.g., "LQFP-100" not "LQFP-100_OSHPARK")
- [ ] Pad count and pitch match datasheet (e.g., 100 pins, 0.5mm pitch)
- [ ] Pad sizes verified against IPC-7351 density level and land pattern in datasheet
- [ ] Solder mask opening type documented (NSMD preferred, SMD allowed if justified)
- [ ] Silkscreen: Part outline, pin 1 marker, reference designator visible after population
- [ ] Clearance rules: 0.15mm min (0.25mm preferred) from traces; no vias in pads
- [ ] 3D model available and renders correctly in assembly drawing

### Manufacturing Verification
- **Footprint to assembly drawing**: Export PDF with all footprints and confirm visual against BOM photos
- **Panelization**: Check footprint spacing within panel; verify no shorts from soldermask registration errors
- **Test points**: Add test pads for power/ground on high-pin-count parts (BGA, LQFP)
- **Assembly preview**: Gerber preview + actual assembly drawing reviewed by PCB fab before manufacturing

## Moisture Sensitivity (MSL) & Handling

### MSL Rating Scale (IPC/JEDEC J-STD-020)
- **MSL 1**: Not moisture sensitive; no floor life restrictions; 5-year shelf life at ≤30% RH
- **MSL 2**: Floor life 1 year at ≤30% RH
- **MSL 2a**: Floor life up to 4 weeks at ≤30% RH
- **MSL 3**: Floor life 168 hours (7 days) at ≤30% RH, then bake before use
- **MSL 4**: Floor life 72 hours (3 days); very sensitive
- **MSL 5**: Floor life 48 hours; extremely sensitive
- **MSL 5a**: Floor life 24 hours; critical
- **MSL 6**: Moisture damage risk immediate after MBB opening; bake and use same day

### Storage & Handling Requirements
- **Dry pack**: MSL 2+ components require moisture barrier bag (MBB) with desiccant and humidity indicator card (HIC)
- **Storage environment**: ≤5% relative humidity (RH) for MSL 3+; ≤30% RH for MSL 1–2
- **Floor life**: Once MBB is opened, timer starts; if exceeded without baking, component must be discarded or rebaked
- **Baking procedure**: 125°C ± 5°C for 24 hours in dry oven (< 5% RH) to remove absorbed moisture before reflow
- **Humidity balance**: Maintain 40–60% RH in assembly area to minimize MSL risk while reducing ESD risk (> 30% RH prevents ESD)

### Common MSL Components
- **BGA/LFCSP packages**: Usually MSL 3–5 (moisture trapped under package)
- **Ceramic capacitors (0603 and smaller)**: Often MSL 3
- **Tantalum capacitors**: MSL 3–5 depending on voltage/case size
- **Microcontrollers (LQFP/QFN)**: MSL 3–4
- **Standard discrete (0805/1206)**: Typically MSL 1 (check datasheet)

## ESD Sensitivity Ratings

### ESD Classification (IEC 61340-2-1)
- **Class 0 (< 100V)**: Extremely sensitive (MOSFET gates, JFET, high-speed logic)
- **Class 1a (100–200V)**: Very sensitive (most ICs, op-amps, microcontrollers)
- **Class 1b (200–500V)**: Sensitive (standard logic, memory)
- **Class 2 (500–1kV)**: Moderately sensitive
- **Class 3 (1–2kV)**: Moderately insensitive
- **Class 4 (> 2kV)**: Insensitive (power transistors, thick-film hybrids)

### ESD Handling Best Practices
- All ICs (micros, op-amps, analog) are Class 0–1; assume ESD-sensitive unless rated otherwise
- Use grounded wrist straps + ESD mats during assembly and rework
- Store sensitive components in static-safe bags or ESD-safe trays
- Avoid touching component leads or pads directly
- Maintain humidity 40–60% RH during assembly (< 30% increases ESD risk; > 60% increases moisture risk)
- Use antistatic packaging for delivery and long-term storage

## Component Availability & Lead Time Planning

### Lead Time Tracking
- **Short lead time**: < 4 weeks (readily available)
- **Standard lead time**: 4–8 weeks (normal procurement window)
- **Long lead time**: > 8 weeks (requires advance planning; check availability at design freeze)
- **Custom lead time**: 12+ weeks (custom part, test, or low-volume specialty component)

### Lead Time Mitigation
1. **Parametric search early**: During component selection, check current stock levels and lead times
2. **Buffer stock**: For long-lead components, procure 20–50% extra at production
3. **Dual source**: Define alternates for parts with single-source risk
4. **Negotiate allocation**: Work with distributor for priority allocation during shortage
5. **Stagger procurement**: Order high-risk parts 12+ weeks before assembly; defer standard parts to 6–8 weeks before

### Supply Chain Monitoring
- Monitor distributor allocation notices (issued 30–90 days before shortage)
- Track industry reports (chip shortage cycles typically 18–36 months)
- Check for design obsolescence notices (NRND/EOL) immediately upon component selection
- Use supply chain APIs (Octopart, IntelliSense) to track real-time availability trends

## BOM Documentation Requirements

### Required BOM Fields (Minimum)
1. **Reference Designator**: U1, R1, C1, etc. (matches schematic and board silkscreen)
2. **Manufacturer Part Number (MPN)**: Exact part code from manufacturer datasheet
3. **Description**: Brief function (e.g., "Microcontroller STM32F407VGT6", "Ceramic Cap 100nF")
4. **Package/Case**: SMD package code (0603, SOIC-8, LQFP-100, BGA-48, etc.)
5. **Quantity**: Per board × number of boards
6. **Unit**: "pieces" (or "mm" for discrete wire/mechanical)
7. **Manufacturer**: Company name (TI, NXP, Murata, etc.)
8. **Distributor / Stock**: Primary distributor + alternate (Digi-Key, Mouser, Arrow, etc.)
9. **Unit Cost**: Supplied by distributor (updates quarterly)
10. **Extended Cost**: Unit cost × quantity

### Recommended BOM Fields (Enhanced)
11. **Alternative Part Numbers**: List 1–2 approved substitutes per component (same specs, different MPN)
12. **Voltage/Current Rating**: Operating specs (e.g., "16V, 100mA")
13. **Temperature Range**: Operating range (e.g., "-40°C to +85°C")
14. **Tolerance**: For resistors/capacitors (e.g., "±5%", "±10%")
15. **Datasheet Link**: URL or local path to component datasheet PDF
16. **Component Status**: Active / NRND / EOL / Obsolete
17. **Lead Time (weeks)**: Current lead time from distributor
18. **MSL Rating**: Moisture sensitivity level (MSL 1–6)
19. **ESD Sensitivity**: Class 0–4 (only for ICs; omit for passives)
20. **Notes**: EOL replacement part, LAST-TIME-BUY deadline, supply constraints, etc.
21. **Availability Check Date**: When availability/lead time was last verified

### BOM Organization
- **Sort by reference type**: All resistors (R1–R50), all capacitors (C1–C100), all ICs (U1–U20)
- Or **sort by function**: Power section, analog section, digital section, connectors
- **Group duplicates**: If R1 and R2 are both 10k 0805, list as single line with Qty=2
- **Separate test components**: Mark test points, jumpers, 0Ω links clearly
- **DNP (Do Not Populate)**: Clearly mark with "—" or "DNP" in quantity field

### BOM Release & Change Control
- **Freeze date**: BOM locked at design review; no changes without ECN (engineering change notice)
- **Version control**: Track BOM revisions (v1.0, v1.1, etc.) with date and change summary
- **Cross-reference**: Link BOM to schematic revision, PCB revision, assembly drawing
- **Obsolescence tracking**: Update BOM ≥quarterly for lead time + status changes
- **Manufacturing handoff**: Issue final BOM 4–6 weeks before assembly start

### Sourcing & Quotation
- **RFQ (Request for Quote)**: Submit to ≥2 suppliers with complete BOM; include lead time + availability
- **Quote analysis**: Compare unit price, lead time, MOQ (minimum order quantity), NRE (non-recurring engineering)
- **Procurement authority**: Cross-check MPN spelling, package, manufacturer against datasheet before purchase order (PO)
- **Post-receipt inspection**: Verify received parts match MPN, packaging, and documentation on receipt

## Design Best Practices

### Component Selection Workflow
1. **Define electrical specs** (voltage, current, frequency, temperature range)
2. **Parametric search**: Find candidates from multiple manufacturers with ≥3 options per function
3. **Compare datasheets**: Verify specs, package, availability, cost, lead time
4. **Check status**: Confirm part is Active (not NRND/EOL); verify ≥2-year supply availability
5. **Identify alternates**: Document 1–2 approved second sources with cross-reference
6. **Thermal analysis**: Verify derating curves for temperature/power/voltage
7. **Finalize MPN**: Use exact manufacturer part number; avoid generic descriptors
8. **BOM entry**: Add all required fields; include datasheet link and availability check date

### Design Review Gates
- **Component selection freeze**: Before PCB layout starts; verify no NRND/EOL parts
- **BOM freeze**: Before manufacturing quote; lead times confirmed, alternates documented
- **Assembly readiness**: Confirm all parts in stock ≥2 weeks before production; no surprises

### Documentation Handoff
- **Schematic**: All reference designators match BOM exactly
- **PCB layout**: All footprints verified against BOM package codes
- **Assembly drawing**: Shows all populated references, test points, orientation marks
- **Manufacturing notes**: Identify MSL components, ESD-sensitive parts, thermal management areas
- **Rework procedure**: Document hand-soldering or thermal rework steps for critical components

