import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, X, Pencil, Copy, GripVertical, ChevronDown, ChevronRight, Maximize } from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import {
  DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors,
} from "@dnd-kit/core";
import {
  arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { METRICS_EMPTY_NOTE } from "../constants";

// Services that have metrics in collected_metrics; map to list API and metric options.
const WIDGET_SERVICE_CONFIG = {
  ec2: {
    label: "EC2",
    listKey: "getEC2",
    resourceIdKey: "id",
    resourceNameKey: "name",
    regionFromItem: (item) => item.region || item.attributes?.region,
    metrics: [
      { value: "CPUUtilization", label: "CPU %" },
      { value: "NetworkIn", label: "Network In" },
      { value: "NetworkOut", label: "Network Out" },
      { value: "DiskReadBytes", label: "Disk Read (bytes)" },
      { value: "DiskWriteBytes", label: "Disk Write (bytes)" },
      { value: "EBSReadBytes", label: "EBS Read (bytes)" },
      { value: "EBSWriteBytes", label: "EBS Write (bytes)" },
    ],
  },
  eks: {
    label: "EKS",
    listKey: "getEKS",
    resourceIdKey: "name",
    resourceNameKey: "name",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "apiserver_request_total", label: "API Server Requests (total)" },
      { value: "apiserver_request_total_4XX", label: "API Server 4XX" },
      { value: "apiserver_request_total_429", label: "API Server 429" },
      { value: "apiserver_request_total_5XX", label: "API Server 5XX" },
      { value: "apiserver_flowcontrol_current_executing_seats", label: "Flow Control Executing Seats" },
      { value: "apiserver_storage_size_bytes", label: "etcd Storage (bytes)" },
      { value: "scheduler_pending_pods", label: "Scheduler Pending Pods" },
      { value: "scheduler_schedule_attempts_total", label: "Scheduler Schedule Attempts" },
      { value: "scheduler_schedule_attempts_SCHEDULED", label: "Scheduler SCHEDULED" },
      { value: "scheduler_schedule_attempts_UNSCHEDULABLE", label: "Scheduler UNSCHEDULABLE" },
    ],
  },
  elb: {
    label: "Load Balancers",
    listKey: "getLBs",
    resourceIdKey: "arn",
    getResourceId: (item) => item.arn || item.name,
    resourceNameKey: "name",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "ProcessedBytes", label: "Traffic (Processed Bytes)" },
      { value: "RequestCount", label: "Request Count" },
      { value: "ActiveFlowCount", label: "Active Flows (NLB)" },
      { value: "EstimatedProcessedBytes", label: "Traffic (Classic LB)" },
    ],
  },
  databases: {
    label: "RDS",
    listKey: "getRDS",
    listTransform: (res) => [...(res?.instances || []), ...(res?.clusters || [])],
    resourceIdKey: "id",
    resourceNameKey: "id",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "CPUUtilization", label: "CPU %" },
      { value: "DatabaseConnections", label: "Connections" },
      { value: "FreeStorageSpace", label: "Free Storage" },
      { value: "FreeableMemory", label: "Free Memory" },
      { value: "ReadIOPS", label: "Read IOPS" },
      { value: "WriteIOPS", label: "Write IOPS" },
      { value: "ReadLatency", label: "Read Latency (ms)" },
      { value: "WriteLatency", label: "Write Latency (ms)" },
    ],
  },
  docdb: {
    label: "DocumentDB",
    listKey: "getDocDB",
    listTransform: (res) => [...(res?.instances || []), ...(res?.clusters || [])],
    resourceIdKey: "id",
    resourceNameKey: "id",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "CPUUtilization", label: "CPU %" },
      { value: "DatabaseConnections", label: "DB Connections" },
      { value: "FreeStorageSpace", label: "Free Storage (GB)" },
      { value: "ReadIOPS", label: "Read IOPS" },
      { value: "WriteIOPS", label: "Write IOPS" },
    ],
  },
  opensearch: {
    label: "OpenSearch",
    listKey: "getOpenSearch",
    resourceIdKey: "name",
    resourceNameKey: "name",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "CPUUtilization", label: "CPU %" },
      { value: "JVMMemoryPressure", label: "JVM Memory %" },
      { value: "FreeStorageSpace", label: "Free Storage (GB)" },
      { value: "SysMemoryUtilization", label: "System Memory %" },
      { value: "SearchRate", label: "Search Rate" },
      { value: "IndexingRate", label: "Indexing Rate" },
    ],
  },
  elasticache: {
    label: "ElastiCache",
    listKey: "getElastiCache",
    listTransform: (res) => [...(res?.replication_groups || []), ...(res?.standalone_clusters || [])],
    resourceIdKey: "id",
    resourceNameKey: "id",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "CPUUtilization", label: "CPU %" },
      { value: "CurrConnections", label: "Current Connections" },
      { value: "DatabaseMemoryUsagePercentage", label: "Memory Usage %" },
      { value: "CacheHits", label: "Cache Hits" },
    ],
  },
  mq: {
    label: "MQ",
    listKey: "getMQ",
    resourceIdKey: "id",
    resourceNameKey: "name",
    regionFromItem: (item) => item.region,
    metrics: [
      { value: "cpu", label: "CPU Utilization" },
      { value: "memory", label: "Memory Usage" },
      { value: "connections", label: "Total Connections" },
      { value: "messages", label: "Total Messages / Queue Depth" },
      { value: "queues", label: "Queues" },
      { value: "memory_heap", label: "Heap Usage (ActiveMQ)" },
      { value: "storage_free", label: "Free Disk (RabbitMQ)" },
      { value: "storage_usage", label: "Store % (ActiveMQ)" },
    ],
  },
};

const CHART_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#a855f7"];

function getMetricLabel(serviceType, metricName) {
  const config = WIDGET_SERVICE_CONFIG[serviceType];
  const m = config?.metrics?.find((x) => x.value === metricName);
  return m?.label ?? metricName;
}

/** One chart for a single metric (own Y-axis). data = [{ ts, v }]. */
function SingleMetricChart({ data, label, rangeHours, color, height = 180, large = false }) {
  const xFmt = (ts) => {
    const d = new Date(ts);
    return rangeHours <= 24
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  };
  const isEmpty = !data || data.length === 0;
  const tickInterval = isEmpty ? 0 : Math.max(0, Math.floor(data.length / 5) - 1);
  const tickFill = "#8b9cc0";
  const tickSize = large ? 12 : 8;
  const yWidth = large ? 50 : 36;
  if (isEmpty) {
    return (
      <div className="widget-single-metric">
        <div className="widget-single-metric-label">{label}</div>
        <div className="widget-chart-placeholder">{METRICS_EMPTY_NOTE}</div>
      </div>
    );
  }
  return (
    <div className="widget-single-metric">
      <div className="widget-single-metric-label">{label}</div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 2, right: 6, left: 0, bottom: 0 }} style={{ cursor: "crosshair" }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2840" vertical={false} />
          <XAxis
            dataKey="ts"
            tick={{ fill: tickFill, fontSize: tickSize }}
            tickFormatter={xFmt}
            interval={tickInterval}
          />
          <YAxis tick={{ fill: tickFill, fontSize: tickSize }} width={yWidth} />
          <Tooltip
            content={({ active, payload, label: t }) => {
              if (!active || !payload?.length) return null;
              const v = payload[0]?.value;
              return (
                <div className="chart-tooltip">
                  <div className="tooltip-label">{xFmt(t)}</div>
                  <div style={{ color: color || "#22c55e", fontSize: "0.75rem" }}>
                    {typeof v === "number" ? v.toFixed(2) : v}
                  </div>
                </div>
              );
            }}
          />
          <Line
            type="monotone"
            dataKey="v"
            stroke={color || CHART_COLORS[0]}
            dot={false}
            strokeWidth={1.5}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function MaximizedWidgetModal({ widget, metricsData, rangeHours, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const metricNames = widget.metric_names || [];
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="widget-maximized-modal" onClick={(e) => e.stopPropagation()}>
        <div className="widget-maximized-header">
          <div className="widget-card-heading">
            <span className="widget-card-title">{widget.title || widget.resource_id}</span>
            <span className="widget-card-subheading">{widget.service_type} · {widget.region}</span>
          </div>
          <button type="button" className="modal-close" onClick={onClose}><X size={20} /></button>
        </div>
        <div className="widget-maximized-body">
          {metricNames.map((metricName, i) => {
            const points = (metricsData && metricsData[metricName]) || [];
            const data = points.map((p) => ({ ts: p.ts, v: p.v })).sort((a, b) => a.ts.localeCompare(b.ts));
            return (
              <SingleMetricChart
                key={metricName}
                data={data}
                label={getMetricLabel(widget.service_type, metricName)}
                rangeHours={rangeHours}
                color={CHART_COLORS[i % CHART_COLORS.length]}
                height={360}
                large
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function WidgetCard({ widget, rangeHours, onDelete, onEdit, onClone, onDataUpdated, onDeleteError }) {
  const [deleting, setDeleting] = useState(false);
  const [metricsData, setMetricsData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [maximized, setMaximized] = useState(false);

  const metricNamesKey = JSON.stringify(widget.metric_names || []);

  const fetchData = useCallback((showLoading = false) => {
    if (showLoading) {
      setLoading(true);
      setError(null);
    }
    api
      .getDashboardWidgetData(widget.id, rangeHours)
      .then((res) => {
        setMetricsData(res?.metrics ?? {});
        onDataUpdated?.();
      })
      .catch((e) => setError(e.message))
      .finally(() => { if (showLoading) setLoading(false); });
  }, [widget.id, rangeHours, metricNamesKey, onDataUpdated]);

  useEffect(() => {
    fetchData(true);
  }, [fetchData]);

  useEffect(() => {
    const interval = setInterval(() => fetchData(false), 2 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleDelete = () => {
    if (deleting) return;
    setDeleting(true);
    api
      .deleteDashboardWidget(widget.id)
      .then(() => onDelete())
      .catch((e) => {
        setDeleting(false);
        onDeleteError?.(e.message);
      });
  };

  const handleClone = () => {
    if (!onClone) return;
    onClone(widget);
  };

  const metricNames = widget.metric_names || [];

  return (
    <div className="widget-card">
      <div className="widget-card-header">
        <div className="widget-card-heading">
          <span className="widget-card-title" title={widget.resource_id}>
            {widget.title || widget.resource_id}
          </span>
          <span className="widget-card-subheading">
            {widget.service_type} · {widget.region}
          </span>
        </div>
        <div className="widget-card-actions">
          <button
            type="button"
            className="widget-card-action"
            onClick={() => setMaximized(true)}
            title="Maximize"
          >
            <Maximize size={14} />
          </button>
          <button
            type="button"
            className="widget-card-action"
            onClick={handleClone}
            title="Clone widget"
          >
            <Copy size={14} />
          </button>
          <button
            type="button"
            className="widget-card-action"
            onClick={() => onEdit(widget)}
            title="Edit widget"
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            className="widget-card-delete"
            onClick={handleDelete}
            disabled={deleting}
            title="Remove widget"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      <div className="widget-card-charts">
        {loading && <div className="widget-chart-placeholder">Loading…</div>}
        {error && <div className="widget-chart-placeholder widget-chart-error">{error}</div>}
        {!loading && !error && metricNames.length === 0 && (
          <div className="widget-chart-placeholder">No metrics selected.</div>
        )}
        {!loading && !error &&
          metricNames.map((metricName, i) => {
            const points = (metricsData && metricsData[metricName]) || [];
            const data = points.map((p) => ({ ts: p.ts, v: p.v })).sort((a, b) => a.ts.localeCompare(b.ts));
            return (
              <SingleMetricChart
                key={metricName}
                data={data}
                label={getMetricLabel(widget.service_type, metricName)}
                rangeHours={rangeHours}
                color={CHART_COLORS[i % CHART_COLORS.length]}
              />
            );
          })}
      </div>
      {maximized && (
        <MaximizedWidgetModal
          widget={widget}
          metricsData={metricsData}
          rangeHours={rangeHours}
          onClose={() => setMaximized(false)}
        />
      )}
    </div>
  );
}

function AddWidgetModal({ panelId, prefill, onClose, onAdded, approvedServices, sessionRegion }) {
  const [serviceType, setServiceType] = useState(prefill?.service_type ?? "");
  const [resources, setResources] = useState([]);
  const [resourceId, setResourceId] = useState(prefill?.resource_id ?? "");
  const [region, setRegion] = useState(prefill?.region ?? "");
  const [title, setTitle] = useState(prefill?.title ?? "");
  const [metricName, setMetricName] = useState(prefill?.metric_names?.[0] ?? "");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const servicesWithMetrics = approvedServices.filter((s) => WIDGET_SERVICE_CONFIG[s]);
  const config = serviceType ? WIDGET_SERVICE_CONFIG[serviceType] : null;
  const metricOptions = config?.metrics || [];
  const regionForItem = (item) => (config?.regionFromItem?.(item) || sessionRegion || "").trim() || null;

  const handleServiceChange = (newService) => {
    setServiceType(newService);
    setResources([]);
    setResourceId("");
    setRegion("");
    setTitle("");
    setMetricName("");
  };

  useEffect(() => {
    if (!serviceType || !config) return;
    setLoading(true);
    setError("");
    const listFn = api[config.listKey];
    if (typeof listFn !== "function") {
      setLoading(false);
      return;
    }
    listFn(true)
      .then((res) => {
        const list = config.listTransform
          ? config.listTransform(res)
          : (res?.instances ?? res?.clusters ?? res?.domains ?? res?.brokers ?? res?.load_balancers ?? res?.loadBalancers ?? (Array.isArray(res) ? res : []));
        setResources(Array.isArray(list) ? list : []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [serviceType]);

  const onResourceChange = (e) => {
    const id = e.target.value;
    const item = resources.find((r) => (config.getResourceId ? config.getResourceId(r) : (r[config.resourceIdKey] ?? r.id)) === id);
    if (item) {
      const name = item[config.resourceNameKey] ?? item.name ?? id;
      setResourceId(id);
      setRegion(regionForItem(item) || sessionRegion || "");
      setTitle(name || id);
    } else {
      setResourceId("");
      setRegion("");
      setTitle("");
    }
  };

  const handleSubmit = () => {
    const effectiveRegion = region || sessionRegion;
    if (!resourceId || !effectiveRegion || !metricName) {
      setError("Select service, resource, and metric.");
      return;
    }
    setError("");
    setSubmitting(true);
    api
      .createDashboardWidget({
        service_type: serviceType,
        resource_id: resourceId,
        region: effectiveRegion,
        title: title || undefined,
        metric_names: [metricName],
        panel_id: panelId,
      })
      .then(() => {
        onAdded();
        onClose();
      })
      .catch((e) => {
        setError(e.message);
        setSubmitting(false);
      });
  };

  const canSubmit = serviceType && resourceId && metricName && !submitting;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content widget-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add widget</h3>
          <button type="button" className="modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          <label>Service</label>
          <select
            value={serviceType}
            onChange={(e) => handleServiceChange(e.target.value)}
            className="widget-select"
          >
            <option value="">— Select —</option>
            {[...servicesWithMetrics]
              .sort((a, b) => (WIDGET_SERVICE_CONFIG[a]?.label ?? a).localeCompare(WIDGET_SERVICE_CONFIG[b]?.label ?? b, undefined, { sensitivity: "base" }))
              .map((s) => (
              <option key={s} value={s}>
                {WIDGET_SERVICE_CONFIG[s]?.label ?? s}
              </option>
            ))}
          </select>

          <label>Resource</label>
          <select
            value={resourceId}
            onChange={onResourceChange}
            className="widget-select"
            disabled={!serviceType || loading}
          >
            <option value="">{loading ? "Loading…" : "— Select —"}</option>
            {!loading &&
              resources.map((item) => {
                const id = config.getResourceId ? config.getResourceId(item) : (item[config.resourceIdKey] ?? item.id);
                const name = item[config.resourceNameKey] ?? item.name ?? id;
                return (
                  <option key={id} value={id}>
                    {name} ({regionForItem(item) || sessionRegion || "—"})
                  </option>
                );
              })}
          </select>

          <label>Metric</label>
          <select
            value={metricName}
            onChange={(e) => setMetricName(e.target.value)}
            className="widget-select"
            disabled={!serviceType}
          >
            <option value="">— Select —</option>
            {metricOptions.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          {error && <p className="form-error">{error}</p>}
          <button
            type="button"
            className="btn-primary"
            style={{ marginTop: "1rem" }}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {submitting ? "Adding…" : "Add widget"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EditWidgetModal({ widget, onClose, onSaved, approvedServices, sessionRegion }) {
  const [serviceType, setServiceType] = useState(widget?.service_type ?? "");
  const [resources, setResources] = useState([]);
  const [resourceId, setResourceId] = useState(widget?.resource_id ?? "");
  const [region, setRegion] = useState(widget?.region ?? "");
  const [title, setTitle] = useState(widget?.title ?? "");
  const [metricName, setMetricName] = useState(widget?.metric_names?.[0] ?? "");
  const [loading, setLoading] = useState(!!widget?.service_type);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const servicesWithMetrics = approvedServices.filter((s) => WIDGET_SERVICE_CONFIG[s]);
  const config = serviceType ? WIDGET_SERVICE_CONFIG[serviceType] : null;
  const metricOptions = config?.metrics || [];
  const regionForItem = (item) => (config?.regionFromItem?.(item) || sessionRegion || "").trim() || null;

  useEffect(() => {
    if (!serviceType || !config) {
      setResources([]);
      return;
    }
    setLoading(true);
    setError("");
    const listFn = api[config.listKey];
    if (typeof listFn !== "function") {
      setLoading(false);
      setResources([]);
      return;
    }
    listFn(true)
      .then((res) => {
        const list = config.listTransform
          ? config.listTransform(res)
          : (res?.instances ?? res?.clusters ?? res?.domains ?? res?.brokers ?? res?.load_balancers ?? res?.loadBalancers ?? (Array.isArray(res) ? res : []));
        setResources(Array.isArray(list) ? list : []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [serviceType]);

  useEffect(() => {
    if (serviceType !== widget?.service_type) {
      setResourceId("");
      setRegion("");
      setTitle("");
      setMetricName("");
    }
  }, [serviceType, widget?.service_type]);

  const onResourceChange = (e) => {
    const id = e.target.value;
    const item = resources.find((r) => (config.getResourceId ? config.getResourceId(r) : (r[config.resourceIdKey] ?? r.id)) === id);
    if (item) {
      const name = item[config.resourceNameKey] ?? item.name ?? id;
      setResourceId(id);
      setRegion(regionForItem(item) || sessionRegion || "");
      setTitle(name || id);
    } else {
      setResourceId("");
      setRegion("");
      setTitle("");
    }
  };

  const handleSubmit = () => {
    const effectiveRegion = region || sessionRegion;
    if (!resourceId || !effectiveRegion || !metricName) {
      setError("Select service, resource, and metric.");
      return;
    }
    setError("");
    setSubmitting(true);
    api
      .updateDashboardWidget(widget.id, {
        service_type: serviceType,
        resource_id: resourceId,
        region: effectiveRegion,
        title: title || undefined,
        metric_names: [metricName],
      })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((e) => {
        setError(e.message);
        setSubmitting(false);
      });
  };

  const canSubmit = serviceType && resourceId && metricName && !submitting;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content widget-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit widget</h3>
          <button type="button" className="modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          <label>Service</label>
          <select
            value={serviceType}
            onChange={(e) => setServiceType(e.target.value)}
            className="widget-select"
          >
            <option value="">— Select —</option>
            {[...servicesWithMetrics]
              .sort((a, b) => (WIDGET_SERVICE_CONFIG[a]?.label ?? a).localeCompare(WIDGET_SERVICE_CONFIG[b]?.label ?? b, undefined, { sensitivity: "base" }))
              .map((s) => (
              <option key={s} value={s}>
                {WIDGET_SERVICE_CONFIG[s]?.label ?? s}
              </option>
            ))}
          </select>

          <label>Resource</label>
          <select
            value={resourceId}
            onChange={onResourceChange}
            className="widget-select"
            disabled={!serviceType || loading}
          >
            <option value="">{loading ? "Loading…" : "— Select —"}</option>
            {!loading &&
              resources.map((item) => {
                const id = config.getResourceId ? config.getResourceId(item) : (item[config.resourceIdKey] ?? item.id);
                const name = item[config.resourceNameKey] ?? item.name ?? id;
                return (
                  <option key={id} value={id}>
                    {name} ({regionForItem(item) || sessionRegion || "—"})
                  </option>
                );
              })}
          </select>

          <label>Metric</label>
          <select
            value={metricName}
            onChange={(e) => setMetricName(e.target.value)}
            className="widget-select"
            disabled={!serviceType}
          >
            <option value="">— Select —</option>
            {metricOptions.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          {error && <p className="form-error">{error}</p>}
          <button
            type="button"
            className="btn-primary"
            style={{ marginTop: "1rem" }}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

const WIDGET_RANGE_KEY = "widget-dashboard-range-hours";
const RANGE_OPTIONS = [1, 6, 24, 72];

function getStoredRangeHours() {
  try {
    const saved = localStorage.getItem(WIDGET_RANGE_KEY);
    const n = parseInt(saved, 10);
    return RANGE_OPTIONS.includes(n) ? n : 24;
  } catch {
    return 24;
  }
}

/* ─── Panel-level components ─────────────────────────────────────────────── */

function AddPanelModal({ onClose, onCreated }) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = () => {
    const trimmed = title.trim();
    if (!trimmed) {
      setError("Title is required.");
      return;
    }
    setError("");
    setSubmitting(true);
    api
      .createDashboardPanel({ title: trimmed })
      .then(() => {
        onCreated();
        onClose();
      })
      .catch((e) => {
        setError(e.message);
        setSubmitting(false);
      });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content widget-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add panel</h3>
          <button type="button" className="modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          <label>Title</label>
          <input
            className="widget-select"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
            autoFocus
            placeholder="e.g. Production Metrics"
          />
          {error && <p className="form-error">{error}</p>}
          <button
            type="button"
            className="btn-primary"
            style={{ marginTop: "1rem" }}
            onClick={handleSubmit}
            disabled={!title.trim() || submitting}
          >
            {submitting ? "Adding…" : "Add panel"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DeletePanelModal({ panel, onClose, onDeleted }) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const widgetCount = panel.widgets?.length || 0;

  const handleDelete = () => {
    setDeleting(true);
    setError("");
    api
      .deleteDashboardPanel(panel.id)
      .then(() => {
        onDeleted();
        onClose();
      })
      .catch((e) => {
        setError(e.message);
        setDeleting(false);
      });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content widget-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Delete panel</h3>
          <button type="button" className="modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          <p>
            Delete <strong>{panel.title}</strong>? This will also remove{" "}
            {widgetCount} widget{widgetCount !== 1 ? "s" : ""} inside it.
          </p>
          {error && <p className="form-error">{error}</p>}
          <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
            <button type="button" className="btn-secondary" onClick={onClose} disabled={deleting}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              style={{ background: "var(--red)" }}
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function PanelSection({
  panel,
  rangeHours,
  onAddWidget,
  onEditWidget,
  onDeletePanel,
  onRefresh,
  onDataUpdated,
  approvedServices,
  sessionRegion,
}) {
  const [collapsed, setCollapsed] = useState(!!panel.collapsed);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(panel.title);

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: panel.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const toggleCollapse = () => {
    const next = !collapsed;
    setCollapsed(next);
    api.updateDashboardPanel(panel.id, { collapsed: next }).catch(() => {});
  };

  const startTitleEdit = () => {
    setTitleDraft(panel.title);
    setEditingTitle(true);
  };

  const saveTitleEdit = () => {
    const trimmed = titleDraft.trim();
    if (trimmed && trimmed !== panel.title) {
      api.updateDashboardPanel(panel.id, { title: trimmed }).then(() => onRefresh()).catch(() => {});
    }
    setEditingTitle(false);
  };

  const cancelTitleEdit = () => {
    setTitleDraft(panel.title);
    setEditingTitle(false);
  };

  const widgets = panel.widgets || [];

  const handleCloneWidget = useCallback(
    (widget) => onAddWidget(panel.id, widget),
    [onAddWidget, panel.id]
  );

  return (
    <div ref={setNodeRef} style={style} className="panel-section">
      <div className="panel-section-header">
        <button
          type="button"
          className="panel-drag-handle"
          {...attributes}
          {...listeners}
          title="Drag to reorder"
        >
          <GripVertical size={16} />
        </button>

        <button type="button" className="panel-collapse-btn" onClick={toggleCollapse} title={collapsed ? "Expand" : "Collapse"}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        </button>

        <div className="panel-section-title-area">
          {editingTitle ? (
            <input
              className="panel-title-input"
              type="text"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={saveTitleEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveTitleEdit();
                if (e.key === "Escape") cancelTitleEdit();
              }}
              autoFocus
            />
          ) : (
            <>
              <span className="panel-section-title" onDoubleClick={startTitleEdit}>
                {panel.title}
              </span>
              <button
                type="button"
                className="panel-title-edit-btn"
                onClick={startTitleEdit}
                title="Rename panel"
              >
                <Pencil size={12} />
              </button>
            </>
          )}
        </div>

        <div className="panel-section-actions">
          <span className="panel-widget-count">
            {widgets.length} widget{widgets.length !== 1 ? "s" : ""}
          </span>
          <button
            type="button"
            className="panel-action-btn"
            onClick={() => onAddWidget(panel.id)}
            title="Add widget to this panel"
          >
            <Plus size={14} /> Add widget
          </button>
          <button
            type="button"
            className="panel-delete-btn"
            onClick={() => onDeletePanel(panel)}
            title="Delete panel"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="panel-section-body">
          <div className="widget-grid">
            {widgets.map((w) => (
              <WidgetCard
                key={w.id}
                widget={w}
                rangeHours={rangeHours}
                onDelete={onRefresh}
                onEdit={onEditWidget}
                onClone={handleCloneWidget}
                onDataUpdated={onDataUpdated}
              />
            ))}
            <button
              type="button"
              className="widget-add-placeholder"
              onClick={() => onAddWidget(panel.id)}
              title="Add widget to this panel"
              data-widget-index={widgets.length}
            >
              <Plus size={24} />
              <span>Add widget</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Main dashboard ─────────────────────────────────────────────────────── */

export default function WidgetDashboard() {
  const { auth } = useAuth();
  const [panels, setPanels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [addingWidget, setAddingWidget] = useState(null); // { panelId, prefill? }
  const [editingWidget, setEditingWidget] = useState(null);
  const [deletingPanel, setDeletingPanel] = useState(null);
  const [addPanelOpen, setAddPanelOpen] = useState(false);
  const [rangeHours, setRangeHours] = useState(getStoredRangeHours);
  const [lastDataUpdated, setLastDataUpdated] = useState(null);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const fetchPanels = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getDashboardPanels()
      .then(setPanels)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchPanels();
  }, [fetchPanels]);

  useEffect(() => {
    try {
      localStorage.setItem(WIDGET_RANGE_KEY, String(rangeHours));
    } catch (_) {}
  }, [rangeHours]);

  const handleDataUpdated = useCallback(() => {
    setLastDataUpdated(Date.now());
  }, []);

  const handleDragEnd = useCallback(
    (event) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      setPanels((prev) => {
        const oldIndex = prev.findIndex((p) => p.id === active.id);
        const newIndex = prev.findIndex((p) => p.id === over.id);
        if (oldIndex === -1 || newIndex === -1) return prev;
        const reordered = arrayMove(prev, oldIndex, newIndex);
        api.reorderDashboardPanels(reordered.map((p) => p.id)).catch(() => {});
        return reordered;
      });
    },
    []
  );

  const approvedServices = auth?.services || [];
  const serviceOptionsForWidgets =
    approvedServices.includes("databases") && !approvedServices.includes("docdb")
      ? [...approvedServices, "docdb"]
      : approvedServices;

  if (loading && panels.length === 0) {
    return <div className="panel-loading">Loading dashboard…</div>;
  }
  if (error && panels.length === 0) {
    return <div className="panel-error">Dashboard: {error}</div>;
  }

  return (
    <div className="widget-dashboard">
      <div className="widget-dashboard-toolbar">
        <div className="widget-dashboard-range-row">
          <div className="widget-dashboard-range">
            <span>Range:</span>
            {RANGE_OPTIONS.map((h) => (
              <button
                key={h}
                type="button"
                className={rangeHours === h ? "active" : ""}
                onClick={() => setRangeHours(h)}
              >
                {h}h
              </button>
            ))}
          </div>
          {lastDataUpdated != null && (
            <span className="widget-last-updated">
              Last updated: {new Date(lastDataUpdated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => setAddPanelOpen(true)}
        >
          <Plus size={16} />
          Add panel
        </button>
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={panels.map((p) => p.id)} strategy={verticalListSortingStrategy}>
          {panels.map((panel) => (
            <PanelSection
              key={panel.id}
              panel={panel}
              rangeHours={rangeHours}
              onAddWidget={(panelId, prefill) => setAddingWidget({ panelId, prefill: prefill || null })}
              onEditWidget={setEditingWidget}
              onDeletePanel={setDeletingPanel}
              onRefresh={fetchPanels}
              onDataUpdated={handleDataUpdated}
              approvedServices={serviceOptionsForWidgets}
              sessionRegion={auth?.region}
            />
          ))}
        </SortableContext>
      </DndContext>

      {panels.length === 0 && (
        <div className="widget-empty">
          <p>No panels yet. Add one to organize your widgets.</p>
        </div>
      )}

      {addPanelOpen && (
        <AddPanelModal
          onClose={() => setAddPanelOpen(false)}
          onCreated={fetchPanels}
        />
      )}

      {addingWidget && (
        <AddWidgetModal
          panelId={addingWidget.panelId}
          prefill={addingWidget.prefill}
          onClose={() => setAddingWidget(null)}
          onAdded={fetchPanels}
          approvedServices={serviceOptionsForWidgets}
          sessionRegion={auth?.region}
        />
      )}

      {editingWidget && (
        <EditWidgetModal
          widget={editingWidget}
          onClose={() => setEditingWidget(null)}
          onSaved={fetchPanels}
          approvedServices={serviceOptionsForWidgets}
          sessionRegion={auth?.region}
        />
      )}

      {deletingPanel && (
        <DeletePanelModal
          panel={deletingPanel}
          onClose={() => setDeletingPanel(null)}
          onDeleted={fetchPanels}
        />
      )}
    </div>
  );
}
