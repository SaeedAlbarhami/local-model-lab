from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app import app, get_ollama


class FakeOllama:
    def __init__(self, models=None, chat_response=None, error=None, post_error=None):
        self.models = models or []
        self.chat_response = chat_response or {}
        self.error = error
        self.post_error = post_error
        self.last_chat = None

    async def get(self, path, **_kwargs):
        if self.error:
            raise self.error
        if path == "/api/version":
            return httpx.Response(200, json={"version": "test"}, request=httpx.Request("GET", "http://ollama/api/version"))
        return httpx.Response(200, json={"models": self.models}, request=httpx.Request("GET", "http://ollama/api/tags"))

    async def post(self, path, json, **_kwargs):
        self.last_chat = json
        if self.post_error or self.error:
            raise self.post_error or self.error
        return httpx.Response(200, json=self.chat_response, request=httpx.Request("POST", f"http://ollama{path}"))


def use_fake(fake):
    async def override():
        return fake

    app.dependency_overrides[get_ollama] = override


def clear_fake():
    app.dependency_overrides.clear()


def test_health_reports_backend_and_ollama():
    use_fake(FakeOllama())
    try:
        with TestClient(app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["backend"]["reachable"] is True
        assert response.json()["ollama"]["reachable"] is True
    finally:
        clear_fake()


def test_models_preserve_exact_tags():
    fake = FakeOllama(models=[{"name": "qwen3.6:27b-q4_K_M", "size": 17}, {"name": "llama3:8b", "size": 5}])
    use_fake(fake)
    try:
        with TestClient(app) as client:
            response = client.get("/api/models")
        assert response.status_code == 200
        assert [model["name"] for model in response.json()["models"]] == ["llama3:8b", "qwen3.6:27b-q4_K_M"]
    finally:
        clear_fake()


def test_chat_uses_request_model_and_returns_metrics():
    fake = FakeOllama(
        models=[{"name": "llama3:8b"}, {"name": "tiny:latest"}],
        chat_response={
            "model": "tiny:latest",
            "message": {"role": "assistant", "content": "pong"},
            "done": True,
            "total_duration": 2_000_000_000,
            "eval_count": 20,
            "eval_duration": 1_000_000_000,
            "prompt_eval_count": 7,
        },
    )
    use_fake(fake)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "model": "tiny:latest",
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 64, "num_ctx": 4096},
                },
            )
        assert response.status_code == 200
        assert fake.last_chat["model"] == "tiny:latest"
        assert fake.last_chat["options"]["temperature"] == 0.2
        assert response.json()["message"]["content"] == "pong"
        assert response.json()["metrics"]["generation_tokens_per_second"] == 20.0
    finally:
        clear_fake()


def test_missing_model_is_a_clear_404():
    use_fake(FakeOllama(models=[{"name": "llama3:8b"}]))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"model": "missing:latest", "messages": [{"role": "user", "content": "hello"}]},
            )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "model_not_found"
        assert response.json()["error"]["available_models"] == ["llama3:8b"]
    finally:
        clear_fake()


def test_chat_defaults_include_modest_context():
    fake = FakeOllama(
        models=[{"name": "llama3:8b"}],
        chat_response={
            "model": "llama3:8b",
            "message": {"role": "assistant", "content": "hello"},
            "done": True,
        },
    )
    use_fake(fake)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"model": "llama3:8b", "messages": [{"role": "user", "content": "hello"}]},
            )
        assert response.status_code == 200
        assert fake.last_chat["options"] == {"temperature": 0.7, "num_predict": 512, "num_ctx": 4096}
    finally:
        clear_fake()


def test_generation_timeout_is_a_clear_504():
    request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
    fake = FakeOllama(
        models=[{"name": "llama3:8b"}],
        post_error=httpx.ReadTimeout("timed out", request=request),
    )
    use_fake(fake)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"model": "llama3:8b", "messages": [{"role": "user", "content": "hello"}]},
            )
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "generation_timeout"
    finally:
        clear_fake()


def test_streaming_request_is_rejected():
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "model": "llama3:8b",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    assert response.status_code == 422


def test_health_degrades_when_ollama_is_unavailable():
    request = httpx.Request("GET", "http://127.0.0.1:11434/api/version")
    use_fake(FakeOllama(error=httpx.ConnectError("connection refused", request=request)))
    try:
        with TestClient(app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["ollama"]["reachable"] is False
    finally:
        clear_fake()


def test_index_is_the_offline_single_page_ui():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Local Model Lab" in response.text
    assert "https://" not in response.text
