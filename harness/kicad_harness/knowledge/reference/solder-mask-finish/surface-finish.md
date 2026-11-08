---
scope: personal
---

# Surface Finish & Stencil Design

## Table of Contents

- [Surface Finish Selection & Shelf Life](#surface-finish-selection--shelf-life)
- [Surface Finish Selection Decision Matrix](#surface-finish-selection-decision-matrix)
- [Solder Paste Stencil Aperture Design](#solder-paste-stencil-aperture-design)
- [Paste Release Considerations](#paste-release-considerations)
- [When to Use Each Surface Finish](#when-to-use-each-surface-finish)
- [Common Mistakes to Avoid](#common-mistakes-to-avoid)

## Surface Finish Selection & Shelf Life

### HASL (Hot Air Solder Leveling) — Lead-Free Recommended

- **Shelf life**: 12 months in sealed bag with desiccant
- **Flatness**: ± 3 mils (worst), suitable for components ≥ 0.5 mm pitch
- **Cost**: lowest
- **Lead-free HASL**: slightly better wetting than leaded
- **Defects**: solder balling, bridges (thermal cycling issues)
- **Best for**: through-hole, coarse-pitch SMT, prototype boards

### ENIG (Electroless Nickel Immersion Gold)

- **Shelf life**: 12+ months (longest), stable in humid environments
- **Flatness**: ±1 mil (excellent), suitable for fine-pitch (≤ 0.5 mm BGA)
- **Cost**: 2-3x HASL
- **Composition**: 2-5 µm Ni + 0.03-0.2 µm Au
- **Advantages**: excellent corrosion resistance, fine-pitch capable, no solder balling
- **Disadvantages**: black pad risk (excess Ni), wire bond reliability
- **Best for**: fine-pitch BGA, high-reliability, long shelf life requirement, gold wire bonding

### OSP (Organic Solderability Preservative)

- **Shelf life**: 12 months (sensitive to humidity, dark storage required)
- **Flatness**: excellent (similar to ENIG)
- **Cost**: low (20-30% more than HASL)
- **Composition**: organic coating prevents copper oxidation
- **Advantages**: ultra-flat, excellent wettability, fine-pitch capable
- **Disadvantages**: thin coat (easily abraded), sensitive to humidity/contamination, requires controlled handling
- **Best for**: fine-pitch designs with quick assembly, flatness-critical applications
- **Storage**: sealed, desiccant, avoid dark storage beyond 6 months

### Immersion Silver (Ag)

- **Shelf life**: 12 months (sensitive to tarnishing)
- **Flatness**: excellent
- **Cost**: moderate (20-50% more than HASL)
- **Composition**: 5-12 µin Ag over Cu, OSP layer prevents tarnish
- **Advantages**: excellent flatness, fine-pitch capable
- **Disadvantages**: tarnish sensitivity, contamination-prone, migration risk
- **Storage**: sealed, desiccant, < 6 months preferred
- **Best for**: fine-pitch, flatness-critical, when ENIG cost is prohibitive
- **Not recommended for**: long-term storage, outdoor/humid environments

### Immersion Tin (Sn)

- **Shelf life**: 6 months (shortest)
- **Flatness**: excellent
- **Cost**: low
- **Composition**: 5-12 µin Sn over Cu
- **Advantages**: flat surface, RoHS compliant
- **Disadvantages**: whisker growth risk, shortest shelf life, poor long-term stability
- **Best for**: RoHS boards with fast turnaround, flatness required
- **Not recommended for**: long-term storage, applications with whisker sensitivity

### Hard Gold (Electroplated)

- **Shelf life**: indefinite (extremely stable)
- **Cost**: highest (5-10x HASL)
- **Composition**: 50+ µin over Ni strike
- **Advantages**: wear resistance, connector contact surfaces, indefinite shelf life
- **Disadvantages**: very expensive, gold embrittlement risk, overkill for solder pads
- **Best for**: connector fingers, contact pads, high-reliability legacy systems
- **Not for**: general solder pads (wasteful)

## Surface Finish Selection Decision Matrix

| Requirement | Best Choice | Rationale |
|---|---|---|
| **Lowest cost** | Lead-free HASL | minimum cost, adequate flatness |
| **Fine-pitch BGA (< 0.5 mm)** | ENIG or OSP | flatness + reliability |
| **Maximum shelf life** | ENIG | 12+ months stable |
| **Fastest time-to-assembly** | OSP | excellent wettability |
| **High reliability** | ENIG | stable, no tarnish, excellent corrosion resistance |
| **Long-term storage (> 6 months)** | ENIG | most stable |
| **Prototype with quick assembly** | HASL or OSP | cost + speed trade-off |
| **Gold wire bonding** | ENIG | prevents Au-Cu diffusion |

## Solder Paste Stencil Aperture Design

### Area Ratio (Stencil Aperture Efficiency)

Formula: `(L × W) / (2 × (L + W) × T)` where L=length, W=width, T=thickness

- **Minimum acceptable**: 0.66 (hard limit for paste transfer)
- **Target**: 0.75+ for reliable paste transfer efficiency (PTE) ≥ 80%
- **Aspect ratio (W/T)**: minimum 1.5 (aperture width / stencil thickness)

### Standard PCB Components

| Component Type | Stencil Thickness | Aperture Size | Notes |
|---|---|---|---|
| **QFP pitch ≥ 0.65 mm** | 0.15 mm (6 mil) | 1:1 pad size | standard approach |
| **QFP pitch 0.5 mm** | 0.13 mm (5 mil) | 85-90% pad width, 110% length | asymmetric for paste coverage |
| **QFP pitch < 0.5 mm** | 0.12 mm (5 mil) or 0.13 mm | 75-80% pad size | requires electropolishing |
| **BGA pitch ≥ 1.0 mm** | 0.15 mm (6 mil) | 1:1 pad size | standard circular aperture |
| **BGA pitch 0.5-1.0 mm** | 0.13 mm (5 mil) | 95% pad size | reduction to prevent solder balling |
| **BGA pitch < 0.5 mm** | 0.12 mm (5 mil) | 90-95% pad size | fine-pitch reduction |
| **QFN/CSP < 0.5 mm** | 0.12 mm (5 mil) | 85-90% pad size | electropolishing required |

### Stencil Thickness vs Pitch

- **Coarse pitch (≥ 0.65 mm)**: 0.15 mm (6 mil) standard
- **Medium pitch (0.5-0.65 mm)**: 0.13 mm (5 mil)
- **Fine pitch (< 0.5 mm)**: 0.12 mm (5 mil) or 0.125 mm
- Rule: thinner stencil = better aspect ratio for fine features

### Paste Transfer Efficiency (PTE)

- **Target**: ≥ 80% (paste deposited / paste in stencil opening)
- **Factors**: area ratio, aspect ratio, paste rheology, printer pressure, separation angle
- **Electropolishing benefit**: improves PTE by 5-10% for fine-pitch by smoothing aperture walls

### Electropolishing (Required for Fine Pitch)

- **Use when**: AFP, QFN, CSP pitch < 0.5 mm
- **Benefit**: smooth aperture walls reduce paste shear, improve PTE ≥ 85%
- **Laser-cut + electropolished stencil**: can achieve 0.8+ PTE even for 0.4 mm pitch
- **Cost**: 20-30% premium over standard laser-cut

## Paste Release Considerations

### Solder Paste Stencil Design for Release

- **Snap-off height (squeegee separation)**: typically 1-2 mm above board
- **Separation angle**: 45-60° (sharper angle = easier paste release)
- **Undercut apertures**: slight taper (microseconds laser) improves release
- **Paste tackiness**: influences release; high-tack pastes require steeper angle

### Paste Slump Prevention

- **Solder mask opening size**: limit area to prevent paste slump before reflow
- **Paste volume**: area ratio ≥ 0.66 ensures enough aperture for paste volume
- **Dwell time**: keep paste on board < 10 minutes before reflow to minimize slump
- **Stencil-to-board gap**: too large (> 0.5 mm) allows paste migration

### Fine-Pitch Paste Deposition

- **Smaller apertures**: higher squeegee pressure needed (risk of stencil damage)
- **Multiple passes**: for very fine pitch (< 0.4 mm), two-pass print may improve coverage
- **Asymmetric apertures**: elongate apertures in one direction to improve release for directional vias

## When to Use Each Surface Finish

### HASL — General SMT, Prototypes
- Cost-sensitive
- Through-hole dominant
- QFP/pitch ≥ 0.65 mm
- Fast turnaround, don't care about flatness

### ENIG — Production, Fine-Pitch, High-Reliability
- BGA with pitch < 0.5 mm
- Long shelf life required (> 6 months)
- High-reliability application (mil-spec, automotive)
- Gold wire bonding needed
- Flatness critical
- Budget allows (2-3x HASL cost)

### OSP — Fine-Pitch with Fast Assembly
- BGA 0.5-1.0 mm pitch
- Assembly happens within 1-2 months
- Excellent flatness needed
- Cost-sensitive (avoid ENIG cost)
- Risk: humidity exposure during storage

### Immersion Silver — Flatness-Critical, Cost-Conscious
- Fine-pitch, flatness critical
- Budget between HASL and ENIG
- Assembly within 6 months
- Avoid environments with high humidity
- Risk: tarnishing, contamination sensitivity

### Immersion Tin — RoHS Compliance, Cost Sensitive
- RoHS-only requirement
- Fast assembly (< 6 months)
- Acceptable to avoid whisker risk
- Cost lower than Ag/ENIG
- Not for long-term storage

### Hard Gold — Connectors, Contact Pads
- Connector fingers, edge card contacts
- Sliding/wear-prone surfaces
- Long-term storage indefinite
- Cost not a primary factor
- Never for general solder pads (wasteful)

## Common Mistakes to Avoid

- **Solder mask expansion too small** (< 3 mil): pad may be partially covered, compromises solder joint
- **Solder mask dam too narrow** (< 4 mil): bridging between adjacent pads during reflow
- **Tenting large vias** (> 12-15 mil): solder mask may not fully cover, leaving openings
- **Using SMD pads for non-fine-pitch**: reduces solder volume unnecessarily
- **Over-tight stencil apertures** (< 70% pad size): insufficient paste transfer, cold joints
- **Ignoring shelf life for application**: OSP/Immersion Ag > 6 months storage risk
- **Hard gold on solder pads**: astronomical cost waste, gold embrittlement risk
- **Electropolishing only top surface**: aperture walls still rough, defeats purpose
