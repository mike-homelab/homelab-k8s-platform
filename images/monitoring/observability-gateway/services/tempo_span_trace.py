from clients.prometheus import PrometheusClient
import logging

logger = logging.getLogger(__name__)

class TempoSpanTraceService:
    def __init__(self, prom_client: PrometheusClient):
        self.prom_client = prom_client

    async def get_span_summary(self, service_name: str, window: str = "5m"):
        # Example PromQL to get p99 latency from span metrics
        latency_query = f'histogram_quantile(0.99, sum(rate(duration_ms_bucket{{service_name="{service_name}"}}[{window}])) by (le, service_name))'
        rps_query = f'sum(rate(duration_ms_count{{service_name="{service_name}"}}[{window}])) by (service_name)'
        error_rate_query = f'sum(rate(duration_ms_count{{service_name="{service_name}", status_code="STATUS_CODE_ERROR"}}[{window}])) by (service_name)'
        
        try:
            latency_res = await self.prom_client.query(latency_query)
            rps_res = await self.prom_client.query(rps_query)
            error_res = await self.prom_client.query(error_rate_query)
            
            summary = {
                "service": service_name,
                "window": window,
                "p99_latency_ms": self._extract_value(latency_res),
                "requests_per_second": self._extract_value(rps_res),
                "error_rate_per_second": self._extract_value(error_res)
            }
            return summary
        except Exception as e:
            logger.error(f"Failed to get span summary for {service_name}: {e}")
            return {"error": str(e)}

    def _extract_value(self, prom_res):
        if prom_res and prom_res.get("data") and prom_res["data"].get("result"):
            return float(prom_res["data"]["result"][0]["value"][1])
        return 0.0
