# EKS Nodes View Design

## Goal
Add a Nodes modal to the EKS cluster detail drawer showing all EC2 nodes (managed nodegroups + Karpenter), with pod counts and click-through to EC2 detail.

## Architecture
- New backend endpoint `GET /api/eks/clusters/{name}/nodes` discovers nodes via EC2 tag filters and enriches with CloudWatch CPU + Container Insights pod counts.
- Frontend adds a Nodes button to the EKS `DetailDrawer` header, triggering a full-screen modal that reuses the EC2 detail drawer for per-node drill-down.

## Components

### Backend
- `GET /api/eks/clusters/{name}/nodes` — uncached, on-demand
- EC2 tag filter: `tag:eks:cluster-name = <name>` (covers managed nodegroups + Karpenter v0.33+)
- Fallback filter: `tag:kubernetes.io/cluster/<name> = owned` for self-managed nodes
- Per node: instance_id, name, state, type, az, private_ip, uptime_hours, cpu_percent (CW), nodegroup_name (tag `eks:nodegroup-name`), karpenter_nodepool (tag `karpenter.sh/nodepool`), pod_count (CW Container Insights `node_number_of_running_pods` or null)

### Frontend
- Nodes button in `DetailDrawer` header (next to X)
- `NodesModal` component (full-screen, like EC2's MetricsModal)
  - Header: cluster name + node count badges
  - Filter tabs: All | per-nodegroup | Karpenter (if present)
  - Table: Name, Instance ID, State, Type, AZ, Private IP, Uptime, CPU, Pods, Group
  - Click row → `EC2DetailDrawer` (reuses `api.getEC2Detail(instanceId)`)
