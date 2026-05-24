import re

with open("images/ai-agent/raphael/backend/discord_bot.py", "r") as f:
    content = f.read()

# Make sure imports are there
if "from discord.ext import commands, tasks" not in content:
    content = content.replace("from discord.ext import commands", "from discord.ext import commands, tasks\nimport re\nimport asyncio")

# Add setup_hook
setup_hook_code = """
    async def setup_hook(self):
        self.realtime_monitor.start()

    @tasks.loop(minutes=2)
    async def realtime_monitor(self):
        print("Running real-time observability monitor (logs, metrics, traces)...")
        # Monitor Logs
        query = '{namespace=~"ai-agent|monitoring|default"} |= "error" |~ "(?i)(exception|failed|fatal|error)"'
        params = {"query": query, "limit": 2, "direction": "backward"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.loki_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for result in data.get("data", {}).get("result", []):
                            pod_name = result.get("metric", {}).get("pod", "unknown")
                            logs = "\\n".join([v[1] for v in result.get("values", [])])
                            if logs:
                                print(f"Detected error in {pod_name}, running agentic diagnosis...")
                                diagnosis = await self.agentic_diagnosis(f"Real-time log error in {pod_name}", logs)
                                await self.send_diagnosis_to_discord(pod_name, diagnosis)
        except Exception as e:
            print(f"Monitor error: {e}")
            
    async def search_searxng(self, query: str):
        searxng_url = "http://searxng.ai-platform.svc:8080/search"
        params = {"q": query, "format": "json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(searxng_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        return "\\n".join([f"- {r.get('title')}: {r.get('content')}" for r in results[:3]])
        except Exception as e:
            print(f"SearxNG error: {e}")
        return "No search results."

    async def agentic_diagnosis(self, alert_desc: str, logs: str):
        messages = [
            {"role": "system", "content": "You are an SRE AI diagnosing an issue. If you need more info to understand the error, output 'SEARCH: <query>'. If you are done determining the error, output 'DIAGNOSIS: <text>'."},
            {"role": "user", "content": f"Alert: {alert_desc}\\nLogs: {logs}"}
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
                                match = re.search(r"SEARCH:\\s*(.*)", content)
                                if match:
                                    query = match.group(1).strip()
                                    search_res = await self.search_searxng(query)
                                    messages.append({"role": "assistant", "content": content})
                                    messages.append({"role": "user", "content": f"Search Results:\\n{search_res}"})
                                    continue
                            
                            if "DIAGNOSIS:" in content:
                                return content.replace("DIAGNOSIS:", "").strip()
                            return content
            except Exception as e:
                print(f"LLM error: {e}")
                return "Diagnosis failed due to LLM error."
        return "Diagnosis incomplete after max steps."

    async def send_diagnosis_to_discord(self, pod_name, diagnosis):
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if not webhook_url: return
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            embed = discord.Embed(title=f"🚨 Real-time Monitor: {pod_name}", description=diagnosis[:4000], color=discord.Color.orange())
            embed.set_footer(text="Agentic Reasoning + SearxNG Internet Search")
            await webhook.send(embed=embed)
"""

if "def setup_hook" not in content:
    content = content.replace("async def on_ready(self):", setup_hook_code + "\n    async def on_ready(self):")

# Update get_ai_diagnosis to use the new agentic_diagnosis
content = content.replace("async def get_ai_diagnosis(self, alert_desc: str, logs: str):", "async def old_get_ai_diagnosis(self, alert_desc: str, logs: str):")
content = content.replace("diagnosis = await self.get_ai_diagnosis(desc, await self.get_pod_logs(pod_name, namespace))", "diagnosis = await self.agentic_diagnosis(desc, await self.get_pod_logs(pod_name, namespace))")

with open("images/ai-agent/raphael/backend/discord_bot.py", "w") as f:
    f.write(content)

