---
scope: personal
---

# PCB Version Control & Git Workflow

## Table of Contents

- [Revision Numbering Schemes](#revision-numbering-schemes)
- [File Naming Conventions](#file-naming-conventions)
- [Version Control (.gitignore)](#version-control-gitignore)
- [Git for Hardware: Pros / Cons / Practices](#git-for-hardware-pros--cons--practices)

## Revision Numbering Schemes

### Board Revision (Hardware)
- Format: `RevA`, `RevB`, `RevC` ... `RevZ`, `RevAA`, `RevAB`
- Increments with physical layout changes, layer stack changes, connector relocations
- Represents manufacturing-ready state
- Assigned only at design freeze / release to manufacturing
- Map to git tags: `hw-v1.0-RevA`, `hw-v2.0-RevB`

### Schematic Revision
- Format: `S1.0`, `S1.1`, `S2.0` (semantic versioning: major.minor)
- Increments when circuit topology changes (major) or values/part numbers update (minor)
- Tracks functional changes independent of layout
- Can iterate multiple times per board revision (e.g., RevA uses S1.0-S1.3)

### BOM Revision
- Format: `BOM_v1.0`, `BOM_v1.1`, `BOM_v2.0`
- Increments when part numbers, quantities, or obsolescence handling changes
- Date-tagged: `BOM_v1.0_20250304`
- Linked to schematic revision that generates it

### Gerber/Manufacturing Revision
- Implicit from board revision (e.g., `RevA_Gerbers`, `RevB_Gerbers`)
- Never released without board revision tag
- Timestamp Gerbers: `RevA_20250304_manufacturing`
- Track Gerber job file separate: `RevA_JLCPCB_20250304.gbrjob`

## File Naming Conventions

### Schematic Files
- `<ProjectName>-Schematic.kicad_sch`
- Sheets: `<ProjectName>-<SheetName>.kicad_sch` (e.g., `MyBoard-PowerDelivery.kicad_sch`)
- Always save with project name prefix

### PCB Layout
- `<ProjectName>-PCB.kicad_pcb` (main layout file)
- Backup naming during active design: `<ProjectName>-PCB_20250304_checkpoint.kicad_pcb` (save before major changes)
- Never commit checkpoint files; use git history instead

### Symbol & Footprint Libraries
- Symbols: `<ProjectName>-Symbols.kicad_sym`
- Footprints: `<ProjectName>-Footprints.pretty/` (directory format)
- Use consistent naming: Prefix with domain (e.g., `STM32`, `TPS`, `CONN_USB` for category)
- Version in path: `libraries/v2/STM32_Symbols.kicad_sym`

### BOM & Assembly
- `<ProjectName>_BOM_v1.0.csv` or `.xlsx`
- Columns: Reference, Value, Footprint, Quantity, Manufacturer Part #, Supplier, Notes
- Assembly drawing: `<ProjectName>_Assembly_Guide_RevA.pdf`
- Pick & place: `<ProjectName>_PNP_RevA.csv`

### Gerber & Fabrication
- Directory: `manufactering/<RevX>/` (e.g., `manufacturing/RevA/`)
- Gerber naming: Standard `<ProjectName>-F.Cu.gbr`, `<ProjectName>-B.Cu.gbr`
- Drills: `<ProjectName>.xln` or `.drl` (Excellon format)
- Job file: `<ProjectName>_JLCPCB.gbrjob` or manufacturer-specific (e.g., `_PCBWay.gbrjob`)
- Stack-up: `<ProjectName>_Stackup_RevA.pdf`
- Notes: `<ProjectName>_MfgNotes_RevA.txt`

### Documentation & Design Review
- Design review: `DR_<ProjectName>_<Date>.md`
- Schematic review notes: `docs/schematic-review-RevX.md`
- Layout review notes: `docs/layout-review-RevX.md`
- Sign-off sheet: `docs/signoff-<component>-RevX.pdf`

## Version Control (.gitignore)

### Track in Git
- `*.kicad_sch` (schematics)
- `*.kicad_pcb` (layouts)
- `*.kicad_sym` (project symbol libraries)
- `libraries/*.pretty/` (footprint directories)
- `*.csv` (BOM, pick & place, when human-reviewed)
- `*.md` (design review docs, ECO notes, change tracking)
- `.gitignore` (explicit)

### Exclude from Git
- `*.kicad_pcb-bak` (auto-saves; recreated locally)
- `*~` (editor temp files)
- `*.gch` (KiCad cache files)
- `sym-lib-table` (local symbol table mappings)
- `fp-lib-table` (local footprint table mappings)
- `*.3dshapes/` (auto-generated 3D models)
- `*.bak`, `*.swp` (editor backups)
- `manufacturing/` (gerbers and fab files, store separately; see archival section)

### .gitignore Template
```
# KiCad auto-generated
*.kicad_pcb-bak
*.kicad_sch-bak
*.kicad_pro-bak
*.kicad_prl
*.kicad_prl
*-cache.kicad_sym
*-backups/
*-rescue.kicad_sch
*-rescue.lib

# Editor cache
*.gch
sym-lib-table
fp-lib-table
settings.json
⤴
# Build artifacts
*. Cu.gbr
*.B.Cu.gbr
*.drl
*.xln
*.gbrjob
*.pdf

# OS
.DS_Store
Thumbs.db
```

## Git for Hardware: Pros / Cons / Practices

### Advantages
- Tracks design iteration, design intent, and decision history
- Enables collaborative schematic/layout work (with discipline)
- Audit trail for compliance and change tracking
- Diff schematics/layouts (text-based in .kicad_sch, metadata in .kicad_pcb)

### Disadvantages
- Binary .kicad_pcb files don't diff well in standard git diff
- Merge conflicts in layout files are hard to resolve
- File size grows with each revision; consider shallow clones
- Layout thrashing (frequent saves) pollutes history

### Best Practices

#### Branching
- `main` = stable releases (design-frozen, tagged)
- `develop` = integration branch, ready for review
- `feature/<name>` = active schematic/layout work
- Naming: `feature/power-delivery-redesign`, `feature/add-usb3-port`

#### Commits
- Commit after logical design changes, not auto-saves
- Message format: `[COMPONENT] description of change`
  - Examples: `[Power] add AMS1117 LDO for 3.3V rail`, `[Layout] route USB differential pair`, `[BOM] update STM32G4 to newer variant`
- Reference issue: `[Power] fix #47: add decoupling caps`
- Avoid large commits; prefer small, reviewable changes

#### Avoiding Merge Conflicts
- One person owns layout file during active routing (no parallel edits)
- Use `git lock` or branch discipline to prevent concurrent edits
- Schematic sheets: divide by subsystem (power, analog, digital) to reduce conflicts
- Merge strategy: `--ours` for layout (keep local), manual review for schematic

#### Binary File Handling
- DO NOT use `git-lfs` for .kicad_pcb (KiCad files still merge-conflict)
- DO use standard git; binary files are small enough (<5MB typical)
- If using LFS: LFS for large Gerber archives, high-res design photos, 3D model .step files
- Exclude Gerber directories from git; archive separately (see below)
