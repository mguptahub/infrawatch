import { useState, useEffect } from "react";
import { api } from "../api/client";

const SERVICE_LABELS = {
  ec2: "EC2", elb: "Load Balancers", eks: "EKS", rds: "RDS", elasticache: "ElastiCache",
  opensearch: "OpenSearch", mq: "Amazon MQ", ses: "SES",
  secrets: "Secrets Manager", iam: "IAM", cost: "Cost Explorer",
};

export default function ApprovalPage() {
  const token = new URLSearchParams(window.location.search).get("token");

  const [stage, setStage] = useState("loading"); // loading | details | otp | done | error
  const [requestDetails, setRequestDetails] = useState(null);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [action, setAction] = useState(null); // "approve" | "deny"
  const [denialReason, setDenialReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!token) { setStage("error"); setError("No approval token found in URL"); return; }
    api.getApprovalRequest(token)
      .then((data) => { setRequestDetails(data); setStage("details"); })
      .catch((err) => { setError(err.message); setStage("error"); });
  }, [token]);

  async function handleSendOTP(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.sendApprovalOTP(token, email.trim().toLowerCase());
      setStage("otp");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleApprove(chosenAction) {
    setAction(chosenAction);
    if (chosenAction === "approve") {
      submitAction(chosenAction);
    } else {
      setStage("deny_reason");
    }
  }

  async function submitAction(chosenAction) {
    setLoading(true);
    setError(null);
    try {
      const res = await api.submitApproval(
        token, email.trim().toLowerCase(), code.trim(),
        chosenAction, chosenAction === "deny" ? denialReason : undefined,
      );
      setResult(res);
      setStage("done");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ─── Render ───────────────────────────────────────────────────────────────

  if (stage === "loading") {
    return <PageShell><div className="panel-loading">Loading request…</div></PageShell>;
  }

  if (stage === "error") {
    return <PageShell><div className="panel-error">{error}</div></PageShell>;
  }

  if (stage === "done") {
    const approved = result?.action === "approved";
    return (
      <PageShell>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: "2.5rem", marginBottom: "0.5rem" }}>{approved ? "✅" : "❌"}</div>
          <h2 style={{ color: approved ? "var(--green)" : "var(--red)" }}>
            Request {approved ? "Approved" : "Denied"}
          </h2>
          <p style={{ color: "var(--text-muted)", marginTop: "0.5rem" }}>
            {approved
              ? `${requestDetails?.requester_name} will receive an email with login instructions.`
              : "The requester has been notified."}
          </p>
        </div>
      </PageShell>
    );
  }

  if (stage === "details") {
    return (
      <PageShell>
        <h2 style={{ marginBottom: "1.25rem" }}>Review Access Request</h2>
        <div className="drawer-meta-grid" style={{ marginBottom: "1.25rem" }}>
          <div className="drawer-meta-item">
            <span className="drawer-meta-key">Requested by</span>
            <span className="drawer-meta-val">{requestDetails.requester_name} ({requestDetails.requester_email})</span>
          </div>
          <div className="drawer-meta-item">
            <span className="drawer-meta-key">Duration</span>
            <span className="drawer-meta-val">{requestDetails.duration_hours}h</span>
          </div>
          <div className="drawer-meta-item" style={{ gridColumn: "1 / -1" }}>
            <span className="drawer-meta-key">Services</span>
            <span className="drawer-meta-val" style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
              {requestDetails.services.map((s) => (
                <span key={s} className="state-pill state-blue" style={{ fontSize: "0.72rem" }}>
                  {SERVICE_LABELS[s] || s}
                </span>
              ))}
            </span>
          </div>
        </div>
        <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1.25rem" }}>
          Enter your email to verify your identity before approving or denying.
        </p>
        <form onSubmit={handleSendOTP} className="login-form" style={{ gap: "0.75rem" }}>
          <div className="field">
            <label>Your Email</label>
            <input
              type="email"
              placeholder="manager@company.com"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setError(null); }}
              required
            />
          </div>
          {error && (
            <div className="login-error">
              {error === "This email is not authorised to approve this request"
                ? "Only the assigned manager or admin can approve this request. Check your email and try again."
                : error}
            </div>
          )}
          <button type="submit" className="login-btn" disabled={loading || !email.trim()}>
            {loading ? "Sending code…" : "Send Verification Code"}
          </button>
        </form>
      </PageShell>
    );
  }

  if (stage === "otp") {
    return (
      <PageShell>
        <h2 style={{ marginBottom: "0.5rem" }}>Enter Verification Code</h2>
        <p className="otp-hint" style={{ marginBottom: "1.25rem" }}>
          Code sent to <strong>{email}</strong>
        </p>
        <div className="field" style={{ marginBottom: "1.25rem" }}>
          <input
            type="text"
            inputMode="numeric"
            placeholder="000000"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            className="otp-input"
            autoFocus
          />
        </div>
        {error && <div className="login-error" style={{ marginBottom: "1rem" }}>{error}</div>}
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            className="login-btn"
            style={{ background: "var(--green)", flex: 1 }}
            onClick={() => handleApprove("approve")}
            disabled={loading || code.length !== 6}
          >
            {loading && action === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            className="login-btn"
            style={{ background: "var(--red)", flex: 1 }}
            onClick={() => handleApprove("deny")}
            disabled={loading || code.length !== 6}
          >
            Deny
          </button>
        </div>
      </PageShell>
    );
  }

  if (stage === "deny_reason") {
    return (
      <PageShell>
        <h2 style={{ marginBottom: "0.75rem" }}>Deny Request</h2>
        <div className="field" style={{ marginBottom: "1rem" }}>
          <label>Reason (optional)</label>
          <textarea
            className="ses-bulk-textarea"
            rows={3}
            placeholder="Let the requester know why..."
            value={denialReason}
            onChange={(e) => setDenialReason(e.target.value)}
          />
        </div>
        {error && <div className="login-error" style={{ marginBottom: "1rem" }}>{error}</div>}
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            className="login-btn"
            style={{ background: "var(--red)", flex: 1 }}
            onClick={() => submitAction("deny")}
            disabled={loading}
          >
            {loading ? "Denying…" : "Confirm Deny"}
          </button>
          <button
            className="login-secondary-btn"
            style={{ flex: 1 }}
            onClick={() => setStage("otp")}
          >
            Cancel
          </button>
        </div>
      </PageShell>
    );
  }

  return null;
}

function PageShell({ children }) {
  return (
    <div className="login-page">
      <div className="login-card" style={{ maxWidth: 500 }}>
        <div className="login-logo" style={{ marginBottom: "1rem" }}>
          <span className="logo-icon">⬡</span>
          <h1>InfraWatch</h1>
        </div>
        {children}
      </div>
    </div>
  );
}
