"""OpenAI Vision LLM implementation.

This module provides the OpenAI Vision LLM implementation for multimodal
interactions (text + image). Supports gpt-4o, gpt-4o-mini, gpt-4-vision-preview
and other OpenAI vision-capable models for image captioning, visual QA, etc.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.libs.llm.base_llm import ChatResponse, Message
from src.libs.llm.base_vision_llm import BaseVisionLLM, ImageInput


class OpenAIVisionLLMError(RuntimeError):
    """Raised when OpenAI Vision API call fails."""


class OpenAIVisionLLM(BaseVisionLLM):
    """OpenAI Vision LLM provider implementation.

    Implements BaseVisionLLM for OpenAI's chat completions API with vision
    (e.g. gpt-4o, gpt-4-vision-preview). Uses the same message format as
    Azure (content array with text + image_url). Supports image preprocessing
    (resize) via max_image_size.
    """

    DEFAULT_BASE_URL = "https://api.whatai.cc/v1/chat/completions"
    DEFAULT_MAX_IMAGE_SIZE = 2048

    def __init__(
        self,
        settings: Any,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_image_size: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        vision_settings = getattr(settings, "vision_llm", None)
        self.model = model or (getattr(vision_settings, "model", None) if vision_settings else None) or settings.llm.model
        self.default_temperature = settings.llm.temperature
        self.default_max_tokens = settings.llm.max_tokens

        self.api_key = api_key
        if not self.api_key and vision_settings and getattr(vision_settings, "api_key", None):
            self.api_key = vision_settings.api_key
        if not self.api_key:
            self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not provided. Set OPENAI_API_KEY or vision_llm.api_key."
            )

        self.base_url = base_url
        if not self.base_url and vision_settings and getattr(vision_settings, "base_url", None):
            self.base_url = vision_settings.base_url
        if not self.base_url:
            self.base_url = self.DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip("/")

        vision_max = getattr(vision_settings, "max_image_size", None) if vision_settings else None
        self.max_image_size = max_image_size or vision_max or self.DEFAULT_MAX_IMAGE_SIZE

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: Optional[List[Message]] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.validate_text(text)
        self.validate_image(image)

        processed_image = self.preprocess_image(
            image,
            max_size=(self.max_image_size, self.max_image_size),
        )
        image_base64 = self._get_image_base64(processed_image)

        temperature = kwargs.get("temperature", self.default_temperature)
        max_tokens = kwargs.get("max_tokens", self.default_max_tokens)
        model = kwargs.get("model", self.model)

        api_messages: List[Dict[str, Any]] = []
        if messages:
            api_messages.extend([{"role": m.role, "content": m.content} for m in messages])

        current_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{processed_image.mime_type};base64,{image_base64}"
                    },
                },
            ],
        }
        api_messages.append(current_message)

        try:
            response_data = self._call_api(
                messages=api_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response_data["choices"][0]["message"]["content"]
            usage = response_data.get("usage")
            return ChatResponse(
                content=content,
                model=response_data.get("model", model),
                usage=usage,
                raw_response=response_data,
            )
        except KeyError as e:
            raise OpenAIVisionLLMError(
                f"[OpenAI Vision] Unexpected response format: missing key {e}"
            ) from e
        except Exception as e:
            if isinstance(e, OpenAIVisionLLMError):
                raise
            raise OpenAIVisionLLMError(
                f"[OpenAI Vision] API call failed: {type(e).__name__}: {e}"
            ) from e

    def preprocess_image(
        self,
        image: ImageInput,
        max_size: Optional[tuple[int, int]] = None,
    ) -> ImageInput:
        if not max_size:
            return image
        try:
            from PIL import Image
        except ImportError:
            return image

        if image.data:
            image_bytes = image.data
        elif image.path:
            image_bytes = Path(image.path).read_bytes()
        elif image.base64:
            return image
        else:
            return image

        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        max_width, max_height = max_size
        if width <= max_width and height <= max_height:
            return image

        ratio = min(max_width / width, max_height / height)
        new_size = (int(width * ratio), int(height * ratio))
        img_resized = img.resize(new_size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img_format = img.format or "PNG"
        img_resized.save(buffer, format=img_format)
        return ImageInput(data=buffer.getvalue(), mime_type=image.mime_type)

    def _get_image_base64(self, image: ImageInput) -> str:
        try:
            if image.base64:
                return image.base64
            if image.data:
                return base64.b64encode(image.data).decode("utf-8")
            if image.path:
                return base64.b64encode(Path(image.path).read_bytes()).decode("utf-8")
            raise ValueError("ImageInput has no valid data source")
        except Exception as e:
            raise OpenAIVisionLLMError(
                f"[OpenAI Vision] Failed to encode image: {e}"
            ) from e

    def _call_api(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        base = self.base_url.rstrip("/")
        url = base if base.endswith("chat/completions") else f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, json=payload, headers=headers)
                if response.status_code != 200:
                    try:
                        err = response.json().get("error", {})
                        msg = err.get("message", response.text) if isinstance(err, dict) else response.text
                    except Exception:
                        msg = response.text
                    raise OpenAIVisionLLMError(
                        f"[OpenAI Vision] API error (HTTP {response.status_code}): {msg}"
                    )
                return response.json()
        except httpx.TimeoutException as e:
            raise OpenAIVisionLLMError(
                "[OpenAI Vision] Request timed out after 60 seconds"
            ) from e
        except httpx.RequestError as e:
            raise OpenAIVisionLLMError(
                f"[OpenAI Vision] Request failed: {type(e).__name__}: {e}"
            ) from e
