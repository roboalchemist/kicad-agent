# PCB Copper Pours (Zones/Fills)

## When to Use Pours
- **Ground return paths** — low-impedance return for signal currents, reduces loop area
- **Power distribution** — spreads voltage evenly, minimizes IR drop
- **EMI shielding** — ground pour acts as Faraday cage, effective below ~1 GHz
- **Thermal spreading** — copper's 380 W/m·K conducts heat away from hot components
- **Copper balance** — fill sparse areas to prevent board warping during reflow
- **Do NOT pour "just because"** — every pour needs a purpose (return path, power, shielding, or balance)

## When NOT to Use Pours
- Simple low-speed circuits where a ground trace suffices
- Compact layouts where pours create unavoidable dead copper islands
- High-frequency RF (>1 GHz) without proper via stitching — cavity resonances dominate
- When pour would bridge analog/digital ground isolation boundaries
- 2-layer boards with complex routing — minimal benefit, high risk of floating islands

## The 2-Layer Pour Debate
- 2-layer ground pours provide ~15 dB less EMI reduction than 4-layer dedicated planes
- They CAN help if: all copper is stitched (<20mm spacing), no dead islands, low-speed design
- They HURT if: creates circuitous return paths, floating islands, or thermal imbalance
- **Recommendation**: for anything >10 MHz, upgrade to 4 layers rather than relying on 2-layer pours

## Clearance Rules
| Context | Clearance |
|---------|-----------|
| Low-voltage (<50V) | 8mil (0.2mm) minimum |
| High-voltage (>50V) | 20-30mil (0.5-0.75mm) |
| High-speed signals | 20mil (0.5mm) from pour edge |
| Board edge | 10-20mil (0.25-0.5mm) |

## Solid vs Hatched Fill
- **Solid** — better electrically (lower impedance, better shielding); preferred for most designs
- **Hatched (50% density)** — reduces thermal stress and warping; better for flex PCBs and RF
- **Default to solid** unless thermal/mechanical concerns dictate otherwise

## Thermal Relief
- **Always use thermal relief** on component pads connecting to pours
- 4 spokes, 8-10mil spoke width, 10mil clearance gap
- Without relief: copper sinks heat, pad doesn't reach 230°C, cold solder joints result
- Well-designed relief reduces solder defects by ~30%
- Not needed on vias (they should connect solidly to planes)

## Stitching Vias
- Connect pour to ground plane on other layers — a pour without stitching is useless
- **Low-frequency**: 10-15mm spacing
- **High-speed digital (>1 GHz)**: 5-10mm spacing
- **RF**: spacing <= wavelength/10 (e.g., 12mm at 2.4 GHz, 3mm at 5 GHz)
- Place return vias within 0.5-1mm of signal vias at layer transitions

## Copper Balance
- Target **50-70% copper density** per layer
- Keep layer-to-layer difference within **10-15%**
- Add copper thieving (non-functional fill) in sparse areas if needed
- Imbalanced copper causes: warping, uneven etching, reflow defects

## Dead Copper / Floating Islands
- **Never leave floating copper** — it acts as an antenna, couples and re-radiates noise
- Remove all isolated islands smaller than 0.2mm (manufacturing limit — they break off)
- Run DRC to detect unconnected copper fragments
- A single via to ground near an island's center is enough to neutralize it

## Pour Priority (Overlapping Zones)
- Higher-priority pours are filled first and respected by lower-priority pours
- Use priority to create local power islands within a global ground pour
- Set custom clearance on high-priority pours for fine-grained control
