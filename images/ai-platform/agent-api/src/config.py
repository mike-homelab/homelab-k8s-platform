import os
import redis.asyncio as redis
from kubernetes import config as k8s_config
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Setup helpers
_k8s_ready = False
_otel_ready = False


def _ensure_k8s() -> None:
    global _k8s_ready
    if _k8s_ready:
        return
    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    _k8s_ready = True


def _ensure_otel(app) -> None:
    global _otel_ready
    if _otel_ready:
        return
    if os.getenv("OTEL_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        _otel_ready = True
        return

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://otel-collector.monitoring.svc.cluster.local:4318/v1/traces",
    )
    service_name = os.getenv("OTEL_SERVICE_NAME", "agent-api")

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    _otel_ready = True


def _base_url(model: str) -> str:
    # All LLM requests now route to the specialised coder model
    return os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")


def _redis_client() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://redis.ai-platform.svc.cluster.local:6379")
    return redis.from_url(url, decode_responses=True)


def _model_id(model: str) -> str:
    # Return the specialised coder model ID, defaulting to the 7B Instruct version
    return os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")


def _embedding_base_url() -> str:
    return os.getenv("EMBEDDING_BASE_URL", "http://embedding-api.ai-platform.svc.cluster.local:80")


def _qdrant_url() -> str:
    return os.getenv("QDRANT_URL", "http://qdrant.ai-platform.svc.cluster.local:6333")


def _tei_url() -> str:
    return os.getenv("TEI_URL", "http://reranker.ai-platform.svc.cluster.local:80")
