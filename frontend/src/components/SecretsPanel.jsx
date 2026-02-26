import { useCallback, useState, useEffect } from "react";
import { RefreshCw, Copy, Check, X } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
import { REFRESH_STREAM_TIMEOUT_MS } from "../constants";
import AlertBanner from "./AlertBanner";
import ResourceAlerts from "./ResourceAlerts";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function AgeBadge({ days }) {
  if (days === null || days === undefined) return <span className="metric-na">—</span>;
  const color = days > 180 ? "var(--red)" : days > 90 ? "var(--amber)" : "var(--green)";
  return <span style={{ fontFamily: "var(--font-mono)", color }}>{days}d</span>;
}

function RelativeTime({ iso }) {
  if (!iso) return <span style={{ color: "var(--text-muted)" }}>Never</span>;
  const date = new Date(iso);
  const diff = Math.floor((Date.now() - date) / 1000);
  const label =
    diff < 3600  ? `${Math.floor(diff / 60)}m ago` :
    diff < 86400 ? `${Math.floor(diff / 3600)}h ago` :
                   `${Math.floor(diff / 86400)}d ago`;
  return <span title={date.toLocaleString()}>{label}</span>;
}

function CopyButton({ text, size = 13, label }) {
  const [copied, setCopied] = useState(false);
  function handleCopy(e) {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button className={`copy-btn ${copied ? "copied" : ""}`} onClick={handleCopy} title={copied ? "Copied!" : `Copy${label ? " " + label : ""}`}>
      {copied ? <Check size={size} /> : <Copy size={size} />}
      {label && <span style={{ fontSize: "0.68rem", fontFamily: "var(--font-mono)", marginLeft: "3px" }}>{copied ? "Copied!" : label}</span>}
    </button>
  );
}

// ─── Secret Value display ─────────────────────────────────────────────────────

function SecretValue({ arn }) {
  const [state, setState] = useState("idle"); // idle | loading | done | error
  const [valueData, setValueData] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    setState("loading");
    try {
      const data = await api.getSecretValue(arn);
      setValueData(data);
      setState("done");
    } catch (e) {
      setError(e.message);
      setState("error");
    }
  }

  const displayText =
    valueData?.type === "json"
      ? JSON.stringify(valueData.value, null, 2)
      : valueData?.value ?? "";

  return (
    <div className="drawer-section">
      <div className="drawer-value-header">
        <p className="drawer-label">Secret Value</p>
        {state === "done" && <CopyButton text={displayText} />}
      </div>

      {state === "idle" && (
        <button className="ses-lookup-btn" style={{ alignSelf: "flex-start" }} onClick={load}>
          Load Value
        </button>
      )}
      {state === "loading" && (
        <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Loading…</p>
      )}
      {state === "error" && (
        <div className="panel-error" style={{ borderRadius: "var(--radius)" }}>{error}</div>
      )}
      {state === "done" && valueData?.type === "binary" && (
        <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Binary secret — not displayable</p>
      )}
      {state === "done" && valueData?.type !== "binary" && (
        <pre className="secret-value-box">{displayText}</pre>
      )}
    </div>
  );
}

// ─── Drawer ───────────────────────────────────────────────────────────────────

function SecretDrawer({ secret, onClose }) {
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="secret-drawer">

        <div className="drawer-header">
          <div>
            <p className="drawer-title">{secret.name}</p>
            <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
              {secret.stale && <span className="state-pill state-red" style={{ fontSize: "0.65rem" }}>STALE</span>}
              <span className={`state-pill ${secret.rotation_enabled ? "state-green" : "state-gray"}`} style={{ fontSize: "0.65rem" }}>
                {secret.rotation_enabled ? "Rotation On" : "Rotation Off"}
              </span>
            </div>
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="drawer-body">
          <ResourceAlerts resourceId={secret.name} />

          {/* ARN */}
          <div className="drawer-section">
            <p className="drawer-label">ARN</p>
            <div className="drawer-arn-row">
              <code className="drawer-arn">{secret.arn}</code>
              <CopyButton text={secret.arn} />
            </div>
          </div>

          {/* Metadata */}
          <div className="drawer-section">
            <p className="drawer-label">Details</p>
            <div className="drawer-meta-grid">
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Age</span>
                <span className="drawer-meta-val"><AgeBadge days={secret.age_days} /></span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Rotate Every</span>
                <span className="drawer-meta-val">{secret.rotation_days ? `${secret.rotation_days}d` : "—"}</span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Last Accessed</span>
                <span className="drawer-meta-val"><RelativeTime iso={secret.last_accessed} /></span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Last Changed</span>
                <span className="drawer-meta-val"><RelativeTime iso={secret.last_changed} /></span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">Last Rotated</span>
                <span className="drawer-meta-val"><RelativeTime iso={secret.last_rotated} /></span>
              </div>
              <div className="drawer-meta-item">
                <span className="drawer-meta-key">KMS Key</span>
                <span className="drawer-meta-val" style={{ fontSize: "0.72rem" }}>
                  {secret.kms_key?.includes("/") ? secret.kms_key.split("/").pop() : secret.kms_key || "Default"}
                </span>
              </div>
            </div>
          </div>

          {/* Secret value — loaded on demand */}
          <SecretValue arn={secret.arn} />

          {/* Tags */}
          {Object.keys(secret.tags || {}).length > 0 && (
            <div className="drawer-section">
              <p className="drawer-label">Tags</p>
              <div className="drawer-tags">
                {Object.entries(secret.tags).map(([k, v]) => (
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

// ─── Main Panel ───────────────────────────────────────────────────────────────

export default function SecretsPanel() {
  const fetcher = useCallback((force = false) => api.getSecrets(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: null, dir: "asc" });
  const [syncing, setSyncing] = useState(false);
  const [showRefreshed, setShowRefreshed] = useState(false);
  const [resourceAlarms, setResourceAlarms] = useState([]);

  async function handleRefresh() {
    setSyncing(true);
    setShowRefreshed(false);
    const streamUrl = api.secretsRefreshStreamUrl();
    const es = new EventSource(streamUrl);
    const timeoutId = setTimeout(() => {
      es.close();
      refresh();
      setSyncing(false);
    }, REFRESH_STREAM_TIMEOUT_MS);
    es.addEventListener("refresh_done", () => {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
      setShowRefreshed(true);
      setTimeout(() => setShowRefreshed(false), 1500);
    });
    es.onerror = () => {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    };
    try {
      await api.refreshSecrets();
    } catch (e) {
      clearTimeout(timeoutId);
      es.close();
      refresh();
      setSyncing(false);
    }
  }

  function toggleSort(col) {
    setSort(s => s.col === col
      ? { col, dir: s.dir === "asc" ? "desc" : "asc" }
      : { col, dir: "asc" }
    );
  }

  function sortIcon(col) {
    if (sort.col !== col) return "↕";
    return sort.dir === "asc" ? "↑" : "↓";
  }

  const secrets = (() => {
    const list = data?.secrets || [];
    if (!sort.col) return list;
    return [...list].sort((a, b) => {
      let av, bv;
      if (sort.col === "name") {
        av = a.name?.toLowerCase() ?? "";
        bv = b.name?.toLowerCase() ?? "";
        return sort.dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      if (sort.col === "age") {
        av = a.age_days ?? -1;
        bv = b.age_days ?? -1;
      } else if (sort.col === "last_accessed") {
        av = a.last_accessed ? new Date(a.last_accessed).getTime() : -1;
        bv = b.last_accessed ? new Date(b.last_accessed).getTime() : -1;
      } else if (sort.col === "last_changed") {
        av = a.last_changed ? new Date(a.last_changed).getTime() : -1;
        bv = b.last_changed ? new Date(b.last_changed).getTime() : -1;
      }
      return sort.dir === "asc" ? av - bv : bv - av;
    });
  })();

  if (loading) return <div className="panel-loading">Loading Secrets Manager…</div>;
  if (error) return <div className="panel-error">Secrets Manager: {error}</div>;

  const staleCount = data?.stale_count ?? 0;

  return (
    <>
      <section className="panel">
        <AlertBanner serviceType="secrets" onAlarmsLoaded={setResourceAlarms} />
        <div className="panel-header">
          <h2>
            Secrets Manager{" "}
            {staleCount > 0 && (
              <span className="alarm-badge">{staleCount} STALE</span>
            )}
          </h2>
          <div className="panel-header-actions">
            <span className="count-badge">{data?.count ?? 0} secrets</span>
            <button className="refresh-btn" onClick={handleRefresh} disabled={refreshing || syncing} title="Sync from AWS and refresh">
              {showRefreshed ? (
                <span className="refresh-done"><Check size={13} /> Refreshed</span>
              ) : (
                <RefreshCw size={13} className={refreshing || syncing ? "spinning" : ""} />
              )}
            </button>
          </div>
        </div>

        {secrets.length === 0 ? (
          <div className="panel-empty">No secrets found</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "32px" }}></th>
                  <th onClick={() => toggleSort("name")} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
                    Name {sortIcon("name")}
                  </th>
                  <th>Rotation</th>
                  <th>Rotate Every</th>
                  <th onClick={() => toggleSort("age")} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
                    Age {sortIcon("age")}
                  </th>
                  <th onClick={() => toggleSort("last_accessed")} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
                    Last Accessed {sortIcon("last_accessed")}
                  </th>
                  <th onClick={() => toggleSort("last_changed")} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
                    Last Changed {sortIcon("last_changed")}
                  </th>
                  <th>KMS Key</th>
                </tr>
              </thead>
              <tbody>
                {secrets.map((s) => (
                  <tr
                    key={s.arn}
                    className="row-clickable"
                    style={s.stale ? { background: "rgba(240,77,95,0.04)" } : {}}
                    onClick={() => setSelected(s)}
                  >
                    <td>
                      {resourceAlarms.some(a => a.resource_id === s.name && a.state === "ALARM") && (
                        <span className="alert-dot" title={`${resourceAlarms.filter(a => a.resource_id === s.name && a.state === "ALARM").length} active alarm(s)`} />
                      )}
                    </td>
                    <td>
                      <div className="row-copy-cell">
                        <span className="cell-bold" title={s.description}>{s.name}</span>
                        <span onClick={(e) => e.stopPropagation()} title="Copy ARN">
                          <CopyButton text={s.arn} label="ARN" />
                        </span>
                        {s.stale && (
                          <span className="state-pill state-red" style={{ fontSize: "0.65rem" }}>STALE</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <span className={`state-pill ${s.rotation_enabled ? "state-green" : "state-gray"}`}>
                        {s.rotation_enabled ? "Enabled" : "Disabled"}
                      </span>
                    </td>
                    <td className="cell-mono">
                      {s.rotation_days ? `${s.rotation_days}d` : <span className="metric-na">—</span>}
                    </td>
                    <td><AgeBadge days={s.age_days} /></td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                      <RelativeTime iso={s.last_accessed} />
                    </td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                      <RelativeTime iso={s.last_changed} />
                    </td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem", maxWidth: "160px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {s.kms_key?.includes("/") ? s.kms_key.split("/").pop() : s.kms_key || "Default"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <SecretDrawer secret={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
