"""Text embeddings with Gemini (`gemini-embedding-2`, 100+ languages incl. Khmer).

Per Google's guidance for gemini-embedding-2, the task is written into the text itself
("task: search result | query: ...") instead of a task_type parameter, and each text is
wrapped in its own Content so we get one vector per text.
"""

import math
from collections.abc import Callable
from typing import Any

from google.genai import types

Vector = list[float]
MAX_BATCH = 100


def normalize(vec: Vector) -> Vector:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity of two already-normalised vectors."""
    return sum(x * y for x, y in zip(a, b, strict=False))


def query_text(text: str) -> str:
    return f"task: search result | query: {text}"


def document_text(title: str, text: str) -> str:
    return f"title: {title} | text: {text}"


class GeminiEmbedder:
    def __init__(self, get_client: Callable[[], Any], model: str, dimensions: int = 768):
        self._get_client = get_client
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[Vector]:
        vectors: list[Vector] = []
        client = self._get_client()
        for start in range(0, len(texts), MAX_BATCH):
            batch = texts[start : start + MAX_BATCH]
            result = await client.aio.models.embed_content(
                model=self.model,
                contents=[types.Content(parts=[types.Part.from_text(text=t)]) for t in batch],
                config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
            )
            got = [normalize(list(e.values or [])) for e in (result.embeddings or [])]
            if len(got) != len(batch):
                raise RuntimeError(f"expected {len(batch)} embeddings, got {len(got)}")
            vectors.extend(got)
        return vectors
