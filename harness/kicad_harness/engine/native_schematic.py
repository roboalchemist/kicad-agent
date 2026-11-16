"""Fast native whole-schematic generation.

skidl parses every symbol library in a fresh process and can spend more than ten minutes on a
30-part board. This path uses the harness's own schematic writer: load only the libraries the
parts name, place the symbols on a grid, put a net label on every pin, add PWR_FLAGs for the
supply nets, draw stubs and run the existing tidy pass. It is deliberately simple; it is fast
and deterministic, and the normal schematic tools can refine the result.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any

from ..config import REPO_DIR
from .schematic import Schematic, read_library
from .skidl_gen import NetSpec, PartSpec

_POWER = re.compile(r"(^GND$|^\+|^VCC|^VDD|^VBUS|^VIN|^P\d*V|^3V3$|^5V$)", re.IGNORECASE)


def symbol_dirs(extra: list[Path] | None = None) -> list[Path]:
    out: list[Path] = []
    for value in [os.environ.get("KICAD_SYMBOL_DIR")] + [
            os.environ.get(f"KICAD{version}_SYMBOL_DIR") for version in (6, 7, 8, 9, 10)]:
        if value:
            out.append(Path(value).expanduser())
    out += [Path("/usr/share/kicad/symbols"),
            REPO_DIR / "demo" / "libs" / "symbols",
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")]
    out += list(extra or [])
    seen: set[Path] = set()
    return [p for p in out if p.is_dir() and not (p in seen or seen.add(p))]


def _library_path(name: str, dirs: list[Path]) -> Path:
    for directory in dirs:
        path = directory / f"{name}.kicad_sym"
        if path.is_file():
            return path
    names = sorted({p.stem for d in dirs for p in d.glob("*.kicad_sym")})
    close = difflib.get_close_matches(name, names, n=6)
    raise ValueError(f"no symbol library {name!r}; close matches: {close}")


def generate(parts: list[PartSpec], nets: list[NetSpec], out: str | Path, *,
             symbol_dirs_extra: list[Path] | None = None) -> dict[str, Any]:
    dirs = symbol_dirs(symbol_dirs_extra)
    libraries: dict[str, dict] = {}
    for part in parts:
        if part.library not in libraries:
            libraries[part.library] = read_library(_library_path(part.library, dirs))
        library = libraries[part.library]
        if part.symbol not in library:
            close = difflib.get_close_matches(part.symbol, sorted(library), n=8)
            raise ValueError(f"no symbol {part.library}:{part.symbol}; close matches: {close}")

    doc = Schematic.create(out, libraries=libraries)
    columns = 6
    for index, part in enumerate(parts):
        x = 25.0 + (index % columns) * 38.0
        y = 25.0 + (index // columns) * 34.0
        properties = {}
        if part.lcsc:
            properties["LCSC"] = part.lcsc
        if part.description:
            properties["Description"] = part.description
        doc.add_symbol(f"{part.library}:{part.symbol}", part.ref, x, y,
                       value=part.value or None, footprint=part.footprint or None,
                       properties=properties or None)

    # Power flags make ERC accept connector/fuse-fed rails. Do not add one where the net already
    # has a power-output pin (an LDO/buck output), or ERC reports two power outputs connected.
    def has_power_output(net: NetSpec) -> bool:
        for ref, number in net.pins:
            placement = doc._placement(ref)
            symbol = doc.lookup(placement.lib_id) if placement else None
            pin = symbol.pins.get(str(number)) if symbol else None
            if pin is not None and pin.electrical == "power_out":
                return True
        return False

    power_nets = [net for net in nets
                  if _POWER.search(net.name or "") and not has_power_output(net)]
    if power_nets and "power" not in doc.libraries:
        doc.libraries["power"] = read_library(_library_path("power", dirs))
    for index, net in enumerate(power_nets):
        ref = f"#FLG{index + 1}"
        x = 25.0 + ((len(parts) + index) % columns) * 38.0
        y = 25.0 + ((len(parts) + index) // columns) * 34.0
        flag = doc.add_symbol("power:PWR_FLAG", ref, x, y, value="PWR_FLAG")
        point = doc.pin_position(flag, "1")
        if point:
            doc.add_label(net.name, *point)

    for net in nets:
        for ref, number in net.pins:
            placement = doc._placement(ref)
            if placement is None:
                raise ValueError(f"net {net.name!r} refers to missing symbol {ref!r}")
            point = doc.pin_position(placement, str(number))
            if point is None:
                symbol = doc.lookup(placement.lib_id)
                valid = ", ".join(sorted(symbol.pins)) if symbol else "none"
                raise ValueError(f"{ref} has no pin {number!r}; valid pins: {valid}")
            doc.add_label(net.name, *point)
    doc.save()

    stubs = doc.draw_pin_stubs()
    doc.save()
    from . import layout
    tidy = layout.tidy(doc)
    doc.save()
    _verify_netlist(out, nets)
    return {"path": Path(out).name, "parts": len(parts), "nets": len(nets),
            "flags": len(power_nets), "stubs": stubs, "layout": tidy}


def _verify_netlist(out: str | Path, nets: list[NetSpec]) -> None:
    """Fail if the drawn sheet's netlist is not the one that was asked for.

    Labels make connectivity in a schematic, so a stub or label that lands on the wrong pin is
    invisible in the drawing but changes the design: two nets become one. KiCad's own netlist is
    the authority, so compare it before the file is accepted.
    """
    from . import kicad_cli

    intended: dict[str, set[str]] = {}
    for net in nets:
        intended.setdefault(str(net.name).lstrip("/"), set()).update(
            f"{ref}.{number}" for ref, number in net.pins
        )
    netlist_path = Path(out).with_suffix(".verify.net")
    try:
        netlist = kicad_cli.parse_netlist(kicad_cli.export_netlist(out, netlist_path).read_text())
    except Exception as exc:  # pragma: no cover - a broken kicad-cli is reported, not guessed at
        raise ValueError(f"could not verify the drawn netlist: {exc}") from exc
    finally:
        netlist_path.unlink(missing_ok=True)
    actual: dict[str, set[str]] = {}
    for name, members in netlist["nets"].items():
        actual.setdefault(str(name).lstrip("/"), set()).update(
            f"{ref}.{pin}" for ref, pin in members
        )

    problems: list[str] = []
    for name, expected in intended.items():
        found = actual.get(name, set())
        missing = expected - found
        extra = found - expected
        if missing:
            problems.append(f"net {name!r} is missing {sorted(missing)[:4]}")
        if extra:
            problems.append(f"net {name!r} wrongly includes {sorted(extra)[:4]}")
    if problems:
        raise ValueError(
            "the drawn schematic does not match the requested netlist: " + "; ".join(problems[:6])
        )
