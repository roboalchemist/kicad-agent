# PCB Mechanical Design

## Mounting Holes
- **Standard sizes**: M3 (3.2mm), M2.5 (2.9mm), #4-40 (3.2mm)
- **Pad diameter**: hole + 0.8-1.0mm minimum
- **Annular ring**: 0.3-0.5mm minimum (use 0.5mm for production reliability)
- **From board edge**: 5-10mm minimum (3mm absolute minimum)
- **Between holes**: 10mm minimum center-to-center
- **From SMD components**: 10mm minimum (prevents reflow heat damage)
- **Grounding**: 4 vias minimum per mounting hole, 0.5-0.8mm via diameter
- Use teardrop pads for stress relief in high-vibration environments

## Board Outline
- **Minimum dimension**: 10x10mm (smaller = fragile during handling)
- **Aspect ratio**: avoid >10:1 (warping risk during reflow)
- **Inner corners**: 0.2mm radius minimum (stress concentration)
- **Outer corners**: 0.5-1.0mm radius recommended
- **Edge tolerance**: ±0.2-0.3mm typical (±0.1mm precision)
- **Best thickness**: 1.0-1.6mm balances manufacturability, cost, and reliability

## Edge Connectors / Gold Fingers
- **Finger width**: 1.0-2.0mm (1.5mm typical), pitch tolerance ±0.05mm
- **Gold plating**: 0.75-2.5µm (1.25µm preferred for durability)
- **Nickel underplate**: 2.5-5.0µm (prevents gold diffusion into copper)
- **Beveled edge**: 20-45° chamfer at finger entrance
- **Finger start**: 0.5mm minimum from board edge
- **Keepout**: 1.0mm before fingers, 5.0mm component clearance before finger area
- Design for 1000+ insertion cycles minimum

## Keepout Zones
| Area | Keepout |
|------|---------|
| Mechanical connectors | 2-5mm beyond mating surface |
| USB Type-C | 8mm around housing |
| 3.5mm audio jack | 10mm (cable bend radius) |
| Mounting holes | 0.5mm from signal traces |
| Monopole antenna | 30mm perpendicular |
| WiFi/BLE chip antenna | 15-20mm all sides |
| NFC antenna | 10mm from edge |
| LTE/cellular antenna | 50mm from ground planes |

- No metal components (nuts, standoffs, cans) in antenna keepout zones
- Thermal via farm under hot ICs: 0.5-1.0mm spacing, 10-20mm² per watt

## Panelization
- **Panel margin**: 10-15mm around entire panel
- **Inter-board spacing**: 1-3mm (depends on breakaway method)
- **V-score depth**: 50-70% of board thickness, 30-45° angle
- **V-score clearance**: 2.0mm minimum from components
- **Mouse bites**: 0.5-1.0mm tab width, 10-20mm spacing, teardrop shape
- **Tooling holes**: 3 minimum per panel (asymmetric pattern), 2.0-3.2mm diameter, ±0.05mm tolerance
- **Fiducials**: 3 per panel (global), 2 per board (local), 1.0mm copper pad, solder-mask-free

## Flex & Rigid-Flex
- **Minimum bend radius**: 10-15mm dynamic (1.0mm flex), 20mm for production reliability
- **Trace width in bend**: 0.3mm minimum
- **Stiffeners**: under connectors and high-stress components, never in active bend zones
- **Transition zone**: 10-15mm minimum between rigid and flex sections
- Route perpendicular to flex axis, avoid right-angle corners in flex zones
- Avoid ground planes in main flex section (limits bending)

## Component Height & Enclosure
- **PCB to lid**: 5-10mm minimum
- **PCB edge to wall**: 3-5mm minimum
- **Connector mating**: 10-15mm clearance
- **Component spacing**: 5mm minimum between high-power components
- **Thermal vias**: 0.3-0.5mm diameter, 1mm spacing under heat sources
