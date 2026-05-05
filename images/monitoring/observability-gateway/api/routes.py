from fastapi import APIRouter, Query, Depends
from services.observability import ObservabilityService
from services.tempo_span_trace import TempoSpanTraceService
from clients.prometheus import PrometheusClient
from clients.loki import LokiClient

router = APIRouter()

# Dependency injection helpers
def get_prom_client():
    return PrometheusClient()

def get_loki_client():
    return LokiClient()

def get_obs_service(
    prom: PrometheusClient = Depends(get_prom_client),
    loki: LokiClient = Depends(get_loki_client)
):
    return ObservabilityService(prom, loki)

def get_tempo_service(prom: PrometheusClient = Depends(get_prom_client)):
    return TempoSpanTraceService(prom)

@router.get("/service/health")
async def get_service_health(
    name: str = Query(..., description="Service name to check health for"),
    service: ObservabilityService = Depends(get_obs_service)
):
    return await service.get_health(name)

@router.get("/service/errors")
async def get_service_errors(
    name: str = Query(..., description="Service name to check errors for"),
    window: str = Query("5m", description="Time window for error calculation"),
    service: ObservabilityService = Depends(get_obs_service)
):
    return await service.get_errors(name, window)

@router.get("/trace/summary")
async def get_trace_summary(
    service_name: str = Query(..., alias="service", description="Service name to get trace summary for"),
    window: str = Query("5m", description="Time window for trace summary"),
    service: TempoSpanTraceService = Depends(get_tempo_service)
):
    return await service.get_span_summary(service_name, window)
