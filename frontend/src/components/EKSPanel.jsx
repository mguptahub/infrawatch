import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

export default function EKSPanel() {
  const fetcher = useCallback((force = false) => api.getEKS(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading EKS…</div>;
  if (error) return <div className="panel-error">EKS: {error}</div>;

  const clusters = data?.clusters || [];

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>EKS Clusters <span className="count-badge">{clusters.length}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>
      {clusters.length === 0 ? (
        <div className="panel-empty">No EKS clusters found</div>
      ) : (
        <div className="eks-grid">
          {clusters.map((c) => (
            <div key={c.arn} className="eks-cluster-card">
              <div className="eks-cluster-header">
                <span className="cell-bold">{c.name}</span>
                <span className={`state-pill ${c.status === "ACTIVE" ? "state-green" : "state-amber"}`}>
                  {c.status}
                </span>
                <span className="version-badge">v{c.version}</span>
              </div>
              <div className="eks-details">
                <div className="eks-field">
                  <span className="field-lbl">Endpoint:</span>
                  <span className="field-val cell-mono truncate" title={c.endpoint}>{c.endpoint}</span>
                </div>
                <div className="eks-field">
                  <span className="field-lbl">Created:</span>
                  <span className="field-val">{new Date(c.created_at).toLocaleDateString()}</span>
                </div>
              </div>

              {c.nodegroups.length > 0 && (
                <div className="eks-nodegroups">
                  <p className="sub-label">Node Groups</p>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Instance Types</th>
                        <th>Scaling (D/M/M)</th>
                        <th>Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.nodegroups.map((ng) => (
                        <tr key={ng.name}>
                          <td className="cell-bold">{ng.name}</td>
                          <td>
                            <span className={`state-pill ${ng.status === "ACTIVE" ? "state-green" : "state-amber"}`}>
                              {ng.status}
                            </span>
                          </td>
                          <td>{ng.instance_types.join(", ")}</td>
                          <td>
                            {ng.scaling_config.desiredSize} / {ng.scaling_config.minSize} / {ng.scaling_config.maxSize}
                          </td>
                          <td>{ng.capacity_type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
