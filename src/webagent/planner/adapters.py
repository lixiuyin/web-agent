"""Multi-provider API adapters (migrated from api_adapters.py)."""

from __future__ import annotations

from typing import Any, Protocol


class APIAdapter(Protocol):
    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict: ...
    def build_headers(self, api_key: str) -> dict[str, str]: ...
    def parse_response(self, data: dict) -> str: ...


class OpenAIAdapter:
    """OpenAI-compatible API (OpenAI, DashScope, vLLM server, etc.)."""

    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict:
        return {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    def parse_response(self, data: dict) -> str:
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        return data.get("response", "")


class AzureAdapter:
    """Azure OpenAI API."""

    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict:
        return {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json", "api-key": api_key}

    def parse_response(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]


class ClaudeAdapter:
    """Anthropic Claude API."""

    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict:
        claude_messages = [m for m in messages if m["role"] != "system"]
        return {"model": model, "max_tokens": max_tokens, "messages": claude_messages}

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    def parse_response(self, data: dict) -> str:
        return data["content"][0]["text"]


class GeminiAdapter:
    """Google Gemini API."""

    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict:
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = "user" if msg["role"] in ("user", "system") else "model"
            parts: list[dict[str, Any]] = []
            if isinstance(msg["content"], str):
                parts.append({"text": msg["content"]})
            elif isinstance(msg["content"], list):
                for item in msg["content"]:
                    if item["type"] == "text":
                        parts.append({"text": item["text"]})
                    elif item["type"] == "image_url":
                        parts.append(
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": item["image_url"]["url"].split(",")[1],
                                }
                            }
                        )
            contents.append({"role": role, "parts": parts})
        return {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def parse_response(self, data: dict) -> str:
        return data["candidates"][0]["content"]["parts"][0]["text"]


class MiniMaxAdapter:
    """MiniMax API (M2.5 with vision support).

    MiniMax uses OpenAI-compatible format but may require specific configurations.
    """

    def build_payload(
        self, model: str, messages: list, max_tokens: int, temperature: float
    ) -> dict:
        # Process messages to ensure proper image format
        processed_messages = []
        for msg in messages:
            processed_msg = dict(msg)
            if isinstance(msg.get("content"), list):
                processed_content = []
                for item in msg["content"]:
                    if isinstance(item, dict):
                        # Ensure image_url has detail parameter
                        if item.get("type") == "image_url":
                            image_url_item = dict(item.get("image_url", {}))
                            if "detail" not in image_url_item:
                                image_url_item["detail"] = "high"
                            processed_content.append(
                                {"type": "image_url", "image_url": image_url_item}
                            )
                        else:
                            processed_content.append(item)
                    else:
                        processed_content.append(item)
                processed_msg["content"] = processed_content
            processed_messages.append(processed_msg)

        return {
            "model": model,
            "messages": processed_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    def parse_response(self, data: dict) -> str:
        if "choices" in data:
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
            return content
        # Handle alternative response formats
        response = data.get("response", "")
        if not response and "data" in data:
            response = data.get("data", {}).get("content", "")
        return response


def get_adapter(api_url: str, provider: str | None = None) -> APIAdapter:
    """Auto-detect or select an API adapter by provider name / URL."""
    if provider:
        registry: dict[str, type] = {
            "openai": OpenAIAdapter,
            "azure": AzureAdapter,
            "claude": ClaudeAdapter,
            "gemini": GeminiAdapter,
            "minimax": MiniMaxAdapter,
        }
        return registry[provider]()

    url_lower = api_url.lower()
    if "anthropic.com" in url_lower or "claude" in url_lower:
        return ClaudeAdapter()
    if "azure.com" in url_lower or "openai.azure" in url_lower:
        return AzureAdapter()
    if "generativelanguage.googleapis.com" in url_lower or "gemini" in url_lower:
        return GeminiAdapter()
    if "minimaxi.com" in url_lower or "minimax" in url_lower:
        return MiniMaxAdapter()
    return OpenAIAdapter()
