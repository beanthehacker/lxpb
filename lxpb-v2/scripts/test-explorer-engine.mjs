// Parity test: the /explorer engine (lib/explorer/engine.ts) must give the
// same stats as the report's own in-page filters for the same settings.
//
// Each SCENARIO below is written twice: once as report panel settings, once
// as the equivalent explorer scenario + filter tree. The report side is run
// in the real report page by headless Edge/Chrome (its own recomputeDynStats),
// the explorer side by the engine over the facts file, and every stat box is
// compared as the report prints it.
//
//   node scripts/test-explorer-engine.mjs <report.html(.gz)> [<facts.json.gz>]
//
// Needs Node 23.6+ (runs the .ts engine directly) and Edge or Chrome.
import { readFileSync, writeFileSync, mkdtempSync, existsSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import * as E from "../lib/explorer/engine.ts";

const ANY = { rr: "any", daygap: "any", h1gap: "any", mingap: "any", er: "any", p1ratio: "any", vspikeoffs: "any" };

const cond = (id, patch = {}) => Object.assign(E.newCond(id), patch);
const num = (id, op, value, params = {}, extra = {}) =>
  cond(id, { op, value, params: { ...E.newCond(id).params, ...params }, ...extra });
const all = (...k) => E.newGroup("all", k);
const any = (...k) => E.newGroup("any", k);
const tagIn = (...values) => cond("tag", { values });
const defaults = (facts) => E.reportDefaultTree(facts).children;

// report: settings applied to the page on top of its own starting state.
// tree(facts) / scenario: the same thing in explorer terms.
const SCENARIOS = [
  { name: "report defaults", report: {}, tree: (f) => all(...defaults(f)) },
  { name: "no filters", report: { num: ANY }, tree: () => all() },
  {
    name: "defaults, exclude fading-bias + eod flat",
    report: { excl: ["fading_bias", "eod_flat"] },
    tree: (f) => all(...defaults(f), cond("tag", { values: ["fading_bias", "eod_flat"], negate: true })),
  },
  {
    name: "defaults, only volume-spike",
    report: { iso: ["volume-spike"] },
    tree: (f) => all(...defaults(f), tagIn("volume-spike")),
  },
  {
    // (fading_bias and bias_served share one radio group in the report, so
    // only one of those two can be "only" at a time.)
    name: "no filters, only fading-bias or volume-spike, excl ignored",
    report: { num: ANY, iso: ["fading_bias", "volume-spike"], excl: ["low_liquidity"] },
    tree: () => all(tagIn("fading_bias", "volume-spike")),
  },
  {
    name: "swing extreme only + management",
    report: { num: ANY, modes: ["swing-extreme"], mgmt: true },
    scenario: { targetModes: ["swing-extreme"], mgmt: true },
    tree: () => all(),
  },
  {
    name: "opposite-zz only, defaults",
    report: { modes: ["opposite-m5-zz"] },
    scenario: { targetModes: ["opposite-m5-zz"] },
    tree: (f) => all(...defaults(f)),
  },
  {
    name: "quick exit + (box or approach)",
    report: {
      inputs: { "f-qx-sec": 20, "f-qx-min": 1, "f-qx-slip": 0.25 },
      checks: { "f-qx-on": true, "f-box-on": true, "f-appr-on": true }, selects: { "f-setup-join": "or" },
    },
    scenario: { qx: { on: true, sec: 20, min: 1, slip: 0.25 } },
    tree: (f) => all(...defaults(f), any(num("box", "lt", 2, { n: 6 }), num("appr", "lte", 1, { k: 3 }))),
  },
  {
    name: "box and approach, custom params, several numeric filters",
    report: {
      num: { ...ANY, er: ["lte", 0.6], p1ratio: ["gte", 1.2], mingap: ["gte", 30], daygap: ["lte", 1], rr: ["gt", 0.8] },
      inputs: { "f-prep1-k": 6, "f-weakp1-window": 10, "f-box-n": 8, "f-box-max": 3, "f-appr-k": 4, "f-appr-max": 1.5 },
      checks: { "f-box-on": true, "f-appr-on": true }, selects: { "f-setup-join": "and" },
    },
    tree: () =>
      all(
        num("rr", "gt", 0.8), num("daygap", "lte", 1), num("mingap", "gte", 30),
        num("er", "lte", 0.6, { k: 6 }), num("p1ratio", "gte", 1.2, { w: 10 }),
        num("box", "lt", 3, { n: 8 }), num("appr", "lte", 1.5, { k: 4 }),
      ),
  },
  {
    name: "wins only (outcome filter)",
    report: { outcome: ["win"] },
    tree: (f) => all(...defaults(f), cond("outcome", { values: ["win"] })),
  },
  {
    name: "management + quick exit, volume-spike offset <= 10",
    report: { num: { ...ANY, vspikeoffs: ["lte", 10] }, mgmt: true, checks: { "f-qx-on": true } },
    scenario: { mgmt: true, qx: { on: true, sec: 30, min: 0.75, slip: 0.25 } },
    tree: () => all(num("vspikeoffs", "lte", 10)),
  },
];

const STAT_IDS = ["sum-trades", "sum-win-rate", "sum-wins", "sum-losses", "sum-avg-r", "sum-total-r",
  "sum-total-pnl", "sum-total-comm", "sum-max-win-mae", "sum-max-loss-mfe"];

function engineStats(s) {
  const f1 = (x) => x.toFixed(1), f2 = (x) => x.toFixed(2);
  return {
    "sum-trades": String(s.n), "sum-win-rate": f1(s.winRate) + "%", "sum-wins": String(s.wins),
    "sum-losses": String(s.losses), "sum-avg-r": f2(s.avgR), "sum-total-r": f1(s.totalR),
    "sum-total-pnl": (s.pnl >= 0 ? "+" : "") + f1(s.pnl),
    "sum-total-comm": "$" + s.comm.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    "sum-max-win-mae": f2(s.maxWinMae), "sum-max-loss-mfe": f2(s.maxLossMfe),
  };
}

// Runs inside the report page, after its own scripts.
function pageScript(scenarios, statIds) {
  const initial = Array.from(document.querySelectorAll(".filter-panel input, .filter-panel select")).map((el) => [
    el, el.type === "checkbox" || el.type === "radio" ? el.checked : el.value,
  ]);
  const out = [];
  for (const sc of scenarios) {
    initial.forEach(([el, v]) => (el.type === "checkbox" || el.type === "radio" ? (el.checked = v) : (el.value = v)));
    const r = sc.report;
    Object.entries(r.num || {}).forEach(([t, v]) => {
      const [op, val] = Array.isArray(v) ? v : [v];
      document.querySelector(`.f-num-op[data-target="${t}"]`).value = op;
      if (val !== undefined) document.querySelector(`.f-num-val[data-target="${t}"]`).value = val;
    });
    Object.entries(r.inputs || {}).forEach(([id, v]) => (document.getElementById(id).value = v));
    Object.entries(r.selects || {}).forEach(([id, v]) => (document.getElementById(id).value = v));
    Object.entries(r.checks || {}).forEach(([id, v]) => (document.getElementById(id).checked = v));
    (r.excl || []).forEach((t) => (document.querySelector(`.f-dyn-exclude[data-tag="${t}"]`).checked = true));
    (r.iso || []).forEach((t) => (document.querySelector(`.f-dyn-isolate[data-tag="${t}"]`).checked = true));
    if (r.modes) document.querySelectorAll(".f-target-mode").forEach((cb) => (cb.checked = r.modes.includes(cb.dataset.mode)));
    if (r.mgmt !== undefined) document.getElementById("mgmt-thrust-trail").checked = r.mgmt;
    if (r.outcome) document.querySelectorAll(".f-outcome").forEach((cb) => (cb.checked = r.outcome.includes(cb.value)));
    recomputeDynStats();
    out.push({ name: sc.name, stats: Object.fromEntries(statIds.map((id) => [id, document.getElementById(id).textContent])) });
  }
  const pre = document.createElement("pre");
  pre.id = "parity-out";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
}

function browser() {
  for (const p of [
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
  ]) if (existsSync(p)) return p;
  throw new Error("no Edge/Chrome found");
}

// ---- tree logic on a synthetic dataset (no report needed) ----------------
function treeLogicTests() {
  const mk = (i, tags, h1gap) => ({
    idx: i, i, key: "k" + i, type: "LHPB", filled: true, reason: null, retest: null, entryTime: `2026-01-0${i + 1} 06:00:00`,
    entry: 1, stop: 0, risk: 1, comm: 1, contracts: 1, members: 1, h1confl: 0, bias: null, biasTitle: null,
    tags, daygap: 0, h1gap, mingap: 0, vspikeoffs: null, erByK: null, p1RatioByWindow: null, boxByN: null,
    apprByK: null, fresh: null, sfp: null, qx: null,
    modes: { "opposite-m5-zz": { dist: 1, target: 2, rr: 1, r: 1, pnl: 1, outcome: "target", label: "WIN", mgmtR: 1, mgmtPnl: 1,
      mgmtOutcome: "target", mgmtFired: false, tags: [], exitSec: 1, mgmtExitSec: 1, mae: 0, mfe: 1 } },
  });
  // 0: A      1: A B      2: B      3: none     4: A, h1gap unknown
  const trades = [mk(0, ["a"], 1), mk(1, ["a", "b"], 5), mk(2, ["b"], 5), mk(3, [], 0), mk(4, ["a"], null)];
  const ds = { id: "s", label: "s", facts: { trades }, review: { k2: { valid: true } } };
  const kept = (tree) => Array.from(E.evaluate(ds, E.DEFAULT_SCENARIO, tree).keep).flatMap((v, i) => (v ? [i] : []));
  const A = () => tagIn("a"), B = () => tagIn("b"), gap = (op, v, x = {}) => num("h1gap", op, v, {}, x);
  const cases = [
    ["strict exclude A", all(cond("tag", { values: ["a"], negate: true })), [2, 3]],
    ["only A", all(A()), [0, 1, 4]],
    ["drop A unless B (soft exclude)", E.newExcept([A()], [B()]), [1, 2, 3]],
    ["drop A unless B, only B strict", all(E.newExcept([A()], [B()]), B()), [1, 2]],
    ["at least 2 of (A, B, gap>=3)", Object.assign(any(A(), B(), gap("gte", 3)), { min: 2 }), [1, 2]],
    ["none of (A, B)", E.newGroup("none", [A(), B()]), [3]],
    ["missing reading fails", all(gap("lte", 10)), [0, 1, 2, 3]],
    ["missing reading fails even when negated", all(gap("gt", 3, { negate: true })), [0, 3]],
    ["missing reading can pass", all(gap("gte", 3, { missing: "pass" })), [1, 2, 4]],
    ["off node is ignored", all(Object.assign(A(), { off: true }), B()), [1, 2]],
    ["empty list condition is neutral", all(cond("tag", { values: [] })), [0, 1, 2, 3, 4]],
    ["review data", all(cond("valid")), [2]],
  ];
  let bad = 0;
  for (const [name, tree, want] of cases) {
    const got = kept(tree);
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) bad++;
    console.log(`${ok ? "ok  " : "FAIL"} logic: ${name}${ok ? "" : ` -> kept ${got}, want ${want}`}`);
  }
  // impact: removing the "unless" leaves a strict exclude
  const tree = E.newExcept([A()], [B()]);
  const ev = E.evaluate(ds, E.DEFAULT_SCENARIO, tree);
  const without = Array.from(E.keepWithout(ev, tree, tree.unless.children[0].id)).flatMap((v, i) => (v ? [i] : []));
  if (JSON.stringify(without) !== JSON.stringify([2, 3])) { bad++; console.log(`FAIL logic: impact -> ${without}`); }
  else console.log("ok   logic: impact of removing the unless");
  return bad;
}
const logicFailures = treeLogicTests();
if (process.argv.length <= 2) process.exit(logicFailures ? 1 : 0);

const [reportPath, factsArg] = process.argv.slice(2);
if (!reportPath) {
  console.error("usage: node scripts/test-explorer-engine.mjs <report.html(.gz)> [<facts.json.gz>]");
  process.exit(2);
}
const read = (p) => (p.endsWith(".gz") ? gunzipSync(readFileSync(p)) : readFileSync(p)).toString("utf8");
const factsPath = factsArg || reportPath.replace(/\.html(\.gz)?$/, ".trades.json.gz");
const facts = JSON.parse(read(factsPath));

const dir = mkdtempSync(path.join(tmpdir(), "explorer-parity-"));
const page = path.join(dir, "report.html");
const payload = SCENARIOS.map((s) => ({ name: s.name, report: s.report }));
const inject = `<script>(${pageScript.toString()})(${JSON.stringify(payload)}, ${JSON.stringify(STAT_IDS)});</script>`;
writeFileSync(page, read(reportPath).replace(/<\/body>\s*<\/html>\s*$/, inject + "</body></html>"));
const dom = execFileSync(browser(), [
  "--headless=new", "--disable-gpu", "--no-first-run", "--virtual-time-budget=60000",
  `--user-data-dir=${path.join(dir, "profile")}`, "--dump-dom", pathToFileURL(page).href,
], { maxBuffer: 1 << 30, timeout: 600000 }).toString("utf8");
const m = dom.match(/<pre id="parity-out">([\s\S]*?)<\/pre>/);
if (!m) throw new Error("report page did not produce parity output (script error?)");
const reportOut = JSON.parse(m[1].replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"'));

const ds = { id: "t", label: "t", facts, review: {} };
let failed = 0;
SCENARIOS.forEach((sc, i) => {
  const scenario = { ...E.DEFAULT_SCENARIO, ...(sc.scenario || {}), qx: { ...E.DEFAULT_SCENARIO.qx, ...(sc.scenario?.qx || {}) } };
  const ev = E.evaluate(ds, scenario, sc.tree(facts));
  const mine = engineStats(E.stats(ev));
  const theirs = reportOut[i].stats;
  const diffs = STAT_IDS.filter((id) => mine[id] !== theirs[id]);
  if (diffs.length) failed++;
  console.log(`${diffs.length ? "FAIL" : "ok  "} ${sc.name}: ${theirs["sum-trades"]} trades, ${theirs["sum-total-r"]}R` +
    diffs.map((id) => `\n       ${id}: report ${theirs[id]} vs explorer ${mine[id]}`).join(""));
});
console.log(failed ? `\n${failed} scenario(s) differ` : `\nall ${SCENARIOS.length} scenarios match`);
process.exit(failed || logicFailures ? 1 : 0);
