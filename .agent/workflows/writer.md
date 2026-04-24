---
description: Read the codebase architecture and update the knowledge wiki in the Obsidian vault using the host MCP server
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) under ANY circumstances. You MUST exclusively use the local LLM endpoints.

1. Run after successful code patches, architecture changes, or research phases.
2. Perform a targeted search across the codebase and existing Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/` (leveraging the /researcher pattern) to identify the most relevant files to update.
3. Analyze the research output and the git diff/change logs representing the newly implemented features.
4. Using the MCP tools, connect to the local fastmcp proxy on port 8080.
5. Read the target Obsidian knowledge base files identified in Step 2 via `read_local_file`.
6. Formulate a technical documentation update using the `vllm-coder` (Qwen-3.5-9B) reasoning engine, ensuring all architectural endpoints and model specifications are accurate.
7. Write the documentation update back to the corresponding file in the Obsidian vault using `write_local_file`.
