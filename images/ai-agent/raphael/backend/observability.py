import os
import io
import aiohttp
import asyncio
import discord
from discord.ext import tasks
import re

class ObservabilityEngine:
    def __init__(self):
        self.llm_host = os.getenv("LLM_HOST", "http://litellm.ai-platform.svc:4000/v1")
        self.llm_url = f"{self.llm_host}/chat/completions"
        self.loki_url = "http://loki-gateway.monitoring.svc/loki/api/v1/query_range"
    
    def start(self):
        self.monitor_logs.start()
        self.monitor_metrics.start()
        self.monitor_traces.start()

    @tasks.loop(minutes=2)
    async def monitor_logs(self):
        print("Running real-time log monitor...")
        query = '{namespace=~"ai-agent|monitoring|default"} |= "error" |~ "(?i)(exception|failed|fatal|error)"'
        params = {"query": query, "limit": 2, "direction": "backward"}
        await self._run_monitor(self.loki_url, params, "log")

    @tasks.loop(minutes=2)
    async def monitor_metrics(self):
        print("Running real-time metrics monitor...")
        query = '{namespace=~"ai-agent|monitoring"} |= "HTTP 5" |~ "(?i)error"'
        params = {"query": query, "limit": 2, "direction": "backward"}
        await self._run_monitor(self.loki_url, params, "metric")

    @tasks.loop(minutes=2)
    async def monitor_traces(self):
        print("Running real-time trace monitor...")
        query = '{namespace=~"ai-agent|monitoring"} |= "trace" |~ "(?i)error"'
        params = {"query": query, "limit": 2, "direction": "backward"}
        await self._run_monitor(self.loki_url, params, "trace")

    async def _run_monitor(self, url, params, monitor_type):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for result in data.get("data", {}).get("result", []):
                            pod_name = result.get("stream", {}).get("pod", "unknown")
                            logs = "\n".join([v[1] for v in result.get("values", [])])
                            if logs:
                                print(f"Detected {monitor_type} error in {pod_name}, running agentic diagnosis...")
                                diagnosis = await self.agentic_diagnosis(f"Real-time {monitor_type} error in {pod_name}", logs)
                                print(f"Diagnosis completed for {pod_name}. Sending to Discord...")
                                await self.send_diagnosis_to_discord(pod_name, diagnosis)
        except Exception as e:
            print(f"Monitor error ({monitor_type}): {e}")
            
    async def search_searxng(self, query: str):
        searxng_url = "http://searxng.ai-platform.svc:8080/search"
        params = {"q": query, "format": "json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(searxng_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        return "\n".join([f"- {r.get('title')}: {r.get('content')}" for r in results[:3]])
        except Exception as e:
            print(f"SearxNG error: {e}")
        return "No search results."

    async def agentic_diagnosis(self, alert_desc: str, logs: str):
        messages = [
            {"role": "system", "content": "You are an SRE AI diagnosing an issue. If you need more info to understand the error, output 'SEARCH: <query>'. If you are done determining the error, output 'DIAGNOSIS: <text>'."},
            {"role": "user", "content": f"Alert: {alert_desc}\nLogs: {logs}"}
        ]
        
        for _ in range(4):
            payload = {"model": "reasoning", "messages": messages, "temperature": 0.1}
            headers = {"Authorization": f"Bearer {os.getenv('LLM_KEY')}"}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.llm_url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                            
                            if "SEARCH:" in content:
                                match = re.search(r"SEARCH:\s*(.*)", content)
                                if match:
                                    query = match.group(1).strip()
                                    print(f"Agent requested search for: {query}")
                                    search_res = await self.search_searxng(query)
                                    messages.append({"role": "assistant", "content": content})
                                    messages.append({"role": "user", "content": f"Search Results:\n{search_res}"})
                                    continue
                            
                            if "DIAGNOSIS:" in content:
                                print("Agent reached final diagnosis.")
                                return content.replace("DIAGNOSIS:", "").strip()
                            print("Agent reached intermediate step or finished without DIAGNOSIS block.")
                            return content
            except Exception as e:
                print(f"LLM error: {e}")
                return "Diagnosis failed due to LLM error."
        return "Diagnosis incomplete after max steps."

    async def get_pod_logs(self, pod_name: str, namespace: str = "ai-agent"):
        query = f'{{pod="{pod_name}", namespace="{namespace}"}}'
        params = {"query": query, "limit": 50, "direction": "backward"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.loki_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logs = []
                        for result in data.get("data", {}).get("result", []):
                            for value in result.get("values", []):
                                logs.append(value[1])
                        return "\n".join(logs[-50:])
        except Exception as e:
            print(f"Error fetching logs from Loki: {e}")
        return "No logs found or Loki unreachable."

    async def handle_alert(self, alert_data: dict):
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return

        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            
            alerts = alert_data.get("alerts", [])
            for alert in alerts:
                labels = alert.get("labels", {})
                status = alert.get("status", "firing")
                pod_name = labels.get("pod")
                namespace = labels.get("namespace", "ai-agent")
                
                # 1. Post Initial Alert
                desc = alert.get("annotations", {}).get("description", "No description")
                embed = discord.Embed(
                    title=f"🚨 Alert: {labels.get('alertname', 'Unknown')}",
                    description=desc[:4000] if len(desc) > 4000 else desc,
                    color=discord.Color.red() if status == "firing" else discord.Color.green()
                )
                await webhook.send(embed=embed)

                # 2. Autonomous Diagnosis
                if status == "firing" and pod_name:
                    diagnosis = await self.agentic_diagnosis(desc, await self.get_pod_logs(pod_name, namespace))
                    
                    file = None
                    display_desc = diagnosis
                    
                    # If report is too long, attach as file
                    if len(diagnosis) > 4000:
                        display_desc = diagnosis[:1000] + "...\n\n📄 **Full diagnostic report attached below.**"
                        # Create in-memory file
                        file_data = io.BytesIO(diagnosis.encode('utf-8'))
                        file = discord.File(file_data, filename=f"diagnosis_{pod_name}.txt")

                    diag_embed = discord.Embed(
                        title=f"🧠 AI Diagnostic Report: {pod_name}",
                        description=display_desc,
                        color=discord.Color.blurple()
                    )
                    diag_embed.set_footer(text="Powered by local reasoning LLM (RTX 5070 Ti) & SearxNG")
                    
                    if file:
                        await webhook.send(embed=diag_embed, file=file)
                    else:
                        await webhook.send(embed=diag_embed)

    async def send_diagnosis_to_discord(self, pod_name, diagnosis):
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            print("DISCORD_WEBHOOK_URL not set! Cannot send Discord message.")
            return
        print(f"Sending diagnosis to webhook for pod: {pod_name}")
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            embed = discord.Embed(title=f"🚨 Real-time Monitor: {pod_name}", description=diagnosis[:4000], color=discord.Color.orange())
            embed.set_footer(text="Agentic Reasoning + SearxNG Internet Search")
            try:
                await webhook.send(embed=embed)
                print("Successfully sent to Discord via webhook.")
            except Exception as e:
                print(f"Failed to send to Discord webhook: {e}")
