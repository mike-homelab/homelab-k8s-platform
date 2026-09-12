---
description: Read the codebase architecture and update the knowledge wiki in the Obsidian vault
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) for generation or logic. You MUST exclusively use the local LLM endpoints via `curl`. Cloud APIs are permitted ONLY for high-level orchestration; all reasoning, code generation, and research analysis MUST be offloaded to the cluster models. Usage of both Cloud and Local tokens MUST be reported at the end of the workflow.

0. **Memory Bank Context**: Read and incorporate the context from `c:\workspace\homelab-memory-bank.html` before proceeding.
1. Run after successful code patches, architecture changes, or research phases.
2. Perform a targeted search across the codebase and existing Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/` (leveraging the /researcher pattern) to identify the most relevant files to update.
3. Analyze the research output and the git diff/change logs representing the newly implemented features.
4. Read the target Obsidian knowledge base files identified in Step 2 via `view_file`.
5. Formulate a technical documentation update using the local `builder` model via the LiteLLM proxy at `https://llm.michaelhomelab.work/v1/chat/completions`, ensuring all architectural endpoints and model specifications are accurate.
6. Write the documentation update back to the corresponding file in the Obsidian vault using `replace_file_content` or `write_to_file`.
7. **Usage Reporting**: Report total token consumption for Cloud API and Cluster LLM, including cost savings.
