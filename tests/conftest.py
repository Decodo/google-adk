from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from decodo import RemoteSchema

API_KEY = "test-key"
CLIENT = "google-adk-python"

# Importing research_agent builds the agent, which needs a key to exist.
os.environ.setdefault("DECODO_API_KEY", API_KEY)

# Cut from the Decodo IR built for version 2.6.0: its real endpoints, flows and surface.
PUBLISHED_IR = json.loads((Path(__file__).parent / "fixtures" / "ir.json").read_text(encoding="utf-8"))

Ir = dict[str, Any]


def schema_of(ir: Ir) -> RemoteSchema:
    targets = ir["apis"]["webScrapingApi"]["targets"]
    return RemoteSchema(ir, {name: target["parameter_schema"] for name, target in targets.items()})  # type: ignore[arg-type]


def tool_entry(ir: Ir, name: str) -> dict[str, Any]:
    return next(tool for tool in ir["surfaces"][CLIENT]["tools"] if tool["name"] == name)


class FakeHttp:
    """Replaces the SDK's httpx.request: records every call and answers from a queue."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answers: list[httpx.Response | Exception] = []

    def answer(self, status: int = 200, body: Any = None) -> None:
        self.answers.append(httpx.Response(status, json=body if body is not None else {}))

    def fail(self, error: Exception) -> None:
        self.answers.append(error)

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def ir() -> Ir:
    """A copy of the published IR that a test may change."""
    return copy.deepcopy(PUBLISHED_IR)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr("decodo.http.httpx.request", fake)
    return fake
