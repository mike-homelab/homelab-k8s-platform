import os
import io
import aiohttp
import discord
from discord.ext import commands

class RaphaelBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(*args, intents=intents, **kwargs)
        # Use environment variable for host to allow internal/external switching
        self.llm_host = os.getenv("LLM_HOST", "http://litellm.ai-platform.svc:4000/v1")
        self.llm_url = f"{self.llm_host}/chat/completions"
        self.loki_url = "http://loki-gateway.monitoring.svc/loki/api/v1/query_range"

    async def on_ready(self):
        print(f'Raphael has awakened as {self.user} (ID: {self.user.id})')
        print('Autonomous Diagnostic Systems: ONLINE (Internal Direct Access)')

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

    async def get_ai_diagnosis(self, alert_desc: str, logs: str):
        prompt = f"You are an SRE assistant. Analyze the following alert and logs to identify the root cause.\n\nALERT: {alert_desc}\n\nLOGS:\n{logs}\n\nDiagnosis:"
        payload = {
            "model": "reasoning",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        headers = {"Authorization": f"Bearer {os.getenv('LLM_KEY')}"}
        try:
            async with aiohttp.ClientSession() as session:
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
                    diagnosis = await self.get_ai_diagnosis(desc, await self.get_pod_logs(pod_name, namespace))
                    
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
                    diag_embed.set_footer(text="Powered by local reasoning LLM (RTX 5070 Ti)")
                    
                    if file:
                        await webhook.send(embed=diag_embed, file=file)
                    else:
                        await webhook.send(embed=diag_embed)

    @commands.command()
    async def status(self, ctx):
        await ctx.send("🛡️ **Raphael System Status**\n- **LGTM Ingestion**: Active\n- **Internal AI Mesh**: Connected (Direct Service)\n- **Diagnostic Engine**: Active")

    @commands.command()
    async def savings(self, ctx, tokens: int):
        savings_usd = (tokens / 1_000_000) * 12.50
        await ctx.send(f"💰 **Financial Efficiency**: Savings of **${savings_usd:,.2f} USD** identified.")
