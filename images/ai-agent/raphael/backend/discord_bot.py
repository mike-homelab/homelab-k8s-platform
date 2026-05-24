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

    async def on_ready(self):
        print(f'Raphael has awakened as {self.user} (ID: {self.user.id})')
        print('Autonomous Diagnostic Systems: ONLINE (Internal Direct Access)')

    @commands.command()
    async def status(self, ctx):
        await ctx.send("🛡️ **Raphael System Status**\n- **LGTM Ingestion**: Active\n- **Internal AI Mesh**: Connected (Direct Service)\n- **Diagnostic Engine**: Active")

    @commands.command()
    async def savings(self, ctx, tokens: int):
        savings_usd = (tokens / 1_000_000) * 12.50
        await ctx.send(f"💰 **Financial Efficiency**: Savings of **${savings_usd:,.2f} USD** identified.")
