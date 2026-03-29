---
description: Proxy prompts natively to Jevin while running the Host MCP Server
---
1. When the user requests `/jevin [prompt]`, dynamically start the MCP proxy loopback on the filesystem. Use `run_command`: `python /home/michael/homelab-k8s-platform/host_mcp_server.py &`.
2. Give the server roughly 2 seconds to initialize background sockets.
// turbo-all
3. Pass the user's prompt into the Kubernetes fabric via the external ingress gateway curl:
`curl -sS -X POST -H 'Content-Type: application/json' -d "{\"prompt\": \"<INSERT_USER_PROMPT_HERE>\"}" https://jevin.michaelhomelab.work/agent/chat`
4. Print the exact output summary from Jevin directly to the user for auditing verification.
