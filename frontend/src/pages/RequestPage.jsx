import { useState, useRef, useEffect } from "react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const SERVICE_LABELS = {
  ec2: "EC2", elb: "Load Balancers", eks: "EKS", databases: "Databases", elasticache: "ElastiCache",
  opensearch: "OpenSearch", mq: "Amazon MQ", ses: "SES",
  secrets: "Secrets Manager", iam: "IAM", cost: "Cost Explorer",
};

export default function RequestPage({ initialEmail = "", onBack }) {
  const { loginWithOTP } = useAuth();
  const [email, setEmail] = useState(initialEmail);
  const [services, setServices] = useState([]);
  const [duration, setDuration] = useState(4);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [submission, setSubmission] = useState(null);
  const [otpCode, setOtpCode] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [otpLoading, setOtpLoading] = useState(false);
  const [otpError, setOtpError] = useState(null);

  // Allowed services + max duration fetched from backend once email is entered
  const [allowedServices, setAllowedServices] = useState(null); // null = not yet fetched
  const [maxDuration, setMaxDuration] = useState(12);
  const configEmailRef = useRef(""); // tracks which email was last fetched

  // Auto-fetch config when the page opens with a prefilled email (redirect from login)
  useEffect(() => {
    if (initialEmail) fetchConfig(initialEmail);
  }, []); // eslint-disable-line

  const [regStage, setRegStage] = useState(null); // null | "verify"
  const [regOtp, setRegOtp] = useState("");
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState(null);
  const [pendingServices, setPendingServices] = useState([]);
  const [pendingDuration, setPendingDuration] = useState(4);

  async function fetchConfig(rawEmail) {
    const trimmed = rawEmail.trim().toLowerCase();
    if (!trimmed || trimmed === configEmailRef.current) return;
    configEmailRef.current = trimmed;
    try {
      const cfg = await api.getRequestConfig(trimmed);
      setAllowedServices(cfg.allowed_services);
      setMaxDuration(cfg.max_duration_hours);
      // Drop any already-selected services that are no longer allowed
      setServices((prev) => prev.filter((s) => cfg.allowed_services.includes(s)));
      // Drop duration if it now exceeds the new max
      setDuration((prev) => (prev <= cfg.max_duration_hours ? prev : 1));
    } catch {
      // Unknown email — clear constraints so the form stays usable;
      // the submit call will return the proper error.
      setAllowedServices(null);
      setMaxDuration(12);
    }
  }

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
      const normalizedEmail = email.trim().toLowerCase();
      const result = await api.submitRequest(normalizedEmail, services, duration);
      if (result.status === "verification_required") {
        setEmail(normalizedEmail);
        setPendingServices(services);
        setPendingDuration(duration);
        setRegStage("verify");
      } else {
        setEmail(normalizedEmail);
        setSubmission(result);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyRegistration(e) {
    e.preventDefault();
    setRegLoading(true);
    setRegError(null);
    try {
      const result = await api.verifyAndSubmitRequest(
        email, regOtp.trim(), pendingServices, pendingDuration
      );
      setRegStage(null);
      setSubmission(result);
    } catch (err) {
      setRegError(err.message);
      setRegOtp("");
    } finally {
      setRegLoading(false);
    }
  }

  async function handleSendOTPNow() {
    setOtpLoading(true);
    setOtpError(null);
    try {
      await api.requestOTP(email);
      setOtpSent(true);
    } catch (err) {
      setOtpError(err.message);
    } finally {
      setOtpLoading(false);
    }
  }

  async function handleLoginNow(e) {
    e.preventDefault();
    setOtpLoading(true);
    setOtpError(null);
    try {
      await loginWithOTP(email, otpCode.trim());
    } catch (err) {
      setOtpError(err.message);
      setOtpCode("");
    } finally {
      setOtpLoading(false);
    }
  }

  if (regStage === "verify") {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-logo">
            <span className="logo-icon">⬡</span>
            <h1>Verify Your Email</h1>
            <p>We sent a 6-digit code to confirm your address.</p>
          </div>
          <form onSubmit={handleVerifyRegistration} className="login-form">
            <p className="otp-hint">
              Check your inbox at <strong>{email}</strong>
            </p>
            <div className="field">
              <label>Verification Code</label>
              <input
                type="text"
                inputMode="numeric"
                placeholder="000000"
                value={regOtp}
                onChange={(e) => setRegOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="otp-input"
                autoFocus
                required
              />
            </div>
            {regError && <div className="login-error">{regError}</div>}
            <button type="submit" className="login-btn" disabled={regLoading || regOtp.length !== 6}>
              {regLoading ? "Verifying…" : "Confirm & Submit Request"}
            </button>
            <button
              type="button"
              className="login-secondary-btn"
              onClick={() => { setRegStage(null); setRegOtp(""); setRegError(null); }}
            >
              ← Back
            </button>
          </form>
          <p className="login-footer">Code expires in 10 minutes.</p>
        </div>
      </div>
    );
  }

  if (submission) {
    const isAutoApproved = !!submission.auto_approved;
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-logo">
            <span className="logo-icon" style={{ color: "var(--green)" }}>✓</span>
            <h1>{isAutoApproved ? "Access Approved" : "Request Submitted"}</h1>
            <p>
              {isAutoApproved
                ? "You are pre-approved. You can log in now or come back later."
                : "Your manager has been notified and will review your request shortly."}
            </p>
          </div>

          {!isAutoApproved && (
            <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", textAlign: "center", marginTop: "1rem" }}>
              You'll receive an email when your request is approved.
            </p>
          )}

          {isAutoApproved && !otpSent && (
            <button className="login-btn" style={{ marginTop: "1.25rem", marginRight: "1.25rem" }} onClick={handleSendOTPNow} disabled={otpLoading}>
              {otpLoading ? "Sending code…" : "Get OTP & Login Now"}
            </button>
          )}

          {isAutoApproved && otpSent && (
            <form onSubmit={handleLoginNow} className="login-form" style={{ marginTop: "1rem" }}>
              <p className="otp-hint">
                A 6-digit code was sent to <strong>{email}</strong>
              </p>
              <div className="field">
                <label>Verification Code</label>
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="000000"
                  value={otpCode}
                  onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  className="otp-input"
                  autoFocus
                  required
                />
              </div>
              {otpError && <div className="login-error">{otpError}</div>}
              <button type="submit" className="login-btn" disabled={otpLoading || otpCode.length !== 6}>
                {otpLoading ? "Verifying…" : "Log In"}
              </button>
            </form>
          )}

          {otpError && !otpSent && <div className="login-error" style={{ marginTop: "0.75rem" }}>{otpError}</div>}

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
              onChange={(e) => {
                setEmail(e.target.value);
                configEmailRef.current = ""; // invalidate cache so blur re-fetches
              }}
              onBlur={(e) => fetchConfig(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label>Services Required</label>
            <div className="service-grid">
              {Object.entries(SERVICE_LABELS)
                .filter(([key]) => !allowedServices || allowedServices.includes(key))
                .map(([key, label]) => (
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
              {[1, 2, 4, 8].filter((h) => h <= maxDuration).map((h) => (
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
