import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, Shield, Settings, Copy, Check,
  ChevronDown, ChevronRight, Server, Network, Lock,
} from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const STATUS_COLORS = {
  ACTIVE:   "state-green",
  CREATING: "state-amber",
  UPDATING: "state-amber",
  DELETING: "state-red",
  FAILED:   "state-red",
};

function SortTh({ col, label, sort, onSort }) {
  const active = sort.col === col;
  return (
    <th
      className="th-sort"
      onClick={() => onSort(col)}
      style={{ color: active ? "var(--amber)" : undefined }}
    >
      {label}{" "}
      <span style={{ opacity: active ? 1 : 0.3, fontSize: "0.65rem" }}>
        {active ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}
      </span>
    </th>
  );
}

export default function EKSPanel() {
  const fetcher = useCallback((force = false) => api.getEKS(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useState({ col: "name", dir: "asc" });

  if (loading) return <div className="panel-loading">Loading EKS…</div>;
  if (error)   return <div className="panel-error">EKS: {error}</div>;

  const clusters = data?.clusters || [];

  const toggleSort = (col) =>
    setSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...clusters].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col], vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>EKS Clusters <span className="count-badge">{clusters.length}</span></h2>
        <div className="panel-header-actions">
          <button className="refresh-btn" onClick={refresh} disabled={refreshing} title="Refresh">
            <RefreshCw size={13} className={refreshing ? "spinning" : ""} />
          </button>
        </div>
      </div>

      {clusters.length === 0 ? (
        <div className="panel-empty">No EKS clusters found</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="name"            label="Cluster"       sort={sort} onSort={toggleSort} />
                <SortTh col="status"          label="Status"        sort={sort} onSort={toggleSort} />
                <SortTh col="version"         label="K8s Version"   sort={sort} onSort={toggleSort} />
                <SortTh col="nodegroup_count" label="Node Groups"   sort={sort} onSort={toggleSort} />
                <SortTh col="node_count"      label="Total Nodes"   sort={sort} onSort={toggleSort} />
                <th>Endpoint Access</th>
                <SortTh col="created_at"      label="Created"       sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map(c => (
                <tr
                  key={c.arn}
                  className={`row-clickable ${selected?.arn === c.arn ? "row-selected" : ""}`}
                  onClick={() => setSelected(c)}
                >
                  <td className="cell-bold">{c.name}</td>
                  <td>
                    <span className={`state-pill ${STATUS_COLORS[c.status] || "state-gray"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="cell-mono">v{c.version}</td>
                  <td className="cell-mono">{c.nodegroup_count ?? c.nodegroups?.length ?? "—"}</td>
                  <td className="cell-mono">{c.node_count ?? "—"}</td>
                  <td>
                    {c.public_access && <span className="state-pill state-amber" style={{ marginRight: "0.25rem" }}>Public</span>}
                    {c.private_access && <span className="state-pill state-green">Private</span>}
                    {!c.public_access && !c.private_access && <span className="metric-na">—</span>}
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
                    {c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <>
          <div className="drawer-backdrop" onClick={() => setSelected(null)} />
          <DetailDrawer cluster={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </section>
  );
}

function DetailDrawer({ cluster, onClose }) {
  const [detail, setDetail]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [sgExpanded, setSgExpanded] = useState({});
  const [openSections, setOpenSections] = useState({
    overview: true, endpoint: true, networking: true,
    nodegroups: false, logging: false, oidc: false,
    security: false, tags: false,
  });

  useEffect(() => {
    setLoading(true);
    setError(null);
    setDetail(null);
    setSgExpanded({});
    api.getEKSDetail(cluster.name)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [cluster.name]);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const toggle = (s) => setOpenSections(p => ({ ...p, [s]: !p[s] }));

  function Section({ id, title, icon: Icon, children }) {
    const open = openSections[id];
    return (
      <div className="drawer-section">
        <div className="drawer-section-hdr" onClick={() => toggle(id)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Icon size={13} style={{ margin: "0 4px" }} />
          <span>{title}</span>
        </div>
        {open && <div className="drawer-section-body">{children}</div>}
      </div>
    );
  }

  return (
    <div className="detail-drawer">
      <div className="drawer-header">
        <div>
          <div className="drawer-title">{cluster.name}</div>
          <div className="drawer-subtitle">
            {detail
              ? <span className={`state-pill ${STATUS_COLORS[detail.status] || "state-gray"}`}>{detail.status}</span>
              : !error && <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>Loading…</span>
            }
          </div>
        </div>
        <button className="drawer-close" onClick={onClose}><X size={16} /></button>
      </div>

      <div className="drawer-body">
        {loading && <div className="panel-loading">Loading details…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {detail  && (
          <>
            {/* ── Overview ── */}
            <Section id="overview" title="Overview" icon={Info}>
              <div className="drawer-meta-grid">
                <MetaItem label="K8s Version"      value={`v${detail.version}`} mono />
                <MetaItem label="Platform Version" value={detail.platform_version || "—"} mono />
                <MetaItem label="Status"           value={detail.status} />
                <MetaItem label="Created"          value={detail.created_at ? new Date(detail.created_at).toLocaleString() : "—"} />
                <MetaItem label="Role ARN"         value={detail.role_arn || "—"} mono />
                <MetaItem label="Cluster ARN"      value={detail.arn} mono />
              </div>
            </Section>

            {/* ── Endpoint ── */}
            <Section id="endpoint" title="Endpoint" icon={Server}>
              {detail.endpoint ? (
                <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      API Server Endpoint
                      {detail.public_access && <span style={{ fontSize: "0.6rem", background: "var(--bg-card)", color: "var(--amber)", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, border: "1px solid var(--border)" }}>PUBLIC</span>}
                      {detail.private_access && <span style={{ fontSize: "0.6rem", background: "var(--bg-card)", color: "var(--green)", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, border: "1px solid var(--border)" }}>PRIVATE</span>}
                    </span>
                    <CopyButton text={detail.endpoint} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.8rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.endpoint}
                  </div>
                </div>
              ) : (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No endpoint available</span>
              )}
              {detail.public_access_cidrs?.length > 0 && (
                <div style={{ marginTop: "0.75rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Allowed Public CIDRs</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                    {detail.public_access_cidrs.map(cidr => (
                      <span key={cidr} className="cell-mono" style={{ fontSize: "0.72rem", padding: "0.15rem 0.4rem", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "4px" }}>{cidr}</span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* ── Networking ── */}
            <Section id="networking" title="Networking" icon={Network}>
              <div className="drawer-meta-grid">
                {detail.vpc_id && <MetaItem label="VPC" value={detail.vpc_id} mono />}
              </div>
              {detail.subnet_ids?.length > 0 && (
                <div style={{ marginTop: "0.6rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Subnets ({detail.subnet_ids.length})</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                    {detail.subnet_ids.map(s => (
                      <span key={s} className="cell-mono" style={{ fontSize: "0.72rem", padding: "0.15rem 0.4rem", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "4px" }}>{s}</span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* ── Node Groups ── */}
            <Section id="nodegroups" title={`Node Groups (${detail.nodegroups?.length ?? 0})`} icon={Server}>
              {detail.nodegroups?.length === 0 ? (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No node groups</span>
              ) : (
                <div className="table-wrap">
                  <table className="data-table" style={{ fontSize: "0.78rem" }}>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Instance Types</th>
                        <th>D / Min / Max</th>
                        <th>Capacity</th>
                        <th>AMI</th>
                        <th>Disk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.nodegroups ?? []).map(ng => (
                        <tr key={ng.name}>
                          <td className="cell-bold">{ng.name}</td>
                          <td>
                            <span className={`state-pill ${STATUS_COLORS[ng.status] || "state-gray"}`} style={{ fontSize: "0.65rem" }}>
                              {ng.status}
                            </span>
                          </td>
                          <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{ng.instance_types?.join(", ") || "—"}</td>
                          <td className="cell-mono">
                            {ng.scaling_config
                              ? `${ng.scaling_config.desiredSize} / ${ng.scaling_config.minSize} / ${ng.scaling_config.maxSize}`
                              : "—"}
                          </td>
                          <td>
                            <span className={`state-pill ${ng.capacity_type === "SPOT" ? "state-amber" : "state-green"}`} style={{ fontSize: "0.65rem" }}>
                              {ng.capacity_type || "—"}
                            </span>
                          </td>
                          <td className="cell-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>{ng.ami_type || "—"}</td>
                          <td className="cell-mono">{ng.disk_size ? `${ng.disk_size} GB` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {detail.nodegroups?.some(ng => ng.health_issues?.length > 0) && (
                <div style={{ marginTop: "0.75rem" }}>
                  <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem", color: "var(--red)" }}>Health Issues</span>
                  {(detail.nodegroups ?? []).filter(ng => ng.health_issues?.length > 0).map(ng => (
                    <div key={ng.name} style={{ marginBottom: "0.4rem" }}>
                      <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>{ng.name}:</span>
                      {ng.health_issues.map((issue, i) => (
                        <div key={i} style={{ fontSize: "0.75rem", color: "var(--red)", marginLeft: "0.75rem" }}>
                          {issue.code}: {issue.message}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* ── Encryption & Logging ── */}
            <Section id="logging" title="Encryption & Logging" icon={Lock}>
              <div className="drawer-meta-grid">
                <MetaItem label="Encryption at Rest" value={detail.kms_key ? "Enabled (KMS)" : "Default"} />
                {detail.kms_key && <MetaItem label="KMS Key ARN" value={detail.kms_key} mono />}
              </div>
              <div style={{ marginTop: "0.75rem" }}>
                <span className="stat-lbl" style={{ display: "block", marginBottom: "0.4rem" }}>Control Plane Logging</span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                  {["api", "audit", "authenticator", "controllerManager", "scheduler"].map(logType => {
                    const enabled = detail.enabled_log_types?.includes(logType);
                    return (
                      <span
                        key={logType}
                        className={`state-pill ${enabled ? "state-green" : "state-gray"}`}
                        style={{ fontSize: "0.68rem" }}
                      >
                        {logType}
                      </span>
                    );
                  })}
                </div>
              </div>
            </Section>

            {/* ── OIDC ── */}
            {detail.oidc_issuer && (
              <Section id="oidc" title="OIDC / IAM Roles for Service Accounts" icon={Settings}>
                <div style={{ padding: "0.7rem 0.8rem", background: "var(--bg-app)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                  <div className="stat-lbl" style={{ marginBottom: "0.3rem", display: "flex", justifyContent: "space-between" }}>
                    <span>OIDC Issuer URL</span>
                    <CopyButton text={detail.oidc_issuer} />
                  </div>
                  <div className="cell-mono" style={{ fontSize: "0.78rem", color: "var(--amber)", wordBreak: "break-all" }}>
                    {detail.oidc_issuer}
                  </div>
                </div>
              </Section>
            )}

            {/* ── Security Groups ── */}
            {detail.security_groups?.length > 0 && (
              <Section id="security" title={`Security Groups (${detail.security_groups.length})`} icon={Shield}>
                {detail.security_groups.map(sg => (
                  <div key={sg.id} className="ec2-sg-block">
                    <button
                      className="ec2-sg-hdr"
                      onClick={() => setSgExpanded(p => ({ ...p, [sg.id]: !p[sg.id] }))}
                    >
                      {sgExpanded[sg.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      <span style={{ fontWeight: 600 }}>{sg.name}</span>
                      <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginLeft: "0.4rem" }}>
                        {sg.id}
                      </span>
                    </button>
                    {sgExpanded[sg.id] && (
                      <div className="ec2-sg-rules">
                        <RulesTable label="Inbound"  rules={sg.inbound}  />
                        <RulesTable label="Outbound" rules={sg.outbound} />
                      </div>
                    )}
                  </div>
                ))}
              </Section>
            )}

            {/* ── Tags ── */}
            {detail.tags?.length > 0 && (
              <Section id="tags" title={`Tags (${detail.tags.length})`} icon={Info}>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>Key</th><th>Value</th></tr></thead>
                    <tbody>
                      {detail.tags.map(t => (
                        <tr key={t.key}>
                          <td className="cell-mono" style={{ color: "var(--text-dim)" }}>{t.key}</td>
                          <td style={{ whiteSpace: "normal", wordBreak: "break-all" }}>{t.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function MetaItem({ label, value, mono }) {
  return (
    <div className="drawer-meta-item">
      <span className="drawer-meta-key">{label}</span>
      <span
        className={`drawer-meta-val ${mono ? "cell-mono" : ""}`}
        style={mono ? { fontSize: "0.72rem", wordBreak: "break-all" } : {}}
      >
        {value}
      </span>
    </div>
  );
}

function RulesTable({ label, rules }) {
  if (!rules || rules.length === 0) return null;
  return (
    <div style={{ marginBottom: "0.75rem" }}>
      <div style={{
        fontSize: "0.68rem", fontWeight: 700, textTransform: "uppercase",
        letterSpacing: "0.08em", color: "var(--text-muted)", marginBottom: "0.35rem",
      }}>
        {label}
      </div>
      <table className="data-table" style={{ fontSize: "0.78rem" }}>
        <thead><tr><th>Protocol</th><th>Port</th><th>Source / Dest</th></tr></thead>
        <tbody>
          {rules.map((r, idx) => (
            <tr key={idx}>
              <td className="cell-mono">{r.protocol}</td>
              <td className="cell-mono">{r.port}</td>
              <td className="cell-mono" style={{ fontSize: "0.7rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                {r.source}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
    >
      {copied ? <Check size={12} style={{ color: "var(--green)" }} /> : <Copy size={12} />}
    </button>
  );
}
