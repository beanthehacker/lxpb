"use client";

import {
  COND_BY_ID, CONDITIONS, NUM_OPS, newCond, newExcept, newGroup,
  type CondNode, type Dataset, type ExceptNode, type GroupNode, type Node, type NumOp,
} from "@/lib/explorer/engine";
import s from "./explorer.module.css";

/** Per node: rows it passes (lead dataset) and, per dataset, how the stats
 * move if the node is taken out of the tree. */
export type Impact = {
  pass: number | null;
  per: { label: string; dN: number; dR: number }[];
};

const fmtR = (x: number) => (x >= 0 ? "+" : "") + x.toFixed(1);

function ImpactBadge({ imp, root }: { imp?: Impact; root?: boolean }) {
  if (!imp || root) return null;
  return (
    <span className={s.impact} title="What this part adds, per dataset: total R with it minus total R without it (positive = it helps), then the trades it takes out (−) or lets in (+, e.g. one member of an ANY group).">
      {imp.pass !== null && <span className={s.pass}>{imp.pass} pass</span>}
      {imp.per.map((p) => (
        <span key={p.label} className={p.dR > 0.05 ? s.good : p.dR < -0.05 ? s.bad : s.muted}>
          {p.label} {fmtR(p.dR)}R{p.dN > 0 ? ` / −${p.dN}` : p.dN < 0 ? ` / +${-p.dN}` : ""}
        </span>
      ))}
    </span>
  );
}

function AddMenu({ onAdd }: { onAdd: (n: Node) => void }) {
  const groups = [...new Set(CONDITIONS.map((c) => c.group))];
  return (
    <select
      className={s.add}
      value=""
      onChange={(e) => {
        const v = e.target.value;
        if (!v) return;
        if (v === "@all") onAdd(newGroup("all"));
        else if (v === "@any") onAdd(newGroup("any"));
        else if (v === "@none") onAdd(newGroup("none"));
        else if (v === "@except") onAdd(newExcept());
        else onAdd(newCond(v));
      }}
    >
      <option value="">+ add…</option>
      <optgroup label="Groups">
        <option value="@all">ALL of (and)</option>
        <option value="@any">ANY of (or / at least k)</option>
        <option value="@none">NONE of (exclude)</option>
        <option value="@except">Drop … unless … (soft exclude)</option>
      </optgroup>
      {groups.map((g) => (
        <optgroup key={g} label={g}>
          {CONDITIONS.filter((c) => c.group === g).map((c) => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

type Props = {
  node: Node;
  root?: boolean;
  onChange: (n: Node) => void;
  onRemove?: () => void;
  impact: Map<string, Impact>;
  datasets: Dataset[];
};

export default function FilterNode(props: Props) {
  const { node } = props;
  if (node.kind === "cond") return <CondRow {...props} node={node} />;
  if (node.kind === "except") return <ExceptBox {...props} node={node} />;
  return <GroupBox {...props} node={node} />;
}

function Toolbar({ node, onChange, onRemove }: { node: Node; onChange: (n: Node) => void; onRemove?: () => void }) {
  return (
    <span className={s.tools}>
      <label title="Switch this part off without deleting it">
        <input type="checkbox" checked={!node.off} onChange={(e) => onChange({ ...node, off: !e.target.checked })} /> on
      </label>
      {onRemove && (
        <button className={s.x} onClick={onRemove} title="Delete">
          ×
        </button>
      )}
    </span>
  );
}

function GroupBox({ node, root, onChange, onRemove, impact, datasets }: Props & { node: GroupNode }) {
  const set = (patch: Partial<GroupNode>) => onChange({ ...node, ...patch });
  const setChild = (i: number, k: Node) => set({ children: node.children.map((c, j) => (j === i ? k : c)) });
  const opLabel = { all: "ALL of", any: "ANY of", none: "NONE of" }[node.op];
  return (
    <div className={`${s.group} ${s["op_" + node.op]} ${node.off ? s.off : ""}`}>
      <div className={s.head}>
        <select value={node.op} onChange={(e) => set({ op: e.target.value as GroupNode["op"] })} title={opLabel}>
          <option value="all">Keep trades matching ALL of</option>
          <option value="any">Keep trades matching ANY of</option>
          <option value="none">Drop trades matching ANY of</option>
        </select>
        {node.op === "any" && (
          <label className={s.small}>
            at least{" "}
            <input
              type="number" min={1} max={Math.max(1, node.children.length)} value={node.min || 1}
              onChange={(e) => set({ min: Math.max(1, parseInt(e.target.value, 10) || 1) })}
            />
          </label>
        )}
        <input
          className={s.labelInput} placeholder="label (optional)" value={node.label || ""}
          onChange={(e) => set({ label: e.target.value || undefined })}
        />
        <ImpactBadge imp={impact.get(node.id)} root={root} />
        {!root && <Toolbar node={node} onChange={onChange} onRemove={onRemove} />}
      </div>
      <div className={s.kids}>
        {node.children.map((k, i) => (
          <FilterNode
            key={k.id} node={k} onChange={(n) => setChild(i, n)}
            onRemove={() => set({ children: node.children.filter((_, j) => j !== i) })}
            impact={impact} datasets={datasets}
          />
        ))}
        {!node.children.length && <div className={s.empty}>empty -- no effect</div>}
        <AddMenu onAdd={(n) => set({ children: [...node.children, n] })} />
      </div>
    </div>
  );
}

function ExceptBox({ node, onChange, onRemove, impact, datasets }: Props & { node: ExceptNode }) {
  return (
    <div className={`${s.group} ${s.op_except} ${node.off ? s.off : ""}`}>
      <div className={s.head}>
        <b>Drop trades matching</b>
        <span className={s.muted}>(ALL of)</span>
        <input
          className={s.labelInput} placeholder="label (optional)" value={node.label || ""}
          onChange={(e) => onChange({ ...node, label: e.target.value || undefined })}
        />
        <ImpactBadge imp={impact.get(node.id)} />
        <Toolbar node={node} onChange={onChange} onRemove={onRemove} />
      </div>
      <div className={s.kids}>
        <GroupKids group={node.drop} onChange={(g) => onChange({ ...node, drop: g })} impact={impact} datasets={datasets} />
      </div>
      <div className={s.head}>
        <b>…unless they match</b>
        <select
          value={node.unless.op === "all" ? "all" : "any"}
          onChange={(e) => onChange({ ...node, unless: { ...node.unless, op: e.target.value as "all" | "any" } })}
        >
          <option value="any">ANY of</option>
          <option value="all">ALL of</option>
        </select>
        <ImpactBadge imp={impact.get(node.unless.id)} />
      </div>
      <div className={s.kids}>
        <GroupKids group={node.unless} onChange={(g) => onChange({ ...node, unless: g })} impact={impact} datasets={datasets} />
      </div>
    </div>
  );
}

function GroupKids({ group, onChange, impact, datasets }: {
  group: GroupNode; onChange: (g: GroupNode) => void; impact: Map<string, Impact>; datasets: Dataset[];
}) {
  const setKids = (children: Node[]) => onChange({ ...group, children });
  return (
    <>
      {group.children.map((k, i) => (
        <FilterNode
          key={k.id} node={k} impact={impact} datasets={datasets}
          onChange={(n) => setKids(group.children.map((c, j) => (j === i ? n : c)))}
          onRemove={() => setKids(group.children.filter((_, j) => j !== i))}
        />
      ))}
      {!group.children.length && <div className={s.empty}>empty -- no effect</div>}
      <AddMenu onAdd={(n) => setKids([...group.children, n])} />
    </>
  );
}

function numInput(v: number | undefined, on: (x: number | undefined) => void, step?: number) {
  return (
    <input
      type="number" step={step ?? "any"} value={v ?? ""}
      onChange={(e) => on(e.target.value === "" ? undefined : parseFloat(e.target.value))}
    />
  );
}

function CondRow({ node, onChange, onRemove, impact, datasets }: Props & { node: CondNode }) {
  const d = COND_BY_ID[node.cond];
  const set = (patch: Partial<CondNode>) => onChange({ ...node, ...patch });
  if (!d) return <div className={s.cond}>unknown condition {node.cond}</div>;
  const opts = d.kind === "enum" ? d.options(datasets) : [];
  return (
    <div className={`${s.cond} ${node.off ? s.off : ""}`} title={d.help}>
      <button
        className={`${s.not} ${node.negate ? s.notOn : ""}`}
        onClick={() => set({ negate: !node.negate })}
        title="NOT: flip this condition"
      >
        NOT
      </button>
      <span className={s.condLabel}>{d.label}</span>
      {(d.params || []).map((p) => (
        <label key={p.key} className={s.small}>
          {p.label}{" "}
          {p.options ? (
            <select value={String(node.params[p.key])} onChange={(e) => set({ params: { ...node.params, [p.key]: e.target.value } })}>
              {p.options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <input
              type="number" min={p.min} max={p.max} step={p.step} value={node.params[p.key]}
              onChange={(e) => set({ params: { ...node.params, [p.key]: parseFloat(e.target.value) } })}
            />
          )}
        </label>
      ))}
      {d.kind === "number" && (
        <>
          <select value={node.op} onChange={(e) => set({ op: e.target.value as NumOp })}>
            {NUM_OPS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {numInput(node.value, (x) => set({ value: x }), d.step)}
          {node.op === "between" && (
            <>
              <span className={s.small}>and</span>
              {numInput(node.value2, (x) => set({ value2: x }), d.step)}
            </>
          )}
          {d.unit && <span className={s.small}>{d.unit}</span>}
        </>
      )}
      {d.kind === "enum" && (
        <span className={s.chips}>
          {opts.map((o) => {
            const on = (node.values || []).includes(o.value);
            return (
              <button
                key={o.value} className={`${s.chip} ${on ? s.chipOn : ""}`}
                onClick={() => set({ values: on ? (node.values || []).filter((v) => v !== o.value) : [...(node.values || []), o.value] })}
              >
                {o.label}
              </button>
            );
          })}
          {!(node.values || []).length && <span className={s.muted}>pick one or more (any matches)</span>}
        </span>
      )}
      <select
        className={s.small} value={node.missing || "fail"} title="What happens on a row with no reading for this condition"
        onChange={(e) => set({ missing: e.target.value as "fail" | "pass" })}
      >
        <option value="fail">no reading: fails</option>
        <option value="pass">no reading: passes</option>
      </select>
      <ImpactBadge imp={impact.get(node.id)} />
      <Toolbar node={node} onChange={onChange} onRemove={onRemove} />
    </div>
  );
}
