import { useState, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import AlertBanner from "./AlertBanner";
import {
  AreaChart, Area,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

const RANGES = [
  { label: "MTD", key: "MTD" },
  { label: "7d",  key: 7 },
  { label: "30d", key: 30 },
  { label: "60d", key: 60 },
];

const MONTH_COLORS = ["#4d9cf5", "#f5a623", "#22d18b"];  // m2, m1, m0

function fmt(n) {
  if (n == null) return "—";
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(2)}`;
}

function fmtFull(n) {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}

function delta(newer, older) {
  if (!older || older === 0) return null;
  return round1(((newer - older) / older) * 100);
}

function round1(n) { return Math.round(n * 10) / 10; }

function DeltaBadge({ d }) {
  if (d == null) return null;
  const up = d > 0;
  return (
    <span style={{
      fontSize: "0.68rem", fontWeight: 700,
      color: up ? "var(--red)" : "var(--green)",
      background: up ? "rgba(240,77,95,0.12)" : "rgba(34,209,139,0.12)",
      borderRadius: 4, padding: "1px 5px", marginLeft: "0.3rem",
      flexShrink: 0,
    }}>
      {up ? "▲" : "▼"} {Math.abs(d)}%
    </span>
  );
}

function StatCard({ label, value, sub, d, highlight }) {
  return (
    <div className="cost-stat-card">
      <div className="cost-stat-label">{label}</div>
      <div className="cost-stat-value" style={highlight ? { color: "var(--amber)" } : {}}>
        {value}
        {d != null && <DeltaBadge d={d} />}
      </div>
      {sub && <div className="cost-stat-sub">{sub}</div>}
    </div>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <p className="tooltip-label">{label}</p>
      <p className="tooltip-val">${payload[0].value.toFixed(2)}</p>
    </div>
  );
}


export default function CostPanel() {
  const [range, setRange] = useState("MTD");
  const [serviceSearch, setServiceSearch] = useState("");
  const [svcSort, setSvcSort]   = useState({ col: "m0", dir: "desc" });
  const [showComparison, setShowComparison] = useState(true);
  const fetcher = useCallback((force = false) => api.getCost(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading billing…</div>;
  if (error)   return <div className="panel-error">Cost: {error}</div>;

  // Daily chart data trimmed to range
  let daily;
  if (range === "MTD") {
    const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    daily = (data?.daily || [])
      .filter((d) => new Date(d.date) >= monthStart)
      .map((d) => ({ ...d, label: d.date.slice(5) }));
  } else {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - range);
    daily = (data?.daily || [])
      .filter((d) => new Date(d.date) >= cutoff)
      .map((d) => ({ ...d, label: d.date.slice(5) }));
  }

  // Monthly history
  const history = data?.monthly_history || [];
  const [m2, m1, m0] = [history[0], history[1], history[2]];

  // Service comparison table
  const comparison = data?.service_comparison || [];
  const filtered = comparison.filter((s) =>
    s.service.toLowerCase().includes(serviceSearch.toLowerCase())
  );

  const SORT_COLS = ["m2", "m1", "m0", "service"];
  function toggleSort(col) {
    setSvcSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: col === "service" ? "asc" : "desc" }
    );
  }
  const sorted = [...filtered].sort((a, b) => {
    const av = svcSort.col === "service" ? a.service.toLowerCase() : a[svcSort.col];
    const bv = svcSort.col === "service" ? b.service.toLowerCase() : b[svcSort.col];
    const cmp = typeof av === "number" ? av - bv : av.localeCompare(bv);
    return svcSort.dir === "asc" ? cmp : -cmp;
  });

  function SortTh({ col, label, right }) {
    const active = svcSort.col === col;
    return (
      <th
        className="th-sort"
        onClick={() => toggleSort(col)}
        style={{ textAlign: right ? "right" : undefined, color: active ? "var(--amber)" : undefined }}
      >
        {label}{" "}
        <span style={{ opacity: active ? 1 : 0.3, fontSize: "0.6rem" }}>
          {active ? (svcSort.dir === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </th>
    );
  }

  const prevLabel  = m1?.label || "Last Month";
  const prev2Label = m2?.label || "2 Mo Ago";

  return (
    <section className="panel">
      <AlertBanner serviceType="cost" onAlarmsLoaded={() => {}} />
      <div className="panel-header">
        <h2>Cost &amp; Billing</h2>
        <div className="panel-header-actions">
          <button
            className="refresh-btn"
            onClick={() => refresh(true)}
            disabled={refreshing}
            title="Refresh cost data (past months are saved automatically)"
          >
            <RefreshCw size={14} className={refreshing ? "spin" : ""} />
          </button>
        </div>
      </div>

      {/* ── Stat Cards: Last month (baseline) → MTD → Projected (vs last month) → Today ── */}
      <div className="cost-stats-row">
        <StatCard label={prevLabel} value={fmt(data?.prev_month_total)} sub="Last month total" />
        <StatCard
          label="Month-to-Date"
          value={fmt(data?.month_total)}
          d={data?.prev_month_same_period_label != null ? data?.mom_delta_same_period : undefined}
          sub={data?.prev_month_same_period_label != null ? `vs ${prevLabel} till date` : undefined}
          highlight
        />
        <StatCard
          label="Projected Month-End"
          value={data?.projected != null ? fmt(data.projected) : "—"}
          d={data?.projected_delta}
          sub={
            data?.projected != null
              ? `vs ${prevLabel} · ${data?.projected_source === "forecast" ? "CE forecast" : "extrapolated"}`
              : "No data yet"
          }
          highlight={data?.projected != null}
        />
        <StatCard
          label="Today"
          value={fmt(data?.today_cost)}
          d={data?.today_vs_yesterday_delta}
          sub={
            data?.yesterday_cost != null
              ? `Yesterday ${fmt(data.yesterday_cost)}`
              : "So far today"
          }
        />
      </div>

      {/* ── 3-Month Comparison Cards ─────────────────────────────────────────── */}
      <div className="cost-section" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="cost-section-hdr">
          <span className="chart-title" style={{ margin: 0 }}>3-Month Comparison</span>
        </div>
        <div className="cost-3mo-cards">
          {history.map((h, i) => {
            const prev = history[i - 1];
            const d = prev ? delta(h.total, prev.total) : null;
            const absDiff = prev ? h.total - prev.total : null;
            return (
              <div key={i} className="cost-3mo-card" style={{ borderTop: `3px solid ${MONTH_COLORS[i]}` }}>
                <div className="cost-3mo-card-label">{h.label}{h.is_mtd ? " (MTD)" : ""}</div>
                <div className="cost-3mo-card-value">
                  {fmt(h.total)}
                  {d != null && <DeltaBadge d={d} />}
                </div>
                <div className="cost-3mo-card-sub">
                  {absDiff != null
                    ? `${absDiff >= 0 ? "+" : ""}${fmtFull(absDiff)}${h.is_mtd ? " so far" : " vs prior"}`
                    : "\u00a0"}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Daily Chart ─────────────────────────────────────────────────────── */}
      <div className="cost-section" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="cost-section-hdr">
          <span className="chart-title" style={{ margin: 0 }}>Daily Spend</span>
          <div className="cost-range-tabs">
            {RANGES.map((r) => (
              <button
                key={r.key}
                className={`cost-range-tab ${range === r.key ? "active" : ""}`}
                onClick={() => setRange(r.key)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={daily} margin={{ top: 6, right: 12, left: -10, bottom: 0 }}>
            <defs>
              <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="var(--amber)" stopOpacity={0.3} />
                <stop offset="95%" stopColor="var(--amber)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              interval={range === "MTD" || range <= 7 ? 1 : range <= 30 ? 3 : 6}
            />
            <YAxis
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickFormatter={(v) => `$${v >= 1000 ? (v / 1000).toFixed(1) + "k" : v}`}
              width={52}
            />
            <Tooltip content={<ChartTooltip />} />
            <Area type="monotone" dataKey="cost" stroke="var(--amber)" fill="url(#costGrad)" strokeWidth={2} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* ── Service Comparison Table ─────────────────────────────────────────── */}
      <div className="cost-section" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="cost-section-hdr">
          <span className="chart-title" style={{ margin: 0 }}>
            Services{" "}
            <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
              ({filtered.length}{filtered.length !== comparison.length ? ` / ${comparison.length}` : ""})
            </span>
          </span>
          <input
            className="cost-svc-search"
            placeholder="Filter services…"
            value={serviceSearch}
            onChange={(e) => setServiceSearch(e.target.value)}
          />
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="service" label="Service" />
                <SortTh col="m2" label={prev2Label} right />
                <SortTh col="m1" label={prevLabel} right />
                <SortTh col="m0" label={`${m0?.label || "MTD"} (MTD)`} right />
                <th style={{ textAlign: "right" }}>MoM</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((s) => {
                const d = delta(s.m0, s.m1);
                return (
                  <tr key={s.service}>
                    <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={s.service}>
                      {s.service.replace("Amazon ", "").replace("AWS ", "")}
                    </td>
                    <td className="cell-mono" style={{ textAlign: "right", color: "var(--text-dim)" }}>
                      {s.m2 > 0 ? `$${s.m2.toFixed(2)}` : <span style={{ color: "var(--text-muted)" }}>—</span>}
                    </td>
                    <td className="cell-mono" style={{ textAlign: "right", color: "var(--text-dim)" }}>
                      {s.m1 > 0 ? `$${s.m1.toFixed(2)}` : <span style={{ color: "var(--text-muted)" }}>—</span>}
                    </td>
                    <td className="cell-mono" style={{ textAlign: "right", color: "var(--amber)", fontWeight: 600 }}>
                      {s.m0 > 0 ? `$${s.m0.toFixed(2)}` : <span style={{ color: "var(--text-muted)" }}>—</span>}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {d != null && s.m1 > 0
                        ? <span style={{
                            fontSize: "0.72rem", fontWeight: 700,
                            color: d > 0 ? "var(--red)" : "var(--green)",
                          }}>
                            {d > 0 ? "▲" : "▼"} {Math.abs(d)}%
                          </span>
                        : <span style={{ color: "var(--text-muted)" }}>—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
