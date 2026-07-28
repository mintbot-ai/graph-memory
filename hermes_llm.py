"""Graphiti LLM adapter that reuses Hermes' active model and provider.

The adapter deliberately resolves the runtime for every extraction call. If the
user switches from the mintbot proxy to Claude Code, Codex, BYOK, or another
Hermes provider, subsequent memory extraction follows that choice without a
second set of credentials or a provider-specific sidecar.

A nested ``AIAgent`` is created with ``skip_memory=True`` and no tools. This is
important: graph extraction must not recursively invoke graph memory.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.prompts.models import Message
from pydantic import BaseModel, ValidationError

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from a model response.

    Hermes providers generally obey the JSON-only instruction, but this accepts
    a fenced object or a short prose prefix without weakening schema validation.
    """
    cleaned = _JSON_FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise json.JSONDecodeError("Model response contained no JSON object", cleaned, 0)


class HermesAgentLLMClient(LLMClient):
    """Use the model/provider currently selected in Hermes for Graphiti."""

    def __init__(self) -> None:
        # Graphiti expects a model label for tracing/cache keys. The actual model
        # is resolved fresh in each call so model switches take effect.
        super().__init__(LLMConfig(model="hermes-active", temperature=0), cache=False)

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16_384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        del model_size  # Hermes resolves the user's selected model, not a size alias.
        return await asyncio.to_thread(
            self._generate_sync,
            messages,
            response_model,
            max_tokens,
        )

    @staticmethod
    def _generate_sync(
        messages: list[Message],
        response_model: type[BaseModel] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from run_agent import AIAgent

        config = load_config()
        model_config = config.get("model") or {}
        if isinstance(model_config, str):
            model = model_config
            requested_provider = None
        else:
            model = str(model_config.get("default") or model_config.get("model") or "")
            requested_provider = model_config.get("provider")

        runtime = resolve_runtime_provider(
            requested=requested_provider,
            target_model=model or None,
        )

        system_parts = [m.content for m in messages if m.role == "system"]
        conversation_parts = [
            f"[{str(m.role).upper()}]\n{m.content}"
            for m in messages
            if m.role != "system"
        ]
        prompt = "\n\n".join(conversation_parts).strip()
        prompt += (
            "\n\nReturn exactly one JSON object. Do not use Markdown fences, "
            "comments, or explanatory text."
        )

        agent = AIAgent(
            base_url=runtime.get("base_url"),
            api_key=runtime.get("api_key"),
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            acp_command=runtime.get("acp_command"),
            acp_args=runtime.get("acp_args"),
            command=runtime.get("command"),
            args=runtime.get("args"),
            model=model,
            max_iterations=1,
            max_tokens=max_tokens,
            enabled_toolsets=[],
            quiet_mode=True,
            skip_context_files=True,
            load_soul_identity=False,
            skip_memory=True,
            platform="graph-memory",
            ephemeral_system_prompt="\n\n".join(system_parts),
            credential_pool=runtime.get("credential_pool"),
            request_overrides=runtime.get("request_overrides"),
        )
        result = agent.run_conversation(prompt)
        if isinstance(result, dict):
            if result.get("failed"):
                raise RuntimeError(result.get("final_response") or "Hermes extraction call failed")
            output = str(result.get("final_response") or "")
        else:
            output = str(result or "")

        payload = _extract_json_object(output)
        if response_model is None:
            return payload
        try:
            return response_model.model_validate(payload).model_dump()
        except ValidationError as exc:
            raise ValueError(f"Hermes extraction response failed schema validation: {exc}") from exc
