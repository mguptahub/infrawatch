import json
import boto3
from .config import settings

# ─── Service → IAM policy statement mapping ───────────────────────────────────
# Each service maps to the minimum read-only actions needed for the dashboard.

SERVICE_POLICIES = {
    "ec2": {
        "Effect": "Allow",
        "Action": ["ec2:Describe*"],
        "Resource": "*",
    },
    "eks": {
        "Effect": "Allow",
        "Action": ["eks:List*", "eks:Describe*"],
        "Resource": "*",
    },
    "databases": {
        "Effect": "Allow",
        "Action": ["rds:Describe*", "rds:List*"],
        "Resource": "*",
    },
    "elasticache": {
        "Effect": "Allow",
        "Action": ["elasticache:Describe*", "elasticache:List*"],
        "Resource": "*",
    },
    "opensearch": {
        "Effect": "Allow",
        "Action": ["es:List*", "es:Describe*", "es:ESHttpGet"],
        "Resource": "*",
    },
    "mq": {
        "Effect": "Allow",
        "Action": ["mq:List*", "mq:Describe*"],
        "Resource": "*",
    },
    "ses": {
        "Effect": "Allow",
        "Action": [
            "ses:Get*", "ses:List*",
            "sesv2:Get*", "sesv2:List*",
            "sesv2:DeleteSuppressedDestination",
        ],
        "Resource": "*",
    },
    "secrets": {
        "Effect": "Allow",
        "Action": [
            "secretsmanager:ListSecrets",
            "secretsmanager:DescribeSecret",
            "secretsmanager:GetSecretValue",
        ],
        "Resource": "*",
    },
    "iam": {
        "Effect": "Allow",
        "Action": [
            "iam:ListUsers",
            "iam:GetUser",
            "iam:GetLoginProfile",
            "iam:GenerateCredentialReport",
            "iam:GetCredentialReport",
            "iam:ListGroupsForUser",
            "iam:ListAttachedUserPolicies",
            "iam:ListUserPolicies",
            "iam:ListMFADevices",
            "iam:ListAccessKeys",
            "iam:GetAccessKeyLastUsed",
        ],
        "Resource": "*",
    },
    "cost": {
        "Effect": "Allow",
        "Action": ["ce:Get*", "ce:List*", "ce:Describe*"],
        "Resource": "*",
    },
    "elb": {
        "Effect": "Allow",
        "Action": [
            "elasticloadbalancing:Describe*",
        ],
        "Resource": "*",
    },
}

ALL_SERVICES = list(SERVICE_POLICIES.keys())


def assume_role_for_services(services: list, duration_hours: int, session_name: str) -> dict:
    """
    Calls STS AssumeRole using power keys with a session policy scoped to
    the requested services. Returns the temp credentials dict.
    """
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        raise RuntimeError("Power AWS keys and BASE_ROLE_ARN must be configured")

    # Build session policy combining requested services
    statements = [SERVICE_POLICIES[s] for s in services if s in SERVICE_POLICIES]
    if not statements:
        raise ValueError("No valid services requested")

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
