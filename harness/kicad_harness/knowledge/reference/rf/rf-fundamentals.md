---
scope: personal
---

# RF Fundamentals & Impedance

## Contents

- [Transmission Line Types and Selection](#transmission-line-types-and-selection)
- [Impedance Control and Matching](#impedance-control-and-matching)
- [Substrate Materials for RF](#substrate-materials-for-rf)

## Transmission Line Types and Selection

### Microstrip
- Use for frequencies ≤6 GHz
- Single signal trace over ground plane with dielectric above
- Lower cost, easier fabrication
- More susceptible to EMI and crosstalk
- Loss increases with frequency due to dielectric and conductor losses

### Stripline
- Sandwiched between two ground planes
- Excellent shielding from adjacent planes and EMI
- More complex manufacturing
- Lower radiation and crosstalk compared to microstrip
- Preferred for high-density, sensitive designs

### Coplanar Waveguide (CPW)
- Signal trace with ground traces on same layer
- Balanced shielding and fabrication simplicity
- Better radiation suppression at ≤3 GHz
- Requires careful trace spacing and via fencing
- Bottom layer must be solid ground plane

### Grounded Coplanar Waveguide (GCPW)
- CPW with via fence to underlying ground plane
- Enhanced shielding compared to standard CPW
- Reduced crosstalk and EMI
- More vias required, slight impedance adjustment needed

### Substrate Integrated Waveguide (SIW)
- Maximum shielding with via fence on sides
- Highest performance for critical high-frequency applications
- Most complex fabrication
- Minimal radiation losses

## Impedance Control and Matching

### Characteristic Impedance Target
- **Standard value**: 50 Ω (most RF/wireless systems)
- **Video/cable**: 75 Ω (less common in wireless)
- **Tolerance**: ±5% across signal path
- **Impedance matching**: Required at source and load to maximize power transfer
- **Mismatch loss**: Each impedance mismatch causes reflections; minimize via consistent trace geometry

### Return Loss Targets (S11)
- **General RF PCB**: S11 < −20 dB (excellent impedance match)
- **Minimum acceptable**: S11 > −13 dB (rule of thumb threshold)
- **Antenna designs**: S11 < −10 dB acceptable across operating band
- **Width tolerance impact**: ±7 μm width error in 50 Ω line degrades return loss to −10 dB
- **Verify with VNA**: Measure S11 and S21 (insertion loss) across operating band

### Impedance Matching Networks

#### L-Network
- Simplest topology: two components (one series, one shunt)
- Lowest component loss
- Fixed Q-factor determined by source/load impedances
- Single-band designs common
- Fast to design and tune

#### Pi-Network
- Three-component topology: two parallel (shunt) + one series
- Greater control over quality factor (Q) and bandwidth
- Enables higher Q-factors for miniaturized designs
- Better filter characteristics (sharper selectivity)
- Suitable for multiple impedance matching with filtering

#### T-Network
- Mirror topology of pi-network
- Three components: two series + one parallel (shunt)
- Provides Q-factor control similar to pi-network
- Alternative when pi-network doesn't meet specifications

#### Design Rules
- Place matching components as close as possible to source/load to minimize trace inductance
- Keep interconnecting traces short (<1/20 wavelength at operating frequency)
- Verify Q-factor and bandwidth meet design specifications (Q = sqrt(Zs/Zl) for L-networks, adjustable for pi/T)
- Use lumped elements (capacitors, inductors) for low-frequency designs (<3 GHz typical)
- Transition to distributed elements (transmission line stubs) above 3 GHz if needed

## Substrate Materials for RF

### Rogers RO3000 Series (PTFE-based)
- **RO3003**: Dk = 3.00 ± 0.04 (@ 10 GHz), Df = 0.0013 (@ 10 GHz)
- **RO3010**: Dk = 10.2 ± 0.5, high dielectric for compact matching networks
- Ceramic-filled PTFE composites
- Excellent thermal stability
- Stable Dk and Df across wide frequency range
- Cost premium over FR4

### Rogers RO4000 Series (Hydrocarbon/Ceramic)
- **RO4003C**: Dk = 3.55 ± 0.05 (tight tolerance), Df = 0.0027 (@ 10 GHz)
- **RO4350B**: Dk = 3.48, Df = 0.002 (@ 10 GHz), remains < 0.004 up to 77 GHz
- Better CTE (coefficient of thermal expansion) matching to copper
- Improved Z-axis CTE for plated through hole reliability
- More economical than RO3000 series
- Good performance to ~20 GHz

### PTFE (Teflon) Materials
- Dk = 2.1 ± 0.05 (frequency-dependent)
- Df = 0.001 at 10 GHz
- Excellent low-loss, low-dispersion properties
- Higher cost, fragile handling
- Best for broadband, low-loss applications (>20 GHz)

### Taconic RF Laminates
- TLC series: Dk = 3.2-4.0, Df = 0.003-0.006
- TLY series: Dk = 2.17, Df = 0.0019
- Competitive alternative to Rogers
- Stable properties to ~30 GHz
- Cost-effective for high-volume RF production

### Ceramic Substrates
- Dk = 9-12 (high dielectric constant)
- Df = 0.001-0.003 (low loss)
- Excellent thermal conductivity
- Use for high-power PA circuits with thermal requirements
- Very brittle, requires careful handling

### Material Selection Criteria
- **Frequency range**: Choose Dk/Df stable across entire operating band (±5% tolerance on Dk typical)
- **Dielectric loss**: Lower Df/tan delta = lower signal attenuation; prioritize Df < 0.003 for >6 GHz
- **Thermal stability**: Low CTE (ppm/°C) for temperature-stable impedance
- **Cost vs. performance**: Rogers RO4003C offers balance; FR4 only acceptable below 2 GHz
- **Manufacturing**: Specify material in stackup; verify supplier availability
