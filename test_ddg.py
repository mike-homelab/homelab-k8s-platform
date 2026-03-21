import asyncio
from duckduckgo_search import AsyncDDGS

async def test():
    async with AsyncDDGS() as ddgs:
        results = [r async for r in ddgs.text("kubernetes", max_results=2)]
        print(results)
asyncio.run(test())
