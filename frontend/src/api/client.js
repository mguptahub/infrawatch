const BASE = process.env.REACT_APP_API_URL || "";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("session-expired"));
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    // FastAPI uses `detail`; slowapi rate-limit responses use `error`
    throw new Error(err.detail || err.error || "Request failed");
  }
  return res.json();
}

function withForce(path, force) {
  return force ? `${path}?force=true` : path;
}

export const api = {
  // ─── OTP auth ──────────────────────────────────────────────────────────────
  requestOTP:  (email) =>
    req("/api/otp/request", { method: "POST", body: JSON.stringify({ email }) }),
  verifyOTP:   (email, code) =>
    req("/api/otp/verify", { method: "POST", body: JSON.stringify({ email, code }) }),
  getMe:            () => req("/api/otp/me"),
  terminateSession: () => req("/api/otp/terminate", { method: "POST" }),
  switchRegion:     (region) => req("/api/otp/region", { method: "PUT", body: JSON.stringify({ region }) }),

  // ─── Access requests ───────────────────────────────────────────────────────
  getRequestConfig: (email) => req(`/api/requests/config?email=${encodeURIComponent(email)}`),
  submitRequest: (email, services, duration_hours) =>
    req("/api/requests", { method: "POST", body: JSON.stringify({ email, services, duration_hours }) }),
  verifyAndSubmitRequest: (email, otp_code, services, duration_hours) =>
    req("/api/requests/verify", {
      method: "POST",
      body: JSON.stringify({ email, otp_code, services, duration_hours }),
    }),
  getApprovalRequest: (token) => req(`/api/requests/approve/${token}`),
  sendApprovalOTP:    (token, email) =>
    req("/api/requests/approve/otp", { method: "POST", body: JSON.stringify({ token, email }) }),
  submitApproval:     (token, email, code, action, denial_reason) =>
    req("/api/requests/approve", {
      method: "POST",
      body: JSON.stringify({ token, email, code, action, denial_reason }),
    }),
  getMyRequests: () => req("/api/requests/my"),

  // ─── Admin ─────────────────────────────────────────────────────────────────
  adminListUsers:    () => req("/api/admin/users"),
  adminCreateUser:   (data) => req("/api/admin/users", { method: "POST", body: JSON.stringify(data) }),
  adminUpdateUser:   (id, data) => req(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  adminListServices: () => req("/api/admin/services"),
  adminListRequests: (status) => req(`/api/admin/requests${status ? `?status=${status}` : ""}`),
  adminAction:       (id, action, denial_reason) =>
    req(`/api/admin/requests/${id}/action`, {
      method: "POST",
      body: JSON.stringify({ action, denial_reason }),
    }),

  // ─── AWS data panels ───────────────────────────────────────────────────────
  getEC2:         (force = false) => req(withForce("/api/ec2/instances", force)),
  getEC2Detail:   (id) => req(`/api/ec2/instances/${id}`),
  getEC2Metrics:  (id, hours = 24) => req(`/api/ec2/instances/${id}/metrics?hours=${hours}`),
  refreshEC2:     () => req("/api/ec2/refresh", { method: "POST" }),
  ec2RefreshStreamUrl:   () => `${BASE}/api/ec2/refresh/stream`,
  getEKS:         (force = false) => req(withForce("/api/eks/clusters", force)),
  getEKSDetail:   (name) => req(`/api/eks/clusters/${encodeURIComponent(name)}`),
  getEKSNodes:    (name) => req(`/api/eks/clusters/${encodeURIComponent(name)}/nodes`),
  getEKSMetrics:  (name, hours = 24, region = null) =>
    req(`/api/eks/clusters/${encodeURIComponent(name)}/metrics?hours=${hours}${region ? `&region=${encodeURIComponent(region)}` : ""}`),
  refreshEKS:     () => req("/api/eks/refresh", { method: "POST" }),
  eksRefreshStreamUrl:   () => `${BASE}/api/eks/refresh/stream`,
  getRDS:         (force = false) => req(withForce("/api/rds/instances", force)),
  getRDSDetail:   (id, is_cluster = false) => req(`/api/rds/detail?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}`),
  getRDSMetrics:  (id, is_cluster = false, hours = 24) => req(`/api/rds/metrics?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}&hours=${hours}`),
  refreshRDS:     () => req("/api/rds/refresh", { method: "POST" }),
  rdsRefreshStreamUrl: () => `${BASE}/api/rds/refresh/stream`,
  getDocDB:       (force = false) => req(withForce("/api/docdb/instances", force)),
  getDocDBDetail: (id, is_cluster = false) => req(`/api/docdb/detail?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}`),
  getDocDBMetrics:(id, is_cluster = false, hours = 24) => req(`/api/docdb/metrics?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}&hours=${hours}`),
  refreshDocDB:   () => req("/api/docdb/refresh", { method: "POST" }),
  docdbRefreshStreamUrl: () => `${BASE}/api/docdb/refresh/stream`,
  getCost:        (force = false) => req(withForce("/api/cost/summary", force)),
  getOpenSearch:       (force = false) => req(withForce("/api/opensearch/domains", force)),
  getOpenSearchDetail: (name) => req(`/api/opensearch/detail?name=${encodeURIComponent(name)}`),
  getOpenSearchMetrics:(name, hours = 24) => req(`/api/opensearch/metrics?name=${encodeURIComponent(name)}&hours=${hours}`),
  refreshOpenSearch:   () => req("/api/opensearch/refresh", { method: "POST" }),
  opensearchRefreshStreamUrl: () => `${BASE}/api/opensearch/refresh/stream`,
  getMQ:          (force = false) => req(withForce("/api/mq/brokers", force)),
  getMQDetail:    (id) => req(`/api/mq/brokers/${id}`),
  getMQMetrics:   (id, hours = 24) => req(`/api/mq/brokers/${id}/metrics?hours=${hours}`),
  refreshMQ:      () => req("/api/mq/refresh", { method: "POST" }),
  mqRefreshStreamUrl: () => `${BASE}/api/mq/refresh/stream`,
  getElastiCache: (force = false) => req(withForce("/api/elasticache/clusters", force)),
  getECDetail:    (id, is_rg = true) => req(`/api/elasticache/detail?id=${id}&is_rg=${is_rg}`),
  getECMetrics:   (id, engine = "redis", hours = 24, is_rg = true) => req(`/api/elasticache/metrics?id=${id}&engine=${engine}&hours=${hours}&is_rg=${is_rg}`),
  refreshElastiCache: () => req("/api/elasticache/refresh", { method: "POST" }),
  elasticacheRefreshStreamUrl: () => `${BASE}/api/elasticache/refresh/stream`,
  getSecrets:      (force = false) => req(withForce("/api/secrets", force)),
  getSecretValue:  (arn) => req(`/api/secrets/value?arn=${encodeURIComponent(arn)}`),
  refreshSecrets:  () => req("/api/secrets/refresh", { method: "POST" }),
  secretsRefreshStreamUrl: () => `${BASE}/api/secrets/refresh/stream`,
  getIAMUsers:     (force = false) => req(withForce("/api/iam/users", force)),
  getIAMUserDetail:(username) => req(`/api/iam/users/${encodeURIComponent(username)}`),
  refreshIAM:      () => req("/api/iam/refresh", { method: "POST" }),
  iamRefreshStreamUrl: () => `${BASE}/api/iam/refresh/stream`,
  getSES:         (force = false) => req(withForce("/api/ses/overview", force)),
  getSESIdentities: (force = false) => req(withForce("/api/ses/identities", force)),
  getSESSuppressionSearch: (q, reason) =>
    req(`/api/ses/suppression/search?${new URLSearchParams({ q: q || "", ...(reason && { reason }) }).toString()}`),
  /**
   * Stream removal: POST emails, read NDJSON stream, call onEvent({ email, removed, error? }) for each line.
   * Returns a promise that resolves when the stream ends; rejects on HTTP error.
   */
  async postSESSuppressionRemoveStream(emails, onEvent) {
    const res = await fetch(`${BASE}/api/ses/suppression/remove/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ emails }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) window.dispatchEvent(new CustomEvent("session-expired"));
      throw new Error(err.detail || err.error || "Request failed");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const s = line.trim();
        if (!s) continue;
        try {
          onEvent(JSON.parse(s));
        } catch (_) { /* skip malformed line */ }
      }
      if (done) break;
    }
  },
  getLBs:         (force = false) => req(withForce("/api/lb", force)),
  getLBDetail:    (id) => req(`/api/lb/${encodeURIComponent(id)}`),
  getLBMetrics:   (id, hours = 24) => req(`/api/lb/${encodeURIComponent(id)}/metrics?hours=${hours}`),
  refreshLB:      () => req("/api/lb/refresh", { method: "POST" }),
  lbRefreshStreamUrl:    () => `${BASE}/api/lb/refresh/stream`,

  // Dashboard panels
  getDashboardPanels:    () => req("/api/dashboard/panels"),
  createDashboardPanel:  (body) => req("/api/dashboard/panels", { method: "POST", body: JSON.stringify(body) }),
  updateDashboardPanel:  (id, body) => req(`/api/dashboard/panels/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteDashboardPanel:  async (id) => {
    const res = await fetch(`${BASE}/api/dashboard/panels/${id}`, { method: "DELETE", credentials: "include" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) window.dispatchEvent(new CustomEvent("session-expired"));
      throw new Error(err.detail || err.error || "Request failed");
    }
  },
  reorderDashboardPanels: (panel_ids) => req("/api/dashboard/panels/reorder", { method: "POST", body: JSON.stringify({ panel_ids }) }),

  // Dashboard widgets (Phase 3)
  getDashboardWidgets:   () => req("/api/dashboard/widgets"),
  createDashboardWidget: (body) => req("/api/dashboard/widgets", { method: "POST", body: JSON.stringify(body) }),
  updateDashboardWidget: (id, body) => req(`/api/dashboard/widgets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteDashboardWidget: async (id) => {
    const res = await fetch(`${BASE}/api/dashboard/widgets/${id}`, { method: "DELETE", credentials: "include" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) window.dispatchEvent(new CustomEvent("session-expired"));
      throw new Error(err.detail || err.error || "Request failed");
    }
  },
  getDashboardWidgetData: (id, range = 24) => req(`/api/dashboard/widgets/${id}/data?range=${range}`),

  // ─── Alerts ─────────────────────────────────────────────────────────────────
  getAlertsSummary:      () => req("/api/alerts/summary"),
  getAlarms:             (serviceType, state) => {
    const params = new URLSearchParams();
    if (serviceType) params.set("service_type", serviceType);
    if (state) params.set("state", state);
    const qs = params.toString();
    return req(`/api/alerts/alarms${qs ? `?${qs}` : ""}`);
  },
  getHealthEvents:       (serviceType, status) => {
    const params = new URLSearchParams();
    if (serviceType) params.set("service_type", serviceType);
    if (status) params.set("status", status);
    const qs = params.toString();
    return req(`/api/alerts/health${qs ? `?${qs}` : ""}`);
  },
  getResourceAlarms:     (resourceId) => req(`/api/alerts/resource/${encodeURIComponent(resourceId)}`),

  logout:  () => req("/api/otp/terminate", { method: "POST" }),
  health:  () => req("/api/health"),
};
