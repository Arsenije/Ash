"""Local-first guards: no silent fallback to hosted OpenAI (privacy)."""

from __future__ import annotations

import pytest

import khora_client as kc
import vision


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "PHOTO_ALLOW_CLOUD",
                "KHORA_LLM_MODEL", "KHORA_EXTRACTION_MODEL", "KHORA_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(vision, "_client", None)
    yield
    vision._client = None


# --- vision (photo upload path) ------------------------------------------------

def test_vision_refuses_cloud_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")  # ambient key must NOT be enough
    with pytest.raises(RuntimeError, match="refusing to send photos"):
        vision._get_client()


def test_vision_allows_local_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local")
    assert vision._get_client() is not None


def test_vision_allows_explicit_cloud_optin(monkeypatch):
    monkeypatch.setenv("PHOTO_ALLOW_CLOUD", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")
    assert vision._get_client() is not None


# --- khora (description/extraction path) ----------------------------------------

def test_khora_refuses_hosted_defaults():
    with pytest.raises(RuntimeError, match="refusing to fall back to hosted"):
        kc.ensure_local_models()


def test_khora_refuses_openai_models_without_base(monkeypatch):
    monkeypatch.setenv("KHORA_LLM_MODEL", "openai/text")
    monkeypatch.setenv("KHORA_EXTRACTION_MODEL", "openai/text")
    monkeypatch.setenv("KHORA_EMBED_MODEL", "openai/embed")
    with pytest.raises(RuntimeError):
        kc.ensure_local_models()


def test_khora_allows_local_base(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9999/v1")
    kc.ensure_local_models()  # the app's engineEnv shape — must not raise


def test_khora_allows_non_openai_providers(monkeypatch):
    monkeypatch.setenv("KHORA_LLM_MODEL", "ollama/llama3")
    monkeypatch.setenv("KHORA_EXTRACTION_MODEL", "ollama/llama3")
    monkeypatch.setenv("KHORA_EMBED_MODEL", "ollama/nomic-embed-text")
    kc.ensure_local_models()  # explicitly routed elsewhere — must not raise


def test_khora_allows_explicit_cloud_optin(monkeypatch):
    monkeypatch.setenv("PHOTO_ALLOW_CLOUD", "1")
    kc.ensure_local_models()
