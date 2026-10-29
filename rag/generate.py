
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"

SLOW_REQUEST_SECONDS = 20
REQUEST_TIMEOUT_MS = 45_000

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def generate_answer(question: str, chunks: list[dict]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    if not chunks:
        raise ValueError("No context chunks were provided.")

    primary_model = (
        os.environ.get("GEMINI_MODEL", "").strip()
        or DEFAULT_MODEL
    )
    fallback_model = (
        os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
        or DEFAULT_FALLBACK_MODEL
    )

    context_parts = []

    for chunk in chunks:
        context_parts.append(
            f"Source: {chunk['source']}, Page: {chunk['page']}\n"
            f"{chunk['text']}"
        )

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""
You are a helpful teaching assistant for SyncDraw.

Answer the student's question using only the supplied document context.
If the context does not contain enough information, say so clearly.
Do not invent facts or claim the document says something it does not.
Cite relevant page numbers in your answer.

DOCUMENT CONTEXT:
{context}

STUDENT QUESTION:
{question}
"""

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(
                attempts=1,
            ),
        ),
    )

    models_to_try = [primary_model]

    if fallback_model != primary_model:
        models_to_try.append(fallback_model)

    last_error = None

    for model_name in models_to_try:
        start_time = time.monotonic()
        slow_warning = threading.Timer(
            SLOW_REQUEST_SECONDS,
            lambda: logger.warning(
                "Generation is still waiting after %s seconds "
                "(model=%s).",
                SLOW_REQUEST_SECONDS,
                model_name,
            ),
        )

        logger.info("Starting generation (model=%s).", model_name)
        slow_warning.start()

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

            answer = response.text

            if not answer or not answer.strip():
                raise RuntimeError(
                    f"Model '{model_name}' returned an empty response."
                )

            elapsed = time.monotonic() - start_time

            logger.info(
                "Generation succeeded (model=%s, elapsed=%.2fs).",
                model_name,
                elapsed,
            )

            if model_name != primary_model:
                logger.warning(
                    "Answer generated using fallback model '%s'.",
                    model_name,
                )

            return answer.strip()

        except Exception as exc:
            last_error = exc
            elapsed = time.monotonic() - start_time

            logger.exception(
                "Generation failed (model=%s, elapsed=%.2fs).",
                model_name,
                elapsed,
            )

            # Don't retry another model when credentials are rejected.
            if isinstance(exc, errors.APIError) and exc.code in (401, 403):
                raise

            if model_name == primary_model and len(models_to_try) > 1:
                logger.warning(
                    "Primary model failed; trying fallback model '%s'.",
                    fallback_model,
                )
                continue

            raise

        finally:
            slow_warning.cancel()

    raise RuntimeError("All configured models failed.") from last_error
