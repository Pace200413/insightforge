"""LLM client factory.

Supports Anthropic (paid) and Groq (free tier).
Set LLM_PROVIDER=groq in .env to use Groq.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any


# ── Response adapters (make Groq responses look like Anthropic responses) ──

@dataclass
class _TextBlock:
    text: str
    type: str = "text"

@dataclass
class _ToolUseBlock:
    input: dict
    name: str
    type: str = "tool_use"

@dataclass
class _AdaptedResponse:
    content: list
    stop_reason: str = "tool_use"


# ── Groq client ────────────────────────────────────────────────────────────

class GroqCompletionClient:
    """Text-only client for sql_generator and query_repair."""
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self) -> None:
        from groq import Groq
        from app.core.config import get_settings
        self._client = Groq(api_key=get_settings().groq_api_key)

    def create_message(self, *, model, max_tokens, system, messages, tools=None) -> Any:
        msgs = [{"role": "system", "content": system}] + messages
        resp = self._client.chat.completions.create(
            model=self.MODEL, max_tokens=max_tokens, messages=msgs
        )
        text = resp.choices[0].message.content or ""
        return _AdaptedResponse(content=[_TextBlock(text=text)], stop_reason="end_turn")


class GroqToolClient:
    """Tool-calling client for question_interpreter."""
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self) -> None:
        from groq import Groq
        from app.core.config import get_settings
        self._client = Groq(api_key=get_settings().groq_api_key)

    def create_message(self, *, model, max_tokens, system, messages, tools=None) -> Any:
        msgs = [{"role": "system", "content": system}] + messages
        groq_tools = None
        if tools:
            groq_tools = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                }}
                for t in tools
            ]
        resp = self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=max_tokens,
            messages=msgs,
            tools=groq_tools,
            tool_choice="required" if groq_tools else None,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            block = _ToolUseBlock(
                name=tc.function.name,
                input=json.loads(tc.function.arguments),
            )
            return _AdaptedResponse(content=[block], stop_reason="tool_use")
        text = msg.content or ""
        return _AdaptedResponse(content=[_TextBlock(text=text)], stop_reason="end_turn")


# ── Factory ────────────────────────────────────────────────────────────────

def get_interpreter_client():
    from app.core.config import get_settings
    if get_settings().llm_provider == "groq":
        return GroqToolClient()
    from app.agents.question_interpreter import AnthropicLLMClient
    return AnthropicLLMClient()

def get_completion_client():
    from app.core.config import get_settings
    if get_settings().llm_provider == "groq":
        return GroqCompletionClient()
    from app.agents.sql_generator import AnthropicLLMClient
    return AnthropicLLMClient()
