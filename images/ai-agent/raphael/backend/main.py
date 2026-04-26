import os
import asyncio
from fastapi import FastAPI, Request
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
        "modules": ["alerts", "financials"]
    }

@app.post("/alert")
async def receive_alert(request: Request):
    """
    Endpoint to receive webhooks from Grafana
    """
    alert_data = await request.json()
    print(f"Received alert: {alert_data.get('status', 'unknown')}")
    
    # Delegate alert handling to the bot
    await bot.handle_alert(alert_data)
    
    return {"status": "received"}
