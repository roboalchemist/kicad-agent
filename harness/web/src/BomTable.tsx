/** Bill of materials: one row per orderable part, with every reference that uses it.
 *
 * Soldering a board means ordering per *part*, not per component, so identical parts collapse
 * into a single line (R1, R2, R10) the way a real BOM does — including when the schematic labels
 * them differently: nineteen terminal blocks wired to OUT1…OUT16 share one part number and are
 * one line. Supplier columns always render, and the header states how much of the BOM is
 * basic-tier — JLCPCB charges a reel fee for every extended part, so "how many are basic" is a
 * cost question, not a cosmetic one.
 */

import { useEffect, useState } from "react";

import { api } from "./api.ts";
import type { BomBasicReport, BomResponse } from "./types.ts";

function money(value?: number | null): string {
  if (value == null) return "—";
  return `$${value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`;
}

function shortFootprint(footprint: string): string {
  const name = (footprint || "").split(":").pop() ?? "";
  return name || "—";
}

/** Distinct extended parts with the reason each has no basic replacement. */
function extendedReasons(report: BomBasicReport) {
  const byCode = new Map<
    string,
    { value: string; lcsc: string; category: string; reason: string; limit: string; qty: number }
  >();
  for (const line of report.lines) {
    if (line.basic) continue;
    const key = line.lcsc || `${line.value}|${line.footprint}`;
    const seen = byCode.get(key);
    if (seen) {
      seen.qty += line.qty;
      continue;
    }
    byCode.set(key, {
      value: line.value,
      lcsc: line.lcsc,
      category: line.category,
      reason: line.reason,
      limit: line.limit ?? "",
      qty: line.qty,
    });
  }
  return [...byCode.values()].sort((a, b) => b.qty - a.qty);
}

export function BomTable({ design }: { design: string }) {
  const [bom, setBom] = useState<BomResponse | null>(null);
  const [basic, setBasic] = useState<BomBasicReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .bom(design)
      .then((table) => live && setBom(table))
      .catch((e) => live && setError(String(e instanceof Error ? e.message : e)));
    // The audit scans the parts database per line, so it loads after the table rather than
    // holding the BOM hostage; until it arrives the table simply has no coverage badge.
    api
      .bomBasic(design)
      .then((audit) => live && setBasic(audit))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [design]);

  // The columns are part of the BOM's shape, not a property of the data: hiding them when no
  // part has been sourced yet makes the table look like it has no part numbers or prices at all.
  const hasSupplier = true;
  const tiers = bom?.tier_summary;
  const extended = basic ? extendedReasons(basic) : [];

  return (
    <div className="bom-view" data-testid="bom-view">
      <div className="bom-bar">
        <span className="badge badge-phase">BOM</span>
        <span className="muted" data-testid="bom-summary">
          {bom
            ? `${bom.line_count} line${bom.line_count === 1 ? "" : "s"} · ${bom.total_qty} part${
                bom.total_qty === 1 ? "" : "s"
              }${bom.source !== "none" ? ` · from the ${bom.source}` : ""}`
            : "loading…"}
        </span>
        {tiers && tiers.parts > 0 && (
          <span className="badge badge-ok" data-testid="bom-tier">
            {tiers.basic_parts} of {tiers.parts} parts basic
          </span>
        )}
      </div>

      {error && <div className="error-text">{error.slice(0, 300)}</div>}

      {bom && bom.lines.length === 0 && (
        <div className="bom-empty muted" data-testid="bom-empty">
          Nothing to order yet — this project has no parts. Draw a schematic (or place
          footprints) and they will appear here.
        </div>
      )}

      {bom && bom.lines.length > 0 && (
        <div className="bom-scroll">
          <table className="bom-table" data-testid="bom-table">
            <thead>
              <tr>
                <th>Refs</th>
                <th>Qty</th>
                <th>Value</th>
                <th>Footprint</th>
                {hasSupplier && <th>Part number</th>}
                {hasSupplier && <th>Tier</th>}
                {hasSupplier && <th>Stock</th>}
                {hasSupplier && <th>Unit</th>}
                {hasSupplier && <th>Line</th>}
              </tr>
            </thead>
            <tbody>
              {bom.lines.map((line) => (
                <tr key={line.refs[0] ?? `${line.value}|${line.footprint}|${line.lcsc}`}>
                  <td className="bom-refs">{line.refs.join(", ")}</td>
                  <td>{line.qty}</td>
                  <td title={line.values && line.values.length > 1 ? line.values.join(", ") : undefined}>
                    {line.value || "—"}
                    {line.values && line.values.length > 1 && (
                      <span className="bom-value-more" data-testid="bom-value-more">
                        +{line.values.length - 1}
                      </span>
                    )}
                  </td>
                  <td title={line.footprint}>{shortFootprint(line.footprint)}</td>
                  {hasSupplier && (
                    <td className="bom-lcsc">
                      {line.lcsc ? (
                        <a href={line.url || undefined} target="_blank" rel="noreferrer" title={line.mpn || line.lcsc}>
                          {line.mpn || line.lcsc}
                          {line.mpn && <span className="bom-lcsc-code">{line.lcsc}</span>}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  )}
                  {hasSupplier && (
                    <td>
                      {line.tier ? (
                        <span className={`badge ${line.tier === "basic" ? "badge-ok" : "badge-warn"}`}>
                          {line.tier}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  )}
                  {hasSupplier && <td>{line.stock ? line.stock.toLocaleString() : "—"}</td>}
                  {hasSupplier && <td>{money(line.unit_price)}</td>}
                  {hasSupplier && <td>{money(line.line_total)}</td>}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr data-testid="bom-total">
                <td className="bom-refs">Total</td>
                <td>{bom.total_qty}</td>
                <td colSpan={hasSupplier ? 2 : 1} />
                {hasSupplier && <td />}
                {hasSupplier && <td />}
                {hasSupplier && <td />}
                {hasSupplier && (
                  <td className="bom-total-price">
                    {bom.total_price > 0 ? money(bom.total_price) : "—"}
                  </td>
                )}
              </tr>
              {bom.unpriced_qty > 0 && (
                <tr className="bom-note" data-testid="bom-unpriced">
                  <td colSpan={9}>
                    {bom.unpriced_qty} part{bom.unpriced_qty === 1 ? "" : "s"} have no supplier part
                    number yet, so they are not in the total. Record an LCSC code per part (the agent
                    does this when it sources parts with find_parts / check_part) and the part number,
                    link and price fill in.
                  </td>
                </tr>
              )}
            </tfoot>
          </table>
        </div>
      )}

      {basic && basic.extended_lines > 0 && (
        <details className="bom-basic" data-testid="bom-basic">
          <summary>
            {extended.length} part{extended.length === 1 ? "" : "s"} can't be basic — why
            {basic.power && (
              <span className="bom-basic-rail">
                {" "}
                · {basic.power.rail} load measured at {basic.power.load_stress_a.toFixed(2)} A stress /{" "}
                {basic.power.load_max_a.toFixed(2)} A worst case
              </span>
            )}
          </summary>
          <ul>
            {extended.map((part) => (
              <li key={part.lcsc || part.value}>
                <span className="bom-basic-value">{part.value}</span>
                {part.lcsc && <span className="bom-lcsc-code">{part.lcsc}</span>}
                {" — "}
                {part.reason}
                {part.limit && <div className="bom-basic-limit">{part.limit}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
