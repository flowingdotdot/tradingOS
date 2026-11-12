import { useEffect, useState } from "react";
import { api, type SystemInfo } from "./api";
import { BacktestLab } from "./pages/BacktestLab";
import { ChartWorkspace, type WorkspacePreset } from "./pages/ChartWorkspace";
import { StrategyLab, type BacktestPreset, type LabPreset } from "./pages/StrategyLab";

type PageId = "chart" | "dashboard" | "strategy" | "backtest" | "desk" | "journal" | "intel" | "settings";

const NAV: { id: PageId; label: string; phase?: string }[] = [
  { id: "chart", label: "차트" },
  { id: "dashboard", label: "Dashboard" },
  { id: "strategy", label: "Strategy Lab" },
  { id: "backtest", label: "Backtest Lab" },
  { id: "desk", label: "Trading Desk", phase: "Phase 4" },
  { id: "journal", label: "Trade Journal", phase: "Phase 4–5" },
  { id: "intel", label: "Investment Intelligence", phase: "Track B" },
  { id: "settings", label: "Settings", phase: "Phase 4" },
];

const PLACEHOLDER: Partial<Record<PageId, string>> = {
  desk:
    "OKX DEMO automated trading: live chart, strategy state, positions, orders, risk controls, kill switch. " +
    "LIVE remains hard-disabled.",
  journal: "Automatic trade records with entry/exit reasons, strategy version, execution quality and incidents.",
  intel:
    "Macro research & investment decision support (FRED/central-bank data with point-in-time vintages, scenarios, " +
    "explainable ranking, recommendation history). Research only — no order execution. See docs/INVESTMENT_INTELLIGENCE_SPEC.md.",
  settings: "Environment, credential status (never values), risk limits.",
};

function EnvBanner({ system }: { system: SystemInfo | null }) {
  return (
    <div className="env-banner">
      <span className="env-chip backtest">BACKTEST · ENABLED</span>
      <span className="env-chip demo off">DEMO · {system?.environments.DEMO.status ?? "…"}</span>
      <span className="env-chip live off">LIVE · DISABLED</span>
    </div>
  );
}

function Dashboard({ system }: { system: SystemInfo | null }) {
  if (!system) return <div className="page"><p className="dim">Connecting to API…</p></div>;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Dashboard</h1>
      </div>
      <section className="panel">
        <h2>Execution environments</h2>
        <table>
          <thead>
            <tr><th>Environment</th><th>Enabled</th><th>Status</th></tr>
          </thead>
          <tbody>
            {Object.entries(system.environments).map(([k, v]) => (
              <tr key={k}>
                <td className="mono">{k}</td>
                <td className={v.enabled ? "pos" : "neg"}>{v.enabled ? "yes" : "no"}</td>
                <td>
                  {v.status}
                  {v.credentials_configured != null && (
                    <span className="dim"> · credentials {v.credentials_configured ? "configured" : "not configured"}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Milestone 1 scope</h2>
        <p>OKX historical data → Strategy → Backtest → Results. Account, positions and live signals arrive with DEMO trading (Phase 4).</p>
      </section>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<PageId>("chart");
  const [wsPreset, setWsPreset] = useState<WorkspacePreset | null>(null);
  const [preset, setPreset] = useState<BacktestPreset | null>(null);
  const [labPreset, setLabPreset] = useState<LabPreset | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [apiDown, setApiDown] = useState(false);

  useEffect(() => {
    api.system().then(setSystem).catch(() => setApiDown(true));
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          Trading<span>OS</span>
          <div className="ver">v{system?.version ?? "—"}</div>
        </div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} className={page === n.id ? "active" : ""} onClick={() => setPage(n.id)}>
              {n.label}
              {n.phase && <span className="phase">{n.phase}</span>}
            </button>
          ))}
        </nav>
        <div className={`api-status ${apiDown ? "neg" : system ? "pos" : ""}`}>
          ● API {apiDown ? "unreachable" : system ? "connected" : "…"}
        </div>
      </aside>
      <main>
        <EnvBanner system={system} />
        {apiDown && (
          <div className="alert error" style={{ margin: 16 }}>
            Cannot reach the backend. Start it with <span className="mono">tradingos serve</span> (port 8000).
          </div>
        )}
        <div hidden={page !== "chart"}>
          <ChartWorkspace preset={wsPreset} />
        </div>
        {page === "dashboard" && <Dashboard system={system} />}
        {page === "strategy" && (
          <StrategyLab preset={labPreset} onBacktest={(p) => { setPreset(p); setPage("backtest"); }} />
        )}
        {page === "backtest" && (
          <BacktestLab system={system} preset={preset}
            onInspectTrade={(p) => { setLabPreset({ ...p, nonce: Date.now() }); setPage("strategy"); }}
            onOpenTradeChart={(p) => { setWsPreset({ ...p, nonce: Date.now() }); setPage("chart"); }} />
        )}
        {PLACEHOLDER[page] && (
          <div className="page">
            <div className="page-head">
              <h1>{NAV.find((n) => n.id === page)?.label}</h1>
              <span className="env-chip off">{NAV.find((n) => n.id === page)?.phase} · not implemented</span>
            </div>
            <section className="panel">
              <p>{PLACEHOLDER[page]}</p>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
