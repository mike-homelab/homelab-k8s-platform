import httpx
from config import settings
import logging

logger = logging.getLogger(__name__)

class TempoClient:
    def __init__(self):
        self.base_url = f"{settings.tempo_url}/api"
        self.timeout = settings.timeout_seconds

    async def get_trace(self, trace_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(settings.retry_count):
                try:
                    response = await client.get(f"{self.base_url}/traces/{trace_id}")
                    response.raise_for_status()
                    return response.json()
                except Exception as e:
                    logger.error(f"Tempo trace retrieval failed (attempt {attempt+1}): {e}")
                    if attempt == settings.retry_count - 1:
                        raise
        return None

    async def search(self, params: dict):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(settings.retry_count):
                try:
                    response = await client.get(f"{self.base_url}/search", params=params)
                    response.raise_for_status()
                    return response.json()
                except Exception as e:
                    logger.error(f"Tempo search failed (attempt {attempt+1}): {e}")
                    if attempt == settings.retry_count - 1:
                        raise
        return None
