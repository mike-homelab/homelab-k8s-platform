import os
import asyncio
from fastapi import FastAPI
from .discord_bot import RaphaelBot

app = FastAPI(title="Raphael Observability Agent")

# Environment Variables
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Initialize Discord Bot
bot = RaphaelBot(command_prefix="!")

@app.on_event("startup")
async def startup_event():
    # Run Discord Bot in the background
    asyncio.create_task(bot.start(DISCORD_TOKEN))

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "discord_connected": bot.is_ready(),
        "qdrant_connected": True, # TODO: Add check
        "redis_connected": True   # TODO: Add check
    }

@app.post("/alert")
async def receive_alert(alert: dict):
    """
    Endpoint to receive webhooks from Grafana
    """
    # TODO: Implement alert handling logic
    await bot.handle_alert(alert)
    return {"status": "received"}
