---
description: Read the codebase architecture and update the knowledge wiki in the Obsidian vault using the host MCP server
---
1. Run after successful code patches or architecture changes.
2. Analyze the modified codebase or git diff representing the newly coded features.
3. Connect out or ensure the local fastmcp proxy is running on port 8080.
4. Using the MCP tools, specifically target the Obsidian vault at `/home/michael/obsidian/homelab-k8s-platform/`. Read the relevant application note (e.g., `Applications/Savant.md`) via `read_local_file`.
5. Compare the newly added code changes to the current documentation content. Formulate a technical writing payload using in-cluster LLM intelligence.
6. Write the unified documentation patch back exactly to the corresponding file in `/home/michael/obsidian/homelab-k8s-platform/` using `write_local_file`. Ensure architectural endpoints, models used, and the overall narrative are accurate.

