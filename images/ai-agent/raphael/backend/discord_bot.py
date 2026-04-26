import os
import discord
from discord.ext import commands

class RaphaelBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(*args, intents=intents, **kwargs)

    async def on_ready(self):
        print(f'Raphael has awakened as {self.user} (ID: {self.user.id})')
        print('Observability modules active.')

    async def handle_alert(self, alert_data: dict):
        """
        Process incoming Grafana alert payloads and notify Discord
        """
        channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        channel = self.get_channel(channel_id)
        if not channel:
            print(f"Error: Channel {channel_id} not found.")
            return

        alerts = alert_data.get("alerts", [])
        for alert in alerts:
            status = alert.get("status", "firing")
            color = discord.Color.red() if status == "firing" else discord.Color.green()
            
            embed = discord.Embed(
                title=f"🚨 Alert: {alert.get('labels', {}).get('alertname', 'Unknown')}",
                description=alert.get("annotations", {}).get("description", "No description"),
                color=color
            )
            
            # Add Labels
            labels = alert.get("labels", {})
            label_text = "\n".join([f"**{k}**: {v}" for k, v in labels.items() if k != "alertname"])
            if label_text:
                embed.add_field(name="Labels", value=label_text, inline=False)
            
            # Add Annotations
            annotations = alert.get("annotations", {})
            anno_text = "\n".join([f"**{k}**: {v}" for k, v in annotations.items() if k != "description"])
            if anno_text:
                embed.add_field(name="Annotations", value=anno_text, inline=False)

            await channel.send(embed=embed)

    @commands.command()
    async def status(self, ctx):
        await ctx.send("🛡️ **Raphael System Status**\n- **LGTM Ingestion**: Active\n- **Discord Bridge**: Connected\n- **Local Inference**: Operational")

    @commands.command()
    async def savings(self, ctx, tokens: int):
        """
        Calculate savings compared to Public Cloud APIs ($12.50 / 1M tokens avg)
        """
        savings_usd = (tokens / 1_000_000) * 12.50
        embed = discord.Embed(
            title="💰 Financial Efficiency Report",
            description=f"By processing **{tokens:,}** tokens locally, you saved approximately:",
            color=discord.Color.gold()
        )
        embed.add_field(name="Estimated Savings", value=f"**${savings_usd:,.2f} USD**")
        embed.set_footer(text="Based on avg. cost of GPT-4o / Claude 3.5 Sonnet")
        await ctx.send(embed=embed)
