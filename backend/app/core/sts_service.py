"""
STS AssumeRole with a session policy scoped to the requested services.

The base role (BASE_ROLE_ARN) must allow at least the actions below plus CloudWatch.
We append CloudWatch to every session for metrics (EC2, EKS, RDS, LB, etc.).
EKS node listing uses EC2 DescribeInstances (tag filter); EKS metrics use CloudWatch.
AWS Health API (health:Describe*) must be on the base role directly (not session-scoped).
"""
import json
import boto3
from .config import settings

# ─── Service → IAM policy statement mapping ───────────────────────────────────
# Kept compact to stay under AWS AssumeRole packed policy size limit (2048 bytes).
# Base role must grant these + cloudwatch:Get*, cloudwatch:List*.

SERVICE_POLICIES = {
    "ec2": {"Effect": "Allow", "Action": ["ec2:Describe*"], "Resource": "*"},
    "eks": {"Effect": "Allow", "Action": ["eks:List*", "eks:Describe*"], "Resource": "*"},
    "databases": {
        "Effect": "Allow",
        "Action": ["rds:Describe*", "rds:List*", "docdb:Describe*", "docdb:List*"],
        "Resource": "*",
    },
    "elasticache": {"Effect": "Allow", "Action": ["elasticache:Describe*", "elasticache:List*"], "Resource": "*"},
    "opensearch": {"Effect": "Allow", "Action": ["es:List*", "es:Describe*", "es:ESHttpGet"], "Resource": "*"},
    "mq": {"Effect": "Allow", "Action": ["mq:List*", "mq:Describe*"], "Resource": "*"},
    "ses": {"Effect": "Allow", "Action": ["ses:*", "sesv2:*"], "Resource": "*"},
    "secrets": {"Effect": "Allow", "Action": ["secretsmanager:Get*", "secretsmanager:List*", "secretsmanager:Describe*"], "Resource": "*"},
    "iam": {"Effect": "Allow", "Action": ["iam:Get*", "iam:List*", "iam:GenerateCredentialReport"], "Resource": "*"},
    "cost": {"Effect": "Allow", "Action": ["ce:Get*", "ce:List*", "ce:Describe*"], "Resource": "*"},
    "elb": {"Effect": "Allow", "Action": ["elasticloadbalancing:Describe*"], "Resource": "*"},
}

ALL_SERVICES = list(SERVICE_POLICIES.keys())

# Required for all metrics (EC2, EKS control-plane, RDS, LB, MQ, OpenSearch, ElastiCache, DocDB).
_CLOUDWATCH_STATEMENT = {"Effect": "Allow", "Action": ["cloudwatch:Get*", "cloudwatch:List*"], "Resource": "*"}


def assume_role_for_services(services: list, duration_hours: int, session_name: str) -> dict:
    """
    Calls STS AssumeRole using power keys with a session policy scoped to
    the requested services. Returns the temp credentials dict.
    """
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        raise RuntimeError("Power AWS keys and BASE_ROLE_ARN must be configured")

    # Build session policy combining requested services + CloudWatch (always needed for metrics)
    statements = [SERVICE_POLICIES[s] for s in services if s in SERVICE_POLICIES]
    if not statements:
        raise ValueError("No valid services requested")
    statements.append(_CLOUDWATCH_STATEMENT)

    session_policy = json.dumps({"Version": "2012-10-17", "Statement": statements})

    sts = boto3.client(
        "sts",
        aws_access_key_id=settings.power_aws_access_key_id,
        aws_secret_access_key=settings.power_aws_secret_access_key,
        region_name=settings.power_aws_region,
    )

    duration_seconds = min(duration_hours * 3600, 43200)  # STS max = 12h

    resp = sts.assume_role(
        RoleArn=settings.base_role_arn,
        RoleSessionName=session_name[:64],
        Policy=session_policy,
        DurationSeconds=duration_seconds,
    )

    creds = resp["Credentials"]
    return {
        "access_key": creds["AccessKeyId"],
        "secret_key": creds["SecretAccessKey"],
        "session_token": creds["SessionToken"],
        "region": settings.power_aws_region,
    }
