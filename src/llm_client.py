"""Unified LLM client for text tasks — a thin facade over DeepSeek.

Kept as a stable import surface so every caller keeps doing
`from src.llm_client import generate_json`; the engine underneath is DeepSeek
(OpenAI-compatible JSON mode + repair fallback, in src/deepseek_client.py).

Usage:
    from src.llm_client import generate_json

    result = generate_json("Analyze this text...", system="You are a literary analyst.")
"""

from typing import Any

from src import deepseek_client


def generate_json(prompt: str, system: str = "", max_retries: int = 3) -> dict[str, Any]:
    """Generate JSON from the text engine (DeepSeek). Delegates to
    deepseek_client at call time so tests can monkeypatch the engine."""
    return deepseek_client.generate_json(prompt, system=system, max_retries=max_retries)
