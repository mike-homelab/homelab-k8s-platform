
# AI Agentic RAG Platform – Architecture & Implementation Plan

## 1. Project Objective

Build a **production-style AI platform** running on a Kubernetes homelab cluster with GPU acceleration.

The system will support three intelligent agents:

1. **Cloud Knowledge Agent**
   - RAG over AWS and Azure documentation
2. **Product Search Agent**
   - Fetch live product data from Amazon India and Flipkart India
3. **Observability Agent**
   - Chat interface for LGTM observability data

The platform will include:

- Multi‑agent orchestration
- GPU inference
- Agentic memory layers
- RAG knowledge retrieval
- Full observability using LGTM
- GitOps deployment
- CI pipeline for container images

(This document extends the original architecture plan provided by the user.)

---

# 2. Existing Infrastructure

## Kubernetes Cluster

Version: **v1.33**

Nodes:

| Node | Role | GPU |
|-----|------|-----|
| kubemaster01 | Control Plane | None |
| kubeworker01 | Worker | RTX 5070 Ti (16GB) |
| kubeworker02 | Worker | RTX 4070 Ti (16GB) |
| kubeworker03 | Worker | RTX 3060 (12GB) |

---

## Observability Stack (LGTM)

- Grafana
- Loki
- Mimir
- Tempo
- OpenTelemetry Collector

---

## Storage

Primary Object Storage:

- **MinIO**

---

## Networking

- Istio Service Mesh
- MetalLB Load Balancer

---

# 3. High Level Platform Architecture

```
User
 │
 ▼
Istio Gateway
 │
 ▼
AI Gateway (FastAPI)
 │
 ▼
Agent Orchestrator (LangGraph)
 │
 ├── Knowledge Agent
 ├── Product Search Agent
 └── Observability Agent
 │
 ▼
Tool Layer
 │
 ├── Vector Database
 ├── External APIs
 ├── LGTM Queries
 └── Web Automation Tools
 │
 ▼
GPU Inference Layer (vLLM)
```

---

# 4. Kubernetes Microservice Layout

Namespaces:

```
ai-gateway
ai-agents
ai-inference
ai-memory
ai-data
```

---

## ai-gateway

Services:

- chat-api (FastAPI)

Responsibilities:

- user chat interface
- request routing
- telemetry export

---

## ai-agents

Services:

- agent-orchestrator
- knowledge-agent
- product-agent
- observability-agent

Framework:

LangGraph

Responsibilities:

- agent orchestration
- tool execution
- memory access

---

## ai-inference

Services:

- vllm-server
- embedding-server

---

## ai-memory

Services:

- Redis
- Qdrant

Redis → short term memory  
Qdrant → vector search and long term memory

---

## ai-data

Services:

- document-ingestion
- chunking-workers
- embedding-workers
- product-scraper-workers

---

# 5. GPU Allocation Strategy

| Worker | GPU | Usage |
|------|------|------|
| kubeworker01 | RTX 5070 Ti | Main LLM inference |
| kubeworker02 | RTX 4070 Ti | Embedding server |
| kubeworker03 | RTX 3060 | Agents and tools |

---

# 6. Agent Responsibilities

## Knowledge Agent

Uses **RAG pipeline** to answer questions about AWS and Azure.

Workflow:

User Query  
→ Embedding  
→ Vector Search (Qdrant)  
→ Context Retrieval  
→ LLM Answer

---

## Product Search Agent

Fetches **live product data**.

Sources:

- Amazon India
- Flipkart India

Workflow:

User Query  
→ Agent planner  
→ Amazon worker  
→ Flipkart worker  
→ Results merge  
→ LLM summary

---

## Observability Agent

Allows chat queries against monitoring stack.

Queries:

- PromQL
- LogQL
- TraceQL

Example:

User: Why did inference latency spike?

Agent:
1 Query metrics from Mimir
2 Check logs in Loki
3 Inspect traces in Tempo
4 Generate explanation

---

# 7. Agentic Memory Architecture

## Short‑Term Memory

Redis

Stores:

- chat history
- session context

---

## Long‑Term Memory

Qdrant

Stores:

- vector embeddings
- knowledge documents

---

## Episodic Memory

Stores previous tasks and decisions performed by agents.

---

# 8. RAG Pipeline

Data Sources:

- AWS documentation
- Azure documentation
- Terraform docs
- internal documentation

Pipeline:

Document ingestion  
→ Chunking  
→ Embeddings  
→ Vector storage  
→ Retrieval  
→ LLM generation

---

# 9. Observability Architecture

All AI services export telemetry using **OpenTelemetry**.

Flow:

AI Services  
→ OpenTelemetry SDK  
→ OTEL Collector  

Metrics → Mimir  
Logs → Loki  
Traces → Tempo  

Visualization → Grafana

---

# 10. AI Observability Metrics

Key metrics:

- LLM inference latency
- tokens per second
- GPU utilization
- vector search latency
- embedding latency
- queue depth
- agent reasoning time

---

# 11. API Gateway Endpoints

/chat  
/product-search  
/observability

---

# 12. Security Model

Vault → secrets management  
Istio → mTLS between services  
Kubernetes RBAC → access control

---

# 13. CI/CD Architecture

CI → GitHub Actions  
CD → ArgoCD  
Registry → Nexus OSS

---

## Container Registry

Local registry:

Nexus OSS Docker Registry

Example images:

nexus.local/ai/chat-api:1.0.0  
nexus.local/ai/product-agent:1.0.0

---

## CI Pipeline

Developer push  
→ GitHub Actions  
→ Build Docker image  
→ Push to Nexus registry

---

## CD Pipeline

GitOps repo update  
→ ArgoCD detects changes  
→ Kubernetes deployment

---

# 14. Repository Structure

Application Repo

ai-platform-services

- chat-api
- agent-orchestrator
- knowledge-agent
- product-agent
- observability-agent

GitOps Repo

ai-platform-gitops

- namespaces
- ai-gateway
- ai-agents
- ai-inference
- ai-memory
- ai-data
- helm charts

---

# 15. Future Enhancements

- hybrid search
- reranking models
- GPU autoscaling
- multi‑tenant agents
- advanced Grafana dashboards

---

