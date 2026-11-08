---
scope: personal
---

# PCB RF and Microwave Layout Design Rules

## Transmission Line Types and Selection

### Microstrip
- Use for frequencies ≤6 GHz
- Single signal trace over ground plane with dielectric above
- Lower cost, easier fabrication
- More susceptible to EMI and crosstalk
- Loss increases with frequency due to dielectric and conductor losses

### Stripline
- Sandwiched between two ground planes
- Excellent shielding from adjacent planes and EMI
- More complex manufacturing
- Lower radiation and crosstalk compared to microstrip
- Preferred for high-density, sensitive designs

### Coplanar Waveguide (CPW)
- Signal trace with ground traces on same layer
- Balanced shielding and fabrication simplicity
- Better radiation suppression at ≤3 GHz
- Requires careful trace spacing and via fencing
- Bottom layer must be solid ground plane

### Grounded Coplanar Waveguide (GCPW)
- CPW with via fence to underlying ground plane
- Enhanced shielding compared to standard CPW
- Reduced crosstalk and EMI
- More vias required, slight impedance adjustment needed

### Substrate Integrated Waveguide (SIW)
- Maximum shielding with via fence on sides
- Highest performance for critical high-frequency applications
- Most complex fabrication
- Minimal radiation losses

## Impedance Control and Matching

### Characteristic Impedance Target
- **Standard value**: 50 Ω (most RF/wireless systems)
- **Video/cable**: 75 Ω (less common in wireless)
- **Tolerance**: ±5% across signal path
- **Impedance matching**: Required at source and load to maximize power transfer
- **Mismatch loss**: Each impedance mismatch causes reflections; minimize via consistent trace geometry

### Return Loss Targets (S11)
- **General RF PCB**: S11 < −20 dB (excellent impedance match)
- **Minimum acceptable**: S11 > −13 dB (rule of thumb threshold)
- **Antenna designs**: S11 < −10 dB acceptable across operating band
- **Width tolerance impact**: ±7 μm width error in 50 Ω line degrades return loss to −10 dB
- **Verify with VNA**: Measure S11 and S21 (insertion loss) across operating band

### Impedance Matching Networks

#### L-Network
- Simplest topology: two components (one series, one shunt)
- Lowest component loss
- Fixed Q-factor determined by source/load impedances
- Single-band designs common
- Fast to design and tune

#### Pi-Network
- Three-component topology: two parallel (shunt) + one series
- Greater control over quality factor (Q) and bandwidth
- Enables higher Q-factors for miniaturized designs
- Better filter characteristics (sharper selectivity)
- Suitable for multiple impedance matching with filtering

#### T-Network
- Mirror topology of pi-network
- Three components: two series + one parallel (shunt)
- Provides Q-factor control similar to pi-network
- Alternative when pi-network doesn't meet specifications

#### Design Rules
- Place matching components as close as possible to source/load to minimize trace inductance
- Keep interconnecting traces short (<1/20 wavelength at operating frequency)
- Verify Q-factor and bandwidth meet design specifications (Q = sqrt(Zs/Zl) for L-networks, adjustable for pi/T)
- Use lumped elements (capacitors, inductors) for low-frequency designs (<3 GHz typical)
- Transition to distributed elements (transmission line stubs) above 3 GHz if needed

## Substrate Materials for RF

### Rogers RO3000 Series (PTFE-based)
- **RO3003**: Dk = 3.00 ± 0.04 (@ 10 GHz), Df = 0.0013 (@ 10 GHz)
- **RO3010**: Dk = 10.2 ± 0.5, high dielectric for compact matching networks
- Ceramic-filled PTFE composites
- Excellent thermal stability
- Stable Dk and Df across wide frequency range
- Cost premium over FR4

### Rogers RO4000 Series (Hydrocarbon/Ceramic)
- **RO4003C**: Dk = 3.55 ± 0.05 (tight tolerance), Df = 0.0027 (@ 10 GHz)
- **RO4350B**: Dk = 3.48, Df = 0.002 (@ 10 GHz), remains < 0.004 up to 77 GHz
- Better CTE (coefficient of thermal expansion) matching to copper
- Improved Z-axis CTE for plated through hole reliability
- More economical than RO3000 series
- Good performance to ~20 GHz

### PTFE (Teflon) Materials
- Dk = 2.1 ± 0.05 (frequency-dependent)
- Df = 0.001 at 10 GHz
- Excellent low-loss, low-dispersion properties
- Higher cost, fragile handling
- Best for broadband, low-loss applications (>20 GHz)

### Taconic RF Laminates
- TLC series: Dk = 3.2-4.0, Df = 0.003-0.006
- TLY series: Dk = 2.17, Df = 0.0019
- Competitive alternative to Rogers
- Stable properties to ~30 GHz
- Cost-effective for high-volume RF production

### Ceramic Substrates
- Dk = 9-12 (high dielectric constant)
- Df = 0.001-0.003 (low loss)
- Excellent thermal conductivity
- Use for high-power PA circuits with thermal requirements
- Very brittle, requires careful handling

### Material Selection Criteria
- **Frequency range**: Choose Dk/Df stable across entire operating band (±5% tolerance on Dk typical)
- **Dielectric loss**: Lower Df/tan delta = lower signal attenuation; prioritize Df < 0.003 for >6 GHz
- **Thermal stability**: Low CTE (ppm/°C) for temperature-stable impedance
- **Cost vs. performance**: Rogers RO4003C offers balance; FR4 only acceptable below 2 GHz
- **Manufacturing**: Specify material in stackup; verify supplier availability

## Ground Plane and Via Fencing

### Ground Plane Strategy
- **Solid planes**: Dedicate at least one complete layer to ground for each RF layer
- **No splits**: Avoid splitting analog and digital ground; maintain single continuous plane
- **Return paths**: Trace displacement currents around interconnects; confine EM fields locally
- **Voltage planes**: Reserve additional planes for power distribution on high-power designs

### Via Fencing for RF Traces
- **Via pitch**: 3 mm (λg/20 at 2.4 GHz) for noticeably cleaner transitions; tighter spacing (1-2 mm) reduces reflection peaks in S11
- **Fence placement**: Run via fence on both sides of sensitive transmission lines (e.g., antenna feed, LNA input)
- **Anti-pad sizing**: Specify via anti-pads to preserve 50 Ω impedance; typically 0.1-0.15 mm larger than via pad
- **Minimum via diameter**: 0.3 mm for RF frequencies; larger diameters (0.4-0.5 mm) reduce parasitic inductance
- **Ground keepout**: Extend 30 mils (0.76 mm) clearance from through-hole SMA connector contact pads to adjacent ground

### Ground Return Path Management
- **Minimize loop area**: Keep signal and return paths tightly coupled and close together
- **Via placement**: Add ground vias at component pads (especially LNA, PA, filter inputs/outputs)
- **Multiple ground vias**: Use 2+ vias per RF component pad for parallel return paths
- **Star point grounding**: Avoid multi-point grounds at low frequencies; use multiple planes at RF

## Antenna Integration and Keepout Zones

### Antenna Placement
- **Distance from edges**: Place antenna trace ≥10 mm from PCB edge to avoid near-field interference with external structures
- **Separation from components**: Antenna feed point ≥5 mm from RF components to minimize coupling
- **Keepout zone**: Create 20 mm × 20 mm clearance around antenna resonant element; no ground traces allowed within this zone
- **Return path**: Antenna center feed point requires solid ground connection via short via fence (1-2 mm pitch)

### Antenna Feed Transition
- **Impedance matching**: Match antenna (typical 10-50 Ω) to 50 Ω system impedance via L or pi-network within 5 mm of feed point
- **Ground clearance**: If antenna feeds via SMA connector, maintain 30 mils clearance from ground plane around connector opening
- **Resonant element**: Isolate antenna element from digital/power plane noise; surround with ground via fence at 1-2 mm pitch

### Return Loss Targets for Antenna
- **Target**: S11 < −10 dB across operating frequency band
- **Acceptable**: S11 < −6 dB minimum for marginal designs
- **Excellent**: S11 < −15 dB for high-efficiency systems
- **Measurement**: Verify with VNA sweep across full operating band; document S11 response

## RF Connector Footprint Design (SMA, U.FL)

### SMA Edge Connector
- **Center pin landing pad**: 0.064" (1.63 mm) diameter, 50 Ω transition
- **Ground pads**: Sized to match center pad impedance; typical pad ring 1-1.5 mm wide
- **Via fence around pin**: 1 mm pitch vias within 1-2 mm of center pin reduces reflection peaks
- **Anti-pad clearance**: 30 mils (0.76 mm) from ground plane around via anti-pads
- **Trace width from pin**: Transition smoothly from connector pad to microstrip/CPW; avoid abrupt width changes
- **Coplanar guard traces**: For CPW designs, include ground traces alongside signal trace (1-2 mm spacing, 2-3 mm width)

### U.FL Connector (Micro Coax)
- **Impedance**: 50 Ω characteristic impedance
- **Via fence**: 1-2 mm pitch vias on both sides of coax trace from U.FL to main RF circuit
- **Trace routing**: Keep U.FL-to-circuit trace short (<5 mm) and straight when possible
- **Ground via**: Place multiple ground vias (2-3) adjacent to coax signal trace
- **Connector mounting**: Secure with solder-down or snap-fit; ensure repeatable impedance if removable

### SMA Right-Angle Connector
- **PCB edge requirement**: Trace edge-mounted at board perimeter
- **Via transition**: Smooth via field under connector pad; use field solver to verify impedance
- **Clearance**: Maintain 30 mils (0.76 mm) from edge connector ground cup to adjacent traces/components

## Component Placement Strategy

### Signal Flow
- **Antenna → Switch → LNA → SAW Filter → Mixer → (TX path: PA → Filter → Switch)**
- Arrange components in this order to maintain RF integrity and minimize cross-coupling

### LNA (Low Noise Amplifier)
- **Placement**: Immediately after antenna switch, before SAW filter
- **Thermal isolation**: ≥3 mm clearance from heat sources (PA, power regulators) to minimize temperature drift
- **Ground vias**: Add 2-3 ground vias under LNA pad for low-impedance ground return
- **Matching network**: Place within 2 mm of LNA input pin; minimize trace length to <1 mm
- **Isolation**: Surround with ground via fence (1-2 mm pitch) to prevent coupling with other circuits

### PA (Power Amplifier)
- **Heat management**: Integrate thermal simulation; reserve copper area for heat dissipation (>100 cm² typical)
- **Matching network**: L0 inductor placed <1 mm from TX RF pin to minimize series parasitic inductance
- **Bias network**: Isolate DC supply traces from RF paths with ferrite beads or RC filters
- **Output filter**: Place SAW or ceramic filter <3 mm from PA output to prevent unwanted harmonics

### Filter (SAW/Ceramic/Lumped)
- **Placement strategy**: Between antenna/duplexer and LNA (RX) or after PA (TX)
- **Island-style layout** (multi-band): Each filter group surrounded by ground via array (1-2 mm pitch) to prevent cross-module interference
- **Via fencing**: 1 mm pitch via fence on all sides of filter to minimize EMI coupling
- **Input/output matching**: Place within 2 mm of filter ports; use short traces with via fencing

### Mixer
- **Placement**: After LNA and SAW filter (RX path)
- **LO/RF/IF isolation**: Keep LO, RF, and IF ports ≥5 mm apart; route with ground via fencing between paths
- **Input filter**: Place SAW filter <3 mm from mixer RF input
- **Output routing**: IF output can use lower-impedance traces if not critical path

### Digital Components
- **Separation**: Keep RF analog section ≥10 mm from digital logic
- **Return path isolation**: Prevent return paths from digital section from overlapping RF section
- **Ground plane partition**: Consider separate ground for digital; ensure single connection point at a star point

## Trace Routing and Geometric Rules

### Trace Width and Spacing
- **Impedance consistency**: Width tolerances ±5% to maintain ±5% impedance tolerance; verify with field solver
- **Parallel traces minimum spacing**: 3W (three times trace width) minimum separation to reduce crosstalk; 5W preferred
- **Adjacent layer crosstalk**: Orthogonal routing in adjacent layers; same layer requires ≥3W spacing
- **Guard traces**: Place grounded traces alongside critical signal lines (1-2 mm away) to provide EM shielding

### Trace Length Constraints
- **Signal delays**: Keep RF traces <1/20 wavelength at operating frequency to minimize signal loss
- **Example calculations**:
  - 2.4 GHz: λ ≈ 125 mm, max trace length ≈ 6 mm
  - 5 GHz: λ ≈ 60 mm, max trace length ≈ 3 mm
  - 10 GHz: λ ≈ 30 mm, max trace length ≈ 1.5 mm

### Trace Bend Rules
- **Bend radius**: Minimum 3× trace width to preserve characteristic impedance; 5× width preferred
- **45° bends**: Use chamfered or curved bends, not sharp right angles; right angles create impedance notches
- **Via placement at bends**: Add ground vias on concave side of bends to suppress reflections

### Via Stitching
- **Via count**: Minimize via transitions; each via adds parasitic inductance and small capacitance
- **Via pair**: When layer transition is necessary, use 2+ vias for parallel return paths
- **Via spacing**: 0.3-0.5 mm center-to-center for low inductance
- **Anti-pad sizing**: Slightly larger (0.1-0.15 mm) than via pad to preserve local impedance

## Skin Effect and Surface Finish

### Skin Effect at High Frequencies
- **Frequency impact**: Skin depth = sqrt(ρ / (π × f × μ)) increases with frequency; most current flows at surface
- **2.4 GHz**: Skin depth ≈ 1.65 μm in copper
- **5 GHz**: Skin depth ≈ 1.13 μm in copper
- **10 GHz**: Skin depth ≈ 0.80 μm in copper
- **Loss increase**: Surface roughness and plating quality dominate losses at frequencies >1 GHz

### Surface Finish Recommendations
- **Electroless nickel gold (ENIG)**: Preferred for RF; smooth plating reduces conductor loss ~5-15% vs. bare copper
- **Immersion gold**: Excellent for fine-pitch components and RF applications
- **Hot air solder leveling (HASL)**: Acceptable for <3 GHz; rougher surface increases losses
- **Avoid**: Carbon-based finishes; excessive oxidation layers

### Copper Weight
- **Standard**: 1 oz/ft² (35 μm) typical
- **Heavy copper**: 2 oz/ft² (70 μm) for lower loss at >5 GHz; reduces conductor resistance ~30%
- **Trade-off**: Heavy copper increases cost and manufacturing complexity

## Frequency-Dependent Material Selection

### Material Choice by Frequency Band
| Band | Primary Material | Alt. Option | Dk Tolerance | Max Df |
|------|------------------|-------------|--------------|---------|
| <2 GHz | FR4 (only) | — | ±10% | 0.020 |
| 2-6 GHz | Rogers RO4003C | FR4 (marginal) | ±5% | 0.0027 |
| 6-20 GHz | Rogers RO4350B, RO3003 | PTFE | ±5% | 0.002 |
| >20 GHz | PTFE, Taconic TLY | Rogers RO4350B | ±3% | 0.002 |

### Dielectric Constant (Dk) Impact
- **Wavelength scaling**: λg = λ / sqrt(Dk); higher Dk compresses wavelengths, reduces trace lengths
- **Impedance scaling**: Z0 = (Z_air) / sqrt(Dk); higher Dk reduces impedance for same trace width
- **Frequency dispersion**: Dk varies with frequency; Rogers materials ±2-4% typical across 1-20 GHz, FR4 varies ±10%+
- **Temperature drift**: Choose materials with low temperature coefficient of Dk (<50 ppm/°C); Rogers < 30 ppm/°C typical

### Loss Tangent (Df/tan δ) Impact
- **Attenuation**: Insertion loss ≈ alpha × length (alpha increases with f and Df)
- **Example**: 50 mm RO4003C trace at 10 GHz ≈ 0.5 dB loss; FR4 ≈ 2-3 dB loss (4-6× higher)
- **Quality factor degradation**: Higher Df reduces matching network Q, broadens bandwidth, reduces selectivity
- **Field solver verification**: Model complete stackup at target frequency; measure S11/S21 vs. theory

### Stackup Design Rules
- **Minimum layer count**: 4 layers for simple RF (RF on layer 1, ground layer 2, power/digital layers 3-4)
- **6-8 layers preferred**: Separate power planes, signal integrity, power distribution
- **Dielectric thickness**: Thin outer dielectrics (0.1-0.15 mm) for high-density BGAs; thicker core (0.2-0.4 mm) for controlled impedance
- **Impedance control**: Use field solver (Agilent ADS, Keysight, ANSYS HFSS) to calculate trace width for 50 Ω target on each layer type

## Power Distribution and Thermal Management

### Power Supply Filtering
- **Ferrite bead + capacitor**: Filter bias supply lines to RF components (especially PA, LNA) to prevent noise injection
- **Placement**: Ferrite bead <5 mm from RF pin; capacitor <1 mm from ferrite
- **Capacitor values**: 0.1 µF (high frequency decoupling) + 1-10 µF (bulk) typical; verify with impedance plots

### Thermal Design
- **PA heat dissipation**: Reserve >100 cm² of copper (multiple layers for high power); via thermal vias (0.3 mm, 1 mm pitch) to back side
- **Temperature measurement**: Integrate temperature sensor near PA; feed back to bias network for compensation
- **Fan cooling (if required)**: Ensure airflow paths; avoid turbulent eddies near RF sections

## Testing and Verification

### Design Verification Checklist
- [ ] Impedance field solver confirms ±5% tolerance across operating frequency band
- [ ] Return loss (S11) ≤ −13 dB (target −20 dB) at all ports (antenna, PA output, LNA input)
- [ ] Insertion loss (S21) <0.5 dB over matching networks at operating frequency
- [ ] Crosstalk measurements (S31, S41) between adjacent traces <−40 dB typical
- [ ] Via inductance <0.1 nH per via pair (simulate with 3D EM solver if critical)
- [ ] Thermal modeling confirms PA junction temperature <150°C at full power

### Production Testing
- Verify S-parameters with VNA (Agilent, HP, Keysight, Rohde & Schwarz)
- Conduct thermal cycling (−40°C to +85°C) to verify CTE matching
- Power sweep testing to verify PA linearity and efficiency
- Radiated emissions testing per FCC Part 15 (USA) or equivalent

## Common Design Pitfalls

- **Impedance mismatch at transitions** — Use smooth transitions and via fencing; avoid abrupt width changes
- **Insufficient ground vias** — Add minimum 2-3 ground vias per RF component pad
- **Parallel traces without spacing** — Maintain ≥3W minimum spacing between coupled traces
- **Right-angle trace bends** — Use 45° or curved bends; right angles reflect signals
- **Long stubs on RF lines** — Minimize dangling traces; cap unused pads with vias to ground
- **Over-aggressive impedance tolerance** — ±5% tolerance is standard; tight ±2% requires expensive field solver verification
- **Inadequate shielding of LNA** — Isolate LNA input with ground via fence to prevent coupling from PA or clocks
- **Poor matching network placement** — Place components <2 mm from RF pin; longer traces add parasitic inductance
- **Insufficient heat dissipation for PA** — Simulate thermal path; verify peak junction temperature with FEA
