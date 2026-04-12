# Build vLLM from source with Blackwell (sm_120) CUDA support.
# Pre-built vllm-openai images only support up to sm_90 (Hopper).
# RTX 5060 Ti requires CUDA compute capability sm_120.
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TORCH_CUDA_ARCH_LIST="12.0"
ENV MAX_JOBS=4

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev git curl build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

# Install a pinned PyTorch nightly build that supports CUDA 12.8 / sm_120.
# Pin the exact nightly date to avoid pip resolver loops on subsequent runs.
RUN pip install --no-cache-dir \
    torch==2.7.0.dev20250312+cu128 \
    torchvision==0.22.0.dev20250312+cu128 \
    torchaudio==2.7.0.dev20250312+cu128 \
    --index-url https://download.pytorch.org/whl/nightly/cu128

# Clone vLLM at a fixed tag and build against the torch already installed above.
# use_existing_torch.py removes torch from pyproject.toml so pip won't re-resolve it.
ARG VLLM_VERSION=v0.8.5
RUN git clone --depth 1 --branch ${VLLM_VERSION} https://github.com/vllm-project/vllm.git /vllm
WORKDIR /vllm
RUN python use_existing_torch.py \
    && pip install --no-cache-dir -r requirements/build.txt \
    && pip install --no-cache-dir --no-build-isolation .

# Install OpenTelemetry (pinned; installed after vLLM to avoid transitive conflicts)
RUN pip install --no-cache-dir \
    opentelemetry-api==1.41.0 \
    opentelemetry-sdk==1.41.0 \
    opentelemetry-exporter-otlp==1.41.0 \
    opentelemetry-semantic-conventions-ai==0.5.1

WORKDIR /

ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
