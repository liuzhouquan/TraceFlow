from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

from config import Settings


class ModelServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelHealth:
    embedding_available: bool
    chat_available: bool
    detail: str


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_./:-]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    chinese_bigrams = [f"{left}{right}" for left, right in zip(chinese, chinese[1:])]
    return latin + chinese + chinese_bigrams


def deterministic_embedding(text: str, dimensions: int = 384) -> list[float]:
    vector = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:8], "little") % dimensions
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def format_embedding_query(query: str, task_instruction: str | None) -> str:
    clean_query = query.strip()
    clean_instruction = (task_instruction or "").strip()
    if not clean_instruction:
        return clean_query
    return f"Instruct: {clean_instruction}\nQuery:{clean_query}"


class ModelClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def embedding_provider(self) -> str:
        return "builtin-demo" if self.settings.provider == "deterministic" else self.settings.provider

    @property
    def embedding_model(self) -> str:
        return "char-ngram-hash-v1" if self.settings.provider == "deterministic" else self.settings.embedding_model

    @property
    def embedding_query_instruction(self) -> str | None:
        if self.settings.provider == "deterministic":
            return None
        instruction = (self.settings.embedding_query_instruction or "").strip()
        return instruction or None

    @property
    def chat_model(self) -> str | None:
        return None if self.settings.provider == "deterministic" else self.settings.chat_model

    def document_embedding_fingerprint(self, dimensions: int) -> tuple[str, str, int]:
        # Query instructions never touch document inputs, so changing one does not invalidate the index.
        return self.embedding_provider, self.embedding_model, dimensions

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            return {}
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    async def health(self) -> ModelHealth:
        if self.settings.provider == "deterministic":
            return ModelHealth(
                embedding_available=True,
                chat_available=False,
                detail="内置字符 n-gram 哈希仅用于零配置理解链路",
            )
        if self.settings.provider == "openai_compatible":
            configured = bool(self.settings.base_url and self.settings.api_key)
            return ModelHealth(
                embedding_available=configured,
                chat_available=configured,
                detail="OpenAI-compatible 接口已配置" if configured else "缺少 MODEL_BASE_URL 或 MODEL_API_KEY",
            )

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.settings.base_url}/api/tags")
                response.raise_for_status()
            names = {str(item.get("name", "")) for item in response.json().get("models", [])}
            embedding_available = self._model_is_present(self.settings.embedding_model, names)
            chat_available = self._model_is_present(self.settings.chat_model, names)
            missing = []
            if not embedding_available:
                missing.append(self.settings.embedding_model)
            if not chat_available:
                missing.append(self.settings.chat_model)
            detail = "Ollama 已就绪" if not missing else f"尚未拉取模型：{', '.join(missing)}"
            return ModelHealth(embedding_available, chat_available, detail)
        except Exception as exc:
            return ModelHealth(False, False, f"Ollama 不可用：{exc}")

    @staticmethod
    def _model_is_present(model: str, names: set[str]) -> bool:
        return model in names or f"{model}:latest" in names or model.removesuffix(":latest") in names

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_texts(texts)

    async def embed_query(self, query: str) -> list[float]:
        query_text = query
        if self.settings.provider != "deterministic":
            query_text = format_embedding_query(query, self.embedding_query_instruction)
        vectors = await self._embed_texts([query_text])
        return vectors[0] if vectors else []

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.provider == "deterministic":
            return [deterministic_embedding(text) for text in texts]
        if self.settings.provider == "ollama":
            return await self._ollama_embeddings(texts)
        return await self._openai_embeddings(texts)

    async def _ollama_embeddings(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.base_url}/api/embed",
                    json={
                        "model": self.settings.embedding_model,
                        "input": texts,
                        "truncate": True,
                    },
                )
                response.raise_for_status()
            vectors = response.json().get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ModelServiceError("Ollama 返回的 Embedding 数量不正确")
            return [[float(value) for value in vector] for vector in vectors]
        except ModelServiceError:
            raise
        except Exception as exc:
            raise ModelServiceError(
                f"无法调用 Ollama Embedding。请先运行 `ollama serve` 并拉取 {self.settings.embedding_model}：{exc}"
            ) from exc

    async def _openai_embeddings(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.base_url}/embeddings",
                    headers=self._headers(),
                    json={"model": self.settings.embedding_model, "input": texts},
                )
                response.raise_for_status()
            rows = sorted(response.json().get("data", []), key=lambda item: int(item.get("index", 0)))
            if len(rows) != len(texts):
                raise ModelServiceError("Embedding 接口返回的向量数量不正确")
            return [[float(value) for value in row["embedding"]] for row in rows]
        except ModelServiceError:
            raise
        except Exception as exc:
            raise ModelServiceError(f"无法调用 OpenAI-compatible Embedding 接口：{exc}") from exc

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        if self.settings.provider == "deterministic":
            raise ModelServiceError("deterministic 模式没有生成模型")
        if self.settings.provider == "ollama":
            return await self._ollama_chat(system_prompt, user_prompt)
        return await self._openai_chat(system_prompt, user_prompt)

    async def _ollama_chat(self, system_prompt: str, user_prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.base_url}/api/chat",
                    json={
                        "model": self.settings.chat_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
            content = str((response.json().get("message") or {}).get("content") or "").strip()
            if not content:
                raise ModelServiceError("Ollama 没有返回回答内容")
            return content
        except ModelServiceError:
            raise
        except Exception as exc:
            raise ModelServiceError(
                f"无法调用 Ollama 对话模型。请确认已拉取 {self.settings.chat_model}：{exc}"
            ) from exc

    async def _openai_chat(self, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
            choices = response.json().get("choices") or []
            content = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "").strip()
            if not content:
                raise ModelServiceError("对话接口没有返回回答内容")
            return content
        except ModelServiceError:
            raise
        except Exception as exc:
            raise ModelServiceError(f"无法调用 OpenAI-compatible 对话接口：{exc}") from exc
