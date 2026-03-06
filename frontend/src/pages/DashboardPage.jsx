import { useState, useEffect } from "react";
import { useAuth } from "../hooks/useAuth";
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

const ALL_TABS = [
  { id: "ec2",         label: "EC2" },
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

  useEffect(() => {
    fetch("/api/otp/regions", { credentials: "include" })
      .then((r) => r.json())
      .then(setAwsRegions)
      .catch(() => {}); // non-critical; select will be empty until loaded
  }, []);

  // Gate tabs to services approved for this session
  const approvedServices = auth?.services || [];
  const TABS = approvedServices.length
    ? ALL_TABS.filter((t) => approvedServices.includes(t.id))
    : ALL_TABS;

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

  if (!TABS.length) {
    return (
      <div className="dashboard">
        <header className="dash-header">
          <div className="dash-brand">
            <span className="logo-icon">⬡</span>
            <span className="brand-name">Cloud Dashboard</span>
          </div>
          <div className="dash-account">
            <button className="logout-btn" onClick={logout}>Disconnect</button>
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
          <span className="brand-name">Cloud Dashboard</span>
        </div>

        <div className="dash-account">
          <span className="account-arn">{auth?.name || auth?.email}</span>

          {/* Region selector — only for users with AWS credentials */}
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

          {/* Terminate session (destroys STS creds) */}
          {auth?.role === "keys" && !confirmTerminate && (
            <button
              className="logout-btn terminate-btn"
              onClick={() => setConfirmTerminate(true)}
              title="Destroy STS credentials — requires new approval to log back in"
            >
              Terminate
            </button>
          )}
          {auth?.role === "keys" && confirmTerminate && (
            <div className="terminate-confirm">
              <span>Destroy session?</span>
              <button
                className="logout-btn"
                style={{ borderColor: "var(--red)", color: "var(--red)" }}
                onClick={handleTerminate}
                disabled={terminating}
              >
                {terminating ? "…" : "Yes"}
              </button>
              <button className="logout-btn" onClick={() => setConfirmTerminate(false)}>
                No
              </button>
            </div>
          )}

          <button className="logout-btn" onClick={logout}>Disconnect</button>
        </div>
      </header>

      <nav className="dash-nav">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`nav-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main key={contentKey} className="dash-content">
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
