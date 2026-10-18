"""Async access to Gemini for picking the most interesting holiday or historical event."""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from beans_bot.config.schema import GeminiConfig

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self, config: GeminiConfig):
        self.config = config
        self.client: genai.Client | None = None
        if config.enabled:
            assert config.api_key is not None
            self.client = genai.Client(api_key=config.api_key.get_secret_value())

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def generate(self, prompt: str, response_schema: types.Schema | None = None) -> str | None:
        if self.client is None:
            return None
        assert self.config.model is not None
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=self.config.thinking_level)
            if self.config.thinking_level
            else None,
            response_mime_type="application/json" if response_schema is not None else "text/plain",
            response_schema=response_schema,
        )
        response = await self.client.aio.models.generate_content(model=self.config.model, contents=prompt, config=config)
        return response.text

    async def generate_json(self, prompt: str, response_schema: types.Schema) -> dict[str, Any] | None:
        text = await self.generate(prompt, response_schema)
        if text is None:
            return None
        return json.loads(text)

    async def check(self) -> None:
        """Send a test prompt. Raises if Gemini is configured but doesn't answer."""
        if not self.enabled:
            logger.info("Gemini is not configured; holiday and event picks will be random.")
            return
        text = await self.generate("Respond with the exact word 'pong'")
        logger.info(f"Gemini API test successful. Response: {(text or '<No text returned>').strip()}")
