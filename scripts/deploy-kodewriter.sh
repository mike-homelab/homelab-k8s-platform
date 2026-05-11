#!/bin/bash

# Deployment script for Kodewriter platform components
REGISTRY="harbor.michaelhomelab.work/homelab/ai-agent/coding-agent"

echo "Building and pushing Harness API..."
docker build -t $REGISTRY/kodewriter-harness:latest ./images/ai-agent/coding-agent/kodewriter-harness
docker push $REGISTRY/kodewriter-harness:latest

echo "Building and pushing Frontend..."
docker build -t $REGISTRY/kodewriter-frontend:latest ./images/ai-agent/coding-agent/kodewriter-frontend
docker push $REGISTRY/kodewriter-frontend:latest

echo "Kodewriter images pushed to $REGISTRY"
echo "You can now sync the coding-agent application in ArgoCD."
