---
scope: personal
---

# Solder Mask Design

## Table of Contents

- [Solder Mask Types](#solder-mask-types)
- [Solder Mask Clearance & Expansion](#solder-mask-clearance--expansion)
- [Solder Mask Dam (Web) Width Between Pads](#solder-mask-dam-web-width-between-pads)
- [Solder Mask Over Via (SMOV)](#solder-mask-over-via-smov)

## Solder Mask Types

- **Liquid Photoimageable (LPI)** — standard, lowest cost, best for fine features
- **Dry Film** — more expensive, better dimensional stability
- **Acrylate-based** — fastest cure, best flexibility
- **Epoxy-based** — better chemical resistance, slower cure

## Solder Mask Clearance & Expansion

### NSMD Pad (Non-Solder Mask Defined) — Preferred

- **Minimum expansion**: 3 mils (0.075 mm) per side to account for 2 mils misregistration
- **Recommended expansion**: 4 mils (0.1 mm) per side for all SMT pads
- Use for: standard SMT, BGA, QFP — provides best solder joint reliability

### SMD Pad (Solder Mask Defined) — Fine Pitch Only

- Use only when pitch < 0.5 mm and solder mask dam cannot be maintained
- Mask opening defines pad size, must be larger than copper pad
- Typical for fine-pitch BGA (< 0.5 mm pitch)
- Solder joint strength reduced due to reduced solder volume

## Solder Mask Dam (Web) Width Between Pads

Minimum widths vary by solder mask color and material:

| Color | Minimum Width |
|-------|---|
| Green | 4 mil (0.1 mm) |
| Blue | 5 mil (0.127 mm) |
| Red | 5 mil (0.127 mm) |
| Black | 6 mil (0.152 mm) |
| White | 7 mil (0.178 mm) |

- **Standard practice**: design for ≥ 4 mil minimum (green mask)
- Smaller dams risk solder bridging between adjacent pads
- For fine-pitch BGA, may need to switch to SMD pads if dam < 4 mil cannot be maintained

## Solder Mask Over Via (SMOV)

### Tenting — Standard Practice

- **Ideal for vias**: finished hole diameter < 12 mil (0.3 mm)
- **Acceptable up to**: 15 mil (0.38 mm) diameter
- **Solder mask expansion for tented vias**: -2 to -3 mils (negative expansion ensures complete coverage)
- **Prevents solder wicking** into via during reflow
- **Lowest cost** option, most common

### Plugging — Via-in-Pad Designs

- Non-conductive fill material + solder mask plug
- **Use for**: Ball Grid Array (BGA) via-in-pad, high-density designs
- Completely prevents solder flow into via
- More expensive than tenting
- Requires specialized process

### Open Via — Special Cases Only

- No mask coverage, pad exposed
- Use for: test points, thermal vias not adjacent to solder pads
- **Risk**: solder wicking onto open via reduces pad solder volume
- Avoid near pads where solder joint quality is critical
