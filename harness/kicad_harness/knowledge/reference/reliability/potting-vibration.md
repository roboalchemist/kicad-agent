---
scope: both
---

# Potting & Vibration Resistance

## Contents

- [Potting & Encapsulation Materials](#potting--encapsulation-materials)
- [Potting Thermal Considerations](#potting-thermal-considerations)
- [Vibration Resistance & Mechanical Design](#vibration-resistance--mechanical-design)

## Potting & Encapsulation Materials

### Polyurethane (2-Part)
- **Density**: 1.1–1.2 g/cm³
- **Shore hardness**: 80A–90D (can be formulated for flexibility or rigidity)
- **Thermal conductivity**: 0.20–0.25 W/m·K
- **CTE (coefficient of thermal expansion)**: 60–100 ppm/°C
- **Cure time**: 4–24 hours (depends on formulation)
- **Temperature range**: –40°C to +120°C
- **Best for**: Flexible potting, vibration dampening, cost-effective encapsulation
- **Disadvantages**: Hygroscopic (absorbs moisture), CTE mismatch with PCB/components
- **Thermal management**: Poor thermal conductor, not suitable for high-power applications

### Epoxy (2-Part)
- **Density**: 1.15–1.25 g/cm³
- **Shore hardness**: 80D–90D (rigid)
- **Thermal conductivity**: 0.20–0.30 W/m·K (higher than urethane)
- **CTE**: 40–80 ppm/°C
- **Cure time**: 1–24 hours (depends on catalyst/temperature)
- **Temperature range**: –55°C to +150°C
- **Best for**: Maximum mechanical protection, moisture barrier, high-reliability applications
- **Advantages**: Excellent adhesion, low shrinkage (1–2%), superior moisture resistance
- **Disadvantages**: Brittle, risk of cracking on thermal shock, difficult to rework, requires careful degassing

### Silicone (1-Part or 2-Part)
- **Density**: 1.0–1.1 g/cm³
- **Shore hardness**: 40A–60A (flexible)
- **Thermal conductivity**: 0.15–0.30 W/m·K
- **CTE**: 300–400 ppm/°C (very high, mismatch with PCB)
- **Cure time**: 1–24 hours (typically slow)
- **Temperature range**: –55°C to +200°C
- **Best for**: Flexibility, vibration isolation, extreme temperature stability, optical transparency (clear formulations)
- **Advantages**: Best thermal cycling tolerance (low modulus), easy rework, good gas permeability
- **Disadvantages**: Poor moisture barrier (absorbs water), lowest thermal conductivity, very high CTE mismatch
- **Use case**: Components that must flex or where thermal cycling is extreme

### Acrylic (UV-Cured or Thermal)
- **Density**: 1.18–1.20 g/cm³
- **Thermal conductivity**: 0.18–0.22 W/m·K
- **CTE**: 50–70 ppm/°C
- **Cure time**: Minutes (UV) to hours (thermal)
- **Temperature range**: –20°C to +100°C
- **Best for**: Low-cost encapsulation, fast production, optical clarity
- **Disadvantages**: Low thermal cycling tolerance, moisture absorption, brittle

### Polyester
- **Not recommended** for potting — high shrinkage (8–15%), exothermic cure (risk of delamination, component damage), poor adhesion to PCB

## Potting Thermal Considerations

### Thermal Conductivity Enhancement
- **Base epoxy**: 0.20–0.25 W/m·K
- **Add 50% alumina filler**: +40% improvement → 0.28–0.35 W/m·K
- **Add 60% alumina filler**: +80% improvement → 0.36–0.45 W/m·K
- **Add 70% boron nitride filler**: +100% improvement → 0.40–0.50 W/m·K
- **Limit filler load**: >70% risks poor flow, void formation, reduced electrical properties

### Exothermic Heat Management
- Epoxy and polyurethane release significant heat during cure (50–150°C local temperature rise)
- **Batch potting**: Keep pot volumes <500 mL to prevent runaway exotherm
- **Cure schedule**: Use room-temperature or low-temperature formulations to control exotherm
- **Degassing**: Vacuum degas at <10 kPa for 10–20 minutes to remove air bubbles (traps heat, creates voids)

### Thermal Cycling Issues
- **Silicone**: Best choice for wide thermal cycling (low modulus prevents stress)
- **Epoxy/Urethane**: Monitor for solder joint cracking if >100 cycles between –40°C and +85°C
- **CTE mismatch**: PCB ≈ 16 ppm/°C, Si components ≈ 3 ppm/°C, potting ≈ 60–400 ppm/°C — leads to interfacial stress
- **Mitigation**: Use flexible potting (silicone) or ensure potting has lower CTE than PCB (rare — use underfill instead)

## Vibration Resistance & Mechanical Design

### Natural Frequency Calculation
PCB acts as cantilever beam (for PCB-edge mounting):

```
f_n = (λ_n / 2π) × √(E·I / (ρ·A))
```

Where:
- `f_n`: Natural frequency (Hz)
- `λ_n`: Mode constant (first mode ≈ 1.875)
- `E`: Young's modulus (FR-4 ≈ 17 GPa)
- `I`: Second moment of inertia (mm⁴)
- `ρ`: Density (FR-4 ≈ 1.85 g/cm³)
- `A`: Cross-sectional area (mm²)

**Target natural frequency**: f_n > 2–3× highest vibration frequency in environment
- Example: If environment has 200 Hz vibration, target f_n > 500 Hz

### Mounting & Support
- **Support spacing**: PCB should be supported at <150 mm intervals to avoid sagging and resonance
- **Support type**: Elastomer standoffs (durometer 50–70A) preferable to rigid standoffs (absorbs vibration energy)
- **Corner mounting**: Always mount at 4 corners minimum; >4 points for large boards (>200×250 mm)
- **Avoid unsupported cantilevers**: PCB edges >50 mm unsupported dramatically reduce natural frequency

### Component Placement for Vibration
- **Heavy components** (>10 g, transformers, large inductors, connectors): Place near support points (minimizes moment arm)
- **Tall components** (>20 mm height, electrolytic caps): Low CG placement; avoid cantilever edges
- **BGA packages**: Solder joint stress increases with vibration amplitude — solder ball fatigue life ∝ (f_resonant)^–3 (cubic relationship)
- **Connector stress**: Large connectors create high shear stress on solder joints during vibration; reinforce with mechanical support (cable clamp, strain relief)

### Solder Joint Reliability Under Vibration
- **Fundamental**: Vibration causes micro-crack nucleation in solder joints, leading to open circuits
- **Fatigue life** (Miner's Rule approximation):
  ```
  N_f = K / (ε_shear)^m
  ```
  Where N_f = cycles to failure, ε_shear = shear strain, m ≈ 2 (for SAC solder)

- **Mitigation**:
  - Use wave solder or reflow oven (larger fillets reduce stress concentration)
  - Underfill around high-stress joints (transforms bending into shear, 2–4× improvement)
  - Thermal cycling test first (find weak joints before vibration testing)
  - Avoid BGA in high-vibration environments if solder ball fatigue is critical (prefer wire-bonded or flip-chip with underfill)

### Potting & Vibration
- **Rigid potting (epoxy)**: Excellent vibration damping, but increases solder joint stress (encapsulates component, transfers load to solder instead of PCB)
- **Flexible potting (silicone)**: Better solder joint fatigue resistance, worse mechanical protection
- **Partial potting**: Pot assembly from bottom up, leaving connector area clear — compromises environmental protection
