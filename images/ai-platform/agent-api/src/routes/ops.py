from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, HTTPException
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException

from ..models import (
    QueryTelemetryRequest, QueryTelemetryResponse,
    TriggerIngestorRequest, TriggerIngestorResponse
)
from ..config import _ensure_k8s, _qdrant_url

router = APIRouter()

@router.post("/ops/query-telemetry", response_model=QueryTelemetryResponse)
async def ops_query_telemetry(req: QueryTelemetryRequest):
    prometheus_url = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/api/v1/query"
    resp_data = QueryTelemetryResponse()
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            q_gpu = "avg(vllm:gpu_cache_usage_perc) * 100"
            r = await client.get(prometheus_url, params={"query": q_gpu})
            if r.status_code == 200:
                res = r.json().get("data", {}).get("result", [])
                if res: resp_data.gpu_cache = float(res[0]["value"][1])
        except Exception: pass

    return resp_data


@router.get("/ops/status")
async def ops_status() -> dict:
    _ensure_k8s()
    namespace = "ai-platform"
    core = k8s_client.CoreV1Api()
    apps_api = k8s_client.AppsV1Api()
    batch = k8s_client.BatchV1Api()
    custom = k8s_client.CustomObjectsApi()

    pods = core.list_namespaced_pod(namespace).items
    deployments = apps_api.list_namespaced_deployment(namespace).items

    pod_rows = [{"name": p.metadata.name, "status": p.status.phase} for p in pods]
    deploy_rows = [{"name": d.metadata.name, "ready": d.status.ready_replicas or 0} for d in deployments]

    argo_apps = []
    try:
        raw = custom.list_namespaced_custom_object(group="argoproj.io", version="v1alpha1", namespace="argocd", plural="applications")
        for item in raw.get("items", []):
            spec = item.get("spec", {})
            if spec.get("project") == "ai-platform":
                status = item.get("status", {})
                argo_apps.append({"name": item.get("metadata", {}).get("name"), "sync": status.get("sync", {}).get("status", "Unknown")})
    except Exception: pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pods": pod_rows,
        "deployments": deploy_rows,
        "argocd_apps": argo_apps,
    }


@router.post("/ops/actions/run-ingestor", response_model=TriggerIngestorResponse)
def run_ingestor(req: TriggerIngestorRequest) -> TriggerIngestorResponse:
    _ensure_k8s()
    batch = k8s_client.BatchV1Api()
    namespace = "ai-platform"
    cronjob_name = f"{req.source}-git-ingestor"

    try:
        cronjob = batch.read_namespaced_cron_job(name=cronjob_name, namespace=namespace)
        job = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(generate_name=f"{cronjob_name}-manual-"),
            spec=cronjob.spec.job_template.spec,
        )
        created = batch.create_namespaced_job(namespace=namespace, body=job)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"k8s api error: {exc.reason}") from exc

    return TriggerIngestorResponse(source=req.source, cronjob=cronjob_name, job_name=created.metadata.name)
