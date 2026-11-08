# PCB High-Speed Design

## Differential Pairs
- **Gap**: 2-5mil typical; maintain consistent spacing throughout entire route
- **Impedance**: 90Ω (USB 2.0), 100Ω (USB 3.x, PCIe, HDMI, Ethernet)
- **Length matching**: route meanders on the longer trace, not both
- **Minimum bend radius**: 2x trace width
- **Plane clearance**: 10mil minimum from split planes/gaps

## Controlled Impedance
- **50Ω single-ended**: 5-8mil trace on standard FR-4 (varies by stackup)
- **Reference planes**: ground plane ALWAYS closer to signal than power plane
- Use Polar SI9000 or equivalent with actual stackup parameters
- Maintain width within ±15% across entire signal path
- Approximate formula: Z = (87 × H) / (W + 1.41 × T) × log(5.98 × H / (W + T))

## Termination Strategies
| Type | When | Value | Placement |
|------|------|-------|-----------|
| Series (source) | Short traces, low-Z drivers | Z0 - Zdriver (22-68Ω) | Within 1" of driver |
| Parallel (end) | Long traces, reflections | Z0 (50Ω SE, 90Ω diff) | Within 0.5" of receiver |
| AC (capacitive) | Reduce DC power draw | RC cutoff well below signal freq | At receiver end |
| None | <6", >500ps rise time | — | — |

- LVDS: integrated 100Ω termination in receiver, usually no external needed
- If rise time <500ps, use controlled impedance and termination

## Via Stubs & Back-Drilling
- Stub resonance: fres ≈ 0.35 / stub_length_ns
- **Rule**: keep stubs <15mil for >1GHz signals
- Back-drill: mandatory for PCIe, DDR4/5, USB 3.x
- Blind/buried vias: 2-4x cost, use when back-drilling isn't viable

## Return Path Management
- Continuous ground plane — never split under signal traces without explicit return path
- Place return vias <100mil from signal vias
- Via fence rows to contain return currents (200-300mil spacing)
- Ground plane within 5-10mil of signal layer

## Clock Routing
- Star distribution (one driver to multiple loads) — avoid daisy-chain
- Differential clocks: ±10mil intra-pair for low phase noise
- 500mil minimum separation from switching supplies
- Guard traces for ultra-low jitter (<20ps)

## Common Interface Requirements

| Interface | Impedance | Intra-pair Skew | Back-drill | Key Notes |
|-----------|-----------|-----------------|------------|-----------|
| USB 2.0 | 90Ω diff | ±25mil | No | 480 Mbps, AC-cap optional |
| USB 3.x | 100Ω diff | ±10mil | Yes | 5-20 Gbps, AC-cap 100nF both ends |
| HDMI | 100Ω diff | ±25mil | No | TMDS + control, AC-cap 100nF |
| GigE | 100Ω diff | ±50mil | Rec. | Transformer coupled |
| DDR3 | 50Ω SE | ±50mil addr | No | Stubs <10mil |
| DDR4 | 50Ω SE | ±5mil DQ | Yes | Stubs <5mil |
| DDR5 | 50Ω SE | ±5mil DQ | Yes | Tan δ <0.012, PAM3 on CA |
| PCIe Gen3 | 100Ω diff | ±50mil lane | Yes | 8 Gbps/lane |
| PCIe Gen4+ | 100Ω diff | ±25mil lane | Yes | 16-32 Gbps/lane |

## Crosstalk Management
- Adjacent signals same layer: spacing ≥ 3× trace width
- Orthogonal routing on adjacent layers reduces coupling
- Guard traces between high-speed signals (~50-70% crosstalk reduction)
- Keep high-speed signals >500mil from switching supplies

## Power Integrity
- Decoupling within 500mil of IC power pins
- Mix 0.1µF (HF), 1µF (mid), 10µF (LF) — roughly 10:1 ratio
- 2-4 vias per supply pin on large ICs
- Continuous ground planes on layers adjacent to all signal layers

## High-Speed Material
- FR-4: Dk ~4.5, Df 0.02 — suitable <300MHz
- High-speed laminate (Tan δ <0.015): required for 10+ Gbps
- Rogers RO4350B: Dk 3.48, Df 0.004 — for >300MHz/RF
- Skin effect: ~0.002 dB/inch at 5 Gbps, ~0.004 dB/inch at 10 Gbps
