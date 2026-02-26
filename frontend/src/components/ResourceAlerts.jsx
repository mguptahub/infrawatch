import { useState, useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { api } from "../api/client";

export default function ResourceAlerts({ resourceId }) {
  const [alarms, setAlarms] = useState([]);

  useEffect(() => {
    if (!resourceId) return;
    api.getResourceAlarms(resourceId).then(setAlarms).catch(() => {});
  }, [resourceId]);

  if (alarms.length === 0) return null;

  const inAlarm = alarms.filter(a => a.state === "ALARM");
  const other = alarms.filter(a => a.state !== "ALARM");

  return (
    <div className="resource-alerts">
      <div className="resource-alerts-title">
        <AlertTriangle size={14} />
        {inAlarm.length} active alarm{inAlarm.length !== 1 ? "s" : ""}
      </div>
      {inAlarm.map(a => (
        <div key={a.id} className="resource-alert-row">
          <span className="alert-dot" />
          <span className="resource-alert-name">{a.alarm_name}</span>
          <span className="resource-alert-reason">{a.state_reason}</span>
        </div>
      ))}
      {other.length > 0 && (
        <div className="resource-alerts-other">
          {other.length} other alarm{other.length !== 1 ? "s" : ""} ({other.map(a => a.state).join(", ")})
        </div>
      )}
    </div>
  );
}
