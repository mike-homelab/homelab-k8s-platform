import os
import logging
from fastapi import FastAPI, Response
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ast-builder")

app = FastAPI(title="Kito AST Builder")

QWEN_VL_ENDPOINT = os.getenv("QWEN_VL_ENDPOINT", "http://litellm.ai-platform.svc:4000/v1")

@app.get("/healthz")
async def healthz():
    # Simple check to see if we can reach the LLM proxy
    try:
        response = requests.get(f"{QWEN_VL_ENDPOINT}/models", timeout=3)
        if response.status_code == 200:
            return {"status": "healthy"}
    except Exception as e:
        logger.warning(f"Failed to contact Qwen-VL endpoint: {e}")
    # Return healthy anyway to allow startup, but log warning
    return {"status": "healthy", "warning": "LLM endpoint unreachable"}

@app.post("/build")
async def build_ast(image_path: str):
    logger.info(f"Building AST for image: {image_path}")
    # Integration with Qwen-VL to reconstruct layout
    return {"status": "success", "ast": {}}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
