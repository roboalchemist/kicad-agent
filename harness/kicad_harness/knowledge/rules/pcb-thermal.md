---
scope: both
---

## Thermal Vias

### Sizing
- Diameter: 0.3 mm optimal, range 0.2–0.4 mm (8–16 mil)
- Aspect ratio: ≤ 10:1 (board thickness to drill diameter)
- Via-to-via spacing: 0.8 mm optimal, 1.2–1.5× via diameter minimum
- Array pattern: Multiple smaller vias superior to fewer large ones under same pad area

### Pattern & Placement
- Distribute under heat source rather than single column
- Connect directly to solid copper planes (power/ground), no thermal relief around vias
- Fill with conductive material (copper plating void-free)
- For high-power components: 2–4 vias minimum; scale up as needed

### Connection to Heatsinks
- Extend vias to bottom copper layer for heatsink thermal contact
- Ensure minimum 0.5 mm bottom pad clearance around via clusters
- Bottom copper thickness: 2 oz/ft² or higher for heatsink coupling

## Thermal Relief Pads

### Spoke Design
- Spoke count: 3–4 per pad (4 preferred for reliability)
- Spoke width: 0.2–0.5 mm depending on current path requirements
- Spoke length: ≥0.5 mm to limit heat leakage during soldering
- Avoid thermal relief on pads with thermal vias underneath

### When to Use
- Soldering-intensive layouts (top/bottom pads on power planes)
- Signal pads connecting to ground/power planes
- Do NOT use on thermal via pads (increases thermal resistance)

## Copper Spreading & Planes

### Thickness & Coverage
- Power/ground planes: minimum 0.5 oz/ft²; use 2 oz/ft² or higher for high-power designs
- Top copper pad coverage: ≥ 2× component pad area when possible
- Contiguous copper zones preferred; avoid segmentation around pad edges

### Area Distribution
- Spread heat over ≥ 4× component footprint area within first 5 mm
- Create continuous copper fill to board edges or designated heat sinks
- Use wide traces (≥0.5 mm) to connect high-power pads to copper planes

## Component Derating

### Temperature Calculation
- Junction temperature: Tj = Ta + (P × θJA)
  - Tj: junction temp (°C)
  - Ta: ambient temp (°C)
  - P: power dissipation (W)
  - θJA: junction-to-ambient thermal resistance (°C/W)
- Total thermal resistance chain: RthJA = RthJC + RthCS + RthSA
  - RthJC: junction-to-case (~0.5–5 °C/W)
  - RthCS: case-to-sink (~0.1–0.5 °C/W with TIM)
  - RthSA: sink-to-ambient (~1–20 °C/W)

### Derating Rules
- Keep Tj ≤ Tmax - 20 °C (20 °C margin minimum)
- Derate power output by 0.3–0.5% per °C above 25 °C (check datasheet)
- Conservative design: target Tj ≤ 70% Tmax at max ambient + worst-case airflow

### Corner Cases
- Enclosed designs: assume 0 cfm (natural convection, RthSA ≤ 50 °C/W)
- Forced airflow: validate with thermal FEA; RthSA can drop to 5–10 °C/W

## Thermal Simulation Basics

### Tool Selection
- **ANSYS Icepak**: CFD-primary, complex multi-component assemblies, 3D airflow
- **Simcenter Flotherm**: Balanced CFD + FEA, electronics-focused, fan modeling
- **SOLIDWORKS Flow Simulation**: Accessible CFD within CAD, smaller designs
- **Cadence/Altium**: Integrated PCB thermal, component-level analysis

### Setup Parameters
- Boundary conditions: ambient temp, convection coefficient (natural: 5 W/m²K, forced: 20–100 W/m²K)
- Heat source definition: per-component power or power density
- Mesh density: ≥3 layers around high-power components
- Simulation time: convergence check every 5 min (transient) or iterate to <1% change (steady-state)

### Results Interpretation
- Identify hotspots (Tj > rated Tmax or derating threshold)
- Validate airflow patterns match intended convection paths
- Report max Tj, Tj distribution across board, thermal gradients (ΔT)
- Tolerance: simulation ±5°C vs. measured typical

## Hot Spot Identification

### Pre-Simulation Analysis
- Rank components by power dissipation and thermal resistance
- Flag: any Tj prediction > 85 °C without active cooling
- Check: components with θJA > 10 °C/W in high-ambient enclosures

### Post-Simulation Review
- Identify regions Tj > 60 °C (natural convection limit)
- Zone 1 (critical): any Tj > Tmax - 20 °C
- Zone 2 (concern): any ΔT > 15 °C between board center and edges
- Mitigation: relocate high-power parts, add vias, increase copper area, active cooling

## Thermal Zones on PCB

### Zone Definition
- **Hot zone**: high-power components (CPU, regulators, power amplifiers), Tj ≥ 70 °C
- **Warm zone**: moderate dissipation (signal processors), Tj 50–70 °C
- **Cool zone**: low-power logic (microcontrollers, low-loss signals), Tj ≤ 50 °C
- Keep hot & cool zones separated by ≥ 20 mm if possible

### Layout Guidelines
- Place heat sources at board edges or designated sink areas
- Surround hot zone with ground/power plane for spreading and isolation
- Route sensitive signals away from hotspot paths
- Reserve cool zone for temperature-sensitive components (oscillators, analog circuits)

## Airflow Considerations

### Natural Convection (0 cfm)
- RthSA baseline: 40–50 °C/W for typical PCB in still air
- Vertical board orientation: 10–15% improvement over horizontal
- Enclosure ventilation: add minimum 10 mm clearance top/bottom edges
- Effective convection coeff: 5 W/m²K (low, conservative)

### Forced Airflow (fans)
- Minimum 0.5 cfm per watt for compact designs (safety margin)
- Optimal 1–2 cfm/W for margin + duct losses
- Duct design: uniform airflow across board, ≥20 mm duct height for laminar flow
- Temperature drop potential: 15–20 °C with 2+ cfm/W airflow
- Verify in simulation: validate actual airflow path vs. assumed

### Convection Coefficient
- Natural convection: 5 W/m²K
- Forced airflow (0.5 m/s): 25 W/m²K
- Forced airflow (2+ m/s): 75–100 W/m²K
- Use CFD to refine per geometry

## Thermal Interface Materials (TIM)

### Thermal Conductivity Ranges
- Thermal pads: 1–5 W/m·K
- Thermal grease: 2–8 W/m·K
- Phase-change materials (PCM): 3–6 W/m·K
- Graphite/metal-filled advanced: 8–15 W/m·K
- Indium alloys (premium): 15–60 W/m·K
- Sintered silver: 100+ W/m·K (expensive, brittle)

### Material Selection
- Budget designs: thermal pads (1–3 W/m·K), min gap thickness 0.2–0.5 mm
- Mid-range: PCM or grease (3–8 W/m·K), bondline 0.1–0.3 mm
- High-performance: metal-filled (10–15 W/m·K) or specialty alloys, < 0.1 mm gap
- Compliance: ensure TIM covers >90% pad/sink interface to minimize voids

### Application
- Bondline thickness: critical; thicker = higher resistance (linear relationship)
- Standard application: 1–1.5 mm before compression
- Pressure: 50–150 psi typical for pad materials (check TIM spec)
- Cure/set time: allow per datasheet before operation (24–48 hr typical)
- Spreading resistance (pad-to-case): typically 0.1–0.5 °C/W depending on TIM

## Design Checklist

- [ ] Calculated max Tj for all power components at max ambient + worst-case airflow
- [ ] All Tj ≤ Tmax - 20 °C (or worse-case margin validated in simulation)
- [ ] Thermal vias: ≥0.3 mm diameter, ≤0.8 mm spacing, connected to inner planes
- [ ] Copper planes: 2 oz/ft² minimum, contiguous under hot components
- [ ] Thermal relief: 3–4 spokes, 0.2–0.5 mm width, only on soldered pads
- [ ] Heatsink pads: ≥ 2× component area, TIM thickness specified, pressure spec defined
- [ ] Airflow path: verified in CAD or CFD; natural convection or forced air documented
- [ ] Hotspot analysis: simulation or empirical correlation completed; all zones confirmed safe
- [ ] TIM selection: thermal conductivity, bondline thickness, and cure time specified in assembly notes
- [ ] Thermal margins: documented per component; derating strategy for production test temp range
