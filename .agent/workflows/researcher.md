---
description: Perform web searches, fetch documentation, and rerank context via the in-cluster embedding API, including local Obsidian knowledge.
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) under ANY circumstances. You MUST exclusively use the local LLM endpoints.

1. Understand the user's research request. Identify missing context or unknown APIs required for the build.
2. Search the local Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/` for internal documentation, architecture notes, and credentials.
3. Use Antigravity web search tools to pull relevant documentation, repositories, or error solutions from the internet if internal docs are insufficient.
4. If the research requires interacting with a live web UI or extracting data from a dynamic page, trigger the `/tester` workflow to perform automated browser diagnostics and data capture.
5. Send an internal "Wake request" via `curl` to the target local LLM endpoints (`https://llm.michaelhomelab.work/coder` or `reasoning`) with a short dummy prompt. This forces Ollama to load the model into GPU VRAM proactively, preventing NGINX 504 timeouts when sending large context payloads.
6. Pipe the raw search findings (local + web + browser logs) to the in-cluster Reranker at `https://llm.michaelhomelab.work/rerank/v1/rerank`.
7. Filter out any results that score poorly in the reranker response.
8. Compile the highest-rated context into a polished Research Report (`artifacts/research_plan.md`) using the `vllm-coder` (Qwen-3.5-9B) as the primary reasoning engine for data analysis.
9. Present the summary to the user or trigger the `/coder` workflow to begin execution.
10. Trigger the `/writer` workflow to document the research findings and any identified architectural patterns into the Obsidian LLM wiki.
