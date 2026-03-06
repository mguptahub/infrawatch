import { useState, useEffect, useCallback } from "react";
import { RefreshCw, X, ChevronDown, ChevronRight, BarChart2, Check } from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import { REFRESH_STREAM_TIMEOUT_MS } from "../constants";

const STATE_COLORS = {
  running:         "state-green",
  stopped:         "state-red",
  pending:         "state-amber",
  stopping:        "state-amber",
  terminated:      "state-gray",
  "shutting-down": "state-gray",
};

function formatBytes(bytes) {
  if (bytes == null) return "N/A";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
  return `${(bytes / k ** i).toFixed(1)} ${units[i]}`;
}

function formatUptime(hours) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${Math.floor(hours)}h`;
  const d = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function CpuBar({ value }) {
  if (value == null) return <span className="metric-na">N/A</span>;
  const color = value > 80 ? "var(--red)" : value > 50 ? "var(--amber)" : "var(--green)";
  return (
    <div className="cpu-bar-wrap">
      <div className="cpu-bar-track">
        <div className="cpu-bar-fill" style={{ width: `${value}%`, background: color }} />
      </div>
      <span className="cpu-label" style={{ color }}>{value}%</span>
    </div>
  );
}

function SortTh({ col, label, sort, onSort }) {
  const active = sort.col === col;
  return (
    <th
      className="th-sort"
      onClick={() => onSort(col)}
      style={{ color: active ? "var(--amber)" : undefined }}
    >
      {label}{" "}
      <span style={{ opacity: active ? 1 : 0.3, fontSize: "0.65rem" }}>
        {active ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}
      </span>
    </th>
  );
}

// ── Main Panel ────────────────────────────────────────────────────────────────
export default function EC2Panel() {
  const [stateFilter, setStateFilter] = useState("all");
  const [sort, setSort] = useState({ col: "name", dir: "asc" });
  const [selected, setSelected] = useState(null);

  const fetcher = useCallback((force = false) => api.getEC2(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [syncing, setSyncing] = useState(false);
  const [showRefreshed, setShowRefreshed] = useState(false);

  async function handleRefresh() {
    setSyncing(true);
    setShowRefreshed(false);
    const streamUrl = api.ec2RefreshStreamUrl();
    const es = new EventSource(streamUrl);
    const timeoutId = setTimeout(() => {
      es.close();
      refresh();
      setSyncing(false);
    }, REFRESH_STREAM_TIMEOUT_MS);
    es.addEventListener("refresh_done", () => {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
      setShowRefreshed(true);
      setTimeout(() => setShowRefreshed(false), 1500);
    });
    es.onerror = () => {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    };
    try {
      await api.refreshEC2();
    } catch (e) {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    }
  }

  const instances = data?.instances || [];
  const filtered = stateFilter === "all"
    ? instances
    : instances.filter((i) => i.state === stateFilter);

  const sorted = [...filtered].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    switch (sort.col) {
      case "name":       return d * (a.name || "").localeCompare(b.name || "");
      case "state":      return d * (a.state || "").localeCompare(b.state || "");
      case "type":       return d * (a.type || "").localeCompare(b.type || "");
      case "az":         return d * (a.az || "").localeCompare(b.az || "");
      case "private_ip": return d * (a.private_ip || "").localeCompare(b.private_ip || "");
      case "uptime":     return d * ((a.uptime_hours || 0) - (b.uptime_hours || 0));
      case "cpu":        return d * ((a.cpu_percent || 0) - (b.cpu_percent || 0));
      default:           return 0;
    }
  });

  function toggleSort(col) {
    setSort((s) =>
      s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" }
    );
  }

  if (loading) return <div className="panel-loading">Loading EC2…</div>;
  if (error)   return <div className="panel-error">EC2: {error}</div>;

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>EC2 Instances <span className="count-badge">{filtered.length}</span></h2>
        <div className="panel-header-actions">
          <select
            className="ses-filter-select"
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value)}
          >
            <option value="all">All states</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
            <option value="pending">Pending</option>
            <option value="terminated">Terminated</option>
          </select>
          <button className="refresh-btn" onClick={handleRefresh} disabled={refreshing || syncing} title="Sync from AWS and refresh">
            {showRefreshed ? (
              <span className="refresh-done"><Check size={13} /> Refreshed</span>
            ) : (
              <RefreshCw size={13} className={refreshing || syncing ? "spinning" : ""} />
            )}
          </button>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="panel-empty">No instances found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="name"       label="Name"       sort={sort} onSort={toggleSort} />
                <th>ID</th>
                <SortTh col="state"      label="State"      sort={sort} onSort={toggleSort} />
                <SortTh col="type"       label="Type"       sort={sort} onSort={toggleSort} />
                <SortTh col="az"         label="AZ"         sort={sort} onSort={toggleSort} />
                <SortTh col="private_ip" label="Private IP" sort={sort} onSort={toggleSort} />
                <SortTh col="uptime"     label="Uptime"     sort={sort} onSort={toggleSort} />
                <SortTh col="cpu"        label="CPU"        sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((i) => (
                <tr key={i.id} className="row-clickable" onClick={() => setSelected(i)}>
                  <td className="cell-bold">{i.name}</td>
                  <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{i.id}</td>
                  <td>
                    <span className={`state-pill ${STATE_COLORS[i.state] || "state-gray"}`}>
                      {i.state}
                    </span>
                  </td>
                  <td className="cell-mono">{i.type}</td>
                  <td>{i.az}</td>
                  <td className="cell-mono">{i.private_ip || "—"}</td>
                  <td className="cell-mono">{formatUptime(i.uptime_hours)}</td>
                  <td><CpuBar value={i.cpu_percent} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <DetailDrawer instance={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}

// ── Detail Drawer ─────────────────────────────────────────────────────────────
function DetailDrawer({ instance, onClose }) {
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState(null);
  const [sgExpanded, setSgExpanded] = useState({});
  const [showMetrics, setShowMetrics] = useState(false);

  useEffect(() => {
    setDetail(null);
    setDetailLoading(true);
    setDetailError(null);
    api.getEC2Detail(instance.id)
      .then((d) => { setDetail(d); setDetailLoading(false); })
      .catch((e) => { setDetailError(e.message); setDetailLoading(false); });
  }, [instance.id]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "Escape") return;
      if (showMetrics) {
        setShowMetrics(false);
        return;
      }
      onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, showMetrics]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="detail-drawer">
        {/* Header */}
        <div className="drawer-header">
          <div>
            <div className="drawer-title">{instance.name}</div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.35rem" }}>
              <span className={`state-pill ${STATE_COLORS[instance.state] || "state-gray"}`}>
                {instance.state}
              </span>
              <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                {instance.id}
              </span>
            </div>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <button
              className="refresh-btn"
              onClick={() => setShowMetrics(true)}
              title="View Metrics"
              style={{ padding: "0.3rem 0.6rem", display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.75rem" }}
            >
              <BarChart2 size={13} /> Metrics
            </button>
            <button className="drawer-close" onClick={onClose}><X size={14} /></button>
          </div>
        </div>

        {/* Body */}
        <div className="drawer-body">
          {detailLoading && <div className="panel-loading" style={{ padding: "2rem 0" }}>Loading details…</div>}
          {detailError   && <div className="panel-error">{detailError}</div>}

          {detail && (
            <>
              {/* ── Overview ── */}
              <div className="drawer-section">
                <div className="drawer-section-hdr">Overview</div>
                <div className="drawer-meta-grid">
                  <MetaItem label="Type"         value={detail.type} />
                  <MetaItem label="Architecture" value={detail.architecture} />
                  <MetaItem label="AZ"           value={detail.az} />
                  <MetaItem label="VPC"          value={detail.vpc_id} />
                  <MetaItem label="Subnet"       value={detail.subnet_id} />
                  <MetaItem label="Key Pair"     value={detail.key_name} />
                  <MetaItem label="Private IP"   value={detail.private_ip || "—"} />
                  <MetaItem label="Public IP"    value={detail.public_ip  || "—"} />
                  <MetaItem label="IAM Profile"  value={detail.iam_profile || "—"} />
                  <MetaItem label="AMI"          value={detail.ami_id} />
                  <MetaItem label="Uptime"       value={formatUptime(detail.uptime_hours)} />
                  <MetaItem label="Launched"     value={detail.launch_time ? new Date(detail.launch_time).toLocaleString() : "—"} />
                </div>
              </div>

              {/* ── Metrics ── */}
              <div className="drawer-section">
                <div className="drawer-section-hdr">Metrics (last 5 min avg)</div>
                <div className="ec2-metrics-grid">
                  <MetricBox
                    label="CPU"
                    value={detail.metrics.cpu_percent != null ? `${detail.metrics.cpu_percent}%` : "N/A"}
                    color={
                      detail.metrics.cpu_percent != null
                        ? detail.metrics.cpu_percent > 80 ? "var(--red)"
                        : detail.metrics.cpu_percent > 50 ? "var(--amber)"
                        : "var(--green)"
                        : undefined
                    }
                  />
                  <MetricBox label="Net In"     value={formatBytes(detail.metrics.network_in_bytes)} />
                  <MetricBox label="Net Out"    value={formatBytes(detail.metrics.network_out_bytes)} />
                  <MetricBox label="Disk Read"  value={formatBytes(detail.metrics.disk_read_bytes)} />
                  <MetricBox label="Disk Write" value={formatBytes(detail.metrics.disk_write_bytes)} />
                </div>
              </div>

              {/* ── Security Groups ── */}
              {detail.security_groups.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">
                    Security Groups ({detail.security_groups.length})
                  </div>
                  {detail.security_groups.map((sg) => (
                    <div key={sg.id} className="ec2-sg-block">
                      <button
                        className="ec2-sg-hdr"
                        onClick={() => setSgExpanded((p) => ({ ...p, [sg.id]: !p[sg.id] }))}
                      >
                        {sgExpanded[sg.id]
                          ? <ChevronDown size={13} />
                          : <ChevronRight size={13} />}
                        <span style={{ fontWeight: 600 }}>{sg.name}</span>
                        <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginLeft: "0.4rem" }}>
                          {sg.id}
                        </span>
                      </button>
                      {sgExpanded[sg.id] && (
                        <div className="ec2-sg-rules">
                          <RulesTable label="Inbound"  rules={sg.inbound} />
                          <RulesTable label="Outbound" rules={sg.outbound} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* ── Storage ── */}
              {detail.volumes.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Storage ({detail.volumes.length} volume{detail.volumes.length > 1 ? "s" : ""})</div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Device</th>
                          <th>Volume ID</th>
                          <th>Size</th>
                          <th>Type</th>
                          <th>State</th>
                          <th>Encrypted</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.volumes.map((v) => (
                          <tr key={v.id}>
                            <td className="cell-mono">{v.device}</td>
                            <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{v.id}</td>
                            <td className="cell-mono">{v.size_gb} GB</td>
                            <td className="cell-mono">{v.type}{v.iops ? ` · ${v.iops} IOPS` : ""}</td>
                            <td>
                              <span className={`state-pill ${v.state === "in-use" ? "state-green" : "state-gray"}`}>
                                {v.state}
                              </span>
                            </td>
                            <td>
                              <span className={`state-pill ${v.encrypted ? "state-green" : "state-gray"}`}>
                                {v.encrypted ? "Yes" : "No"}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ── Tags ── */}
              {detail.tags.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Tags ({detail.tags.length})</div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Key</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.tags.map((t) => (
                          <tr key={t.key}>
                            <td className="cell-mono" style={{ color: "var(--text-dim)" }}>{t.key}</td>
                            <td style={{ whiteSpace: "normal", wordBreak: "break-all" }}>{t.value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {showMetrics && (
        <MetricsModal instance={instance} onClose={() => setShowMetrics(false)} />
      )}
    </>
  );
}

// ── Metrics Modal ─────────────────────────────────────────────────────────────
function MetricsModal({ instance, onClose }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setLoading(true);
    setError(null);
    api.getEC2Metrics(instance.id, hours)
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [instance.id, hours]);

  // Merge two {ts,v} series into [{ts, k1, k2}] by timestamp
  function merge(s1, k1, s2, k2) {
    const map = {};
    (s1 || []).forEach((p) => { map[p.ts] = { ts: p.ts, [k1]: p.v }; });
    (s2 || []).forEach((p) => {
      if (map[p.ts]) map[p.ts][k2] = p.v;
      else map[p.ts] = { ts: p.ts, [k2]: p.v };
    });
    return Object.values(map).sort((a, b) => a.ts < b.ts ? -1 : 1);
  }

  const m = data?.metrics;
  const networkData = m ? merge(m.network_in, "in", m.network_out, "out") : [];
  const diskData    = m ? merge(m.disk_read,  "read", m.disk_write, "write") : [];

  return (
    <div className="metrics-modal">
      {/* Header */}
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{instance.name}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {instance.id}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="metrics-range-tabs">
            {[1, 6, 24, 72].map((h) => (
              <button
                key={h}
                className={`metrics-range-tab ${hours === h ? "active" : ""}`}
                onClick={() => setHours(h)}
              >
                {h}h
              </button>
            ))}
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      {/* Body */}
      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading metrics…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <MetricChart
              title="CPU Utilization"
              hours={hours}
              series={[{ data: m.cpu, key: "v", color: "var(--green)", label: "CPU %" }]}
              yDomain={[0, 100]}
              yFmt={(v) => `${v}%`}
              tipFmt={(v) => `${v}%`}
            />
            <MetricChart
              title="Memory"
              hours={hours}
              series={[{ data: m.memory, key: "v", color: "var(--blue)", label: "Mem %" }]}
              yDomain={[0, 100]}
              yFmt={(v) => `${v}%`}
              tipFmt={(v) => `${v}%`}
              emptyNote="No data — requires CloudWatch Agent on the instance"
            />
            <MetricChart
              title="Network Traffic (bytes / period)"
              hours={hours}
              series={[
                { data: networkData, key: "in",  color: "var(--amber)", label: "In" },
                { data: networkData, key: "out", color: "var(--blue)",  label: "Out" },
              ]}
              merged
              yFmt={formatBytes}
              tipFmt={formatBytes}
            />
            <MetricChart
              title="Disk I/O (bytes / period)"
              hours={hours}
              series={[
                { data: diskData, key: "read",  color: "var(--amber)", label: "Read" },
                { data: diskData, key: "write", color: "var(--red)",   label: "Write" },
              ]}
              merged
              yFmt={formatBytes}
              tipFmt={formatBytes}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function MetricChart({ title, hours, series, merged, yDomain, yFmt, tipFmt, emptyNote }) {
  const primaryData = merged ? series[0].data : series[0].data;
  const isEmpty = !primaryData || primaryData.length === 0;

  const xFmt = (ts) => {
    const d = new Date(ts);
    if (hours <= 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  };

  const tickInterval = isEmpty ? 0 : Math.max(0, Math.floor(primaryData.length / 6) - 1);

  return (
    <div className="metrics-chart-card">
      <div className="metrics-chart-title">
        {title}
        <span style={{ display: "flex", gap: "0.75rem", float: "right" }}>
          {series.map((s) => (
            <span key={s.key} className="metrics-legend-item">
              <span className="metrics-legend-dot" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </span>
      </div>

      {isEmpty ? (
        <div className="metrics-chart-empty">{emptyNote || "No data available"}</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={primaryData} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2840" vertical={false} />
            <XAxis
              dataKey="ts"
              tick={{ fill: "#5a6a85", fontSize: 10 }}
              tickFormatter={xFmt}
              interval={tickInterval}
            />
            <YAxis
              tick={{ fill: "#5a6a85", fontSize: 10 }}
              tickFormatter={yFmt || ((v) => v)}
              domain={yDomain || ["auto", "auto"]}
              width={56}
            />
            <Tooltip content={<ChartTooltip xFmt={xFmt} tipFmt={tipFmt} series={series} />} />
            {series.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                dot={false}
                strokeWidth={1.5}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ChartTooltip({ active, payload, label, xFmt, tipFmt, series }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="tooltip-label">{xFmt(label)}</div>
      {payload.map((p, i) => {
        const s = series.find((s) => s.key === p.dataKey);
        return (
          <div key={i} style={{ color: p.stroke, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
            {s?.label}: {tipFmt ? tipFmt(p.value) : p.value}
          </div>
        );
      })}
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────
function MetaItem({ label, value }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span className="drawer-meta-val">{value}</span>
    </div>
  );
}

function MetricBox({ label, value, color }) {
  return (
    <div className="ec2-metric-box">
      <div className="ec2-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ec2-metric-lbl">{label}</div>
    </div>
  );
}

function RulesTable({ label, rules }) {
  if (!rules || rules.length === 0) return null;
  return (
    <div style={{ marginBottom: "0.75rem" }}>
      <div style={{
        fontSize: "0.68rem", fontWeight: 700, textTransform: "uppercase",
        letterSpacing: "0.08em", color: "var(--text-muted)", marginBottom: "0.35rem",
      }}>
        {label}
      </div>
      <table className="data-table" style={{ fontSize: "0.78rem" }}>
        <thead>
          <tr>
            <th>Protocol</th>
            <th>Port</th>
            <th>Source / Dest</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r, idx) => (
            <tr key={idx}>
              <td className="cell-mono">{r.protocol}</td>
              <td className="cell-mono">{r.port}</td>
              <td className="cell-mono" style={{ fontSize: "0.7rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                {r.source}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
