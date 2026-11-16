"""Part sourcing: the JLCPCB catalogue and i2cdb datasheets.

The JLCPCB database is large and third-party; these tests skip when it isn't present rather
than downloading anything. i2cdb needs the CLI and a token, so those skip too.
"""

from __future__ import annotations

import pytest

from kicad_harness.engine import parts

needs_db = pytest.mark.skipif(not parts.available(), reason="no JLCPCB parts database here")
needs_i2cdb = pytest.mark.skipif(
    not parts.i2cdb_available() or not parts._token(), reason="no i2cdb CLI/token here"
)


def test_package_hint_reads_kicad_footprint_names() -> None:
    assert parts.package_hint("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm") == "SOIC-8"
    assert parts.package_hint("Resistor_SMD:R_0805_2012Metric") == "0805"
    assert parts.package_hint("Package_TO_SOT_SMD:SOT-23") == "SOT-23"


@needs_db
def test_search_returns_stock_price_and_tier() -> None:
    result = parts.search_jlc("NE555", limit=3)
    assert result["parts"], result
    p = result["parts"][0]
    assert p["lcsc"].startswith("C") and p["mpn"] and p["package"]
    assert p["tier"] in ("basic", "extended")
    assert p["stock"] > 0 and p["unit_price"] is not None


@needs_db
def test_get_by_lcsc_accepts_both_forms() -> None:
    a = parts.get_jlc("C7593")
    assert a is not None and a["mpn"] == "NE555DR"
    assert parts.get_jlc("7593")["lcsc"] == "C7593"
    assert parts.get_jlc("C0") is None


@needs_db
def test_bad_lcsc_is_rejected() -> None:
    with pytest.raises(parts.PartsError, match="not an LCSC code"):
        parts.get_jlc("resistor")


@needs_db
def test_basic_only_filters_to_basic_parts() -> None:
    result = parts.search_jlc(limit=5, basic_only=True)
    assert all(p["basic"] for p in result["parts"])


@needs_i2cdb
def test_i2cdb_search_requires_a_prefix() -> None:
    with pytest.raises(parts.PartsError, match="MPN prefix"):
        parts.search_i2cdb("")
    hits = parts.search_i2cdb("UCC287", limit=3)
    assert hits["parts"] and hits["parts"][0]["mpn"].startswith("UCC287")


@needs_db
def test_check_part_reports_usable_and_supplier_datasheet() -> None:
    """A stocked part is usable; when i2cdb lacks it, the JLC datasheet URL fills the gap."""
    result = parts.check_part("NE555DR", lcsc="C7593")
    assert result["usable_for_jlc"] is True
    assert result["checks"]["jlcpcb"]["matches"][0]["lcsc"] == "C7593"


@needs_db
def test_check_part_flags_a_part_jlc_does_not_stock() -> None:
    result = parts.check_part("ZZZNOTAPART999")
    assert result["usable_for_jlc"] is False
    assert any("JLCPCB" in n for n in result["notes"])


@needs_db
def test_category_search_finds_in_stock_parts() -> None:
    """Descriptions are empty in this catalogue, so categories are the way to browse by type."""
    cats = parts.categories("LED")
    assert cats and cats[0]["in_stock"] > 0
    hits = parts.search_jlc(category="LED", package="0805", limit=3)
    assert hits["parts"], hits
    assert all("0805" in (p["package"] or "") for p in hits["parts"])


@needs_db
def test_empty_result_explains_which_filter_emptied_it() -> None:
    """NE555 has no basic parts; the empty answer must say so instead of looking like 'no such part'."""
    result = parts.search_jlc("NE555", basic_only=True)
    assert result["count"] == 0
    reason = result["empty_reason"]
    assert reason["matching_any_stock"] > 0
    assert "basic_only=false" in reason["note"]


def test_i2cdb_search_passes_filters_and_derives_pdf_state(monkeypatch) -> None:
    captured: dict = {}

    def fake(args, timeout=60):
        captured["args"] = args
        return {"total": 2, "has_more": False, "items": [
            {"manufacturer": "ti", "manufacturer_name": "TI", "mpn": "A1",
             "category": "PMIC", "category_slug": "pmic", "package": "SOIC-8",
             "file_count": 1, "placeholder_count": 0},
            {"manufacturer": "ti", "manufacturer_name": "TI", "mpn": "A2",
             "category": "PMIC", "category_slug": "pmic", "package": "SOIC-8",
             "file_count": 1, "placeholder_count": 1},
        ]}

    monkeypatch.setattr(parts, "_i2cdb", fake)
    result = parts.search_i2cdb("A", 5, manufacturer="ti", package="SOIC-8",
                                has_pdf=True, attr=["Output Voltage=5V"], offset=10)
    args = captured["args"]
    assert args[:2] == ["parts", "search"] and "A" in args
    for flag in ("--manufacturer", "--package", "--has-pdf", "--attr", "--offset"):
        assert flag in args
    assert result["parts"][0]["id"] == "ti/A1"
    assert result["parts"][0]["has_pdf"] is True
    assert result["parts"][1]["has_pdf"] is False


def test_datasheet_page_search_ranks_pages_and_returns_text(monkeypatch) -> None:
    def fake_pages(mfg, mpn, pages=None):
        return {"mfg": mfg, "mpn": mpn, "page_count": 3, "pages": [
            {"n": 1, "text": "Absolute maximum ratings 50 V"},
            {"n": 2, "text": "Typical characteristics graph output current"},
            {"n": 3, "text": "Package drawing and thermal information graph"},
        ]}

    monkeypatch.setattr(parts, "datasheet_pages", fake_pages)
    result = parts.search_datasheet("ti", "X", "graph thermal")
    assert result["page_count"] == 3
    assert result["pages"] == [3]
    assert result["matches"][0]["page"] == 3
    assert "Package drawing" in result["text"]
