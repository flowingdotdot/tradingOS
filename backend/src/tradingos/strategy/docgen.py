"""Generate the parameter/component reference for each plugin from its schema.

Output: docs/strategies/generated/<strategy_id>.md  (regenerate with `tradingos docs`).
The hand-written strategy docs link to these files; tests fail if they are stale, so the
reference can never drift from the code (the schema is the single source of truth).
"""

from __future__ import annotations

from pathlib import Path

from tradingos.config import PROJECT_DIR
from tradingos.strategy.registry import STRATEGY_CLASSES, describe_strategy

GENERATED_DIR = PROJECT_DIR / "docs" / "strategies" / "generated"


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "[]"
    return str(v)


def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _range(p: dict) -> str:
    lo = f"≥ {_n(p['minimum'])}" if p.get("minimum") is not None else (
        f"> {_n(p['exclusive_minimum'])}" if p.get("exclusive_minimum") is not None else "")
    hi = f"≤ {_n(p['maximum'])}" if p.get("maximum") is not None else (
        f"< {_n(p['exclusive_maximum'])}" if p.get("exclusive_maximum") is not None else "")
    parts = [x for x in (lo, hi) if x]
    if p.get("options"):
        parts.append("one of: " + ", ".join(f"`{o}`" for o in p["options"]))
    if p.get("pattern"):
        parts.append("format HH:MM-HH:MM (UTC)" if p["kind"] == "session" else f"pattern `{p['pattern']}`")
    return "; ".join(parts) or "—"


def render(strategy_id: str) -> str:
    d = describe_strategy(strategy_id)
    lines = [
        f"# {d['name']} — generated reference",
        "",
        "> AUTO-GENERATED from the plugin schema by `tradingos docs`. Do not edit by hand.",
        "",
        f"- strategy_id: `{d['strategy_id']}`",
        f"- definition_version: `{d['definition_version']}`",
        f"- category: {d['category']} · maturity: **{d['maturity']}**",
        f"- run timeframes: {', '.join(d['data']['base_timeframes'])}"
        + (f" · derived internally: {', '.join(d['data']['derived_timeframes'])}" if d['data']['derived_timeframes']
           else ""),
        f"- instruments: {', '.join(d['data']['instruments'])} · fields: {', '.join(d['data']['fields'])}",
        f"- warm-up with default parameters: {d['default_warmup_bars']} bars",
        "",
        "## Components",
        "",
        "| id | label | role | toggle | requires |",
        "|---|---|---|---|---|",
    ]
    for c in d["components"]:
        toggle = f"`{c['toggle_path']}` = `{_fmt(c['on_value'])}`" if c["toggle_path"] else "always on"
        lines.append(f"| `{c['id']}` | {c['label']} | {c['role']} | {toggle} | {_fmt(c['requires']) if c['requires'] else '—'} |")
    for r in d["component_rules"]:
        lines.append(f"\nRule ({r['kind']}): {', '.join(r['components'])} — {r['message']}")
    lines += ["", "## Parameters", "", "| path | label | type | default | unit | allowed | depends on |",
              "|---|---|---|---|---|---|---|"]
    for p in d["param_specs"]:
        if p["kind"] == "group":
            lines.append(f"| **{p['path']}** | **{p['label']}** | group | | | | |")
            continue
        dep = p.get("depends_on")
        dep_s = (f"`{dep['path']}` {'∈ ' + _fmt(dep['in']) if 'in' in dep else '= ' + _fmt(dep.get('equals'))}"
                 if dep else "—")
        kind = p["kind"] + (" (nullable)" if p["nullable"] else "")
        desc = f" — {p['description']}" if p["description"] else ""
        lines.append(f"| `{p['path']}` | {p['label']}{desc} | {kind} | `{_fmt(p['default'])}` | {_fmt(p['unit'])} "
                     f"| {_range(p)} | {dep_s} |")
    return "\n".join(lines) + "\n"


def write_all(out_dir: Path = GENERATED_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for sid in STRATEGY_CLASSES:
        path = out_dir / f"{sid}.md"
        path.write_text(render(sid), encoding="utf-8", newline="\n")
        paths.append(path)
    return paths
