# PCB Silkscreen

## What to Include (Priority Order)
1. **Reference designators** (R1, C2, U5) — every component, mandatory
2. **Polarity marks** — cathode band on diodes/LEDs, + on electrolytics, Pin 1 dot on ICs
3. **Pin 1 indicators** — dot/dimple/notch on all multi-pin packages (ICs, connectors, QFN/BGA)
4. **Test point labels** (TP1, TP-VIN) — for diagnostics and troubleshooting
5. **Board ID, part number, revision** — version control and traceability
6. **Component values** — where space allows (10k, 100nF)
7. **Power rail labels** (GND, VCC, 3V3) — on test points or exposed pads

## Text Sizing
| Parameter | Minimum | Recommended |
|-----------|---------|-------------|
| Character height | 0.035" (0.89mm) | 0.050" (1.27mm) |
| Stroke width | 0.006" (0.15mm) | 0.007" (0.18mm) |
| Aspect ratio | — | 1:6 (width:height) |

- Use **simple sans-serif fonts** only (Arial, Helvetica) — decorative fonts distort at small sizes
- If you can't read it in the CAD tool at 1:1, it won't be legible on the board

## Clearance Rules
| From | Minimum Clearance |
|------|-------------------|
| Solder pads | 6mil (0.15mm) |
| Vias/holes | 6mil (0.15mm) |
| Solder mask edge | 4.5mil (0.11mm), 8mil safer |
| Board edge | 0.1" (2.54mm) for important text |

## Placement Rules
- **Never place silkscreen over pads** — ink prevents solder wetting, causes cold joints
- **Never place under component bodies** — invisible after assembly, defeats the purpose
- Place reference designators within 0.030" (0.76mm) of their component
- Use **0 and 90 degree rotation only** — minimize board flipping to read
- All text readable from one or two directions (top, or top + right side)
- Orient all similar components consistently (all diodes cathode same direction)

## Manufacturing Methods
| Method | Min Line Width | Min Font | Cost |
|--------|---------------|----------|------|
| Screen print | 0.15mm (6mil) | 30x30mil | Lowest |
| Inkjet (DLP) | 0.3mm (12mil) | 20x30mil | Medium |
| LPI (photo) | 0.15mm (6mil) | Finest | Highest |

## Color
- **White on dark mask** (green, black) — standard, cheapest, best contrast
- **Black on light mask** (white, cream) — acceptable alternative
- **Yellow** — better in low-light (automotive), slight cost premium, can fade with UV
- Ensure minimum 50% luminance difference between ink and substrate

## When to Skip Silkscreen
- Prototype/lab boards you're assembling yourself
- Fully encapsulated/potted modules
- Extremely high-density BGA boards where clearance rules make it impossible
- Cost-critical high-volume consumer products (saves ~15% board cost)

## Both-Sides Silkscreen
- **Top only** is default and cheapest
- Add bottom silkscreen only if: bottom has components, test points, or connectors
- Both-sides adds ~50% to silkscreen cost
- Prefix bottom designators with "B_" to avoid confusion

## Fiducials (Not Silkscreen, But Related)
- Fiducials must be **bare copper pads**, never silkscreen patterns
- 1-3mm diameter, with 2-3mm clear zone around them (no silkscreen)
- 3 per board, placed asymmetrically for orientation detection
- Used by pick-and-place machines for alignment — separate from silkscreen registration

## Common Mistakes
- Text too small (<0.035") — illegible after printing
- Silkscreen on pads — solder defects
- Missing polarity marks — reversed components, catastrophic failures
- Overlapping designators — assembly confusion
- Inconsistent text direction — slows assembly
- Silkscreen over RF/high-speed traces — potential signal integrity degradation
