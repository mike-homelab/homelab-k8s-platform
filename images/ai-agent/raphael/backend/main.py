import os
import asyncio
import discord
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
    if DISCORD_TOKEN:
        try:
            asyncio.create_task(bot.start(DISCORD_TOKEN))
        except discord.errors.LoginFailure:
            print("FAILED to log in to Discord Bot. Commands will be unavailable, but Webhook alerts will still function.")
        except Exception as e:
            print(f"Unexpected error starting Discord bot: {e}")
    else:
        print("DISCORD_TOKEN not set. Running in Webhook-only mode.")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "discord_connected": bot.is_ready(),
        "webhook_active": bool(os.getenv("DISCORD_WEBHOOK_URL")),
        "modules": ["alerts", "financials"]
    }

@app.post("/alert")
async def receive_alert(request: Request):
    """
    Endpoint to receive webhooks from Grafana.
    Delegates to the bot which now uses a Webhook for delivery.
    """
    try:
        alert_data = await request.json()
        print(f"Received alert: {alert_data.get('status', 'unknown')}")
        await bot.handle_alert(alert_data)
        return {"status": "received"}
    except Exception as e:
        print(f"Error processing alert: {e}")
        return {"status": "error", "message": str(e)}, 500
