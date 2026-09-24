"use client";

import { useEffect, useMemo, useState } from "react";
import {
  COND_BY_ID, DEFAULT_SCENARIO, TARGET_MODES, TARGET_MODE_LABELS,
  describe, evaluate, keepWithout, newCond, newExcept, newGroup, nodeVector, readCond, reportDefaultTree, stats, walk,
  type CondNode, type Dataset, type Evaluated, type FactsFile, type GroupNode, type Node, type ReviewState,
  type Scenario, type Stats,
} from "@/lib/explorer/engine";
import FilterNode, { type Impact } from "./FilterBuilder";
import s from "./explorer.module.css";

export type DatasetRef = { id: string; label: string; url: string; report: string };

type State = { sel: string[]; lead: string; scenario: Scenario; tree: GroupNode };

// ------------------------------------------------------------- loading ----

async function fetchJson(url: string) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const buf = new Uint8Array(await res.arrayBuffer());
  // Served as a raw .gz (no Content-Encoding): inflate here. If a host ever
  // decodes it on the way, the bytes are already JSON.
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await new Response(stream).text());
  }
  return JSON.parse(new TextDecoder().decode(buf));
}

const reviewCache: Record<string, Promise<Record<string, ReviewState>>> = {};
function loadReview(key: string | null) {
  if (!key) return Promise.resolve({});
  if (!reviewCache[key]) {
    const get = (k: string) =>
      fetch("/api/rows?report=" + encodeURIComponent(k))
        .then((r) => (r.ok ? r.json() : {}))
        .catch(() => ({}));
    reviewCache[key] = Promise.all([get(key), get(key + "_done")]).then(([rows, done]) => {
      const out: Record<string, ReviewState> = { ...rows };
      for (const [k, v] of Object.entries(done as Record<string, ReviewState>)) out[k] = { ...(out[k] || {}), done: !!v?.done };
      return out;
    });
  }
  return reviewCache[key];
}

// -------------------------------------------------------- url + presets ----

function encodeState(st: State) {
  const bytes = new TextEncoder().encode(JSON.stringify(st));
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function decodeState(h: string): State | null {
  try {
    const bin = atob(h.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0))));
  } catch {
    return null;
  }
}

const PRESET_KEY = "lxpb-explorer-presets-v1";
function readPresets(): Record<string, { scenario: Scenario; tree: GroupNode }> {
  try {
    return JSON.parse(localStorage.getItem(PRESET_KEY) || "{}");
  } catch {
    return {};
  }
}
function writePresets(p: Record<string, unknown>) {
  try {
    localStorage.setItem(PRESET_KEY, JSON.stringify(p));
  } catch {
    /* private window: presets just don't persist */
  }
}

function builtInPresets(facts?: FactsFile): Record<string, () => GroupNode> {
  const defaults = () => reportDefaultTree(facts).children;
  const num = (id: string, op: CondNode["op"], value: number, params: Record<string, number | string>) =>
    Object.assign(newCond(id), { op, value, params: { ...newCond(id).params, ...params } });
  const tag = (t: string) => Object.assign(newCond("tag"), { values: [t] });
  return {
    "Report defaults": () => reportDefaultTree(facts),
    "No filters": () => newGroup("all"),
    "Feature study: (box or approach) and (fresh or SFP)": () =>
      Object.assign(
        newGroup("all", [
          ...defaults(),
          Object.assign(newGroup("any", [num("box", "lt", 2, { n: 6 }), num("appr", "lte", 1, { k: 3 })]), { label: "group A" }),
          Object.assign(newGroup("any", [num("fresh", "lte", 2, { days: 2, tol: "1" }), newCond("sfp")]), { label: "group B" }),
        ]),
        { label: "Feature study mix" },
      ),
    "Soft exclude: drop fading-bias unless tight box": () =>
      newGroup("all", [...defaults(), newExcept([tag("fading_bias")], [num("box", "lt", 2, { n: 6 })])]),
  };
}

// ---------------------------------------------------------------- view ----

const f1 = (x: number) => x.toFixed(1);
const f2 = (x: number) => x.toFixed(2);
const sgn = (x: number, d = 1) => (x >= 0 ? "+" : "") + x.toFixed(d);

function Spark({ curve }: { curve: number[] }) {
  if (curve.length < 2) return <svg className={s.spark} />;
  const w = 220, h = 44;
  const lo = Math.min(0, ...curve), hi = Math.max(0, ...curve);
  const y = (v: number) => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
  const pts = curve.map((v, i) => `${((i / (curve.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = curve[curve.length - 1];
  return (
    <svg className={s.spark} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-label="cumulative R">
      <line x1={0} x2={w} y1={y(0)} y2={y(0)} className={s.zero} />
      <polyline points={pts} className={last >= 0 ? s.lineUp : s.lineDown} />
    </svg>
  );
}

function StatCard({ label, st, base, lead, onLead }: {
  label: string; st: Stats; base: Stats; lead: boolean; onLead: () => void;
}) {
  return (
    <div className={`${s.card} ${lead ? s.cardLead : ""}`} onClick={onLead} title="Click to show this dataset's trades below">
      <div className={s.cardHead}>
        <b>{label}</b>
        <span className={s.muted}>
          {st.n} of {base.n} trades
        </span>
      </div>
      <div className={s.big + " " + (st.totalR >= 0 ? s.good : s.bad)}>{sgn(st.totalR)}R</div>
      <Spark curve={st.curve} />
      <div className={s.kv}>
        <span>win</span><b>{f1(st.winRate)}%</b>
        <span>avg R</span><b>{f2(st.avgR)}</b>
        <span>PF</span><b>{Number.isFinite(st.profitFactor) ? f2(st.profitFactor) : "∞"}</b>
        <span>max DD</span><b>{f1(st.maxDD)}R</b>
        <span>PnL</span><b>{sgn(st.pnl)} pt</b>
        <span>comm</span><b>${st.comm.toFixed(0)}</b>
        <span>W / L</span><b>{st.wins} / {st.losses}</b>
        <span>unfiltered</span><b className={s.muted}>{sgn(base.totalR)}R</b>
      </div>
    </div>
  );
}

type Col = { key: string; label: string; title?: string; val: (i: number) => number | string | null; fmt?: (v: never) => string; leaf?: string };

function fmtReading(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.length ? v.join(" ") : "–";
  return String(v);
}

function TradeTable({ ev, tree, reportUrl }: { ev: Evaluated; tree: GroupNode; reportUrl: string }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "time", dir: 1 });
  const [showNoTrade, setShowNoTrade] = useState(false);
  const [limit, setLimit] = useState(300);

  const leaves: CondNode[] = [];
  const seen = new Set<string>();
  walk(tree, (n) => {
    if (n.kind !== "cond" || n.off) return;
    const k = describe({ ...n, negate: false });
    if (seen.has(k)) return;
    seen.add(k);
    leaves.push(n);
  });

  const C = ev.ctx;
  const cols: Col[] = [
    { key: "i", label: "#", val: (i) => C[i].t.i },
    { key: "time", label: "Fill (PT)", val: (i) => C[i].t.entryTime || C[i].t.retest },
    { key: "dir", label: "Dir", val: (i) => (C[i].t.type === "LHPB" ? "long" : "short") },
    { key: "entry", label: "Entry", val: (i) => C[i].t.entry },
    { key: "risk", label: "Stop pt", val: (i) => C[i].t.risk },
    { key: "rr", label: "R offer", val: (i) => (Number.isNaN(C[i].e.rr) ? null : C[i].e.rr) },
    {
      key: "res", label: "Result",
      val: (i) => (C[i].e.noTrade ? C[i].t.reason || "NO TARGET (rule off)" : C[i].e.outcome === "quick_exit" ? "QUICK EXIT" : C[i].e.m?.label || C[i].e.outcome),
    },
    { key: "r", label: "R", val: (i) => (Number.isNaN(C[i].e.r) ? null : C[i].e.r) },
    { key: "pnl", label: "PnL pt", val: (i) => (Number.isNaN(C[i].e.pnl) ? null : C[i].e.pnl) },
    { key: "tags", label: "Tags", val: (i) => C[i].e.tags.join(" ") },
    { key: "bias", label: "Bias", val: (i) => C[i].t.bias },
    ...leaves.map((n) => {
      const d = COND_BY_ID[n.cond];
      const ps = (d.params || []).map((p) => `${p.label} ${n.params[p.key]}`).join(", ");
      return {
        key: "leaf:" + n.id, leaf: n.id, label: d.label + (ps ? ` (${ps})` : ""), title: d.help,
        val: (i: number) => {
          const v = readCond(n, C[i]);
          return typeof v === "number" || typeof v === "string" || v === null ? v : fmtReading(v);
        },
      };
    }),
  ];

  const rows = C.map((_, i) => i).filter((i) => ev.keep[i] && (showNoTrade || !C[i].e.noTrade));
  const col = cols.find((c) => c.key === sort.key) || cols[1];
  rows.sort((a, b) => {
    const x = col.val(a), y = col.val(b);
    if (x === y) return a - b;
    if (x === null || x === "") return 1;
    if (y === null || y === "") return -1;
    return (x < y ? -1 : 1) * sort.dir;
  });

  return (
    <div>
      <div className={s.tableBar}>
        <b>{rows.length}</b>&nbsp;rows
        <label>
          <input type="checkbox" checked={showNoTrade} onChange={(e) => setShowNoTrade(e.target.checked)} /> show no-trade rows
        </label>
        <span className={s.muted}>Condition columns: green = passes, red = fails. Charts: open the </span>
        <a href={reportUrl} target="_blank" rel="noreferrer">full report</a>
      </div>
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th
                  key={c.key} title={c.title} className={c.leaf ? s.leafTh : ""}
                  onClick={() => setSort({ key: c.key, dir: sort.key === c.key ? ((-sort.dir) as 1 | -1) : c.key === "time" || c.key === "i" ? 1 : -1 })}
                >
                  {c.label}
                  {sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((i) => {
              const { e } = C[i];
              return (
                <tr key={i} className={e.noTrade ? s.noTrade : ""}>
                  {cols.map((c) => {
                    const v = c.val(i);
                    let cls = "";
                    if (c.leaf) cls = ev.leaf.get(c.leaf)?.[i] ? s.cellPass : s.cellFail;
                    else if (c.key === "r" || c.key === "pnl") cls = typeof v === "number" ? (v > 0 ? s.good : v < 0 ? s.bad : "") : "";
                    else if (c.key === "res") cls = e.bucket === "win" ? s.good : e.bucket === "loss" ? s.bad : s.muted;
                    return (
                      <td key={c.key} className={cls}>
                        {c.key === "r" && typeof v === "number" ? sgn(v, 2) : c.key === "pnl" && typeof v === "number" ? sgn(v, 2) : fmtReading(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button className={s.more} onClick={() => setLimit(rows.length)}>
          show all {rows.length} rows
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- main ----

export default function Explorer({ datasets }: { datasets: DatasetRef[] }) {
  const [st, setSt] = useState<State>(() => ({
    sel: datasets.map((d) => d.id),
    lead: datasets[0]?.id || "",
    scenario: DEFAULT_SCENARIO,
    tree: reportDefaultTree(),
  }));
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState<Record<string, Dataset>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [presets, setPresets] = useState<Record<string, { scenario: Scenario; tree: GroupNode }>>({});
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [copied, setCopied] = useState(false);

  // State from the link, if any; then keep the link in step with every edit.
  useEffect(() => {
    const m = location.hash.match(/q=([\w-]+)/);
    const fromUrl = m ? decodeState(m[1]) : null;
    if (fromUrl?.tree) setSt((cur) => ({ ...cur, ...fromUrl, scenario: { ...DEFAULT_SCENARIO, ...fromUrl.scenario } }));
    setPresets(readPresets());
    setReady(true);
  }, []);
  useEffect(() => {
    if (ready) history.replaceState(null, "", "#q=" + encodeState(st));
  }, [st, ready]);

  useEffect(() => {
    for (const ref of datasets) {
      if (!st.sel.includes(ref.id) || loaded[ref.id] || errors[ref.id] === "loading") continue;
      setErrors((e) => ({ ...e, [ref.id]: "loading" }));
      fetchJson(ref.url)
        .then(async (facts: FactsFile) => {
          const review = await loadReview(facts.reviewKey);
          setLoaded((l) => ({ ...l, [ref.id]: { id: ref.id, label: ref.label, facts, review } }));
          setErrors((e) => {
            const { [ref.id]: _, ...rest } = e;
            return rest;
          });
        })
        .catch((err) => setErrors((e) => ({ ...e, [ref.id]: String(err) })));
    }
  }, [st.sel, datasets, loaded, errors]);

  const active = datasets.filter((d) => st.sel.includes(d.id) && loaded[d.id]).map((d) => loaded[d.id]);
  const leadDs = loaded[st.lead] || active[0];
  const set = (patch: Partial<State>) => setSt((cur) => ({ ...cur, ...patch }));
  const setSc = (patch: Partial<Scenario>) => setSt((cur) => ({ ...cur, scenario: { ...cur.scenario, ...patch } }));

  const results = useMemo(
    () =>
      active.map((ds) => {
        const ev = evaluate(ds, st.scenario, st.tree);
        const base = stats(evaluate(ds, st.scenario, newGroup("all")));
        return { ds, ev, st: stats(ev), base };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [active.map((d) => d.id).join(","), st.scenario, st.tree],
  );

  const impact = useMemo(() => {
    const out = new Map<string, Impact>();
    walk(st.tree, (n) => {
      if (n === st.tree || n.off) return;
      const per = results.map((r) => {
        const w = stats(r.ev, keepWithout(r.ev, st.tree, n.id));
        return { label: r.ds.label, dN: w.n - r.st.n, dR: r.st.totalR - w.totalR };
      });
      let pass: number | null = null;
      const lr = results.find((r) => r.ds.id === leadDs?.id);
      if (lr && n.kind !== "except") {
        const v = nodeVector(lr.ev, n);
        if (v) pass = lr.ev.ctx.reduce((a, c, i) => a + (!c.e.noTrade && v[i] ? 1 : 0), 0);
      }
      out.set(n.id, { pass, per });
    });
    return out;
  }, [results, st.tree, leadDs?.id]);

  const leadResult = results.find((r) => r.ds.id === leadDs?.id);
  const builtIns = builtInPresets(leadDs?.facts);

  return (
    <main className={s.page}>
      <header className={s.header}>
        <div>
          <h1>Trade explorer</h1>
          <p className={s.muted}>
            M5 Confluence v2 trades. Same numbers as the reports' own filters, with any combination of them.{" "}
            <a href="/">All reports</a>
          </p>
        </div>
        <div className={s.datasets}>
          {datasets.map((d) => (
            <label key={d.id} className={s.chip + (st.sel.includes(d.id) ? " " + s.chipOn : "")}>
              <input
                type="checkbox" checked={st.sel.includes(d.id)}
                onChange={(e) => set({ sel: e.target.checked ? [...st.sel, d.id] : st.sel.filter((x) => x !== d.id) })}
              />
              {d.label}
              {errors[d.id] === "loading" ? " …" : errors[d.id] ? " ⚠" : ""}
            </label>
          ))}
        </div>
      </header>
      {Object.entries(errors).filter(([, v]) => v !== "loading").map(([k, v]) => (
        <p key={k} className={s.bad}>{k}: {v}</p>
      ))}
      {!datasets.length && <p className={s.bad}>No facts files found (run ss_m5_confl2/trade_facts.py on the reports).</p>}

      <section className={s.cards}>
        {results.map((r) => (
          <StatCard key={r.ds.id} label={r.ds.label} st={r.st} base={r.base} lead={r.ds.id === leadDs?.id} onLead={() => set({ lead: r.ds.id })} />
        ))}
      </section>

      <section className={s.panel}>
        <h2>Scenario <span className={s.muted}>changes each trade's result; applied before the filters</span></h2>
        <div className={s.row}>
          <span className={s.small}>Target rules (farther target wins):</span>
          {TARGET_MODES.map((m) => (
            <label key={m}>
              <input
                type="checkbox" checked={st.scenario.targetModes.includes(m)}
                onChange={(e) =>
                  setSc({ targetModes: e.target.checked ? [...st.scenario.targetModes, m] : st.scenario.targetModes.filter((x) => x !== m) })
                }
              />{" "}
              {TARGET_MODE_LABELS[m]}
            </label>
          ))}
          <label>
            <input type="checkbox" checked={st.scenario.mgmt} onChange={(e) => setSc({ mgmt: e.target.checked })} /> thrust-trail + RR-floor management
          </label>
        </div>
        <div className={s.row}>
          <label>
            <input type="checkbox" checked={st.scenario.qx.on} onChange={(e) => setSc({ qx: { ...st.scenario.qx, on: e.target.checked } })} /> Quick exit
          </label>
          at
          <input type="number" min={1} max={120} value={st.scenario.qx.sec} onChange={(e) => setSc({ qx: { ...st.scenario.qx, sec: +e.target.value } })} />
          s after fill, if best bounce &lt;
          <input type="number" step={0.25} value={st.scenario.qx.min} onChange={(e) => setSc({ qx: { ...st.scenario.qx, min: +e.target.value } })} />
          pt; slippage
          <input type="number" step={0.25} value={st.scenario.qx.slip} onChange={(e) => setSc({ qx: { ...st.scenario.qx, slip: +e.target.value } })} />
          pt
        </div>
      </section>

      <section className={s.panel}>
        <h2>
          Filters{" "}
          <span className={s.muted}>
            badges: trades passing (lead dataset) · what each part adds in total R / trades it removes, per dataset
          </span>
        </h2>
        <div className={s.row}>
          <select
            value=""
            onChange={(e) => {
              const v = e.target.value;
              if (v.startsWith("b:")) set({ tree: builtIns[v.slice(2)]() });
              else if (v.startsWith("u:")) {
                const p = presets[v.slice(2)];
                if (p) set({ tree: p.tree, scenario: { ...DEFAULT_SCENARIO, ...p.scenario } });
              }
            }}
          >
            <option value="">Load preset…</option>
            <optgroup label="Built in">
              {Object.keys(builtIns).map((k) => (
                <option key={k} value={"b:" + k}>{k}</option>
              ))}
            </optgroup>
            {Object.keys(presets).length > 0 && (
              <optgroup label="Saved">
                {Object.keys(presets).map((k) => (
                  <option key={k} value={"u:" + k}>{k}</option>
                ))}
              </optgroup>
            )}
          </select>
          <button
            onClick={() => {
              const name = prompt("Save this scenario + filters as:");
              if (!name) return;
              const next = { ...presets, [name]: { scenario: st.scenario, tree: st.tree } };
              setPresets(next);
              writePresets(next);
            }}
          >
            Save preset
          </button>
          {Object.keys(presets).length > 0 && (
            <select
              value=""
              onChange={(e) => {
                const k = e.target.value;
                if (!k || !confirm(`Delete preset "${k}"?`)) return;
                const { [k]: _, ...rest } = presets;
                setPresets(rest);
                writePresets(rest);
              }}
            >
              <option value="">Delete preset…</option>
              {Object.keys(presets).map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => {
              navigator.clipboard?.writeText(location.href);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied" : "Copy link"}
          </button>
          <button
            onClick={() => {
              setJsonText(JSON.stringify(st.tree, null, 1));
              setJsonOpen(!jsonOpen);
            }}
          >
            {jsonOpen ? "Close JSON" : "Edit as JSON"}
          </button>
        </div>
        {jsonOpen && (
          <div className={s.json}>
            <textarea value={jsonText} onChange={(e) => setJsonText(e.target.value)} rows={12} />
            <button
              onClick={() => {
                try {
                  const t = JSON.parse(jsonText) as Node;
                  if (t.kind !== "group") throw new Error("the top level must be a group");
                  set({ tree: t as GroupNode });
                  setJsonOpen(false);
                } catch (err) {
                  alert(String(err));
                }
              }}
            >
              Apply JSON
            </button>
          </div>
        )}
        <code className={s.describe}>{describe(st.tree)}</code>
        <FilterNode root node={st.tree} onChange={(n) => set({ tree: n as GroupNode })} impact={impact} datasets={active} />
      </section>

      {leadResult && (
        <section className={s.panel}>
          <h2>
            Trades <span className={s.muted}>{leadResult.ds.label} (click a dataset card above to switch)</span>
          </h2>
          <TradeTable ev={leadResult.ev} tree={st.tree} reportUrl={datasets.find((d) => d.id === leadResult.ds.id)?.report || "/"} />
        </section>
      )}
    </main>
  );
}
