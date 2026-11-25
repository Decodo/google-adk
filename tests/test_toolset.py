from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest
from conftest import API_KEY, CLIENT, FakeHttp, Ir, schema_of, tool_entry
from decodo import BundledSchema, RemoteSchema

from decodo_adk import DecodoTool, DecodoToolset, ToolSpec
from decodo_adk.client import INTEGRATION_HEADER, load_schema
from decodo_adk.tool_specs import CEILINGS, Limits, specs_from_surface

LIMITS = Limits()

TOOL_NAMES = [
    "decodo_search",
    "decodo_scrape",
    "decodo_map",
    "decodo_crawl_start",
    "decodo_crawl_results",
    "decodo_crawl_cancel",
]


def tools_of(ir: Ir, specs: tuple[ToolSpec, ...] | None = None) -> dict[str, DecodoTool]:
    toolset = DecodoToolset(api_key=API_KEY, schema=schema_of(ir), specs=specs)
    return {tool.name: tool for tool in asyncio.run(toolset.get_tools())}  # type: ignore[misc]


def spec_of(ir: Ir, name: str) -> ToolSpec:
    return next(spec for spec in specs_from_surface(ir["surfaces"][CLIENT]) if spec.name == name)


def parameters(tool: DecodoTool) -> dict[str, Any]:
    declared = tool._get_declaration().parameters_json_schema
    assert isinstance(declared, dict)
    return declared


def call(tool: DecodoTool, **args: Any) -> dict[str, Any]:
    result = asyncio.run(tool.run_async(args=args, tool_context=None))  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result


def test_the_tools_are_the_published_ones(ir: Ir) -> None:
    assert list(tools_of(ir)) == TOOL_NAMES


def test_inputs_are_the_shown_fields_with_the_api_types(ir: Ir) -> None:
    declared = parameters(tools_of(ir)["decodo_map"])

    assert list(declared["properties"]) == ["url", "limit", "sitemap", "domain_filter"]
    assert declared["properties"]["sitemap"]["enum"] == ["include", "only", "skip"]
    assert declared["required"] == ["url"]


def test_a_field_added_to_the_published_surface_appears_with_no_code_change(ir: Ir) -> None:
    tool_entry(ir, "decodo_search")["fields"].append("gl")

    assert parameters(tools_of(ir)["decodo_search"])["properties"]["gl"] == {"type": "string"}


def test_a_renamed_and_reworded_tool_needs_no_code_change(ir: Ir) -> None:
    tool_entry(ir, "decodo_search").update(name="web_search", description="Look things up.")

    tool = tools_of(ir)["web_search"]

    assert tool.description == "Look things up."


def test_an_api_change_to_a_shown_field_reaches_the_tool(ir: Ir) -> None:
    ir["operations"]["mapSite"]["requestSchema"]["properties"]["sitemap"]["enum"].append("auto")

    assert "auto" in parameters(tools_of(ir)["decodo_map"])["properties"]["sitemap"]["enum"]


def test_a_tool_for_another_scrape_target_is_only_a_surface_entry(ir: Ir, http: FakeHttp) -> None:
    ir["apis"]["webScrapingApi"]["targets"]["amazon_search"] = {
        "group": "Amazon",
        "response_format": "json",
        "parameter_schema": {
            "type": "object",
            "required": ["target", "query"],
            "properties": {"target": {"const": "amazon_search"}, "query": {"type": "string"}},
        },
    }
    ir["surfaces"][CLIENT]["tools"].append(
        {
            "name": "amazon_search",
            "description": "Search Amazon.",
            "flow": "scrape",
            "target": "amazon_search",
            "fields": ["query"],
            "fixed": {"target": "amazon_search"},
        }
    )
    http.answer(body={"results": [{"content": {"products": []}}]})

    result = call(tools_of(ir)["amazon_search"], query="kettle")

    assert http.calls[0]["url"].endswith("/v1/scrape")
    assert http.calls[0]["json"] == {"query": "kettle", "target": "amazon_search"}
    assert result == {"results": [{"content": {"products": []}}]}


def test_tools_for_a_job_flow_this_client_never_saw_are_only_surface_entries(ir: Ir, http: FakeHttp) -> None:
    ir["surfaces"][CLIENT]["tools"] += [
        {
            "name": "task_start",
            "description": "Queue a scrape.",
            "flow": "task",
            "step": "start",
            "target": "universal",
            "fields": ["url"],
            "required": ["url"],
            "fixed": {"target": "universal"},
        },
        {"name": "task_results", "description": "Read a queued scrape.", "flow": "task", "step": "results"},
    ]
    tools = tools_of(ir)
    http.answer(body={"id": "777", "status": "pending"})
    http.answer(body={"id": "777", "status": "done"})
    http.answer(body={"results": [{"content": "<html>", "status_code": 200}]})

    started = call(tools["task_start"], url="https://a.test")
    finished = call(tools["task_results"], id=started["id"])

    assert [sent["url"].split("/v1")[1] for sent in http.calls] == ["/task", "/task/777", "/task/777/results"]
    assert list(parameters(tools["task_results"])["properties"]) == ["id"]
    assert finished == {"id": "777", "status": "done", "results": [{"content": "<html>", "status_code": 200}]}


def test_a_tool_for_a_batch_flow_is_only_a_surface_entry(ir: Ir, http: FakeHttp) -> None:
    ir["surfaces"][CLIENT]["tools"].append(
        {
            "name": "scrape_many",
            "description": "Queue many scrapes.",
            "flow": "task_batch",
            "fields": ["url"],
            "fixed": {"target": "universal"},
        }
    )
    http.answer(body={"id": 9, "queries": [{"id": "1", "status": "pending", "url": "https://a.test/1"}]})

    result = call(tools_of(ir)["scrape_many"], url=["https://a.test/1"])

    assert http.calls[0]["url"].endswith("/v1/task/batch")
    assert result == {"jobs": [{"id": "1", "status": "pending"}]}


def test_a_changed_flow_endpoint_is_followed(ir: Ir, http: FakeHttp) -> None:
    ir["operations"]["runSearch"]["path"] = "/v2/search"
    http.answer(body={"organic": []})

    call(tools_of(ir)["decodo_search"], query="q")

    assert http.calls[0]["url"] == "https://data.decodo.com/v2/search"


def test_a_new_pending_status_keeps_the_results_tool_waiting(ir: Ir, http: FakeHttp, fast_clock: list[float]) -> None:
    ir["flows"]["crawl"]["pendingStatuses"].append("queued")
    for _ in range(20):
        http.answer(body={"crawl_id": "c1", "status": "queued"})

    assert "again right away" in call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")["next"]


def test_published_default_replaces_the_api_default_and_is_sent(ir: Ir, http: FakeHttp) -> None:
    tool = tools_of(ir)["decodo_map"]
    http.answer(body={"results": [{"status_code": 200, "url": "https://a.test", "content": {"links": []}}]})

    call(tool, url="https://a.test")

    assert parameters(tool)["properties"]["limit"]["default"] == 100
    assert http.calls[0]["json"] == {"limit": 100, "url": "https://a.test"}


def test_published_text_replaces_the_api_description(ir: Ir) -> None:
    declared = parameters(tools_of(ir)["decodo_search"])

    assert declared["properties"]["page"]["description"] == "Results page number, a whole number starting at 1."


@pytest.mark.parametrize("limit", [10_000, 9999.0])
def test_published_cap_lowers_the_maximum_and_the_value_sent(ir: Ir, http: FakeHttp, limit: float) -> None:
    tool = tools_of(ir)["decodo_crawl_start"]
    http.answer(body={"crawl_id": "c1", "status": "pending"})

    call(tool, url="https://a.test", limit=limit)

    assert parameters(tool)["properties"]["limit"]["maximum"] == 50
    assert http.calls[0]["json"]["limit"] == 50


def test_scrape_requires_url_and_sends_the_fixed_values(ir: Ir, http: FakeHttp) -> None:
    tool = tools_of(ir)["decodo_scrape"]
    http.answer(body={"results": [{"status_code": 200, "url": "https://a.test", "content": "# Title"}]})

    result = call(tool, url="https://a.test", markdown=False, unknown="x")

    assert parameters(tool)["required"] == ["url"]
    assert "markdown" not in parameters(tool)["properties"]
    assert http.calls[0]["json"] == {"url": "https://a.test", "target": "universal", "markdown": True}
    assert result == {"url": "https://a.test", "content": "# Title"}


def test_missing_required_input_is_a_result_and_nothing_is_sent(ir: Ir, http: FakeHttp) -> None:
    tools = tools_of(ir)

    assert call(tools["decodo_crawl_results"])["code"] == "invalid_input"
    assert call(tools["decodo_search"], query=None)["code"] == "invalid_input"
    assert http.calls == []


def test_input_the_api_schema_refuses_is_not_sent(ir: Ir, http: FakeHttp) -> None:
    result = call(tools_of(ir)["decodo_scrape"], url="https://a.test", headless="pdf")

    assert result["code"] == "invalid_input"
    assert http.calls == []


def test_every_request_carries_the_key_and_the_integration_header(ir: Ir, http: FakeHttp) -> None:
    tools = tools_of(ir)
    http.answer(body={"organic": []})
    http.answer(body={"crawl_id": "c1", "status": "pending"})
    http.answer(body={"crawl_id": "c1", "status": "cancelled"})

    call(tools["decodo_search"], query="q")
    call(tools["decodo_crawl_start"], url="https://a.test")
    call(tools["decodo_crawl_cancel"], crawl_id="c1")

    assert [sent["url"] for sent in http.calls] == [
        "https://data.decodo.com/v1/search",
        "https://data.decodo.com/v1/crawl",
        "https://data.decodo.com/v1/crawl/c1/cancel",
    ]
    for sent in http.calls:
        assert sent["headers"]["x-integration"] == INTEGRATION_HEADER == "google-adk-python"
        assert sent["headers"]["Authorization"] == f"Bearer {API_KEY}"


def test_search_drops_the_echoed_parameters(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"organic": [{"rank": 1, "title": "T", "link": "https://a.test"}], "search_parameters": {}})

    result = call(tools_of(ir)["decodo_search"], query="decodo")

    assert result == {"organic": [{"rank": 1, "title": "T", "link": "https://a.test"}]}


def test_crawl_start_bounds_the_crawl_and_returns_its_id(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"crawl_id": "c1", "status": "pending"})

    result = call(tools_of(ir)["decodo_crawl_start"], url="https://a.test")

    assert http.calls[0]["json"] == {"limit": 10, "url": "https://a.test", "output": "markdown"}
    assert (result["crawl_id"], result["status"]) == ("c1", "pending")


def test_crawl_results_returns_pages_once_settled(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"crawl_id": "c1", "status": "truncated", "reason": "max_duration"})
    http.answer(body={"crawl_id": "c1", "url": [{"url": "https://a.test/1", "status": "done", "content": "one"}]})

    result = call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert http.calls[1]["url"] == "https://data.decodo.com/v1/crawl/c1?type=content"
    assert result == {
        "crawl_id": "c1",
        "status": "truncated",
        "reason": "max_duration",
        "items": [{"url": "https://a.test/1", "status": "done", "content": "one"}],
    }


@pytest.fixture
def fast_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    import decodo.api.flows as flows_module

    clock = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(flows_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(flows_module.time, "sleep", sleep)
    return slept


def test_crawl_results_waits_inside_the_call_then_asks_to_be_called_again(
    ir: Ir, http: FakeHttp, fast_clock: list[float]
) -> None:
    for _ in range(20):
        http.answer(body={"crawl_id": "c1", "status": "pending"})

    result = call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert sum(fast_clock) == LIMITS.wait_seconds
    assert (result["crawl_id"], result["status"]) == ("c1", "pending")
    assert "again right away" in result["next"]
    assert all(sent["method"] == "GET" for sent in http.calls)


def test_long_content_is_cut(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"results": [{"status_code": 200, "content": "x" * (LIMITS.max_text_chars + 1)}]})

    result = call(tools_of(ir)["decodo_scrape"], url="https://a.test")

    assert len(result["content"]) == LIMITS.max_text_chars
    assert result["truncated"] is True


def test_map_links_are_capped(ir: Ir, http: FakeHttp) -> None:
    links = [{"url": f"https://a.test/{n}"} for n in range(LIMITS.max_list_values + 3)]
    http.answer(body={"results": [{"status_code": 200, "url": "https://a.test", "content": {"links": links}}]})

    result = call(tools_of(ir)["decodo_map"], url="https://a.test")

    assert len(result["links"]) == LIMITS.max_list_values
    assert result["links_omitted"] == 3


def test_crawl_pages_beyond_the_cap_are_counted_not_returned(ir: Ir, http: FakeHttp) -> None:
    pages = [{"url": f"https://a.test/{n}", "status": "done", "content": "x"} for n in range(52)]
    http.answer(body={"crawl_id": "c1", "status": "done"})
    http.answer(body={"crawl_id": "c1", "url": pages})

    result = call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert len(result["items"]) == LIMITS.max_items
    assert result["more_items"] is True


def test_reading_a_crawl_stops_once_enough_pages_are_in(ir: Ir, http: FakeHttp) -> None:
    pages = [{"url": f"https://a.test/{n}", "status": "done", "content": "x"} for n in range(30)]
    http.answer(body={"crawl_id": "c1", "status": "done"})
    http.answer(body={"crawl_id": "c1", "url": pages, "next": "/v1/crawl/c1?cursor=2"})
    http.answer(body={"crawl_id": "c1", "url": pages, "next": "/v1/crawl/c1?cursor=3"})

    call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert len(http.calls) == 3


def test_a_whole_crawl_fits_the_result_limit(ir: Ir, http: FakeHttp) -> None:
    pages = [{"url": f"https://a.test/{n}", "status": "done", "content": "x" * 50_000} for n in range(50)]
    http.answer(body={"crawl_id": "c1", "status": "done"})
    http.answer(body={"crawl_id": "c1", "url": pages})

    result = call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert len(result["items"]) == 50
    assert all(page["truncated"] for page in result["items"])


@pytest.mark.parametrize("shaped", [True, False])
def test_any_result_over_the_limit_is_cut_whatever_its_shape(ir: Ir, http: FakeHttp, shaped: bool) -> None:
    if not shaped:
        del tool_entry(ir, "decodo_search")["result"]
    http.answer(body={"organic": ["x" * LIMITS.max_result_chars]})

    result = call(tools_of(ir)["decodo_search"], query="q")

    assert len(result["result_json"]) == LIMITS.max_result_chars
    assert result["truncated"] is True


def test_published_limits_change_how_much_a_tool_returns(ir: Ir, http: FakeHttp) -> None:
    tool_entry(ir, "decodo_scrape")["limits"] = {"maxTextChars": 10}
    tool_entry(ir, "decodo_crawl_results")["limits"] = {"maxItems": 2}
    http.answer(body={"results": [{"status_code": 200, "content": "x" * 11}]})
    http.answer(body={"crawl_id": "c1", "status": "done"})
    http.answer(body={"crawl_id": "c1", "url": [{"url": "u", "status": "done", "content": "x"}] * 3})
    tools = tools_of(ir)

    assert len(call(tools["decodo_scrape"], url="https://a.test")["content"]) == 10
    assert len(call(tools["decodo_crawl_results"], crawl_id="c1")["items"]) == 2


def test_a_published_wait_changes_how_long_a_results_tool_waits(
    ir: Ir, http: FakeHttp, fast_clock: list[float]
) -> None:
    tool_entry(ir, "decodo_crawl_results")["limits"] = {"waitSeconds": 10}
    ir["flows"]["crawl"]["pollSeconds"] = 2
    for _ in range(20):
        http.answer(body={"crawl_id": "c1", "status": "pending"})

    call(tools_of(ir)["decodo_crawl_results"], crawl_id="c1")

    assert fast_clock == [2.0] * 5


@pytest.mark.parametrize("published", [0, -1, 1.5, "many", True])
def test_a_published_limit_this_client_cannot_read_keeps_the_built_in_one(ir: Ir, published: Any) -> None:
    tool_entry(ir, "decodo_scrape")["limits"] = {"maxTextChars": published, "maxItems": 7}

    assert spec_of(ir, "decodo_scrape").limits == Limits(max_items=7)


def test_limits_that_are_not_an_object_keep_the_tool_with_built_in_limits(ir: Ir) -> None:
    tool_entry(ir, "decodo_scrape")["limits"] = [1, 2]

    assert spec_of(ir, "decodo_scrape").limits == Limits()


def test_a_published_limit_is_held_under_the_ceiling(ir: Ir) -> None:
    tool_entry(ir, "decodo_scrape")["limits"] = {"maxResultChars": 10**9, "waitSeconds": 10**6}

    limits = spec_of(ir, "decodo_scrape").limits

    assert (limits.max_result_chars, limits.wait_seconds) == (CEILINGS.max_result_chars, CEILINGS.wait_seconds)


def test_a_surface_without_limits_uses_the_built_in_ones(ir: Ir) -> None:
    del tool_entry(ir, "decodo_scrape")["limits"]

    assert spec_of(ir, "decodo_scrape").limits == Limits()


@pytest.mark.parametrize("limit", [0, -5])
def test_published_floor_raises_a_value_the_api_would_read_as_no_limit(ir: Ir, http: FakeHttp, limit: int) -> None:
    tool = tools_of(ir)["decodo_map"]
    http.answer(body={"results": [{"status_code": 200, "url": "https://a.test", "content": {"links": []}}]})

    call(tool, url="https://a.test", limit=limit)

    assert parameters(tool)["properties"]["limit"]["minimum"] == 1
    assert http.calls[0]["json"]["limit"] == 1


def test_a_changed_result_rule_changes_what_the_tool_returns(ir: Ir, http: FakeHttp) -> None:
    tool_entry(ir, "decodo_scrape")["result"]["fields"] = {"page": "url", "markdown": "content", "at": "updated_at"}
    tool_entry(ir, "decodo_scrape")["result"]["required"] = ["markdown"]
    reply = {"status_code": 200, "url": "https://a.test", "content": "# T", "updated_at": "2026-01-02 03:04:05"}
    http.answer(body={"results": [reply]})

    result = call(tools_of(ir)["decodo_scrape"], url="https://a.test")

    assert result == {"page": "https://a.test", "markdown": "# T", "at": "2026-01-02 03:04:05"}


def test_a_new_failure_rule_turns_a_reply_into_an_error(ir: Ir, http: FakeHttp) -> None:
    tool_entry(ir, "decodo_map")["result"]["failures"] = [
        {"path": "results.0.warning", "equals": "robots_disallowed", "message": "results.0.warning"}
    ]
    http.answer(body={"results": [{"status_code": 200, "url": "https://a.test", "warning": "robots_disallowed"}]})

    result = call(tools_of(ir)["decodo_map"], url="https://a.test")

    assert (result["code"], result["error"]) == ("robots_disallowed", "robots_disallowed")


def test_a_tool_without_a_result_rule_returns_the_response(ir: Ir, http: FakeHttp) -> None:
    del tool_entry(ir, "decodo_map")["result"]
    reply = {"results": [{"status_code": 200, "url": "https://a.test", "content": {"links": []}}]}
    http.answer(body=reply)

    assert call(tools_of(ir)["decodo_map"], url="https://a.test") == reply


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"results": [{"status_code": 613, "content": "<html>blocked</html>"}]}, "613"),
        ({"results": []}, "empty_response"),
        ({"status": "failed", "message": "target unreachable", "status_code": 612}, "612"),
        ({"results": "nope"}, "empty_response"),
        (["not", "an", "object"], "empty_response"),
        ({"results": ["text"]}, "empty_content"),
        ({"results": [{"status_code": 200, "content": ""}]}, "empty_content"),
    ],
)
def test_failed_scrape_inside_a_2xx_is_an_error(ir: Ir, http: FakeHttp, body: Any, code: str) -> None:
    http.answer(body=body)

    result = call(tools_of(ir)["decodo_scrape"], url="https://a.test")

    assert result["code"] == code
    assert "content" not in result


def test_failed_search_inside_a_2xx_is_an_error(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"status": "failed", "message": "no luck"})

    result = call(tools_of(ir)["decodo_search"], query="q")

    assert result["code"] == "failed"
    assert "no luck" in result["error"]


@pytest.mark.parametrize(
    ("status", "body", "code", "retryable"),
    [
        (401, {"message": "bad key"}, "authentication_failed", False),
        (403, {"message": "forbidden"}, "authentication_failed", False),
        (429, {"message": "slow down"}, "rate_limited", True),
        (422, {"message": "bad geo"}, "invalid_input", False),
        (400, {"message": "bad body"}, "invalid_input", False),
        (500, {"message": "boom"}, "500", True),
        (404, {"message": "no such crawl"}, "404", False),
    ],
)
def test_api_errors_become_results_the_model_can_read(
    ir: Ir, http: FakeHttp, status: int, body: Any, code: str, retryable: bool
) -> None:
    http.answer(status, body)

    result = call(tools_of(ir)["decodo_search"], query="q")

    assert (result["code"], result["retryable"]) == (code, retryable)
    assert API_KEY not in result["error"]


def test_authentication_error_names_the_env_var(ir: Ir, http: FakeHttp) -> None:
    http.answer(401, {"message": "bad key"})

    assert "DECODO_API_KEY" in call(tools_of(ir)["decodo_search"], query="q")["error"]


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (httpx.ReadTimeout("slow"), "timeout", True),
        (httpx.ConnectError("refused"), "network_error", True),
        (httpx.LocalProtocolError(f"Illegal header value b'Bearer {API_KEY}'"), "invalid_request", False),
    ],
)
def test_transport_errors_become_results(ir: Ir, http: FakeHttp, error: Exception, code: str, retryable: bool) -> None:
    http.fail(error)

    result = call(tools_of(ir)["decodo_search"], query="q")

    assert (result["code"], result["retryable"]) == (code, retryable)
    assert API_KEY not in result["error"]


@pytest.mark.parametrize(
    ("tool", "args", "status", "body"),
    [
        ("decodo_crawl_results", {"crawl_id": "c1"}, 200, ["not", "an", "object"]),
        ("decodo_search", {"query": "q"}, 502, ["bad", "gateway"]),
    ],
)
def test_a_reply_in_an_unexpected_shape_is_a_result(
    ir: Ir, http: FakeHttp, tool: str, args: dict[str, Any], status: int, body: Any
) -> None:
    http.answer(status, body)

    assert call(tools_of(ir)[tool], **args)["code"] == "unexpected_error"


def test_a_reply_that_is_not_json_is_a_result(ir: Ir, http: FakeHttp) -> None:
    http.answers.append(httpx.Response(200, text="<html>gateway</html>"))

    assert call(tools_of(ir)["decodo_search"], query="q")["code"] == "unexpected_error"


def test_an_unexpected_failure_is_logged_without_its_message(
    ir: Ir, http: FakeHttp, caplog: pytest.LogCaptureFixture
) -> None:
    http.fail(RuntimeError(f"bug with {API_KEY}"))

    with caplog.at_level(logging.ERROR):
        result = call(tools_of(ir)["decodo_search"], query="q")

    assert result["code"] == "unexpected_error"
    assert API_KEY not in result["error"]
    assert "decodo_search failed unexpectedly with RuntimeError" in caplog.text
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("answer", [httpx.ReadTimeout("slow"), httpx.ConnectError("reset"), 500])
def test_an_unanswered_crawl_start_is_never_retryable(ir: Ir, http: FakeHttp, answer: Any) -> None:
    if isinstance(answer, int):
        http.answer(answer, {"message": "boom"})
    else:
        http.fail(answer)

    result = call(tools_of(ir)["decodo_crawl_start"], url="https://a.test")

    assert result["retryable"] is False
    assert "do not send it again" in result["error"]


def test_a_crawl_start_answered_without_an_id_is_not_sent_again(ir: Ir, http: FakeHttp) -> None:
    http.answer(body={"status": "pending"})

    result = call(tools_of(ir)["decodo_crawl_start"], url="https://a.test")

    assert (result["code"], result["retryable"]) == ("unexpected_error", False)
    assert "do not send it again" in result["error"]


def test_a_refused_batch_may_have_accepted_some_of_it(ir: Ir, http: FakeHttp) -> None:
    ir["surfaces"][CLIENT]["tools"].append(
        {"name": "scrape_many", "description": "Queue many scrapes.", "flow": "task_batch", "fields": ["url"]}
    )
    http.answer(400, {"message": "some items were refused", "errors": [{"index": 1}]})

    result = call(tools_of(ir)["scrape_many"], url=["https://a.test/1", "not a url"])

    assert (result["code"], result["retryable"]) == ("invalid_input", False)
    assert "may have been accepted" in result["error"]


def test_a_refused_crawl_start_stays_retryable(ir: Ir, http: FakeHttp) -> None:
    http.answer(429, {"message": "slow down"})

    assert call(tools_of(ir)["decodo_crawl_start"], url="https://a.test")["retryable"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"flow": "transcribe"},
        {"step": "pause"},
        {"step": "start"},
        {"fields": "query"},
        {"maximums": {"page": "5"}},
        {"fixed": ["x"]},
        {"result": {"failures": [{"path": "status"}]}},
        {"result": "markdown"},
        {"name": None},
        {"target": "no_such_target"},
    ],
)
def test_a_published_tool_this_code_cannot_use_costs_only_that_tool(
    ir: Ir, change: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    tool_entry(ir, "decodo_search").update(change)

    with caplog.at_level(logging.WARNING):
        tools = tools_of(ir)

    assert list(tools) == TOOL_NAMES[1:]
    assert "Leaving out" in caplog.text


def test_a_field_the_api_no_longer_has_is_left_out(ir: Ir, caplog: pytest.LogCaptureFixture) -> None:
    del ir["operations"]["mapSite"]["requestSchema"]["properties"]["sitemap"]

    with caplog.at_level(logging.WARNING):
        declared = parameters(tools_of(ir)["decodo_map"])

    assert "sitemap" not in declared["properties"]
    assert "sitemap" in caplog.text


@pytest.mark.parametrize("surface", [None, "x", {}, {"tools": "x"}, ["x"], {"tools": [{"name": "t"}, "x", 3]}])
def test_the_schema_bundled_with_the_sdk_is_used_when_the_published_one_gives_no_tools(
    ir: Ir, surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = {**ir, "surfaces": {CLIENT: surface}}
    monkeypatch.setattr("decodo_adk.toolset.load_schema", lambda: schema_of(published))
    monkeypatch.setattr(BundledSchema, "shared", schema_of(ir))

    toolset = DecodoToolset(api_key=API_KEY)

    assert [tool.name for tool in asyncio.run(toolset.get_tools())] == TOOL_NAMES


def test_no_usable_schema_anywhere_is_a_clear_error(ir: Ir, monkeypatch: pytest.MonkeyPatch) -> None:
    without_flows = {key: value for key, value in ir.items() if key != "flows"}
    monkeypatch.setattr("decodo_adk.toolset.load_schema", lambda: schema_of(without_flows))
    monkeypatch.setattr(BundledSchema, "shared", schema_of(without_flows))

    with pytest.raises(RuntimeError, match="decodo-sdk 3.1.0 or newer"):
        asyncio.run(DecodoToolset(api_key=API_KEY).get_tools())


def test_bundled_schema_is_used_when_the_published_one_cannot_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(options: Any = None) -> Any:
        raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(RemoteSchema, "load", unreachable)

    assert load_schema() is BundledSchema.shared


def test_own_specs_replace_the_published_tools(ir: Ir, http: FakeHttp) -> None:
    spec = ToolSpec(name="find", description="Search.", flow="search", fields=("query",))
    http.answer(body={"organic": []})

    tools = tools_of(ir, (spec,))

    assert list(tools) == ["find"]
    assert call(tools["find"], query="q") == {"organic": []}


def test_an_empty_list_of_specs_means_no_tools(ir: Ir, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("decodo_adk.toolset.load_schema", lambda: schema_of(ir))

    assert tools_of(ir, ()) == {}
    assert asyncio.run(DecodoToolset(api_key=API_KEY, specs=()).get_tools()) == []


def test_the_agent_prompt_names_no_tool_so_a_published_rename_cannot_break_it() -> None:
    from research_agent.agent import INSTRUCTION

    assert "decodo_" not in INSTRUCTION


@pytest.mark.parametrize("key", ["", "   ", "line\nbreak", "tab\tkey", "ключ"])
def test_unusable_key_is_refused_without_quoting_it(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECODO_API_KEY", raising=False)

    with pytest.raises(ValueError) as raised:
        DecodoToolset(api_key=key)

    assert "DECODO_API_KEY" in str(raised.value)
    assert all(part not in str(raised.value) for part in key.split())


def test_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch, ir: Ir) -> None:
    monkeypatch.setenv("DECODO_API_KEY", " from-env ")

    assert DecodoToolset(schema=schema_of(ir))._api_key == "from-env"
