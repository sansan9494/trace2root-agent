from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Settings


class ChatModel:
    """Small provider adapter. Deterministic mode keeps the complete app runnable offline."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def mode(self) -> str:
        return self.settings.llm_provider

    @staticmethod
    def final_answer(content: str) -> str:
        """Remove Qwen-style reasoning blocks if a model/backend leaks them."""
        if "</think>" in content:
            content = content.rsplit("</think>", 1)[-1]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
        return content.strip()

    @classmethod
    def structured_answer(cls, content: str) -> str:
        """Read the constrained Ollama response, with plain-text compatibility."""
        cleaned = cls.final_answer(content)
        # Some small models wrap an already-JSON answer inside the schema's
        # string field. Unwrap a small bounded number of times.
        for _ in range(3):
            try:
                value = json.loads(cleaned)
            except json.JSONDecodeError:
                break
            if not isinstance(value, dict) or not isinstance(value.get("answer"), str):
                break
            cleaned = cls.final_answer(value["answer"])
        return cleaned

    async def available(self) -> bool:
        if self.mode != "ollama":
            return True
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{self.settings.ollama_base_url}/api/tags")
                if not response.is_success:
                    return False
                names = {item.get("name") for item in response.json().get("models", [])}
                return self.settings.ollama_model in names
        except httpx.HTTPError:
            return False

    @staticmethod
    def _chatml_text(value: str) -> str:
        """Prevent untrusted evidence from injecting ChatML control tokens."""
        return value.replace("<|", "< |").replace("|>", "| >")

    def _raw_prompt(self, system: str, prompt: str, output_instruction: str) -> str:
        safe_system = self._chatml_text(system)
        safe_prompt = self._chatml_text(prompt)
        return (
            f"<|im_start|>system\n{safe_system}\n{output_instruction}<|im_end|>\n"
            f"<|im_start|>user\n{safe_prompt}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n</think>\n"
        )

    async def generate_json(
        self,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        fallback: dict[str, Any],
        *,
        max_tokens: int = 700,
    ) -> dict[str, Any]:
        """Generate a schema-constrained public decision, or a safe local fallback."""
        if self.mode != "ollama":
            return {**fallback, "_decision_source": "deterministic_fallback"}
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "prompt": self._raw_prompt(
                system,
                prompt,
                "只输出满足给定 JSON Schema 的对象；不要输出隐藏思维过程或额外文字。",
            ),
            "raw": True,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(f"{self.settings.ollama_base_url}/api/generate", json=payload)
                response.raise_for_status()
                value = json.loads(self.final_answer(response.json()["response"]))
                if isinstance(value, dict):
                    return {**value, "_decision_source": "llm"}
                return {**fallback, "_decision_source": "llm_invalid_fallback"}
        except (httpx.HTTPError, KeyError, json.JSONDecodeError, TypeError):
            return {**fallback, "_decision_source": "llm_error_fallback"}

    async def generate(self, system: str, prompt: str, fallback: str) -> str:
        if self.mode != "ollama":
            return fallback
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "prompt": self._raw_prompt(
                system,
                prompt,
                "仅把最终自然语言答复写入 JSON 的 answer 字段，不要复述问题，不解释输出格式。",
            ),
            "raw": True,
            "stream": False,
            "format": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 256},
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(f"{self.settings.ollama_base_url}/api/generate", json=payload)
                response.raise_for_status()
                content = self.structured_answer(response.json()["response"])
                return content or fallback
        except (httpx.HTTPError, KeyError, json.JSONDecodeError):
            return fallback
