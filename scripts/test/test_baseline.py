import requests
import json
import sys

# Target Raphael Endpoint (via port-forward or internal service)
URL = "http://localhost:8000/alert" if "--local" in sys.argv else "http://raphael.ai-agent.svc:8000/alert"

data = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "RaphaelBaselineTest",
                "pod": "raphael-test-harness",
                "namespace": "ai-agent",
                "severity": "critical"
            },
            "annotations": {
                "description": "CRITICAL: Database connection timeout simulation. Detected in raphael-test-harness."
            }
        }
    ]
}

headers = {'Content-type': 'application/json'}

print(f"🚀 Triggering Raphael Baseline Test -> {URL}")
try:
    response = requests.post(URL, data=json.dumps(data), headers=headers)
    print(f"✅ Status Code: {response.status_code}")
    print(f"📝 Response: {response.text}")
except Exception as e:
    print(f"❌ Test Failed: {e}")
