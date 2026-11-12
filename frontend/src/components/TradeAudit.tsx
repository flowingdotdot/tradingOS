// Per-trade look-ahead audit of a stored run (evidence known-times + prefix replay).
import { Fragment, useState } from "react";
import { api, type RunAudit, type Trade } from "../api";
import { fmtNum, fmtSigned, fmtTs } from "../format";

const tick = (v: boolean | null) => (v === null ? <span className="dim">n/a</span> : v ? <span className="pos">✓</span> : <span className="neg">✗</span>);

export function TradeAudit({ runId, onOpenInLab }: { runId: string; onOpenInLab: (t: Trade) => void }) {
  const [audit, setAudit] = useState<RunAudit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setAudit(await api.audit(runId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (audit && audit.run_id !== runId) setAudit(null);

  return (
    <div>
      <div className="row wrap">
        <button onClick={run} disabled={busy}>{busy ? "Auditing…" : "Audit trades for look-ahead"}</button>
        {audit && (
          <span className={audit.all_checks_passed ? "pos" : "neg"}>
            {audit.all_checks_passed ? "✓ all checks passed" : "✗ at least one check failed"} · same data as run: {audit.same_market_data_as_run ? "yes" : "NO"}
          </span>
        )}
      </div>
      {error && <div className="alert error">{error}</div>}
      <p className="fine">
        Re-evaluates the strategy with the run's parameter snapshot. <b>Signal</b>: the stored trade's signal is reproduced.{" "}
        <b>Prefix</b>: identical signal when the strategy only receives bars up to the signal bar (no future data exists for it).{" "}
        <b>Evidence</b>: every recorded input was known at or before the decision time.
      </p>
      {audit && audit.notes.map((n) => <div key={n} className="alert error">{n}</div>)}
      {audit && (
        <div className="table-wrap tall">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Signal bar</th><th>Fill</th><th className="r">Signal close</th><th className="r">Fill px</th>
                <th className="r">RR @signal</th><th className="r">RR @fill</th><th className="r">R realised</th><th>Exit</th>
                <th>Signal</th><th>Prefix</th><th>Evidence</th><th></th>
              </tr>
            </thead>
            <tbody>
              {audit.trades.map((t) => (
                <Fragment key={t.trade_id}>
                  <tr className={open === t.trade_id ? "sel" : ""} onClick={() => setOpen(open === t.trade_id ? null : t.trade_id)}>
                    <td>{t.trade_id}</td>
                    <td>{fmtTs(t.signal_bar_ts)}</td>
                    <td>{fmtTs(t.entry_ts)}</td>
                    <td className="r">{fmtNum(t.signal_bar_close, 1)}</td>
                    <td className="r">{fmtNum(t.entry_price, 1)}</td>
                    <td className="r">{fmtNum(t.planned_rr_at_signal)}</td>
                    <td className="r">{fmtNum(t.planned_rr_at_fill)}</td>
                    <td className={`r ${t.r_multiple >= 0 ? "pos" : "neg"}`}>{fmtSigned(t.r_multiple)}</td>
                    <td>{t.exit_reason}</td>
                    <td>{tick(t.signal_reproduced)}</td>
                    <td>{tick(t.prefix_replay_ok)}</td>
                    <td>{tick(t.evidence_ok)}</td>
                    <td><button onClick={(e) => { e.stopPropagation(); onOpenInLab(t as unknown as Trade); }}>Chart →</button></td>
                  </tr>
                  {open === t.trade_id && (
                    <tr className="detail-row">
                      <td colSpan={13}>
                        <div className="mono fine">{t.entry_reason}</div>
                        {t.evidence.length === 0 ? (
                          <span className="dim">This strategy records no evidence list; the prefix replay is the look-ahead check.</span>
                        ) : (
                          <table>
                            <thead><tr><th>Input</th><th>TF</th><th>Occurred (drawn)</th><th>Known (usable from)</th><th>Lag</th><th>≤ decision {fmtTs(t.decision_ts)}</th></tr></thead>
                            <tbody>
                              {t.evidence.map((e, i) => (
                                <tr key={i}>
                                  <td>{e.what}</td><td>{e.tf}</td><td>{fmtTs(e.ts)}</td><td>{fmtTs(e.known_ts)}</td>
                                  <td>{Math.round((e.known_ts - e.ts) / 60000)} min</td>
                                  <td className={e.ok ? "pos" : "neg"}>{e.ok ? "✓" : "✗ LOOK-AHEAD"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
