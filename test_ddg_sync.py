from duckduckgo_search import DDGS

with DDGS() as ddgs:
    results = ddgs.text("kubernetes", max_results=2)
    print(results)
