"""Resolve BOM lines to real JLCPCB parts, from evidence in the catalogue.

A BOM is only useful if each line names an orderable part. The catalogue stores each part's
attributes (``Resistance: 10kΩ``, ``Capacitance: 100nF``, ``Pitch: 5.08mm``), so a line can be
matched on *its own numbers* rather than by guessing from an MPN substring — which would happily
match 510k for "10k".

Matching is deliberately conservative: a line resolves only when exactly one part fits the value,
the package (from the footprint) and, where the value states one, the voltage/current rating.
Anything ambiguous stays unresolved and is reported, because a wrong part number is worse than a
blank one.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from . import parts

# value like "10k", "4k7", "100nF", "33uH", "470uF/10V", "2A"
_PASSIVE = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mult>[pnuµmkKMG]?)\s*(?P<unit>F|H|Ω|R|ohm|A|V)?"
    r"(?:\s*/\s*(?P<extra_num>\d+(?:\.\d+)?)\s*(?P<extra_unit>V|A))?$"
)
_MULT = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
    "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
}
_UNIT_ATTR = {"F": "Capacitance", "H": "Inductance", "Ω": "Resistance", "R": "Resistance",
              "ohm": "Resistance", "A": "Current"}


def _normalise_unit(text: str) -> float | None:
    """Parse a catalogue attribute such as ``10kΩ`` or ``100nF`` into base units."""
    text = (text or "").strip().replace(" ", "")
    match = re.match(r"^([\d.]+)\s*([pnuµmkKMG]?)\s*(F|H|Ω|ohm|A|V)?$", text)
    if not match:
        return None
    number = float(match.group(1))
    return number * _MULT.get(match.group(2) or "", 1.0)


def parse_value(value: str) -> dict[str, Any] | None:
    """Turn a BOM value into a comparable quantity, or ``None`` if it is not one.

    ``10k`` -> resistance 1e4; ``100nF`` -> capacitance 1e-7; ``470uF/10V`` adds a 10 V rating.
    """
    text = (value or "").strip()
    match = _PASSIVE.match(text)
    if not match:
        return None
    unit = match.group("unit")
    # "10k" (no unit) is a resistance; "10" alone is ambiguous and rejected.
    if not unit:
        if not match.group("mult"):
            return None
        unit = "Ω"
    if unit == "V":
        return None
    amount = float(match.group("num")) * _MULT.get(match.group("mult") or "", 1.0)
    out: dict[str, Any] = {"attr": _UNIT_ATTR.get(unit, "Resistance"), "value": amount}
    if match.group("extra_num"):
        out["rating_unit"] = match.group("extra_unit")
        out["rating"] = float(match.group("extra_num"))
    return out


def package_hint(footprint: str) -> str | None:
    """The catalogue package implied by a footprint name (``...R_0805_2012Metric`` -> ``0805``)."""
    return parts.package_hint(footprint or "")


def _millimetres(text: Any) -> float | None:
    """Pitch-style attributes are written in mm ("5.08mm"), which the unit parser does not cover."""
    match = re.match(r"^\s*([\d.]+)\s*mm\s*$", str(text or ""), re.IGNORECASE)
    return float(match.group(1)) if match else None


def _close(a: float, b: float, tolerance: float = 0.02) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= max(abs(b) * tolerance, 1e-15)


@dataclass
class Match:
    """A catalogue part that satisfies a BOM line, with the reason it was accepted."""

    lcsc: str
    mpn: str
    package: str
    basic: bool
    stock: int
    unit_price: float | None
    matched_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lcsc": self.lcsc,
            "mpn": self.mpn,
            "package": self.package,
            "tier": "basic" if self.basic else "extended",
            "stock": self.stock,
            "unit_price": self.unit_price,
            "url": f"https://www.lcsc.com/product-detail/{self.lcsc}",
            "matched_by": self.matched_by,
        }


def _rows(package: str | None, limit: int = 400, attribute: str | None = None,
          mpn_like: str | None = None, extra_like: str | None = None) -> Iterable[tuple]:
    """Candidates, prefiltered in SQL so the scan is bounded.

    ``attribute`` narrows to parts that carry that parameter (``Resistance``); ``mpn_like``
    searches part numbers directly.
    """
    path = parts.db_path()
    if path is None:
        return []
    sql = "SELECT lcsc, mfr, package, stock, basic, price, extra FROM components WHERE stock > 0"
    params: list[Any] = []
    if package:
        sql += " AND package LIKE ?"
        params.append(f"%{package}%")
    if attribute:
        sql += " AND extra LIKE ?"
        params.append(f'%"{attribute}"%')
    if mpn_like:
        sql += " AND mfr LIKE ?"
        params.append(f"%{mpn_like}%")
    if extra_like:
        sql += " AND extra LIKE ?"
        params.append(extra_like)
    sql += " ORDER BY basic DESC, stock DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(f"file:{path}?immutable=1", uri=True, timeout=10) as conn:
        return conn.execute(sql, params).fetchall()


def _unit_price(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        breaks = json.loads(raw)
    except ValueError:
        return None
    return breaks[0].get("price") if breaks else None


def resolve(value: str, footprint: str = "") -> Match | None:
    """Find the catalogue part a BOM line names, or ``None`` when it cannot be determined."""
    quantity = parse_value(value)
    package = package_hint(footprint)

    if quantity is None:
        # Not a value: try the part number itself (G5LE-14, ULN2803A, SS34, LM2596S-5.0).
        text = (value or "").strip()
        if len(text) < 4 or not re.search(r"\d", text):
            return None
        hits = [
            row for row in _rows(package, limit=200, mpn_like=text)
            if text.upper() in str(row[1]).upper()
        ]
        if not hits:
            return None
        row = hits[0]
        return Match(f"C{row[0]}", row[1], row[2], bool(row[4]), row[3], _unit_price(row[5]), "part number")

    candidates: list[tuple[tuple, Match]] = []
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(package, limit=2000,
                                                            attribute=quantity["attr"]):
        try:
            attrs = json.loads(extra or "{}").get("attributes") or {}
        except ValueError:
            continue
        raw = attrs.get(quantity["attr"])
        if not raw:
            continue
        if not _close(_normalise_unit(raw), quantity["value"]):
            continue
        if quantity.get("rating"):
            # A stated rating must appear in the attributes (voltage for capacitors, current for fuses).
            ratings = [
                _normalise_unit(v) for k, v in attrs.items()
                if quantity["rating_unit"] == "V" and "voltage" in k.lower()
                or quantity["rating_unit"] == "A" and ("current" in k.lower() or "amp" in k.lower())
            ]
            if not any(r and _close(r, quantity["rating"]) for r in ratings):
                continue
        match = Match(f"C{lcsc}", mfr, pkg, bool(basic), stock, _unit_price(price),
                      f"{quantity['attr']} {value}")
        # Rank: basic first, then stock, then exact package match.
        candidates.append(((not match.basic, -match.stock, match.package != package), match))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    best = candidates[0][1]
    return best


def resolve_workspace(ws: Any) -> dict[str, Any]:
    """Fill in LCSC part numbers for a workspace's BOM lines, from the catalogue.

    Writes the code onto every symbol that uses the part, so the BOM, the schematic and the
    board all carry it. Returns a report naming what resolved and what did not — an unresolved
    line stays blank rather than being guessed.
    """
    from .schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    placements = [p for p in doc.placements() if not p.reference.startswith("#")]
    # Group the same way the BOM does, so one lookup fills every reference using the part.
    lines: dict[tuple[str, str], list[str]] = {}
    for placement in placements:
        key = (placement.value.strip(), placement.footprint.strip())
        lines.setdefault(key, []).append(placement.reference)

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for (value, footprint), refs in sorted(lines.items()):
        if any((p.properties or {}).get("LCSC") for p in placements if p.reference in refs):
            continue  # already sourced
        match = resolve(value, footprint) or resolve_connector(footprint) or resolve_fuse(value, footprint)
        if match is None:
            unresolved.append({"value": value, "footprint": footprint, "refs": refs})
            continue
        for ref in refs:
            doc.set_symbol_property(ref, "LCSC", match.lcsc)
            doc.set_symbol_property(ref, "MPN", match.mpn)
        resolved.append({"value": value, "refs": refs, **match.to_dict()})

    if resolved:
        doc.save()
    return {"resolved": resolved, "unresolved": unresolved,
            "resolved_lines": len(resolved), "unresolved_lines": len(unresolved)}


_CONNECTOR = re.compile(r"_(?P<rows>\d)x(?P<pos>\d+)_")
_PITCH = re.compile(r"P(?P<pitch>\d+(?:\.\d+)?)mm")


def resolve_connector(footprint: str) -> Match | None:
    """Match a connector by its footprint: pitch and position count.

    Connector values in a BOM are usually net names ("OUT1"), not part numbers, so the footprint
    is the only evidence available. A part is accepted only when the pitch and the number of
    positions both match exactly.
    """
    name = footprint or ""
    geometry = _CONNECTOR.search(name)
    pitch = _PITCH.search(name)
    if not geometry or not pitch:
        return None
    positions = int(geometry.group("pos"))
    if int(geometry.group("rows")) != 1:
        return None  # dual-row parts are a different family; do not guess
    want_pitch = float(pitch.group("pitch"))
    is_terminal = "TerminalBlock" in name

    best: Match | None = None
    # Filter on the exact pitch in SQL: the catalogue is large and stock-ordering alone buries
    # the right family behind thousands of unrelated parts.
    pitch_text = f"{want_pitch:.2f}".rstrip("0").rstrip(".") + "mm"
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(
        None, limit=4000, attribute="Pitch", extra_like=f'%"Pitch": "{pitch_text}"%'
    ):
        try:
            attrs = json.loads(extra or "{}").get("attributes") or {}
            cats = json.loads(extra or "{}").get("category") or {}
        except ValueError:
            continue
        if not _close(_millimetres(attrs.get("Pitch")), want_pitch):
            continue
        if is_terminal and "terminal" not in str(cats.get("name2", "")).lower():
            continue
        positions_attr = next(
            (v for k, v in attrs.items() if k.lower() in ("number of positions", "positions", "pin/pin count")),
            None,
        )
        if positions_attr and str(positions_attr).strip() != str(positions):
            continue
        if not positions_attr and f"-{positions}P" not in mfr:
            continue
        candidate = Match(f"C{lcsc}", mfr, pkg, bool(basic), stock, _unit_price(price),
                          f"footprint pitch {want_pitch}mm, {positions} positions")
        if best is None or (candidate.basic and not best.basic) or (
            candidate.basic == best.basic and candidate.stock > best.stock
        ):
            best = candidate
    return best


def resolve_fuse(value: str, footprint: str) -> Match | None:
    """A fuse rating ("2A") matched by current in the package the footprint names."""
    amount = parse_value(value)
    if not amount or amount["attr"] != "Current":
        return None
    package = package_hint(footprint)
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(package, limit=2000):
        try:
            payload = json.loads(extra or "{}")
        except ValueError:
            continue
        cats = payload.get("category") or {}
        if "fuse" not in str(cats.get("name1", "")).lower() and "fuse" not in str(cats.get("name2", "")).lower():
            continue
        for key, raw in (payload.get("attributes") or {}).items():
            if "current" not in key.lower():
                continue
            if _close(_normalise_unit(str(raw)), amount["value"]):
                return Match(f"C{lcsc}", mfr, pkg, bool(basic), stock, _unit_price(price),
                             f"fuse {value} in {package or 'any package'}")
    return None
