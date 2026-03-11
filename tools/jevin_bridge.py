#!/usr/bin/env python3
import sys
import json
import httpx
import asyncio
import argparse

API_URL = "http://jevin.michaelhomelab.work/agent/chat"

async def invoke_agent(prompt: str):
    print(f"[*] Delegating task to Jevin Agent...\n[*] Prompt: {prompt}\n")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                API_URL,
                json={"prompt": prompt}
            )
            if resp.status_code == 200:
                data = resp.json()
                print("--- JEVIN RESPONSE ---")
                print(data.get("response", "No response content found."))
                print("----------------------")
            else:
                print(f"[!] Error: Jevin returned HTTP {resp.status_code}")
                print(resp.text)
    except Exception as e:
        print(f"[!] Failed to connect to Jevin: {e}")
        print("Note: Ensure you are running this from a network that can resolve jevin.michaelhomelab.work")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delegate CLI tasks to the local Jevin ReAct Agent.")
    parser.add_argument("prompt", type=str, help="The instruction to send to Jevin.")
    args = parser.parse_args()
    
    asyncio.run(invoke_agent(args.prompt))
