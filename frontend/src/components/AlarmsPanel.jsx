import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const STATE_MAP = {
  ALARM: { cls: "state-red", label: "ALARM" },
  OK: { cls: "state-green", label: "OK" },
  INSUFFICIENT_DATA: { cls: "state-gray", label: "NO DATA" },
};

export default function AlarmsPanel() {
  const fetcher = useCallback((force = false) => api.getAlarms(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  if (loading) return <div className="panel-loading">Loading alarms…</div>;
  if (error) return <div className="panel-error">Alarms: {error}</div>;

  const alarms = data?.alarms || [];
  const alarmCount = data?.alarm_count ?? 0;

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>
          CloudWatch Alarms{" "}
          {alarmCount > 0 && (
            <span className="alarm-badge">{alarmCount} ALARM{alarmCount !== 1 ? "S" : ""}</span>
          )}
        </h2>
        <div className="panel-header-actions">
          <span className="count-badge">{alarms.length} total</span>
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>
      {alarms.length === 0 ? (
        <div className="panel-empty">No alarms configured</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Alarm</th>
                <th>State</th>
                <th>Metric</th>
                <th>Namespace</th>
                <th>Last Updated</th>
              </tr>
            </thead>
            <tbody>
              {alarms.map((a) => {
                const s = STATE_MAP[a.state] || { cls: "state-gray", label: a.state };
                const updated = a.updated
                  ? new Date(a.updated).toLocaleString()
                  : "—";
                return (
                  <tr key={a.name}>
                    <td className="cell-bold" title={a.description}>{a.name}</td>
                    <td>
                      <span className={`state-pill ${s.cls}`}>{s.label}</span>
                    </td>
                    <td className="cell-mono">{a.metric}</td>
                    <td className="cell-mono" style={{ fontSize: "0.75rem" }}>{a.namespace}</td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>{updated}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
