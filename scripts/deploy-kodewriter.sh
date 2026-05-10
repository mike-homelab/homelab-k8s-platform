#!/bin/bash

# Deployment script for Kodewriter platform components
REGISTRY="harbor.michaelhomelab.work/homelab/coding-agent"

echo "Building and pushing Harness API..."
docker build -t $REGISTRY/harness-api:latest ./platform/harness-api
docker push $REGISTRY/harness-api:latest

echo "Building and pushing Frontend..."
docker build -t $REGISTRY/agent-ui:latest ./platform/frontend
docker push $REGISTRY/agent-ui:latest

echo "Kodewriter images pushed to $REGISTRY"
echo "You can now sync the coding-agent application in ArgoCD."
