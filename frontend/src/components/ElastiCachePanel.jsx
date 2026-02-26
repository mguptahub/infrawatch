import { useCallback, useState, useEffect, useMemo } from "react";
import { RefreshCw, X, Info, Layers, BarChart2, ChevronDown, ChevronRight, Shield, Globe, Cpu, Database, Copy, Check } from "lucide-react";
import { 
  ResponsiveContainer, LineChart, Line, 
  XAxis, YAxis, CartesianGrid, Tooltip 
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const STATUS_COLORS = {
  available: "state-green",
  creating: "state-amber",
  modifying: "state-amber",
  deleting: "state-red",
  "create-failed": "state-red",
};

function formatBytes(bytes) {
  if (bytes == null) return "N/A";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
  return `${(bytes / k ** i).toFixed(1)} ${units[i]}`;
}

function HitRate({ hits, misses }) {
  if (hits === null || misses === null || hits === undefined || misses === undefined) {
    return <span className="metric-na">N/A</span>;
  }
  const total = hits + misses;
  if (total === 0) return <span className="metric-na">—</span>;
  const rate = Math.round((hits / total) * 100);
  const color = rate >= 90 ? "var(--green)" : rate >= 70 ? "var(--amber)" : "var(--red)";
  return <span style={{ color, fontFamily: "var(--font-mono)" }}>{rate}%</span>;
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

export default function ElastiCachePanel() {
  const fetcher = useCallback((force = false) => api.getElastiCache(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null); // { id, is_rg, engine }
  const [rgSort, setRgSort] = useState({ col: "id", dir: "asc" });
  const [cSort, setCSort] = useState({ col: "id", dir: "asc" });

  if (loading) return <div className="panel-loading">Loading ElastiCache…</div>;
  if (error) return <div className="panel-error">ElastiCache: {error}</div>;

  const rgs = data?.replication_groups || [];
  const standalone = data?.standalone_clusters || [];

  const sortedRgs = [...rgs].sort((a, b) => {
    const d = rgSort.dir === "asc" ? 1 : -1;
    const valA = a[rgSort.col];
    const valB = b[rgSort.col];
    if (typeof valA === "string") return d * valA.localeCompare(valB || "");
    return d * ((valA || 0) - (valB || 0));
  });

  const sortedClusters = [...standalone].sort((a, b) => {
    const d = cSort.dir === "asc" ? 1 : -1;
    const valA = a[cSort.col];
    const valB = b[cSort.col];
    if (typeof valA === "string") return d * valA.localeCompare(valB || "");
    return d * ((valA || 0) - (valB || 0));
  });

  const toggleRgSort = (col) => setRgSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });
  const toggleCSort = (col) => setCSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>ElastiCache <span className="count-badge">{data?.total ?? 0}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>

      {rgs.length > 0 && (
        <>
          <div className="table-section-hdr">Redis / Valkey Replication Groups</div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh col="id" label="Group ID" sort={rgSort} onSort={toggleRgSort} />
                  <SortTh col="status" label="Status" sort={rgSort} onSort={toggleRgSort} />
                  <SortTh col="mode" label="Mode" sort={rgSort} onSort={toggleRgSort} />
                  <SortTh col="node_groups" label="Shards" sort={rgSort} onSort={toggleRgSort} />
                  <SortTh col="cpu_percent" label="CPU" sort={rgSort} onSort={toggleRgSort} />
                  <SortTh col="memory_percent" label="Mem%" sort={rgSort} onSort={toggleRgSort} />
                  <th>Hit Rate</th>
                  <th>Primary Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {sortedRgs.map((rg) => (
                  <tr 
                    key={rg.id} 
                    className={`row-clickable ${selected?.id === rg.id ? "row-selected" : ""}`}
                    onClick={() => setSelected({ id: rg.id, is_rg: true, engine: rg.engine, version: rg.version, name: rg.id })}
                  >
                    <td className="cell-bold">{rg.id}</td>
                    <td><span className={`state-pill ${STATUS_COLORS[rg.status] || "state-amber"}`}>{rg.status}</span></td>
                    <td>{rg.mode}</td>
                    <td>{rg.node_groups}</td>
                    <td className="cell-mono">{rg.cpu_percent != null ? `${rg.cpu_percent}%` : "—"}</td>
                    <td className="cell-mono">{rg.memory_percent != null ? `${rg.memory_percent}%` : "—"}</td>
                    <td><HitRate hits={rg.cache_hits} misses={rg.cache_misses} /></td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>
                      {rg.primary_endpoint ? `${rg.primary_endpoint.slice(0, 40)}…` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {standalone.length > 0 && (
        <>
          <div className="table-section-hdr" style={{ marginTop: "1.5rem" }}>Memcached / Standalone Clusters</div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh col="id" label="Cluster ID" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="engine" label="Engine" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="status" label="Status" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="node_type" label="Node Type" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="num_nodes" label="Nodes" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="cpu_percent" label="CPU" sort={cSort} onSort={toggleCSort} />
                  <SortTh col="memory_percent" label="Mem%" sort={cSort} onSort={toggleCSort} />
                  <th>Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {sortedClusters.map((c) => (
                  <tr 
                    key={c.id} 
                    className={`row-clickable ${selected?.id === c.id ? "row-selected" : ""}`}
                    onClick={() => setSelected({ id: c.id, is_rg: false, engine: c.engine.split(' ')[0], version: c.engine.split(' ')[1] || "", name: c.id })}
                  >
                    <td className="cell-bold">{c.id}</td>
                    <td className="cell-mono">{c.engine}</td>
                    <td><span className={`state-pill ${STATUS_COLORS[c.status] || "state-gray"}`}>{c.status}</span></td>
                    <td className="cell-mono">{c.node_type}</td>
                    <td>{c.num_nodes}</td>
                    <td className="cell-mono">{c.cpu_percent != null ? `${c.cpu_percent}%` : "—"}</td>
                    <td className="cell-mono">{c.memory_percent != null ? `${c.memory_percent}%` : "—"}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{c.endpoint || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {rgs.length === 0 && standalone.length === 0 && (
        <div className="panel-empty">No ElastiCache clusters found</div>
      )}

      {selected && (
        <>
          <div className="drawer-backdrop" onClick={() => setSelected(null)} />
          <DetailDrawer cluster={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

function DetailDrawer({ cluster, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [openSections, setOpenSections] = useState({ overview: true, security: true, nodes: false });
  const [showMetrics, setShowMetrics] = useState(false);
  const [sgExpanded, setSgExpanded] = useState({});

  useEffect(() => {
    setLoading(true);
    api.getECDetail(cluster.id, cluster.is_rg)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [cluster.id, cluster.is_rg]);

  const toggle = (s) => setOpenSections(prev => ({ ...prev, [s]: !prev[s] }));

  function Section({ id, title, icon: Icon, children }) {
    const open = openSections[id];
    return (
      <div className="drawer-section">
        <div className="drawer-section-hdr" onClick={() => toggle(id)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Icon size={13} style={{ margin: "0 4px" }} />
          <span>{title}</span>
        </div>
        {open && <div className="drawer-section-body">{children}</div>}
      </div>
    );
  }

  return (
    <div className="detail-drawer">
      <div className="drawer-header">
        <div>
          <div className="drawer-title">{cluster.id}</div>
          <div className="drawer-subtitle">
            <span className={`state-pill ${detail?.Status ? (STATUS_COLORS[detail.Status.toLowerCase()] || "state-amber") : "state-gray"}`}>
              {detail?.Status || "Loading…"}
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
                <MetaItem label="Engine" value={detail.Engine || cluster.engine || "—"} />
                <MetaItem label="Version" value={detail.EngineVersion || cluster.version || "—"} />
                <MetaItem label="Node Type" value={detail.CacheNodeType || (detail.CacheClusterId && "Standalone") || "—"} />
                <MetaItem label="Multi-AZ" value={detail.MultiAZ || "—"} />
                <MetaItem label="Automatic Failover" value={detail.AutomaticFailover || "—"} />
                <MetaItem label="Created" value={detail.ReplicationGroupCreateTime ? new Date(detail.ReplicationGroupCreateTime).toLocaleString() : (detail.Created ? new Date(detail.Created).toLocaleString() : "—")} />
              </div>
              {detail.ConnectionEndpoint && (
                <div style={{ marginTop: "1rem", padding: "0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.4rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      Connection Endpoint
                      {detail.TransitEncryptionEnabled && (
                        <span style={{ fontSize: "0.6rem", background: "var(--brand-bg)", color: "var(--brand)", padding: "2px 6px", borderRadius: "4px", fontWeight: "600", border: "1px solid var(--brand-border)" }}>TLS</span>
                      )}
                    </div>
                    <CopyableText text={detail.ConnectionEndpoint} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.8rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.ConnectionEndpoint}
                  </div>
                </div>
              )}
            </Section>

            <Section id="security" title="Security & Networking" icon={Shield}>
              <div className="drawer-meta-grid">
                <MetaItem label="TLS (In-Transit)" value={detail.TransitEncryptionEnabled ? "Enabled" : "Disabled"} />
                <MetaItem label="At-Rest Encryption" value={detail.AtRestEncryptionEnabled ? "Yes" : "No"} />
                <MetaItem label="Auth Token" value={detail.AuthTokenEnabled ? "Enabled" : "Disabled"} />
                {detail.SecretArn && <MetaItem label="Secret ARN" value={detail.SecretArn} mono />}
                {detail.KmsKeyId && <MetaItem label="KMS Key" value={detail.KmsKeyId.split('/').pop()} mono />}
              </div>
              {detail.SecurityGroupsEnriched && detail.SecurityGroupsEnriched.length > 0 && (
                <div style={{ marginTop: "1rem" }}>
                  <span className="stat-lbl" style={{ marginBottom: "0.5rem", display: "block" }}>
                    Security Groups ({detail.SecurityGroupsEnriched.length})
                  </span>
                  {detail.SecurityGroupsEnriched.map(sg => (
                    <div key={sg.id} className="ec2-sg-block">
                      <button
                        className="ec2-sg-hdr"
                        onClick={() => setSgExpanded(p => ({ ...p, [sg.id]: !p[sg.id] }))}
                      >
                        {sgExpanded[sg.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
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
            </Section>

            <Section id="nodes" title="Nodes / Shards" icon={Layers}>
              {cluster.is_rg ? (
                (detail.NodeGroups || []).map((ng, i) => (
                  <div key={i} className="mq-instance-card" style={{ marginBottom: "0.8rem" }}>
                    <div className="mq-instance-header">
                      <span className="cell-bold">Shard {ng.NodeGroupId}</span>
                      <span className="state-pill state-green">{ng.Status}</span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", marginTop: "0.4rem" }}>
                      {ng.NodeGroupMembers?.map(m => (
                        <div key={m.CacheClusterId} className="endpoint-row">
                          <span className="cell-mono" style={{ fontSize: "0.72rem" }}>{m.CacheClusterId} ({m.CurrentRole})</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                <div className="mq-instance-card">
                   <div className="mq-instance-header">
                      <span className="cell-bold">{detail.CacheClusterId}</span>
                      <span className="state-pill state-green">{detail.CacheClusterStatus}</span>
                    </div>
                    <div className="drawer-meta-grid" style={{ marginTop: "0.5rem" }}>
                       <MetaItem label="AZ" value={detail.PreferredAvailabilityZone} />
                       <MetaItem label="Preferred Window" value={detail.PreferredMaintenanceWindow} />
                    </div>
                </div>
              )}
            </Section>
          </>
        )}
      </div>

      {showMetrics && (
        <MetricsModal cluster={cluster} onClose={() => setShowMetrics(false)} />
      )}
    </div>
  );
}

function MetricsModal({ cluster, onClose }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setLoading(true);
    setError(null);
    api.getECMetrics(cluster.id, cluster.engine, hours, cluster.is_rg)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [cluster.id, cluster.engine, hours, cluster.is_rg]);

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{cluster.id}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {cluster.engine} metrics · {hours}h range
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
            <MetricChart title="CPU Utilization" hours={hours} data={data.CPUUtilization} yFmt={v => `${v}%`} color="var(--blue)" label="CPU" />
            <MetricChart title="Memory Usage" hours={hours} data={data.DatabaseMemoryUsagePercentage} yFmt={v => `${v}%`} color="var(--amber)" label="Usage %" />
            <MetricChart title="Connections" hours={hours} data={data.CurrConnections} color="var(--green)" label="Count" />
            <MetricChart title="Cache Hits" hours={hours} data={data.CacheHits} color="var(--teal)" label="Hits" />
          </div>
        )}
      </div>
    </div>
  );
}

function MetricChart({ title, hours, data, color, label, yFmt }) {
  const isEmpty = !data || data.length === 0;

  const xFmt = (utcStr) => {
    const d = new Date(utcStr);
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
              dataKey="time"
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
              dataKey="value"
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

function MetaItem({ label, value, mono }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span className={`drawer-meta-val ${mono ? "cell-mono" : ""}`} style={mono ? { fontSize: "0.72rem", wordBreak: "break-all" } : {}}>{value}</span>
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

function CopyableText({ text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={onCopy} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center" }}>
      {copied ? <Check size={12} style={{ color: "var(--green)" }} /> : <Copy size={12} />}
    </button>
  );
}
