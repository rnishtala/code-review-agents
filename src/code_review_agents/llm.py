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
DEFAULT_PROJECT = "code-review-agents"

# Honor the "no data leaves your machine" promise BY DEFAULT. LangChain/LangSmith tracing
# uploads prompts, the diff, and model outputs to LangSmith (an external service), so it is
# forced OFF unless the user explicitly opts in with CODE_REVIEW_ENABLE_TRACING=1 — even if
# their shell already exports LANGCHAIN_TRACING_V2 / a LangSmith key. When opted in, tracing
# uses the standard LangSmith env vars (LANGSMITH_API_KEY or LANGCHAIN_API_KEY; project name
# from LANGCHAIN_PROJECT, default "code-review-agents").
_TRACING_FLAGS = ("1", "true", "yes")


def tracing_enabled() -> bool:
    """Whether the user opted into LangSmith tracing (CODE_REVIEW_ENABLE_TRACING)."""
    return os.environ.get("CODE_REVIEW_ENABLE_TRACING", "").lower() in _TRACING_FLAGS


def has_langsmith_key() -> bool:
    """Whether a LangSmith API key is present (either env-var spelling)."""
    return bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))


def configure_tracing() -> bool:
    """Apply the tracing policy and return whether tracing is enabled.

    Off by default (forced, for privacy); when opted in, actively turns LangSmith tracing
    on and sets a default project. Idempotent and safe to re-call — e.g. after the CLI's
    ``--trace`` flag sets the opt-in var — since LangChain reads these vars at call time.
    """
    if not tracing_enabled():
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGCHAIN_PROJECT", DEFAULT_PROJECT)
    os.environ.setdefault("LANGSMITH_PROJECT", os.environ["LANGCHAIN_PROJECT"])
    return True


# Apply the policy at import, before any model call reads these vars.
configure_tracing()


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
