---
description: Perform web searches, fetch documentation, and rerank context via the in-cluster embedding API
---
1. Understand the user's research request. Identify missing context or unknown APIs required for the build.
2. Use Antigravity web search tools to pull relevant documentation, repositories, or error solutions from the internet.
3. Pipe the raw search findings to the in-cluster Reranker at `https://llm.michaelhomelab.work/rerank/v1/rerank` (Running `BAAI/bge-reranker-large`). You can use a `curl` command or python script via `run_command` to hit the reranker API. Ensure the data is formatted according to the reranker API schema.
4. Filter out any results that score poorly in the reranker response.
5. Compile the highest-rated context into a polished Research Report (`artifacts/research_plan.md`) describing the precise steps and dependencies required.
6. Present the summary to the user or trigger the `/coder` workflow to begin execution.
