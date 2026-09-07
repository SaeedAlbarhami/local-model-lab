from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
DEFAULT_CONTEXT_TOKENS = int(os.getenv("DEFAULT_CONTEXT_TOKENS", "4096"))
STATIC_DIR = Path(__file__).parent / "static"


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra: object) -> None:
        self.status_code = status_code
        self.payload = {"code": code, "message": message, **extra}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.7, ge=0, le=2)
    num_predict: int = Field(default=512, ge=1, le=8192)
    num_ctx: int = Field(default=DEFAULT_CONTEXT_TOKENS, ge=512, le=262144)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    stream: Literal[False] = False
    options: GenerationOptions = Field(default_factory=GenerationOptions)


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=3.0)
    async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=timeout, trust_env=False) as client:
        app.state.ollama = client
        yield


app = FastAPI(
    title="Local Model Lab",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(ApiError)
async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.payload})


async def get_ollama(request: Request) -> httpx.AsyncClient:
    return request.app.state.ollama


OllamaClient = Annotated[httpx.AsyncClient, Depends(get_ollama)]


def ollama_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return payload["error"]
    except ValueError:
        pass
    return response.text.strip() or f"Ollama returned HTTP {response.status_code}."


async def installed_models(client: httpx.AsyncClient) -> list[dict[str, object]]:
    try:
        response = await client.get("/api/tags", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise ApiError(504, "ollama_timeout", "Ollama timed out while listing models.") from exc
    except httpx.RequestError as exc:
        raise ApiError(
            503,
            "ollama_unavailable",
            f"Cannot reach Ollama at {OLLAMA_BASE_URL}. Start it with 'ollama serve'.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ApiError(
            502,
            "ollama_error",
            f"Ollama could not list models: {ollama_error_message(exc.response)}",
        ) from exc
    except ValueError as exc:
        raise ApiError(502, "invalid_ollama_response", "Ollama returned invalid JSON.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ApiError(502, "invalid_ollama_response", "Ollama returned an unexpected model list.")
    raw_models = payload["models"]
    models: list[dict[str, object]] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        exact_tag = raw.get("name") or raw.get("model")
        if not isinstance(exact_tag, str):
            continue
        models.append(
            {
                "name": exact_tag,
                "size": raw.get("size"),
                "digest": raw.get("digest"),
                "modified_at": raw.get("modified_at"),
                "details": raw.get("details") if isinstance(raw.get("details"), dict) else {},
            }
        )
    models.sort(key=lambda model: str(model["name"]).casefold())
    return models


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health(client: OllamaClient) -> dict[str, object]:
    try:
        response = await client.get("/api/version", timeout=3.0)
        response.raise_for_status()
        payload = response.json()
        version = payload.get("version") if isinstance(payload, dict) else None
        return {
            "status": "ok",
            "backend": {"reachable": True, "version": app.version},
            "ollama": {"reachable": True, "version": version, "url": OLLAMA_BASE_URL},
        }
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        return {
            "status": "degraded",
            "backend": {"reachable": True, "version": app.version},
            "ollama": {
                "reachable": False,
                "version": None,
                "url": OLLAMA_BASE_URL,
                "error": str(exc),
            },
        }


@app.get("/api/models")
async def models(client: OllamaClient) -> dict[str, object]:
    local_models = await installed_models(client)
    return {"models": local_models, "count": len(local_models)}


def duration_ms(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return round(value / 1_000_000, 2)
    return None


@app.post("/api/chat")
async def chat(body: ChatRequest, client: OllamaClient) -> dict[str, object]:
    local_models = await installed_models(client)
    available = [str(model["name"]) for model in local_models]
    if body.model not in available:
        raise ApiError(
            404,
            "model_not_found",
            f"Model '{body.model}' is not installed. Run 'ollama pull {body.model}' and refresh models.",
            model=body.model,
            available_models=available,
        )

    options = body.options.model_dump(exclude_none=True)
    payload = {
        "model": body.model,
        "messages": [message.model_dump() for message in body.messages],
        "stream": False,
        "options": options,
    }
    started = time.perf_counter()
    try:
        response = await client.post("/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_SECONDS)
        response.raise_for_status()
        result = response.json()
    except httpx.TimeoutException as exc:
        raise ApiError(
            504,
            "generation_timeout",
            f"Generation exceeded {OLLAMA_TIMEOUT_SECONDS:g} seconds. Try fewer output tokens or raise OLLAMA_TIMEOUT_SECONDS.",
        ) from exc
    except httpx.RequestError as exc:
        raise ApiError(
            503,
            "ollama_unavailable",
            f"Lost connection to Ollama at {OLLAMA_BASE_URL}. Confirm 'ollama serve' is running.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        status = upstream_status if upstream_status in {400, 404, 429} else 502
        code = "model_not_found" if status == 404 else "ollama_error"
        raise ApiError(status, code, ollama_error_message(exc.response), model=body.model) from exc
    except ValueError as exc:
        raise ApiError(502, "invalid_ollama_response", "Ollama returned invalid JSON.") from exc

    if not isinstance(result, dict):
        raise ApiError(502, "invalid_ollama_response", "Ollama returned an unexpected response.")
    message = result.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ApiError(502, "invalid_ollama_response", "Ollama response did not include an assistant message.")

    eval_count = result.get("eval_count")
    eval_duration = result.get("eval_duration")
    tokens_per_second = None
    if isinstance(eval_count, int) and isinstance(eval_duration, (int, float)) and eval_duration > 0:
        tokens_per_second = round(eval_count / (eval_duration / 1_000_000_000), 2)

    metrics = {
        "request_duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "total_duration_ms": duration_ms(result, "total_duration"),
        "load_duration_ms": duration_ms(result, "load_duration"),
        "prompt_eval_duration_ms": duration_ms(result, "prompt_eval_duration"),
        "generation_duration_ms": duration_ms(result, "eval_duration"),
        "prompt_tokens": result.get("prompt_eval_count"),
        "generation_tokens": eval_count,
        "generation_tokens_per_second": tokens_per_second,
    }
    return {
        "model": result.get("model") or body.model,
        "message": {"role": message.get("role", "assistant"), "content": message["content"]},
        "done": result.get("done", True),
        "done_reason": result.get("done_reason"),
        "metrics": metrics,
    }
