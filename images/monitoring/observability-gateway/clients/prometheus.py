import httpx
from config import settings
import logging

logger = logging.getLogger(__name__)

class PrometheusClient:
    def __init__(self):
        self.base_url = f"{settings.prometheus_url}/api/v1"
        self.timeout = settings.timeout_seconds

    async def query(self, promql: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(settings.retry_count):
                try:
                    response = await client.get(
                        f"{self.base_url}/query",
                        params={"query": promql}
                    )
                    response.raise_for_status()
                    return response.json()
                except Exception as e:
                    logger.error(f"Prometheus query failed (attempt {attempt+1}): {e}")
                    if attempt == settings.retry_count - 1:
                        raise
        return None
