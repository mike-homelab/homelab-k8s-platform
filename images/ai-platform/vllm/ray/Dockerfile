FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System deps
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-dev \
    python3-pip \
    git \
    build-essential \
    cmake \
    ninja-build \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.12 default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1

# Upgrade pip
RUN python -m pip install --upgrade pip setuptools wheel

# ---- CRITICAL: Install PyTorch NIGHTLY with CUDA 12.8 ----
RUN pip install --pre torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/nightly/cu128

# Verify CUDA (optional but useful during build)
RUN python -c "import torch; print(torch.__version__, torch.version.cuda)"

# ---- Install Ray ----
RUN pip install "ray[default]==2.23.0"

# ---- Build vLLM from source ----
WORKDIR /workspace
RUN git clone https://github.com/vllm-project/vllm.git

WORKDIR /workspace/vllm

# Optional but recommended for new GPUs
ENV TORCH_CUDA_ARCH_LIST="9.0"

RUN pip install -e .

# ---- Runtime env fixes ----
ENV VLLM_USE_FLASH_ATTENTION=0
ENV NCCL_DEBUG=WARN
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Default command
CMD ["python", "-c", "import torch; print(torch.cuda.get_device_name(0))"]
