"""The PCB knowledge the harness already carries, made available to the agent and the UI.

The repo's ``.agents/rules`` (23 documents) and ``.agents/skills/pcb-design/reference`` (30
documents) are the design intelligence behind the harness. They used to live only in the agent's
static context; this module packages them so they can be:

- summarised into the per-step instructions (``digest``), with the source document named, so the
  workflow guidance is actually the repo's rules instead of a hand-written paraphrase;
- read in full on demand through the ``read_design_reference`` tool;
- listed in the global Settings page (id, title, source, section headings).

The markdown is copied into ``kicad_harness/knowledge`` so a deployment (which copies the package,
not the repo root) still has it. Keep it in sync with ``.agents`` when the rules change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent / "knowledge"
RULES = BASE / "rules"
REFERENCE = BASE / "reference"

# Which documents describe each phase, in priority order.
PHASE_TOPICS: dict[str, list[str]] = {
    "bom": ["pcb-bom-components", "pcb-dfm", "pcb-design-process"],
    "schematic": ["pcb-design-process", "pcb-bom-components", "pcb-power", "pcb-mixed-signal"],
    "board": [
        "pcb-placement",
        "pcb-routing",
        "pcb-power",
        "pcb-dfm",
        "pcb-copper-pours",
        "pcb-silkscreen",
        "pcb-emc",
        "pcb-thermal",
        "pcb-mechanical",
        "pcb-high-speed",
        "pcb-autorouter",
        "pcb-metrics",
        "pcb-test-debug",
        "pcb-stackup",
    ],
}

# A short, high-signal playbook per phase; the rest is one tool call away.
DIGEST_TOPICS: dict[str, list[str]] = {
    "bom": ["pcb-bom-components", "pcb-dfm"],
    "schematic": ["pcb-design-process", "pcb-power", "pcb-bom-components"],
    "board": ["pcb-placement", "pcb-routing", "pcb-power", "pcb-dfm", "pcb-copper-pours", "pcb-silkscreen"],
}

_BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?P<text>.+?)\s*$")


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """``[(heading, [bullet, ...])]`` for a rule document."""
    out: list[tuple[str, list[str]]] = []
    heading = ""
    bullets: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading or bullets:
                out.append((heading or "Overview", bullets))
            heading, bullets = line[3:].strip(), []
            continue
        match = _BULLET.match(line)
        if match:
            bullets.append(re.sub(r"\*\*(.+?)\*\*", r"\1", match.group("text")).strip())
    if heading or bullets:
        out.append((heading or "Overview", bullets))
    return out


def _doc_path(topic: str) -> Path | None:
    """Resolve a topic id such as ``pcb-routing`` or ``routing`` to a bundled markdown file."""
    key = (topic or "").strip().lower()
    key = key.removeprefix("pcb-")
    candidates = [
        RULES / f"pcb-{key}.md",
        REFERENCE / f"{key}.md",
        REFERENCE / key / "README.md",
    ]
    if not key.endswith(".md"):
        candidates.append(REFERENCE / f"{key}/index.md")
    for path in candidates:
        if path.is_file():
            return path
    # Nested reference notes (e.g. reliability/coatings.md) by stem.
    for path in REFERENCE.rglob("*.md"):
        if path.stem.lower() == key:
            return path
    return None


def read(topic: str) -> dict[str, Any] | None:
    """One document in full, or ``None`` when the topic is unknown."""
    path = _doc_path(topic)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    try:
        source = f".agents/rules/{path.relative_to(RULES)}"
    except ValueError:
        source = f".agents/skills/pcb-design/reference/{path.relative_to(REFERENCE)}"
    return {
        "id": path.stem,
        "title": _title(text, path.stem),
        "source": source,
        "headings": [heading for heading, _ in _sections(text) if heading],
        "text": text,
    }


def index() -> list[dict[str, Any]]:
    """Every bundled rule/reference with its source path and section headings."""
    out: list[dict[str, Any]] = []
    for path in sorted(RULES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        out.append({
            "id": path.stem,
            "title": _title(text, path.stem),
            "source": f".agents/rules/{path.name}",
            "headings": [heading for heading, _ in _sections(text) if heading],
            "kind": "rule",
        })
    for path in sorted(REFERENCE.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        out.append({
            "id": path.stem,
            "title": _title(text, path.stem),
            "source": f".agents/skills/pcb-design/reference/{path.relative_to(REFERENCE)}",
            "headings": [heading for heading, _ in _sections(text) if heading],
            "kind": "reference",
        })
    return out


def topic_ids() -> list[str]:
    return [item["id"] for item in index()]


def digest(phase: str, *, max_chars: int = 3500, bullets_per_section: int = 2,
           sections_per_topic: int = 2) -> str:
    """A cited, compact playbook for ``phase`` drawn from the real rule documents."""
    topics = DIGEST_TOPICS.get(phase, DIGEST_TOPICS["schematic"])
    lines: list[str] = ["Playbook (sourced from .agents/rules):"]
    used = 0
    for topic in topics:
        doc = read(topic)
        if doc is None:
            continue
        lines.append(f"[{doc['title']} — {doc['source']}]")
        shown = 0
        for heading, bullets in _sections(doc["text"]):
            if not bullets or shown >= sections_per_topic:
                continue
            lines.append(f"  {heading}: " + "; ".join(bullets[:bullets_per_section]))
            shown += 1
            used += 1
    lines.append(
        "Deeper detail: call read_design_reference(topic), e.g. "
        + ", ".join(f"'{topic}'" for topic in PHASE_TOPICS.get(phase, [])[:4])
        + "."
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text
