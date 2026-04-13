---
description: Read the codebase architecture and update the knowledge wiki using the host MCP server
---
1. Run after successful code patches or architecture changes.
2. Analyze the modified codebase or git diff representing the newly coded features.
3. Connect out or ensure the local fastmcp proxy is running on port 8080.
4. Using the MCP tools, specifically target the `docs/ai-application` folder (and specifically your `wiki.md`). Read the existing documentation via `read_local_file`.
5. Compare the newly added code changes to the current wiki content. Formulate a technical writing payload using in-cluster LLM intelligence.
6. Write the unified documentation patch back exactly to the `docs/ai-application/wiki.md` file using `write_local_file`. Ensure architectural endpoints, models used, and the overall narrative are accurate avoiding boilerplate filler.
