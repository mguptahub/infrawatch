import { useState, useEffect } from "react";
import { Bell, Clock } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { api } from "../api/client";
import EC2Panel from "../components/EC2Panel";
import EKSPanel from "../components/EKSPanel";
import DatabasesPanel from "../components/DatabasesPanel";
import CostPanel from "../components/CostPanel";
import OpenSearchPanel from "../components/OpenSearchPanel";
import MQPanel from "../components/MQPanel";
import ElastiCachePanel from "../components/ElastiCachePanel";
import SecretsPanel from "../components/SecretsPanel";
import IAMPanel from "../components/IAMPanel";
import SESPanel from "../components/SESPanel";
import LBPanel from "../components/LBPanel";
import WidgetDashboard from "../components/WidgetDashboard";

const ALL_TABS = [
  { id: "dashboard",    label: "My Dashboard" },
  { id: "ec2",        label: "EC2" },
  { id: "elb",         label: "Load Balancers" },
  { id: "eks",         label: "EKS" },
  { id: "databases",   label: "Databases" },
  { id: "elasticache", label: "ElastiCache" },
  { id: "opensearch",  label: "OpenSearch" },
  { id: "mq",          label: "MQ" },
  { id: "ses",         label: "SES" },
  { id: "secrets",     label: "Secrets" },
  { id: "iam",         label: "IAM" },
  { id: "cost",        label: "Cost" },
];


export default function DashboardPage() {
  const { auth, logout, terminate, switchRegion } = useAuth();
  const [contentKey, setContentKey] = useState(0);
  const [switchingRegion, setSwitchingRegion] = useState(false);
  const [confirmTerminate, setConfirmTerminate] = useState(false);
  const [terminating, setTerminating] = useState(false);
  const [awsRegions, setAwsRegions] = useState([]);
  const [alertSummary, setAlertSummary] = useState(null);
  const [bellOpen, setBellOpen] = useState(false);
  const [bellAlarms, setBellAlarms] = useState([]);
  const [bellHealth, setBellHealth] = useState([]);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [timeLeft, setTimeLeft] = useState("");

  // Session countdown timer
  useEffect(() => {
    if (!auth?.expires_at) return;
    function tick() {
      const diff = new Date(auth.expires_at) - Date.now();
      if (diff <= 0) { setTimeLeft("Expired"); return; }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      setTimeLeft(h > 0 ? `${h}h ${m}m left` : `${m}m left`);
    }
    tick();
    const id = setInterval(tick, 60000);
    return () => clearInterval(id);
  }, [auth?.expires_at]);

  useEffect(() => {
    fetch("/api/otp/regions", { credentials: "include" })
      .then((r) => r.json())
      .then(setAwsRegions)
      .catch(() => {}); // non-critical; select will be empty until loaded
  }, []);

  useEffect(() => {
    if (!auth || auth.role === "admin") return;
    let cancelled = false;
    async function fetchSummary() {
      try {
        const s = await api.getAlertsSummary();
        if (!cancelled) setAlertSummary(s);
      } catch (e) { /* non-critical */ }
    }
    fetchSummary();
    const interval = setInterval(fetchSummary, 60000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [auth]);

  // Gate tabs to services approved for this session; "My Dashboard" (widgets) always shown for authenticated users
  const approvedServices = auth?.services || [];
  const serviceTabs = approvedServices.length
    ? ALL_TABS.filter((t) => t.id === "dashboard" || approvedServices.includes(t.id))
    : ALL_TABS;
  const TABS = serviceTabs.length ? serviceTabs : ALL_TABS;

  const [tab, _setTab] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const initialTab = params.get("tab");
    return TABS.find((t) => t.id === initialTab) ? initialTab : TABS[0]?.id;
  });

  function setTab(newTab) {
    _setTab(newTab);
    const url = new URL(window.location);
    url.searchParams.set("tab", newTab);
    window.history.pushState({}, "", url);
  }

  async function handleRegionChange(e) {
    const newRegion = e.target.value;
    setSwitchingRegion(true);
    try {
      await switchRegion(newRegion);
      setContentKey((k) => k + 1); // remounts all panels → fresh fetch
    } finally {
      setSwitchingRegion(false);
    }
  }

  async function handleTerminate() {
    setTerminating(true);
    try {
      await terminate();
    } finally {
      setTerminating(false);
      setConfirmTerminate(false);
    }
  }

  async function handleBellClick() {
    if (!bellOpen) {
      try {
        const [a, h] = await Promise.all([
          api.getAlarms(null, "ALARM"),
          api.getHealthEvents(null, "open"),
        ]);
        setBellAlarms(a);
        setBellHealth(h);
      } catch (e) { /* non-critical */ }
    }
    setBellOpen(!bellOpen);
  }

  useEffect(() => {
    function handleEsc(e) {
      if (e.key === "Escape") { setBellOpen(false); setUserMenuOpen(false); }
    }
    if (bellOpen || userMenuOpen) document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [bellOpen, userMenuOpen]);

  if (!TABS.length) {
    return (
      <div className="dashboard">
        <header className="dash-header">
          <div className="dash-brand">
            <span className="logo-icon">⬡</span>
            <span className="brand-name">InfraWatch</span>
          </div>
          <div className="dash-account">
            <button className="logout-btn" onClick={logout}>Logout</button>
          </div>
        </header>
        <div className="panel-empty" style={{ margin: "3rem auto" }}>
          No services approved for this session.
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <header className="dash-header">
        <div className="dash-brand">
          <span className="logo-icon">⬡</span>
          <span className="brand-name">InfraWatch</span>
        </div>

        <div className="dash-account">
          {/* Region selector */}
          {auth?.role === "keys" && (
            <select
              className="region-select"
              value={auth?.region || ""}
              onChange={handleRegionChange}
              disabled={switchingRegion}
              title="Switch AWS region"
            >
              {awsRegions.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          )}

          {/* Bell icon */}
          {auth?.role !== "admin" && (
            <div className="bell-wrap">
              <button className="bell-btn" onClick={handleBellClick} title="Alerts">
                <Bell size={18} />
                {alertSummary && (alertSummary.alarms.total + alertSummary.health.total) > 0 && (
                  <span className="bell-badge">
                    {alertSummary.alarms.total + alertSummary.health.total}
                  </span>
                )}
              </button>
              {bellOpen && (
                <>
                  <div className="bell-backdrop" onClick={() => setBellOpen(false)} />
                  <div className="bell-dropdown">
                    <div className="bell-dropdown-title">Alerts</div>
                    {bellAlarms.length === 0 && bellHealth.length === 0 && (
                      <div className="bell-empty">No active alerts</div>
                    )}
                    {bellAlarms.length > 0 && (
                      <div className="bell-section">
                        <div className="bell-section-label">CloudWatch Alarms</div>
                        {bellAlarms.slice(0, 10).map((a) => (
                          <button key={a.id} className="bell-item" onClick={() => { setTab(a.service_type); setBellOpen(false); }}>
                            <span className="alert-dot" />
                            <span className="bell-item-text">
                              <strong>{a.alarm_name}</strong>
                              {a.resource_id && <span className="bell-item-sub">{a.resource_id}</span>}
                            </span>
                            <span className="bell-item-service">{a.service_type}</span>
                          </button>
                        ))}
                      </div>
                    )}
                    {bellHealth.length > 0 && (
                      <div className="bell-section">
                        <div className="bell-section-label">Health Events</div>
                        {bellHealth.slice(0, 10).map((h) => (
                          <button key={h.id} className="bell-item" onClick={() => { if (h.service_type) setTab(h.service_type); setBellOpen(false); }}>
                            <span className="alert-dot amber" />
                            <span className="bell-item-text">
                              <strong>{h.title}</strong>
                            </span>
                            <span className="bell-item-service">{h.service_type || "general"}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {/* User icon with dropdown */}
          <div className="user-menu-wrap">
            <button
              className="user-menu-btn"
              onClick={() => { setUserMenuOpen(!userMenuOpen); setBellOpen(false); }}
              title={auth?.name || auth?.email}
            >
              <span className="user-avatar-letter">{(auth?.email || "?")[0].toUpperCase()}</span>
            </button>
            {userMenuOpen && (
              <>
                <div className="user-menu-backdrop" onClick={() => setUserMenuOpen(false)} />
                <div className="user-menu-dropdown">
                  <div className="user-menu-email">{auth?.email}</div>
                  {timeLeft && (
                    <div className="user-menu-timer">
                      <Clock size={13} />
                      <span>{timeLeft}</span>
                    </div>
                  )}
                  {auth?.role === "keys" && !confirmTerminate && (
                    <button
                      className="user-menu-item terminate"
                      onClick={() => setConfirmTerminate(true)}
                    >
                      Terminate Session
                    </button>
                  )}
                  {auth?.role === "keys" && confirmTerminate && (
                    <div className="user-menu-confirm">
                      <span>Destroy session?</span>
                      <div className="user-menu-confirm-btns">
                        <button
                          className="user-menu-item terminate"
                          onClick={handleTerminate}
                          disabled={terminating}
                        >
                          {terminating ? "…" : "Yes, Terminate"}
                        </button>
                        <button
                          className="user-menu-item"
                          onClick={() => setConfirmTerminate(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                  <button className="user-menu-item" onClick={logout}>
                    Logout
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      <nav className="dash-nav">
        {TABS.map((t) => {
          const alarmCount = alertSummary?.alarms?.by_service?.[t.id] || 0;
          const healthCount = alertSummary?.health?.by_service?.[t.id] || 0;
          const hasAlerts = alarmCount + healthCount > 0;
          return (
            <button
              key={t.id}
              className={`nav-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {hasAlerts && <span className="alert-dot tab-alert-dot" />}
            </button>
          );
        })}
      </nav>

      <main key={contentKey} className="dash-content">
        {tab === "dashboard"     && <WidgetDashboard />}
        {tab === "ec2"         && <EC2Panel />}
        {tab === "eks"         && <EKSPanel />}
        {tab === "databases"   && <DatabasesPanel />}
        {tab === "elasticache" && <ElastiCachePanel />}
        {tab === "opensearch"  && <OpenSearchPanel />}
        {tab === "mq"          && <MQPanel />}
        {tab === "elb"         && <LBPanel />}
        {tab === "ses"         && <SESPanel />}
        {tab === "secrets"     && <SecretsPanel />}
        {tab === "iam"         && <IAMPanel />}
        {tab === "cost"        && <CostPanel />}
      </main>
    </div>
  );
}
