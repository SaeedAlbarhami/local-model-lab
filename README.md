# Local Model Lab

A deliberately small, localhost-only Ollama client: one FastAPI backend and one dependency-free HTML page. The browser and external clients use the same API, and every chat request carries its own exact model tag—there is no global “current model.”

## Exact macOS setup and start

Requirements: Apple Silicon macOS, Python 3.10+, and enough free disk for the selected weights.

```bash
cd /Users/sthabit/Documents/mbrhe/local-model-lab
brew install ollama
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

If Homebrew's formula service is unavailable, use Ollama's native macOS installer instead. This is the fallback used during the recorded test:

```bash
curl -fsSL https://ollama.com/download/install.sh | OLLAMA_NO_START=1 sh
ln -sf /Applications/Ollama.app/Contents/Resources/ollama /opt/homebrew/bin/ollama
```

`run.sh` binds Ollama to `127.0.0.1:11434` and FastAPI to `127.0.0.1:8000`. It starts Ollama when needed and applies these conservative defaults:

- `OLLAMA_MAX_LOADED_MODELS=1`
- `OLLAMA_NUM_PARALLEL=1`
- `OLLAMA_CONTEXT_LENGTH=4096`
- `OLLAMA_KEEP_ALIVE=5m`
- `OLLAMA_NO_CLOUD=1`
- `DEFAULT_CONTEXT_TOKENS=4096`

With the launcher still running, use a second terminal to install the baseline. This test host already had `mistral:latest`; on a clean host pull it too, or use the smaller alternative shown below.

```bash
ollama pull llama3:8b
ollama pull mistral:latest
# Smaller switch-test alternative (815 MB):
ollama pull gemma3:1b-it-q4_K_M
```

Open <http://127.0.0.1:8000>. Pulling any exact model tag and pressing **Refresh** makes it selectable without a code change or application restart. API documentation is at <http://127.0.0.1:8000/docs>.

If Ollama is already running (for example, via its menu-bar app), `run.sh` reuses it. To guarantee the one-model, one-request, 4K-context, local-only settings, quit that instance first and let `run.sh` start Ollama.

Once Python packages and weights are downloaded, runtime traffic stays on localhost: the page has no CDN, font, script, or image dependency, and the launcher disables Ollama cloud features.

## API examples

Health:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

Installed models, with exact tags, digests, sizes, and quantization details:

```bash
curl -sS http://127.0.0.1:8000/api/models
```

Llama 3 chat (the shorter request from the specification also works because every option has a default):

```bash
curl -sS http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3:8b","messages":[{"role":"user","content":"Explain recursion with a short Python example."}],"stream":false,"options":{"temperature":0.2,"num_predict":256,"num_ctx":4096}}'
```

Select the other model per request, without changing code or server state:

```bash
curl -sS http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"mistral:latest","messages":[{"role":"user","content":"Explain why the sky looks blue in one sentence."}],"stream":false,"options":{"temperature":0.2,"num_predict":64,"num_ctx":4096}}'
```

A missing exact tag returns HTTP 404 with `error.code=model_not_found`, an actionable pull command, and the available tags:

```bash
curl -sS -i http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"missing:model","messages":[{"role":"user","content":"hello"}]}'
```

Generation is intentionally non-streaming. `stream:true` is rejected. The default generation timeout is 180 seconds and can be changed by starting with `OLLAMA_TIMEOUT_SECONDS=300 ./run.sh`. Successful responses include the actual upstream model, stop reason, token counts, Ollama durations, request duration, and calculated generation tokens/second when the counters are available.

## Automated tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The suite uses a fake Ollama transport for deterministic health, exact-tag, per-request selection, defaults, metrics, missing-model, timeout, and validation checks. Real-inference and browser tests are recorded below.

## Actual host audit

Audited on 2026-09-07 before selecting runtime settings:

| Item | Observed |
| --- | --- |
| Machine | MacBook Pro `Mac15,8`, Apple M3 Max, arm64 |
| Compute | 16 CPU cores (12 performance + 4 efficiency), 40-core integrated GPU |
| Unified memory | **48 GB** (`51,539,607,552` bytes), not the stated 128 GB |
| OS | macOS 26.6.2 (25G83), Darwin 25.6.0 |
| Free disk at audit | 207 GiB |
| Python | Homebrew Python 3.13.7, native arm64 |
| Ollama | Native macOS app/CLI 0.33.3; no Docker |

The machine was already under substantial unrelated memory pressure (about 9 GB swap in use), so timings below are valid observations, not clean hardware benchmarks. Larger-model fit recommendations are estimates for a true 128 GB Mac and are not measurements on this 48 GB host.

## What was actually tested

- Fresh virtual environment install succeeded. Automated contract tests: **9 passed** (one third-party Starlette/AnyIO deprecation warning).
- Before the pull, `/api/models` returned only `mistral:latest`. After `ollama pull llama3:8b`, the still-running backend immediately returned both exact tags, proving dynamic model discovery without restart.
- The specification's exact minimal Llama request returned a complete generated recursion explanation. Warm run: `model=llama3:8b`, `done_reason=stop`, 455 generated tokens, 16.945 s total, **26.93 tok/s**.
- API switching worked `llama3:8b` → `mistral:latest` → `llama3:8b`. The measured Mistral switch took 3.350 s including 2.328 s load (24 output tokens, 28.57 tok/s); switching back took 3.179 s including 2.871 s load (5 output tokens, 42.92 tok/s).
- After each switch, `ollama ps` showed only the requested model, context `4096`, and `100% GPU`, confirming Metal inference and the one-loaded-model setting.
- A request for `definitely-missing:model` returned HTTP 404 with `error.code=model_not_found`, its pull command, and `available_models`.
- An isolated backend pointed at an unused Ollama port reported `status=degraded` from health while keeping `backend.reachable=true`; both model listing and chat returned actionable HTTP 503 `ollama_unavailable` errors.
- A real Chromium UI run populated the exact-tag dropdown, sent configured temperature/output limits, completed a two-turn Llama conversation that recalled the codeword `COBALT-731`, displayed actual model/duration/token rate, switched to Mistral, cleared both visible and submitted history, generated through Mistral, switched back, refreshed models, exercised New Chat, and recovered from a simulated model-list outage. Captured request history lengths were `1, 3, 1`; there were no browser console errors. The observed second-turn Llama response was 0.49 s at 24.09 tok/s; the Mistral UI response was 2.77 s at 37.89 tok/s.
- Both live listeners were verified on IPv4 loopback only. After restart, Ollama logged `Ollama cloud disabled: true`.

Installed model identities used for these measurements:

| Exact requested tag | Registry digest | Blob size | Parameters | Quantization | Native context | Test context |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| `llama3:8b` | `365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5fcb5d8ad1` | 4,661,224,676 B | 8.0B | Q4_0 | 8,192 | 4,096 |
| `mistral:latest` | `61e88e884507ba5e06c49b40e6226884b2a16e872382c2b44a42f2d119d804a5` | 4,109,865,159 B | 7B | Q4_0 | 32,768 | 4,096 |

Tags can be mutable aliases; preserve the digest with every comparison result.

## Current models worth comparing

Registry metadata below was verified on 2026-09-07. Sizes are downloadable artifacts, not peak unified-memory use. Capability descriptions are publisher claims; the local-fit column is an estimate, and none of these larger models was benchmarked during this build.

| Purpose | Exact reproducible tag | Published artifact | Estimated fit |
| --- | --- | --- | --- |
| Current coding/all-round first choice | [`qwen3.8:27b-nvfp4`](https://ollama.com/library/qwen3.8/tags) | 18 GB, MLX/NVFP4, 256K; digest `5642e97495e1` | Comfortable at 4K on 48 GB after closing heavy apps; ample on 128 GB |
| Requested Qwen 3.6 comparator | [`qwen3.6:27b-nvfp4`](https://ollama.com/library/qwen3.6/tags) | 19 GB, MLX/NVFP4, 256K; digest `d49fd9e0da45` | Comfortable at 4K on a clean 48 GB host; ample on 128 GB. The shorter `qwen3.6:27b` is valid but currently aliases an 18 GB Q4 artifact |
| General chat, multimodal, vendor-diverse | [`gemma4:31b-nvfp4`](https://ollama.com/library/gemma4/tags) | 19 GB, MLX/NVFP4, 256K; digest `a22a363052da` | Comfortable at 4K on a clean 48 GB host; ample on 128 GB |
| Deep reasoning/agent work | [`gpt-oss:120b`](https://ollama.com/library/gpt-oss:120b) | 65 GB, native MXFP4, 117B; digest `a951a23b46a1` | **Not for this 48 GB Mac**; likely suitable on 128 GB at 4K with substantial headroom, to be measured |
| Upper-bound reasoning/coding stress test | [`mistral-medium-3.5:128b-q4_K_M`](https://ollama.com/library/mistral-medium-3.5/tags) | 80 GB, Q4_K_M, 256K; digest `0341632adb05` | **Not for 48 GB**; borderline but plausible on 128 GB at 4K after freeing other workloads |

Quantization and memory guidance:

- On current Ollama for Apple Silicon, start with explicit `-nvfp4`/MLX tags when offered, then measure. Ollama's own Gemma 4 experiment reports lower quality loss and about 20% higher output speed than Q4_K_M; that is a vendor benchmark, not a result from this Mac. See [Ollama's MLX performance note](https://ollama.com/blog/mlx-performance).
- For a portable GGUF comparison, start with Q4_K_M. On a 128 GB finalist run, compare higher-quality `qwen3.8:27b-mtp-q8_0` (30 GB) or `gemma4:31b-it-q8_0` (34 GB). Keep 120–128B models at MXFP4/Q4_K_M; the 128B Mistral Q8 artifact alone is 138 GB.
- On a 128 GB system, reserve roughly 20–30 GB for macOS, other applications, Ollama/runtime allocations, and KV cache; target at most roughly 90–100 GB peak until measured. On this busy 48 GB machine, leave at least 10–12 GB free and close other heavy workloads before trying 27–31B models.
- Keep `num_ctx=4096` for the first sweep. Context/KV memory grows with context length and parallelism; Ollama documents that required RAM scales with `OLLAMA_NUM_PARALLEL × OLLAMA_CONTEXT_LENGTH`. Keep the default f16 KV cache at 4K. For later 64K+ tests, enable Flash Attention and consider `OLLAMA_KV_CACHE_TYPE=q8_0`, which Ollama documents as roughly half the f16 KV memory with a small precision tradeoff. See the [Ollama runtime FAQ](https://docs.ollama.com/faq).

For reproducible comparisons, record: Ollama version, exact tag, registry digest, reported quantization, request `num_ctx`, `temperature`, `seed`, `num_predict`, full prompt/history, whether the load was cold or warm, `ollama ps`, and the returned timing/token fields.
