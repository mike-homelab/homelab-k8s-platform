---
description: Perform web searches, fetch documentation, and rerank context via the in-cluster embedding API, including local Obsidian knowledge.
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) for generation or logic. You MUST exclusively use the local LLM endpoints via `curl`. Cloud APIs are permitted ONLY for high-level orchestration; all reasoning, code generation, and research analysis MUST be offloaded to the cluster models. Usage of both Cloud and Local tokens MUST be reported at the end of the workflow.

1. Understand the user's research request. Identify missing context or unknown APIs required for the build.
2. Search the local Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/` for internal documentation, architecture notes, and credentials.
3. Use Antigravity web search tools to pull relevant documentation, repositories, or error solutions from the internet if internal docs are insufficient.
4. If the research requires interacting with a live web UI or extracting data from a dynamic page, trigger the `/tester` workflow to perform automated browser diagnostics and data capture.
5. Send an internal "Wake request" via `curl` to the LiteLLM proxy at `https://llm.michaelhomelab.work/v1/chat/completions` with a short dummy prompt for both `agent-executor` and `agent-planner`. This forces the backend Ollama models to load into GPU VRAM proactively, preventing NGINX 504 timeouts when sending large context payloads.
6. Pipe the raw search findings (local + web + browser logs) to the in-cluster Reranker at `https://llm.michaelhomelab.work/rerank/v1/rerank`.
7. Filter out any results that score poorly in the reranker response.
8. Compile the highest-rated context into a polished Research Report and implementation plan (`artifacts/research_plan.md`) using the local `agent-planner` model via the LiteLLM proxy. If code modifications are identified as necessary, consult the `agent-executor` model for implementation details and architectural patterns specific to the homelab environment documented in `/home/michael/obsidian/homelab-k8s-platform/`.
9. Present the summary to the user or trigger the `/coder` workflow to begin execution.
10. Trigger the `/writer` workflow to document the research findings and any identified architectural patterns into the Obsidian LLM wiki at `/home/michael/obsidian/homelab-k8s-platform/`.
11. **Usage Reporting**: Report total token consumption for Cloud API and Cluster LLM, including cost savings.
