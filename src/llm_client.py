"""Unified LLM client for text tasks. Supports DeepSeek and Gemini.

Uses DeepSeek by default (cheaper), switches to Gemini via TEXT_LLM env var.
Image generation always uses Gemini (separate module).

Usage:
    from src.llm_client import generate_json

    result = generate_json("Analyze this text...", system="You are a literary analyst.")
"""

import json
import logging
import time
from typing import Any

from src.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    TEXT_LLM,
)

logger = logging.getLogger(__name__)


def _call_deepseek(prompt: str, system: str = "", max_retries: int = 3, max_tokens: int = 8192) -> str:
    """Call DeepSeek API (OpenAI-compatible)."""
    from openai import OpenAI

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            error_str = str(e).lower()
            if any(kw in error_str for kw in ["rate limit", "429", "503"]) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning("DeepSeek rate limit, retrying in %ds: %s", wait, e)
                time.sleep(wait)
                continue
            if attempt < max_retries - 1:
                logger.warning("DeepSeek error (attempt %d): %s", attempt + 1, e)
                time.sleep(2)
                continue
            raise


def _call_gemini(prompt: str, system: str = "", max_retries: int = 3) -> str:
    """Call Gemini API."""
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = genai.types.GenerateContentConfig(
        response_mime_type="application/json",
    )
    if system:
        config.system_instruction = system

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            return response.text
        except Exception as e:
            error_str = str(e).lower()
            if any(kw in error_str for kw in ["rate limit", "429", "resource exhausted", "503"]) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning("Gemini rate limit, retrying in %ds: %s", wait, e)
                time.sleep(wait)
                continue
            if attempt < max_retries - 1:
                logger.warning("Gemini error (attempt %d): %s", attempt + 1, e)
                time.sleep(2)
                continue
            raise


def generate_json(prompt: str, system: str = "", max_retries: int = 3) -> dict[str, Any]:
    """Generate JSON from text LLM (DeepSeek or Gemini based on config).

    Args:
        prompt: The user prompt.
        system: Optional system instruction.
        max_retries: Max retry attempts.

    Returns:
        Parsed JSON dict.
    """
    provider = TEXT_LLM.lower()

    if provider == "deepseek":
        if not DEEPSEEK_API_KEY:
            if GEMINI_API_KEY:
                logger.warning("TEXT_LLM=deepseek but DEEPSEEK_API_KEY not set, falling back to Gemini")
                raw = _call_gemini(prompt, system, max_retries)
            else:
                raise ValueError("TEXT_LLM=deepseek but DEEPSEEK_API_KEY is not set.")
        else:
            raw = _call_deepseek(prompt, system, max_retries)
            logger.debug("Using DeepSeek for text LLM")
    elif provider == "gemini":
        if not GEMINI_API_KEY:
            raise ValueError("TEXT_LLM=gemini but GEMINI_API_KEY is not set.")
        raw = _call_gemini(prompt, system, max_retries)
        logger.debug("Using Gemini for text LLM")
    else:
        raise ValueError(f"Unknown TEXT_LLM provider: {provider}. Use 'deepseek' or 'gemini'.")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to fix common JSON issues
    import re

    # Extract from markdown code blocks
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fix trailing commas: ,} → } and ,] → ]
    fixed = re.sub(r',\s*([}\]])', r'\1', raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Fix unescaped quotes in strings by trying to find the JSON object
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
        fixed2 = re.sub(r',\s*([}\]])', r'\1', match.group(0))
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass

    logger.error("Failed to parse JSON. Raw response: %s", raw[:1000])
    raise ValueError(f"LLM returned invalid JSON")
