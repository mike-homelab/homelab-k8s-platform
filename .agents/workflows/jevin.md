---
description: Ask the Jevin agent to autonomously complete a coding task
---

1. When the user requests `/jevin [prompt]`, take their instructions and formulate a direct message for the Jevin agent.
2. Use the `run_command` tool to securely proxy a cURL request into the Kubernetes cluster to hit the internally hosted Jevin endpoint. 
// turbo-all
3. Run the following command exactly, injecting the user's prompt as the JSON payload string:
`kubectl exec -n ai-platform deployments/agent-api -- curl -sS -X POST -H 'Content-Type: application/json' -d "{\"prompt\": \"<INSERT_USER_PROMPT_HERE>\"}" http://jevin:8000/agent/chat`
4. Wait for the command to return. The Jevin system processes synchronous requests, so it'll execute its loops, open a Pull Request locally in Git, and return the final HTML URL of the PR.
5. Provide Jevin's exact text summary to the user!
