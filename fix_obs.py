import re

with open("images/ai-agent/raphael/backend/observability.py", "r") as f:
    content = f.read()

content = content.replace("from discord.ext import tasks", "")

# We need to replace @tasks.loop(minutes=2) with standard async loops.
# Let's just redefine the class methods for start and the monitors.
start_str = """
    def start(self):
        self.monitor_logs.start()
        self.monitor_metrics.start()
        self.monitor_traces.start()
"""
new_start_str = """
    def start(self):
        asyncio.create_task(self.monitor_logs())
        asyncio.create_task(self.monitor_metrics())
        asyncio.create_task(self.monitor_traces())
"""
content = content.replace(start_str, new_start_str)

def replace_monitor(name, monitor_type, query):
    old = f"""    @tasks.loop(minutes=2)
    async def {name}(self):
        print("Running real-time {monitor_type} monitor...")
        query = '{query}'
        params = {{"query": query, "limit": 2, "direction": "backward"}}
        await self._run_monitor(self.loki_url, params, "{monitor_type}")"""
    
    new = f"""    async def {name}(self):
        while True:
            try:
                print("Running real-time {monitor_type} monitor...")
                query = '{query}'
                params = {{"query": query, "limit": 2, "direction": "backward"}}
                await self._run_monitor(self.loki_url, params, "{monitor_type}")
            except Exception as e:
                print(f"{name} loop error: {{e}}")
            await asyncio.sleep(120)"""
    return old, new

o1, n1 = replace_monitor("monitor_logs", "log", '{namespace=~"ai-agent|monitoring|default"} |= "error" |~ "(?i)(exception|failed|fatal|error)"')
content = content.replace(o1, n1)

o2, n2 = replace_monitor("monitor_metrics", "metric", '{namespace=~"ai-agent|monitoring"} |= "HTTP 5" |~ "(?i)error"')
content = content.replace(o2, n2)

o3, n3 = replace_monitor("monitor_traces", "trace", '{namespace=~"ai-agent|monitoring"} |= "trace" |~ "(?i)error"')
content = content.replace(o3, n3)

with open("images/ai-agent/raphael/backend/observability.py", "w") as f:
    f.write(content)
