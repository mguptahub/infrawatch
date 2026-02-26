import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

function MetricChip({ label, value, unit = "", warn = 70, danger = 90 }) {
  if (value === null || value === undefined) return <span className="metric-na">N/A</span>;
  const color = value >= danger ? "var(--red)" : value >= warn ? "var(--amber)" : "var(--green)";
  return (
    <span className="metric-chip" style={{ color, borderColor: color }}>
      {value}{unit} <span style={{ opacity: 0.6, fontSize: "0.7rem" }}>{label}</span>
    </span>
  );
}

export default function OpenSearchPanel() {
  const fetcher = useCallback((force = false) => api.getOpenSearch(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading OpenSearch…</div>;
  if (error) return <div className="panel-error">OpenSearch: {error}</div>;

  const domains = data?.domains || [];

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>OpenSearch Domains <span className="count-badge">{data?.count ?? 0}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
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
                <th>Domain</th>
                <th>Version</th>
                <th>Status</th>
                <th>Nodes</th>
                <th>Instance Type</th>
                <th>Storage</th>
                <th>CPU</th>
                <th>JVM Mem</th>
                <th>HA</th>
                <th>Endpoint</th>
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => (
                <tr key={d.name}>
                  <td className="cell-bold">{d.name}</td>
                  <td className="cell-mono">{d.engine_version}</td>
                  <td>
                    <span className={`state-pill ${d.status === "Active" ? "state-green" : "state-amber"}`}>
                      {d.status}
                    </span>
                  </td>
                  <td>{d.instance_count}</td>
                  <td className="cell-mono">{d.instance_type}</td>
                  <td>{d.ebs_volume_gb ? `${d.ebs_volume_gb} GB` : "—"}</td>
                  <td><MetricChip label="CPU" value={d.cpu_percent} unit="%" /></td>
                  <td><MetricChip label="JVM" value={d.jvm_memory_percent} unit="%" warn={75} danger={90} /></td>
                  <td>
                    <span className={`state-pill ${d.zone_awareness ? "state-green" : "state-gray"}`}>
                      {d.zone_awareness ? "Multi-AZ" : "Single"}
                    </span>
                  </td>
                  <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{d.endpoint ? `${d.endpoint.slice(0, 40)}…` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
