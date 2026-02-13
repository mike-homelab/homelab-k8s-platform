# AI_PLATFORM_CONTEXT.md

**Authoritative System Context for Unified AI Platform**

---

## 0. Purpose of This File
This document is the **single source of truth** for designing, deploying, and operating a **unified AI platform** in a Kubernetes homelab.

Any AI, automation, or human operator **must follow this context strictly**:
- Do not change architectural choices
- Do not replace selected tools or models
- Do not introduce SaaS or proprietary components
- Do not hardcode GPU nodes
- Do not bypass Argo CD

---

## 1. Platform Goals

Build **one shared AI system** that provides:

### 1.1 VS Code Coding Agent
- Repo-aware coding assistant
- Diff-based code generation
- Code explanation and refactoring
- Optional **scoped** internet access (e.g. Helm values, CRDs)

### 1.2 Gemini-style Web Application
- Live internet search
- Web scraping
- Source-grounded answers with citations
- Multi-step reasoning

### 1.3 Observability Agent
- Reads homelab telemetry
- Explains metrics, logs, and traces
- Read-only access to infrastructure

All three solutions **share the same LLM inference layer**.

---

## 2. Deployment Constraints (Hard Rules)

### 2.1 Kubernetes
- Platform runs **entirely on Kubernetes**
- All components are deployed via **Argo CD**
- GitOps is mandatory

### 2.2 GPUs
- NVIDIA GPU Operator is already installed
- GPU selection **must use labels exposed by GPU Operator**
- Node names must never be hardcoded

Expected GPU labels include (examples):
```
nvidia.com/gpu.product
nvidia.com/gpu.count
```

---

## 3. Available GPU Hardware

| GPU Model | VRAM | Intended Role |
|---------|------|---------------|
| RTX 5070 Ti | 16 GB | Primary reasoning & synthesis |
| RTX 5060 Ti | 16 GB | Code execution & parallel inference |
| RTX 3060 OC | 12 GB | Embeddings & reranking |

GPUs are **shared across all agents**, not statically assigned per application.

---

## 4. LLM & Embedding Models (Fixed)

### 4.1 LLMs
- **LLaMA-3.1-8B-Instruct (Q5 quantized)**  
  → Primary planner & synthesizer

- **Mixtral-8x7B (Q4 quantized)**  
  → Code-heavy tasks and execution

### 4.2 Embeddings
- **bge-large-en**

Constraints:
- No closed models
- No SaaS APIs (OpenAI, Gemini, Anthropic, etc.)

---

## 5. Inference Layer

### 5.1 Engine
- **vLLM**
- One Deployment per GPU class
- OpenAI-compatible API
- Token streaming enabled
- Continuous batching enabled

### 5.2 GPU Scheduling Rules
Each vLLM Deployment:
- Requests exactly `nvidia.com/gpu: 1`
- Uses `nodeSelector` or `nodeAffinity`
- Matches **GPU model labels**, not node names

Intent example:
```
Planner LLM must run only on RTX 5070 Ti GPUs
```

---

## 6. Agent Architecture

### 6.1 Orchestration
- **LangGraph is mandatory**
- No LangChain agent abstractions
- Explicit state-machine graphs only

### 6.2 Agent Router
A single **Agent Router**:
- Classifies user intent
- Selects the correct agent graph
- Injects appropriate tools
- Applies safety guardrails
- Emits observability telemetry

---

## 7. Agents (Fixed Responsibilities)

### 7.1 VS Code Coding Agent
- Default: **no unrestricted internet access**
- Allowed tools:
  - Repository file reader
  - Git diff generation
  - AST parsing
  - RAG over codebase

Optional **scoped** tools (explicit enablement only):
- Helm repository fetch
- Raw GitHub fetch (version or commit pinned)

### 7.2 Web (Gemini-style) Agent
- Mandatory live internet access
- Multi-step reasoning and planning

Tools:
- Web search
- Web scraping (HTTP fetch)
- HTML → Markdown conversion
- RAG ingestion into vector database

All outputs must include **citations**.

### 7.3 Observability Agent
- Strictly read-only
- No mutation tools allowed

Data sources:
- Kubernetes API
- Prometheus / Mimir
- Loki
- Tempo
- Argo CD

Purpose:
- Explain incidents
- Summarize system health
- Diagnose anomalies

---

## 8. Vector & Data Storage

### 8.1 Vector Database
- **Qdrant** (mandatory)

Stores:
- Code embeddings
- Web content embeddings
- Infrastructure summaries

Requirements:
- Payload filtering
- Multi-collection isolation

### 8.2 Relational & Memory Store
- PostgreSQL + pgvector (optional, allowed)

Stores:
- Conversation memory
- Agent state
- Tool outputs
- Metadata

Qdrant must **not** be replaced with ChromaDB.

---

## 9. Observability (LGTM Stack)

The AI platform must emit telemetry to the LGTM stack.

### 9.1 Metrics
- Tokens per second
- Tool latency
- Retry counts
- GPU utilization
- Request queue depth

### 9.2 Logs
- Agent decisions
- Tool execution results (redacted where needed)
- Validation failures

### 9.3 Traces
- Full request lifecycle
- Planner → tools → synthesis

Instrumentation standard:
- **OpenTelemetry**

---

## 10. Security & Guardrails

- NetworkPolicies between agents and tools
- Tool allow-lists per agent
- Max tool calls per request
- Max context size
- Domain allow-list for web scraping
- Observability agent remains read-only

---

## 11. GitOps Repository Layout (Expected)

```
ai-platform/
├── argocd/
├── inference/
├── router/
├── agents/
├── tools/
├── vector/
├── ui/
├── observability/
└── docs/
```

All components are deployed as **Argo CD Applications**.

---

## 12. Explicit Non-Goals

The platform must **NOT**:
- Use SaaS LLM APIs
- Require internet for core inference
- Duplicate LLMs per agent
- Hardcode GPU node names
- Auto-modify infrastructure
- Bypass Argo CD

---

## 13. Instruction to Any AI Using This Context

> Treat this document as **authoritative system context**.  
> Do not redesign or reinterpret unless explicitly instructed.  
> Ask for clarification only if required information is missing.

---

## 14. Usage Instructions

1. Commit this file to the repository
2. Provide it as the **first message** to any AI system
3. Then issue task-specific instructions

This prevents architectural drift and hallucinated redesigns.
