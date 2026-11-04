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
          mpn_like: str | None = None, extra_like: str | None = None,
          basic_only: bool = False) -> Iterable[tuple]:
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
    if basic_only:
        sql += " AND basic = 1"
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


@lru_cache(maxsize=512)
def resolve(value: str, footprint: str = "", *, basic_only: bool = False) -> Match | None:
    """Find the catalogue part a BOM line names, or ``None`` when it cannot be determined.

    ``basic_only`` restricts the search to JLCPCB *basic* parts — the ones with no extended-part
    fee. It is used both when choosing a part and when auditing an existing choice.
    """
    quantity = parse_value(value)
    package = package_hint(footprint)

    if quantity is None:
        # Not a value: try the part number itself (G5LE-14, ULN2803A, SS34, LM2596S-5.0).
        text = (value or "").strip()
        if len(text) < 4 or not re.search(r"\d", text):
            return None
        hits = [
            row for row in _rows(package, limit=200, mpn_like=text, basic_only=basic_only)
            if text.upper() in str(row[1]).upper()
        ]
        if not hits:
            return None
        row = hits[0]
        return Match(f"C{row[0]}", row[1], row[2], bool(row[4]), row[3], _unit_price(row[5]), "part number")

    candidates: list[tuple[tuple, Match]] = []
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(package, limit=2000,
                                                            attribute=quantity["attr"],
                                                            basic_only=basic_only):
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


@lru_cache(maxsize=512)
def _match_line(value: str, footprint: str, *, basic_only: bool = False) -> Match | None:
    """The catalogue part for one BOM line, trying value, then connector geometry, then fuse."""
    return (
        resolve(value, footprint, basic_only=basic_only)
        or resolve_connector(footprint, basic_only=basic_only)
        or resolve_fuse(value, footprint, basic_only=basic_only)
    )


def _line_groups(doc: Any) -> dict[tuple[str, str], list[str]]:
    """``(value, footprint) -> refs``, grouped exactly as the BOM groups lines."""
    groups: dict[tuple[str, str], list[str]] = {}
    for placement in doc.placements():
        if placement.reference.startswith("#"):  # power flags are not parts
            continue
        groups.setdefault((placement.value.strip(), placement.footprint.strip()), []).append(
            placement.reference
        )
    return groups


def _current_lcsc(doc: Any, refs: list[str]) -> str:
    for placement in doc.placements():
        if placement.reference in refs:
            code = (placement.properties or {}).get("LCSC")
            if code:
                return code
    return ""


def resolve_workspace(ws: Any, *, prefer_basic: bool = True, resourced: bool = True) -> dict[str, Any]:
    """Fill in — or improve — the LCSC part numbers on a workspace's BOM lines.

    Sourcing is **basic-first**: JLCPCB charges a reel fee for every *extended* part, so a part
    is only chosen as extended when no basic part satisfies the same value, package and rating.
    With ``resourced`` (the default) already-sourced lines are re-examined too: an extended part
    is replaced by an equivalent basic one when the catalogue has one, and otherwise left alone.

    Writes the codes onto every symbol using the part, so the BOM, the schematic and the board
    agree. Returns a report naming what resolved, what was switched to a basic part, and what
    could not be determined at all — an unresolved line stays blank rather than being guessed.
    """
    from .schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    resolved: list[dict[str, Any]] = []
    switched: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for (value, footprint), refs in sorted(_line_groups(doc).items()):
        current = _current_lcsc(doc, refs)
        if current:
            if not (resourced and prefer_basic):
                continue
            if _tier_of(current) == "basic":
                continue  # already the cheapest tier there is
            basic = _match_line(value, footprint, basic_only=True)
            if basic is None or basic.lcsc.upper() == current.upper():
                continue  # no basic equivalent: keep the extended part
            for ref in refs:
                doc.set_symbol_property(ref, "LCSC", basic.lcsc)
                doc.set_symbol_property(ref, "MPN", basic.mpn)
            switched.append({"value": value, "refs": refs, "from": current, **basic.to_dict()})
            continue

        match = _match_line(value, footprint, basic_only=prefer_basic) or (
            None if prefer_basic else _match_line(value, footprint)
        )
        if match is None and prefer_basic:
            # Nothing basic fits; an extended part still beats an unsourced line.
            match = _match_line(value, footprint)
        if match is None:
            unresolved.append({"value": value, "footprint": footprint, "refs": refs})
            continue
        for ref in refs:
            doc.set_symbol_property(ref, "LCSC", match.lcsc)
            doc.set_symbol_property(ref, "MPN", match.mpn)
        resolved.append({"value": value, "refs": refs, **match.to_dict()})

    if resolved or switched:
        doc.save()
    return {
        "resolved": resolved,
        "switched": switched,
        "unresolved": unresolved,
        "resolved_lines": len(resolved),
        "switched_lines": len(switched),
        "unresolved_lines": len(unresolved),
    }


def _catalogue_row(lcsc: str) -> dict[str, Any] | None:
    """One part as the catalogue knows it: manufacturer, package, tier, stock, price, category."""
    path = parts.db_path()
    key = (lcsc or "").strip().upper().lstrip("C")
    if path is None or not key.isdigit():
        return None
    with sqlite3.connect(f"file:{path}?immutable=1", uri=True, timeout=10) as conn:
        row = conn.execute(
            "SELECT mfr, package, stock, basic, price, extra FROM components WHERE lcsc = ?",
            (int(key),),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[5] or "{}")
    category = payload.get("category") or {}
    return {
        "lcsc": f"C{key}",
        "mpn": row[0],
        "package": row[1],
        "stock": row[2],
        "basic": bool(row[3]),
        "tier": "basic" if row[3] else "extended",
        "unit_price": _unit_price(row[4]),
        "category": category.get("name2") or category.get("name1") or "",
    }


def _tier_of(lcsc: str) -> str | None:
    row = _catalogue_row(lcsc)
    return row["tier"] if row else None


@lru_cache(maxsize=1)
def _basic_categories() -> dict[str, int]:
    """How many *basic* parts JLCPCB has per category — the evidence behind "no basic part"."""
    path = parts.db_path()
    if path is None:
        return {}
    with sqlite3.connect(f"file:{path}?immutable=1", uri=True, timeout=10) as conn:
        rows = conn.execute(
            """SELECT COALESCE(json_extract(extra, '$.category.name2'),
                               json_extract(extra, '$.category.name1'), ''), COUNT(*)
               FROM components WHERE basic = 1 GROUP BY 1"""
        ).fetchall()
    return {str(name): count for name, count in rows}


def _why_no_basic(value: str, footprint: str, current: dict[str, Any] | None) -> str:
    """A sentence saying why an extended line has no basic replacement."""
    category = (current or {}).get("category") or ""
    package = (current or {}).get("package") or package_hint(footprint) or "this package"
    what = value or "this value"
    if not category:
        # The catalogue has no metadata for this part, so there is nothing to reason about
        # beyond the value and package themselves.
        return f"no basic part matches {what} in {package}"
    available = _basic_categories().get(category, 0)
    if available == 0:
        return f"JLCPCB's basic library has no {category} at all"
    return f"basic {category} parts exist, but none is {what} in {package}"


def basic_report(ws: Any) -> dict[str, Any]:
    """Basic-vs-extended status of a design's BOM, with the reason for every extended part.

    "Use as many basic parts as possible" is only answerable if you can see, per part, whether a
    basic replacement exists. Each line reports its tier, the basic alternative the catalogue
    offers (if any), and — when there is none — why not.
    """
    from .schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    budget = power_budget(doc)
    lines: list[dict[str, Any]] = []
    for (value, footprint), refs in sorted(_line_groups(doc).items()):
        lcsc = _current_lcsc(doc, refs)
        current = _catalogue_row(lcsc) if lcsc else None
        tier = (current or {}).get("tier")
        item: dict[str, Any] = {
            "value": value,
            "footprint": footprint,
            "refs": sorted(refs),
            "qty": len(refs),
            "lcsc": lcsc,
            "mpn": (current or {}).get("mpn", ""),
            "package": (current or {}).get("package", ""),
            "category": (current or {}).get("category", ""),
            "tier": tier,
            "basic": tier == "basic",
            "basic_alternative": None,
            "reason": "",
            "limit": "",
        }
        if tier != "basic":
            alternative = _match_line(value, footprint, basic_only=True)
            if alternative is not None and alternative.lcsc.upper() != lcsc.upper():
                item["basic_alternative"] = alternative.to_dict()
            else:
                item["reason"] = _why_no_basic(value, footprint, current)
                item["limit"] = _measured_limit(value, budget)
        lines.append(item)

    basic = [l for l in lines if l["basic"]]
    extended = [l for l in lines if not l["basic"]]
    return {
        "lines": lines,
        "line_count": len(lines),
        "basic_lines": len(basic),
        "extended_lines": len(extended),
        "basic_units": sum(l["qty"] for l in basic),
        "total_units": sum(l["qty"] for l in lines),
        "power": budget,
        "replaceable": [l for l in lines if l["basic_alternative"]],
        "extended_parts": [
            {"value": l["value"], "lcsc": l["lcsc"], "mpn": l["mpn"], "category": l["category"],
             "reason": l["reason"], "limit": l["limit"]}
            for l in extended
        ],
    }


_CONNECTOR = re.compile(r"_(?P<rows>\d)x(?P<pos>\d+)_")
_PITCH = re.compile(r"P(?P<pitch>\d+(?:\.\d+)?)mm")


def _geometry(attrs: dict[str, Any]) -> tuple[int, int] | None:
    """``(rows, positions_per_row)`` from whichever vocabulary the part uses.

    Male parts say "Pin Structure"/"Number of Pins"; female parts say "Holes
    Structure"/"Number of Holes". The structure string carries the row count, so it is preferred;
    anything that cannot be pinned down returns ``None`` and is rejected rather than assumed to
    fit — assuming let a 1x20 socket pass for a 2x20 footprint.
    """
    structure = str(attrs.get("Pin Structure") or attrs.get("Holes Structure") or "")
    match = re.match(r"^(\d+)\s*[x*]\s*(\d+)", structure)
    if match:
        return int(match.group(1)), int(match.group(2))

    raw_rows = str(attrs.get("Number of Rows") or "").strip()
    rows = int(raw_rows) if raw_rows.isdigit() else (2 if "双" in raw_rows else None)
    if not rows:
        return None
    per_row = str(attrs.get("Pin Number in each Row") or attrs.get("Number of PINs Per Row") or "")
    per_row = per_row.strip().rstrip("P")
    if per_row.isdigit():
        return rows, int(per_row)
    total = str(attrs.get("Number of Pins") or attrs.get("Number of Holes") or "").strip().rstrip("P")
    if total.isdigit() and int(total) % rows == 0:
        return rows, int(total) // rows
    return None


def _gender_ok(footprint: str, category: str) -> bool:
    """A socket footprint needs a female part; a header footprint needs a pin header.

    Substituting one for the other would silently order the wrong hardware, so a mismatch is
    treated as no match at all.
    """
    name = footprint or ""
    cat = (category or "").lower()
    if "PinSocket" in name:
        return "female" in cat or "socket" in cat
    if "PinHeader" in name or "Pin_Header" in name:
        return "female" not in cat and "socket" not in cat and cat != ""
    return True


@lru_cache(maxsize=512)
def resolve_connector(footprint: str, *, basic_only: bool = False) -> Match | None:
    """Match a connector by its footprint: pitch, row count and positions per row.

    Connector values in a BOM are usually net names ("OUT1"), not part numbers, so the footprint
    is the only evidence available. Pitch, rows *and* positions must all match, and a socket
    footprint only accepts a female part.
    """
    name = footprint or ""
    geometry = _CONNECTOR.search(name)
    pitch = _PITCH.search(name)
    if not geometry or not pitch:
        return None
    want_rows = int(geometry.group("rows"))
    want_pos = int(geometry.group("pos"))
    want_pitch = float(pitch.group("pitch"))
    is_terminal = "TerminalBlock" in name

    pitch_text = f"{want_pitch:.2f}".rstrip("0").rstrip(".") + "mm"
    best: Match | None = None
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(
        None, limit=6000, attribute="Pitch", extra_like=f'%"Pitch": "{pitch_text}"%',
        basic_only=basic_only,
    ):
        try:
            payload = json.loads(extra or "{}")
        except ValueError:
            continue
        attrs = payload.get("attributes") or {}
        cats = payload.get("category") or {}
        category = f"{cats.get('name1', '')} {cats.get('name2', '')}"
        if not _close(_millimetres(attrs.get("Pitch")), want_pitch):
            continue
        if is_terminal and "terminal" not in category.lower():
            continue
        if not is_terminal and not _gender_ok(name, category):
            continue
        if _geometry(attrs) != (want_rows, want_pos):
            continue
        candidate = Match(f"C{lcsc}", mfr, pkg, bool(basic), stock, _unit_price(price),
                          f"footprint {want_pitch}mm, {want_rows}x{want_pos}")
        if best is None or (candidate.basic and not best.basic) or (
            candidate.basic == best.basic and candidate.stock > best.stock
        ):
            best = candidate
    return best


@lru_cache(maxsize=128)
def resolve_fuse(value: str, footprint: str, *, basic_only: bool = False) -> Match | None:
    """A fuse rating ("2A") matched by current in the package the footprint names."""
    amount = parse_value(value)
    if not amount or amount["attr"] != "Current":
        return None
    package = package_hint(footprint)
    for lcsc, mfr, pkg, stock, basic, price, extra in _rows(package, limit=2000,
                                                            basic_only=basic_only):
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


# --------------------------------------------------------------------------------------
# Measured constraints behind "no basic part fits"
#
# A swap is only rejected on numbers: the rail load measured from the schematic, compared with
# the datasheet limit of the part that would replace it. Guessed figures are not evidence.
COIL_RESISTANCE_OHM = 63.0     # Omron G5LE-14 5VDC coil resistance (G5LE datasheet)
PI3_STRESS_A = 0.8             # Raspberry Pi 3 Model B, 5 V: 0.5-0.8 A under stress
PI3_MAX_A = 1.34               # and 1.34 A maximum (Raspberry Pi published measurements)
LM2596_CIN_UF = 470.0          # TI LM2596 fixed-5 V application: C_IN 470-680 uF
LM2596_COUT_UF = 220.0         # ... and C_OUT 220 uF


def power_budget(doc: Any, rail: str = "+5V") -> dict[str, Any]:
    """The ``rail`` load, measured from the schematic rather than assumed.

    Every part that hangs on the rail is counted and attributed: relay coils at their datasheet
    current, and a Raspberry Pi 3 sitting on the 40-pin header at its published draw. Comparing
    that total with a candidate regulator's rating is what decides whether a swap is legitimate.
    """
    nets = doc.nets()
    on_rail = {m["ref"] for m in nets.get(rail, [])}
    relays = [
        p for p in doc.placements()
        if p.reference in on_rail and p.value.strip().upper().startswith("G5LE")
    ]
    coil_a = 5.0 / COIL_RESISTANCE_OHM
    coil_load = len(relays) * coil_a
    pi = any(
        p.reference.startswith("J") and p.reference in on_rail and "40-pin" in p.value.lower()
        for p in doc.placements()
    )
    pi_typ = PI3_STRESS_A if pi else 0.0
    pi_max = PI3_MAX_A if pi else 0.0
    return {
        "rail": rail,
        "relay_coils": len(relays),
        "coil_current_ma": round(coil_a * 1000, 1),
        "coil_load_a": round(coil_load, 3),
        "pi3_on_rail": pi,
        "pi3_stress_a": pi_typ,
        "pi3_max_a": pi_max,
        "load_stress_a": round(coil_load + pi_typ, 3),
        "load_max_a": round(coil_load + pi_max, 3),
    }


def _measured_limit(value: str, budget: dict[str, Any] | None) -> str:
    """The numbers that rule a basic replacement out for a part, or ``""`` when none apply."""
    if not budget:
        return ""
    key = (value or "").strip().upper()
    if key.startswith("LM2596"):
        return (
            "the basic XL1509-5.0E1 is rated 2 A against a measured {stress:.2f} A stress / "
            "{max:.2f} A worst case on {rail} ({coils} relay coils at {coil:.1f} mA plus a Pi 3); "
            "the basic 3 A TPS5430DDAR does fit, with a feedback divider (12k/3.9k gives 4.98 V) "
            "and a 10 nF boot capacitor"
        ).format(stress=budget["load_stress_a"], max=budget["load_max_a"], rail=budget["rail"],
                 coils=budget["relay_coils"], coil=budget["coil_current_ma"])
    if key in ("100UF/35V", "470UF/10V"):
        return (
            "the LM2596 datasheet calls for C_IN {cin:.0f} uF and C_OUT {cout:.0f} uF; the basic "
            "MLCC options are 22 uF/25 V, 47 uF/10 V and 100 uF/6.3 V — below the recommended "
            "capacitance, and 6.3 V leaves 1.3x margin on a 5 V rail"
        ).format(cin=LM2596_CIN_UF, cout=LM2596_COUT_UF)
    if key.startswith("ULN2803"):
        return (
            "the basic ULN2003ADR has 7 channels, so 16 relays need three SOIC-16 parts instead of "
            "two DIP-18: 79 mA per coil against a 500 mA rating makes it electrically fine, but it "
            "is a board re-layout, not a re-source"
        )
    return ""
