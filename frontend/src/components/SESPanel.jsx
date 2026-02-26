import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Copy, Check, X } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

function CopyButton({ text, size = 13 }) {
  const [copied, setCopied] = useState(false);
  function handleCopy(e) {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button className={`copy-btn ${copied ? "copied" : ""}`} onClick={handleCopy} title={copied ? "Copied!" : "Copy"}>
      {copied ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}

// ─── API helpers (not in shared client since these are POST actions) ──────────
const BASE = process.env.REACT_APP_API_URL || "";
async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}
async function getJson(path) {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatBox({ label, value, sub, color }) {
  return (
    <div className="ses-stat-box">
      <span className="ses-stat-val" style={{ color: color || "var(--text)" }}>
        {value ?? "—"}
      </span>
      <span className="ses-stat-label">{label}</span>
      {sub && <span className="ses-stat-sub">{sub}</span>}
    </div>
  );
}

function ReasonBadge({ reason }) {
  const color = reason === "BOUNCE" ? "state-amber" : reason === "COMPLAINT" ? "state-red" : "state-gray";
  return <span className={`state-pill ${color}`}>{reason}</span>;
}

function RelTime({ iso }) {
  if (!iso) return <span className="metric-na">—</span>;
  const d = new Date(iso);
  const diff = Math.floor((Date.now() - d) / 1000);
  const label =
    diff < 3600 ? `${Math.floor(diff / 60)}m ago`
    : diff < 86400 ? `${Math.floor(diff / 3600)}h ago`
    : `${Math.floor(diff / 86400)}d ago`;
  return <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }} title={d.toLocaleString()}>{label}</span>;
}

// ─── Suppression Lookup Tool ──────────────────────────────────────────────────
function SuppressionLookup() {
  const [email, setEmail] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [removing, setRemoving] = useState(false);
  const [removed, setRemoved] = useState(false);

  async function handleLookup(e) {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setRemoved(false);
    try {
      const data = await post("/api/ses/suppression/lookup", { email: email.trim() });
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleRemove() {
    if (!result?.email) return;
    setRemoving(true);
    try {
      await post("/api/ses/suppression/remove", { emails: [result.email] });
      setRemoved(true);
      setResult((r) => ({ ...r, found: false }));
    } catch (err) {
      setError(err.message);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="ses-lookup-box">
      <p className="ses-section-title">🔍 Suppression Lookup &amp; Removal</p>
      <form onSubmit={handleLookup} className="ses-lookup-form">
        <input
          type="email"
          className="ses-lookup-input"
          placeholder="user@example.com"
          value={email}
          onChange={(e) => { setEmail(e.target.value); setResult(null); setRemoved(false); }}
        />
        <button type="submit" className="ses-lookup-btn" disabled={loading || !email.trim()}>
          {loading ? "Checking…" : "Look Up"}
        </button>
      </form>

      {error && <div className="ses-lookup-error">{error}</div>}

      {result && (
        <div className={`ses-lookup-result ${result.found ? "ses-result-found" : "ses-result-clear"}`}>
          {result.found ? (
            <>
              <div className="ses-result-header">
                <span className="ses-result-icon">⚠</span>
                <strong>{result.email}</strong> is on the suppression list
              </div>
              <div className="ses-result-meta">
                <span>Reason: <ReasonBadge reason={result.reason} /></span>
                <span>Suppressed: <RelTime iso={result.suppressed_at} /></span>
                {result.feedback_id && <span className="cell-mono" style={{ fontSize: "0.72rem" }}>Feedback ID: {result.feedback_id}</span>}
              </div>
              {removed ? (
                <div className="ses-removed-confirm">✓ Removed from suppression list</div>
              ) : (
                <button
                  className="ses-remove-btn"
                  onClick={handleRemove}
                  disabled={removing}
                >
                  {removing ? "Removing…" : "Remove from Suppression List"}
                </button>
              )}
            </>
          ) : (
            <div className="ses-result-header">
              <span className="ses-result-icon ses-icon-ok">✓</span>
              <strong>{result.email}</strong>
              {removed ? " was successfully removed from the suppression list" : " is NOT on the suppression list"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Bulk Remove Tool ─────────────────────────────────────────────────────────
function BulkRemove({ onRemoved }) {
  const [input, setInput] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const emails = input
    .split(/[\n,;]+/)
    .map((e) => e.trim().toLowerCase())
    .filter((e) => e.includes("@"));

  async function handleBulkRemove() {
    if (!emails.length) return;
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const data = await post("/api/ses/suppression/remove", { emails });
      setResults(data);
      if (data.removed_count > 0 && onRemoved) onRemoved();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="ses-lookup-box">
      <p className="ses-section-title">🗑 Bulk Remove from Suppression List</p>
      <p className="ses-helper-text">Paste emails separated by newlines, commas, or semicolons.</p>
      <textarea
        className="ses-bulk-textarea"
        placeholder={"user1@example.com\nuser2@example.com\nuser3@example.com"}
        value={input}
        onChange={(e) => { setInput(e.target.value); setResults(null); }}
        rows={5}
      />
      <div className="ses-bulk-footer">
        <span className="ses-email-count">
          {emails.length > 0 ? `${emails.length} email${emails.length !== 1 ? "s" : ""} detected` : "No valid emails yet"}
        </span>
        <button
          className="ses-remove-btn"
          onClick={handleBulkRemove}
          disabled={loading || emails.length === 0}
        >
          {loading ? "Removing…" : `Remove ${emails.length > 0 ? emails.length : ""} Email${emails.length !== 1 ? "s" : ""}`}
        </button>
      </div>

      {error && <div className="ses-lookup-error">{error}</div>}

      {results && (
        <div className="ses-bulk-results">
          <div className="ses-bulk-summary">
            <span style={{ color: "var(--green)" }}>✓ {results.removed_count} removed</span>
            {results.failed_count > 0 && (
              <span style={{ color: "var(--red)", marginLeft: "1rem" }}>✗ {results.failed_count} failed</span>
            )}
          </div>
          <div className="ses-bulk-detail">
            {results.results.map((r) => (
              <div key={r.email} className={`ses-bulk-row ${r.removed ? "ses-bulk-ok" : "ses-bulk-fail"}`}>
                <span>{r.removed ? "✓" : "✗"}</span>
                <span className="cell-mono">{r.email}</span>
                {r.error && <span className="ses-bulk-err">{r.error}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Suppression List Browser ─────────────────────────────────────────────────
function SuppressionBrowser() {
  const [filter, setFilter] = useState("");
  const [reasonFilter, setReasonFilter] = useState("ALL");
  const [page, setPage] = useState(null); // next_token
  const [entries, setEntries] = useState([]);
  const [nextToken, setNextToken] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [removing, setRemoving] = useState(false);
  const [removeMsg, setRemoveMsg] = useState(null);

  async function loadPage(token = null, reset = false) {
    setLoading(true);
    setRemoveMsg(null);
    try {
      const params = new URLSearchParams({ page_size: 100 });
      if (token) params.set("next_token", token);
      if (reasonFilter !== "ALL") params.set("reason", reasonFilter);
      const data = await getJson(`/api/ses/suppression?${params}`);
      setEntries((prev) => reset ? data.entries : [...prev, ...data.entries]);
      setNextToken(data.next_token || null);
      setLoaded(true);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  function handleLoad() {
    setEntries([]);
    setSelected(new Set());
    loadPage(null, true);
  }

  const displayed = entries.filter((e) =>
    !filter || e.email.toLowerCase().includes(filter.toLowerCase())
  );

  function toggleSelect(email) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(email) ? next.delete(email) : next.add(email);
      return next;
    });
  }

  function toggleAll() {
    if (selected.size === displayed.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(displayed.map((e) => e.email)));
    }
  }

  async function removeSelected() {
    if (!selected.size) return;
    setRemoving(true);
    setRemoveMsg(null);
    try {
      const data = await post("/api/ses/suppression/remove", { emails: [...selected] });
      setEntries((prev) => prev.filter((e) => !selected.has(e.email)));
      setSelected(new Set());
      setRemoveMsg(`✓ Removed ${data.removed_count} address${data.removed_count !== 1 ? "es" : ""}`);
    } catch (err) {
      setRemoveMsg(`Error: ${err.message}`);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="ses-browser">
      <p className="ses-section-title">📋 Suppression List Browser</p>

      <div className="ses-browser-controls">
        <select
          className="ses-filter-select"
          value={reasonFilter}
          onChange={(e) => setReasonFilter(e.target.value)}
        >
          <option value="ALL">All reasons</option>
          <option value="BOUNCE">Bounce only</option>
          <option value="COMPLAINT">Complaint only</option>
        </select>
        <button className="ses-lookup-btn" onClick={handleLoad} disabled={loading}>
          {loading ? "Loading…" : loaded ? "Reload" : "Load List"}
        </button>
      </div>

      {loaded && (
        <>
          <div className="ses-browser-toolbar">
            <input
              type="text"
              className="ses-search-input"
              placeholder="Filter by email address…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <span className="ses-browser-count">
              {filter ? `${displayed.length} of ${entries.length}` : entries.length} address{entries.length !== 1 ? "es" : ""}
              {nextToken && " (more available)"}
            </span>
            {selected.size > 0 && (
              <button
                className="ses-remove-btn"
                onClick={removeSelected}
                disabled={removing}
              >
                {removing ? "Removing…" : `Remove ${selected.size} selected`}
              </button>
            )}
          </div>

          {removeMsg && (
            <div className={`ses-remove-msg ${removeMsg.startsWith("✓") ? "ses-msg-ok" : "ses-msg-err"}`}>
              {removeMsg}
            </div>
          )}

          {displayed.length > 0 ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        checked={selected.size > 0 && selected.size === displayed.length}
                        onChange={toggleAll}
                        style={{ accentColor: "var(--amber)" }}
                      />
                    </th>
                    <th>Email Address</th>
                    <th>Reason</th>
                    <th>Suppressed</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((e) => (
                    <tr
                      key={e.email}
                      onClick={() => toggleSelect(e.email)}
                      style={{ cursor: "pointer", background: selected.has(e.email) ? "rgba(245,166,35,0.07)" : undefined }}
                    >
                      <td onClick={(ev) => ev.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selected.has(e.email)}
                          onChange={() => toggleSelect(e.email)}
                          style={{ accentColor: "var(--amber)" }}
                        />
                      </td>
                      <td className="cell-mono cell-bold">{e.email}</td>
                      <td><ReasonBadge reason={e.reason} /></td>
                      <td><RelTime iso={e.suppressed_at} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="panel-empty">
              {filter ? `No addresses matching "${filter}"` : "Suppression list is empty"}
            </div>
          )}

          {nextToken && (
            <div style={{ padding: "1rem 1.25rem", borderTop: "1px solid var(--border)" }}>
              <button
                className="ses-lookup-btn"
                onClick={() => loadPage(nextToken)}
                disabled={loading}
              >
                {loading ? "Loading…" : "Load Next 100"}
              </button>
            </div>
          )}
        </>
      )}

      {!loaded && !loading && (
        <div className="panel-empty" style={{ padding: "2rem" }}>
          Click "Load List" to browse the suppression list (paginated, 100 per page).
        </div>
      )}
    </div>
  );
}

// ─── Main Panel ───────────────────────────────────────────────────────────────
export default function SESPanel() {
  const overviewFetcher = useCallback((force = false) => api.getSES(force), []);
  const { data, loading, error, refresh, refreshing } = useData(overviewFetcher);

  const [subTab, setSubTab] = useState("overview");

  const subtabs = [
    { id: "overview",     label: "Overview" },
    { id: "lookup",       label: "Lookup & Remove" },
    { id: "bulk",         label: "Bulk Remove" },
    { id: "browser",      label: "Browse List" },
    { id: "identities",   label: "Identities" },
  ];

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Amazon SES</h2>
        <div className="panel-header-actions">
          {!loading && !error && data && (
          <div className="ses-header-stats">
            <span className="ses-mini-stat">
              <span style={{ color: "var(--text-muted)" }}>Sent 24h:</span>
              <span style={{ color: "var(--amber)", fontFamily: "var(--font-mono)", marginLeft: 4 }}>
                {data.sent_last_24h?.toLocaleString()} / {data.max_24h_send?.toLocaleString()}
              </span>
            </span>
            {data.bounces_24h > 0 && (
              <span className="ses-mini-stat" style={{ color: "var(--red)" }}>
                ↩ {data.bounces_24h} bounces
              </span>
            )}
            {data.complaints_24h > 0 && (
              <span className="ses-mini-stat" style={{ color: "var(--red)" }}>
                ⚠ {data.complaints_24h} complaints
              </span>
            )}
          </div>
          )}
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="ses-subtabs">
        {subtabs.map((t) => (
          <button
            key={t.id}
            className={`ses-subtab ${subTab === t.id ? "active" : ""}`}
            onClick={() => setSubTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="ses-body">
        {/* Overview */}
        {subTab === "overview" && (
          loading ? <div className="panel-loading">Loading SES…</div> :
          error ? <div className="panel-error">SES: {error}</div> :
          data && (
            <>
              <div className="ses-stats-grid">
                <StatBox label="Max / 24h" value={data.max_24h_send?.toLocaleString()} sub="sending quota" />
                <StatBox label="Sent 24h" value={data.sent_last_24h?.toLocaleString()} color="var(--amber)" />
                <StatBox label="Max / sec" value={data.max_per_second} sub="send rate" />
                <StatBox label="Deliveries" value={data.deliveries_24h?.toLocaleString()} color="var(--green)" sub="last 24h" />
                <StatBox label="Bounces" value={data.bounces_24h} color={data.bounces_24h > 0 ? "var(--red)" : "var(--green)"} sub="last 24h" />
                <StatBox label="Complaints" value={data.complaints_24h} color={data.complaints_24h > 0 ? "var(--red)" : "var(--green)"} sub="last 24h" />
                <StatBox label="Rejects" value={data.rejects_24h} color={data.rejects_24h > 0 ? "var(--amber)" : "var(--text)"} sub="last 24h" />
                <StatBox label="Identities" value={`${data.identities?.verified}/${data.identities?.total}`} sub="verified" color="var(--blue)" />
              </div>
              {data.suppression_reasons?.length > 0 && (
                <div className="ses-suppression-note">
                  <span>Account-level suppression active for:</span>
                  {data.suppression_reasons.map((r) => (
                    <ReasonBadge key={r} reason={r} />
                  ))}
                </div>
              )}
            </>
          )
        )}

        {subTab === "lookup" && <SuppressionLookup />}
        {subTab === "bulk"   && <BulkRemove />}
        {subTab === "browser" && <SuppressionBrowser />}
        {subTab === "identities" && <IdentitiesTab />}
      </div>
    </section>
  );
}

// ─── Identity Drawer ──────────────────────────────────────────────────────────
function IdentityDrawer({ identity: id, onClose }) {
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const mailFromColor =
    id.mail_from_status === "SUCCESS" ? "var(--green)" :
    id.mail_from_status === "PENDING" ? "var(--amber)" :
    id.mail_from_status ? "var(--red)" : "var(--text-muted)";

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="secret-drawer">
        <div className="drawer-header">
          <div>
            <p className="drawer-title" style={{ fontSize: "0.9rem", wordBreak: "break-all" }}>{id.identity}</p>
            <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginTop: "0.3rem" }}>
              <span className="state-pill state-gray" style={{ fontSize: "0.65rem" }}>{id.type}</span>
              <span className={`state-pill ${id.status === "SUCCESS" ? "state-green" : id.status === "PENDING" ? "state-amber" : "state-red"}`} style={{ fontSize: "0.65rem" }}>
                {id.status}
              </span>
            </div>
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="drawer-body">

          {/* General */}
          <div className="drawer-section">
            <p className="drawer-label">General</p>
            <div className="drawer-meta-grid">
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Sending</span>
                <span className="drawer-meta-val">
                  <span className={`state-pill ${id.sending_enabled ? "state-green" : "state-gray"}`}>
                    {id.sending_enabled ? "Enabled" : "Disabled"}
                  </span>
                </span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Feedback Fwd</span>
                <span className="drawer-meta-val">
                  <span className={`state-pill ${id.feedback_forwarding ? "state-green" : "state-gray"}`}>
                    {id.feedback_forwarding ? "On" : "Off"}
                  </span>
                </span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Config Set</span>
                <span className="drawer-meta-val" style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)" }}>
                  {id.configuration_set || "—"}
                </span>
              </div>
            </div>
          </div>

          {/* DKIM */}
          <div className="drawer-section">
            <p className="drawer-label">DKIM</p>
            <div className="drawer-meta-grid">
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Signing</span>
                <span className="drawer-meta-val">
                  <span className={`state-pill ${id.dkim_enabled ? "state-green" : "state-gray"}`}>
                    {id.dkim_enabled ? "On" : "Off"}
                  </span>
                </span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Status</span>
                <span className="drawer-meta-val">
                  <span className={`state-pill ${id.dkim_status === "SUCCESS" ? "state-green" : id.dkim_status === "PENDING" ? "state-amber" : "state-gray"}`}>
                    {id.dkim_status}
                  </span>
                </span>
              </div>
              {id.dkim_origin && (
                <div className="drawer-meta-item">
                  <span className="drawer-meta-key">Origin</span>
                  <span className="drawer-meta-val" style={{ fontSize: "0.75rem" }}>{id.dkim_origin}</span>
                </div>
              )}
            </div>
            {id.dkim_tokens?.length > 0 && (
              <div style={{ marginTop: "0.75rem" }}>
                <p style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginBottom: "0.4rem" }}>CNAME records to add to DNS:</p>
                {id.dkim_tokens.map((token) => (
                  <div key={token} style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.3rem" }}>
                    <code className="drawer-arn" style={{ fontSize: "0.7rem", flex: 1 }}>
                      {token}._domainkey.{id.identity} → {token}.dkim.amazonses.com
                    </code>
                    <CopyButton text={`${token}._domainkey.${id.identity}`} size={12} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* MAIL FROM */}
          {(id.mail_from_domain || id.type === "DOMAIN") && (
            <div className="drawer-section">
              <p className="drawer-label">Custom MAIL FROM</p>
              {id.mail_from_domain ? (
                <div className="drawer-meta-grid">
                  <div className="drawer-meta-item">
                    <span className="drawer-meta-key">Domain</span>
                    <span className="drawer-meta-val" style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
                      {id.mail_from_domain}
                    </span>
                  </div>
                  <div className="drawer-meta-item">
                    <span className="drawer-meta-key">Status</span>
                    <span className="drawer-meta-val" style={{ color: mailFromColor, fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
                      {id.mail_from_status}
                    </span>
                  </div>
                  <div className="drawer-meta-item">
                    <span className="drawer-meta-key">MX Failure</span>
                    <span className="drawer-meta-val" style={{ fontSize: "0.75rem" }}>
                      {id.mail_from_mx_failure === "REJECT_MESSAGE" ? "Reject" : "Use Default"}
                    </span>
                  </div>
                </div>
              ) : (
                <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No custom MAIL FROM configured</p>
              )}
            </div>
          )}

          {/* Tags */}
          {Object.keys(id.tags || {}).length > 0 && (
            <div className="drawer-section">
              <p className="drawer-label">Tags</p>
              <div className="drawer-tags">
                {Object.entries(id.tags).map(([k, v]) => (
                  <span key={k} className="drawer-tag">{k}: {v}</span>
                ))}
              </div>
            </div>
          )}

        </div>
      </div>
    </>
  );
}

// ─── Identities sub-tab ───────────────────────────────────────────────────────
function IdentitiesTab() {
  const fetcher = useCallback((force = false) => api.getSESIdentities(force), []);
  const { data, loading, error } = useData(fetcher);
  const [selected, setSelected] = useState(null);

  if (loading) return <div className="panel-loading">Loading identities…</div>;
  if (error) return <div className="panel-error">{error}</div>;

  const identities = data?.identities || [];

  return (
    <>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Identity</th>
              <th>Type</th>
              <th>Verification</th>
              <th>Sending</th>
              <th>DKIM</th>
              <th>DKIM Status</th>
              <th>MAIL FROM</th>
              <th>Config Set</th>
            </tr>
          </thead>
          <tbody>
            {identities.map((i) => (
              <tr key={i.identity} className="row-clickable" onClick={() => setSelected(i)}>
                <td className="cell-bold cell-mono">{i.identity}</td>
                <td style={{ fontSize: "0.78rem" }}>{i.type}</td>
                <td>
                  <span className={`state-pill ${i.status === "SUCCESS" ? "state-green" : i.status === "PENDING" ? "state-amber" : "state-red"}`}>
                    {i.status}
                  </span>
                </td>
                <td>
                  <span className={`state-pill ${i.sending_enabled ? "state-green" : "state-gray"}`}>
                    {i.sending_enabled ? "On" : "Off"}
                  </span>
                </td>
                <td>
                  <span className={`state-pill ${i.dkim_enabled ? "state-green" : "state-gray"}`}>
                    {i.dkim_enabled ? "On" : "Off"}
                  </span>
                </td>
                <td>
                  <span className={`state-pill ${i.dkim_status === "SUCCESS" ? "state-green" : i.dkim_status === "PENDING" ? "state-amber" : "state-gray"}`}>
                    {i.dkim_status}
                  </span>
                </td>
                <td>
                  {i.mail_from_domain ? (
                    <span className={`state-pill ${i.mail_from_status === "SUCCESS" ? "state-green" : i.mail_from_status === "PENDING" ? "state-amber" : "state-red"}`}>
                      {i.mail_from_status}
                    </span>
                  ) : (
                    <span className="metric-na">—</span>
                  )}
                </td>
                <td className="cell-mono" style={{ fontSize: "0.75rem" }}>{i.configuration_set || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <IdentityDrawer identity={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
