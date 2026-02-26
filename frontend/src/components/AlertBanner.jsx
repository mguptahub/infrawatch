import React, { useState, useEffect } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Activity, Shield } from "lucide-react";
import { api } from "../api/client";

function timeAgo(isoStr) {
  if (!isoStr) return "";
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function AlertBanner({ serviceType, onAlarmsLoaded }) {
  const [alarms, setAlarms] = useState([]);
  const [healthEvents, setHealthEvents] = useState([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function fetchAlerts() {
      try {
        const [alarmRes, healthRes] = await Promise.all([
          api.getAlarms(serviceType, "ALARM"),
          api.getHealthEvents(serviceType, "open"),
        ]);
        if (cancelled) return;
        const alarmList = alarmRes.alarms || [];
        const healthList = healthRes.events || [];
        setAlarms(alarmList);
        setHealthEvents(healthList);
        if (onAlarmsLoaded) onAlarmsLoaded(alarmList);
      } catch (_) {
        // silently ignore — banner is non-critical
      }
    }

    fetchAlerts();
    const interval = setInterval(fetchAlerts, 60000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [serviceType, onAlarmsLoaded]);

  if (alarms.length === 0 && healthEvents.length === 0) return null;

  const parts = [];
  if (alarms.length > 0) {
    parts.push(`${alarms.length} alarm${alarms.length !== 1 ? "s" : ""} in ALARM state`);
  }
  if (healthEvents.length > 0) {
    parts.push(`${healthEvents.length} health event${healthEvents.length !== 1 ? "s" : ""}`);
  }
  const summary = parts.join(" \u00b7 ");

  return (
    <div className="alert-banner">
      <button
        className="alert-banner-header"
        onClick={() => setExpanded((e) => !e)}
      >
        <AlertTriangle size={16} className="alert-banner-icon" />
        <span className="alert-banner-summary">{summary}</span>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>

      {expanded && (
        <div className="alert-banner-body">
          {alarms.map((a, i) => (
            <div key={`alarm-${i}`} className="alert-banner-item">
              <Activity size={14} className="alert-item-icon alarm" />
              <span className="alert-item-name">{a.alarm_name}</span>
              {a.resource_id && (
                <span className="alert-item-resource">{a.resource_id}</span>
              )}
              <span className="alert-item-time">{timeAgo(a.state_updated_at)}</span>
            </div>
          ))}
          {healthEvents.map((h, i) => (
            <div key={`health-${i}`} className="alert-banner-item">
              <Shield size={14} className="alert-item-icon health" />
              <span className="alert-item-name">{h.title}</span>
              <span
                className={`alert-item-badge ${
                  h.type_code === "scheduledChange" ? "maintenance" : "issue"
                }`}
              >
                {h.type_code === "scheduledChange" ? "Maintenance" : "Issue"}
              </span>
              <span className="alert-item-time">{timeAgo(h.start_time)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
