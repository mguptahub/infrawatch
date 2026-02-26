from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/eks", tags=["EKS"])


def _fetch_eks(session):
    eks = session.client("eks")

    cluster_names = eks.list_clusters()["clusters"]
    if not cluster_names:
        return {"clusters": []}

    def fetch_cluster_details(name):
        c = eks.describe_cluster(name=name)["cluster"]
        ng_list = eks.list_nodegroups(clusterName=name).get("nodegroups", [])

        def fetch_nodegroup(ng_name):
            ng = eks.describe_nodegroup(clusterName=name, nodegroupName=ng_name)["nodegroup"]
            return {
                "name": ng["nodegroupName"],
                "status": ng["status"],
                "instance_types": ng.get("instanceTypes", []),
                "scaling_config": ng.get("scalingConfig", {}),
                "capacity_type": ng.get("capacityType", ""),
            }

        with ThreadPoolExecutor(max_workers=5) as ng_executor:
            nodegroups = list(ng_executor.map(fetch_nodegroup, ng_list))

        return {
            "name": c["name"],
            "arn": c["arn"],
            "status": c["status"],
            "version": c["version"],
            "endpoint": c.get("endpoint"),
            "role_arn": c.get("roleArn"),
            "created_at": c["createdAt"].isoformat() if c.get("createdAt") else None,
            "nodegroups": nodegroups,
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        result = list(executor.map(fetch_cluster_details, cluster_names))

    return {"clusters": result}


@router.get("/clusters")
def get_eks_clusters(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("eks", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_eks(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
