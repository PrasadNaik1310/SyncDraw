
"""Render the board to a PNG and summarize it with Google Gemini."""

import io
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw

# The API key is read only from GEMINI_API_KEY (project-root .env).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"
# Also sent to Google as X-Server-Timeout; past it Gemini returns 504.
REQUEST_TIMEOUT_MS = 60_000

# Gemini overloaded (500/503): retry after these delays, then fall back.
BUSY_CODES = (500, 503)
RETRY_DELAYS = (2, 4)
# Gemini too slow (504): retrying would wait the full timeout again,
# so go straight to the (faster) fallback model.
TIMEOUT_CODE = 504
BACKGROUND_COLOR = "#FFFFFF"
PADDING = 20
MAX_SIDE = 1024
SUPERSAMPLE = 2

PROMPT = (
    "This is an image of a collaborative whiteboard. Summarize what is "
    "drawn or written: any text, diagrams, shapes and how they relate. "
    "Use short bullet points, at most about 150 words. If it is only "
    "unclear scribbles, say so."
)


class SummaryError(Exception):
    """An error whose message is safe to show to users."""


def render_board(ops):
    """Replay ops onto a white image. Returns PNG bytes, or None if empty."""
    pen_ops = [op for op in ops if op.get("tool", "pen") != "eraser"]

    if not pen_ops:
        return None

    # Crop to the pen strokes (plus half their width) and padding.
    left = min(min(op["x1"], op["x2"]) - op["width"] / 2 for op in pen_ops) - PADDING
    top = min(min(op["y1"], op["y2"]) - op["width"] / 2 for op in pen_ops) - PADDING
    right = max(max(op["x1"], op["x2"]) + op["width"] / 2 for op in pen_ops) + PADDING
    bottom = max(max(op["y1"], op["y2"]) + op["width"] / 2 for op in pen_ops) + PADDING

    # Draw directly at the final scale so huge boards stay small in memory.
    scale = min(1.0, MAX_SIDE / max(right - left, bottom - top))
    out_w = max(1, round((right - left) * scale))
    out_h = max(1, round((bottom - top) * scale))
    k = scale * SUPERSAMPLE

    image = Image.new("RGB", (out_w * SUPERSAMPLE, out_h * SUPERSAMPLE), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    # Version order matters: an eraser only hides what came before it.
    for op in sorted(ops, key=lambda op: op.get("version", 0)):
        if op.get("tool", "pen") == "eraser":
            color = BACKGROUND_COLOR
        else:
            color = op["color"]

        width = max(SUPERSAMPLE, round(op.get("width", 3) * k))
        p1 = ((op["x1"] - left) * k, (op["y1"] - top) * k)
        p2 = ((op["x2"] - left) * k, (op["y2"] - top) * k)

        draw.line([p1, p2], fill=color, width=width)

        # Round line ends, like capstyle=ROUND on the Tk canvas.
        r = width / 2
        for x, y in (p1, p2):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

    image = image.resize((out_w, out_h), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def summarize_image(png_bytes):
    """Send the board image to Gemini and return its text summary."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key or api_key == "your_key_here":
        raise SummaryError("Gemini API key not configured on server.")

    try:
        import httpx
        from google import genai
        from google.genai import errors, types
    except ImportError:
        raise SummaryError("Summary feature not installed on server.") from None

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    fallback = (
        os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
        or DEFAULT_FALLBACK_MODEL
    )

    # Main model up to 3 times, then the fallback model once.
    attempts = [model] * (len(RETRY_DELAYS) + 1)
    if fallback != model:
        attempts.append(fallback)

    contents = [
        types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
        PROMPT,
    ]

    # Low thinking keeps a short summary fast (Gemini 3.x models).
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.LOW,
        ),
    )

    i = 0
    current = model

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

        while True:
            current = attempts[i]

            try:
                response = client.models.generate_content(
                    model=current,
                    contents=contents,
                    config=config,
                )
                break

            except (errors.APIError, httpx.TimeoutException) as exc:
                code = getattr(exc, "code", TIMEOUT_CODE)
                last = i == len(attempts) - 1

                if last or code not in BUSY_CODES + (TIMEOUT_CODE,):
                    raise

                if code == TIMEOUT_CODE:
                    if attempts[-1] == model:
                        raise  # no separate fallback model

                    print(f"Gemini {current} timed out, trying {attempts[-1]}")
                    i = len(attempts) - 1
                    continue

                print(f"Gemini {current} busy ({code}), retrying")

                if i < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[i])
                i += 1

    except errors.APIError as exc:
        print(f"Gemini API error {exc.code} {exc.status}: {exc.message}")

        if exc.code in BUSY_CODES:
            raise SummaryError("Gemini is busy right now, try again in a minute.") from None
        if exc.code == TIMEOUT_CODE:
            raise SummaryError("Gemini took too long to answer, try again.") from None
        if exc.code == 429:
            raise SummaryError("Gemini quota or rate limit reached, try again later.") from None
        if exc.code in (400, 401, 403) and "key" in str(exc.message).lower():
            raise SummaryError("Gemini rejected the server's API key.") from None
        if exc.code == 404:
            raise SummaryError(f"Gemini model '{current}' not found.") from None
        raise SummaryError(f"Gemini API error ({exc.code}).") from None

    except httpx.TimeoutException:
        raise SummaryError("Gemini request timed out.") from None

    except (httpx.TransportError, OSError) as exc:
        print(f"Gemini network error: {exc}")
        raise SummaryError("Could not reach Gemini (network error).") from None

    # Empty or blocked response.
    text = response.text

    if not text or not text.strip():
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason:
            raise SummaryError(f"Gemini blocked the request ({feedback.block_reason}).")

        if response.candidates and response.candidates[0].finish_reason:
            reason = response.candidates[0].finish_reason
            raise SummaryError(f"Gemini returned no text ({reason}).")

        raise SummaryError("Gemini returned an empty response.")

    return text.strip()
