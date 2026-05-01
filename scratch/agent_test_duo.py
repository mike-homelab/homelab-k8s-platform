import requests
import json
import time

LITELLM_BASE = "https://llm.michaelhomelab.work/v1"
LITELLM_KEY  = "sk-michael-homelab-llm-proxy"

def call_llm(model, system, user):
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
    }
    print(f"\n[CALLING {model}]...")
    start = time.time()
    response = requests.post(url, headers=headers, json=payload, verify=False)
    duration = time.time() - start
    
    if response.status_code != 200:
        return f"Error: {response.status_code} - {response.text}"
    
    content = response.json()['choices'][0]['message']['content']
    print(f"[DONE in {duration:.2f}s]")
    return content

def agentic_test():
    # 1. PLANNER: Break down a complex task
    planner_system = "You are the Agent Planner. Break down the user's request into 3 logical steps for an execution agent. Output only the steps in a numbered list."
    task = "Research the historical impact of the 1906 San Francisco earthquake on building codes and suggest 3 modern earthquake-resistant technologies."
    
    plan = call_llm("agent-planner", planner_system, task)
    print("\n--- PLAN (from DeepSeek-R1) ---")
    print(plan)
    
    # 2. EXECUTOR: Execute step 2 of the plan (formatting/tool-like)
    executor_system = "You are the Agent Executor. Format the provided information into a structured JSON report with keys: 'technology_name', 'mechanism', and 'benefit'. Output only JSON."
    execution_input = "Technology 1: Base Isolation. It decouples the building from the ground using flexible bearings. Benefit: Reduces seismic forces by 80%. Technology 2: Dampers. They act like shock absorbers. Benefit: Dissipates energy. Technology 3: Cross Laminated Timber. Lightweight and flexible. Benefit: Higher resilience."
    
    execution = call_llm("agent-executor", executor_system, execution_input)
    print("\n--- EXECUTION (from Mistral-Small) ---")
    print(execution)

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    agentic_test()
