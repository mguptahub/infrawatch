import { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { api } from "../api/client";

// Two stages: enter email → enter OTP code
export default function LoginPage({ onRequestAccess }) {
  const { loginWithOTP } = useAuth();
  const [stage, setStage] = useState("email"); // "email" | "otp"
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleEmailSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.requestOTP(email.trim().toLowerCase());
      setStage("otp");
    } catch (err) {
      if (err.message === "no_active_access") {
        setError(null);
        onRequestAccess(email.trim().toLowerCase());
      } else if (err.message === "not_registered") {
        setError(null);
        onRequestAccess(email.trim().toLowerCase());
      } else {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleOTPSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await loginWithOTP(email, code.trim());
    } catch (err) {
      setError(err.message);
      setCode("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo">
          <span className="logo-icon">⬡</span>
          <h1>Cloud Dashboard</h1>
          <p>Infrastructure visibility dashboard</p>
        </div>

        {stage === "email" ? (
          <form onSubmit={handleEmailSubmit} className="login-form">
            <div className="field">
              <label>Work Email</label>
              <input
                type="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                required
              />
            </div>
            {error && <div className="login-error">{error}</div>}
            <button type="submit" className="login-btn" disabled={loading || !email.trim()}>
              {loading ? "Sending code…" : "Continue"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleOTPSubmit} className="login-form">
            <p className="otp-hint">
              A 6-digit code was sent to <strong>{email}</strong>
            </p>
            <div className="field">
              <label>Verification Code</label>
              <input
                type="text"
                inputMode="numeric"
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="otp-input"
                autoFocus
                required
              />
            </div>
            {error && <div className="login-error">{error}</div>}
            <button type="submit" className="login-btn" disabled={loading || code.length !== 6}>
              {loading ? "Verifying…" : "Log In"}
            </button>
            <button
              type="button"
              className="login-secondary-btn"
              onClick={() => { setStage("email"); setCode(""); setError(null); }}
            >
              ← Back
            </button>
          </form>
        )}

        <p className="login-footer">
          No account? Enter your work email to request access.
        </p>
      </div>
    </div>
  );
}
