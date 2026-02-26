# AWS Monitor & Access Dashboard

A central hub for monitoring AWS infrastructure and managing Just-In-Time (JIT) access. This dashboard combines real-time resource visibility with a secure request-approval workflow for temporary AWS credentials using AWS STS.

## Supported AWS Services

The dashboard provides visibility into the following AWS services:

- **Compute**:
  - **EC2**: Monitor instance states, types, availability zones, and CPU utilization via CloudWatch.
  - **EKS**: Track cluster status, versions, and nodegroup health.
- **Databases & Cache**:
  - **RDS**: View database health, engine details, storage, and performance metrics (CPU, connection counts).
  - **ElastiCache**: Monitor replication groups and cache cluster status.
- **Integration & Messaging**:
  - **OpenSearch**: Track domain health and status.
  - **Amazon MQ**: View broker status and configuration details.
- **Networking & Delivery**:
  - **Load Balancers (ALB/NLB)**: Monitor health and configuration.
  - **SES**: View account-level details and manage email identities and suppression lists.
- **Security & Infrastructure**:
  - **Secrets Manager**: List and view metadata for stored secrets.
  - **IAM**: Browse IAM users and inspect their group/policy access details.
- **Cost Management**:
  - **Billing**: View month-to-date totals, daily spend charts, and cost breakdown by service.

## Access Management (Employee/Manager Workflow)

The dashboard implements a secure, role-based access request system to reduce long-term IAM credential exposure.

### User Roles

- **Employee**: Can view the dashboard and request JIT access to specific services.
- **Manager**: Can review, approve, or deny access requests from employees.
- **Admin**: Full administrative control over users and system configuration.

### How it Works

1. **Request Phase**: An employee requests access to specific AWS services for a defined duration.
2. **Approval Phase**:
   - A manager receives a notification with a secure approval token.
   - For sensitive operations, managers verify their identity via a 6-digit OTP sent to their registered email.
3. **Activation Phase**: Once approved, the system uses **AWS STS (Security Token Service)** to generate temporary credentials for the requested services.
4. **Session Management**: Access is automatically terminated when the requested duration expires.

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd aws-dashboard

# Set up environment
cp .env.example .env

# Start with Docker Compose
docker compose up -d --build
```

### Authentication setup

The dashboard itself requires an IAM profile or role with permissions to:

- Describe the AWS resources listed above.
- Call `sts:AssumeRole` to generate temporary credentials for users.
- Configure an SMTP relay (for example SES SMTP, Mailgun, or internal SMTP) for OTP and approval notifications.

## Architecture

The system consists of a **FastAPI** backend (Python) communicating with the AWS SDK (boto3) and a **React** frontend (JavaScript/JSX) for the user interface. It uses **Valkey** (Redis alternative) for session management and **PostgreSQL** for storing user data and request history.
