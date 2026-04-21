FROM nvidia/cuda:12.8.0-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# System deps
RUN apt-get update && apt-get install -y \
    python3 \
    python3-dev \
    python3-pip \
    git \
    build-essential \
    cmake \
    ninja-build \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Symlink python to python3 (standard for Ubuntu 24.04)
RUN ln -s /usr/bin/python3 /usr/bin/python

# Upgrade setuptools and wheel (but NOT pip itself to avoid Debian conflict)
RUN python -m pip install --upgrade setuptools wheel

# ---- CRITICAL: Install PyTorch NIGHTLY with CUDA 12.8 ----
RUN pip install --pre torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/nightly/cu128

# Verify CUDA
RUN python -c "import torch; print(f'Torch: {torch.__version__}, CUDA: {torch.version.cuda}')"

# ---- Install Ray ----
RUN pip install "ray[default]==2.23.0"

# ---- Build vLLM from source ----
WORKDIR /workspace
RUN git clone https://github.com/vllm-project/vllm.git

WORKDIR /workspace/vllm

# Required for 50-series (Blackwell) support in vLLM kernels
ENV TORCH_CUDA_ARCH_LIST="9.0"

# Install vLLM from source
RUN pip install -e .

# ---- Runtime env fixes ----
ENV VLLM_USE_FLASH_ATTENTION=0
ENV NCCL_DEBUG=WARN
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Default command
CMD ["python", "-c", "import torch; print(torch.cuda.get_device_name(0)) if torch.cuda.is_available() else print('No GPU detected during startup')"]
