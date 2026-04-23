---
description: Monitor CI/CD pipelines, digest GitHub Action failures, and auto-heal the workspace
---
> [!IMPORTANT]
> **Cloud API Restriction**: Never use cloud APIs (Vertex, OpenAI, Anthropic, etc.) without explicit user approval. Request approval directly via Antigravity before any external calls.

1. Triggered on command or after pushing commits to check GitHub workflow statuses.
2. Use Antigravity to run `run_command` on the host: `gh run list --limit 5` to inspect the most recent build jobs.
3. If the most recent pipeline failed, run `run_command`: `gh run view --log` to extract the error traces. Since Antigravity directly runs on the host shell, it automatically authenticates seamlessly with your `gh auth status`.
4. Parse the error stack trace internally and identify what code caused the failure.
5. Recursively invoke the `/coder` workflow strategy, passing the error logs, to formulate and patch the bug via the MCP server.
6. Auto-commit the fix and run `gh run watch` to ensure the self-healed pipeline turns green.
7. If the failure involves a web application or UI component, trigger the `/tester` workflow to verify the fix in a live browser environment and capture final state diagnostics.
