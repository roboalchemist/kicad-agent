---
scope: personal
---

# Release Management & Change Control

## Table of Contents

- [Design Review Documentation & Sign-Off](#design-review-documentation--sign-off)
- [ECO (Engineering Change Order) Process](#eco-engineering-change-order-process)
- [Release Management](#release-management)
- [Design Freeze & Change Control](#design-freeze--change-control)
- [Library Management](#library-management)
- [Archival & Historical Records](#archival--historical-records)

## Design Review Documentation & Sign-Off

### Review Types
- **Schematic Review**: Functional correctness, part selection, compliance (EMC, safety, thermal)
- **Layout Review**: Design rules (trace width, clearance), manufacturability, signal integrity
- **BOM Review**: Part availability, lead times, obsolescence, cost
- **Manufacturing Readiness Review (MRR)**: Gerbers, silkscreen, test points, DFM checks

### Sign-Off Tracking
- File: `docs/signoff.md` or spreadsheet
- Track reviewer name, date, sign-off type, and any action items
- Format:
  ```markdown
  ## RevA Sign-Offs
  - **Schematic Review**: John Smith, 2025-03-04, APPROVED (no issues)
  - **Layout Review**: Jane Doe, 2025-03-04, APPROVED (trace width OK, HDI constraints met)
  - **BOM Review**: Supply Chain, 2025-03-04, NEEDS_REVIEW (lead time 12 weeks on STM32)
  - **MRR**: Fab Contact, 2025-03-05, APPROVED (Gerbers OK, ready for production)
  ```
- Require sign-off before releasing to manufacturing
- Attach review checklists in `docs/` directory

### Design Review Checklist
- Schematic completeness (all signals connected, no dangling nets)
- Part datasheets reviewed (derating, operating conditions, package)
- Design rules verified (trace width, clearance, via spacing for layer stack)
- Power distribution (decoupling caps, bulk caps, current limits)
- Signal integrity (trace routing, impedance control, differential pair spacing)
- Thermal analysis (hot spots, thermal vias, copper area)
- DFM concerns (fine pitch parts, solder mask bridges, silkscreen readability)
- Testing accessibility (test points, probe clearance, JTAG/SWD access)

## ECO (Engineering Change Order) Process

### When to Issue ECO
- Schematic net changes after layout started
- Part obsolescence / substitution
- Design errors found post-release
- Compliance issues
- Performance improvements

### ECO Workflow
1. **Create ECO document**: `docs/ECO_001_20250304_<description>.md`
2. **Describe change**: Problem statement, proposed solution, affected subsystems
3. **Impact analysis**: Board revision required? Schematic rev only? BOM only?
4. **Approvals**: Design lead, manufacturing, supply chain
5. **Implementation**: Update schematics, layouts, BOM, Gerbers
6. **Tag release**: `hw-v1.1-ECO001` or `hw-v1.1-RevA1` (revision letter increment)
7. **Archive ECO**: Keep in `docs/echos/` with final sign-off

### ECO Document Template
```markdown
# ECO-001: Add Bulk Capacitor on 5V Rail

## Issue
Input transient on 5V rail causes brownout reset during LED load dump.

## Root Cause
Insufficient bulk capacitance; existing 100uF depleted in 200ms.

## Solution
Add 470uF tantalum capacitor in parallel with existing cap on 5V rail.

## Impact
- **Schematic**: Add C35, update value on C12 footprint
- **Layout**: Place C35 near power connector, no new traces required
- **BOM**: Add 1x TAJI D-case 470uF/16V part (TDK TAJA477M016)
- **Gerbers**: No change to stackup or layer assignment
- **Board Revision**: RevA -> RevA1 (minor change)

## Approvals
- [ ] Design Lead (John Smith)
- [ ] Manufacturing (Jane Doe) - Footprint available, 2-week lead time
- [ ] Supply Chain (Bob Brown)

## Release Tag
`hw-v1.0-RevA1-ECO001` (2025-03-05)
```

## Release Management

### Prototype Release
- Tag format: `prototype-v1.0` (internal iteration)
- Gerbers optional (may use Gerber viewer)
- BOM not finalized (part numbers may have alternatives)
- Assembly notes document manual process
- Archive: `releases/prototype-v1.0/`

### Production Release
- Tag format: `hw-v1.0-RevA` (semantic + board revision)
- Gerbers mandatory, generated from final layout
- BOM finalized with validated part numbers, lead times, alternates
- Manufacturing notes finalized (inspection steps, test procedure)
- Design review sign-offs complete and documented
- Archive: `releases/hw-v1.0-RevA/` with checksums
- Create GitHub/Gitea release with changelog, manufacturing notes, BOM

### Release Contents Checklist
- [ ] Schematics (PDF export + .kicad_sch source)
- [ ] PCB layout (PDF + .kicad_pcb source)
- [ ] BOM (CSV + XLS, with part numbers verified)
- [ ] Gerbers (all layers, job file, stackup doc)
- [ ] Assembly drawing (PDF with reference designators, rotation)
- [ ] Manufacturing notes (DFM, panelization, test points)
- [ ] Design review sign-offs (scanned PDFs or MD summary)
- [ ] ECO history (if applicable)
- [ ] Test/validation reports
- [ ] Part datasheets for critical components (linked, not embedded)

## Design Freeze & Change Control

### Design Freeze Triggers
- Schematic review complete and approved
- Layout design rules check (DRC) passes
- Manufacturing readiness review scheduled
- No unresolved ECOs

### Frozen State Behaviors
- Only ECO changes permitted (tracked and tagged)
- Gerber generation locked (timestamp preserved)
- BOM locked (new line items require ECO)
- Schematic/layout edits require ECO justification
- Formal approval required before changes

### Change Control Board (CCB)
- Meets weekly or by-need
- Reviews pending ECOs
- Approves / rejects / defers changes
- Tracks impact (cost, schedule, risk)
- Maintains ECO ledger

### Post-Release Fixes
- Bug fixes after manufacturing starts: ECO process
- Critical safety issues: interim guidance doc + formal ECO + reworked build
- Minor silkscreen errors: next board revision, no ECO needed
- Document all fixes in `docs/field-updates.md`

## Library Management

### Symbol Management
- Centralized: `libraries/symbols/` directory in project
- Versioned: `libraries/symbols/v2/` (major version per breaking change)
- Naming: `PARTFAMILY_VARIANT.kicad_sym` (e.g., `STM32L073_LQFP48.kicad_sym`)
- Document pin functions in symbol properties
- Review symbols for accuracy before use in schematic

### Footprint Management
- Centralized: `libraries/footprints/<category>.pretty/` (directory per footprint library)
- Categories: `CONN` (connectors), `IC` (integrated circuits), `PASSIVES` (R/L/C/D)
- Naming: `<PARTFAMILY>_<PACKAGE>.kicad_mod` (e.g., `0603_SMD.kicad_mod`, `USB_C_16PIN.kicad_mod`)
- Versioning: Update in-place; track changes in git history
- Footprint properties: Link to datasheet, 3D model path, design rules

### Library Release & Versioning
- Tag symbols/footprints: `lib-symbols-v2.0`, `lib-footprints-v3.1`
- Release notes: `libraries/RELEASE_NOTES.md`
- Breaking changes: Major version (v1 -> v2)
- Bug fixes / new footprints: Minor version (v2.0 -> v2.1)
- Archive old versions: `libraries/archived/v1/` (keep for historical designs)

### Obsolescence Tracking
- Flag deprecated symbols/footprints: `[DEPRECATED]` in name
- Provide migration guide in release notes
- Keep old footprints in library for 2+ years (support rework)
- Example: `STM32F103_LQFP48 [DEPRECATED - use STM32L073_LQFP48]`

## Archival & Historical Records

### Per-Revision Archive Structure
```
releases/hw-v1.0-RevA/
├── schematics/
│   ├── MyBoard-Schematic.kicad_sch
│   ├── MyBoard-Schematic.pdf
│   └── sheets/
├── layout/
│   ├── MyBoard-PCB.kicad_pcb
│   ├── MyBoard-PCB.pdf
│   └── design-rules-check.txt
├── manufacturing/
│   ├── Gerbers/
│   │   ├── MyBoard-F.Cu.gbr
│   │   ├── MyBoard-B.Cu.gbr
│   │   ├── MyBoard.xln
│   │   └── MyBoard_JLCPCB.gbrjob
│   ├── MyBoard_Assembly_RevA.pdf
│   ├── MyBoard_BOM_v1.0.csv
│   ├── MyBoard_PNP_RevA.csv
│   ├── MyBoard_Stackup_RevA.pdf
│   └── MyBoard_MfgNotes_RevA.txt
├── docs/
│   ├── Design-Review-SignOff_RevA.pdf
│   ├── schematic-review_RevA.md
│   ├── layout-review_RevA.md
│   ├── ECO_history.md
│   └── test-report_RevA.pdf
└── MANIFEST.md (contents + checksums)
```

### Archival Checklist
- [ ] Gerbers verified (opened in CAM viewer, no manufacturing defects)
- [ ] BOM validated (all parts available, lead times confirmed)
- [ ] Schematic & layout exported to PDF (read-only)
- [ ] Checksum file generated: `sha256sum * > MANIFEST.sha256`
- [ ] README or MANIFEST.md created (describe contents, how to use)
- [ ] Compressed archive created: `tar.gz` for long-term storage
- [ ] Uploaded to archive storage (external drive, cloud backup, NAS)
- [ ] Git tag created: `releases/hw-v1.0-RevA` (points to commit hash)

### Manufacturing Files Storage
- Gerber archives stored separately from git repo (large, binary)
- Location: `manufacturing-archive/<ProjectName>/RevA/` on external drive or cloud
- Backup: At least 2 copies (on-site + off-site cloud)
- Retention: Keep all revisions indefinitely (support field repairs, reworks)
- Accessible by: Design team, manufacturing partners, field service

### Access & Retrieval
- Document archive locations in `README.md`
- Maintain index of all releases: `RELEASES.csv`
  ```
  Revision,Date,Status,Gerber_Archive,Notes
  RevA,2025-03-04,Production,manufacturing-archive/RevA/,Initial release
  RevA1,2025-04-15,Production,manufacturing-archive/RevA1/,ECO-001 applied
  ```
- Link to design review docs, ECO trail for quick historical lookup

### Long-Term Archival (5+ Years)
- Convert Gerbers to PDF + raster images (CAM software may obsolete)
- Export schematics to PDF + SVG (KiCad format may change)
- Document stack-up, layer assignment, design rules in plain text
- Store in archive-grade format: `.tar.gz` with checksums, NOT cloud-only
