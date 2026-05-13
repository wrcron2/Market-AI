"""
router.py — LLM Router
======================
Routes tasks to the appropriate LLM backend based on complexity:

  LOW complexity  → Ollama (Qwen2.5-Coder 7B, local)
                    Use for: signal generation, risk assessment, fast pattern matching.

  HIGH complexity → AWS Bedrock (Claude Sonnet)
                    Use for: bull-bear debate, nuanced market reasoning.
"""
from __future__ import annotations

import json
import os
import re
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
        self.ollama_model  = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
        self.ollama_host   = os.getenv("OLLAMA_HOST",  "http://127.0.0.1:11434")
        self.bedrock_model = os.getenv(
            "BEDROCK_MODEL_ID",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        self.aws_region = os.getenv("AWS_REGION", "us-east-1")

        # Cached clients — created once, reused across all calls
        self._ollama_client: ollama.Client = ollama.Client(host=self.ollama_host)
        self._bedrock: ChatBedrock | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def complete(
        self,
        system: str,
        user: str,
        complexity: Complexity = Complexity.LOW,
        max_tokens: int = 512,
        schema: Type[BaseModel] | None = None,
    ) -> str:
        """Run a chat completion and return the assistant text."""
        if complexity == Complexity.HIGH:
            return self._bedrock_complete(system, user, max_tokens, schema=schema)
        return self._ollama_complete(system, user, max_tokens, schema=schema)

    def model_tag(self, complexity: Complexity) -> str:
        """Return a short label for the model used (stored on the signal)."""
        if complexity == Complexity.HIGH:
            return f"bedrock/{self.bedrock_model.split('.')[-1]}"
        return f"ollama/{self.ollama_model}"

    # ── Ollama (local) ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def _ollama_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
        schema: Type[BaseModel] | None = None,
    ) -> str:
        log.debug("ollama.complete", model=self.ollama_model)
        fmt = schema.model_json_schema() if schema else "json"
        resp = self._ollama_client.chat(
            model=self.ollama_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            options={"num_predict": max_tokens},
            format=fmt,
            think=False,
        )
        return (resp.message.content or "").strip()

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
    def _bedrock_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
        schema: Type[BaseModel] | None = None,
    ) -> str:
        log.debug("bedrock.complete", model=self.bedrock_model)

        # When a schema is provided, append the JSON schema to the system prompt
        # so Bedrock knows exactly what structure to output
        effective_system = system
        if schema:
            schema_str = json.dumps(schema.model_json_schema(), indent=2)
            effective_system = (
                f"{system}\n\n"
                f"Your response MUST be a valid JSON object matching this schema:\n"
                f"{schema_str}\n"
                f"Output ONLY the JSON object, no markdown, no explanation."
            )

        llm = self._get_bedrock()
        messages: list[BaseMessage] = [
            SystemMessage(content=effective_system),
            HumanMessage(content=user),
        ]
        response = llm.invoke(messages)
        raw = str(response.content).strip()

        # Extract JSON when schema is expected, in case model adds surrounding text
        if schema:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return match.group(0)

        return raw
