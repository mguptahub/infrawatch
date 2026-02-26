import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const BROKER_STATE_COLORS = {
  RUNNING: "state-green",
  REBOOT_IN_PROGRESS: "state-amber",
  CREATION_IN_PROGRESS: "state-amber",
  DELETION_IN_PROGRESS: "state-red",
  CRITICAL_ACTION_REQUIRED: "state-red",
};

export default function MQPanel() {
  const fetcher = useCallback((force = false) => api.getMQ(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading Amazon MQ…</div>;
  if (error) return <div className="panel-error">Amazon MQ: {error}</div>;

  const brokers = data?.brokers || [];

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
      {brokers.length === 0 ? (
        <div className="panel-empty">No MQ brokers found</div>
      ) : (
        <div className="ecs-grid">
          {brokers.map((b) => (
            <div key={b.id} className="ecs-cluster-card">
              <div className="ecs-cluster-header">
                <div>
                  <span className="cell-bold">{b.name}</span>
                  <span className="cell-mono" style={{ marginLeft: "0.75rem", color: "var(--text-muted)", fontSize: "0.75rem" }}>
                    {b.engine_type} {b.engine_version}
                  </span>
                </div>
                <span className={`state-pill ${BROKER_STATE_COLORS[b.state] || "state-gray"}`}>
                  {b.state}
                </span>
              </div>

              <div className="mq-meta-row">
                <span className="mq-meta-item">
                  <span className="stat-lbl">Type</span>
                  <span className="cell-mono">{b.instance_type}</span>
                </span>
                <span className="mq-meta-item">
                  <span className="stat-lbl">Mode</span>
                  <span>{b.deployment_mode}</span>
                </span>
                <span className="mq-meta-item">
                  <span className="stat-lbl">Public</span>
                  <span className={`state-pill ${b.publicly_accessible ? "state-amber" : "state-green"}`}>
                    {b.publicly_accessible ? "Yes" : "No"}
                  </span>
                </span>
                <span className="mq-meta-item">
                  <span className="stat-lbl">Auto Upgrade</span>
                  <span className={`state-pill ${b.auto_minor_upgrade ? "state-green" : "state-gray"}`}>
                    {b.auto_minor_upgrade ? "On" : "Off"}
                  </span>
                </span>
              </div>

              <div className="ecs-stats" style={{ marginTop: "0.75rem" }}>
                <div className="ecs-stat">
                  <span className="stat-val">{b.cpu_percent !== null && b.cpu_percent !== undefined ? `${b.cpu_percent}%` : "—"}</span>
                  <span className="stat-lbl">CPU</span>
                </div>
                <div className="ecs-stat">
                  <span className="stat-val">{b.heap_usage !== null && b.heap_usage !== undefined ? `${b.heap_usage}%` : "—"}</span>
                  <span className="stat-lbl">Heap</span>
                </div>
                <div className="ecs-stat">
                  <span className="stat-val">{b.total_connections ?? "—"}</span>
                  <span className="stat-lbl">Connections</span>
                </div>
                <div className="ecs-stat">
                  <span className="stat-val">{b.total_queues ?? "—"}</span>
                  <span className="stat-lbl">Queues</span>
                </div>
              </div>

              {b.endpoints?.length > 0 && (
                <div style={{ marginTop: "0.75rem" }}>
                  <p className="services-label">Endpoints</p>
                  {b.endpoints.map((ep, i) => (
                    <p key={i} className="cell-mono" style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>{ep}</p>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
