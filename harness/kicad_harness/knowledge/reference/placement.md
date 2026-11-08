# PCB Component Placement

## Priority Order
1. Place ICs and critical components first — they anchor the layout
2. Place decoupling caps within 3-5mm of their IC power pins
3. Place timing components (crystals, RC networks) adjacent to their IC
4. Place connectors and mechanical components at board edges
5. Fill remaining space with passives and discretes

## Grouping
- Group components by function — keep related components physically close
- Separate analog, digital, and power sections into distinct board regions
- Place input circuits on one side, output on the other — signal flows left-to-right or top-to-bottom
- Keep high-speed components near center to minimize trace lengths

## Orientation
- Orient all similar passives consistently (same direction) for inspection and rework
- Align IC pin 1 markers consistently (all north or all west)
- Orient polarized components (LEDs, diodes, electrolytics) consistently

## Spacing
- Minimum 2-3mm from board edge for manufacturing handling
- Leave clearance for solder reflow nozzles and rework tools
- Taller components shouldn't shadow shorter ones during reflow
- Balance component density — avoid dense clusters that trap heat and block routing

## Thermal
- Place heat-generating components (regulators, power resistors) away from temperature-sensitive parts
- Use thermal vias under heatsinks to conduct heat to internal planes
- Distribute power dissipation — don't cluster hot components together
