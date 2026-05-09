import asyncio
import json
from unittest.mock import AsyncMock, patch
import sys
import os

# Add the gateway directory to path
sys.path.append(os.path.abspath("images/monitoring/observability-gateway"))

from services.observability import ObservabilityService
from clients.prometheus import PrometheusClient
from clients.loki import LokiClient

async def verify_logic():
    print("--- Verifying Observability Gateway Logic ---")
    
    # Mock clients
    mock_prom = AsyncMock(spec=PrometheusClient)
    mock_loki = AsyncMock(spec=LokiClient)
    
    # Setup mock data for health
    mock_prom.query.side_effect = [
        # Health check query result
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1714930000, "1"]}]
            }
        },
        # Error rate query result
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1714930000, "0.05"]}]
            }
        },
        # Latency query result
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1714930000, "150.5"]}]
            }
        },
        # RPS query result
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1714930000, "10.2"]}]
            }
        }
    ]
    
    mock_loki.query.return_value = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"service": "builder"},
                    "values": [
                        ["1714930000000000000", "2024-05-05 12:00:00 ERROR Database timeout"]
                    ]
                }
            ]
        }
    }
    
    service = ObservabilityService(mock_prom, mock_loki)
    
    print("Executing get_summary('builder', '5m')...")
    summary = await service.get_summary("builder", "5m")
    
    print("\nResulting Structure:")
    print(json.dumps(summary, indent=2))
    
    # Validation
    required_keys = ["service", "window", "health", "metrics", "logs", "timestamp"]
    missing = [k for k in required_keys if k not in summary]
    
    if not missing:
        print("\n✅ Verification Successful: All structured details are present.")
    else:
        print(f"\n❌ Verification Failed: Missing keys {missing}")

if __name__ == "__main__":
    asyncio.run(verify_logic())
