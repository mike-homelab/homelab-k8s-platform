# AI Platform Infrastructure Deployment

This document serves as a memory record for the deployed AI Platform infrastructure dynamically configured over Kubernetes.

## Hardware to Workload Mapping

The following setup aligns with the cluster's constraints, maximizing local GPU VRAM available and ensuring `OOM` avoidance safely.

### 1. `vllm-reasoning` (Logic & Reasoning Inference)
- **Node**: `NVIDIA-GeForce-RTX-5060-Ti` (16GB VRAM)
- **Model**: `phi3.5:latest`
- **Config**: Optimized for high context (128k tokens).
- **Engine**: `ollama` (via `ollama/ollama:latest` container)

### 2. `vllm-coder` (Code Assistance Inference)
- **Node**: `NVIDIA-GeForce-RTX-5070-Ti` (16GB VRAM)
- **Model**: `ibm-granite/granite3.1-dense:2b`
- **Config**: Leveraging dense weights for high-precision code generation. Context: 128k tokens.
- **Engine**: `ollama`

### 3. `infinity-embedding` (Text Embedding & Reranking)
- **Node**: `NVIDIA-GeForce-RTX-3060` (12GB VRAM)
- **Image**: `vllm-embedding:latest`
- **Engine**: `vllm.entrypoints.openai.api_server`
- **Models**:
  - `BAAI/bge-large-en-v1.5` (Embedding) - Port 8000
  - `BAAI/bge-reranker-large` (Reranker) - Port 8001
- **Config**: Dual-service pod handling both embedding and reranking tasks natively.

### 4. `qdrant` (Vector Database)
- **Node**: CPU-bound execution (standard scheduling)
- **Role**: Persists chunked documentation text embedding structures. Wired to `infinity-embedding` (Port 8000).

## Network Topology & Routing

The platform uses **Nginx Gateway Fabric** for external access and internal service abstraction.

### Unified LLM Gateway
- **Domain**: `llm.michaelhomelab.work`
- **Ingress**: `HTTPRoute` with `URLRewrite` filters.

| Path | Backend Service | Internal Port | Purpose |
|---|---|---|---|
| `/coder` | `vllm-coder` | 11434 | Coding Assistant API |
| `/reasoning` | `vllm-reasoning` | 11434 | General Reasoning API |
| `/embedding` | `infinity-embedding` | 8000 | Vector Embeddings |
| `/rerank` | `infinity-embedding` | 8001 | Cross-Encoder Reranking |

## Configurations
- All Kubernetes definitions run under the namespace `ai-platform`.
- Manifest paths: `clusters/homelab/apps/ai-platform/vllm/*.yaml`.
- Gateway paths: `clusters/homelab/infra/nginx-gateway-fabric/*.yaml`.
