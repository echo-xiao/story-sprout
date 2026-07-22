"""DeepSeek text client — OpenAI-compatible /chat/completions in JSON mode.

The single door for all TEXT generation (analysis, writing, simplification).
Uses httpx (already a dependency) instead of the openai SDK to keep the Vercel
function bundle small. Returns parsed JSON, with the same repair fallbacks the
old Gemini llm_client had.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _call_deepseek(prompt: str, system: str = "", timeout: float = 120.0) -> str:
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not set.")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = httpx.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if not content:
        raise ValueError("DeepSeek returned empty content (blocked or truncated).")
    return content


def _extract_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        for cand in (m.group(0), re.sub(r",\s*([}\]])", r"\1", m.group(0))):
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                continue
    logger.error("DeepSeek returned invalid JSON. Raw: %s", raw[:1000])
    raise ValueError("LLM returned invalid JSON")


def generate_json(prompt: str, system: str = "", max_retries: int = 3) -> dict[str, Any]:
    """Generate JSON from DeepSeek. Retries transient HTTP errors; raises on a
    persistent failure (same contract as the old Gemini llm_client)."""
    last: Exception | None = None
    for attempt in range(max(1, max_retries)):
        try:
            return _extract_json(_call_deepseek(prompt, system))
        except ValueError:
            raise  # bad key / unparseable JSON — retrying won't help
        except httpx.HTTPError as e:
            last = e
            logger.warning("DeepSeek attempt %d/%d failed: %s", attempt + 1, max_retries, e)
    raise last or ValueError("DeepSeek generate_json failed")
