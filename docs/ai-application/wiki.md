# AI Application Wiki

This document outlines the architecture, components, and purpose of the applications running under the `ai-application` section of the Homelab Kubernetes Platform. These services provide higher-level AI capabilities and observability for the core AI platform infrastructure.

## 1. Savant (formerly Knowledge Chat)

**Savant** is the internal AI knowledge base assistant. It provides a conversational interface backed by a hybrid Retrieval-Augmented Generation (RAG) pipeline to accurately answer questions based on the homelab's context, falling back to real-time web search if needed.

### Architecture diagram
* Frontend: React / Vite application providing a chat UI.
* Backend: Python FastAPI service.
* Embedding model: `vllm-embedding` via `infinity-embedding`
* Vector Database: `qdrant` (collection: knowledge)
* Reasoning LLM: `vllm-reasoning` (Ollama running `phi3.5`)
* Fallback Search: DuckDuckGo instant-answer API

### Request Flow
1. **User Query**: Receives query from the chat UI
2. **Embedding**: Uses the `infinity-embedding` API (`BAAI/bge-large-en-v1.5`) to embed the user query.
3. **Semantic Search**: Searches the internal knowledge base in Qdrant using the generated embedding.
4. **Fallback (Optional)**: If Qdrant returns no highly relevant context, it queries the DuckDuckGo API for external web context.
5. **Generation**: Builds a context-aware prompt and streams the response from Ollama (`phi3.5`).

## 2. Watchtower (formerly Sentinel)

**Watchtower** is the LLM inference observability dashboard. It provides real-time visibility into the performance and usage of the AI platform's models (coder, reasoning, embedding, and reranker). 

### Architecture diagram
* Frontend: React / Vite application providing a real-time feed UI.
* Backend: Python FastAPI service.
* Log Source: `loki-gateway` (Parses standard output logs from Ollama inferencing).
* Trace Source: `tempo-query-frontend` (Parses OpenTelemetry traces emitted by vLLM for embedding & reranker usage).

### Feed Sources
* **Reasoning Feed**: Parses Ollama JSON logs from Loki to extract input/output tokens and latency for the `vllm-reasoning` deployment.
* **Coder Feed**: Parses Ollama JSON logs from Loki for the `vllm-coder` deployment.
* **Embedding Feed**: Queries Tempo traces for `/v1/embeddings` requests (extracts `gen_ai.usage` attributes).
* **Reranker Feed**: Queries Tempo traces for `/v1/rerank` requests.

### Key Metrics Tracked
* `input_tokens` (prompt evaluation count)
* `output_tokens` (completion token count)
* `duration_ms` (total generation latency)
* `model` (model string used for inference)

## Recent Updates
* **Renaming**: `knowledge-chat` was renamed to `savant`, and `sentinel` was renamed to `watchtower`. Artifacts, UI text, Kubernetes manifests, and Github Actions workflows were updated to reflect this new nomenclature.
* Routes: Both services expose HTTPRoute definitions mapped to their respective ingress domains (`raphael.michaelhomelab.work` for Savant and `watchtower.michaelhomelab.work` for Watchtower).
