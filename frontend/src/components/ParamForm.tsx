// Auto-generated parameter form + component toggles, driven only by the backend schema.
import type { ComponentSpec, ParamSpec } from "../api";

export type Params = Record<string, unknown>;

export function getPath(obj: Params, path: string): unknown {
  return path.split(".").reduce<unknown>((cur, k) => (cur && typeof cur === "object" ? (cur as Params)[k] : undefined), obj);
}

export function setPath(obj: Params, path: string, value: unknown): Params {
  const [head, ...rest] = path.split(".");
  if (!rest.length) return { ...obj, [head]: value };
  return { ...obj, [head]: setPath((obj[head] as Params) ?? {}, rest.join("."), value) };
}

/** After a change, reset every parameter whose dependency is no longer met to its default.
 *  The backend rejects inert non-default values, so the form keeps the configuration valid
 *  (and the reset is visible: the field shows its default and loses the "changed" tag). */
export function resetInert(specs: ParamSpec[], values: Params): Params {
  let out = values;
  for (let pass = 0; pass < 3; pass++) {
    let changed = false;
    for (const s of specs) {
      if (s.kind === "group") continue;
      const inert = !dependencyMet(s, out) || groupDisabled(s, out);
      if (inert && JSON.stringify(getPath(out, s.path)) !== JSON.stringify(s.default)) {
        out = setPath(out, s.path, s.default);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return out;
}

function dependencyMet(spec: ParamSpec, values: Params): boolean {
  const d = spec.depends_on;
  if (!d) return true;
  const v = getPath(values, d.path);
  if (d.in) return d.in.includes(v);
  return v === d.equals;
}

function groupDisabled(spec: ParamSpec, values: Params): boolean {
  // generic convention: a group with an `enabled` flag that is false greys out its other fields
  if (!spec.group || spec.path.endsWith(".enabled")) return false;
  return getPath(values, `${spec.group}.enabled`) === false;
}

function Field({ spec, value, onChange, disabled, error }: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
  error?: string;
}) {
  const numeric = ["int", "float", "percent", "price", "bps"].includes(spec.kind);
  const isNull = value === null || value === undefined;
  let input;
  if (spec.kind === "bool") {
    input = <input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />;
  } else if (spec.kind === "enum" || spec.kind === "timeframe") {
    input = (
      <select value={String(value)} disabled={disabled} onChange={(e) => {
        const opt = spec.options?.find((o) => String(o) === e.target.value);
        onChange(opt ?? e.target.value);
      }}>
        {spec.options?.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    );
  } else if (spec.kind === "multi_enum") {
    const cur = new Set((value as unknown[]) ?? []);
    input = (
      <span className="multi">
        {spec.options?.map((o) => (
          <label key={String(o)} className="check">
            <input type="checkbox" checked={cur.has(o)} disabled={disabled} onChange={(e) => {
              const next = new Set(cur);
              if (e.target.checked) next.add(o);
              else next.delete(o);
              onChange(spec.options!.filter((x) => next.has(x)));
            }} />
            {String(o)}
          </label>
        ))}
      </span>
    );
  } else if (numeric) {
    input = (
      <input
        type="number"
        value={isNull ? "" : String(value)}
        disabled={disabled || (spec.nullable && isNull)}
        min={spec.minimum ?? spec.exclusive_minimum ?? undefined}
        max={spec.maximum ?? spec.exclusive_maximum ?? undefined}
        step={spec.step ?? (spec.kind === "int" ? 1 : "any")}
        onChange={(e) => {
          if (e.target.value === "") return;
          onChange(spec.kind === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value));
        }}
      />
    );
  } else {
    input = (
      <input type="text" value={isNull ? "" : String(value)} disabled={disabled} pattern={spec.pattern ?? undefined}
        placeholder={spec.kind === "session" ? "HH:MM-HH:MM" : undefined} onChange={(e) => onChange(e.target.value)} />
    );
  }
  const changed = JSON.stringify(value) !== JSON.stringify(spec.default);
  return (
    <div className={`pfield ${disabled ? "disabled" : ""} ${error ? "invalid" : ""}`} title={spec.description || spec.path}>
      <div className="plabel">
        {spec.label}
        {changed && <span className="tag changed">changed</span>}
      </div>
      <div className="pinput">
        {input}
        {spec.unit && <span className="unit">{spec.unit}</span>}
        {spec.nullable && (
          <label className="check nullable">
            <input type="checkbox" checked={isNull} disabled={disabled}
              onChange={(e) => onChange(e.target.checked ? null : spec.default ?? spec.minimum ?? 1)} />
            none
          </label>
        )}
      </div>
      {error && <div className="perror">{error}</div>}
      {spec.description && <div className="phint">{spec.description}</div>}
    </div>
  );
}

export function ParamForm({ specs, values, onChange, errors, hiddenPaths, showAdvanced }: {
  specs: ParamSpec[];
  values: Params;
  onChange: (path: string, value: unknown) => void;
  errors: Record<string, string>;
  hiddenPaths: Set<string>;
  showAdvanced: boolean;
}) {
  const blocks: { title: ParamSpec | null; fields: ParamSpec[] }[] = [{ title: null, fields: [] }];
  for (const s of specs) {
    if (s.kind === "group") blocks.push({ title: s, fields: [] });
    else if (!hiddenPaths.has(s.path) && (showAdvanced || !s.advanced)) blocks[blocks.length - 1].fields.push(s);
  }
  return (
    <div className="pform">
      {blocks.filter((b) => b.fields.length).map((b, i) => (
        <fieldset key={b.title?.path ?? `top${i}`} className="pgroup">
          {b.title && <legend title={b.title.description}>{b.title.label}</legend>}
          {b.fields.map((s) => (
            <Field key={s.path} spec={s} value={getPath(values, s.path)} onChange={(v) => onChange(s.path, v)}
              disabled={!dependencyMet(s, values) || groupDisabled(s, values)} error={errors[s.path]} />
          ))}
        </fieldset>
      ))}
    </div>
  );
}

export function ComponentToggles({ components, values, onChange, errors }: {
  components: ComponentSpec[];
  values: Params;
  onChange: (path: string, value: unknown) => void;
  errors: Record<string, string>;
}) {
  const isOn = (c: ComponentSpec) => (c.toggle_path ? getPath(values, c.toggle_path) === c.on_value : true);
  const byId = Object.fromEntries(components.map((c) => [c.id, c]));
  return (
    <div className="components">
      {components.map((c) => {
        const on = isOn(c);
        const err = c.toggle_path ? errors[c.toggle_path] : undefined;
        return (
          <div key={c.id} className={`component ${on ? "on" : "off"} ${c.role} ${err ? "invalid" : ""}`} title={c.description}>
            {c.role === "required" || !c.toggle_path ? (
              <span className="lock">● required</span>
            ) : (
              <label className="check">
                <input type="checkbox" checked={on} onChange={(e) => onChange(c.toggle_path!, e.target.checked ? c.on_value : c.off_value)} />
                {on ? "ON" : "OFF"}
              </label>
            )}
            <span className="clabel">{c.label}</span>
            {c.requires.length > 0 && <span className="dim fine"> requires {c.requires.map((r) => byId[r]?.label ?? r).join(", ")}</span>}
            {err && <div className="perror">{err}</div>}
          </div>
        );
      })}
    </div>
  );
}
