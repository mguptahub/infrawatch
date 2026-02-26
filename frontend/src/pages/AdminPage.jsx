import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const SERVICE_LABELS = {
  ec2: "EC2", elb: "Load Balancers", eks: "EKS", databases: "Databases", elasticache: "ElastiCache",
  opensearch: "OpenSearch", mq: "Amazon MQ", ses: "SES",
  secrets: "Secrets Manager", cost: "Cost Explorer", alarms: "CloudWatch Alarms",
};

const STATUS_COLORS = {
  pending: "state-amber", approved: "state-green",
  denied: "state-red", expired: "state-gray",
};

// ─── Main Admin Page ──────────────────────────────────────────────────────────
export default function AdminPage() {
  const { auth, logout } = useAuth();
  const [tab, setTab] = useState("requests");
  const tabs = [
    { id: "requests", label: "Requests" },
    { id: "users",    label: "Users" },
  ];

  return (
    <div className="dashboard">
      <header className="dash-header">
        <div className="dash-brand">
          <span className="logo-icon">⬡</span>
          <span className="brand-name">AWS Monitor — Admin</span>
        </div>
        <div className="dash-account">
          <span className="account-arn">{auth?.email}</span>
          <button className="logout-btn" onClick={logout}>Disconnect</button>
        </div>
      </header>

      <nav className="dash-nav">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`nav-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="dash-content">
        {tab === "requests" && <RequestsPanel />}
        {tab === "users"    && <UsersPanel />}
      </main>
    </div>
  );
}

// ─── Requests Panel ───────────────────────────────────────────────────────────
function RequestsPanel() {
  const [requests, setRequests] = useState([]);
  const [statusFilter, setStatusFilter] = useState("pending");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.adminListRequests(statusFilter || undefined);
      setRequests(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  async function handleAction(id, action) {
    const reason = action === "deny"
      ? window.prompt("Denial reason (optional):")
      : null;
    if (action === "deny" && reason === null) return; // cancelled

    setActionLoading(id);
    try {
      await api.adminAction(id, action, reason || undefined);
      await load();
    } catch (e) {
      alert(e.message);
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Access Requests</h2>
        <div className="panel-header-actions">
          <select
            className="ses-filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
            <option value="expired">Expired</option>
          </select>
        </div>
      </div>

      {loading ? <div className="panel-loading">Loading…</div> :
       error   ? <div className="panel-error">{error}</div> :
       requests.length === 0 ? <div className="panel-empty">No requests found</div> : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Services</th>
                <th>Duration</th>
                <th>Status</th>
                <th>Submitted</th>
                <th>Reviewed By</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div className="cell-bold">{r.user_name}</div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{r.user_email}</div>
                  </td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
                      {r.services.map((s) => (
                        <span key={s} className="state-pill state-gray" style={{ fontSize: "0.65rem" }}>
                          {SERVICE_LABELS[s] || s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="cell-mono">{r.duration_hours}h</td>
                  <td>
                    <span className={`state-pill ${STATUS_COLORS[r.status] || "state-gray"}`}>
                      {r.status}
                    </span>
                    {r.denial_reason && (
                      <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                        {r.denial_reason}
                      </div>
                    )}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                    {r.reviewed_by || "—"}
                    {r.reviewed_at && (
                      <div style={{ fontSize: "0.7rem" }}>{new Date(r.reviewed_at).toLocaleString()}</div>
                    )}
                  </td>
                  <td>
                    {r.status === "pending" && (
                      <div style={{ display: "flex", gap: "0.4rem" }}>
                        <button
                          className="ses-lookup-btn"
                          style={{ background: "var(--green)", color: "#fff", border: "none" }}
                          onClick={() => handleAction(r.id, "approve")}
                          disabled={actionLoading === r.id}
                        >
                          {actionLoading === r.id ? "…" : "Approve"}
                        </button>
                        <button
                          className="ses-lookup-btn"
                          style={{ background: "var(--red)", color: "#fff", border: "none" }}
                          onClick={() => handleAction(r.id, "deny")}
                          disabled={actionLoading === r.id}
                        >
                          Deny
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ─── Users Panel ──────────────────────────────────────────────────────────────
function UsersPanel() {
  const [users, setUsers] = useState([]);
  const [services, setServices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null); // user object being edited

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [u, s] = await Promise.all([api.adminListUsers(), api.adminListServices()]);
      setUsers(u);
      setServices(s.services || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function openCreate() { setEditing(null); setShowForm(true); }
  function openEdit(u)   { setEditing(u);    setShowForm(true); }

  async function handleSave(data) {
    try {
      if (editing) {
        await api.adminUpdateUser(editing.id, data);
      } else {
        await api.adminCreateUser(data);
      }
      setShowForm(false);
      await load();
    } catch (e) {
      throw e; // let form handle it
    }
  }

  const managers = users.filter((u) => u.role === "manager" && u.active);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Users</h2>
        <div className="panel-header-actions">
          <button className="ses-lookup-btn" onClick={openCreate}>+ Add User</button>
        </div>
      </div>

      {showForm && (
        <UserForm
          user={editing}
          managers={managers}
          allServices={services}
          onSave={handleSave}
          onCancel={() => setShowForm(false)}
        />
      )}

      {loading ? <div className="panel-loading">Loading…</div> :
       error   ? <div className="panel-error">{error}</div> : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Manager</th>
                <th>Allowed Services</th>
                <th>Max Duration</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={!u.active ? "row-muted" : ""}>
                  <td className="cell-bold">{u.name}</td>
                  <td className="cell-mono" style={{ fontSize: "0.78rem" }}>{u.email}</td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
                      <span className={`state-pill ${u.role === "manager" ? "state-blue" : "state-gray"}`}>
                        {u.role}
                      </span>
                      {u.auto_approve && (
                        <span className="state-pill state-amber" style={{ fontSize: "0.62rem" }}>auto-approve</span>
                      )}
                    </div>
                  </td>
                  <td style={{ fontSize: "0.78rem" }}>{u.manager_name || "—"}</td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
                      {(u.allowed_services || []).map((s) => (
                        <span key={s} className="state-pill state-gray" style={{ fontSize: "0.62rem" }}>
                          {SERVICE_LABELS[s] || s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="cell-mono">{u.max_duration_hours}h</td>
                  <td>
                    <span className={`state-pill ${u.active ? "state-green" : "state-red"}`}>
                      {u.active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    <button className="ses-lookup-btn" onClick={() => openEdit(u)}>Edit</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ─── User Form ────────────────────────────────────────────────────────────────
function UserForm({ user, managers, allServices, onSave, onCancel }) {
  const [form, setForm] = useState({
    email: user?.email || "",
    name: user?.name || "",
    role: user?.role || "employee",
    manager_email: user?.manager_email || "",
    allowed_services: user?.allowed_services || [],
    max_duration_hours: user?.max_duration_hours || 1,
    auto_approve: user?.auto_approve || false,
    active: user?.active !== false,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  function set(k, v) { setForm((p) => ({ ...p, [k]: v })); }

  function toggleService(s) {
    set("allowed_services",
      form.allowed_services.includes(s)
        ? form.allowed_services.filter((x) => x !== s)
        : [...form.allowed_services, s]
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload = { ...form };
      if (!payload.manager_email) delete payload.manager_email;
      await onSave(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="ses-lookup-box" style={{ margin: "0 0 1.5rem 0", padding: "1rem" }}>
      <p className="ses-section-title">{user ? `Edit: ${user.name}` : "Add New User"}</p>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div className="field" style={{ margin: 0 }}>
            <label>Name</label>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} required />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>Email</label>
            <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)}
              required disabled={!!user} />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>Role</label>
            <select value={form.role} onChange={(e) => set("role", e.target.value)}>
              <option value="employee">Employee</option>
              <option value="manager">Manager</option>
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>Manager</label>
            <select value={form.manager_email} onChange={(e) => set("manager_email", e.target.value)}>
              <option value="">— None —</option>
              {managers.map((m) => (
                <option key={m.id} value={m.email}>{m.name} ({m.email})</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>
              Max Duration (hours)
              <span className="field-hint"> — max 12h (AWS STS limit)</span>
            </label>
            <input type="number" min={1} max={12} value={form.max_duration_hours}
              onChange={(e) => set("max_duration_hours", Math.min(12, parseInt(e.target.value) || 1))} />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>
              Auto Approve
              <span className="field-hint"> — skip manager approval</span>
            </label>
            <select value={form.auto_approve ? "yes" : "no"}
              onChange={(e) => set("auto_approve", e.target.value === "yes")}>
              <option value="no">No — requires approval</option>
              <option value="yes">Yes — auto approved</option>
            </select>
          </div>
          {user && (
            <div className="field" style={{ margin: 0 }}>
              <label>Status</label>
              <select value={form.active ? "active" : "inactive"}
                onChange={(e) => set("active", e.target.value === "active")}>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          )}
        </div>

        <div className="field" style={{ margin: 0 }}>
          <label>Allowed Services</label>
          <div className="service-grid">
            {allServices.map((s) => (
              <label key={s} className={`service-chip ${form.allowed_services.includes(s) ? "selected" : ""}`}>
                <input type="checkbox" checked={form.allowed_services.includes(s)}
                  onChange={() => toggleService(s)} style={{ display: "none" }} />
                {SERVICE_LABELS[s] || s}
              </label>
            ))}
          </div>
        </div>

        {error && <div className="panel-error">{error}</div>}
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button type="submit" className="ses-lookup-btn"
            style={{ background: "var(--blue)", color: "#fff", border: "none" }} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="ses-lookup-btn" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </div>
  );
}
