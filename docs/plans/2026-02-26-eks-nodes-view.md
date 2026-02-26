# EKS Nodes View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Nodes button to the EKS cluster drawer that opens a full-screen modal showing all EC2 nodes (managed + Karpenter) with pod counts, and per-node EC2 detail drawer with metrics.

**Architecture:** New backend endpoint discovers cluster nodes via EC2 tag filters and enriches with CW CPU + Container Insights pod counts. Frontend adds NodesModal (table + filter tabs) and a self-contained NodeDetailDrawer (EC2-style detail + metrics) inside EKSPanel.jsx.

**Tech Stack:** FastAPI + boto3 (backend), React 18 + lucide-react + recharts (frontend), existing `api` client, existing CSS classes.

---

### Task 1: Backend nodes endpoint

**Files:**
- Modify: `backend/app/routers/eks.py`

**Step 1: Add `_fetch_eks_nodes` function after `_fetch_eks_detail`**

The function discovers all EC2 instances belonging to the cluster using the tag `eks:cluster-name`, then enriches each with CPU (from CloudWatch `AWS/EC2`) and pod count (from CloudWatch `ContainerInsights`).

Add this function to `backend/app/routers/eks.py`:

```python
def _fetch_eks_nodes(session, cluster_name):
    ec2 = session.client("ec2")
    cw  = session.client("cloudwatch")

    # Primary filter: standard EKS tag applied to all node types (managed + Karpenter v0.33+)
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:eks:cluster-name", "Values": [cluster_name]},
        {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
    ])

    instances = []
    for reservation in resp.get("Reservations", []):
        instances.extend(reservation.get("Instances", []))

    # Fallback: self-managed nodes tagged with kubernetes.io/cluster/<name>=owned
    if not instances:
        resp2 = ec2.describe_instances(Filters=[
            {"Name": f"tag:kubernetes.io/cluster/{cluster_name}", "Values": ["owned"]},
            {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
        ])
        for reservation in resp2.get("Reservations", []):
            instances.extend(reservation.get("Instances", []))

    if not instances:
        return {"nodes": [], "count": 0}

    from datetime import datetime, timedelta, timezone

    def _uptime_hours(launch_time):
        if not launch_time:
            return None
        now = datetime.now(timezone.utc)
        if launch_time.tzinfo is None:
            launch_time = launch_time.replace(tzinfo=timezone.utc)
        return round((now - launch_time).total_seconds() / 3600, 1)

    def fetch_node(i):
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        instance_id = i["InstanceId"]
        name = tags.get("Name", "—")
        nodegroup   = tags.get("eks:nodegroup-name")
        karpenter   = tags.get("karpenter.sh/nodepool") or tags.get("karpenter.sh/provisioner-name")
        private_dns = i.get("PrivateDnsName", "")

        # CPU from EC2 namespace
        cpu = None
        try:
            pts = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"],
            )["Datapoints"]
            if pts:
                cpu = round(sorted(pts, key=lambda x: x["Timestamp"])[-1]["Average"], 1)
        except Exception:
            pass

        # Pod count from Container Insights (requires Container Insights to be enabled)
        pod_count = None
        try:
            pts = cw.get_metric_statistics(
                Namespace="ContainerInsights",
                MetricName="node_number_of_running_pods",
                Dimensions=[
                    {"Name": "ClusterName", "Value": cluster_name},
                    {"Name": "NodeName",    "Value": private_dns},
                ],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"],
            )["Datapoints"]
            if pts:
                pod_count = int(sorted(pts, key=lambda x: x["Timestamp"])[-1]["Average"])
        except Exception:
            pass

        launch_time = i.get("LaunchTime")
        return {
            "id":              instance_id,
            "name":            name,
            "state":           i["State"]["Name"],
            "type":            i["InstanceType"],
            "az":              i.get("Placement", {}).get("AvailabilityZone", "—"),
            "private_ip":      i.get("PrivateIpAddress"),
            "launch_time":     launch_time.isoformat() if launch_time else None,
            "uptime_hours":    _uptime_hours(launch_time),
            "cpu_percent":     cpu,
            "pod_count":       pod_count,
            "nodegroup_name":  nodegroup,
            "karpenter_pool":  karpenter,
        }

    with ThreadPoolExecutor(max_workers=10) as ex:
        nodes = list(ex.map(fetch_node, instances))

    return {"nodes": nodes, "count": len(nodes)}
```

**Step 2: Add the route after `get_eks_cluster_detail`**

```python
@router.get("/clusters/{name}/nodes")
def get_eks_cluster_nodes(name: str, request: Request):
    session, _ = get_session_and_config(request)
    try:
        return _fetch_eks_nodes(session, name)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 3: Verify the file is syntactically correct**

```bash
docker compose exec backend python -c "from app.routers.eks import router; print('OK')"
```
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/routers/eks.py
git commit -m "feat: add EKS cluster nodes endpoint with CPU and pod count metrics"
```

---

### Task 2: Add API client method

**Files:**
- Modify: `frontend/src/api/client.js`

**Step 1: Add `getEKSNodes` after `getEKSDetail` (line 68)**

Find:
```js
  getEKSDetail:   (name) => req(`/api/eks/clusters/${encodeURIComponent(name)}`),
```

Replace with:
```js
  getEKSDetail:   (name) => req(`/api/eks/clusters/${encodeURIComponent(name)}`),
  getEKSNodes:    (name) => req(`/api/eks/clusters/${encodeURIComponent(name)}/nodes`),
```

**Step 2: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat: add getEKSNodes API client method"
```

---

### Task 3: Frontend — Nodes button + NodesModal + NodeDetailDrawer

**Files:**
- Modify: `frontend/src/components/EKSPanel.jsx`

This is the main task. Three things to add:
1. `Nodes` button in `DetailDrawer` header
2. `NodesModal` — full-screen modal with filter tabs + table
3. `NodeDetailDrawer` + `NodeMetricsModal` — EC2-style detail for a single node

**Step 1: Add imports at the top of `EKSPanel.jsx`**

Current imports line 1-7:
```jsx
import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, Shield, Settings, Copy, Check,
  ChevronDown, ChevronRight, Server, Network, Lock,
} from "lucide-react";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
```

Replace with:
```jsx
import { useCallback, useState, useEffect } from "react";
import {
  RefreshCw, X, Info, Shield, Settings, Copy, Check,
  ChevronDown, ChevronRight, Server, Network, Lock, Users, BarChart2,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { api } from "../api/client";
import { useData } from "../hooks/useData";
```

**Step 2: Add helper functions after the `STATUS_COLORS` and `SortTh` blocks (after line ~29)**

Add these after the existing `SortTh` function:

```jsx
function formatBytes(bytes) {
  if (bytes == null) return "N/A";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
  return `${(bytes / k ** i).toFixed(1)} ${units[i]}`;
}

function formatUptime(hours) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${Math.floor(hours)}h`;
  const d = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function CpuBar({ value }) {
  if (value == null) return <span className="metric-na">N/A</span>;
  const color = value > 80 ? "var(--red)" : value > 50 ? "var(--amber)" : "var(--green)";
  return (
    <div className="cpu-bar-wrap">
      <div className="cpu-bar-track">
        <div className="cpu-bar-fill" style={{ width: `${value}%`, background: color }} />
      </div>
      <span className="cpu-label" style={{ color }}>{value}%</span>
    </div>
  );
}

function MetricBox({ label, value, color }) {
  return (
    <div className="ec2-metric-box">
      <div className="ec2-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ec2-metric-lbl">{label}</div>
    </div>
  );
}

function NodeMetricChart({ title, hours, series, merged, yDomain, yFmt, tipFmt, emptyNote }) {
  const primaryData = series[0].data;
  const isEmpty = !primaryData || primaryData.length === 0;
  const xFmt = (ts) => {
    const d = new Date(ts);
    if (hours <= 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  };
  const tickInterval = isEmpty ? 0 : Math.max(0, Math.floor(primaryData.length / 6) - 1);
  return (
    <div className="metrics-chart-card">
      <div className="metrics-chart-title">
        {title}
        <span style={{ display: "flex", gap: "0.75rem", float: "right" }}>
          {series.map(s => (
            <span key={s.key} className="metrics-legend-item">
              <span className="metrics-legend-dot" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </span>
      </div>
      {isEmpty ? (
        <div className="metrics-chart-empty">{emptyNote || "No data available"}</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={primaryData} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2840" vertical={false} />
            <XAxis dataKey="ts" tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={xFmt} interval={tickInterval} />
            <YAxis tick={{ fill: "#5a6a85", fontSize: 10 }} tickFormatter={yFmt || (v => v)} domain={yDomain || ["auto", "auto"]} width={56} />
            <Tooltip content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="chart-tooltip">
                  <div className="tooltip-label">{xFmt(label)}</div>
                  {payload.map((p, i) => {
                    const s = series.find(s => s.key === p.dataKey);
                    return <div key={i} style={{ color: p.stroke, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{s?.label}: {tipFmt ? tipFmt(p.value) : p.value}</div>;
                  })}
                </div>
              );
            }} />
            {series.map(s => (
              <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} dot={false} strokeWidth={1.5} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
```

**Step 3: Add `showNodes` state to `DetailDrawer` and wire the Nodes button**

In `DetailDrawer`, add `showNodes` state after the existing state declarations:
```jsx
const [showNodes, setShowNodes] = useState(false);
```

Find the drawer header close button:
```jsx
        <button className="drawer-close" onClick={onClose}><X size={16} /></button>
```

Replace with:
```jsx
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            className="refresh-btn"
            onClick={() => setShowNodes(true)}
            title="View Nodes"
            style={{ padding: "0.3rem 0.6rem", display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.75rem" }}
          >
            <Users size={13} /> Nodes
          </button>
          <button className="drawer-close" onClick={onClose}><X size={16} /></button>
        </div>
```

At the bottom of the `DetailDrawer` return (just before the closing `</div>` of the outer `detail-drawer` div), add:
```jsx
      {showNodes && (
        <NodesModal cluster={cluster} onClose={() => setShowNodes(false)} />
      )}
```

**Step 4: Add `NodesModal` component after `DetailDrawer`**

```jsx
function NodesModal({ cluster, onClose }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [filter, setFilter]   = useState("all");
  const [sort, setSort]       = useState({ col: "name", dir: "asc" });
  const [selectedNode, setSelectedNode] = useState(null);

  useEffect(() => {
    api.getEKSNodes(cluster.name)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [cluster.name]);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const nodes = data?.nodes || [];

  // Build filter tabs
  const nodegroups = [...new Set(nodes.filter(n => n.nodegroup_name).map(n => n.nodegroup_name))];
  const hasKarpenter = nodes.some(n => n.karpenter_pool);

  const filtered = nodes.filter(n => {
    if (filter === "all") return true;
    if (filter === "karpenter") return !!n.karpenter_pool;
    return n.nodegroup_name === filter;
  });

  const toggleSort = (col) =>
    setSort(s => s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: "asc" });

  const sorted = [...filtered].sort((a, b) => {
    const d = sort.dir === "asc" ? 1 : -1;
    const va = a[sort.col], vb = b[sort.col];
    if (typeof va === "string") return d * (va || "").localeCompare(vb || "");
    return d * ((va ?? 0) - (vb ?? 0));
  });

  const STATE_COLORS = { running: "state-green", stopped: "state-red", pending: "state-amber", stopping: "state-amber", terminated: "state-gray", "shutting-down": "state-gray" };

  return (
    <div className="metrics-modal">
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{cluster.name}</div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
            {loading ? "Loading…" : `${data?.count ?? 0} nodes`}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {/* Filter tabs */}
          {!loading && !error && (
            <div className="metrics-range-tabs">
              <button className={`metrics-range-tab ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>All</button>
              {nodegroups.map(ng => (
                <button key={ng} className={`metrics-range-tab ${filter === ng ? "active" : ""}`} onClick={() => setFilter(ng)}>
                  {ng}
                </button>
              ))}
              {hasKarpenter && (
                <button className={`metrics-range-tab ${filter === "karpenter" ? "active" : ""}`} onClick={() => setFilter("karpenter")}>
                  Karpenter
                </button>
              )}
            </div>
          )}
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading nodes…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {!loading && !error && sorted.length === 0 && (
          <div className="panel-empty">No nodes found</div>
        )}
        {!loading && !error && sorted.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh col="name"       label="Name"       sort={sort} onSort={toggleSort} />
                  <th>Instance ID</th>
                  <SortTh col="state"      label="State"      sort={sort} onSort={toggleSort} />
                  <SortTh col="type"       label="Type"       sort={sort} onSort={toggleSort} />
                  <SortTh col="az"         label="AZ"         sort={sort} onSort={toggleSort} />
                  <SortTh col="private_ip" label="Private IP" sort={sort} onSort={toggleSort} />
                  <SortTh col="uptime_hours" label="Uptime"   sort={sort} onSort={toggleSort} />
                  <SortTh col="cpu_percent"  label="CPU"      sort={sort} onSort={toggleSort} />
                  <SortTh col="pod_count"    label="Pods"     sort={sort} onSort={toggleSort} />
                  <th>Group</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(n => (
                  <tr key={n.id} className={`row-clickable ${selectedNode?.id === n.id ? "row-selected" : ""}`} onClick={() => setSelectedNode(n)}>
                    <td className="cell-bold">{n.name}</td>
                    <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{n.id}</td>
                    <td><span className={`state-pill ${STATE_COLORS[n.state] || "state-gray"}`}>{n.state}</span></td>
                    <td className="cell-mono">{n.type}</td>
                    <td>{n.az}</td>
                    <td className="cell-mono">{n.private_ip || "—"}</td>
                    <td className="cell-mono">{formatUptime(n.uptime_hours)}</td>
                    <td><CpuBar value={n.cpu_percent} /></td>
                    <td className="cell-mono">{n.pod_count != null ? n.pod_count : <span className="metric-na">—</span>}</td>
                    <td>
                      {n.karpenter_pool
                        ? <span className="state-pill state-amber" style={{ fontSize: "0.65rem" }}>⚡ {n.karpenter_pool}</span>
                        : n.nodegroup_name
                          ? <span className="state-pill state-green" style={{ fontSize: "0.65rem" }}>{n.nodegroup_name}</span>
                          : <span className="metric-na">—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedNode && (
        <NodeDetailDrawer node={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
    </div>
  );
}
```

**Step 5: Add `NodeDetailDrawer` and `NodeMetricsModal` after `NodesModal`**

```jsx
function NodeDetailDrawer({ node, onClose }) {
  const [detail, setDetail]     = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [sgExpanded, setSgExpanded] = useState({});
  const [showMetrics, setShowMetrics] = useState(false);

  useEffect(() => {
    setDetail(null); setLoading(true); setError(null); setSgExpanded({});
    api.getEC2Detail(node.id)
      .then(d => { setDetail(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [node.id]);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const STATE_COLORS = { running: "state-green", stopped: "state-red", pending: "state-amber", stopping: "state-amber", terminated: "state-gray", "shutting-down": "state-gray" };

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="detail-drawer">
        <div className="drawer-header">
          <div>
            <div className="drawer-title">{node.name}</div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.35rem" }}>
              <span className={`state-pill ${STATE_COLORS[node.state] || "state-gray"}`}>{node.state}</span>
              <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{node.id}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <button
              className="refresh-btn"
              onClick={() => setShowMetrics(true)}
              title="View Metrics"
              style={{ padding: "0.3rem 0.6rem", display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.75rem" }}
            >
              <BarChart2 size={13} /> Metrics
            </button>
            <button className="drawer-close" onClick={onClose}><X size={14} /></button>
          </div>
        </div>

        <div className="drawer-body">
          {loading && <div className="panel-loading" style={{ padding: "2rem 0" }}>Loading details…</div>}
          {error   && <div className="panel-error">{error}</div>}
          {detail  && (
            <>
              <div className="drawer-section">
                <div className="drawer-section-hdr">Overview</div>
                <div className="drawer-meta-grid">
                  <MetaItem label="Type"         value={detail.type} />
                  <MetaItem label="Architecture" value={detail.architecture} />
                  <MetaItem label="AZ"           value={detail.az} />
                  <MetaItem label="VPC"          value={detail.vpc_id} />
                  <MetaItem label="Subnet"       value={detail.subnet_id} />
                  <MetaItem label="Key Pair"     value={detail.key_name} />
                  <MetaItem label="Private IP"   value={detail.private_ip || "—"} />
                  <MetaItem label="Public IP"    value={detail.public_ip  || "—"} />
                  <MetaItem label="IAM Profile"  value={detail.iam_profile || "—"} />
                  <MetaItem label="AMI"          value={detail.ami_id} />
                  <MetaItem label="Uptime"       value={formatUptime(detail.uptime_hours)} />
                  <MetaItem label="Launched"     value={detail.launch_time ? new Date(detail.launch_time).toLocaleString() : "—"} />
                  {node.nodegroup_name && <MetaItem label="Node Group"  value={node.nodegroup_name} />}
                  {node.karpenter_pool && <MetaItem label="Karpenter Pool" value={node.karpenter_pool} />}
                  {node.pod_count != null && <MetaItem label="Running Pods" value={String(node.pod_count)} />}
                </div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-hdr">Metrics (last 5 min avg)</div>
                <div className="ec2-metrics-grid">
                  <MetricBox label="CPU"       value={detail.metrics.cpu_percent != null ? `${detail.metrics.cpu_percent}%` : "N/A"} color={detail.metrics.cpu_percent != null ? (detail.metrics.cpu_percent > 80 ? "var(--red)" : detail.metrics.cpu_percent > 50 ? "var(--amber)" : "var(--green)") : undefined} />
                  <MetricBox label="Net In"    value={formatBytes(detail.metrics.network_in_bytes)} />
                  <MetricBox label="Net Out"   value={formatBytes(detail.metrics.network_out_bytes)} />
                  <MetricBox label="Disk Read" value={formatBytes(detail.metrics.disk_read_bytes)} />
                  <MetricBox label="Disk Write" value={formatBytes(detail.metrics.disk_write_bytes)} />
                </div>
              </div>

              {detail.security_groups?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Security Groups ({detail.security_groups.length})</div>
                  {detail.security_groups.map(sg => (
                    <div key={sg.id} className="ec2-sg-block">
                      <button className="ec2-sg-hdr" onClick={() => setSgExpanded(p => ({ ...p, [sg.id]: !p[sg.id] }))}>
                        {sgExpanded[sg.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        <span style={{ fontWeight: 600 }}>{sg.name}</span>
                        <span className="cell-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginLeft: "0.4rem" }}>{sg.id}</span>
                      </button>
                      {sgExpanded[sg.id] && (
                        <div className="ec2-sg-rules">
                          <RulesTable label="Inbound"  rules={sg.inbound}  />
                          <RulesTable label="Outbound" rules={sg.outbound} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {detail.volumes?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Storage ({detail.volumes.length} volume{detail.volumes.length > 1 ? "s" : ""})</div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead><tr><th>Device</th><th>Volume ID</th><th>Size</th><th>Type</th><th>State</th><th>Encrypted</th></tr></thead>
                      <tbody>
                        {detail.volumes.map(v => (
                          <tr key={v.id}>
                            <td className="cell-mono">{v.device}</td>
                            <td className="cell-mono" style={{ fontSize: "0.72rem" }}>{v.id}</td>
                            <td className="cell-mono">{v.size_gb} GB</td>
                            <td className="cell-mono">{v.type}{v.iops ? ` · ${v.iops} IOPS` : ""}</td>
                            <td><span className={`state-pill ${v.state === "in-use" ? "state-green" : "state-gray"}`}>{v.state}</span></td>
                            <td><span className={`state-pill ${v.encrypted ? "state-green" : "state-gray"}`}>{v.encrypted ? "Yes" : "No"}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {detail.tags?.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-hdr">Tags ({detail.tags.length})</div>
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
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {showMetrics && <NodeMetricsModal node={node} onClose={() => setShowMetrics(false)} />}
    </>
  );
}

function NodeMetricsModal({ node, onClose }) {
  const [hours, setHours]   = useState(24);
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  useEffect(() => {
    setData(null); setLoading(true); setError(null);
    api.getEC2Metrics(node.id, hours)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [node.id, hours]);

  function merge(s1, k1, s2, k2) {
    const map = {};
    (s1 || []).forEach(p => { map[p.ts] = { ts: p.ts, [k1]: p.v }; });
    (s2 || []).forEach(p => { if (map[p.ts]) map[p.ts][k2] = p.v; else map[p.ts] = { ts: p.ts, [k2]: p.v }; });
    return Object.values(map).sort((a, b) => a.ts < b.ts ? -1 : 1);
  }

  const m = data?.metrics;
  const networkData = m ? merge(m.network_in, "in", m.network_out, "out") : [];
  const diskData    = m ? merge(m.disk_read, "read", m.disk_write, "write") : [];

  return (
    <div className="metrics-modal" style={{ zIndex: 1100 }}>
      <div className="metrics-header">
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem" }}>{node.name}</div>
          <div className="cell-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>{node.id}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="metrics-range-tabs">
            {[1, 6, 24, 72].map(h => (
              <button key={h} className={`metrics-range-tab ${hours === h ? "active" : ""}`} onClick={() => setHours(h)}>{h}h</button>
            ))}
          </div>
          <button className="drawer-close" onClick={onClose}><X size={14} /></button>
        </div>
      </div>
      <div className="metrics-body">
        {loading && <div className="panel-loading">Loading metrics…</div>}
        {error   && <div className="panel-error">{error}</div>}
        {data && (
          <div className="metrics-charts-grid">
            <NodeMetricChart title="CPU Utilization" hours={hours} series={[{ data: m.cpu, key: "v", color: "var(--green)", label: "CPU %" }]} yDomain={[0, 100]} yFmt={v => `${v}%`} tipFmt={v => `${v}%`} />
            <NodeMetricChart title="Memory" hours={hours} series={[{ data: m.memory, key: "v", color: "var(--blue)", label: "Mem %" }]} yDomain={[0, 100]} yFmt={v => `${v}%`} tipFmt={v => `${v}%`} emptyNote="No data — requires CloudWatch Agent" />
            <NodeMetricChart title="Network Traffic (bytes / period)" hours={hours} series={[{ data: networkData, key: "in", color: "var(--amber)", label: "In" }, { data: networkData, key: "out", color: "var(--blue)", label: "Out" }]} merged yFmt={formatBytes} tipFmt={formatBytes} />
            <NodeMetricChart title="Disk I/O (bytes / period)" hours={hours} series={[{ data: diskData, key: "read", color: "var(--amber)", label: "Read" }, { data: diskData, key: "write", color: "var(--red)", label: "Write" }]} merged yFmt={formatBytes} tipFmt={formatBytes} />
          </div>
        )}
      </div>
    </div>
  );
}
```

**Step 6: Verify frontend compiles**

```bash
docker compose logs frontend --tail=20
```
Expected: No compilation errors.

**Step 7: Commit**

```bash
git add frontend/src/components/EKSPanel.jsx
git commit -m "feat: add EKS nodes view with filter tabs, pod counts, and per-node EC2 detail"
```
