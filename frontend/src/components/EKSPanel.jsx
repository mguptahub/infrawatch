import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, Shield, Settings, Copy, Check,
  ChevronDown, ChevronRight, Server, Network, Lock, Users, BarChart2,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import { REFRESH_STREAM_TIMEOUT_MS } from "../constants";

const STATUS_COLORS = {
  ACTIVE:   "state-green",
  CREATING: "state-amber",
  UPDATING: "state-amber",
  DELETING: "state-red",
  FAILED:   "state-red",
};

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

function MetricBox({ label, value, color }) {
  return (
    <div className="ec2-metric-box">
      <div className="ec2-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ec2-metric-lbl">{label}</div>
    </div>
  );
}

function NodeMetricChart({ title, hours, series, merged, yDomain, yFmt, tipFmt, emptyNote }) {
  const primaryData = series[0].data;
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
          {series.map(s => (
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
            <XAxis dataKey="ts" tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={xFmt} interval={tickInterval} />
            <YAxis tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={yFmt || (v => v)} domain={yDomain || ["auto", "auto"]} width={56} />
            <Tooltip content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="chart-tooltip">
                  <div className="tooltip-label">{xFmt(label)}</div>
                  {payload.map((p, i) => {
                    const s = series.find(s => s.key === p.dataKey);
                    return <div key={i} style={{ color: p.stroke, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{s?.label}: {tipFmt ? tipFmt(p.value) : p.value}</div>;
                  })}
                </div>
              );
            }} />
            {series.map(s => (
              <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} dot={false} strokeWidth={1.5} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function EKSPanel() {
  const fetcher = useCallback((force = false) => api.getEKS(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: "name", dir: "asc" });
  const [syncing, setSyncing] = useState(false);
  const [showRefreshed, setShowRefreshed] = useState(false);

  async function handleRefresh() {
    setSyncing(true);
    setShowRefreshed(false);
    const streamUrl = api.eksRefreshStreamUrl();
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
      await api.refreshEKS();
    } catch (e) {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    }
  }

  if (loading) return <div className="panel-loading">Loading EKS…</div>;
  if (error)   return <div className="panel-error">EKS: {error}</div>;

  const clusters = data?.clusters || [];

  const toggleSort = (col) =>
    setSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...clusters].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col], vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>EKS Clusters <span className="count-badge">{clusters.length}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={handleRefresh} disabled={refreshing || syncing} title="Sync from AWS and refresh">
            {showRefreshed ? (
              <span className="refresh-done"><Check size={13} /> Refreshed</span>
            ) : (
              <RefreshCw size={13} className={refreshing || syncing ? "spinning" : ""} />
            )}
          </button>
        </div>
      </div>

      {clusters.length === 0 ? (
        <div className="panel-empty">No EKS clusters found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="name"            label="Cluster"       sort={sort} onSort={toggleSort} />
                <SortTh col="status"          label="Status"        sort={sort} onSort={toggleSort} />
                <SortTh col="version"         label="K8s Version"   sort={sort} onSort={toggleSort} />
                <SortTh col="nodegroup_count" label="Node Groups"   sort={sort} onSort={toggleSort} />
                <SortTh col="node_count"      label="Total Nodes"   sort={sort} onSort={toggleSort} />
                <th>Endpoint Access</th>
                <SortTh col="created_at"      label="Created"       sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map(c => (
                <tr
                  key={c.arn}
                  className={`row-clickable ${selected?.arn === c.arn ? "row-selected" : ""}`}
                  onClick={() => setSelected(c)}
                >
                  <td className="cell-bold">{c.name}</td>
                  <td>
                    <span className={`state-pill ${STATUS_COLORS[c.status] || "state-gray"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="cell-mono">v{c.version}</td>
                  <td className="cell-mono">{c.nodegroup_count ?? c.nodegroups?.length ?? "—"}</td>
                  <td className="cell-mono">{c.node_count ?? "—"}</td>
                  <td>
                    {c.public_access && <span className="state-pill state-amber" style={{ marginRight: "0.25rem" }}>Public</span>}
                    {c.private_access && <span className="state-pill state-green">Private</span>}
                    {!c.public_access && !c.private_access && <span className="metric-na">—</span>}
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
                    {c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}
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
          <DetailDrawer cluster={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

function DetailDrawer({ cluster, onClose }) {
  const [detail, setDetail]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [sgExpanded, setSgExpanded] = useState({});
  const [showNodes, setShowNodes] = useState(false);
  const [openSections, setOpenSections] = useState({
    overview: true, endpoint: true, networking: true,
    nodegroups: false, logging: false, oidc: false,
    security: false, tags: false,
  });

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDetail(null);
    setSgExpanded({});
    api.getEKSDetail(cluster.name)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [cluster.name]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "Escape") return;
      // Keep parent drawer open while nested nodes modal is active.
      if (showNodes) return;
      onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, showNodes]);

  const toggle = (s) => setOpenSections(p => ({ ...p, [s]: !p[s] }));

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
          <div className="drawer-title">{cluster.name}</div>
          <div className="drawer-subtitle">
            {detail
              ? <span className={`state-pill ${STATUS_COLORS[detail.status] || "state-gray"}`}>{detail.status}</span>
              : !error && <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>Loading…</span>
            }
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            className="refresh-btn"
            onClick={() => setShowNodes(true)}
            title="View Nodes"
            style={{ padding: "0.3rem 0.6rem", display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.75rem" }}
          >
            <Users size={13} /> Nodes
          </button>
          <button className="drawer-close" onClick={onClose}><X size={16} /></button>
        </div>
      </div>

      <div className="drawer-body">
        {loading && <div className="panel-loading">Loading details…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {detail  && (
          <>
            {/* ── Overview ── */}
            <Section id="overview" title="Overview" icon={Info}>
              <div className="drawer-meta-grid">
                <MetaItem label="K8s Version"      value={`v${detail.version}`} mono />
                <MetaItem label="Platform Version" value={detail.platform_version || "—"} mono />
                <MetaItem label="Status"           value={detail.status} />
                <MetaItem label="Created"          value={detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"} />
                <MetaItem label="Role ARN"         value={detail.role_arn || "—"} mono />
                <MetaItem label="Cluster ARN"      value={detail.arn} mono />
              </div>
            </Section>

            {/* ── Endpoint ── */}
            <Section id="endpoint" title="Endpoint" icon={Server}>
              {detail.endpoint ? (
                <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      API Server Endpoint
                      {detail.public_access && <span style={{ fontSize: "0.6rem", background: "var(--bg-card)", color: "var(--amber)", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, border: "1px solid var(--border)" }}>PUBLIC</span>}
                      {detail.private_access && <span style={{ fontSize: "0.6rem", background: "var(--bg-card)", color: "var(--green)", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, border: "1px solid var(--border)" }}>PRIVATE</span>}
                    </span>
                    <CopyButton text={detail.endpoint} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.8rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.endpoint}
                  </div>
                </div>
              ) : (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No endpoint available</span>
              )}
              {detail.public_access_cidrs?.length > 0 && (
                <div style={{ marginTop: "0.75rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Allowed Public CIDRs</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                    {detail.public_access_cidrs.map(cidr => (
                      <span key={cidr} className="cell-mono" style={{ fontSize: "0.72rem", padding: "0.15rem 0.4rem", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "4px" }}>{cidr}</span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* ── Networking ── */}
            <Section id="networking" title="Networking" icon={Network}>
              <div className="drawer-meta-grid">
                {detail.vpc_id && <MetaItem label="VPC" value={detail.vpc_id} mono />}
              </div>
              {detail.subnet_ids?.length > 0 && (
                <div style={{ marginTop: "0.6rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Subnets ({detail.subnet_ids.length})</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                    {detail.subnet_ids.map(s => (
                      <span key={s} className="cell-mono" style={{ fontSize: "0.72rem", padding: "0.15rem 0.4rem", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "4px" }}>{s}</span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* ── Node Groups ── */}
            <Section id="nodegroups" title={`Node Groups (${detail.nodegroups?.length ?? 0})`} icon={Server}>
              {detail.nodegroups?.length === 0 ? (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No node groups</span>
              ) : (
                <div className="table-wrap">
                  <table className="data-table" style={{ fontSize: "0.78rem" }}>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Instance Types</th>
                        <th>D / Min / Max</th>
                        <th>Capacity</th>
                        <th>AMI</th>
                        <th>Disk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.nodegroups ?? []).map(ng => (
                        <tr key={ng.name}>
                          <td className="cell-bold">{ng.name}</td>
                          <td>
                            <span className={`state-pill ${STATUS_COLORS[ng.status] || "state-gray"}`} style={{ fontSize: "0.65rem" }}>
                              {ng.status}
                            </span>
                          </td>
                          <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{ng.instance_types?.join(", ") || "—"}</td>
                          <td className="cell-mono">
                            {ng.scaling_config
                              ? `${ng.scaling_config.desiredSize} / ${ng.scaling_config.minSize} / ${ng.scaling_config.maxSize}`
                              : "—"}
                          </td>
                          <td>
                            <span className={`state-pill ${ng.capacity_type === "SPOT" ? "state-amber" : "state-green"}`} style={{ fontSize: "0.65rem" }}>
                              {ng.capacity_type || "—"}
                            </span>
                          </td>
                          <td className="cell-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>{ng.ami_type || "—"}</td>
                          <td className="cell-mono">{ng.disk_size ? `${ng.disk_size} GB` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {detail.nodegroups?.some(ng => ng.health_issues?.length > 0) && (
                <div style={{ marginTop: "0.75rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem", color: "var(--red)" }}>Health Issues</span>
                  {(detail.nodegroups ?? []).filter(ng => ng.health_issues?.length > 0).map(ng => (
                    <div key={ng.name} style={{ marginBottom: "0.4rem" }}>
                      <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>{ng.name}:</span>
                      {ng.health_issues.map((issue, i) => (
                        <div key={i} style={{ fontSize: "0.75rem", color: "var(--red)", marginLeft: "0.75rem" }}>
                          {issue.code}: {issue.message}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* ── Encryption & Logging ── */}
            <Section id="logging" title="Encryption & Logging" icon={Lock}>
              <div className="drawer-meta-grid">
                <MetaItem label="Encryption at Rest" value={detail.kms_key ? "Enabled (KMS)" : "Default"} />
                {detail.kms_key && <MetaItem label="KMS Key ARN" value={detail.kms_key} mono />}
              </div>
              <div style={{ marginTop: "0.75rem" }}>
                <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Control Plane Logging</span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                  {["api", "audit", "authenticator", "controllerManager", "scheduler"].map(logType => {
                    const enabled = detail.enabled_log_types?.includes(logType);
                    return (
                      <span
                        key={logType}
                        className={`state-pill ${enabled ? "state-green" : "state-gray"}`}
                        style={{ fontSize: "0.68rem" }}
                      >
                        {logType}
                      </span>
                    );
                  })}
                </div>
              </div>
            </Section>

            {/* ── OIDC ── */}
            {detail.oidc_issuer && (
              <Section id="oidc" title="OIDC / IAM Roles for Service Accounts" icon={Settings}>
                <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between" }}>
                    <span>OIDC Issuer URL</span>
                    <CopyButton text={detail.oidc_issuer} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.78rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.oidc_issuer}
                  </div>
                </div>
              </Section>
            )}

            {/* ── Security Groups ── */}
            {detail.security_groups?.length > 0 && (
              <Section id="security" title={`Security Groups (${detail.security_groups.length})`} icon={Shield}>
                {detail.security_groups.map(sg => (
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
                        <RulesTable label="Inbound"  rules={sg.inbound}  />
                        <RulesTable label="Outbound" rules={sg.outbound} />
                      </div>
                    )}
                  </div>
                ))}
              </Section>
            )}

            {/* ── Tags ── */}
            {detail.tags?.length > 0 && (
              <Section id="tags" title={`Tags (${detail.tags.length})`} icon={Info}>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>Key</th><th>Value</th></tr></thead>
                    <tbody>
                      {detail.tags.map(t => (
                        <tr key={t.key}>
                          <td className="cell-mono" style={{ color: "var(--text-dim)" }}>{t.key}</td>
                          <td style={{ whiteSpace: "normal", wordBreak: "break-all" }}>{t.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}
          </>
        )}
      </div>

      {showNodes && (
        <NodesModal cluster={cluster} onClose={() => setShowNodes(false)} />
      )}
    </div>
  );
}

function MetaItem({ label, value, mono }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span
        className={`drawer-meta-val ${mono ? "cell-mono" : ""}`}
        style={mono ? { fontSize: "0.72rem", wordBreak: "break-all" } : {}}
      >
        {value}
      </span>
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
        <thead><tr><th>Protocol</th><th>Port</th><th>Source / Dest</th></tr></thead>
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

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
    >
      {copied ? <Check size={12} style={{ color: "var(--green)" }} /> : <Copy size={12} />}
    </button>
  );
}

function NodesModal({ cluster, onClose }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [filter, setFilter]   = useState("all");
  const [sort, setSort]       = useState({ col: "name", dir: "asc" });
  const [selectedNode, setSelectedNode] = useState(null);

  useEffect(() => {
    api.getEKSNodes(cluster.name)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [cluster.name]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "Escape") return;
      // Keep nodes modal open while nested node drawer is active.
      if (selectedNode) return;
      onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, selectedNode]);

  const nodes = data?.nodes || [];

  // Build filter tabs
  const nodegroups = [...new Set(nodes.filter(n => n.nodegroup_name).map(n => n.nodegroup_name))];
  const hasKarpenter = nodes.some(n => n.karpenter_pool);

  const filtered = nodes.filter(n => {
    if (filter === "all") return true;
    if (filter === "karpenter") return !!n.karpenter_pool;
    return n.nodegroup_name === filter;
  });

  const toggleSort = (col) =>
    setSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...filtered].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col], vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });

  const STATE_COLORS = { running: "state-green", stopped: "state-red", pending: "state-amber", stopping: "state-amber", terminated: "state-gray", "shutting-down": "state-gray" };

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{cluster.name}</div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {loading ? "Loading…" : `${data?.count ?? 0} nodes`}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {/* Filter tabs */}
          {!loading && !error && (
            <div className="metrics-range-tabs">
              <button className={`metrics-range-tab ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>All</button>
              {nodegroups.map(ng => (
                <button key={ng} className={`metrics-range-tab ${filter === ng ? "active" : ""}`} onClick={() => setFilter(ng)}>
                  {ng}
                </button>
              ))}
              {hasKarpenter && (
                <button className={`metrics-range-tab ${filter === "karpenter" ? "active" : ""}`} onClick={() => setFilter("karpenter")}>
                  Karpenter
                </button>
              )}
            </div>
          )}
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading nodes…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {!loading && !error && sorted.length === 0 && (
          <div className="panel-empty">No nodes found</div>
        )}
        {!loading && !error && sorted.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh col="name"       label="Name"       sort={sort} onSort={toggleSort} />
                  <th>Instance ID</th>
                  <SortTh col="state"      label="State"      sort={sort} onSort={toggleSort} />
                  <SortTh col="type"       label="Type"       sort={sort} onSort={toggleSort} />
                  <SortTh col="az"         label="AZ"         sort={sort} onSort={toggleSort} />
                  <SortTh col="private_ip" label="Private IP" sort={sort} onSort={toggleSort} />
                  <SortTh col="uptime_hours" label="Uptime"   sort={sort} onSort={toggleSort} />
                  <SortTh col="cpu_percent"  label="CPU"      sort={sort} onSort={toggleSort} />
                  <SortTh col="pod_count"    label="Pods"     sort={sort} onSort={toggleSort} />
                  <th>Group</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(n => (
                  <tr key={n.id} className={`row-clickable ${selectedNode?.id === n.id ? "row-selected" : ""}`} onClick={() => setSelectedNode(n)}>
                    <td className="cell-bold">{n.name}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{n.id}</td>
                    <td><span className={`state-pill ${STATE_COLORS[n.state] || "state-gray"}`}>{n.state}</span></td>
                    <td className="cell-mono">{n.type}</td>
                    <td>{n.az}</td>
                    <td className="cell-mono">{n.private_ip || "—"}</td>
                    <td className="cell-mono">{formatUptime(n.uptime_hours)}</td>
                    <td><CpuBar value={n.cpu_percent} /></td>
                    <td className="cell-mono">{n.pod_count != null ? n.pod_count : <span className="metric-na">—</span>}</td>
                    <td>
                      {n.karpenter_pool
                        ? <span className="state-pill state-amber" style={{ fontSize: "0.65rem" }}>Karpenter</span>
                        : n.nodegroup_name
                          ? <span className="state-pill state-green" style={{ fontSize: "0.65rem" }}>{n.nodegroup_name}</span>
                          : <span className="metric-na">—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedNode && (
        <NodeDetailDrawer node={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
    </div>
  );
}

function NodeDetailDrawer({ node, onClose }) {
  const [detail, setDetail]     = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [sgExpanded, setSgExpanded] = useState({});
  const [showMetrics, setShowMetrics] = useState(false);

  useEffect(() => {
    setDetail(null); setLoading(true); setError(null); setSgExpanded({});
    api.getEC2Detail(node.id)
      .then(d => { setDetail(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [node.id]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "Escape") return;
      // Close metrics first, then node drawer on the next Escape.
      if (showMetrics) {
        setShowMetrics(false);
        return;
      }
      onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, showMetrics]);

  const STATE_COLORS = { running: "state-green", stopped: "state-red", pending: "state-amber", stopping: "state-amber", terminated: "state-gray", "shutting-down": "state-gray" };

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="detail-drawer">
        <div className="drawer-header">
          <div>
            <div className="drawer-title">{node.name}</div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.35rem" }}>
              <span className={`state-pill ${STATE_COLORS[node.state] || "state-gray"}`}>{node.state}</span>
              <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{node.id}</span>
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

        <div className="drawer-body">
          {loading && <div className="panel-loading" style={{ padding: "2rem 0" }}>Loading details…</div>}
          {error   && <div className="panel-error">{error}</div>}
          {detail  && (
            <>
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
                  {node.nodegroup_name && <MetaItem label="Node Group"  value={node.nodegroup_name} />}
                  {node.karpenter_pool && <MetaItem label="Karpenter Pool" value={node.karpenter_pool} />}
                  {node.pod_count != null && <MetaItem label="Running Pods" value={String(node.pod_count)} />}
                </div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-hdr">Metrics (last 5 min avg)</div>
                <div className="ec2-metrics-grid">
                  <MetricBox label="CPU"       value={detail.metrics.cpu_percent != null ? `${detail.metrics.cpu_percent}%` : "N/A"} color={detail.metrics.cpu_percent != null ? (detail.metrics.cpu_percent > 80 ? "var(--red)" : detail.metrics.cpu_percent > 50 ? "var(--amber)" : "var(--green)") : undefined} />
                  <MetricBox label="Net In"    value={formatBytes(detail.metrics.network_in_bytes)} />
                  <MetricBox label="Net Out"   value={formatBytes(detail.metrics.network_out_bytes)} />
                  <MetricBox label="Disk Read" value={formatBytes(detail.metrics.disk_read_bytes)} />
                  <MetricBox label="Disk Write" value={formatBytes(detail.metrics.disk_write_bytes)} />
                </div>
              </div>

              {detail.security_groups?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Security Groups ({detail.security_groups.length})</div>
                  {detail.security_groups.map(sg => (
                    <div key={sg.id} className="ec2-sg-block">
                      <button className="ec2-sg-hdr" onClick={() => setSgExpanded(p => ({ ...p, [sg.id]: !p[sg.id] }))}>
                        {sgExpanded[sg.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        <span style={{ fontWeight: 600 }}>{sg.name}</span>
                        <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginLeft: "0.4rem" }}>{sg.id}</span>
                      </button>
                      {sgExpanded[sg.id] && (
                        <div className="ec2-sg-rules">
                          <RulesTable label="Inbound"  rules={sg.inbound}  />
                          <RulesTable label="Outbound" rules={sg.outbound} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {detail.volumes?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Storage ({detail.volumes.length} volume{detail.volumes.length > 1 ? "s" : ""})</div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead><tr><th>Device</th><th>Volume ID</th><th>Size</th><th>Type</th><th>State</th><th>Encrypted</th></tr></thead>
                      <tbody>
                        {detail.volumes.map(v => (
                          <tr key={v.id}>
                            <td className="cell-mono">{v.device}</td>
                            <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{v.id}</td>
                            <td className="cell-mono">{v.size_gb} GB</td>
                            <td className="cell-mono">{v.type}{v.iops ? ` · ${v.iops} IOPS` : ""}</td>
                            <td><span className={`state-pill ${v.state === "in-use" ? "state-green" : "state-gray"}`}>{v.state}</span></td>
                            <td><span className={`state-pill ${v.encrypted ? "state-green" : "state-gray"}`}>{v.encrypted ? "Yes" : "No"}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {detail.tags?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Tags ({detail.tags.length})</div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead><tr><th>Key</th><th>Value</th></tr></thead>
                      <tbody>
                        {detail.tags.map(t => (
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

      {showMetrics && <NodeMetricsModal node={node} onClose={() => setShowMetrics(false)} />}
    </>
  );
}

function NodeMetricsModal({ node, onClose }) {
  const [hours, setHours]   = useState(24);
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  useEffect(() => {
    setData(null); setLoading(true); setError(null);
    api.getEC2Metrics(node.id, hours)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [node.id, hours]);

  function merge(s1, k1, s2, k2) {
    const map = {};
    (s1 || []).forEach(p => { map[p.ts] = { ts: p.ts, [k1]: p.v }; });
    (s2 || []).forEach(p => { if (map[p.ts]) map[p.ts][k2] = p.v; else map[p.ts] = { ts: p.ts, [k2]: p.v }; });
    return Object.values(map).sort((a, b) => a.ts < b.ts ? -1 : 1);
  }

  const m = data?.metrics;
  const networkData = m ? merge(m.network_in, "in", m.network_out, "out") : [];
  const diskData    = m ? merge(m.disk_read, "read", m.disk_write, "write") : [];

  return (
    <div className="metrics-modal" style={{ zIndex: 1100 }}>
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{node.name}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>{node.id}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="metrics-range-tabs">
            {[1, 6, 24, 72].map(h => (
              <button key={h} className={`metrics-range-tab ${hours === h ? "active" : ""}`} onClick={() => setHours(h)}>{h}h</button>
            ))}
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>
      </div>
      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading metrics…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <NodeMetricChart title="CPU Utilization" hours={hours} series={[{ data: m.cpu, key: "v", color: "var(--green)", label: "CPU %" }]} yDomain={[0, 100]} yFmt={v => `${v}%`} tipFmt={v => `${v}%`} />
            <NodeMetricChart title="Memory" hours={hours} series={[{ data: m.memory, key: "v", color: "var(--blue)", label: "Mem %" }]} yDomain={[0, 100]} yFmt={v => `${v}%`} tipFmt={v => `${v}%`} emptyNote="No data — requires CloudWatch Agent" />
            <NodeMetricChart title="Network Traffic (bytes / period)" hours={hours} series={[{ data: networkData, key: "in", color: "var(--amber)", label: "In" }, { data: networkData, key: "out", color: "var(--blue)", label: "Out" }]} merged yFmt={formatBytes} tipFmt={formatBytes} />
            <NodeMetricChart title="Disk I/O (bytes / period)" hours={hours} series={[{ data: diskData, key: "read", color: "var(--amber)", label: "Read" }, { data: diskData, key: "write", color: "var(--red)", label: "Write" }]} merged yFmt={formatBytes} tipFmt={formatBytes} />
          </div>
        )}
      </div>
    </div>
  );
}
