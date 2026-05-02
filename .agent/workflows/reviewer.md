---
description: Monitor CI/CD pipelines, digest GitHub Action failures, and auto-heal the workspace
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) for generation or logic. You MUST exclusively use the local LLM endpoints via `curl`. Cloud APIs are permitted ONLY for high-level orchestration; all reasoning, code generation, and research analysis MUST be offloaded to the cluster models. Usage of both Cloud and Local tokens MUST be reported at the end of the workflow.

1. Triggered on command or after pushing commits to check GitHub workflow statuses.
2. Use Antigravity to run `run_command` on the host: `gh run list --limit 5` to inspect the most recent build jobs.
3. If the most recent pipeline failed, run `run_command`: `gh run view --log` to extract the error traces. Since Antigravity directly runs on the host shell, it automatically authenticates seamlessly with your `gh auth status`.
4. Parse the error stack trace internally and identify what code caused the failure using the local `builder` model via the LiteLLM proxy.
5. Recursively invoke the `/coder` workflow strategy, passing the error logs, to formulate and patch the bug via the MCP server using the local `analyst` model.
6. Auto-commit the fix and run `gh run watch` to ensure the self-healed pipeline turns green.
7. If the failure involves a web application or UI component, trigger the `/tester` workflow to verify the fix in a live browser environment and capture final state diagnostics.
8. **Usage Reporting**: Report total token consumption for Cloud API and Cluster LLM, including cost savings.
