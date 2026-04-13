---
description: Monitor CI/CD pipelines, digest GitHub Action failures, and auto-heal the workspace
---
1. Triggered on command or after pushing commits to check GitHub workflow statuses.
2. Use Antigravity to run `run_command` on the host: `gh run list --limit 5` to inspect the most recent build jobs.
3. If the most recent pipeline failed, run `run_command`: `gh run view --log` to extract the error traces. Since Antigravity directly runs on the host shell, it automatically authenticates seamlessly with your `gh auth status`.
4. Parse the error stack trace internally and identify what code caused the failure.
5. Recursively invoke the `/coder` workflow strategy, passing the error logs, to formulate and patch the bug via the MCP server.
6. Auto-commit the fix and run `gh run watch` to ensure the self-healed pipeline turns green.
