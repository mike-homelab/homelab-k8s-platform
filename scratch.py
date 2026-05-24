from discord.ext import tasks

@tasks.loop(seconds=60)
async def realtime_monitor(self):
    pass
