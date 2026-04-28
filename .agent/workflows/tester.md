---
description: Execute browser-based UI testing and performance diagnostics using the browser_subagent and local reasoning models.
---
> [!IMPORTANT]
> **Cloud API Restriction**: NEVER use cloud APIs (Vertex, OpenAI, Anthropic, Gemini, etc.) for generation or logic. You MUST exclusively use the local LLM endpoints via `curl`. Cloud APIs are permitted ONLY for high-level orchestration; all reasoning, code generation, and research analysis MUST be offloaded to the cluster models. Usage of both Cloud and Local tokens MUST be reported at the end of the workflow.

1. **Initialization**: Navigate to the target URL provided by the calling workflow (Researcher, Reviewer, or User) using the `browser_subagent`.
2. **Interaction**: Perform the necessary user actions (clicking buttons, filling forms, refreshing, scrolling) to reach the target state.
3. **Diagnostic Capture**:
   - Capture a screenshot of the final UI state.
   - **Console Audit**: Capture all Browser Console logs (Errors, Warnings, Info).
   - **Network Audit**: Capture high-level network timings and 4xx/5xx status codes. Use `window.performance.getEntriesByType('resource')` via script execution to identify slow assets or failed requests.
4. **Data Synthesis**:
   - Aggregate the HTML structure, Console logs, and Performance metrics into a single context block.
5. **Local LLM Analysis**: 
   - Send the context block to the local `reasoning` model via the LiteLLM proxy at `https://llm.michaelhomelab.work/v1/chat/completions`.
   - **Analysis Prompt**: "Analyze the provided browser logs and metrics. Identify any JavaScript exceptions, network failures, or UI elements that failed to load. Evaluate the performance and functional state. Provide a Pass/Fail status."
6. **Reporting**: 
   - Present the pass/fail result with a summary of the analysis.
   - Detail the specific processing time identified and any critical errors found.
   - Attach the captured screenshot for visual verification.
7. **Usage Reporting**: Report total token consumption for Cloud API and Cluster LLM, including cost savings achieved compared to cloud-based testing.

// turbo
`run_command`: `curl -s -X POST https://llm.michaelhomelab.work/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer sk-michael-homelab-llm-proxy" -d '{"model": "reasoning", "messages": [{"role": "user", "content": "Analyze these logs: [PAGE_LOGS]"}]}'`
