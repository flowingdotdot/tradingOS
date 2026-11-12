// Signal Inspector — generic for any strategy that records an inspection.
// Funnel (sequential) and per-condition pass rates (independent) are shown separately on purpose.
import { useMemo, useState } from "react";
import type { Inspection, InspectionCandidate, StageResult } from "../api";
import { fmtNum, fmtPct, fmtTs } from "../format";

const SHORT: Record<string, string> = {
  PASS: "P",
  FAIL: "F",
  NOT_APPLICABLE: "–",
  NOT_EVALUATED: "·",
  PENDING: "…",
};
const CLS: Record<string, string> = {
  PASS: "st-pass",
  FAIL: "st-fail",
  NOT_APPLICABLE: "st-na",
  NOT_EVALUATED: "st-ne",
  PENDING: "st-pend",
};
const NE: StageResult = { status: "NOT_EVALUATED", value: null, reason: "" };

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v !== "object") return String(v);
  return Object.entries(v as Record<string, unknown>)
    .map(([k, x]) => {
      if (typeof x === "number") {
        if (k.endsWith("_ts") || k === "ts") return `${k}=${fmtTs(x)}`;
        return `${k}=${Number.isInteger(x) ? x : fmtNum(x, Math.abs(x) < 10 ? 3 : 1)}`;
      }
      return `${k}=${String(x)}`;
    })
    .join(", ");
}

export function InspectorPanel({
  inspection,
  onFocus,
}: {
  inspection: Inspection;
  onFocus?: (range: { from: number; to: number }) => void;
}) {
  const [outcome, setOutcome] = useState<string>("signal");
  const [selected, setSelected] = useState<InspectionCandidate | null>(null);
  const cands = inspection.candidates ?? [];
  const shown = useMemo(
    () => (outcome === "(all)" ? cands : cands.filter((c) => c.outcome === outcome)).slice(0, 400),
    [cands, outcome],
  );
  const stage = (c: InspectionCandidate, id: string) => c.stages[id] ?? NE;

  return (
    <div className="inspector">
      <p className="fine">
        {inspection.n_candidates} candidates → {inspection.n_signals} signals. Status: <b className="st-pass">P</b> pass ·{" "}
        <b className="st-fail">F</b> fail · <b className="st-na">–</b> not applicable · <b className="st-ne">·</b> not evaluated
        (an earlier stage failed) · <b className="st-pend">…</b> pending at end of data.
      </p>
      <div className="grid-2 even">
        <div>
          <h3>Funnel — sequential (where do candidates stop?)</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stage</th><th className="r">Entered</th><th className="r">Pass</th><th className="r">N/A</th>
                  <th className="r">Fail</th><th className="r">Pend.</th><th className="r">Continue</th><th>Fail reasons</th>
                </tr>
              </thead>
              <tbody>
                {inspection.funnel.map((f) => (
                  <tr key={f.stage} title={inspection.stages.find((s) => s.id === f.stage)?.description}>
                    <td>{f.label}</td>
                    <td className="r">{f.entered}</td>
                    <td className="r pos">{f.passed}</td>
                    <td className="r dim">{f.not_applicable}</td>
                    <td className="r neg">{f.failed}</td>
                    <td className="r">{f.pending}</td>
                    <td className="r"><b>{f.continue}</b></td>
                    <td className="dim">{Object.entries(f.fail_reasons).map(([k, v]) => `${k} ${v}`).join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3>Conditions — independent pass rate (how restrictive is each one?)</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Condition</th><th className="r">Evaluated</th><th className="r">Pass</th><th className="r">Fail</th>
                  <th className="r">Pass rate</th><th className="r">Not eval.</th><th className="r">N/A</th>
                </tr>
              </thead>
              <tbody>
                {inspection.conditions.map((c) => (
                  <tr key={c.stage} title={c.independent ? "evaluated for every impulse, regardless of other conditions" : "only evaluated for candidates that reached this stage — rate is conditional on earlier stages"}>
                    <td>{c.label}{c.independent ? "" : <span className="dim"> *</span>}</td>
                    <td className="r">{c.evaluated}</td>
                    <td className="r pos">{c.passed}</td>
                    <td className="r neg">{c.failed}</td>
                    <td className="r">{fmtPct(c.pass_rate_pct, 1)}</td>
                    <td className="r dim">{c.not_evaluated}</td>
                    <td className="r dim">{c.not_applicable}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="fine">* conditional: only candidates that reached the stage were evaluated, so the rate is not comparable with the unconditional ones.</p>
        </div>
      </div>

      {cands.length > 0 && (
        <>
          <div className="tabs">
            <span className="dim fine">Candidates by outcome:</span>
            {["(all)", ...Object.keys(inspection.outcomes)].map((o) => (
              <button key={o} className={o === outcome ? "active" : ""} onClick={() => setOutcome(o)}>
                {o} {o === "(all)" ? cands.length : inspection.outcomes[o]}
              </button>
            ))}
          </div>
          <div className="table-wrap tall">
            <table className="matrix">
              <thead>
                <tr>
                  <th>#</th><th>Candidate</th><th>Occurred (UTC)</th><th>Decision</th><th>Outcome</th>
                  {inspection.stages.map((s) => <th key={s.id} title={`${s.label} — ${s.description}`}>{s.id.slice(0, 9)}</th>)}
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id} className={selected?.id === c.id ? "sel" : ""} onClick={() => {
                    setSelected(c);
                    onFocus?.({ from: c.ts, to: c.decision_ts ?? c.known_ts });
                  }}>
                    <td>{c.id}</td>
                    <td>{c.label}</td>
                    <td>{fmtTs(c.ts)}</td>
                    <td>{fmtTs(c.decision_ts)}</td>
                    <td className={c.outcome === "signal" ? "pos" : ""}>{c.outcome}</td>
                    {inspection.stages.map((s) => {
                      const r = stage(c, s.id);
                      return <td key={s.id} className={`cell ${CLS[r.status]}`} title={`${s.label}: ${r.status}${r.reason ? ` — ${r.reason}` : ""}${r.value ? `\n${fmtValue(r.value)}` : ""}`}>{SHORT[r.status]}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown.length < (outcome === "(all)" ? cands.length : inspection.outcomes[outcome] ?? 0) && <p className="fine">showing first {shown.length}</p>}
        </>
      )}

      {selected && (
        <div className="cand-detail">
          <h3>Candidate #{selected.id} — {selected.label} · occurred {fmtTs(selected.ts)} · known {fmtTs(selected.known_ts)} · outcome {selected.outcome}</h3>
          <table>
            <thead><tr><th>Stage</th><th>Status</th><th>Computed values</th><th>Reason</th></tr></thead>
            <tbody>
              {inspection.stages.map((s) => {
                const r = stage(selected, s.id);
                return (
                  <tr key={s.id}>
                    <td>{s.label}</td>
                    <td className={CLS[r.status]}>{r.status}</td>
                    <td className="mono">{fmtValue(r.value)}</td>
                    <td className="dim">{r.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {selected.evidence.length > 0 && (
            <>
              <h3>Inputs used — occurrence vs. recognition time</h3>
              <table>
                <thead><tr><th>Input</th><th>TF</th><th>Occurred (drawn)</th><th>Known (usable from)</th><th>≤ decision {fmtTs(selected.decision_ts)}</th></tr></thead>
                <tbody>
                  {selected.evidence.map((e, i) => {
                    const ok = selected.decision_ts != null && e.known_ts <= selected.decision_ts;
                    return (
                      <tr key={i}>
                        <td>{e.what}</td><td>{e.tf}</td><td>{fmtTs(e.ts)}</td><td>{fmtTs(e.known_ts)}</td>
                        <td className={ok ? "pos" : "neg"}>{ok ? "✓" : "✗ LOOK-AHEAD"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
