"""LLM-based image description for RAG document processing."""

import base64
import logging
from abc import ABC, abstractmethod

from openai import OpenAI

logger = logging.getLogger(__name__)

IMAGE_DESCRIPTION_PROMPT = (
    "Describe this image in detail. Focus on any text, data, charts, diagrams, "
    "or visual information that would be useful for document search and retrieval. "
    "Be concise but comprehensive."
)


class BaseImageDescriber(ABC):
    """Abstract base for LLM-based image description."""

    @abstractmethod
    async def describe(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        """Generate a text description of an image."""


class OpenAIImageDescriber(BaseImageDescriber):
    """LLM image describer backed by an OpenAI-compatible chat endpoint.

    Uses the same OpenRouter-compatible client pattern as the embedding
    provider (``app/services/rag/embeddings.py``), so vision-capable models
    on any OpenAI-compatible base URL can be used.
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        prompt: str = IMAGE_DESCRIPTION_PROMPT,
    ) -> None:
        """Initialize the image describer.

        Args:
            model: The vision-capable chat model name (e.g. via OpenRouter).
            api_key: API key; falls back to OPENAI_API_KEY env var when empty.
            base_url: Override base URL (e.g. OpenRouter-compatible endpoint).
            prompt: System prompt guiding image description.
        """
        self.model = model
        self.prompt = prompt
        self.client = OpenAI(api_key=api_key or None, base_url=base_url)

    async def describe(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        """Generate a text description of an image via chat completions."""
        data_uri = f"data:{mime_type};base64,{_b64_encode(image_bytes)}"
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=512,
        )
        content = response.choices[0].message.content
        return content or ""


def _b64_encode(image_bytes: bytes) -> str:
    """Base64-encode raw image bytes."""
    return base64.b64encode(image_bytes).decode("utf-8")
