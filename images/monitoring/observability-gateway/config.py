from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    prometheus_url: str = "http://mimir-query-frontend.monitoring.svc.cluster.local:8080"
    loki_url: str = "http://loki-gateway.monitoring.svc.cluster.local"
    tempo_url: str = "http://tempo-query-frontend.monitoring.svc.cluster.local:3100"
    
    timeout_seconds: int = 30
    retry_count: int = 3
    
    class Config:
        env_file = ".env"

settings = Settings()
