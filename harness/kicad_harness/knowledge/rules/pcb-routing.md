# PCB Routing

## Priority Order
1. Critical/high-speed signals (clocks, differential pairs)
2. Power distribution (wide traces, planes)
3. Standard signals
4. Low-priority/non-critical connections

## Trace Geometry
- Use 45-degree bends, never 90-degree — reduces reflections and acid traps
- Curved traces preferred for high-speed signals
- Maintain consistent trace width — impedance changes with width
- Taper gradually when trace width must change

## Spacing & Crosstalk
- **3W rule**: trace center-to-center spacing >= 3x trace width
- Route analog and digital signals on different layers, or perpendicular if same layer
- Use guard traces (grounded) between sensitive parallel signals
- Increase spacing near high-speed signals

## Signal Integrity
- Provide continuous return path — no gaps or splits in ground plane under signal traces
- Place return vias within 5-10mil of signal vias on layer transitions
- Minimize vias on high-speed nets — each adds ~0.5nH inductance + reflection
- Match trace lengths for differential pairs (within 5-10mil)
- Match trace lengths within parallel buses (within 1 inch)
- Route differential pairs together with consistent spacing

## Via Usage
- Minimize via count — each via adds inductance, capacitance, and a reflection point
- Use smallest practical drill (8-10mil for standard boards)
- Add return path vias near every signal via layer transition
- Stagger vias in high-density areas to reduce stress
