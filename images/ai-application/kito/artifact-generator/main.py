import os
import logging
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artifact-generator")

app = FastAPI(title="Kito Artifact Generator")

@app.get("/healthz")
async def healthz():
    return {"status": "healthy"}

@app.post("/generate")
async def generate_artifact(ast_data: dict, format: str):
    logger.info(f"Generating artifact in format: {format}")
    # Compile AST to document formats
    return {"status": "success", "artifact_url": ""}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
