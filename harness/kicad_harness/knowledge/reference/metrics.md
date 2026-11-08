# PCB Layout Quality Metrics

## Primary (optimize these)
1. **Wirelength** — HPWL or MST; directly impacts delay, crosstalk, EMI, cost
2. **Routability/Congestion** — uniform distribution preferred; hotspots cause routing failures
3. **Routing directness** — ratio of ideal (MST) to actual routed length
4. **Via count** — fewer is better for signal integrity and reliability

## Secondary (monitor these)
5. **Compactness** — component bbox area / board area
6. **Layer balance** — even track distribution across layers
7. **Edge clearance** — minimum distance from components/vias to board edge
8. **Crosstalk risk** — parallel segment pairs on same layer, different nets, close spacing
9. **Power pin proximity** — average distance from power/ground pads to nearest via
10. **Congestion hotspot ratio** — max vs average segments per grid cell
11. **Annular ring adequacy** — all vias meet minimum ring size

## Informational (track for awareness)
12. **Plane coverage** — copper pour/zone area per layer
13. **Net bbox aspect ratio** — elongated nets may indicate poor placement
14. **Segment length distribution** — identifies outlier-long segments
15. **Manhattan directness** — rectilinear routing efficiency
