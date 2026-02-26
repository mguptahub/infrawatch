import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const STATUS_COLORS = {
  available: "state-green",
  stopped: "state-red",
  starting: "state-amber",
  stopping: "state-amber",
  "backing-up": "state-amber",
  modifying: "state-amber",
  deleting: "state-red",
};

export default function RDSPanel() {
  const fetcher = useCallback((force = false) => api.getRDS(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading RDS…</div>;
  if (error) return <div className="panel-error">RDS: {error}</div>;

  const instances = data?.instances || [];

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>RDS Instances <span className="count-badge">{data?.count ?? 0}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>
      {instances.length === 0 ? (
        <div className="panel-empty">No RDS instances found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Identifier</th>
                <th>Engine</th>
                <th>Class</th>
                <th>Status</th>
                <th>Multi-AZ</th>
                <th>Storage</th>
                <th>CPU</th>
                <th>Connections</th>
                <th>Endpoint</th>
              </tr>
            </thead>
            <tbody>
              {instances.map((db) => (
                <tr key={db.id}>
                  <td className="cell-bold">{db.id}</td>
                  <td className="cell-mono">{db.engine}</td>
                  <td className="cell-mono">{db.class}</td>
                  <td>
                    <span className={`state-pill ${STATUS_COLORS[db.status] || "state-gray"}`}>
                      {db.status}
                    </span>
                  </td>
                  <td>
                    <span className={db.multi_az ? "state-pill state-green" : "state-pill state-gray"}>
                      {db.multi_az ? "Yes" : "No"}
                    </span>
                  </td>
                  <td>{db.storage_gb} GB</td>
                  <td>
                    {db.cpu_percent !== null && db.cpu_percent !== undefined
                      ? <span style={{ color: db.cpu_percent > 80 ? "var(--red)" : "var(--amber)" }}>{db.cpu_percent}%</span>
                      : <span className="metric-na">N/A</span>
                    }
                  </td>
                  <td>{db.connections ?? <span className="metric-na">N/A</span>}</td>
                  <td className="cell-mono" style={{ fontSize: "0.72rem" }}>
                    {db.endpoint ? `${db.endpoint}:${db.port}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
