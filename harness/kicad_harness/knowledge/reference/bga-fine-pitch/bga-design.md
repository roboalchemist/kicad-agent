---
scope: both
---

# BGA Design & Fanout

## Table of Contents

- [BGA Fanout Strategies by Pitch](#bga-fanout-strategies-by-pitch)
- [Escape Routing by Pitch Size](#escape-routing-by-pitch-size)
- [Via-in-Pad Requirements (VIPPO)](#via-in-pad-requirements-vippo)
- [Pad Cratering Prevention](#pad-cratering-prevention)
- [BGA Pad Design](#bga-pad-design)
- [Solder Paste Aperture Design](#solder-paste-aperture-design)

## BGA Fanout Strategies by Pitch

### Dog-Bone Fanout
- **Pitch range**: 0.8 mm and larger
- **0.8 mm pitch**: Usually achievable with dog-bone
- **0.65 mm pitch**: Tight but possible with dog-bone
- **Below 0.65 mm**: Via-in-pad required

### Via-in-Pad (VIPPO)
- **When to use**: Pitch ≤0.5 mm, or when dog-bone routing impossible
- **Via placement**: Directly centered in BGA pad
- **Fill material**: Conductive epoxy (preferred) or non-conductive epoxy
- **Plating**: Copper plated over fill to create flat, solderable surface
- **Prevents**: Solder wicking into via during reflow
- **Benefit**: Shortest signal paths, improved signal integrity, maximum routing density

## Escape Routing by Pitch Size

### 1.0 mm Pitch
- Dog-bone fanout standard approach
- Traces between ball rows to escape vias
- Minimal microvia requirements

### 0.8 mm Pitch
- Dog-bone fanout with tighter spacing
- Consider via-in-pad for inner rows
- Trace width: 4-5 mils (0.10-0.13 mm)
- Via spacing: minimum 0.15 mm between via edges

### 0.65 mm Pitch
- Dog-bone margin tight; consider via-in-pad for all balls
- Microvia diameter: 0.2-0.3 mm
- Trace width: 3-4 mils (0.076-0.10 mm)
- Via-in-pad preferred for inner rows

### 0.5 mm Pitch
- Via-in-pad required for practical escape
- Microvia-in-pad standard
- Pad size: 0.25-0.30 mm
- Via diameter: 0.15-0.20 mm
- Allows multi-row escape routing

### 0.4 mm Pitch and Finer
- Microvia-in-pad mandatory
- Multiple microvia technology required (microvias stacked or sequential)
- Pad size: 0.20-0.25 mm
- Via diameter: 0.10-0.15 mm
- Via spacing: 0.10-0.15 mm minimum

## Via-in-Pad Requirements (VIPPO)

### Via Specifications
- **Diameter**: 0.15-0.20 mm for 0.5 mm pitch; 0.10-0.15 mm for ≤0.4 mm pitch
- **Via-to-pad ratio**: Via diameter should not exceed 70% of pad diameter
- **Multiple vias**: Use 2-4 vias per pad for lowest impedance on high-pin-count BGAs

### Fill Requirements
- **Fill type**: Conductive epoxy (Cu-filled, Ag-filled) or non-conductive epoxy
- **Conductive fill**: Preferred for thermal conductivity and lower impedance
- **Coverage**: 100% fill of via cavity

### Plating Over
- **Copper plating**: Minimum 25 µm plating over fill
- **Flatness**: Fill + plating must be coplanar with pad surface (±25 µm tolerance)
- **Surface finish**: Apply standard HASL or ENIG over plated via
- **Purpose**: Prevents solder wicking into via during reflow

### Testing
- **X-ray verification**: Inspect fill quality and coverage
- **Cross-section**: Sample inspection of fill process

## Pad Cratering Prevention

### Solder Joint Stress Management
- **Via size**: Limit via diameter to <70% of pad diameter
- **Via placement**: Center via in pad or offset slightly away from main stress area
- **Via fill + plate**: Prevents solder flow into via, distributing stress evenly
- **Trace attachment angle**: Taper dog-bone traces gradually (not sharp angles)

### Thermal Management
- **Thermal relief spacing**: 0.10-0.30 mm trace width for relief arms
- **Air gap**: 0.15-0.50 mm between pad and plane (wider = more isolation, lower thermal coupling)
- **Number of relief arms**: 4 thermal relief arms for inner pads (more uniform heating)

### Reflow Profile Control
- **Ramp rate**: 2-3 °C/s to peak temperature
- **Time above 217 °C**: 30-60 seconds (IPC-A-610 standard)
- **Peak temperature**: 245-260 °C (varies by solder paste spec)
- **Cooling ramp**: 2-6 °C/s post-peak
- **Thermal cycling test**: Verify joint reliability after 500+ thermal cycles

## BGA Pad Design

### Pad Size (Land Pattern)
- **IPC-A-610 Nominal**: Pad diameter = Ball diameter - 0.05 mm
- **0.8 mm ball pitch**: Pad ~0.45-0.50 mm diameter
- **0.5 mm ball pitch**: Pad ~0.25-0.30 mm diameter
- **0.4 mm ball pitch**: Pad ~0.20-0.25 mm diameter
- **Aspect ratio**: Keep pad roughly circular (within ±0.05 mm of diameter)

### Solder Mask
- **Web clearance**: 0.05-0.10 mm between pads and solder mask edge
- **Type**: Solder mask defined (SMD) for fine pitch (mask overlaps copper)
- **Fine-pitch (<0.5 mm)**: SMD mandatory to prevent bridging
- **Thicker pitches (>0.8 mm)**: Can use solder mask over copper (SOC)
- **Solder mask thickness**: 12-25 µm (thinner for fine-pitch to prevent bridging)

### Copper-to-Pad Clearance
- **Minimum trace clearance**: 0.10 mm (3.9 mils) pad-to-trace
- **Inner layer traces**: 0.076-0.10 mm (3-4 mils) minimum

## Solder Paste Aperture Design

### Stencil Thickness
- **Standard**: 0.10-0.127 mm (4-5 mils)
- **Fine-pitch**: 0.075-0.10 mm preferred to improve paste release

### Aperture Size for BGA
- **Standard aperture**: Same size as pad opening
- **Fine-pitch reduction**: Reduce aperture 20% smaller than pad for 0.5 mm pitch and below
- **Paste coverage target**: 80-100% of pad area

### Area and Aspect Ratios
- **Area ratio**: ≥0.66 (calculated as LW / 2T(L+W) for rectangular apertures)
- **Aspect ratio**: ≥1.5 (calculated as W/T, where W is aperture width, T is stencil thickness)
- **Paste volume**: 80-90% of stencil aperture volume indicates good release
- **Type 5 or finer paste**: Required for fine-pitch (0.5 mm and below)

### Fine-Pitch BGA Aperture Strategy
- **0.5 mm pitch**: 50-70% of pad area with solder paste
- **0.4 mm pitch**: 50-80% paste coverage; use Solder Mask Defined (SMD) pads
- **Rectangular pads**: More common for fine-pitch to reduce aperture opening dimensions
- **Paste reduction**: Helps prevent solder bridging and voids
