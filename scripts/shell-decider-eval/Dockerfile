FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ENV CMAKE_ARGS="-DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_NATIVE=OFF" \
    CMAKE_BUILD_PARALLEL_LEVEL=4
RUN uv pip install --system torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu \
    && uv pip install --system 'decider-ai[gguf]==1.8.1' 'llama-cpp-python==0.3.35' \
       transformers==5.4.0 pydantic==2.12.5 tree-sitter==0.25.2 tree-sitter-bash==0.25.1
RUN uv pip install --system websockets==16.0
RUN uv pip install --system --no-deps \
    'jevk5 @ git+https://github.com/allebee/jevk5@f944fe37ff1d5ed3830aa4c8d88b7189c8c1268a'
