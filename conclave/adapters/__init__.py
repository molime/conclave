"""Adapter registry + factory."""
from __future__ import annotations

from .base import Adapter, AdapterResult
from .cli import (
    HermesAdapter, CodexAdapter, KimiAdapter, ClaudeAdapter, AiderAdapter,
)
from .http import OpenAIHttpAdapter, OllamaHttpAdapter, OllamaActAdapter

REGISTRY = {
    "cli_hermes": HermesAdapter,
    "cli_codex": CodexAdapter,
    "cli_kimi": KimiAdapter,
    "cli_claude": ClaudeAdapter,
    "cli_aider": AiderAdapter,
    "http_openai": OpenAIHttpAdapter,
    "http_ollama": OllamaHttpAdapter,
    "http_ollama_act": OllamaActAdapter,
}


def make_adapter(backend) -> Adapter:
    cls = REGISTRY.get(backend.kind)
    if not cls:
        raise ValueError(
            f"unknown backend kind '{backend.kind}'. Known: {', '.join(REGISTRY)}"
        )
    return cls(backend)


__all__ = ["Adapter", "AdapterResult", "make_adapter", "REGISTRY"]
