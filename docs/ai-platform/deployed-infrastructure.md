# AI Platform Infrastructure Deployment

This document serves as a memory record for the deployed AI Platform infrastructure dynamically configured over Kubernetes.

## Hardware to Workload Mapping

The following setup aligns with the cluster's constraints, maximizing local GPU VRAM available and ensuring `OOM` avoidance safely.

### 1. `vllm-gemma` (Model Inference)
- **Node**: `NVIDIA-GeForce-RTX-5070-Ti` (16GB VRAM)
- **Model**: `google/gemma-4-e4b-it`
- **Config**: Native FP8 weights (`--quantization fp8`) alongside turboquant-style FP8 KV caching (`--kv-cache-dtype fp8`) to enable the highest safe context limit (131k tokens) within memory.
- **Engine**: `vllm`

### 2. `vllm-coder` (Code Assistance Inference)
- **Node**: `NVIDIA-GeForce-RTX-5060-Ti` (16GB VRAM)
- **Model**: `Qwen/Qwen2.5-Coder-3B-Instruct`
- **Config**: Leverages AWQ/FP8 execution configurations and dense FP8 KV cache memory scaling.
- **Engine**: `vllm`

### 3. `infinity-embedding` (Text Embedding & Reranking)
- **Node**: `NVIDIA-GeForce-RTX-3060` (12GB VRAM)
- **Models**:
  - `BAAI/bge-large-en-v1.5` (Embedding)
  - `BAAI/bge-reranker-large` (Reranker)
- **Config**: Serves *both* models within a single execution runtime natively without needing Kubernetes temporal GPU slicing.
- **Engine**: `michaelfeil/infinity`

### 4. `qdrant` (Vector Database)
- **Node**: CPU-bound execution (standard scheduling)
- **Role**: Persists chunked documentation text embedding structures generated from standard data pipelining. Wired over the cluster to `infinity-embedding` model calls.

## Configurations
- All Kubernetes definitions run under the namespace `ai-platform`.
- Manifest paths: `clusters/homelab/apps/ai-platform/vllm/*.yaml`.
