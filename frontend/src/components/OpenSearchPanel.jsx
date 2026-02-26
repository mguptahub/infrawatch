import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, BarChart2,
  ChevronDown, ChevronRight, Shield, Settings, Database, Copy, Check, Server,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import { REFRESH_STREAM_TIMEOUT_MS, METRICS_FROM_COLLECTOR_LABEL, METRICS_EMPTY_NOTE } from "../constants";
import AlertBanner from "./AlertBanner";
import ResourceAlerts from "./ResourceAlerts";

const STATUS_COLORS = {
  Active:      "state-green",
  Processing:  "state-amber",
  Upgrading:   "state-amber",
  Deleting:    "state-red",
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

function StorageCell({ mb }) {
  if (mb == null) return <span className="metric-na">—</span>;
  const gb = (mb / 1024).toFixed(1);
  return <span className="cell-mono">{gb} GB</span>;
}

function PctCell({ v, warn = 70, danger = 90 }) {
  if (v == null) return <span className="metric-na">—</span>;
  const color = v >= danger ? "var(--red)" : v >= warn ? "var(--amber)" : undefined;
  return <span className="cell-mono" style={{ color }}>{v}%</span>;
}

export default function OpenSearchPanel() {
  const fetcher = useCallback((force = false) => api.getOpenSearch(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: "name", dir: "asc" });
  const [syncing, setSyncing] = useState(false);
  const [showRefreshed, setShowRefreshed] = useState(false);
  const [resourceAlarms, setResourceAlarms] = useState([]);

  async function handleRefresh() {
    setSyncing(true);
    setShowRefreshed(false);
    const streamUrl = api.opensearchRefreshStreamUrl();
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
      await api.refreshOpenSearch();
    } catch (e) {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    }
  }

  if (loading) return <div className="panel-loading">Loading OpenSearch…</div>;
  if (error)   return <div className="panel-error">OpenSearch: {error}</div>;

  const domains = data?.domains || [];

  const toggleSort = (col) =>
    setSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...domains].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col], vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });

  return (
    <section className="panel">
      <AlertBanner serviceType="opensearch" onAlarmsLoaded={setResourceAlarms} />
      <div className="panel-header">
        <h2>OpenSearch <span className="count-badge">{data?.count ?? 0}</span></h2>
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

      {domains.length === 0 ? (
        <div className="panel-empty">No OpenSearch domains found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: "32px" }}></th>
                <SortTh col="name"               label="Domain"         sort={sort} onSort={toggleSort} />
                <SortTh col="engine_version"     label="Version"        sort={sort} onSort={toggleSort} />
                <SortTh col="status"             label="Status"         sort={sort} onSort={toggleSort} />
                <SortTh col="instance_count"     label="Nodes"          sort={sort} onSort={toggleSort} />
                <SortTh col="instance_type"      label="Instance Type"  sort={sort} onSort={toggleSort} />
                <SortTh col="ebs_volume_gb"      label="Storage/Node"   sort={sort} onSort={toggleSort} />
                <th>Enc.</th>
                <th>HA</th>
                <SortTh col="cpu_percent"        label="CPU"            sort={sort} onSort={toggleSort} />
                <SortTh col="jvm_memory_percent" label="JVM"            sort={sort} onSort={toggleSort} />
                <SortTh col="free_storage_mb"    label="Free"           sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map(d => (
                <tr
                  key={d.name}
                  className={`row-clickable ${selected?.name === d.name ? "row-selected" : ""}`}
                  onClick={() => setSelected(d)}
                >
                  <td>
                    {resourceAlarms.some(a => a.resource_id === d.name && a.state === "ALARM") && (
                      <span className="alert-dot" title={`${resourceAlarms.filter(a => a.resource_id === d.name && a.state === "ALARM").length} active alarm(s)`} />
                    )}
                  </td>
                  <td className="cell-bold">{d.name}</td>
                  <td className="cell-mono" style={{ fontSize: "0.8rem" }}>{d.engine_version}</td>
                  <td>
                    <span className={`state-pill ${STATUS_COLORS[d.status] || "state-gray"}`}>
                      {d.status}
                    </span>
                  </td>
                  <td className="cell-mono">{d.instance_count}</td>
                  <td className="cell-mono" style={{ fontSize: "0.75rem" }}>{d.instance_type}</td>
                  <td className="cell-mono">{d.ebs_volume_gb ? `${d.ebs_volume_gb} GB` : "—"}</td>
                  <td>
                    <span className={`state-pill ${d.encrypted ? "state-green" : "state-gray"}`}>
                      {d.encrypted ? "Yes" : "No"}
                    </span>
                  </td>
                  <td>
                    <span className={`state-pill ${d.zone_awareness ? "state-green" : "state-gray"}`}>
                      {d.zone_awareness ? "Multi-AZ" : "Single"}
                    </span>
                  </td>
                  <td><PctCell v={d.cpu_percent} /></td>
                  <td><PctCell v={d.jvm_memory_percent} warn={75} danger={90} /></td>
                  <td><StorageCell mb={d.free_storage_mb} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <>
          <div className="drawer-backdrop" onClick={() => setSelected(null)} />
          <DetailDrawer domain={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

function DetailDrawer({ domain, onClose }) {
  const [detail, setDetail]       = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [showMetrics, setShowMetrics] = useState(false);
  const [sgExpanded, setSgExpanded]   = useState({});
  const [openSections, setOpenSections] = useState({
    overview: true, endpoint: true, cluster: false,
    storage: false, security: false, software: false, tags: false,
  });

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDetail(null);
    setSgExpanded({});
    api.getOpenSearchDetail(domain.name)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [domain.name]);

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
          <div className="drawer-title">{domain.name}</div>
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
        {error   && <div className="panel-error">{error}</div>}
        {detail  && (
          <>
            <ResourceAlerts resourceId={domain.name} />
            {/* ── Overview ── */}
            <Section id="overview" title="Overview" icon={Info}>
              <div className="drawer-meta-grid">
                <MetaItem label="Engine"     value={detail.engine_version} />
                <MetaItem label="Status"     value={detail.status} />
                <MetaItem label="Network"    value={detail.in_vpc ? "VPC" : "Public"} />
                {detail.in_vpc && detail.vpc_id && <MetaItem label="VPC" value={detail.vpc_id} mono />}
                <MetaItem label="Encrypted"  value={detail.encryption_at_rest ? "Yes" : "No"} />
                {detail.kms_key && <MetaItem label="KMS Key" value={detail.kms_key} mono />}
                <MetaItem label="N2N Encryption" value={detail.node_to_node_encryption ? "Enabled" : "Disabled"} />
                <MetaItem label="Fine-Grained Access" value={detail.fine_grained_access ? "Enabled" : "Disabled"} />
                {detail.snapshot_hour != null && (
                  <MetaItem label="Auto-Snapshot" value={`${String(detail.snapshot_hour).padStart(2, "0")}:00 UTC`} />
                )}
              </div>
            </Section>

            {/* ── Endpoint ── */}
            <Section id="endpoint" title="Endpoint" icon={Database}>
              {detail.endpoint ? (
                <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      Endpoint
                      {detail.enforce_https && (
                        <span style={{ fontSize: "0.6rem", background: "var(--brand-bg)", color: "var(--brand)", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, border: "1px solid var(--brand-border)" }}>
                          HTTPS
                        </span>
                      )}
                      {detail.tls_policy && detail.tls_policy !== "—" && (
                        <span style={{ fontSize: "0.6rem", color: "var(--text-muted)" }}>{detail.tls_policy}</span>
                      )}
                    </span>
                    <CopyButton text={`https://${detail.endpoint}`} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.8rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.endpoint}
                  </div>
                </div>
              ) : (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No endpoint available</span>
              )}
            </Section>

            {/* ── Cluster Config ── */}
            <Section id="cluster" title="Cluster Configuration" icon={Server}>
              <div className="drawer-meta-grid">
                <MetaItem label="Instance Type"  value={detail.cluster.instance_type} mono />
                <MetaItem label="Data Nodes"     value={detail.cluster.instance_count} />
                <MetaItem label="Zone Awareness" value={detail.cluster.zone_awareness ? `Multi-AZ (${detail.cluster.az_count} AZs)` : "Single-AZ"} />
                {detail.cluster.master_enabled && (<>
                  <MetaItem label="Dedicated Master" value="Enabled" />
                  <MetaItem label="Master Type"  value={detail.cluster.master_type || "—"} mono />
                  <MetaItem label="Master Count" value={detail.cluster.master_count ?? "—"} />
                </>)}
                {detail.cluster.warm_enabled && (<>
                  <MetaItem label="Warm Storage" value="Enabled" />
                  <MetaItem label="Warm Type"    value={detail.cluster.warm_type || "—"} mono />
                  <MetaItem label="Warm Nodes"   value={detail.cluster.warm_count ?? "—"} />
                </>)}
                {detail.azs?.length > 0 && (
                  <MetaItem label="AZs" value={detail.azs.join(", ")} />
                )}
              </div>
              {detail.subnets?.length > 0 && (
                <div style={{ marginTop: "0.8rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Subnets</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                    {detail.subnets.map(s => (
                      <span key={s} className="cell-mono" style={{ fontSize: "0.72rem", padding: "0.15rem 0.4rem", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "4px" }}>
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* ── Storage ── */}
            <Section id="storage" title="Storage (EBS)" icon={Database}>
              <div className="drawer-meta-grid">
                <MetaItem label="EBS Enabled" value={detail.ebs.enabled ? "Yes" : "No"} />
                {detail.ebs.enabled && (<>
                  <MetaItem label="Volume Type" value={detail.ebs.type || "—"} mono />
                  <MetaItem label="Size / Node"  value={detail.ebs.size_gb ? `${detail.ebs.size_gb} GB` : "—"} />
                  {detail.ebs.iops && <MetaItem label="IOPS"        value={detail.ebs.iops} />}
                  {detail.ebs.throughput && <MetaItem label="Throughput" value={`${detail.ebs.throughput} MB/s`} />}
                  {detail.cluster.instance_count && detail.ebs.size_gb && (
                    <MetaItem label="Total Storage" value={`${detail.cluster.instance_count * detail.ebs.size_gb} GB`} />
                  )}
                </>)}
              </div>
            </Section>

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

            {/* ── Software Update ── */}
            {detail.software && (
              <Section id="software" title="Service Software" icon={Settings}>
                <div className="drawer-meta-grid">
                  <MetaItem label="Current Version"   value={detail.software.current_version || "—"} mono />
                  <MetaItem label="Update Available"  value={detail.software.update_available ? "Yes" : "No"} />
                  {detail.software.update_available && (<>
                    <MetaItem label="New Version"   value={detail.software.new_version || "—"} mono />
                    <MetaItem label="Update Status" value={detail.software.update_status || "—"} />
                    <MetaItem label="Optional"      value={detail.software.optional_deploy ? "Yes" : "No"} />
                  </>)}
                </div>
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

      {showMetrics && (
        <MetricsModal domain={domain} onClose={() => setShowMetrics(false)} />
      )}
    </div>
  );
}

function MetricsModal({ domain, onClose }) {
  const [hours, setHours] = useState(24);
  const [data, setData]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  useEffect(() => {
    setData(null);
    setLoading(true);
    setError(null);
    api.getOpenSearchMetrics(domain.name, hours)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [domain.name, hours]);

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{domain.name}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {domain.engine_version} · {hours}h range
          </div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
            {METRICS_FROM_COLLECTOR_LABEL}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="metrics-range-tabs">
            {[1, 6, 24, 72].map(h => (
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
        {error   && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <MetricChart title="CPU Utilization"    hours={hours} data={data.CPUUtilization}       color="var(--blue)"   label="CPU"     yFmt={v => `${v}%`}    />
            <MetricChart title="JVM Memory"         hours={hours} data={data.JVMMemoryPressure}    color="var(--amber)"  label="JVM"     yFmt={v => `${v}%`}    />
            <MetricChart title="Free Storage"       hours={hours} data={data.FreeStorageSpace}     color="var(--teal)"   label="GB"      yFmt={v => `${v} GB`}  domain={[0, "auto"]} />
            <MetricChart title="System Memory"      hours={hours} data={data.SysMemoryUtilization} color="var(--green)"  label="Mem"     yFmt={v => `${v}%`}    />
            <MetricChart title="Search Rate"        hours={hours} data={data.SearchRate}           color="var(--brand)"  label="/min"                           domain={[0, "auto"]} />
            <MetricChart title="Indexing Rate"      hours={hours} data={data.IndexingRate}         color="var(--red)"    label="/min"                           />
          </div>
        )}
      </div>
    </div>
  );
}

function MetricChart({ title, hours, data, color, label, yFmt, domain }) {
  const isEmpty = !data || data.length === 0;
  const xFmt = (s) => {
    const d = new Date(s);
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
        <div className="metrics-chart-empty">{METRICS_EMPTY_NOTE}</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2840" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={xFmt} interval={tickInterval} />
            <YAxis tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={yFmt || (v => v)} width={56} domain={domain} />
            <Tooltip content={<ChartTooltip xFmt={xFmt} tipFmt={yFmt} seriesName={label} />} />
            <Line type="monotone" dataKey="value" stroke={color} dot={false} strokeWidth={1.5} connectNulls />
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
