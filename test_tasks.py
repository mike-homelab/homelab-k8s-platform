import asyncio
from discord.ext import tasks

class TestEngine:
    def start(self):
        self.test_loop.start()

    @tasks.loop(seconds=1)
    async def test_loop(self):
        print("Test loop running")

async def main():
    engine = TestEngine()
    engine.start()
    await asyncio.sleep(3)

asyncio.run(main())
