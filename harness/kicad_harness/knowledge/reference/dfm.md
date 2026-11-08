# PCB Design for Manufacturing (DFM) & Assembly (DFA)

## Trace & Space
| Parameter | Standard | Tight | Conservative |
|-----------|----------|-------|--------------|
| Min trace width | 6mil (0.15mm) | 4mil (0.1mm) | 8mil (0.2mm) |
| Min spacing | 6mil (0.15mm) | 4mil (0.1mm) | 8mil (0.2mm) |
| Min annular ring | 4-5mil | 3mil | 6mil |

## Vias
| Parameter | Standard | Value |
|-----------|----------|-------|
| Drill diameter | Standard | 12mil (0.3mm) |
| Pad diameter | Standard | 24mil (0.6mm) |
| Aspect ratio | Max | 10:1 (drill:board thickness) |
| Via-in-pad | Requires | Filled and capped vias |

## Copper Balance
- Target 40-60% copper density per layer to prevent warping
- Add copper fill in sparse areas if needed
- Use thermal relief (4 spokes, 10mil width) on plane connections

## Board Edge
- 2-3mm clearance from edge for all components
- V-score lines need 0.5mm keepout on each side
- Routed edges need tab connections for panelization

## Pick-and-Place
- Consistent component orientation reduces placement errors
- Place fiducials (3 per board, asymmetric) for machine alignment
- 0.5mm minimum spacing between components (dense), 1.27mm standard
- 100mil (2.54mm) clearance from board edge for machine grippers

## Tombstoning Prevention (0402/0603)
- Ensure equal pad sizes and equal trace connections to each pad
- Symmetric thermal relief on both pads
- Avoid routing thick traces to only one pad (thermal imbalance)
- Use solder paste stencil with equal apertures

## Soldering
- Maintain consistent pad sizes per IPC-7351
- Thermal relief on all plane-connected pads
- Solder mask between fine-pitch pads (minimum 3mil solder mask web)
