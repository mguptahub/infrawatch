import { useCallback, useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

function RelativeTime({ iso }) {
  if (!iso) return <span className="metric-na">Never</span>;
  const d = new Date(iso);
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  const label =
    diff < 3600 ? `${Math.floor(diff / 60)}m ago`
      : diff < 86400 ? `${Math.floor(diff / 3600)}h ago`
        : `${Math.floor(diff / 86400)}d ago`;
  return <span title={d.toLocaleString()}>{label}</span>;
}

function SortTh({ col, label, sort, onSort }) {
  const active = sort.col === col;
  return (
    <th className="th-sort" onClick={() => onSort(col)} style={{ color: active ? "var(--amber)" : undefined }}>
      {label}{" "}
      <span style={{ opacity: active ? 1 : 0.3, fontSize: "0.65rem" }}>
        {active ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}
      </span>
    </th>
  );
}

function UserDetailDrawer({ user, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDetail(null);
    setLoading(true);
    setError(null);
    api.getIAMUserDetail(user.username)
      .then((d) => { setDetail(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [user.username]);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="detail-drawer">
        <div className="drawer-header">
          <div>
            <div className="drawer-title">{user.username}</div>
            <div className="drawer-subtitle">
              <span className="cell-mono" style={{ fontSize: "0.72rem" }}>{user.user_id}</span>
            </div>
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="drawer-body">
          {loading && <div className="panel-loading">Loading user details…</div>}
          {error && <div className="panel-error">{error}</div>}
          {detail && (
            <>
              <div className="drawer-section">
                <div className="drawer-section-hdr">Overview</div>
                <div className="drawer-meta-grid">
                  <MetaItem label="Username" value={detail.user.username} mono />
                  <MetaItem label="Path" value={detail.user.path} mono />
                  <MetaItem label="Created" value={detail.user.created_at ? new Date(detail.user.created_at).toLocaleString() : "—"} />
                  <MetaItem label="Password Last Used" value={detail.user.password_last_used ? new Date(detail.user.password_last_used).toLocaleString() : "Never"} />
                  <MetaItem label="Permissions Boundary" value={detail.user.permissions_boundary_arn || "—"} mono />
                  <MetaItem label="ARN" value={detail.user.arn} mono />
                </div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-hdr">Groups ({detail.groups.length})</div>
                {detail.groups.length === 0 ? (
                  <div className="panel-empty" style={{ marginTop: "0.7rem" }}>No groups attached</div>
                ) : (
                  <table className="data-table">
                    <thead><tr><th>Group</th><th>ARN</th></tr></thead>
                    <tbody>
                      {detail.groups.map((g) => (
                        <tr key={g.arn}>
                          <td className="cell-bold">{g.name}</td>
                          <td className="cell-mono" style={{ fontSize: "0.7rem", whiteSpace: "normal", wordBreak: "break-all" }}>{g.arn}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div className="drawer-section">
                <div className="drawer-section-hdr">Policies</div>
                <div style={{ marginTop: "0.7rem" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.35rem" }}>
                    Managed ({detail.attached_policies.length})
                  </div>
                  {detail.attached_policies.length === 0 ? (
                    <div className="metric-na">None</div>
                  ) : (
                    <table className="data-table">
                      <thead><tr><th>Name</th><th>ARN</th></tr></thead>
                      <tbody>
                        {detail.attached_policies.map((p) => (
                          <tr key={p.arn}>
                            <td className="cell-bold">{p.name}</td>
                            <td className="cell-mono" style={{ fontSize: "0.7rem", whiteSpace: "normal", wordBreak: "break-all" }}>{p.arn}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                <div style={{ marginTop: "0.8rem" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.35rem" }}>
                    Inline ({detail.inline_policies.length})
                  </div>
                  {detail.inline_policies.length === 0 ? (
                    <div className="metric-na">None</div>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
                      {detail.inline_policies.map((p) => (
                        <span key={p} className="state-pill state-gray">{p}</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-hdr">Credentials</div>
                <div style={{ marginTop: "0.7rem" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.35rem" }}>
                    MFA Devices ({detail.mfa_devices.length})
                  </div>
                  {detail.mfa_devices.length === 0 ? (
                    <div className="metric-na">No MFA devices</div>
                  ) : (
                    <table className="data-table">
                      <thead><tr><th>Serial</th><th>Enabled</th></tr></thead>
                      <tbody>
                        {detail.mfa_devices.map((m) => (
                          <tr key={m.serial_number}>
                            <td className="cell-mono" style={{ fontSize: "0.72rem", wordBreak: "break-all" }}>{m.serial_number}</td>
                            <td>{m.enabled_at ? new Date(m.enabled_at).toLocaleString() : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                <div style={{ marginTop: "0.8rem" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.35rem" }}>
                    Access Keys ({detail.access_keys.length})
                  </div>
                  {detail.access_keys.length === 0 ? (
                    <div className="metric-na">No access keys</div>
                  ) : (
                    <div className="table-wrap" style={{ overflowX: "auto" }}>
                      <table className="data-table">
                        <thead><tr><th>Key ID</th><th>Status</th><th>Created</th><th>Last Used</th></tr></thead>
                        <tbody>
                          {detail.access_keys.map((k) => (
                            <tr key={k.id}>
                              <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{k.id}</td>
                              <td>
                                <span className={`state-pill ${k.status === "Active" ? "state-green" : "state-red"}`}>
                                  {k.status}
                                </span>
                              </td>
                              <td>{k.created_at ? new Date(k.created_at).toLocaleString() : "—"}</td>
                              <td>
                                {k.last_used?.date
                                  ? `${new Date(k.last_used.date).toLocaleString()} (${k.last_used.service || "unknown"})`
                                  : "Never"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

            </>
          )}
        </div>
      </div>
    </>
  );
}

function MetaItem({ label, value, mono = false }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span className={`drawer-meta-val ${mono ? "cell-mono" : ""}`} style={mono ? { fontSize: "0.72rem", wordBreak: "break-all" } : {}}>
        {value}
      </span>
    </div>
  );
}

export default function IAMPanel() {
  const fetcher = useCallback((force = false) => api.getIAMUsers(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: "username", dir: "asc" });

  if (loading) return <div className="panel-loading">Loading IAM users…</div>;
  if (error) return <div className="panel-error">IAM: {error}</div>;

  const users = data?.users || [];
  const toggleSort = (col) =>
    setSort((s) => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...users].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col];
    const vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    if (typeof va === "boolean" || typeof vb === "boolean") return d * ((va === vb) ? 0 : va ? 1 : -1);
    return d * ((va ?? 0) - (vb ?? 0));
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>IAM Users <span className="count-badge">{data?.count ?? 0}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="panel-empty">No IAM users found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="username" label="Username" sort={sort} onSort={toggleSort} />
                <th>User ID</th>
                <SortTh col="created_at" label="Created" sort={sort} onSort={toggleSort} />
                <SortTh col="password_last_used" label="Password Last Used" sort={sort} onSort={toggleSort} />
                <SortTh col="console_access" label="Console Access" sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((u) => (
                <tr key={u.user_id} className="row-clickable" onClick={() => setSelected(u)}>
                  <td className="cell-bold">{u.username}</td>
                  <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{u.user_id}</td>
                  <td>{u.created_at ? new Date(u.created_at).toLocaleString() : "—"}</td>
                  <td><RelativeTime iso={u.password_last_used} /></td>
                  <td>
                    {u.console_access === true && <span className="state-pill state-green">Yes</span>}
                    {u.console_access === false && <span className="state-pill state-gray">No</span>}
                    {u.console_access == null && <span className="metric-na">Unknown</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && <UserDetailDrawer user={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}
