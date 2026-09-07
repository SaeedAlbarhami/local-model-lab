#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
OLLAMA_STARTED=0

export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export OLLAMA_NO_CLOUD="1"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export DEFAULT_CONTEXT_TOKENS="${DEFAULT_CONTEXT_TOKENS:-4096}"

cleanup() {
  if [[ "$OLLAMA_STARTED" -eq 1 ]]; then
    kill "$OLLAMA_PID" 2>/dev/null || true
    wait "$OLLAMA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! curl -fsS --max-time 2 "$OLLAMA_BASE_URL/api/version" >/dev/null 2>&1; then
  ollama serve >>"$PROJECT_DIR/.ollama.log" 2>&1 &
  OLLAMA_PID=$!
  OLLAMA_STARTED=1
  for _ in {1..30}; do
    if curl -fsS --max-time 2 "$OLLAMA_BASE_URL/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
fi

if ! curl -fsS --max-time 3 "$OLLAMA_BASE_URL/api/version" >/dev/null 2>&1; then
  print -u2 "Ollama did not start. Check $PROJECT_DIR/.ollama.log"
  exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  print -u2 "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

"$PROJECT_DIR/.venv/bin/python" -m uvicorn app:app --app-dir "$PROJECT_DIR" --host 127.0.0.1 --port "${PORT:-8000}"
