"""Shared Gemini client (google-genai): one global rate limiter, retry on 429/5xx, JSON output.

Used by llm_cleaner and llm_tagger, so both scripts share the same RPM budget in a process and
neither needs the deprecated google.generativeai package.

Env: GEMINI_API_KEY (required), GEMINI_MODEL, GEMINI_RPM (default 8), GEMINI_MAX_RETRIES.
"""
import json
import os
import re
import threading
import time
from typing import Any

import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
REQUESTS_PER_MINUTE = int(os.getenv("GEMINI_RPM", "8"))  # keep below the real quota
MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "6"))


class RateLimiter:
    """Spaces requests at least 60/RPM seconds apart; safe across threads."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(rpm, 1)
        self._lock = threading.Lock()
        self._next_time = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_time)
            self._next_time = start + self.interval
        if start > now:
            time.sleep(start - now)


limiter = RateLimiter(REQUESTS_PER_MINUTE)
_client = None
_client_lock = threading.Lock()


def get_client():
    """Created lazily, so importing this module never exits the process."""
    global _client
    with _client_lock:
        if _client is None:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY not found (set it in .env)")
            from google import genai

            _client = genai.Client(api_key=key)
        return _client


def retry_delay_from_error(e: Exception, default: float) -> float:
    """Read 'retry in Xs' / retryDelay from a 429 message when present."""
    msg = str(e)
    m = (
        re.search(r"retry in ([\d.]+)s", msg, re.I)
        or re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", msg)
        or re.search(r"seconds:\s*(\d+)", msg)
    )
    return float(m.group(1)) + 1 if m else default


def parse_json_text(text: str) -> Any:
    clean = (text or "").replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def generate_json(prompt: str, *, temperature: float = 0.2) -> Any:
    """Call Gemini and return parsed JSON. Retries 429/500/503 with the server's own delay."""
    from google.genai import errors as gerrors
    from google.genai import types

    client = get_client()
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=temperature
                ),
            )
            return parse_json_text(response.text)
        except gerrors.APIError as e:
            if e.code not in (429, 500, 503):
                raise
            wait = retry_delay_from_error(e, default=min(2 ** attempt * 5, 90))
            logger.warning("Gemini HTTP error", code=e.code, attempt=attempt + 1,
                           retries=MAX_RETRIES, wait_s=round(wait))
            time.sleep(wait)
    raise RuntimeError("Exceeded max retries")
