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
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

function withForce(path, force) {
  return force ? `${path}?force=true` : path;
}

export const api = {
  // ─── Legacy access-key auth (kept during transition) ───────────────────────
  verifyAuth: (config) =>
    req("/api/auth/verify", { method: "POST", body: JSON.stringify(config) }),

  // ─── OTP auth ──────────────────────────────────────────────────────────────
  requestOTP:  (email) =>
    req("/api/otp/request", { method: "POST", body: JSON.stringify({ email }) }),
  verifyOTP:   (email, code) =>
    req("/api/otp/verify", { method: "POST", body: JSON.stringify({ email, code }) }),
  getMe:            () => req("/api/otp/me"),
  terminateSession: () => req("/api/otp/terminate", { method: "POST" }),
  switchRegion:     (region) => req("/api/otp/region", { method: "PUT", body: JSON.stringify({ region }) }),

  // ─── Access requests ───────────────────────────────────────────────────────
  submitRequest: (email, services, duration_hours) =>
    req("/api/requests", { method: "POST", body: JSON.stringify({ email, services, duration_hours }) }),
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
  getEKS:         (force = false) => req(withForce("/api/eks/clusters", force)),
  getRDS:         (force = false) => req(withForce("/api/rds/instances", force)),
  getRDSDetail:   (id, is_cluster = false) => req(`/api/rds/detail?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}`),
  getRDSMetrics:  (id, is_cluster = false, hours = 24) => req(`/api/rds/metrics?id=${encodeURIComponent(id)}&is_cluster=${is_cluster}&hours=${hours}`),
  getCost:        (force = false) => req(withForce("/api/cost/summary", force)),
  getAlarms:      (force = false) => req(withForce("/api/alarms", force)),
  getOpenSearch:  (force = false) => req(withForce("/api/opensearch/domains", force)),
  getMQ:          (force = false) => req(withForce("/api/mq/brokers", force)),
  getMQDetail:    (id) => req(`/api/mq/brokers/${id}`),
  getMQMetrics:   (id, hours = 24) => req(`/api/mq/brokers/${id}/metrics?hours=${hours}`),
  getElastiCache: (force = false) => req(withForce("/api/elasticache/clusters", force)),
  getECDetail:    (id, is_rg = true) => req(`/api/elasticache/detail?id=${id}&is_rg=${is_rg}`),
  getECMetrics:   (id, engine = "redis", hours = 24, is_rg = true) => req(`/api/elasticache/metrics?id=${id}&engine=${engine}&hours=${hours}&is_rg=${is_rg}`),
  getSecrets:      (force = false) => req(withForce("/api/secrets", force)),
  getSecretValue:  (arn) => req(`/api/secrets/value?arn=${encodeURIComponent(arn)}`),
  getSES:         (force = false) => req(withForce("/api/ses/overview", force)),
  getSESIdentities: (force = false) => req(withForce("/api/ses/identities", force)),
  getLBs:         (force = false) => req(withForce("/api/lb", force)),
  getLBDetail:    (id) => req(`/api/lb/${encodeURIComponent(id)}`),
  getLBMetrics:   (id, hours = 24) => req(`/api/lb/${encodeURIComponent(id)}/metrics?hours=${hours}`),

  logout:  () => req("/api/auth/logout", { method: "POST" }),
  health:  () => req("/api/health"),
};
