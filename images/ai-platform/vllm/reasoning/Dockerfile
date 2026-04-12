# Build vLLM from source with Blackwell (sm_120) CUDA support.
# Pre-built vllm-openai images only support up to sm_90 (Hopper).
# RTX 5070 Ti / 5060 Ti require CUDA compute capability sm_120.
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TORCH_CUDA_ARCH_LIST="12.0"
ENV VLLM_TORCH_CUDA_ARCH_LIST="12.0"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev git curl build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

# Install PyTorch nightly with CUDA 12.8 (supports sm_120)
RUN pip install --no-cache-dir --pre torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/nightly/cu128

# Install vLLM from source
RUN pip install --no-cache-dir vllm

# Install OpenTelemetry
RUN pip install --no-cache-dir --upgrade \
    opentelemetry-api \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp \
    opentelemetry-semantic-conventions-ai

ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
