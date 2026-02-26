import { useState, useEffect } from "react";
import { api } from "../api/client";

const SERVICE_LABELS = {
  ec2: "EC2", elb: "Load Balancers", eks: "EKS", databases: "Databases", elasticache: "ElastiCache",
  opensearch: "OpenSearch", mq: "Amazon MQ", ses: "SES",
  secrets: "Secrets Manager", cost: "Cost Explorer", alarms: "CloudWatch Alarms",
};

export default function RequestPage({ initialEmail = "", onBack }) {
  const [email, setEmail] = useState(initialEmail);
  const [services, setServices] = useState([]);
  const [duration, setDuration] = useState(4);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [submitted, setSubmitted] = useState(false);

  function toggleService(s) {
    setServices((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!services.length) { setError("Select at least one service"); return; }
    setLoading(true);
    setError(null);
    try {
      await api.submitRequest(email.trim().toLowerCase(), services, duration);
      setSubmitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-logo">
            <span className="logo-icon" style={{ color: "var(--green)" }}>✓</span>
            <h1>Request Submitted</h1>
            <p>Your manager has been notified and will review your request shortly.</p>
          </div>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", textAlign: "center", marginTop: "1rem" }}>
            You'll receive an email when your request is approved.
          </p>
          <button className="login-btn" style={{ marginTop: "1.5rem" }} onClick={onBack}>
            Back to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card" style={{ maxWidth: 480 }}>
        <div className="login-logo">
          <span className="logo-icon">⬡</span>
          <h1>Request Access</h1>
          <p>Submit a request for temporary AWS access</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="field">
            <label>Work Email</label>
            <input
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label>Services Required</label>
            <div className="service-grid">
              {Object.entries(SERVICE_LABELS).map(([key, label]) => (
                <label key={key} className={`service-chip ${services.includes(key) ? "selected" : ""}`}>
                  <input
                    type="checkbox"
                    checked={services.includes(key)}
                    onChange={() => toggleService(key)}
                    style={{ display: "none" }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          <div className="field">
            <label>Duration (hours)</label>
            <div className="duration-row">
              {[1, 2, 4, 8].map((h) => (
                <button
                  key={h}
                  type="button"
                  className={`duration-chip ${duration === h ? "selected" : ""}`}
                  onClick={() => setDuration(h)}
                >
                  {h}h
                </button>
              ))}
            </div>
          </div>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-btn" disabled={loading || !email.trim() || !services.length}>
            {loading ? "Submitting…" : "Submit Request"}
          </button>
          <button type="button" className="login-secondary-btn" onClick={onBack}>
            ← Back to Login
          </button>
        </form>
      </div>
    </div>
  );
}
