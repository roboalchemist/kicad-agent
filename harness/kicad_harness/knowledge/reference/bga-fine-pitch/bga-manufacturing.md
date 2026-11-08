---
scope: both
---

# Fine-Pitch Manufacturing & Inspection

## Table of Contents

- [Fine-Pitch QFN/QFP Layout](#fine-pitch-qfnqfp-layout)
- [Thermal Pad Design (QFN/BGA)](#thermal-pad-design-qfnbga)
- [Solder Mask and Surface Finish](#solder-mask-and-surface-finish)
- [X-Ray Inspection Considerations](#x-ray-inspection-considerations)
- [BGA Rework Clearance](#bga-rework-clearance)
- [Via Placement (General)](#via-placement-general)
- [Layer Stackup for BGA](#layer-stackup-for-bga)
- [Manufacturing Tolerances](#manufacturing-tolerances)
- [High-Speed Design Notes](#high-speed-design-notes)
- [Quick Reference: When to Use What](#quick-reference-when-to-use-what)

## Fine-Pitch QFN/QFP Layout

### Pad Pitch and Trace Rules
- **Pitch ≤0.5 mm**: Requires fine-line PCB (0.075-0.10 mm min traces)
- **Trace width**: 3-4 mils (0.076-0.10 mm) for ≤0.5 mm pitch
- **Trace spacing**: 3-4 mils (0.076-0.10 mm) minimum
- **Via size**: 0.20-0.30 mm diameter (8-12 mils)
- **Via spacing**: 0.15-0.20 mm minimum

### QFN Thermal Pad
- **Stencil aperture**: 50-80% of exposed thermal pad area
- **Solder paste**: Staggered or grid pattern, not continuous coverage
- **Stencil thickness**: 0.10-0.127 mm
- **Solder volume goal**: Around 80-90% of aperture volume
- **Void requirement (IPC-7093C)**: <25% voids under thermal pad
- **Thermal relief arms**: 0.10-0.30 mm width, 0.15-0.50 mm air gap

### Escape from QFN
- **Thermal pad vias**: Place vias on thermal pad grid (0.50-0.75 mm spacing)
- **Via size**: 0.20-0.25 mm diameter
- **Via connection**: Solder mask over via (vias don't need fill/plate on QFN pads)
- **Trace routing**: From thermal pad vias to power/ground planes

### Pin Pad Solderability
- **Solder mask webbing**: 0.05-0.10 mm clearance from pad edge
- **Land diameter**: Pad diameter = 0.70 × ball diameter (for BGA footprints)
- **Fine-pitch QFP**: Similar land sizing rules; maintain ≥0.10 mm pad-to-pad clearance

## Thermal Pad Design (QFN/BGA)

### Thermal Via Strategy
- **Via diameter**: 0.20-0.25 mm for QFN
- **Via pitch**: 0.50-0.75 mm grid spacing under thermal pad
- **Number of vias**: Typically 4-9 vias depending on pad size
- **Copper percentage under pad**: 30-50% copper coverage (rest is solder paste)
- **Thermal isolation**: No thermal relief for power return thermal pads (full plane contact)
- **Thermal relief**: Use for signal thermal pads with lower current (0.10-0.30 mm arms)

### Heat Dissipation Path
- **Solder joint as thermal conductor**: Solder paste volume (60-80%) dissipates ~60% of heat
- **Copper connection**: Via columns and plane contact carry remaining heat
- **Plane stackup**: Vias should connect to 2+ copper planes minimum

## Solder Mask and Surface Finish

### Fine-Pitch Requirements
- **Solder mask thickness**: 12-18 µm (thinner improves paste release)
- **Solder mask type**: Liquid photoimageable (LPI) preferred for accuracy
- **Mask-defined pads (SMD)**: Mandatory for 0.5 mm pitch and finer
- **Mask clearance**: 0.05-0.075 mm for ≤0.5 mm pitch (tighter for fine-pitch)
- **Surface finish**: HASL, ENIG, or IMMERSION Ag preferred (HASL for cost, ENIG for reliability)
- **Finish thickness**: ENIG 0.05-0.10 µm Au, 3-6 µm Ni minimum

## X-Ray Inspection Considerations

### Void Detection
- **Standard requirement**: AXI (Automated X-ray Inspection) for all BGA and QFN assemblies
- **Void threshold (IPC-7093C)**: <25% void area under thermal pads acceptable
- **Critical acceptance**: <10% for high-reliability applications
- **Void location**: Voids at pad edges worse than center; edge voids reduce joint strength

### Inspection Points
- **BGA solder joints**: Inspect all balls for voids, solder bridges, cracks
- **QFN thermal pad**: Check for uniform solder coverage, voids, delamination
- **Solder bridges**: Between adjacent pads (fine-pitch risk)
- **Tombstoning**: Component lift-off during reflow (mainly QFP risk)

### Cross-Sectional Analysis
- **For critical applications**: Sample cross-section of soldered BGA/QFN
- **Sample size**: 1-3 samples per production batch
- **Void measurement**: Quantify void percentage, location, and solder fillet quality
- **Verification**: Ensures paste volume, reflow profile, and fill quality are correct

## BGA Rework Clearance

### Design for Rework
- **Clearance zone**: Minimum 1.0 mm from BGA edge to adjacent components
- **Heating area**: Rework equipment needs space for heat source access
- **Trace routing**: Keep critical traces away from immediate BGA perimeter
- **Via escape**: Route vias outside 0.5 mm perimeter for clean pad access

### Thermal Relief for Rework
- **Thermally relieved pads**: Easier to remove; allows localized heating
- **Solid plane connection**: Harder to rework but better thermal/electrical performance
- **Trade-off**: Use thermal relief for non-critical nodes or heat sinks that may need replacement

### Rework Temperature Profile
- **Preheating**: 60-90 seconds at 150-200 °C
- **Ramp rate**: 2-3 °C/s
- **Reflow**: 245-260 °C peak for 20-30 seconds
- **Cooling**: 2-4 °C/s to avoid thermal shock
- **Multiple rework cycles**: Limit to 3-5 for production (degradation of solder paste, copper oxidation)

## Via Placement (General)

### Trace-to-Via Spacing
- **Via to trace (same net)**: 0.10 mm minimum clearance
- **Via to trace (different net)**: 0.15-0.20 mm minimum clearance
- **Via to via (same net)**: 0.10 mm minimum edge-to-edge
- **Via to via (different net)**: 0.20 mm minimum edge-to-edge

### Via-to-Pad Spacing
- **Dog-bone pad-to-via**: 0.15-0.20 mm minimum gap
- **Via centering**: Center via in escape channel between pads
- **Via size**: 0.25-0.30 mm diameter for standard; 0.15-0.20 mm for microvia

## Layer Stackup for BGA

### Minimum Layers
- **2-layer**: Not recommended for BGA >100 pins
- **4-layer**: Minimum for BGA; L1 (signals), L2 (power), L3 (ground), L4 (signals)
- **6+ layer**: Preferred for high pin-count BGA and fine-pitch, allows layer count for escape routing

### Via Technology
- **Through vias**: Standard for large pitch (≥0.8 mm)
- **Microvias**: Required for ≤0.5 mm pitch; 0.10-0.20 mm diameter
- **Stacked microvias**: 2-3 levels for 0.4 mm pitch and finer
- **Via aspect ratio**: ≤10:1 (via depth to diameter) for reliable plating

## Manufacturing Tolerances

### Pad Registration
- **IPC Class 1/2**: ±0.10 mm pad placement tolerance
- **IPC Class 3**: ±0.075 mm (high-reliability assemblies)
- **Fine-pitch (≤0.5 mm)**: Use Class 3 or tighter

### Solder Mask Alignment
- **Standard**: ±0.10 mm alignment tolerance
- **Fine-pitch requirement**: ±0.075 mm or better

### Stencil Accuracy
- **Laser-cut stencil**: ±0.025 mm aperture tolerance
- **Electroformed stencil**: ±0.015 mm tolerance (preferred for fine-pitch production)

## High-Speed Design Notes

### Impedance Control
- **Differential pairs**: 90-110 Ω for PCIe/LVDS
- **Single-ended signals**: 50 Ω for high-speed clocks
- **Via-in-pad benefit**: Eliminates dog-bone trace, shortens path ~0.3 mm
- **Signal integrity**: Shorter traces = lower parasitic inductance and capacitance

### Ground Plane
- **Continuous ground plane**: Required under all high-speed traces
- **Via stitching**: Stitching vias around BGA perimeter (0.5-1.0 mm spacing) for EMI containment
- **Return path**: Low-impedance return path via ground plane directly under traces

---

## Quick Reference: When to Use What

| Pitch | Strategy | Via Type | Traces Width | Critical Notes |
|-------|----------|----------|--------------|-----------------|
| ≥1.0 mm | Dog-bone | Through | 5-10 mils | Standard routing; thermal relief optional |
| 0.8 mm | Dog-bone | Through | 4-5 mils | Tight but feasible; consider VIP for inner rows |
| 0.65 mm | Dog-bone/VIP mix | Micro | 3-4 mils | VIP for inner balls; tight margins |
| 0.5 mm | Via-in-pad required | Micro | 3-4 mils | Stencil -20%; SMD pads; ≤25 µm fill tolerance |
| 0.4 mm | Stacked microvias | Micro-micro | 2-3 mils | Mandatory multi-level VIP; strict process control |
| ≤0.3 mm | Advanced VIP tech | Stacked micro | 2-3 mils | Research customer capability before design |

---

## References
- Altium Designer: [Which BGA Pad and Fanout Strategy](https://resources.altium.com/p/which-bga-pad-and-fanout-strategy-right-your-pcb)
- Altium Designer: [PCIe BGA Fanouts and Escape Routing](https://resources.altium.com/p/pcie-bga-fanouts-and-escape-routing)
- NWE LLC: [BGA Routing Guide](https://www.nwengineeringllc.com/article/bga-routing-guide-in-your-pcb-layout-and-stackup.php)
- PCBSync: [Via in Pad Explained](https://pcbsync.com/via-in-pad/)
- AllPCB: [Escape Routing for High-Density BGA](https://www.allpcb.com/blog/pcb-assembly/escape-routing-techniques-for-high-density-bga-packages.html)
- Wellerpcb: [0.4mm Fine-Pitch BGA Design Guide](https://wellerpcb.com/fine-pitch-bga-pcb-design/)
- Microchip: [AN10778 NXP MCUs in BGA Packages](https://www.nxp.com/docs/en/application-note/AN10778.pdf)
- IPC-A-610 Standard (BGA and fine-pitch component acceptance criteria)
- IPC-7093C Standard (Attachment and Mounting of Electronic Assemblies)
