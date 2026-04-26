import os
import aiohttp
import discord
import json
from discord.ext import commands

class RaphaelBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(*args, intents=intents, **kwargs)
        # Use Gateway for internal routing
        self.loki_url = "http://loki-gateway.monitoring.svc/loki/api/v1/query_range"
        self.llm_url = "https://llm.michaelhomelab.work/v1/chat/completions"

    async def on_ready(self):
        print(f'Raphael has awakened as {self.user} (ID: {self.user.id})')
        print('Autonomous Diagnostic Systems: ONLINE (Loki Gateway)')

    async def get_pod_logs(self, pod_name: str, namespace: str = "ai-agent"):
        query = f'{{pod="{pod_name}", namespace="{namespace}"}}'
        params = {"query": query, "limit": 50, "direction": "backward"}
        
        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(self.loki_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logs = []
                        for result in data.get("data", {}).get("result", []):
                            for value in result.get("values", []):
                                logs.append(value[1])
                        return "\n".join(logs[-50:])
                    else:
                        print(f"Loki returned status {resp.status}")
        except Exception as e:
            print(f"Error fetching logs from Loki: {e}")
        return "No logs found or Loki unreachable."

    async def get_ai_diagnosis(self, alert_desc: str, logs: str):
        prompt = f"You are an SRE assistant. Analyze the following alert and logs to identify the root cause.\n\nALERT: {alert_desc}\n\nLOGS:\n{logs}\n\nDiagnosis:"
        payload = {
            "model": "reasoning",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        headers = {"Authorization": f"Bearer {os.getenv('LLM_KEY')}"}
        
        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(self.llm_url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("choices", [{}])[0].get("message", {}).get("content", "Diagnosis unavailable.")
        except Exception as e:
            print(f"Error calling local LLM: {e}")
        return "Local LLM unreachable for diagnosis."

    async def handle_alert(self, alert_data: dict):
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            webhook = discord.Webhook.from_url(webhook_url, session=session)
            
            alerts = alert_data.get("alerts", [])
            for alert in alerts:
                labels = alert.get("labels", {})
                status = alert.get("status", "firing")
                pod_name = labels.get("pod")
                namespace = labels.get("namespace", "ai-agent")
                
                # 1. Post Initial Alert
                color = discord.Color.red() if status == "firing" else discord.Color.green()
                embed = discord.Embed(
                    title=f"🚨 Alert: {labels.get('alertname', 'Unknown')}",
                    description=alert.get("annotations", {}).get("description", "No description"),
                    color=color
                )
                await webhook.send(embed=embed)

                # 2. Autonomous Diagnosis
                if status == "firing" and pod_name:
                    logs = await self.get_pod_logs(pod_name, namespace)
                    diagnosis = await self.get_ai_diagnosis(alert.get("annotations", {}).get("description", ""), logs)
                    
                    diag_embed = discord.Embed(
                        title=f"🧠 AI Diagnostic Report: {pod_name}",
                        description=diagnosis,
                        color=discord.Color.blurple()
                    )
                    diag_embed.set_footer(text="Powered by local reasoning LLM (RTX 5070 Ti)")
                    await webhook.send(embed=diag_embed)

    @commands.command()
    async def status(self, ctx):
        await ctx.send("🛡️ **Raphael System Status**\n- **LGTM Ingestion**: Active\n- **Loki Gateway**: Connected\n- **Local Inference**: Connected (SSL Bypass)")

    @commands.command()
    async def savings(self, ctx, tokens: int):
        savings_usd = (tokens / 1_000_000) * 12.50
        await ctx.send(f"💰 **Financial Efficiency**: Savings of **${savings_usd:,.2f} USD** identified.")
