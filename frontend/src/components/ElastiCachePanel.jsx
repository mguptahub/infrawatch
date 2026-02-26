import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

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

const STATUS_COLORS = {
  available: "state-green",
  creating: "state-amber",
  modifying: "state-amber",
  deleting: "state-red",
  "create-failed": "state-red",
};

export default function ElastiCachePanel() {
  const fetcher = useCallback((force = false) => api.getElastiCache(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading ElastiCache…</div>;
  if (error) return <div className="panel-error">ElastiCache: {error}</div>;

  const rgs = data?.replication_groups || [];
  const standalone = data?.standalone_clusters || [];

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
          <div style={{ padding: "0.6rem 1.25rem", background: "var(--bg-card)", borderBottom: "1px solid var(--border)" }}>
            <span style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-muted)" }}>
              Redis / Valkey Replication Groups
            </span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Group ID</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Shards</th>
                  <th>Members</th>
                  <th>Failover</th>
                  <th>TLS</th>
                  <th>CPU</th>
                  <th>Mem%</th>
                  <th>Hit Rate</th>
                  <th>Conns</th>
                  <th>Primary Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {rgs.map((rg) => (
                  <tr key={rg.id}>
                    <td className="cell-bold">{rg.id}</td>
                    <td>
                      <span className={`state-pill ${STATUS_COLORS[rg.status] || "state-amber"}`}>
                        {rg.status}
                      </span>
                    </td>
                    <td>{rg.mode}</td>
                    <td>{rg.node_groups}</td>
                    <td>{rg.member_clusters}</td>
                    <td>
                      <span className={`state-pill ${rg.automatic_failover === "enabled" ? "state-green" : "state-gray"}`}>
                        {rg.automatic_failover}
                      </span>
                    </td>
                    <td>
                      <span className={`state-pill ${rg.in_transit_encryption ? "state-green" : "state-gray"}`}>
                        {rg.in_transit_encryption ? "On" : "Off"}
                      </span>
                    </td>
                    <td>
                      {rg.cpu_percent !== null && rg.cpu_percent !== undefined
                        ? <span style={{ color: rg.cpu_percent > 80 ? "var(--red)" : "var(--text-dim)", fontFamily: "var(--font-mono)" }}>{rg.cpu_percent}%</span>
                        : <span className="metric-na">N/A</span>}
                    </td>
                    <td>
                      {rg.memory_percent !== null && rg.memory_percent !== undefined
                        ? <span style={{ color: rg.memory_percent > 80 ? "var(--red)" : "var(--amber)", fontFamily: "var(--font-mono)" }}>{rg.memory_percent}%</span>
                        : <span className="metric-na">N/A</span>}
                    </td>
                    <td><HitRate hits={rg.cache_hits} misses={rg.cache_misses} /></td>
                    <td className="cell-mono">{rg.connections ?? "—"}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>
                      {rg.primary_endpoint ? `${rg.primary_endpoint.slice(0, 35)}…` : "—"}
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
          <div style={{ padding: "0.6rem 1.25rem", background: "var(--bg-card)", borderBottom: "1px solid var(--border)", borderTop: rgs.length > 0 ? "1px solid var(--border)" : "none" }}>
            <span style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-muted)" }}>
              Memcached / Standalone Clusters
            </span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Cluster ID</th>
                  <th>Engine</th>
                  <th>Status</th>
                  <th>Node Type</th>
                  <th>Nodes</th>
                  <th>AZ</th>
                  <th>CPU</th>
                  <th>Mem%</th>
                  <th>Connections</th>
                  <th>Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {standalone.map((c) => (
                  <tr key={c.id}>
                    <td className="cell-bold">{c.id}</td>
                    <td className="cell-mono">{c.engine}</td>
                    <td>
                      <span className={`state-pill ${STATUS_COLORS[c.status] || "state-gray"}`}>
                        {c.status}
                      </span>
                    </td>
                    <td className="cell-mono">{c.node_type}</td>
                    <td>{c.num_nodes}</td>
                    <td>{c.az}</td>
                    <td className="cell-mono">{c.cpu_percent !== null && c.cpu_percent !== undefined ? `${c.cpu_percent}%` : "—"}</td>
                    <td className="cell-mono">{c.memory_percent !== null && c.memory_percent !== undefined ? `${c.memory_percent}%` : "—"}</td>
                    <td className="cell-mono">{c.connections ?? "—"}</td>
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
    </section>
  );
}
