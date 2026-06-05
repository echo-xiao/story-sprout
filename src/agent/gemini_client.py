"""Wrapper around google.genai for Gemini API calls."""

import json
import logging
import time
from typing import Any

from google import genai

from src.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Lazily initialize and return the Gemini client."""
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set. Please set it in your environment.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def generate_text(
    prompt: str,
    system_instruction: str = "",
    *,
    max_retries: int = 3,
    model: str | None = None,
) -> str:
    """Generate text content using Gemini.

    Args:
        prompt: The user prompt to send.
        system_instruction: Optional system instruction for the model.
        max_retries: Maximum number of retries with exponential backoff.
        model: Override the default model.

    Returns:
        The generated text string.
    """
    client = _get_client()
    config = genai.types.GenerateContentConfig()
    if system_instruction:
        config.system_instruction = system_instruction

    target_model = model or GEMINI_MODEL

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=config,
            )
            return response.text
        except Exception as e:
            error_str = str(e).lower()
            is_retryable = any(
                keyword in error_str
                for keyword in ["rate limit", "429", "resource exhausted", "503", "overloaded"]
            )
            if is_retryable and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited on attempt %d/%d. Retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.error("Gemini API error: %s", e)
                raise


def generate_json(
    prompt: str,
    system_instruction: str = "",
    *,
    max_retries: int = 3,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate JSON content using Gemini with structured output.

    Args:
        prompt: The user prompt to send.
        system_instruction: Optional system instruction for the model.
        max_retries: Maximum number of retries with exponential backoff.
        model: Override the default model.

    Returns:
        Parsed JSON dictionary from the model response.
    """
    client = _get_client()
    config = genai.types.GenerateContentConfig(
        response_mime_type="application/json",
    )
    if system_instruction:
        config.system_instruction = system_instruction

    target_model = model or GEMINI_MODEL

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=config,
            )
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            # Try to fix common JSON issues (trailing commas, unescaped quotes)
            if attempt < max_retries - 1:
                logger.warning("JSON parse failed (attempt %d), retrying: %s", attempt + 1, e)
                time.sleep(2)
                continue
            logger.error("Failed to parse JSON from Gemini response: %s", e)
            raise ValueError(f"Gemini returned invalid JSON: {e}") from e
        except Exception as e:
            error_str = str(e).lower()
            is_retryable = any(
                keyword in error_str
                for keyword in ["rate limit", "429", "resource exhausted", "503", "overloaded"]
            )
            if is_retryable and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited on attempt %d/%d. Retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.error("Gemini API error: %s", e)
                raise
