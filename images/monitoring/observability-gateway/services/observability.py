from clients.prometheus import PrometheusClient
from clients.loki import LokiClient
import logging
import datetime

logger = logging.getLogger(__name__)

class ObservabilityService:
    def __init__(self, prom_client: PrometheusClient, loki_client: LokiClient):
        self.prom_client = prom_client
        self.loki_client = loki_client

    async def get_health(self, service_name: str):
        # Query Prometheus for the 'up' metric
        query = f'up{{job=~".*{service_name}.*"}}'
        try:
            res = await self.prom_client.query(query)
            is_up = False
            if res and res.get("data") and res["data"].get("result"):
                # If any instance is up (value 1), consider the service healthy
                is_up = any(float(r["value"][1]) == 1.0 for r in res["data"]["result"])
            
            return {
                "service": service_name,
                "status": "healthy" if is_up else "unhealthy",
                "up": is_up
            }
        except Exception as e:
            logger.error(f"Health check failed for {service_name}: {e}")
            return {"service": service_name, "status": "error", "error": str(e)}

    async def get_errors(self, service_name: str, window: str = "5m"):
        # Query Prometheus for error rate (HTTP 5xx)
        # Note: This query assumes standard metric names or labels. Adjust as needed for specific setup.
        prom_query = f'sum(rate(http_requests_total{{service=~".*{service_name}.*", status=~"5.."}}[{window}]))'
        
        # Query Loki for error logs
        loki_query = f'{{service=~".*{service_name}.*"}} |= "error" | logfmt'
        
        try:
            prom_res = await self.prom_client.query(prom_query)
            loki_res = await self.loki_client.query(loki_query, limit=10)
            
            error_rate = 0.0
            if prom_res and prom_res.get("data") and prom_res["data"].get("result"):
                error_rate = float(prom_res["data"]["result"][0]["value"][1])
            
            logs = []
            if loki_res and loki_res.get("data") and loki_res["data"].get("result"):
                for stream in loki_res["data"]["result"]:
                    for entry in stream.get("values", []):
                        logs.append(entry[1])
            
            return {
                "service": service_name,
                "window": window,
                "error_rate_per_second": error_rate,
                "recent_error_logs": logs[:5] # Limit to 5 for LLM consumption
            }
        except Exception as e:
            logger.error(f"Error retrieval failed for {service_name}: {e}")
            return {"service": service_name, "status": "error", "error": str(e)}
    async def get_summary(self, service_name: str, window: str = "5m"):
        """
        Provides a unified summary of metrics, logs, and traces for a service.
        """
        try:
            # Get basic health (Prometheus)
            health = await self.get_health(service_name)
            
            # Get error rate and logs (Prometheus + Loki)
            errors = await self.get_errors(service_name, window)
            
            # Get trace/span summary (Prometheus metrics generated from traces)
            # Using same logic as TempoSpanTraceService
            latency_query = f'histogram_quantile(0.99, sum(rate(duration_ms_bucket{{service_name="{service_name}"}}[{window}])) by (le, service_name))'
            rps_query = f'sum(rate(duration_ms_count{{service_name="{service_name}"}}[{window}])) by (service_name)'
            
            latency_res = await self.prom_client.query(latency_query)
            rps_res = await self.prom_client.query(rps_query)
            
            def extract_val(res):
                if res and res.get("data") and res["data"].get("result") and len(res["data"]["result"]) > 0:
                    return float(res["data"]["result"][0]["value"][1])
                return 0.0

            return {
                "service": service_name,
                "window": window,
                "health": health,
                "metrics": {
                    "p99_latency_ms": extract_val(latency_res),
                    "requests_per_second": extract_val(rps_res),
                    "error_rate_per_second": errors.get("error_rate_per_second", 0.0)
                },
                "logs": {
                    "recent_error_logs": errors.get("recent_error_logs", [])
                },
                "timestamp": datetime.datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Summary retrieval failed for {service_name}: {e}")
            return {"service": service_name, "status": "error", "error": str(e)}
