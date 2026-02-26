import { useState, useEffect, useCallback } from "react";
import { RefreshCw, X, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";

const STATE_COLORS = {
  active:           "state-green",
  provisioning:     "state-amber",
  "active_impaired": "state-amber",
  failed:           "state-red",
  unknown:          "state-gray",
};

const TYPE_COLORS = {
  APPLICATION: "state-blue",
  NETWORK:     "state-green",
  GATEWAY:     "state-amber",
  CLASSIC:     "state-gray",
};

const SCHEME_LABELS = {
  "internet-facing": "Internet",
  internal:          "Internal",
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

function HealthBadge({ healthy, unhealthy, total }) {
  if (total === 0) return <span className="metric-na">—</span>;
  const color = unhealthy > 0 ? "var(--red)" : "var(--green)";
  return (
    <span style={{ color, fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
      {healthy}/{total}
    </span>
  );
}

// ── Detail Drawer ─────────────────────────────────────────────────────────────
function DetailDrawer({ lb, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [detailError, setDetailError] = useState(null);
  const [openSections, setOpenSections] = useState({ overview: true, listeners: true, targets: true, tags: false });

  const toggle = (s) => setOpenSections((prev) => ({ ...prev, [s]: !prev[s] }));

  useEffect(() => {
    const id = lb.arn || lb.name;
    api.getLBDetail(id)
      .then(setDetail)
      .catch((e) => setDetailError(e.message))
      .finally(() => setLoadingDetail(false));
  }, [lb.arn, lb.name]);

  function Section({ id, title, children }) {
    const open = openSections[id];
    return (
      <div className="drawer-section">
        <div className="drawer-section-hdr" onClick={() => toggle(id)} style={{ cursor: "pointer" }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
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
          <div className="drawer-title">{lb.name}</div>
          <div className="drawer-subtitle">
            <span className={`state-pill ${TYPE_COLORS[lb.type] || "state-gray"}`}>{lb.type}</span>
            {" "}&nbsp;
            <span className={`state-pill ${STATE_COLORS[lb.state] || "state-gray"}`}>{lb.state}</span>
          </div>
        </div>
        <button className="drawer-close" onClick={onClose}><X size={16} /></button>
      </div>

      <div className="drawer-body">
        {loadingDetail && <div className="panel-loading">Loading…</div>}
        {detailError && <div className="panel-error">{detailError}</div>}
        {detail && (
          <>
            <Section id="overview" title="Overview">
              <table className="detail-kv-table">
                <tbody>
                  <tr><td>DNS Name</td><td className="cell-mono">{detail.dns}</td></tr>
                  <tr><td>Scheme</td><td>{SCHEME_LABELS[detail.scheme] || detail.scheme}</td></tr>
                  <tr><td>VPC</td><td className="cell-mono">{detail.vpc_id}</td></tr>
                  <tr><td>Availability Zones</td><td className="cell-mono">{(detail.azs || []).join(", ") || "—"}</td></tr>
                  {detail.ip_type && detail.ip_type !== "—" && (
                    <tr><td>IP Type</td><td>{detail.ip_type}</td></tr>
                  )}
                  {detail.arn && <tr><td>ARN</td><td className="cell-mono" style={{ wordBreak: "break-all", fontSize: "0.7rem" }}>{detail.arn}</td></tr>}
                  {detail.created_at && (
                    <tr><td>Created</td><td>{new Date(detail.created_at).toLocaleString()}</td></tr>
                  )}
                  {detail.state_reason && (
                    <tr><td>State Reason</td><td>{detail.state_reason}</td></tr>
                  )}
                </tbody>
              </table>
            </Section>

            <Section id="listeners" title={`Listeners (${(detail.listeners || []).length})`}>
              {detail.fetch_errors?.listeners && (
                <div className="panel-error" style={{ padding: "0.5rem 0", fontSize: "0.78rem" }}>
                  {detail.fetch_errors.listeners}
                </div>
              )}
              {!detail.fetch_errors?.listeners && (detail.listeners || []).length === 0 ? (
                <div className="panel-empty" style={{ padding: "1rem 0" }}>No listeners</div>
              ) : (
                <table className="data-table" style={{ width: "100%" }}>
                  <thead>
                    <tr>
                      <th>Protocol</th>
                      <th>Port</th>
                      {detail.generation === "classic" ? <th>→ Instance</th> : <th>Default Action</th>}
                      {detail.generation !== "classic" && <th>SSL Policy</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {detail.listeners.map((lst, idx) => (
                      <tr key={idx}>
                        <td>{lst.protocol}</td>
                        <td className="cell-mono">{lst.port ?? "—"}</td>
                        {detail.generation === "classic" ? (
                          <td className="cell-mono">{lst.instance_protocol}:{lst.instance_port}</td>
                        ) : (
                          <td style={{ fontSize: "0.78rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {lst.default_action}
                          </td>
                        )}
                        {detail.generation !== "classic" && (
                          <td className="cell-mono" style={{ fontSize: "0.7rem" }}>
                            {lst.ssl_policy || "—"}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            {detail.generation === "v2" && (
              <Section id="targets" title={`Target Groups (${(detail.target_groups || []).length})`}>
                {detail.fetch_errors?.target_groups && (
                  <div className="panel-error" style={{ padding: "0.5rem 0", fontSize: "0.78rem" }}>
                    {detail.fetch_errors.target_groups}
                  </div>
                )}
                {!detail.fetch_errors?.target_groups && (detail.target_groups || []).length === 0 ? (
                  <div className="panel-empty" style={{ padding: "1rem 0" }}>No target groups</div>
                ) : (
                  <table className="data-table" style={{ width: "100%" }}>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Protocol:Port</th>
                        <th>Type</th>
                        <th>Health</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.target_groups.map((tg, idx) => (
                        <tr key={idx}>
                          <td className="cell-bold">{tg.name}</td>
                          <td className="cell-mono">{tg.protocol}:{tg.port ?? "—"}</td>
                          <td>{tg.target_type}</td>
                          <td>
                            <HealthBadge healthy={tg.healthy} unhealthy={tg.unhealthy} total={tg.total} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Section>
            )}

            {detail.generation === "classic" && detail.instances && (
              <Section id="targets" title={`Instances (${detail.instances.length})`}>
                {detail.fetch_errors?.instances && (
                  <div className="panel-error" style={{ padding: "0.5rem 0", fontSize: "0.78rem" }}>
                    {detail.fetch_errors.instances}
                  </div>
                )}
                {!detail.fetch_errors?.instances && detail.instances.length === 0 ? (
                  <div className="panel-empty" style={{ padding: "1rem 0" }}>No instances</div>
                ) : (
                  <table className="data-table" style={{ width: "100%" }}>
                    <thead>
                      <tr><th>Instance ID</th><th>State</th><th>Description</th></tr>
                    </thead>
                    <tbody>
                      {detail.instances.map((inst, idx) => (
                        <tr key={idx}>
                          <td className="cell-mono">{inst.id}</td>
                          <td>
                            <span className={`state-pill ${inst.state === "InService" ? "state-green" : "state-red"}`}>
                              {inst.state}
                            </span>
                          </td>
                          <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>{inst.description || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Section>
            )}

            <Section id="tags" title={`Tags (${(detail.tags || []).length})`}>
              {detail.fetch_errors?.tags && (
                <div className="panel-error" style={{ padding: "0.5rem 0", fontSize: "0.78rem" }}>
                  {detail.fetch_errors.tags}
                </div>
              )}
              {!detail.fetch_errors?.tags && (detail.tags || []).length === 0 ? (
                <div className="panel-empty" style={{ padding: "1rem 0" }}>No tags</div>
              ) : (
                <table className="data-table" style={{ width: "100%" }}>
                  <thead><tr><th>Key</th><th>Value</th></tr></thead>
                  <tbody>
                    {detail.tags.map((t, idx) => (
                      <tr key={idx}>
                        <td className="cell-mono">{t.key}</td>
                        <td className="cell-mono">{t.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

// ── Main Panel ────────────────────────────────────────────────────────────────
export default function LBPanel() {
  const [typeFilter, setTypeFilter] = useState("all");
  const [schemeFilter, setSchemeFilter] = useState("all");
  const [sort, setSort] = useState({ col: "name", dir: "asc" });
  const [selected, setSelected] = useState(null);

  const fetcher = useCallback((force = false) => api.getLBs(force), []);
  const { data, loading, error, refresh, refreshing } = useData(fetcher);

  const lbs = data?.load_balancers || [];

  const filtered = lbs.filter((lb) => {
    if (typeFilter !== "all" && lb.type !== typeFilter) return false;
    if (schemeFilter !== "all" && lb.scheme !== schemeFilter) return false;
    return true;
  });

  const colVal = (lb, col) => {
    if (col === "name")   return lb.name?.toLowerCase() ?? "";
    if (col === "type")   return lb.type ?? "";
    if (col === "state")  return lb.state ?? "";
    if (col === "scheme") return lb.scheme ?? "";
    if (col === "azs")    return (lb.azs || []).length;
    return "";
  };

  function onSort(col) {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: "asc" }
    );
  }

  const sorted = [...filtered].sort((a, b) => {
    const av = colVal(a, sort.col);
    const bv = colVal(b, sort.col);
    const cmp = typeof av === "number" ? av - bv : av.localeCompare(bv);
    return sort.dir === "asc" ? cmp : -cmp;
  });

  const types   = [...new Set(lbs.map((lb) => lb.type))].sort();
  const schemes = [...new Set(lbs.map((lb) => lb.scheme))].sort();

  return (
    <div className="panel" style={{ position: "relative" }}>
      <div className="panel-header">
        <h2>Load Balancers</h2>
        <div className="panel-header-actions">
          {/* Type filter */}
          <select
            className="ses-filter-select"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            title="Filter by type"
          >
            <option value="all">All Types</option>
            {types.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>

          {/* Scheme filter */}
          <select
            className="ses-filter-select"
            value={schemeFilter}
            onChange={(e) => setSchemeFilter(e.target.value)}
            title="Filter by scheme"
          >
            <option value="all">All Schemes</option>
            {schemes.map((s) => (
              <option key={s} value={s}>{SCHEME_LABELS[s] || s}</option>
            ))}
          </select>

          <button
            className="refresh-btn"
            onClick={() => refresh(true)}
            disabled={refreshing}
            title="Force refresh"
          >
            <RefreshCw size={14} className={refreshing ? "spin" : ""} />
          </button>
        </div>
      </div>

      {loading  && <div className="panel-loading">Loading load balancers…</div>}
      {error    && <div className="panel-error">{error}</div>}
      {!loading && !error && sorted.length === 0 && (
        <div className="panel-empty">No load balancers found</div>
      )}

      {!loading && !error && sorted.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <SortTh col="name"   label="Name"   sort={sort} onSort={onSort} />
                <SortTh col="type"   label="Type"   sort={sort} onSort={onSort} />
                <SortTh col="state"  label="State"  sort={sort} onSort={onSort} />
                <SortTh col="scheme" label="Scheme" sort={sort} onSort={onSort} />
                <th>AZs</th>
                <th>DNS</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((lb) => (
                <tr
                  key={lb.arn || lb.name}
                  className={selected?.name === lb.name ? "row-selected" : ""}
                  onClick={() => setSelected(lb.name === selected?.name ? null : lb)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="cell-bold">{lb.name}</td>
                  <td>
                    <span className={`state-pill ${TYPE_COLORS[lb.type] || "state-gray"}`}>
                      {lb.type}
                    </span>
                  </td>
                  <td>
                    <span className={`state-pill ${STATE_COLORS[lb.state] || "state-gray"}`}>
                      {lb.state}
                    </span>
                  </td>
                  <td>{SCHEME_LABELS[lb.scheme] || lb.scheme}</td>
                  <td className="cell-mono">{(lb.azs || []).join(", ") || "—"}</td>
                  <td
                    className="cell-mono"
                    style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={lb.dns}
                  >
                    {lb.dns}
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
          <DetailDrawer lb={selected} onClose={() => setSelected(null)} />
        </>
      )}
    </div>
  );
}
