"""Deterministic mock provider — lets the agent run end-to-end with no API keys.

It simulates a tool-using support agent: given a help-search tool and a user
question, it first calls ``search_help_docs``; on the next turn (once tool
results are present) it answers from the retrieved passages with a citation, or
says it can't find the answer. Good enough to demo and to unit-test the engine
loop; real answers come from the Anthropic/OpenAI adapters.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.providers.base import EmitFn, LLMResponse, ToolCall, Usage
from app.config import settings


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


class MockProvider:
    name = "mock"

    def _usage(self, system: str, messages: list[dict], completion: str) -> Usage:
        prompt = system + "".join(str(m.get("content") or "") for m in messages)
        return Usage(
            model=settings.chat_model,
            prompt_tokens=_estimate_tokens(prompt),
            completion_tokens=_estimate_tokens(completion),
        )

    @staticmethod
    def _latest_user_text(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return str(m.get("content") or "")
        return ""

    @staticmethod
    def _collect_passages(messages: list[dict]) -> list[tuple[str, str]]:
        passages: list[tuple[str, str]] = []
        for m in messages:
            if m.get("role") != "tool":
                continue
            try:
                payload = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                continue
            results = (payload.get("data") or {}).get("results") or []
            for r in results:
                if r.get("content"):
                    passages.append((r.get("doc_title", "a help doc"), r["content"]))
        return passages

    def _compose_answer(self, messages: list[dict]) -> str:
        passages = self._collect_passages(messages)
        if not passages:
            return (
                "I couldn't find that in our help docs. If you'd like, I can "
                "escalate this to a human teammate."
            )
        title, content = passages[0]
        snippet = " ".join(content.split())[:300]
        return f"Based on our help docs: {snippet} (Source: {title})"

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: bool = False,
        emit: EmitFn | None = None,
    ) -> LLMResponse:
        tool_names = {t["name"] for t in tools}
        has_tool_results = any(m.get("role") == "tool" for m in messages)

        # First, search the docs if we can and haven't yet.
        if "search_help_docs" in tool_names and not has_tool_results:
            call = ToolCall(
                id="mock_call_search",
                name="search_help_docs",
                arguments={"query": self._latest_user_text(messages)},
            )
            return LLMResponse(
                text=None,
                tool_calls=[call],
                usage=self._usage(system, messages, "[search_help_docs]"),
            )

        # Otherwise answer from whatever tool results we have.
        answer = self._compose_answer(messages)
        if stream and emit is not None:
            for word in answer.split(" "):
                await emit("token", {"text": word + " "})
        return LLMResponse(
            text=answer, tool_calls=[], usage=self._usage(system, messages, answer)
        )
