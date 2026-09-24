// Trade-filter engine for the /explorer page.
//
// Three layers, kept apart on purpose:
//   1. Facts     -- one record per trade, read out of a finished ss_m5_confl2
//                   report by ss_m5_confl2/trade_facts.py. No logic, just readings.
//   2. Scenario  -- the controls that CHANGE a trade's result rather than decide
//                   whether it counts: which target rules are on, trade
//                   management, quick exit. Applied first, so every filter sees
//                   the settled outcome. Mirrors the report's own
//                   applyTargetModes / recomputeDynStats / quickExitResult.
//   3. Filter    -- a tree of ALL / ANY (at least k) / NONE groups, "drop X
//                   unless Y" nodes and conditions from CONDITIONS. Strict and
//                   soft include/exclude, "only", and the report's OR groups are
//                   all just shapes of this tree.
//
// Each condition is evaluated once per dataset into a pass/fail vector and the
// tree combines vectors, so re-evaluating with any one node removed (the
// per-node impact numbers) is cheap.
//
// Self-contained on purpose (no relative imports): the parity test runs it
// straight under node (scripts/test-explorer-engine.mjs).

// ---------------------------------------------------------------- facts ----

export type ModeFacts = {
  dist: number | null;
  target: number | null;
  rr: number | null;
  r: number | null;
  pnl: number | null;
  outcome: string | null;
  label: string | null;
  mgmtR: number | null;
  mgmtPnl: number | null;
  mgmtOutcome: string | null;
  mgmtFired: boolean;
  tags: string[];
  exitSec: number | null;
  mgmtExitSec: number | null;
  mae: number | null;
  mfe: number | null;
};

export type Trade = {
  idx: number;
  i: number;
  key: string;
  type: "LHPB" | "LLPB" | string;
  filled: boolean;
  reason: string | null;
  retest: string | null; // "YYYY-MM-DD HH:MM:SS" PT
  entryTime: string | null; // PT
  entry: number | null;
  stop: number | null;
  risk: number | null;
  comm: number | null;
  contracts: number | null;
  members: number;
  h1confl: number | null;
  bias: string | null;
  biasTitle: string | null;
  tags: string[];
  daygap: number | null;
  h1gap: number | null;
  mingap: number | null;
  vspikeoffs: number | null;
  erByK: (number | null)[] | null;
  p1RatioByWindow: (number | null)[] | null;
  boxByN: (number | null)[] | null;
  apprByK: (number | null)[] | null;
  fresh: (number | null)[][] | null;
  sfp: number[] | null;
  qx: { b: number[]; c: number[] } | null;
  modes: Record<string, ModeFacts>;
};

export type FactsFile = {
  schema: number;
  source: string;
  title: string | null;
  start: string | null;
  end: string | null;
  reviewKey: string | null;
  defaults: {
    num: Record<string, { op?: string; value?: number | null }>;
    inputs: Record<string, number | null>;
    selects: Record<string, { options: string[]; value: string | null }>;
    checked: Record<string, Record<string, boolean>>;
  };
  trades: Trade[];
};

export type ReviewState = {
  reviewed?: boolean;
  valid?: boolean;
  replayed?: boolean;
  notes?: string;
  done?: boolean;
};

export type Dataset = {
  id: string;
  label: string;
  facts: FactsFile;
  review: Record<string, ReviewState>;
};

// ------------------------------------------------------------- scenario ----

export const TARGET_MODES = ["opposite-m5-zz", "swing-extreme"] as const;
export const TARGET_MODE_LABELS: Record<string, string> = {
  "opposite-m5-zz": "Opposite M5 level, zigzag anchor",
  "swing-extreme": "Swing extreme",
};

export type Scenario = {
  targetModes: string[];
  mgmt: boolean;
  qx: { on: boolean; sec: number; min: number; slip: number };
};

export const DEFAULT_SCENARIO: Scenario = {
  targetModes: [...TARGET_MODES],
  mgmt: false,
  qx: { on: false, sec: 30, min: 0.75, slip: 0.25 },
};

export type Bucket = "win" | "loss" | "no_trade";

export type Eff = {
  noTrade: boolean; // unfilled, or no target under the ticked rules
  mode: string | null;
  m: ModeFacts | null;
  r: number; // NaN when none
  pnl: number;
  outcome: string;
  bucket: Bucket;
  rr: number;
  tags: string[];
  usedMgmt: boolean;
  qx: { r: number; pnl: number } | null; // what quick exit WOULD do
};

const num = (v: number | null | undefined) => (v === null || v === undefined ? NaN : v);

function quickExit(t: Trade, m: ModeFacts, useMgmt: boolean, q: Scenario["qx"]) {
  if (!t.qx || !t.risk) return null;
  const sec = Math.round(q.sec);
  if (sec < 0 || sec >= t.qx.b.length) return null;
  const exitSec = num(useMgmt ? m.mgmtExitSec : m.exitSec);
  if (Number.isFinite(exitSec) && exitSec <= sec) return null;
  if (t.qx.b[sec] >= q.min) return null;
  const pnl = t.qx.c[sec] - q.slip;
  return { r: pnl / t.risk, pnl };
}

export function applyScenario(t: Trade, sc: Scenario): Eff {
  let pick: string | null = null;
  if (t.filled) {
    for (const mode of sc.targetModes) {
      const p = t.modes[mode];
      if (p && (pick === null || num(p.dist) > num(t.modes[pick].dist))) pick = mode;
    }
  }
  if (pick === null) {
    return {
      noTrade: true, mode: null, m: null, r: NaN, pnl: NaN, outcome: "", bucket: "no_trade",
      rr: NaN, tags: t.tags, usedMgmt: false, qx: null,
    };
  }
  const m = t.modes[pick];
  const useRow = sc.mgmt && m.mgmtFired;
  let r = num(useRow ? m.mgmtR : m.r);
  let pnl = num(useRow ? m.mgmtPnl : m.pnl);
  let outcome = (useRow ? m.mgmtOutcome : m.outcome) || "";
  const qx = quickExit(t, m, useRow, sc.qx);
  if (sc.qx.on && qx) {
    r = qx.r;
    pnl = qx.pnl;
    outcome = "quick_exit";
  }
  const bucket: Bucket = Number.isNaN(r) ? "no_trade" : outcome === "target" ? "win" : "loss";
  return {
    noTrade: false, mode: pick, m, r, pnl, outcome, bucket, rr: num(m.rr),
    tags: m.tags.length ? [...t.tags, ...m.tags] : t.tags, usedMgmt: useRow, qx,
  };
}

// ----------------------------------------------------------- conditions ----

export type ParamSpec = {
  key: string;
  label: string;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  default: number | string;
};

export type Ctx = { t: Trade; e: Eff; review: ReviewState };
type Params = Record<string, number | string>;

export type CondDef =
  | {
      id: string; label: string; group: string; help: string; kind: "number";
      unit?: string; params?: ParamSpec[]; scenario?: boolean;
      read: (c: Ctx, p: Params) => number | null;
      defaultOp: NumOp; defaultValue: number; step?: number;
    }
  | {
      id: string; label: string; group: string; help: string; kind: "bool";
      params?: ParamSpec[]; scenario?: boolean;
      read: (c: Ctx, p: Params) => boolean | null;
    }
  | {
      id: string; label: string; group: string; help: string; kind: "enum";
      params?: ParamSpec[]; scenario?: boolean; multi?: boolean;
      options: (ds: Dataset[]) => { value: string; label: string }[];
      read: (c: Ctx, p: Params) => string | string[] | null;
    };

export type NumOp = "gte" | "gt" | "lte" | "lt" | "eq" | "between";
export const NUM_OPS: { value: NumOp; label: string }[] = [
  { value: "gte", label: "≥" },
  { value: "gt", label: ">" },
  { value: "lte", label: "≤" },
  { value: "lt", label: "<" },
  { value: "eq", label: "=" },
  { value: "between", label: "between" },
];

const at = <T,>(arr: (T | null)[] | null | undefined, i: number): T | null => {
  if (!arr || i < 0 || i >= arr.length) return null;
  const v = arr[i];
  return v === null || v === undefined ? null : v;
};

const hourOf = (s: string | null) => (s && s.length >= 13 ? parseInt(s.slice(11, 13), 10) : null);
const minuteOfDay = (s: string | null) =>
  s && s.length >= 16 ? parseInt(s.slice(11, 13), 10) * 60 + parseInt(s.slice(14, 16), 10) : null;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const weekdayOf = (s: string | null) => {
  if (!s || s.length < 10) return null;
  const [y, mo, d] = s.slice(0, 10).split("-").map(Number);
  return WEEKDAYS[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()];
};
/** The trade's own time: the fill, or (no fill) the M5 retest. PT. */
const tradeTime = (t: Trade) => t.entryTime || t.retest;

export const TAG_LABELS: Record<string, string> = {
  globex_eth_open: "Globex/ETH open fill (15:00-15:05 PT)",
  low_liquidity: "News / thin book",
  swerve_blocked: "Swerve-blocked (not taken)",
  swerved: "Swerved",
  crest_refined: "Crest-refined entry",
  eod_flat: "End-of-day flat",
  "volume-spike": "Volume spike at fill",
  spike_confl: "H1 spike confluence",
  fading_bias: "Fading a live H1 bias",
  bias_served: "Bias already served",
  r_below_min: "R below the report's minimum",
};

const idxParam = (key: string, label: string, min: number, max: number, dflt: number): ParamSpec => ({
  key, label, min, max, step: 1, default: dflt,
});

export const CONDITIONS: CondDef[] = [
  // ---- trade
  {
    id: "rr", label: "R on offer", group: "Trade", kind: "number", scenario: true,
    help: "Reward:risk at entry (target pts / stop pts) under the target rule in force.",
    read: ({ e }) => (Number.isNaN(e.rr) ? null : e.rr), defaultOp: "gte", defaultValue: 0.5, step: 0.1,
  },
  {
    id: "risk", label: "Stop size", group: "Trade", kind: "number", unit: "pt",
    help: "Distance from the fill to the stop, in points.",
    read: ({ t }) => t.risk, defaultOp: "lte", defaultValue: 10, step: 0.25,
  },
  {
    id: "direction", label: "Direction", group: "Trade", kind: "enum", multi: true,
    help: "LHPB trades are longs, LLPB trades are shorts.",
    options: () => [{ value: "LHPB", label: "Long (LHPB)" }, { value: "LLPB", label: "Short (LLPB)" }],
    read: ({ t }) => t.type,
  },
  {
    id: "members", label: "Merged M5 levels", group: "Trade", kind: "number",
    help: "How many mutually-confluent M5 levels were merged into this one trade.",
    read: ({ t }) => t.members, defaultOp: "gte", defaultValue: 2, step: 1,
  },
  {
    id: "h1confl", label: "H1 P0 confluence", group: "Trade", kind: "number",
    help: "Count of same-type H1 P0s near the fill (the H1 P0 confl column).",
    read: ({ t }) => t.h1confl, defaultOp: "gte", defaultValue: 1, step: 1,
  },
  {
    id: "mode", label: "Target rule used", group: "Trade", kind: "enum", multi: true, scenario: true,
    help: "Which target rule the trade ends up on (with both on, the farther target wins).",
    options: () => TARGET_MODES.map((m) => ({ value: m, label: TARGET_MODE_LABELS[m] })),
    read: ({ e }) => e.mode,
  },
  {
    id: "outcome", label: "Outcome", group: "Trade", kind: "enum", multi: true, scenario: true,
    help: "Win / loss / no trade under the current scenario. This is the result itself -- filtering on it is look-ahead, for review only.",
    options: () => [
      { value: "win", label: "Win" }, { value: "loss", label: "Loss" }, { value: "no_trade", label: "No trade" },
    ],
    read: ({ e }) => e.bucket,
  },
  {
    id: "mgmt_changed", label: "Management changes it", group: "Trade", kind: "bool", scenario: true,
    help: "The thrust-trail / RR-floor management rules change this trade's outcome (whether or not management is switched on).",
    read: ({ e }) => (e.m ? e.m.mgmtFired : null),
  },
  {
    id: "qx_fires", label: "Quick exit would fire", group: "Trade", kind: "bool", scenario: true,
    help: "The quick-exit check, at the scenario's settings, would close this trade (whether or not quick exit is switched on).",
    read: ({ e, t }) => (e.noTrade || !t.qx ? null : e.qx !== null),
  },
  // ---- timing
  {
    id: "daygap", label: "P1→P2 gap (trading days)", group: "Timing", kind: "number",
    help: "Globex reopen-to-reopen trading days between the level's P1 breakout and its P2 retest.",
    read: ({ t }) => t.daygap, defaultOp: "lte", defaultValue: 1, step: 1,
  },
  {
    id: "h1gap", label: "P1→P2 gap (H1 candles)", group: "Timing", kind: "number",
    help: "Whole H1 candles that close strictly between P1 and P2.",
    read: ({ t }) => t.h1gap, defaultOp: "gte", defaultValue: 3, step: 1,
  },
  {
    id: "mingap", label: "P1→P2 gap (minutes)", group: "Timing", kind: "number", unit: "min",
    help: "Minutes between the P1 and P2 M5 candles.",
    read: ({ t }) => t.mingap, defaultOp: "gte", defaultValue: 30, step: 5,
  },
  {
    id: "hour", label: "Time of day (PT)", group: "Timing", kind: "number", unit: "h",
    help: "The fill's time of day in PT as hours, e.g. 6.5 = 06:30 (the retest time for rows with no fill).",
    read: ({ t }) => {
      const m = minuteOfDay(tradeTime(t));
      return m === null ? null : m / 60;
    },
    defaultOp: "between", defaultValue: 6.5, step: 0.25,
  },
  {
    id: "weekday", label: "Weekday (PT)", group: "Timing", kind: "enum", multi: true,
    help: "Day of the week of the fill (PT). Sunday = the Globex evening open.",
    options: () => WEEKDAYS.map((d) => ({ value: d, label: d })),
    read: ({ t }) => weekdayOf(tradeTime(t)),
  },
  {
    id: "month", label: "Month", group: "Timing", kind: "enum", multi: true,
    help: "Calendar month of the fill (PT).",
    options: () =>
      ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"].map((m, i) => ({
        value: m, label: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][i],
      })),
    read: ({ t }) => {
      const s = tradeTime(t);
      return s ? s.slice(5, 7) : null;
    },
  },
  // ---- structure
  {
    id: "er", label: "Pre-P1 structure (ER)", group: "Structure", kind: "number",
    params: [idxParam("k", "k", 2, 60, 10)],
    help: "Kaufman efficiency ratio of the k M5 closes before P1: 0 = chop / real consolidation, 1 = a straight run.",
    read: ({ t }, p) => at(t.erByK, Number(p.k) - 2), defaultOp: "lte", defaultValue: 0.5, step: 0.05,
  },
  {
    id: "p1ratio", label: "P1 range ratio", group: "Structure", kind: "number", unit: "×",
    params: [idxParam("w", "window", 1, 50, 20)],
    help: "The P1 breakout candle's range divided by the average range of the w M5 candles before it.",
    read: ({ t }, p) => at(t.p1RatioByWindow, Number(p.w) - 1), defaultOp: "gte", defaultValue: 2, step: 0.1,
  },
  {
    id: "box", label: "Tight box", group: "Structure", kind: "number", unit: "× avg candle",
    params: [idxParam("n", "n candles", 2, 24, 6)],
    help: "Span of the n M5 candles right before P1, in average M5 candles (20-candle average). Small = coiling before the breakout.",
    read: ({ t }, p) => at(t.boxByN, Number(p.n) - 2), defaultOp: "lt", defaultValue: 2, step: 0.1,
  },
  {
    id: "appr", label: "Slow approach", group: "Structure", kind: "number", unit: "× avg candle",
    params: [idxParam("k", "k candles back", 1, 12, 3)],
    help: "How far price travelled toward the level, from the open of the k-th closed M5 candle before the fill's candle to the fill, in average M5 candles.",
    read: ({ t }, p) => at(t.apprByK, Number(p.k) - 1), defaultOp: "lte", defaultValue: 1, step: 0.1,
  },
  {
    id: "fresh", label: "Fresh price (visits)", group: "Structure", kind: "number",
    params: [
      idxParam("days", "look back days", 1, 5, 2),
      {
        key: "tol", label: "within pt", default: "1",
        options: [{ value: "0.5", label: "0.5" }, { value: "1", label: "1" }, { value: "2", label: "2" }],
      },
    ],
    help: "Separate visits to the planned entry price before the level's P0 (a run of touching candles counts once). Needs a report regenerated with the fresh-price readings.",
    read: ({ t }, p) => {
      const row = at(t.fresh, Number(p.days) - 1);
      const ti = ["0.5", "1", "2"].indexOf(String(p.tol));
      return row && ti >= 0 ? at(row, ti) : null;
    },
    defaultOp: "lte", defaultValue: 2, step: 1,
  },
  {
    id: "sfp", label: "M5 SFP in our favour", group: "Structure", kind: "bool",
    params: [idxParam("back", "within last candles", 1, 12, 6)],
    help: "One of the last n closed M5 candles before the fill's candle was an SFP pointing our way. Needs a report regenerated with the SFP readings.",
    read: ({ t }, p) => (t.sfp === null ? null : t.sfp.some((o) => o <= Number(p.back))),
  },
  // ---- tags & context
  {
    id: "tag", label: "Tagged", group: "Tags", kind: "enum", multi: true, scenario: true,
    help: "The trade carries ANY of the chosen tags (per-rule tags such as end-of-day flat follow the target rule in force).",
    options: (ds) => {
      const seen = new Set<string>(Object.keys(TAG_LABELS));
      for (const d of ds)
        for (const t of d.facts.trades) {
          t.tags.forEach((x) => seen.add(x));
          Object.values(t.modes).forEach((m) => m.tags.forEach((x) => seen.add(x)));
        }
      return [...seen].sort().map((v) => ({ value: v, label: TAG_LABELS[v] || v }));
    },
    read: ({ e }) => e.tags,
  },
  {
    id: "vspikeoffs", label: "Volume spike offset", group: "Tags", kind: "number", unit: "s",
    help: "Seconds between the fill and the nearest qualifying volume-spike second (only volume-spike rows have one).",
    read: ({ t }) => t.vspikeoffs, defaultOp: "lte", defaultValue: 10, step: 1,
  },
  {
    id: "bias", label: "Live H1 bias", group: "Tags", kind: "enum", multi: true,
    help: "Which live H1 biases the trade entered under (the Bias column): bullish, bearish, or none.",
    options: () => [
      { value: "bull", label: "Bullish" }, { value: "bear", label: "Bearish" }, { value: "none", label: "None" },
    ],
    read: ({ t }) => {
      const out: string[] = [];
      if (t.bias?.includes("▲")) out.push("bull");
      if (t.bias?.includes("▼")) out.push("bear");
      return out.length ? out : ["none"];
    },
  },
  // ---- review (from the report's own review store)
  {
    id: "reviewed", label: "Reviewed", group: "Review", kind: "bool",
    help: "Ticked Reviewed in the report.", read: ({ review }) => !!review.reviewed,
  },
  {
    id: "valid", label: "Valid", group: "Review", kind: "bool",
    help: "Ticked Valid in the report.", read: ({ review }) => !!review.valid,
  },
  {
    id: "replayed", label: "Replayed", group: "Review", kind: "bool",
    help: "Ticked Replayed in the report.", read: ({ review }) => !!review.replayed,
  },
  {
    id: "has_notes", label: "Has notes", group: "Review", kind: "bool",
    help: "Has review notes in the report.", read: ({ review }) => !!(review.notes && review.notes.trim()),
  },
  {
    id: "done", label: "Done", group: "Review", kind: "bool",
    help: "Ticked Done in the report.", read: ({ review }) => !!review.done,
  },
];

export const COND_BY_ID: Record<string, CondDef> = Object.fromEntries(CONDITIONS.map((c) => [c.id, c]));

// ----------------------------------------------------------------- tree ----

export type CondNode = {
  kind: "cond";
  id: string;
  cond: string;
  params: Params;
  op?: NumOp; // number
  value?: number;
  value2?: number; // between
  values?: string[]; // enum
  negate?: boolean;
  missing?: "fail" | "pass"; // no reading on the row
  off?: boolean; // kept in the tree but ignored
};
export type GroupNode = {
  kind: "group";
  id: string;
  op: "all" | "any" | "none";
  min?: number; // any: at least this many children
  label?: string;
  children: Node[];
  off?: boolean;
};
/** Drop rows matching `drop`, unless they match `unless` -- a soft exclude. */
export type ExceptNode = {
  kind: "except";
  id: string;
  drop: GroupNode;
  unless: GroupNode;
  label?: string;
  off?: boolean;
};
export type Node = CondNode | GroupNode | ExceptNode;

let _seq = 0;
export const newId = () => `n${Date.now().toString(36)}${(_seq++).toString(36)}`;

export function newCond(condId: string): CondNode {
  const d = COND_BY_ID[condId];
  const params: Params = {};
  (d.params || []).forEach((p) => (params[p.key] = p.default));
  const n: CondNode = { kind: "cond", id: newId(), cond: condId, params };
  if (d.kind === "number") {
    n.op = d.defaultOp;
    n.value = d.defaultValue;
    if (d.defaultOp === "between") n.value2 = d.defaultValue + 1;
  }
  if (d.kind === "enum") n.values = [];
  return n;
}
export const newGroup = (op: GroupNode["op"] = "all", children: Node[] = []): GroupNode => ({
  kind: "group", id: newId(), op, children,
});
export const newExcept = (drop: Node[] = [], unless: Node[] = []): ExceptNode => ({
  kind: "except", id: newId(), drop: newGroup("all", drop), unless: newGroup("any", unless),
});

/** A leaf's reading on one row, for display (null = no reading). */
export function readCond(n: CondNode, c: Ctx): number | boolean | string | string[] | null {
  const d = COND_BY_ID[n.cond];
  return d ? d.read(c, n.params) : null;
}

function testLeaf(n: CondNode, c: Ctx): boolean {
  const d = COND_BY_ID[n.cond];
  if (!d) return true;
  const v = d.read(c, n.params);
  if (v === null || (typeof v === "number" && Number.isNaN(v))) return n.missing === "pass";
  let ok: boolean;
  if (d.kind === "number") {
    const x = v as number;
    const a = n.value ?? NaN;
    // An empty value box means "no limit", as in the report's own numeric filters.
    if (Number.isNaN(a)) ok = true;
    else
      switch (n.op) {
        case "gte": ok = x >= a; break;
        case "gt": ok = x > a; break;
        case "lte": ok = x <= a; break;
        case "lt": ok = x < a; break;
        case "eq": ok = x === a; break;
        case "between": {
          const b = n.value2 ?? NaN;
          ok = x >= a && (Number.isNaN(b) || x <= b);
          break;
        }
        default: ok = true;
      }
  } else if (d.kind === "bool") {
    ok = v as boolean;
  } else {
    const want = n.values || [];
    if (!want.length) ok = true; // nothing chosen yet: no constraint
    else ok = (Array.isArray(v) ? v : [v as string]).some((x) => want.includes(x));
  }
  return n.negate ? !ok : ok;
}

// Rows are evaluated as whole vectors: one Uint8Array (1 = pass) per node.
type Vec = Uint8Array;

export type Evaluated = {
  ctx: Ctx[];
  leaf: Map<string, Vec>; // node id -> leaf result
  keep: Vec;
};

const isActive = (n: Node) => !n.off;

function combine(n: Node, rows: number, leaf: Map<string, Vec>, skip: string | null): Vec | null {
  // null = neutral (node off, skipped, or an empty group): the parent ignores it.
  if (!isActive(n) || n.id === skip) return null;
  if (n.kind === "cond") return leaf.get(n.id) || null;
  if (n.kind === "except") {
    const drop = combine(n.drop, rows, leaf, skip);
    if (!drop) return null;
    const unless = combine(n.unless, rows, leaf, skip);
    const out = new Uint8Array(rows);
    for (let i = 0; i < rows; i++) out[i] = drop[i] && !(unless && unless[i]) ? 0 : 1;
    return out;
  }
  const kids = n.children.map((k) => combine(k, rows, leaf, skip)).filter((v): v is Vec => v !== null);
  if (!kids.length) return null;
  const out = new Uint8Array(rows);
  const need = n.op === "any" ? Math.max(1, Math.min(n.min || 1, kids.length)) : 0;
  for (let i = 0; i < rows; i++) {
    let hits = 0;
    for (const k of kids) hits += k[i];
    if (n.op === "all") out[i] = hits === kids.length ? 1 : 0;
    else if (n.op === "any") out[i] = hits >= need ? 1 : 0;
    else out[i] = hits === 0 ? 1 : 0; // none
  }
  return out;
}

export function walk(n: Node, f: (n: Node, parent: Node | null) => void, parent: Node | null = null) {
  f(n, parent);
  if (n.kind === "group") n.children.forEach((k) => walk(k, f, n));
  if (n.kind === "except") {
    walk(n.drop, f, n);
    walk(n.unless, f, n);
  }
}

export function evaluate(ds: Dataset, sc: Scenario, tree: Node): Evaluated {
  const ctx: Ctx[] = ds.facts.trades.map((t) => ({
    t, e: applyScenario(t, sc), review: ds.review[t.key] || {},
  }));
  const rows = ctx.length;
  const leaf = new Map<string, Vec>();
  walk(tree, (n) => {
    if (n.kind !== "cond") return;
    const d = COND_BY_ID[n.cond];
    // Unknown condition, or a list condition with nothing picked yet: neutral.
    if (!d || (d.kind === "enum" && !(n.values || []).length)) return;
    const v = new Uint8Array(rows);
    for (let i = 0; i < rows; i++) v[i] = testLeaf(n, ctx[i]) ? 1 : 0;
    leaf.set(n.id, v);
  });
  const keep = combine(tree, rows, leaf, null) || new Uint8Array(rows).fill(1);
  return { ctx, leaf, keep };
}

/** The kept vector with node `skipId` taken out of the tree. */
export function keepWithout(ev: Evaluated, tree: Node, skipId: string): Vec {
  return combine(tree, ev.ctx.length, ev.leaf, skipId) || new Uint8Array(ev.ctx.length).fill(1);
}

/** Each node's own pass vector (true where that sub-tree passes). */
export function nodeVector(ev: Evaluated, n: Node): Vec | null {
  return combine({ ...n, off: false } as Node, ev.ctx.length, ev.leaf, null);
}

// ---------------------------------------------------------------- stats ----

export type Stats = {
  n: number;
  wins: number;
  losses: number;
  winRate: number;
  avgR: number;
  totalR: number;
  pnl: number;
  comm: number;
  maxWinMae: number;
  maxLossMfe: number;
  maxDD: number; // deepest peak-to-trough of cumulative R, in R
  profitFactor: number;
  curve: number[]; // cumulative R, in fill-time order
  shown: number; // kept rows, trades or not
};

/** Same counting as the report's recomputeDynStats: no-trade rows never count. */
export function stats(ev: Evaluated, keep: Vec = ev.keep): Stats {
  let n = 0, wins = 0, sumR = 0, pnl = 0, comm = 0, maxWinMae = 0, maxLossMfe = 0, gp = 0, gl = 0, shown = 0;
  const pts: { t: string; r: number }[] = [];
  ev.ctx.forEach(({ t, e }, i) => {
    if (!keep[i]) return;
    shown++;
    if (e.noTrade) return;
    if (!Number.isNaN(e.r)) {
      n++;
      sumR += e.r;
      if (e.r > 0) gp += e.r;
      else gl -= e.r;
      if (t.comm !== null && !Number.isNaN(t.comm)) comm += t.comm;
      if (e.outcome === "target") {
        wins++;
        const mae = e.m?.mae;
        if (mae !== null && mae !== undefined && mae > maxWinMae) maxWinMae = mae;
      } else if (e.outcome === "stop") {
        const mfe = e.m?.mfe;
        if (mfe !== null && mfe !== undefined && mfe > maxLossMfe) maxLossMfe = mfe;
      }
      pts.push({ t: t.entryTime || t.retest || "", r: e.r });
    }
    if (!Number.isNaN(e.pnl)) pnl += e.pnl;
  });
  pts.sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
  let cum = 0, peak = 0, maxDD = 0;
  const curve = pts.map((p) => {
    cum += p.r;
    peak = Math.max(peak, cum);
    maxDD = Math.max(maxDD, peak - cum);
    return cum;
  });
  return {
    n, wins, losses: n - wins, winRate: n ? (wins / n) * 100 : 0, avgR: n ? sumR / n : 0,
    totalR: sumR, pnl, comm, maxWinMae, maxLossMfe, maxDD,
    profitFactor: gl > 0 ? gp / gl : gp > 0 ? Infinity : 0, curve, shown,
  };
}

// -------------------------------------------------------- presets / text ----

/** The report's own starting filters: R ≥ 0.5, P1→P2 ≥ 3 H1 candles,
 * pre-P1 ER(k=10) ≤ 0.5, P1 range ratio(w=20) ≥ 2 -- read from the report's
 * panel when the facts carry it. */
export function reportDefaultTree(facts?: FactsFile): GroupNode {
  const d = facts?.defaults ?? {
    num: {
      rr: { op: "gte", value: 0.5 }, daygap: { op: "any" }, h1gap: { op: "gte", value: 3 },
      mingap: { op: "any" }, er: { op: "lte", value: 0.5 }, p1ratio: { op: "gte", value: 2 },
      vspikeoffs: { op: "any" },
    },
    inputs: { "f-prep1-k": 10, "f-weakp1-window": 20 },
    selects: {}, checked: {},
  };
  const numd = (key: string, cond: string, params: Params = {}) => {
    const x = d?.num?.[key];
    const def = COND_BY_ID[cond] as Extract<CondDef, { kind: "number" }>;
    // A filter the report's panel doesn't have, or has on "any", is left out.
    if (!x || !x.op || x.op === "any") return null;
    const op = x.op as NumOp;
    const n = newCond(cond);
    n.op = op;
    n.value = x?.value ?? def.defaultValue;
    n.params = { ...n.params, ...params };
    return n;
  };
  const kids = [
    numd("rr", "rr"),
    numd("daygap", "daygap"),
    numd("h1gap", "h1gap"),
    numd("mingap", "mingap"),
    numd("er", "er", { k: d?.inputs?.["f-prep1-k"] ?? 10 }),
    numd("p1ratio", "p1ratio", { w: d?.inputs?.["f-weakp1-window"] ?? 20 }),
    numd("vspikeoffs", "vspikeoffs"),
  ].filter((x): x is CondNode => x !== null);
  const g = newGroup("all", kids);
  g.label = "Report defaults";
  return g;
}

const OP_TXT: Record<string, string> = { gte: ">=", gt: ">", lte: "<=", lt: "<", eq: "=" };

/** One-line readable form of a tree, e.g. for the summary line and exports. */
export function describe(n: Node): string {
  const off = n.off ? "[off] " : "";
  if (n.kind === "cond") {
    const d = COND_BY_ID[n.cond];
    if (!d) return "?";
    const ps = (d.params || []).map((p) => `${p.key}=${n.params[p.key]}`).join(",");
    const name = d.id + (ps ? `(${ps})` : "");
    let s: string;
    if (d.kind === "number")
      s = n.op === "between" ? `${name} in [${n.value}, ${n.value2 ?? "∞"}]` : `${name} ${OP_TXT[n.op || "gte"]} ${n.value}`;
    else if (d.kind === "bool") s = name;
    else s = `${name} in {${(n.values || []).join(", ")}}`;
    return off + (n.negate ? `not ${s}` : s);
  }
  if (n.kind === "except") return `${off}drop ${describe(n.drop)} unless ${describe(n.unless)}`;
  const kids = n.children.map(describe);
  if (!kids.length) return `${off}(empty)`;
  if (n.op === "none") return `${off}none(${kids.join(", ")})`;
  if (n.op === "any" && (n.min || 1) > 1) return `${off}at least ${n.min} of (${kids.join(", ")})`;
  return off + "(" + kids.join(n.op === "all" ? " and " : " or ") + ")";
}
