#!/usr/bin/env bash
# Launch llama.cpp server (CPU-only) with Qwen2.5-Coder-7B for this project.
# Replaces Ollama as the local LLM backend via its OpenAI-compatible API.
#
# NOTE: manual startup is now OPTIONAL — the app auto-starts llama-server on
# the first LLM request and stops it after ~5 min idle (agent/llama_server.py,
# see LLAMACPP_* vars in .env). Use this script only if you want it always-on;
# servers started here are detected as already running and left alone.
#
# Usage:   ./scripts/start_llamacpp.sh [port]
# Default port: 8080  ->  project expects http://localhost:8080/v1

set -euo pipefail

PORT="${1:-8080}"
LLAMA_DIR="$HOME/llama.cpp"
MODEL="$LLAMA_DIR/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf"

if [[ ! -x "$LLAMA_DIR/build/bin/llama-server" ]]; then
    echo "ERROR: llama-server not built. Run:"
    echo "  cmake -B ~/llama.cpp/build -S ~/llama.cpp -DGGML_NATIVE=ON -DLLAMA_CURL=OFF"
    echo "  cmake --build ~/llama.cpp/build -j8 --target llama-server"
    exit 1
fi

if [[ ! -f "$MODEL" ]]; then
    echo "ERROR: Model not found at $MODEL"
    echo "Download it with:"
    echo "  curl -L -C - -o '$MODEL' 'https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf'"
    exit 1
fi

echo "Starting llama-server on 127.0.0.1:$PORT (CPU-only, 8 threads, ctx 8192)"
exec "$LLAMA_DIR/build/bin/llama-server" \
    -m "$MODEL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    -t 8 \
    -c 8192 \
    --temp 0.1 \
    --top-p 0.95 \
    --mlock \
    --alias qwen2.5-coder-7b \
    --api-key sk-local
