#!/bin/zsh
set -euo pipefail

readonly PROJECT_DIR="${0:A:h}"
readonly RUNTIME_DIR="$PROJECT_DIR/.runtime"
readonly RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"
readonly UV_BIN="$RUNTIME_BIN_DIR/uv"
readonly VENV_DIR="$PROJECT_DIR/.venv"
readonly VENV_PYTHON="$VENV_DIR/bin/python"
readonly VENV_MARKER="$VENV_DIR/.local-model-lab-runtime"
readonly REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
readonly PYTHON_VERSION="${LOCAL_MODEL_LAB_PYTHON:-3.12}"
readonly UV_VERSION="0.12.10"
readonly UV_INSTALL_URL="https://astral.sh/uv/$UV_VERSION/install.sh"
readonly OLLAMA_DOWNLOAD_URL="https://ollama.com/download/Ollama-darwin.zip"
readonly LOCAL_OLLAMA_APP="$RUNTIME_DIR/Ollama.app"
readonly LOCAL_OLLAMA_BIN="$LOCAL_OLLAMA_APP/Contents/Resources/ollama"

typeset -a REQUIRED_MODELS
if [[ -n "${LOCAL_MODEL_LAB_MODELS:-}" ]]; then
  REQUIRED_MODELS=("${(@s:,:)LOCAL_MODEL_LAB_MODELS}")
else
  # Llama 3.3 was released only as 70B. Llama 3.1 is the matching 8B family.
  REQUIRED_MODELS=("llama3.1:8b" "llama3.3:70b")
fi

integer PULL_MODELS=1
integer SETUP_ONLY=0
integer OLLAMA_STARTED=0
typeset OLLAMA_PID=""
typeset OLLAMA_CLI=""
typeset -a TEMP_DIRS
TEMP_DIRS=()

info() {
  print -r -- "==> $*"
}

warn() {
  print -u2 -r -- "WARNING: $*"
}

die() {
  print -u2 -r -- "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./run.sh [options]

Bootstraps Local Model Lab and starts it on http://127.0.0.1:8000.
The default first run downloads Python, Python packages, Ollama when needed,
llama3.1:8b, and llama3.3:70b.

Options:
  --setup-only   Install runtimes, packages, and models, then exit.
  --skip-models  Skip model downloads for this run.
  -h, --help     Show this help.

Environment:
  LOCAL_MODEL_LAB_MODELS       Comma-separated exact Ollama tags to ensure.
  LOCAL_MODEL_LAB_SKIP_MODELS  Set to 1 to skip model downloads.
  LOCAL_MODEL_LAB_SETUP_ONLY   Set to 1 to exit after setup.
  OLLAMA_MODELS                Optional alternate Ollama model directory.
  PORT                         Web app port (default: 8000).
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --setup-only)
      SETUP_ONLY=1
      ;;
    --skip-models)
      PULL_MODELS=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown option: $1"
      ;;
  esac
  shift
done

[[ "${LOCAL_MODEL_LAB_SKIP_MODELS:-0}" == "1" ]] && PULL_MODELS=0
[[ "${LOCAL_MODEL_LAB_SETUP_ONLY:-0}" == "1" ]] && SETUP_ONLY=1

cleanup() {
  local temp_dir
  for temp_dir in "${TEMP_DIRS[@]}"; do
    if [[ -n "$temp_dir" && -d "$temp_dir" ]]; then
      /bin/rm -rf -- "$temp_dir"
    fi
  done

  if (( OLLAMA_STARTED == 1 )) && [[ -n "$OLLAMA_PID" ]]; then
    kill "$OLLAMA_PID" 2>/dev/null || true
    wait "$OLLAMA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required macOS command '$1' is unavailable."
}

preflight() {
  local os architecture macos_version macos_major translated memory_bytes memory_gib model

  os="$(uname -s)"
  [[ "$os" == "Darwin" ]] || die "This launcher supports macOS only (detected $os)."

  architecture="$(uname -m)"
  if [[ "$architecture" != "arm64" ]]; then
    translated="$(sysctl -in sysctl.proc_translated 2>/dev/null || true)"
    if [[ "$translated" == "1" ]]; then
      die "This shell is running under Rosetta. Re-run natively: /usr/bin/arch -arm64 /bin/zsh ./run.sh"
    fi
    die "Apple Silicon (M1 or newer) is required for Metal GPU acceleration (detected $architecture)."
  fi

  macos_version="$(sw_vers -productVersion)"
  macos_major="${macos_version%%.*}"
  (( macos_major >= 14 )) || die "Ollama requires macOS 14 Sonoma or newer (detected $macos_version)."

  require_command curl
  require_command unzip
  [[ -r "$REQUIREMENTS_FILE" ]] || die "Missing requirements file: $REQUIREMENTS_FILE"
  [[ -w "$PROJECT_DIR" ]] || die "The project directory is not writable: $PROJECT_DIR"

  if (( PULL_MODELS == 1 )); then
    for model in "${REQUIRED_MODELS[@]}"; do
      if [[ "$model" == "llama3.3:70b" ]]; then
        memory_bytes="$(sysctl -n hw.memsize 2>/dev/null || print 0)"
        if (( memory_bytes > 0 )); then
          memory_gib=$(( memory_bytes / 1024 / 1024 / 1024 ))
          if (( memory_gib < 64 )); then
            warn "This Mac has about ${memory_gib} GiB unified memory. llama3.3:70b downloads successfully but normally needs at least 64 GiB to run well; use llama3.1:8b on this Mac."
          fi
        fi
        break
      fi
    done
  fi
}

new_temp_dir() {
  REPLY="$(mktemp -d "${TMPDIR:-/tmp}/local-model-lab.XXXXXX")" || die "Could not create a temporary directory."
  TEMP_DIRS+=("$REPLY")
}

ensure_uv() {
  local temp_dir installer

  if [[ -x "$UV_BIN" ]] && "$UV_BIN" --version >/dev/null 2>&1; then
    return
  fi

  [[ ! -L "$RUNTIME_DIR" ]] || die "Refusing to use symlinked runtime directory: $RUNTIME_DIR"
  info "Installing the portable Python package manager (uv) inside the project..."
  new_temp_dir
  temp_dir="$REPLY"
  installer="$temp_dir/uv-install.sh"
  curl --fail --location --show-error --silent --retry 3 \
    --output "$installer" "$UV_INSTALL_URL" \
    || die "Could not download uv. Check the internet connection and retry."
  /usr/bin/env UV_UNMANAGED_INSTALL="$temp_dir/bin" /bin/sh "$installer" \
    || die "uv installation failed."
  [[ -x "$temp_dir/bin/uv" ]] || die "uv installer completed without creating its executable."

  mkdir -p "$RUNTIME_BIN_DIR"
  /bin/mv -f "$temp_dir/bin/uv" "$UV_BIN"
  if [[ -x "$temp_dir/bin/uvx" ]]; then
    /bin/mv -f "$temp_dir/bin/uvx" "$RUNTIME_BIN_DIR/uvx"
  fi
}

ensure_python_environment() {
  local expected_marker current_marker=""

  ensure_uv
  export UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNTIME_DIR/uv-cache}"
  export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$RUNTIME_DIR/python}"
  export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-only-managed}"

  expected_marker="project=$PROJECT_DIR
os=Darwin
arch=arm64
python=$PYTHON_VERSION
python_install_dir=$UV_PYTHON_INSTALL_DIR"
  if [[ -f "$VENV_MARKER" ]]; then
    current_marker="$(<"$VENV_MARKER")"
  fi

  if [[ ! -x "$VENV_PYTHON" || "$current_marker" != "$expected_marker" ]] \
    || ! "$VENV_PYTHON" -c 'import platform; raise SystemExit(platform.machine() != "arm64")' >/dev/null 2>&1; then
    [[ ! -L "$VENV_DIR" ]] || die "Refusing to replace symlinked virtual environment: $VENV_DIR"
    info "Creating a portable Python $PYTHON_VERSION environment..."
    "$UV_BIN" venv --clear --python "$PYTHON_VERSION" "$VENV_DIR" \
      || die "Could not create the Python environment. Check the network connection and retry."
    print -r -- "$expected_marker" >"$VENV_MARKER"
  else
    info "Python environment is ready."
  fi

  info "Installing/updating required Python packages..."
  "$UV_BIN" pip install --python "$VENV_PYTHON" --requirement "$REQUIREMENTS_FILE" \
    || die "Python package installation failed. Check the network connection and retry."
}

find_ollama() {
  local candidate

  if [[ -n "${OLLAMA_BIN:-}" ]]; then
    [[ -x "$OLLAMA_BIN" ]] || die "OLLAMA_BIN is not executable: $OLLAMA_BIN"
    OLLAMA_CLI="$OLLAMA_BIN"
    return
  fi

  for candidate in \
    "$(command -v ollama 2>/dev/null || true)" \
    "/Applications/Ollama.app/Contents/Resources/ollama" \
    "$HOME/Applications/Ollama.app/Contents/Resources/ollama" \
    "$LOCAL_OLLAMA_BIN"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" --version >/dev/null 2>&1; then
      OLLAMA_CLI="$candidate"
      return
    fi
  done
}

install_local_ollama() {
  local temp_dir archive extracted_app

  [[ ! -L "$RUNTIME_DIR" ]] || die "Refusing to use symlinked runtime directory: $RUNTIME_DIR"
  info "Ollama is not installed; downloading the official macOS app inside the project..."
  new_temp_dir
  temp_dir="$REPLY"
  archive="$temp_dir/Ollama-darwin.zip"
  curl --fail --location --show-error --retry 3 \
    --output "$archive" "$OLLAMA_DOWNLOAD_URL" \
    || die "Could not download Ollama. Check the internet connection and retry."
  mkdir -p "$temp_dir/extracted"
  unzip -q "$archive" -d "$temp_dir/extracted" || die "The Ollama download could not be extracted."
  extracted_app="$temp_dir/extracted/Ollama.app"
  [[ -x "$extracted_app/Contents/Resources/ollama" ]] || die "The Ollama archive did not contain the expected CLI."
  /usr/bin/codesign --verify --deep --strict "$extracted_app" >/dev/null 2>&1 \
    || die "The downloaded Ollama app did not pass macOS code-signature verification."

  mkdir -p "$RUNTIME_DIR"
  if [[ -e "$LOCAL_OLLAMA_APP" || -L "$LOCAL_OLLAMA_APP" ]]; then
    /bin/rm -rf -- "$LOCAL_OLLAMA_APP"
  fi
  /bin/mv "$extracted_app" "$LOCAL_OLLAMA_APP"
  OLLAMA_CLI="$LOCAL_OLLAMA_BIN"
}

ollama_is_ready() {
  curl --fail --silent --show-error --max-time 2 "$OLLAMA_BASE_URL/api/version" >/dev/null 2>&1
}

ensure_ollama_server() {
  local attempt

  find_ollama
  if [[ -z "$OLLAMA_CLI" ]]; then
    install_local_ollama
  else
    info "Using Ollama: $OLLAMA_CLI"
  fi

  if ollama_is_ready; then
    info "Reusing the Ollama server at $OLLAMA_BASE_URL."
    if [[ -n "${OLLAMA_MODELS:-}" ]]; then
      warn "OLLAMA_MODELS cannot change storage for an already-running Ollama server. Quit that server and rerun to apply: $OLLAMA_MODELS"
    fi
    return
  fi

  info "Starting Ollama on $OLLAMA_HOST..."
  "$OLLAMA_CLI" serve >>"$PROJECT_DIR/.ollama.log" 2>&1 &
  OLLAMA_PID=$!
  OLLAMA_STARTED=1

  for attempt in {1..120}; do
    if ollama_is_ready; then
      info "Ollama is ready."
      return
    fi
    kill -0 "$OLLAMA_PID" 2>/dev/null || break
    sleep 0.5
  done

  if [[ -f "$PROJECT_DIR/.ollama.log" ]]; then
    print -u2 -r -- "Last Ollama log lines:"
    tail -n 20 "$PROJECT_DIR/.ollama.log" >&2 || true
  fi
  die "Ollama did not start within 60 seconds. See $PROJECT_DIR/.ollama.log"
}

warn_about_disk() {
  local model="$1" free_kib free_gib minimum_gib storage_probe

  case "$model" in
    llama3.3:70b)
      minimum_gib=48
      ;;
    llama3.1:8b)
      minimum_gib=7
      ;;
    *)
      return
      ;;
  esac

  storage_probe="${OLLAMA_MODELS:-$HOME}"
  while [[ ! -e "$storage_probe" && "$storage_probe" != "/" ]]; do
    storage_probe="${storage_probe:h}"
  done
  free_kib="$(df -Pk "$storage_probe" 2>/dev/null | awk 'NR == 2 {print $4}')"
  if [[ "$free_kib" == <-> ]]; then
    free_gib=$(( free_kib / 1024 / 1024 ))
    if (( free_gib < minimum_gib )); then
      warn "Only about ${free_gib} GiB is free where Ollama stores models; $model may not fit. The pull remains resumable after space is freed."
    fi
  fi
}

ensure_models() {
  local model

  (( PULL_MODELS == 1 )) || {
    info "Skipping model downloads."
    return
  }

  for model in "${REQUIRED_MODELS[@]}"; do
    [[ -n "$model" && "$model" != *[[:space:]]* ]] \
      || die "Invalid model tag in LOCAL_MODEL_LAB_MODELS: '$model'"
    if "$OLLAMA_CLI" show "$model" >/dev/null 2>&1; then
      info "Model already installed: $model"
    else
      warn_about_disk "$model"
      info "Downloading model $model (an interrupted download can be resumed by rerunning this script)..."
      "$OLLAMA_CLI" pull "$model" || die "Failed to download $model. Free disk space/check the network, then rerun."
    fi
  done
}

cd "$PROJECT_DIR" || die "Could not enter the project directory: $PROJECT_DIR"
preflight

# This project is intentionally localhost-only. Keeping one canonical endpoint
# also guarantees that CLI model checks and FastAPI talk to the same daemon.
export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export OLLAMA_NO_CLOUD="${OLLAMA_NO_CLOUD:-1}"
export DEFAULT_CONTEXT_TOKENS="${DEFAULT_CONTEXT_TOKENS:-4096}"

ensure_python_environment
ensure_ollama_server
ensure_models

if (( SETUP_ONLY == 1 )); then
  info "Setup complete. Run ./run.sh to start Local Model Lab."
  exit 0
fi

info "Local Model Lab is ready at http://127.0.0.1:${PORT:-8000}"
info "Press Ctrl-C to stop it."
"$VENV_PYTHON" -m uvicorn app:app --app-dir "$PROJECT_DIR" --host 127.0.0.1 --port "${PORT:-8000}"
