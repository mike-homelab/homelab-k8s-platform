---
description: Perform web searches, fetch documentation, and rerank context via the in-cluster embedding API, including local Obsidian knowledge.
---
1. Understand the user's research request. Identify missing context or unknown APIs required for the build.
2. Search the local Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/` for internal documentation, architecture notes, and credentials.
3. Use Antigravity web search tools to pull relevant documentation, repositories, or error solutions from the internet if internal docs are insufficient.
4. Pipe the raw search findings (local + web) to the in-cluster Reranker at `https://llm.michaelhomelab.work/rerank/v1/rerank`.
5. Filter out any results that score poorly in the reranker response.
6. Compile the highest-rated context into a polished Research Report (`artifacts/research_plan.md`).
7. Present the summary to the user or trigger the `/coder` workflow to begin execution.

