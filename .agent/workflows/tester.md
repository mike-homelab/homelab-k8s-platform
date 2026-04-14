---
description: Execute browser-based UI testing and performance diagnostics using the browser_subagent and local reasoning models.
---

1. **Initialization**: Navigate to the target URL (e.g., `https://watchtower.michaelhomelab.work`) using the `browser_subagent`.
2. **Interaction**: Perform the necessary user actions (clicking buttons, filling forms, refreshing).
3. **Diagnostic Capture**:
   - Capture a screenshot of the final UI state.
   - **Console Audit**: Capture all Browser Console logs (Errors, Warnings, Info).
   - **Network Audit**: Capture high-level network timings and 4xx/5xx status codes. Use `window.performance.getEntriesByType('resource')` via script execution to identify slow assets.
4. **Data Synthesis**:
   - Aggregate the HTML structure, Console logs, and Performance metrics into a single context block.
5. **Local LLM Analysis**: 
   - Send the context block to the local reasoning model (`phi3.5`) at `https://llm.michaelhomelab.work/reasoning`.
   - **Analysis Prompt**: "Analyze the provided browser logs and metrics. Identify any JavaScript exceptions, network failures, or UI elements that failed to load. Evaluate the 'Time to First Byte' (TTFB) and report if it exceeds 500ms. Provide a Pass/Fail status."
6. **Reporting**: 
   - Present the pass/fail result.
   - Detail the specific processing time identified and any errors found.
   - Attach the captured screenshot for visual verification.
7. **Savings Calculation**: Report the tokens processed locally and the credit savings achieved.

// turbo
`run_command`: `curl -s -X POST https://llm.michaelhomelab.work/reasoning/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "phi3.5", "messages": [{"role": "user", "content": "Analyze these logs: [PAGE_LOGS]"}]}'`
