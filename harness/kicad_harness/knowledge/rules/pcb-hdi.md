---
scope: both
---

# PCB HDI (High Density Interconnect) Design Rules

## Microvia Specifications

### Laser-Drilled Microvias
- **Diameter range**: 0.05-0.20mm (50-200 micrometers) typical
- **Minimum aspect ratio**: 1:1 (depth to diameter); 0.5:1 achievable with CO2 lasers
- **Tolerance**: ±0.025mm (±25 micrometers) on diameter
- **Plating thickness**: 5-15 micrometers copper in blind vias

### Via Hole Sizes
- **Micro blind via**: 0.05-0.10mm (most common: 0.076mm / 3 mils)
- **Small blind via**: 0.10-0.15mm (0.127mm / 5 mils common for cost reduction)
- **Mechanical via**: 0.20-0.30mm+ (standard drilled via)

### Aspect Ratio Rules
- **CO2 laser**: 0.5:1 to 1:1 (common: 1:1)
- **UV laser**: 1:1 to 1.5:1
- **Excimer laser**: 1:1 to 2:1 (preferred for tight specs)

## Blind and Buried Vias

### Blind Via (surface to internal layer, not through-hole)
- Via originates on outer layer, terminates on internal layer
- Smallest drill/laser diameter: 0.05mm
- Cost multiplier vs standard PCB: 1.5-2.0x (layer-dependent)

### Buried Via (internal-to-internal, not reaching surface)
- No connection to outer layers
- Requires selective plating/filling between layer pairs
- Cost multiplier: 2.0-3.0x (highest cost variant)
- Usually limited to 2-3 internal layer pairs max

### Via Stacking Rules
- **Stacked microvias**: Aligned vertically, same XY location across multiple layers
  - Aspect ratio: 0.5:1 to 1:1 (compounded across layers)
  - Max stack height: typically 3-4 layers without special process
  - Pros: Minimal space, direct signal path
  - Cons: Higher cost, requires sequential processing

- **Staggered microvias**: Offset XY positions on adjacent layers
  - Aspect ratio per layer: 1:1 to 1.5:1
  - Can pack more vias into dense areas
  - Pros: Lower cost than stacking, higher yield
  - Cons: Longer current paths, minor impedance effect

## Sequential Build-Up (SBU) Process

### Layer-by-Layer Construction
- Copper layer → Dielectric (laser drilled microvias) → Copper layer → repeat
- Typical SBU sequence: 2-6 sequential build-up cycles
- Each cycle adds: microvia drilling + electroplating + pattern definition

### Process Steps per Build-Up Layer
1. Copper deposition (pattern plating or full plate)
2. Laser drilling of microvias
3. Electroless copper deposition (seeding)
4. Electroplating (5-15 micrometers minimum)
5. Solder mask / dielectric layer
6. Repeat for next layer

### Cost Implications
- Each additional SBU layer: +25-40% to PCB cost
- 2+N+2 HDI (4 outer layers, N cores): ~2.5-3.5x standard PCB
- 3+N+3 HDI: ~3.5-5.0x standard PCB (rare, very expensive)

## HDI Layer Configurations

### 1+N+1 (Single Build-Up, Most Common)
- 1 SBU layer on each side, N standard core layers
- 4-6 total layers typical
- Via structure: surface → SBU → core / core → SBU → surface
- Cost: 1.5-2.0x standard PCB
- Via density: ~200-400 vias/cm²

### 2+N+2 (Dual Build-Up)
- 2 sequential build-up layers on each side
- 6-10 total layers typical
- Via structure: surface → SBU1 → SBU2 → core / core → SBU2 → SBU1 → surface
- Cost: 2.5-3.5x standard PCB
- Via density: ~500-800 vias/cm²
- Common for BGA packages >0.4mm pitch

### 3+N+3 (Triple Build-Up - Rare)
- 3 sequential build-up layers on each side (custom fabs only)
- 8-12 total layers
- Cost: 4.0-6.0x standard PCB
- Via density: ~1000+ vias/cm²

### Any-Layer Via (ALV) - Advanced
- Vias at any layer-to-layer interface (not just sequential build-up)
- Requires laser drilling on every layer
- Cost: 5.0-8.0x standard PCB
- Via density: unlimited
- Rare; only advanced fabs; for cutting-edge packages

## Via-in-Pad (ViP) Technology

### Copper-Filled Vias
- Via drilled, filled with conductive epoxy or copper, plated
- Allows component pads on SBU layers (eliminates annular ring space waste)
- Minimum annular ring: 0mm (via centered in pad)
- Via diameter relative to pad: 0.05-0.15mm via in 0.3mm+ pads

### Solder-Mask-Defined (SMD) Vias
- Solder mask opening defines pad size (not copper)
- Via diameter: 0.1-0.3mm typical
- Allows finer pitch land patterns
- Tented vias recommended (mask over hole) for high reliability

### Plugged and Capped Vias
- Via filled with non-conductive material (epoxy/resin)
- Acts as mechanical support
- Allows solder mask coverage
- Cost: +0.3-0.5x per via

### Via-in-Pad Rules
- **Minimum via diameter**: 0.05mm (laser only)
- **Recommended via diameter**: 0.076-0.127mm for manufacturability
- **Pad size for ViP**: pad diameter ≥ via diameter + 0.1mm
- **Via-to-via spacing** (ViP): 0.2mm minimum center-to-center
- **Copper fill**: 0.5-5 micrometers over via (smooth surface)

## Trace Width and Spacing for HDI

### Standard HDI (SBU Layers)
- **Trace width (outer layers)**: 2-3 mils (0.05-0.075mm)
- **Trace width (inner SBU)**: 1-2 mils (0.025-0.05mm) - high cost
- **Minimum spacing (outer)**: 3 mils (0.075mm)
- **Minimum spacing (inner)**: 2-3 mils (0.05-0.075mm)

### Fine-Pitch HDI (Advanced Fabs)
- **Trace width**: 0.75-1.5 mils (0.019-0.038mm)
- **Spacing**: 0.75-1.5 mils (0.019-0.038mm)
- **Notes**: Requires advanced plating and imaging; cost multiplier +1.5-2.0x

### Via Clearance from Traces
- **Minimum via-to-trace clearance**: 0.1mm (4 mils) for reliability
- **Via-to-trace spacing (blind via to adjacent trace)**: 0.1-0.15mm
- **Via-to-pad spacing**: Via pad edge to trace = 0.1mm minimum

## Cost Implications

### HDI vs Standard PCB Cost Multipliers
| Configuration | Layers | Cost Multiplier | Notes |
|---------------|--------|-----------------|-------|
| Standard (through-hole) | 2-4 | 1.0x | Baseline |
| Standard | 6-8 | 1.2x | Minor premium for layers |
| 1+N+1 HDI | 4-6 | 1.5-2.0x | Single build-up each side |
| 1+N+1 HDI (fine pitch) | 4-6 | 2.0-2.5x | 2/2mil traces & spacing |
| 2+N+2 HDI | 6-10 | 2.5-3.5x | Dual build-up each side |
| 2+N+2 HDI (fine pitch) | 6-10 | 3.5-4.5x | Advanced process |
| 3+N+3 HDI | 8-12 | 4.0-6.0x | Triple build-up (custom) |
| Any-Layer Via | 8-12 | 5.0-8.0x | Maximum flexibility (rare) |

### Cost Drivers
- **Sequential drilling/plating cycles**: Each SBU layer adds 20-30% cost
- **Microvia diameter**: Smaller diameter vias = longer drilling time = higher cost
- **Trace/spacing precision**: 2/2mil costs 40-60% more than 3/3mil
- **Via-in-pad processing**: Copper fill/plating adds 15-25% per via
- **Volume**: Low volumes (1-100 units) → 50-100% premium; high volumes improve economics

### Volume Break Points
- **1-10 units**: 1+N+1 or 2+N+2 cost ~3-4x standard (premium for NRE/setup)
- **100-500 units**: Cost normalizes to 1.5-2.0x (1+N+1), 2.5-3.0x (2+N+2)
- **1000+ units**: Economies of scale; cost approaches low end of range

## When HDI is Justified vs Standard PCB

### Use HDI When:
- **Package size < 0.5mm pitch** - BGA, fine-pitch QFP (0.4-0.5mm), 0402/0201 components
- **Via density > 400 vias/cm²** - Fan-out requirements exceed routing capacity with standard vias
- **Trace count prevents routing** - 6+ layers standard PCB cannot route; 4-layer HDI solves
- **Impedance control required** - Blind vias reduce inductance vs through-hole
- **Space/weight critical** - Thinner layer stackup (1+N+1 vs 8-layer)
- **Cost justified by volume** - >500 units; NRE amortized
- **System density > 0.5 W/cm²** - Thermal vias (buried) improve cooling

### Use Standard PCB When:
- **Pitch > 0.65mm** - Standard vias (0.3-0.4mm) sufficient
- **Via density < 200 vias/cm²** - Ample routing room with standard 0.3mm vias
- **Design fits 4-6 layers standard** - No density pressure
- **Low volume (< 100 units)** - HDI NRE not justified
- **Cost-sensitive** - Every 0.5x matters (consumer electronics)
- **Manufacturing partners limited** - Standard PCB available everywhere; HDI fab scarce
- **Reliability margin needed** - Standard vias have longer track record

### Typical Threshold
- **Breakeven**: ~200-300 units where HDI NRE + per-unit cost < standard PCB at high volume
- **Recommended minimum**: 500 units for 1+N+1 HDI to justify design/NRE cost

## HDI-Specific DFM (Design for Manufacturing) Rules

### Microvia DFM
- **Via-to-via clearance**: Minimum 0.1mm (4 mils) center-to-center for stacked vias
- **Via-to-pad clearance**: Minimum 0.1mm from via annular ring to adjacent pad/trace
- **Stacked via alignment**: X/Y tolerance ±0.05mm (laser registration typically ±0.03mm)
- **Aspect ratio**: Design max 1:1 (fab process flow determines achievable ratio)
- **Via barrel plating**: Minimum 5 micrometers; typical 10-15 micrometers

### Layer Stack-Up DFM
- **Core thickness**: 100-250 micrometers (standard FR-4 cores); specify with fab
- **Prepreg thickness**: 90-150 micrometers per layer (cumulative affects lamination)
- **Total thickness tolerance**: ±0.1mm on final board (HDI stacks vary more than standard)
- **Dielectric constant**: Specify if impedance control required (FR-4 ~4.3, check fab specs)
- **Copper weight**: 0.5oz (17 micrometers) typical for SBU layers; 1oz inner cores

### Via-in-Pad DFM
- **Pad size minimum**: Copper pad ≥ via diameter + 0.2mm for reliable fill/plating
- **Tenting requirement**: Specify "plugged and tented" (via capped, mask over hole) for reliability
- **Fill material**: Specify "copper-filled" vs "non-conductive fill" (cost difference 2-3x)
- **Copper plating over fill**: Minimum 1 micrometer; typical 2-5 micrometers
- **Via-in-pad density**: Maximum 1 per component pad to avoid thermal stress

### Buried Via DFM
- **Burial layer limit**: Bury no more than 2 layer pairs (e.g., layer 4→5, layer 5→6)
- **Buried via location**: Keep 0.3mm+ away from component pads (no thermal via impact)
- **Copper weight before burying**: 1oz minimum on buried layer for strength
- **Via-to-via spacing** (buried): 0.15mm minimum (buried vias harder to plate uniformly)

### Solder Mask & Silkscreen DFM
- **Solder mask over via (tenting)**: Recommended for micro-vias to prevent solder wicking
- **Solder mask opening over pad**: Minimum 0.1mm larger than pad all sides
- **Silkscreen clearance**: 0.3mm minimum from vias and fine traces (registration tolerance)
- **Via tenting**: Specify "tented with 0.05mm opening" if test point access needed

### Trace Routing DFM
- **Via-to-trace spacing**: 0.1mm (4 mils) minimum; 0.15mm recommended for reliability
- **Trace-to-trace spacing**: 3 mils (0.075mm) outer layers; 2 mils (0.05mm) inner SBU
- **Trace width minimum**: 2 mils (0.05mm) outer; 1 mil (0.025mm) inner (high cost)
- **Trace-to-via land spacing**: 0.1mm from trace edge to via annular ring outer edge
- **Return path continuity**: Via stitching required in analog/RF designs; 0.5-1.0mm spacing

### Test Point & Probe DFM
- **Test pad size**: Minimum 0.5mm diameter (0.6-0.8mm preferred for mechanical reliability)
- **Test point clearance**: 0.5mm from adjacent component pads
- **Micro test points** (optional): 0.3mm diameter with 1.5mm spacing; higher cost
- **Via-as-test point**: Avoid; use dedicated pads (vias collapse under probe stress)

### Component Placement DFM
- **Component-to-via spacing**: 0.3mm minimum (laser drill precision tolerance)
- **BGA pad-to-via clearance**: Via annular ring edge ≥ 0.1mm from BGA solder ball
- **Via-in-pad under BGA**: Specify center-of-pad only; avoid corner vias (stress/cracking)
- **Thermal vias under large ICs**: 0.2-0.5mm diameter, 1.0mm spacing, multiple layers

### Documentation Requirements for Fab
- **Microvia plating depth**: Specify "plate to" depth (e.g., "plate to layer 3")
- **Blind via definition**: Explicitly state "surface to layer N" or "layer N to layer M"
- **Via fill type**: "Copper-filled + plated" vs "plugged non-conductive"
- **Stacked via permission**: Specify "stacked microvias allowed" or "staggered only"
- **Annular ring**: Specify minimum (typically 0.05mm for microvias; 0.15mm standard vias)
- **Solder mask type**: Specify "liquid photo-imageable" (LPI) for fine definition

### File Submission & Communication
- **Gerber accuracy**: Include ".GBR" files; ensure coordinates in micrometers (or specify units)
- **Fabrication notes**: Text file with layer stack-up, via specs, copper weight per layer
- **Design rule summary**: Provide summary of minimum trace/spacing, microvia diameter/aspect ratio
- **Approval samples**: Request 1-2 prototype boards before production run
- **Process deviation log**: Ask fab to report any exceptions (missed via fills, plating voids, etc.)

---

## Quick Reference Summary

| Parameter | Standard PCB | 1+N+1 HDI | 2+N+2 HDI |
|-----------|--------------|-----------|-----------|
| Via diameter | 0.3-0.4mm | 0.076mm (microvias) | 0.05-0.10mm |
| Trace/spacing | 5/5 mil typical | 3/3 mil; 2/2 mil advanced | 2/2 mil; 1/1 mil cutting-edge |
| Via density | ~100 vias/cm² | ~300 vias/cm² | ~700 vias/cm² |
| Layer count typical | 4-8 | 4-6 | 6-10 |
| Cost multiplier | 1.0x | 1.5-2.0x | 2.5-3.5x |
| Breakeven volume | >500 units | >500 units | >1000 units |
| Development time | 2-4 weeks | 4-8 weeks | 8-12 weeks |
| Design complexity | Low-medium | Medium-high | High |
