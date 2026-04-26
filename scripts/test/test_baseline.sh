#!/bin/bash

URL="http://localhost:8000/alert"
DATA='{
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
}'

echo "🚀 Triggering Raphael Baseline Test -> $URL"
curl -s -X POST "$URL" -H "Content-Type: application/json" -d "$DATA"
echo -e "\n✅ Test complete. Check Discord for 🚨 Alert and 🧠 AI Diagnosis."
