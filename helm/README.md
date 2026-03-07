# InfraWatch

A central hub for monitoring **AWS infrastructure** and managing **Just-In-Time access** to cloud resources. Real-time visibility into your services, combined with a secure request-approval workflow for temporary AWS credentials.

<p align="center">
  <img src="docs/assets/images/dashboard-preview.png" alt="InfraWatch" width="800">
</p>

---

## What You Can Do

### My Dashboard

Build a personalized monitoring view using **panels** and **widgets**.

- **Panels** are full-width, titled sections that group related widgets. Create panels like "Production DBs" or "Network Traffic" to organize your view.
- **Widgets** display live metric charts (CPU, memory, connections, IOPS, etc.) for any resource you have access to.
- Panels can be **collapsed**, **renamed**, **reordered via drag-and-drop**, and **deleted** (with all widgets inside).
- Widgets auto-refresh every 2 minutes. Choose a time range of 1h, 6h, 24h, or 72h.
- Your dashboard layout persists across sessions.

### AWS Service Tabs

Each tab gives you a focused view into a specific AWS service. You only see tabs for services you've been granted access to.

| Tab | What You See |
|-----|-------------|
| **EC2** | Instance states, types, AZs, and CPU utilization |
| **Load Balancers** | ALB/NLB health, traffic metrics, request counts |
| **EKS** | Cluster status, versions, nodegroup health, API server metrics |
| **Databases** | RDS & DocumentDB health, connections, storage, IOPS |
| **ElastiCache** | Replication groups, cache clusters, memory and hit rates |
| **OpenSearch** | Domain health, JVM pressure, search and indexing rates |
| **MQ** | Broker status, connections, queue depth, memory usage |
| **SES** | Sending quotas, suppression list management, identity/DKIM config |
| **Secrets** | Secret metadata, age, rotation status, on-demand value loading |
| **IAM** | Users, group memberships, policies, MFA and access key status |
| **Cost** | Month-to-date totals, daily spend charts, breakdown by service |

### Requesting Access

You don't manage AWS credentials directly. Instead:

1. **Request** access to the services you need and for how long (up to 12 hours).
2. **Your manager** receives an email notification and approves or denies the request.
3. **Once approved**, temporary credentials are generated automatically via AWS STS.
4. **Access expires** when the duration ends. No leftover keys, no manual cleanup.

All authentication uses **email OTP** (6-digit codes) -- no passwords anywhere in the system.

### User Roles

| Role | Can Do |
|------|--------|
| **Employee** | View dashboard, request access to services |
| **Manager** | Everything an employee can, plus approve/deny requests |
| **Admin** | Manage users, set service allowlists, view all requests |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- An AWS IAM role with read-only access to the services above (see [IAM Setup](#iam-setup))
- SMTP credentials for email delivery (SES SMTP, Mailgun, etc.)

### Step 1: Create a `.env` file

```env
# Admin
ADMIN_EMAIL=admin@company.com

# AWS Power Keys (server-side only, never exposed to users)
POWER_AWS_ACCESS_KEY_ID=...
POWER_AWS_SECRET_ACCESS_KEY=...
POWER_AWS_REGION=us-east-1
BASE_ROLE_ARN=arn:aws:iam::123456789012:role/PowerUserRole

# Collector regions (optional — empty = all regions)
# COLLECTOR_REGIONS=us-east-1,us-east-2,us-west-2

# SMTP (for OTP and notification emails)
SMTP_HOST=smtp.company.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM=noreply@company.com

# Postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=awsdashboard
POSTGRES_USER=awsdashboard
POSTGRES_PASSWORD=<strong-password>

# Valkey
VALKEY_PASSWORD=<strong-password>
VALKEY_URL=redis://:${VALKEY_PASSWORD}@valkey:6379

# App
COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:4000
OTP_EXPIRY_MINUTES=10
OTP_MAX_ATTEMPTS=5

# Auto-registration whitelist (optional — leave blank to disable)
# ALLOWED_DOMAINS=company.com,contractor.com
```

### Step 2: Create a `docker-compose.yml`

```yaml
services:
  backend:
    image: mguptahub/infrawatch-backend:latest
    env_file:
      - .env
    depends_on:
      - db
      - valkey

  frontend:
    image: mguptahub/infrawatch-frontend:latest
    ports:
      - "4000:8080"
    depends_on:
      - backend

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-awsdashboard}
      POSTGRES_USER: ${POSTGRES_USER:-awsdashboard}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
    volumes:
      - db_data:/var/lib/postgresql/data

  valkey:
    image: valkey/valkey:8-alpine
    command: ["valkey-server", "--requirepass", "${VALKEY_PASSWORD:?VALKEY_PASSWORD is required}"]

volumes:
  db_data:
  valkey_data:
```

### Step 3: Start

```bash
docker compose up -d
```

The app starts at `http://localhost:4000`. The admin account uses the email set in `ADMIN_EMAIL`.

### Helm (Kubernetes)

Add the InfraWatch Helm repo:

```bash
helm repo add infrawatch https://infrawatch.mguptahub.com/helm
helm repo update
```

Install with required values:

```bash
helm install infrawatch infrawatch/infrawatch \
  --set aws.powerAccessKeyId=AKIA... \
  --set aws.powerSecretAccessKey=... \
  --set aws.baseRoleArn=arn:aws:iam::123456789012:role/PowerUserRole \
  --set app.adminEmail=admin@company.com \
  --set smtp.host=smtp.company.com \
  --set smtp.port=587 \
  --set smtp.user=... \
  --set smtp.password=... \
  --set smtp.from=noreply@company.com \
  --set services.postgresql.auth.password=secret \
  --set services.valkey.password=secret
```

By default, PostgreSQL and Valkey are deployed in-cluster. To use an external database:

```bash
helm install infrawatch infrawatch/infrawatch \
  --set services.postgresql.enabled=false \
  --set externalServices.postgresql.host=my-rds.example.com \
  --set externalServices.postgresql.password=secret \
  ...
```

See [`helm/values.yaml`](helm/values.yaml) for the full list of configuration options.

---

## IAM Setup

The backend assumes `BASE_ROLE_ARN` and applies a restrictive session policy per request. Each approved service maps to a scoped IAM policy statement. CloudWatch is always included for metrics.

The base role must already allow these actions:

| Service | Actions |
| --------------- | --------------------------------------------------- |
| EC2 | `ec2:Describe*` |
| EKS | `eks:List*`, `eks:Describe*` |
| Databases | `rds:Describe*`, `rds:List*`, `docdb:Describe*`, `docdb:List*` |
| ElastiCache | `elasticache:Describe*`, `elasticache:List*` |
| OpenSearch | `es:List*`, `es:Describe*`, `es:ESHttpGet` |
| MQ | `mq:List*`, `mq:Describe*` |
| SES | `ses:*`, `sesv2:*` |
| Secrets Manager | `secretsmanager:Get*`, `secretsmanager:List*`, `secretsmanager:Describe*` |
| IAM | `iam:Get*`, `iam:List*`, `iam:GenerateCredentialReport` |
| Cost Explorer | `ce:Get*`, `ce:List*`, `ce:Describe*` |
| Load Balancers | `elasticloadbalancing:Describe*` |
| CloudWatch | `cloudwatch:Get*`, `cloudwatch:List*` (always included) |
| AWS Health | `health:Describe*` |

Session policies can only restrict, never expand, the base role's permissions.

---

## Architecture

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 18, Recharts, @dnd-kit |
| **Backend** | FastAPI (Python), boto3 |
| **Database** | PostgreSQL |
| **Cache & Sessions** | Valkey |
| **Auth** | Email OTP, AWS STS temporary credentials |
| **Deployment** | Docker Compose, Helm (Kubernetes) |
