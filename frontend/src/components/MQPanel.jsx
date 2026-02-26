import { useCallback, useState, useEffect, useMemo } from "react";
import { RefreshCw, X, Info, Layers, BarChart2, ChevronDown, ChevronRight } from "lucide-react";
import { 
  ResponsiveContainer, LineChart, Line, 
  XAxis, YAxis, CartesianGrid, Tooltip 
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const BROKER_STATE_COLORS = {
  RUNNING: "state-green",
  REBOOT_IN_PROGRESS: "state-amber",
  CREATION_IN_PROGRESS: "state-amber",
  DELETION_IN_PROGRESS: "state-red",
  CRITICAL_ACTION_REQUIRED: "state-red",
};

const ENGINE_COLORS = {
  RabbitMQ: "state-blue",
  ActiveMQ: "state-amber",
};

function formatBytes(bytes) {
  if (bytes == null) return "N/A";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
  return `${(bytes / k ** i).toFixed(1)} ${units[i]}`;
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
export default function MQPanel() {
  const fetcher = useCallback((force = false) => api.getMQ(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: "name", dir: "asc" });

  if (loading) return <div className="panel-loading">Loading Amazon MQ…</div>;
  if (error) return <div className="panel-error">Amazon MQ: {error}</div>;

  const brokers = data?.brokers || [];
  
  const sorted = [...brokers].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    switch (sort.col) {
      case "name":    return d * (a.name || "").localeCompare(b.name || "");
      case "state":   return d * (a.state || "").localeCompare(b.state || "");
      case "engine":  return d * (a.engine_type || "").localeCompare(b.engine_type || "");
      case "type":    return d * (a.instance_type || "").localeCompare(b.instance_type || "");
      case "mode":    return d * (a.deployment_mode || "").localeCompare(b.deployment_mode || "");
      default:        return 0;
    }
  });

  function toggleSort(col) {
    setSort((s) =>
      s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" }
    );
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Amazon MQ Brokers <span className="count-badge">{data?.count ?? 0}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>
      
      {sorted.length === 0 ? (
        <div className="panel-empty">No MQ brokers found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="name"   label="Name"    sort={sort} onSort={toggleSort} />
                <th>ID</th>
                <SortTh col="state"  label="State"   sort={sort} onSort={toggleSort} />
                <SortTh col="engine" label="Engine"  sort={sort} onSort={toggleSort} />
                <SortTh col="type"   label="Type"    sort={sort} onSort={toggleSort} />
                <SortTh col="mode"   label="Mode"    sort={sort} onSort={toggleSort} />
                <th>Public</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((b) => (
                <tr 
                  key={b.id} 
                  className={`row-clickable ${selected?.id === b.id ? "row-selected" : ""}`}
                  onClick={() => setSelected(b)}
                >
                  <td className="cell-bold">{b.name}</td>
                  <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{b.id}</td>
                  <td>
                    <span className={`state-pill ${BROKER_STATE_COLORS[b.state] || "state-gray"}`}>
                      {b.state}
                    </span>
                  </td>
                  <td>
                    <span className={`state-pill ${ENGINE_COLORS[b.engine_type] || "state-gray"}`}>
                      {b.engine_type} {b.engine_version}
                    </span>
                  </td>
                  <td className="cell-mono">{b.instance_type}</td>
                  <td>{b.deployment_mode}</td>
                  <td>
                    <span className={`state-pill ${b.publicly_accessible ? "state-amber" : "state-green"}`}>
                      {b.publicly_accessible ? "Yes" : "No"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <>
          <div className="drawer-backdrop" onClick={() => setSelected(null)} />
          <DetailDrawer broker={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

// ── Detail Drawer ─────────────────────────────────────────────────────────────
function DetailDrawer({ broker, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [openSections, setOpenSections] = useState({ overview: true, instances: true, tags: false });
  const [showMetrics, setShowMetrics] = useState(false);

  const toggle = (s) => setOpenSections((prev) => ({ ...prev, [s]: !prev[s] }));

  useEffect(() => {
    setLoading(true);
    api.getMQDetail(broker.id)
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [broker.id]);

  function Section({ id, title, icon: Icon, children }) {
    const open = openSections[id];
    return (
      <div className="drawer-section">
        <div className="drawer-section-hdr" onClick={() => toggle(id)} style={{ cursor: "pointer" }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Icon size={13} style={{ margin: "0 4px" }} />
          <span>{title}</span>
        </div>
        {open && <div className="drawer-section-body" style={{ marginTop: "0.5rem" }}>{children}</div>}
      </div>
    );
  }

  return (
    <div className="detail-drawer">
      <div className="drawer-header">
        <div>
          <div className="drawer-title">{broker.name}</div>
          <div className="drawer-subtitle">
            <span className={`state-pill ${ENGINE_COLORS[broker.engine_type] || "state-gray"}`}>
              {broker.engine_type} {broker.engine_version}
            </span>
            &nbsp;
            <span className={`state-pill ${BROKER_STATE_COLORS[broker.state] || "state-gray"}`}>{broker.state}</span>
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
          <button className="drawer-close" onClick={onClose}><X size={16} /></button>
        </div>
      </div>

      <div className="drawer-body">
        {loading && <div className="panel-loading">Loading details…</div>}
        {error && <div className="panel-error">{error}</div>}
        {detail && (
          <>
            <Section id="overview" title="Overview" icon={Info}>
              <div className="drawer-meta-grid">
                <MetaItem label="Broker ID" value={broker.id} mono />
                <MetaItem label="Mode" value={broker.deployment_mode} />
                <MetaItem label="Storage Type" value={detail.StorageType || "—"} />
                {broker.engine_type === "ActiveMQ" ? (
                  <MetaItem 
                    label="Storage Usage" 
                    value={broker.storage_usage != null ? `${broker.storage_usage}%` : "Managed (Elastic)"} 
                  />
                ) : (
                  <MetaItem 
                    label="Disk Free" 
                    value={broker.storage_free != null ? formatBytes(broker.storage_free) : "Managed"} 
                  />
                )}
                <MetaItem label="Public Access" value={broker.publicly_accessible ? "Enabled" : "Disabled"} />
                <MetaItem label="Auto Upgrade" value={broker.auto_minor_upgrade ? "On" : "Off"} />
                {detail.MaintenanceWindowStartTime && (
                  <MetaItem label="Maint. Window" value={`${detail.MaintenanceWindowStartTime.DayOfWeek} ${detail.MaintenanceWindowStartTime.TimeOfDay}`} />
                )}
                {detail.Created && (
                  <MetaItem label="Created" value={new Date(detail.Created).toLocaleString()} />
                )}
              </div>
            </Section>

            <Section id="instances" title={`Instances (${(broker.instances || []).length})`} icon={Layers}>
              {broker.instances?.map((inst, idx) => (
                <div key={idx} className="mq-instance-card">
                  <div className="mq-instance-header">
                    <span className="cell-bold">Node {idx + 1}</span>
                    <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                      {inst.IpAddress || inst.ConsoleIpAddress || "N/A"}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                    <span className="stat-lbl">Endpoints</span>
                    {inst.Endpoints?.map((ep, eidx) => (
                      <div key={eidx} className="endpoint-row">
                        <span className="cell-mono" style={{ fontSize: "0.72rem", wordBreak: "break-all" }}>{ep}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </Section>

            {detail.Tags && Object.keys(detail.Tags).length > 0 && (
              <Section id="tags" title={`Tags (${Object.keys(detail.Tags).length})`} icon={Info}>
                <table className="data-table">
                  <thead><tr><th>Key</th><th>Value</th></tr></thead>
                  <tbody>
                    {Object.entries(detail.Tags).map(([k, v]) => (
                      <tr key={k}>
                        <td className="cell-mono">{k}</td>
                        <td className="cell-mono">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            )}
          </>
        )}
      </div>

      {showMetrics && (
        <MetricsModal broker={broker} onClose={() => setShowMetrics(false)} />
      )}
    </div>
  );
}

// ── Metrics Modal ─────────────────────────────────────────────────────────────
function MetricsModal({ broker, onClose }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setLoading(true);
    setError(null);
    api.getMQMetrics(broker.id, hours)
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [broker.id, hours]);

  const pts = (series) => {
    if (!series) return [];
    return Object.entries(series)
      .map(([ts, v]) => ({ ts: new Date(ts).getTime(), v }))
      .sort((a, b) => a.ts - b.ts);
  };

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{broker.name}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {broker.engine_type} broker · {broker.id}
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

      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading metrics…</div>}
        {error && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <MetricChart
              title="CPU Utilization"
              hours={hours}
              data={pts(data.cpu)}
              yFmt={(v) => `${v}%`}
              color="var(--blue)"
              label="CPU"
            />
            <MetricChart
              title={broker.engine_type === "RabbitMQ" ? "Memory Usage" : "Heap Usage"}
              hours={hours}
              data={pts(data.memory)}
              yFmt={broker.engine_type === "RabbitMQ" ? formatBytes : (v) => `${v}%`}
              color="var(--amber)"
              label={broker.engine_type === "RabbitMQ" ? "Bytes" : "Percent"}
            />
            <MetricChart
              title="Total Connections"
              hours={hours}
              data={pts(data.connections)}
              color="var(--green)"
              label="Count"
            />
            <MetricChart
              title="Total Messages / Queue Depth"
              hours={hours}
              data={pts(data.messages)}
              color="var(--red)"
              label="Messages"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function MetricChart({ title, hours, data, color, label, yFmt }) {
  const isEmpty = !data || data.length === 0;

  const xFmt = (ts) => {
    const d = new Date(ts);
    if (hours <= 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  };

  const tickInterval = isEmpty ? 0 : Math.max(0, Math.floor(data.length / 6) - 1);

  return (
    <div className="metrics-chart-card">
      <div className="metrics-chart-title">
        {title}
        <span className="metrics-legend-item" style={{ float: "right" }}>
          <span className="metrics-legend-dot" style={{ background: color }} />
          {label}
        </span>
      </div>

      {isEmpty ? (
        <div className="metrics-chart-empty">No data available</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
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
              width={56}
            />
            <Tooltip content={<ChartTooltip xFmt={xFmt} tipFmt={yFmt} seriesName={label} />} />
            <Line
              type="monotone"
              dataKey="v"
              stroke={color}
              dot={false}
              strokeWidth={1.5}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ChartTooltip({ active, payload, label, xFmt, tipFmt, seriesName }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="tooltip-label">{xFmt(label)}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.stroke, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
          {seriesName}: {tipFmt ? tipFmt(p.value) : p.value}
        </div>
      ))}
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────
function MetaItem({ label, value, mono }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span className={`drawer-meta-val ${mono ? "cell-mono" : ""}`} style={mono ? { fontSize: "0.72rem" } : {}}>{value}</span>
    </div>
  );
}
