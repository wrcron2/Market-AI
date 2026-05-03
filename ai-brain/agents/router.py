"""
router.py — LLM Router
======================
Routes tasks to the appropriate LLM backend based on complexity:

  LOW complexity  → Ollama (Qwen2.5-Coder 7B, local, ~2GB VRAM)
                    Use for: summarization, logging, data formatting,
                    quick pattern recognition.

  HIGH complexity → AWS Bedrock (Claude 3.5 Sonnet)
                    Use for: multi-agent debate, final signal validation,
                    nuanced market reasoning, risk assessment.

This respects the 16GB RAM constraint on the developer's MacBook Pro:
heavy Bedrock calls stay in the cloud while Ollama handles fast,
frequent, low-stakes tasks locally.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Type

from pydantic import BaseModel

import ollama
import structlog
from langchain_aws import ChatBedrock
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)


class Complexity(str, Enum):
    LOW  = "low"   # → Ollama
    HIGH = "high"  # → Bedrock


class LLMRouter:
    """Routes prompts to the appropriate LLM backend."""

    def __init__(self) -> None:
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
        self.ollama_host  = os.getenv("OLLAMA_HOST",  "http://127.0.0.1:11434")
        self.bedrock_model = os.getenv(
            "BEDROCK_MODEL_ID",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        self.aws_region = os.getenv("AWS_REGION", "us-east-1")

        self._bedrock: ChatBedrock | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def complete(
        self,
        system: str,
        user: str,
        complexity: Complexity = Complexity.LOW,
        max_tokens: int = 1024,
        schema: Type[BaseModel] | None = None,
    ) -> str:
        """Run a chat completion and return the assistant text."""
        if complexity == Complexity.HIGH:
            return self._bedrock_complete(system, user, max_tokens)
        return self._ollama_complete(system, user, max_tokens, schema=schema)

    def model_tag(self, complexity: Complexity) -> str:
        """Return a short label for the model used (stored on the signal)."""
        if complexity == Complexity.HIGH:
            return f"bedrock/{self.bedrock_model.split('.')[-1]}"
        return f"ollama/{self.ollama_model}"

    # ── Ollama (local) ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def _ollama_complete(self, system: str, user: str, max_tokens: int, schema: Type[BaseModel] | None = None) -> str:
        log.debug("ollama.complete", model=self.ollama_model)
        client = ollama.Client(host=self.ollama_host)
        fmt = schema.model_json_schema() if schema else "json"
        resp = client.chat(
            model=self.ollama_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            options={"num_predict": max_tokens},
            format=fmt,
            think=False,
        )
        content = resp.message.content or ""
        return content.strip()

    # ── AWS Bedrock ────────────────────────────────────────────────────────────

    def _get_bedrock(self) -> ChatBedrock:
        if self._bedrock is None:
            self._bedrock = ChatBedrock(
                model_id=self.bedrock_model,
                region_name=self.aws_region,
                model_kwargs={"max_tokens": 4096},
            )
        return self._bedrock

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _bedrock_complete(self, system: str, user: str, max_tokens: int) -> str:
        log.debug("bedrock.complete", model=self.bedrock_model)
        llm = self._get_bedrock()
        messages: list[BaseMessage] = [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
        response = llm.invoke(messages)
        return str(response.content).strip()
