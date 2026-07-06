import os
import base64
import tempfile
import logging
from fastapi import FastAPI, Response
import requests
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ast-builder")

app = FastAPI(title="Kito AST Builder")

QWEN_VL_ENDPOINT = os.getenv("QWEN_VL_ENDPOINT", "http://litellm.ai-platform.svc:4000/v1")
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-michael-homelab-llm-proxy")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio.monitoring.svc:9000").replace("http://", "").replace("https://", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

def get_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )

@app.get("/healthz")
async def healthz():
    try:
        response = requests.get(f"{QWEN_VL_ENDPOINT}/models", headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}, timeout=3)
        if response.status_code == 200:
            return {"status": "healthy"}
    except Exception as e:
        logger.warning(f"Failed to contact Qwen-VL endpoint: {e}")
    return {"status": "healthy", "warning": "LLM endpoint unreachable"}

@app.post("/build")
async def build_ast(image_path: str):
    logger.info(f"Building AST for image: {image_path}")
    
    minio_client = get_minio_client()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        local_image_path = os.path.join(tmpdir, "page.png")
        
        # Download from kito-processed-documents
        minio_client.fget_object("kito-processed-documents", image_path, local_image_path)
        
        # Base64 encode
        with open(local_image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")
            
        # Call LiteLLM
        headers = {
            "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "builder",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Extract all text and structure from this document page. Output clean markdown content with clear headers, lists, and tables."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 4096
        }
        
        response = requests.post(f"{QWEN_VL_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=120)
        
        if response.status_code != 200:
            logger.error(f"VLM call failed: {response.text}")
            return {"status": "error", "message": "Failed to call VLM"}
            
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        
        # Simple AST format holding the markdown content of the page
        return {
            "status": "success", 
            "ast": {
                "page": image_path,
                "content": content
            }
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
