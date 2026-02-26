import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, Server, BarChart2,
  ChevronDown, ChevronRight, Database, Copy, Check,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import { REFRESH_STREAM_TIMEOUT_MS, METRICS_FROM_COLLECTOR_LABEL, METRICS_EMPTY_NOTE } from "../constants";
import ResourceAlerts from "./ResourceAlerts";

const STATUS_COLORS = {
  available: "state-green",
  creating: "state-amber",
  deleting: "state-red",
  failed: "state-red",
  maintenance: "state-amber",
  modifying: "state-amber",
  rebooting: "state-amber",
  starting: "state-amber",
  stopped: "state-red",
  stopping: "state-amber",
  upgrading: "state-amber",
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

function CpuCell({ v }) {
  if (v == null) return <span className="metric-na">—</span>;
  const color = v > 80 ? "var(--red)" : v > 50 ? "var(--amber)" : undefined;
  return <span className="cell-mono" style={{ color }}>{v}%</span>;
}

function MetaItem({ label, value, mono }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span className={`drawer-meta-val ${mono ? "cell-mono" : ""}`} style={mono ? { fontSize: "0.72rem", wordBreak: "break-all" } : {}}>
        {value}
      </span>
    </div>
  );
}

function EndpointBlock({ label, value }) {
  return (
    <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
      <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>{label}</span>
        <button
          onClick={() => { navigator.clipboard.writeText(value); }}
          style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
        >
          <Copy size={12} />
        </button>
      </div>
      <div className="cell-mono" style={{ fontSize: "0.8rem", color: "var(--amber)", wordBreak: "break-all" }}>
        {value}
      </div>
    </div>
  );
}

export default function DocumentDBPanel({ resourceAlarms = [] }) {
  const fetcher = useCallback((force = false) => api.getDocDB(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [clSort, setClSort] = useState({ col: "id", dir: "asc" });
  const [instSort, setInstSort] = useState({ col: "id", dir: "asc" });
  const [syncing, setSyncing] = useState(false);
  const [showRefreshed, setShowRefreshed] = useState(false);

  async function handleRefresh() {
    setSyncing(true);
    setShowRefreshed(false);
    const streamUrl = api.docdbRefreshStreamUrl();
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
      await api.refreshDocDB();
    } catch (e) {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    }
  }

  if (loading) return <div className="panel-loading">Loading DocumentDB…</div>;
  if (error) return <div className="panel-error">DocumentDB: {error}</div>;

  const clusters = data?.clusters || [];
  const instances = data?.instances || [];

  const sortRows = (arr, s) => [...arr].sort((a, b) => {
    const d = s.dir === "asc" ? 1 : -1;
    const va = a[s.col], vb = b[s.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });
  const mkToggle = (setter) => (col) =>
    setter(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sortedClusters = sortRows(clusters, clSort);
  const sortedInstances = sortRows(instances, instSort);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>DocumentDB <span className="count-badge">{data?.total ?? 0}</span></h2>
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

      {clusters.length > 0 && (
        <>
          <div className="table-section-hdr" style={{ paddingLeft: "1rem" }}>Clusters</div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "32px" }}></th>
                  <SortTh col="id" label="Cluster ID" sort={clSort} onSort={mkToggle(setClSort)} />
                  <SortTh col="engine" label="Engine" sort={clSort} onSort={mkToggle(setClSort)} />
                  <SortTh col="status" label="Status" sort={clSort} onSort={mkToggle(setClSort)} />
                  <SortTh col="instance_count" label="Nodes" sort={clSort} onSort={mkToggle(setClSort)} />
                  <SortTh col="writer_class" label="Writer Class" sort={clSort} onSort={mkToggle(setClSort)} />
                  <th>Multi-AZ</th>
                  <th>Enc.</th>
                  <SortTh col="cpu_percent" label="CPU" sort={clSort} onSort={mkToggle(setClSort)} />
                  <SortTh col="connections" label="Conn." sort={clSort} onSort={mkToggle(setClSort)} />
                  <th>Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {sortedClusters.map(c => (
                  <tr
                    key={c.id}
                    className={`row-clickable ${selected?.id === c.id ? "row-selected" : ""}`}
                    onClick={() => setSelected({ id: c.id, type: "cluster" })}
                  >
                    <td>
                      {resourceAlarms.some(a => a.resource_id === c.id && a.state === "ALARM") && (
                        <span className="alert-dot" title={`${resourceAlarms.filter(a => a.resource_id === c.id && a.state === "ALARM").length} active alarm(s)`} />
                      )}
                    </td>
                    <td className="cell-bold">{c.id}</td>
                    <td className="cell-mono" style={{ fontSize: "0.8rem" }}>{c.engine} {c.version}</td>
                    <td><span className={`state-pill ${STATUS_COLORS[c.status] || "state-gray"}`}>{c.status}</span></td>
                    <td className="cell-mono">{c.instance_count}</td>
                    <td className="cell-mono" style={{ fontSize: "0.75rem" }}>{c.writer_class}</td>
                    <td><span className={`state-pill ${c.multi_az ? "state-green" : "state-gray"}`}>{c.multi_az ? "Yes" : "No"}</span></td>
                    <td><span className={`state-pill ${c.encrypted ? "state-green" : "state-gray"}`}>{c.encrypted ? "Yes" : "No"}</span></td>
                    <td><CpuCell v={c.cpu_percent} /></td>
                    <td className="cell-mono">{c.connections ?? <span className="metric-na">—</span>}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {c.endpoint ? `${c.endpoint}:${c.port}` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {instances.length > 0 && (
        <>
          <div className="table-section-hdr" style={{ marginTop: clusters.length > 0 ? "1.5rem" : 0, paddingLeft: "1rem" }}>Standalone Instances</div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "32px" }}></th>
                  <SortTh col="id" label="Identifier" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <SortTh col="engine" label="Engine" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <SortTh col="class" label="Class" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <SortTh col="status" label="Status" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <th>Multi-AZ</th>
                  <SortTh col="storage_gb" label="Storage" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <th>Enc.</th>
                  <SortTh col="cpu_percent" label="CPU" sort={instSort} onSort={mkToggle(setInstSort)} />
                  <SortTh col="connections" label="Conn." sort={instSort} onSort={mkToggle(setInstSort)} />
                  <th>Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {sortedInstances.map(i => (
                  <tr
                    key={i.id}
                    className={`row-clickable ${selected?.id === i.id ? "row-selected" : ""}`}
                    onClick={() => setSelected({ id: i.id, type: "instance" })}
                  >
                    <td>
                      {resourceAlarms.some(a => a.resource_id === i.id && a.state === "ALARM") && (
                        <span className="alert-dot" title={`${resourceAlarms.filter(a => a.resource_id === i.id && a.state === "ALARM").length} active alarm(s)`} />
                      )}
                    </td>
                    <td className="cell-bold">{i.id}</td>
                    <td className="cell-mono" style={{ fontSize: "0.8rem" }}>{i.engine} {i.version}</td>
                    <td className="cell-mono" style={{ fontSize: "0.75rem" }}>{i.class}</td>
                    <td><span className={`state-pill ${STATUS_COLORS[i.status] || "state-gray"}`}>{i.status}</span></td>
                    <td><span className={`state-pill ${i.multi_az ? "state-green" : "state-gray"}`}>{i.multi_az ? "Yes" : "No"}</span></td>
                    <td className="cell-mono">{i.storage_gb} GB</td>
                    <td><span className={`state-pill ${i.encrypted ? "state-green" : "state-gray"}`}>{i.encrypted ? "Yes" : "No"}</span></td>
                    <td><CpuCell v={i.cpu_percent} /></td>
                    <td className="cell-mono">{i.connections ?? <span className="metric-na">—</span>}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{i.endpoint ? `${i.endpoint}:${i.port}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {clusters.length === 0 && instances.length === 0 && (
        <div className="panel-empty">No DocumentDB clusters or instances found</div>
      )}

      {selected && (
        <>
          <div className="drawer-backdrop" onClick={() => setSelected(null)} />
          <DocDBDetailDrawer item={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

function DocDBDetailDrawer({ item, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showMetrics, setShowMetrics] = useState(false);
  const [openSections, setOpenSections] = useState({ overview: true, endpoint: true, members: true });

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDetail(null);
    api.getDocDBDetail(item.id, item.type === "cluster")
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [item.id, item.type]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "Escape") return;
      if (showMetrics) { setShowMetrics(false); return; }
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
          <div className="drawer-title">{item.id}</div>
          <div className="drawer-subtitle">
            {detail && <span className={`state-pill ${STATUS_COLORS[detail.status] || "state-gray"}`}>{detail.status}</span>}
            {!detail && !error && <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>Loading…</span>}
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
            <ResourceAlerts resourceId={item.id} />
            <Section id="overview" title="Overview" icon={Info}>
              <div className="drawer-meta-grid">
                <MetaItem label="Engine" value={`${detail.engine} ${detail.version}`} />
                <MetaItem label="Status" value={detail.status} />
                {detail.type === "cluster" ? (
                  <>
                    <MetaItem label="Nodes" value={detail.members?.length ?? "—"} />
                    <MetaItem label="Writer" value={detail.members?.find(m => m.role === "Writer")?.class || "—"} mono />
                    <MetaItem label="Multi-AZ" value={detail.multi_az ? "Yes" : "No"} />
                  </>
                ) : (
                  <>
                    <MetaItem label="Class" value={detail.class} mono />
                    <MetaItem label="AZ" value={detail.az} mono />
                    <MetaItem label="Multi-AZ" value={detail.multi_az ? "Yes" : "No"} />
                    <MetaItem label="Storage" value={`${detail.storage_gb} GB (${detail.storage_type})`} />
                  </>
                )}
                <MetaItem label="Encrypted" value={detail.encrypted ? "Yes" : "No"} />
                <MetaItem label="Deletion protection" value={detail.deletion_protection ? "Enabled" : "Disabled"} />
              </div>
            </Section>

            <Section id="endpoint" title={detail.type === "cluster" ? "Endpoints" : "Endpoint"} icon={Database}>
              {detail.type === "cluster" ? (
                <>
                  {detail.endpoint && <EndpointBlock label="Write Endpoint" value={`${detail.endpoint}:${detail.port}`} />}
                  {detail.reader_endpoint && (
                    <div style={{ marginTop: "0.5rem" }}>
                      <EndpointBlock label="Reader Endpoint" value={`${detail.reader_endpoint}:${detail.port}`} />
                    </div>
                  )}
                </>
              ) : (
                detail.endpoint && <EndpointBlock label="Endpoint" value={`${detail.endpoint}:${detail.port}`} />
              )}
            </Section>

            {detail.type === "cluster" && detail.members?.length > 0 && (
              <Section id="members" title={`Instances (${detail.members.length})`} icon={Server}>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Identifier</th>
                        <th>Role</th>
                        <th>Class</th>
                        <th>AZ</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.members.map(m => (
                        <tr key={m.id}>
                          <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{m.id}</td>
                          <td><span className={`state-pill ${m.role === "Writer" ? "state-amber" : "state-green"}`}>{m.role}</span></td>
                          <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{m.class}</td>
                          <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{m.az}</td>
                          <td><span className={`state-pill ${STATUS_COLORS[m.status] || "state-gray"}`}>{m.status}</span></td>
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

      {showMetrics && <DocDBMetricsModal item={item} onClose={() => setShowMetrics(false)} />}
    </div>
  );
}

function DocDBMetricsModal({ item, onClose }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setLoading(true);
    setError(null);
    api.getDocDBMetrics(item.id, item.type === "cluster", hours)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [item.id, item.type, hours]);

  const xFmt = (s) => {
    const d = new Date(s);
    if (hours <= 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  };

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{item.id}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {item.type === "cluster" ? "Cluster (writer node)" : "Instance"} · {hours}h range
          </div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
            {METRICS_FROM_COLLECTOR_LABEL}
          </div>
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
        {error && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <DocDBMetricChart title="CPU Utilization" hours={hours} data={data.CPUUtilization} color="var(--blue)" yFmt={v => `${v}%`} xFmt={xFmt} />
            <DocDBMetricChart title="DB Connections" hours={hours} data={data.DatabaseConnections} color="var(--green)" xFmt={xFmt} />
            <DocDBMetricChart title="Free Storage (GB)" hours={hours} data={data.FreeStorageSpace} color="var(--teal)" yFmt={v => `${v} GB`} xFmt={xFmt} />
            <DocDBMetricChart title="Read IOPS" hours={hours} data={data.ReadIOPS} color="var(--brand)" xFmt={xFmt} />
            <DocDBMetricChart title="Write IOPS" hours={hours} data={data.WriteIOPS} color="var(--red)" xFmt={xFmt} />
          </div>
        )}
      </div>
    </div>
  );
}

function DocDBMetricChart({ title, hours, data, color, yFmt, xFmt }) {
  const isEmpty = !data || data.length === 0;
  const tickInterval = isEmpty ? 0 : Math.max(0, Math.floor(data.length / 6) - 1);
  return (
    <div className="metrics-chart-card">
      <div className="metrics-chart-title">{title}</div>
      {isEmpty ? (
        <div className="metrics-chart-empty">{METRICS_EMPTY_NOTE}</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2840" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={xFmt} interval={tickInterval} />
            <YAxis tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={yFmt || (v => v)} width={56} />
            <Tooltip content={({ active, payload, label }) => active && payload?.length ? (
              <div className="chart-tooltip">
                <div className="tooltip-label">{xFmt(label)}</div>
                <div style={{ color: payload[0].stroke, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
                  {yFmt ? yFmt(payload[0].value) : payload[0].value}
                </div>
              </div>
            ) : null} />
            <Line type="monotone" dataKey="value" stroke={color} dot={false} strokeWidth={1.5} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
