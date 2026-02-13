import os
import requests

PROM_URL = os.getenv("PROMETHEUS_BASE_URL")
LOKI_URL = os.getenv("LOKI_BASE_URL")

def query_prometheus(query: str):
    resp = requests.get(
        f"{PROM_URL}/api/v1/query",
        params={"query": query},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()

def query_loki(query: str, limit: int = 100):
    resp = requests.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": query,
            "limit": limit,
        },
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()
