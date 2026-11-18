from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from conftest import API_KEY, FakeHttp, Ir, schema_of
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from decodo_adk import DecodoToolset


class ScriptedModel(BaseLlm):
    """Calls decodo_search once, then answers in text."""

    requests: list[LlmRequest] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if len(self.requests) == 1:
            part = types.Part(function_call=types.FunctionCall(name="decodo_search", args={"query": "decodo"}))
        else:
            part = types.Part(text="done")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def run_once(agent: LlmAgent) -> None:
    runner = Runner(agent=agent, app_name="t", session_service=InMemorySessionService(), auto_create_session=True)
    message = types.Content(role="user", parts=[types.Part(text="research decodo")])
    async for _ in runner.run_async(user_id="u", session_id="s", new_message=message):
        pass
    await runner.close()


def test_agent_sees_the_tools_and_gets_an_error_back_as_a_result(ir: Ir, http: FakeHttp) -> None:
    model = ScriptedModel(model="scripted", requests=[])
    toolset = DecodoToolset(api_key=API_KEY, schema=schema_of(ir))
    http.answer(429, {"message": "slow down"})

    asyncio.run(run_once(LlmAgent(name="a", model=model, tools=[toolset])))

    first, second = model.requests
    assert first.config is not None and first.config.tools is not None
    declared = [d.name for tool in first.config.tools for d in tool.function_declarations or []]  # type: ignore[union-attr]
    assert "decodo_search" in declared and "decodo_crawl_results" in declared
    responses: list[dict[str, Any]] = [
        part.function_response.response or {}
        for content in second.contents
        for part in content.parts or []
        if part.function_response
    ]
    assert responses[0]["code"] == "rate_limited"
