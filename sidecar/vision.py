"""OpenAI vision description for a single photo.

One chat call per image returns a strict-JSON object with a prose description
(for semantic embedding) plus structured attributes tuned to the gallery's
filter axes. khora's own ``acompletion`` is text-only, so we call the OpenAI
SDK directly with an image message (same pattern as the repo's
``examples/10_core_apis/07_image_ingestion.py``).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

VISION_MODEL = os.environ.get("PHOTO_VISION_MODEL", "gpt-4o-mini")

_SYSTEM = (
    "You describe photographs for a searchable gallery. Return STRICT JSON with keys:\n"
    '  "description": 2-4 vivid sentences capturing subject, setting, mood (for search).\n'
    '  "location": the place or setting as a short phrase ("beach", "kitchen", "Paris street") or "".\n'
    '  "objects": array of notable things/structures (lowercase short nouns).\n'
    '  "animals": array of animals present, [] if none.\n'
    '  "scene": the activity or scene type ("wedding", "hiking", "sunset") or "".\n'
    '  "tags": array of 3-8 lowercase keywords for filtering.\n'
    "Output ONLY the JSON object."
)

def _as_list(v: Any) -> list[str]:
    """Coerce an array field to a clean list. Small models sometimes return a
    comma-joined string instead of a JSON array — split it, rather than letting
    a downstream ``for x in <string>`` iterate it character by character."""
    if isinstance(v, str):
        parts = v.split(",")
    elif isinstance(v, list):
        parts = v
    else:
        return []
    return [str(x).strip() for x in parts if str(x).strip()]


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()  # reads OPENAI_API_KEY from env
    return _client


async def describe_image(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (attributes, usage) where usage = {model, input, output} token counts."""
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    resp = await _get_client().chat.completions.create(
        model=VISION_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this photo as specified."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
                    },
                ],
            },
        ],
    )
    obj = json.loads(resp.choices[0].message.content or "{}")
    u = resp.usage
    usage = {
        "model": VISION_MODEL,
        "input": getattr(u, "prompt_tokens", 0) or 0,
        "output": getattr(u, "completion_tokens", 0) or 0,
    }
    data = {
        "description": str(obj.get("description", "")).strip(),
        "location": str(obj.get("location", "")).strip(),
        "objects": _as_list(obj.get("objects")),
        "animals": _as_list(obj.get("animals")),
        "scene": str(obj.get("scene", "")).strip(),
        "tags": [t.lower() for t in _as_list(obj.get("tags"))],
    }
    return data, usage
