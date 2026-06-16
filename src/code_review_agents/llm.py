"""Factory for the local Ollama chat model used by every node.

Inference runs entirely against a local Ollama daemon — no API key and no external
network egress. The model and endpoint are configurable via environment variables so
the same code runs on a laptop or a beefier workstation.
"""

from __future__ import annotations

import os

from langchain_ollama import ChatOllama

DEFAULT_MODEL = "qwen2.5-coder:3b"
DEFAULT_BASE_URL = "http://localhost:11434"


def get_model_name(model: str | None = None) -> str:
    """Resolve the model name: explicit arg > OLLAMA_MODEL env > default."""
    return model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL


def get_base_url() -> str:
    """Resolve the Ollama endpoint: OLLAMA_BASE_URL env > default."""
    return os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL


def make_llm(model: str | None = None, **kwargs) -> ChatOllama:
    """Build a ChatOllama client.

    temperature defaults to 0 for deterministic, review-friendly output.
    """
    kwargs.setdefault("temperature", 0)
    return ChatOllama(
        model=get_model_name(model),
        base_url=get_base_url(),
        **kwargs,
    )
