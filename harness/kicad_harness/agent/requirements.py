"""Turn the brief into countable requirements, so leaving things out is a failure.

ERC only judges what is drawn: a schematic that omits half the parts has nothing to be wrong
about, and parity between a schematic and a board only proves the two agree — they can agree on
being incomplete. What the user asked for is the yardstick, so this module extracts the countable
demands from the brief ("16 relays", "a Raspberry Pi socket", "Phoenix terminal blocks") and
checks them against the parts that actually exist.

Extraction is deterministic (numbers plus a small vocabulary of part nouns) so it can be tested
and reasoned about; the stored copy lives in ``project.json`` and can be corrected by hand.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# noun in the brief -> (label, predicate over a part's ref/value/footprint)
# A part is a dict with ref, value and footprint.
Matcher = tuple[str, "callable"]

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "twenty": 20, "thirty": 30, "forty": 40,
}

# Each entry: (regex of nouns, human label, predicate). Order matters: the first match wins.
_VOCABULARY: list[tuple[str, str, Any]] = [
    (r"relays?", "relay", lambda p: p["ref"].startswith("K") or "relay" in p["hay"].lower()),
    (
        r"(?:phoenix|screw[ -]?terminal|terminal block|terminal blocks|connectors?)",
        "connector",
        lambda p: p["ref"].startswith("J") or "terminal" in p["hay"].lower() or "phoenix" in p["hay"].lower(),
    ),
    (r"resistors?", "resistor", lambda p: p["ref"].startswith("R") or "resistor" in p["hay"].lower()),
    (r"capacitors?", "capacitor", lambda p: p["ref"].startswith("C") or "capacitor" in p["hay"].lower()),
    (r"(?:leds?|light emitting)", "led", lambda p: "led" in p["hay"].lower()),
    (r"(?:diodes?)", "diode", lambda p: p["ref"].startswith("D") or "diode" in p["hay"].lower()),
    (r"(?:drivers?|darlington|uln)", "driver", lambda p: p["ref"].startswith("U") or "uln" in p["hay"].lower()),
    (r"(?:fuses?)", "fuse", lambda p: p["ref"].startswith("F") or "fuse" in p["hay"].lower()),
    (r"(?:inductors?|chokes?)", "inductor", lambda p: p["ref"].startswith("L") or "inductor" in p["hay"].lower()),
    (
        r"(?:raspberry pi\w*|pi 3|pi3|40[ -]?pin\w*|gpio header|header socket)",
        "host board / header",
        lambda p: p["ref"].startswith("J") and (
            "pin" in p["hay"].lower() or "header" in p["hay"].lower() or "socket" in p["hay"].lower()
        ),
    ),
]

_NUMBER = r"(\d{1,3}|" + "|".join(_WORD_NUMBERS) + r")"


def _as_int(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


def derive(brief: str) -> list[dict[str, Any]]:
    """Countable requirements stated in ``brief``.

    Only statements of the form "<number> <part noun>" become requirements, which is how people
    actually specify quantities. Everything else is left to the reviewer agent.
    """
    text = (brief or "").lower()
    found: dict[str, dict[str, Any]] = {}
    for nouns, label, _predicate in _VOCABULARY:
        pattern = re.compile(_NUMBER + r"\s+(?:x\s+)?(?:\w+\s+){0,2}?" + nouns, re.IGNORECASE)
        for match in pattern.finditer(text):
            count = _as_int(match.group(1))
            if not count:
                continue
            existing = found.get(label)
            # Keep the largest count stated for a kind ("16 relays", later "the 16 relays").
            if existing is None or count > existing["min_count"]:
                found[label] = {"id": f"need-{label}", "kind": label, "min_count": count}
    return sorted(found.values(), key=lambda r: r["kind"])


def _part_haystack(part: dict[str, Any]) -> dict[str, str]:
    return {
        "ref": str(part.get("ref") or ""),
        "value": str(part.get("value") or ""),
        "footprint": str(part.get("footprint") or ""),
        "hay": " ".join(str(part.get(k) or "") for k in ("ref", "value", "footprint")),
    }


def check(requirements: Iterable[dict[str, Any]], parts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check each requirement against the parts that exist.

    Returns one row per requirement with ``required``, ``found`` and ``ok``. A part counts once
    per kind, so 16 relays cannot also satisfy "16 connectors".
    """
    prepared = [_part_haystack(p) for p in parts if p.get("ref") and not str(p["ref"]).startswith("#")]
    results: list[dict[str, Any]] = []
    for requirement in requirements:
        kind = requirement.get("kind", "")
        predicate = next((pred for _nouns, label, pred in _VOCABULARY if label == kind), None)
        required = int(requirement.get("min_count") or 0)
        if predicate is None:
            results.append({"id": requirement.get("id", kind), "kind": kind, "required": required,
                            "found": 0, "ok": False, "detail": f"unknown requirement kind {kind!r}"})
            continue
        found = sum(1 for part in prepared if predicate(part))
        results.append({
            "id": requirement.get("id", kind),
            "kind": kind,
            "required": required,
            "found": found,
            "ok": found >= required,
            "detail": f"{found} of {required} {kind}",
        })
    return results


def gaps(requirements: Iterable[dict[str, Any]], parts: Iterable[dict[str, Any]]) -> list[str]:
    """Human-readable shortfalls, for a failure detail or a retry prompt."""
    return [
        f"{row['kind']}: found {row['found']}, brief asks for {row['required']}"
        for row in check(requirements, parts)
        if not row["ok"]
    ]
