# PCB Layer Stackup

## Layer Count Decision
| Requirement | Layers | Rationale |
|-------------|--------|-----------|
| Simple logic, <10MHz, no BGA | 2 | Cheapest, acceptable for low-speed |
| Mixed-signal, USB, 10-50MHz | **4** | Sweet spot for cost/performance |
| High-speed digital, DDR, <500MHz | 6 | Better impedance control, lower EMI |
| RF, >500MHz, dense BGA | 8+ | Optimized return paths, thermal |

**Upgrade 2→4 when**: signals >10MHz, BGAs >100 pins, routing barely fits, EMI matters (4-layer reduces radiation 15dB vs 2-layer)

## Cost Ratios
- 2-layer: 1x (baseline)
- 4-layer: ~2x
- 6-layer: ~3x
- 8-layer: ~4x

## Standard 4-Layer Stackups

**SIG-GND-GND-SIG** (most common)
- Ground adjacent to both signal layers, excellent return paths
- Power routed as copper pours on signal layers

**SIG-GND-PWR-SIG**
- Separate power plane for high current domains
- Best when dedicated power distribution needed

**SIG-PWR-GND-SIG** (power electronics)
- Dedicated power layer with multiple voltage rails
- Use when board carries >5A in localized areas

**GND-SIG-SIG-GND** (analog shielding)
- External ground shield, Faraday cage effect
- Best for low-noise analog systems

## 6-Layer Stackups
**SIG-GND-PWR-PWR-GND-SIG** — multiple power domains, good signal isolation
**SIG-GND-PWR-GND-GND-SIG** — three ground planes, exceptional noise immunity

## Symmetric vs Asymmetric
- **Always symmetric unless justified** — reduces warping by ~30%
- IPC-4101: symmetric ≤0.75% warping, asymmetric >1.0%
- Symmetric rule: layer N from top mirrors layer N from bottom
- Asymmetric acceptable only for: thermal dissipation on one side, single-sided HDI, mechanical reinforcement, prototype cost

## Impedance Implications
- **50Ω SE**: ~13mil trace, 7mil dielectric spacing (FR-4)
- **100Ω diff**: ~13-15mil total width, 6-8mil pair spacing
- Tighter dielectric spacing → narrower traces → higher density
- Copper pour must maintain ≥2× trace width clearance
- 6-layer allows 0.13-0.18mm (5-7mil) spacing, convenient for matched impedance

## Material Selection
| Material | Dk | Df | Cost | Use Below |
|----------|----|----|------|-----------|
| FR-4 | ~4.5 | 0.02 | 1x | 300MHz |
| Hybrid (Rogers inner + FR-4 outer) | — | — | 2-3x | 2GHz |
| Rogers RO4350B | 3.48 | 0.004 | 5-10x | >5GHz |

## Standard Thicknesses
**Prepreg**: 7628 (0.21mm), 2116 (0.13mm), 3313 (0.11mm), 1080 (0.08mm)
**Core**: 0.15-1.6mm; common 4-layer inner core 0.2mm

**Typical 4-layer (1.57mm total)**:
- Top copper 0.035mm + prepreg 0.21mm + core 0.77mm + prepreg 0.21mm + bottom copper 0.035mm

## Manufacturing
- Contact fabricator early for 6+ layers, ±10% impedance tolerance, or specialty materials
- Standard tolerances: via ±10%, trace width ±10%, dielectric ±10%
- Request stackup drawing from PCB house before manufacturing
- Place test coupons on board for TDR impedance measurement
