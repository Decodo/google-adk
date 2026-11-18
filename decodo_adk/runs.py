"""Runs a tool's flow through the SDK and fits the result to a model's context.

Nothing here knows a product. How a flow is run (one call, a job that is
started, waited for and read, or a batch), how its response is read and how
much of it is returned all come from the published schema.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from decodo import JobTimeoutError, ResultError, WebScrapingApi, read_result

from .errors import failure
from .tool_specs import Limits, ToolSpec

Params = dict[str, Any]
Result = dict[str, Any]


def _trimmed(value: Any, text_chars: int, list_values: int) -> Any:
    """One result object with each long text cut and each long list shortened, and a note of what was cut."""
    if not isinstance(value, dict):
        return value
    trimmed: Result = {}
    for name, field in value.items():
        if isinstance(field, str) and len(field) > text_chars:
            trimmed[name] = field[:text_chars]
            trimmed["truncated"] = True
        elif isinstance(field, list) and len(field) > list_values:
            trimmed[name] = field[:list_values]
            trimmed[f"{name}_omitted"] = len(field) - list_values
        else:
            trimmed[name] = field
    return trimmed


def _presented(result: Any, limits: Limits) -> Result:
    """A result read from a response, in the form and size a model gets."""
    if isinstance(result, list):
        shown = result[: limits.max_items]
        text_chars = limits.max_text_chars // max(len(shown), 1)
        presented: Result = {"items": [_trimmed(item, text_chars, limits.max_list_values) for item in shown]}
        if len(result) > limits.max_items:
            presented["more_items"] = True
        return presented
    if isinstance(result, dict):
        return cast(Result, _trimmed(result, limits.max_text_chars, limits.max_list_values))
    return {"result": result}


def _bounded(result: Result, limits: Limits) -> Result:
    """Any result, whatever is in it, cut to what a model's context can take."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= limits.max_result_chars:
        return result
    return {"result_json": text[: limits.max_result_chars], "truncated": True}


def _read(spec: ToolSpec, response: Any) -> Result:
    try:
        return _presented(read_result(spec.result, response), spec.limits)
    except ResultError as error:
        return failure(str(error), error.code, retryable=error.retryable)


def starts_work(spec: ToolSpec, flow: Mapping[str, Any]) -> bool:
    """Whether a call starts billable work that a repeat would start a second time."""
    return flow["pattern"] == "batch" or (flow["pattern"] == "job" and spec.step == "start")


def run(api: WebScrapingApi, spec: ToolSpec, params: Params) -> Result:
    return _bounded(_run(api, spec, params), spec.limits)


def _run(api: WebScrapingApi, spec: ToolSpec, params: Params) -> Result:
    flow = api.flows.describe(spec.flow)
    pattern = flow["pattern"]

    if pattern == "call":
        return _read(spec, api.flows.call(spec.flow, params))

    if pattern == "batch":
        job = api.flows.describe(flow["job"])
        jobs = api.flows.start_batch(spec.flow, params)
        return {"jobs": [{key: item.get(key) for key in (job["idField"], job["statusField"])} for item in jobs]}

    id_field, status_field = flow["idField"], flow["statusField"]

    if spec.step == "start":
        started = api.flows.start(spec.flow, params)
        return {
            id_field: started[id_field],
            status_field: started.get(status_field),
            "next": f"Fetch the results with this {id_field} right away; the results tool waits for it.",
        }

    job_id = params[id_field]

    if spec.step == "cancel":
        cancelled = api.flows.cancel(spec.flow, job_id)
        return {id_field: job_id, status_field: cancelled.get(status_field)}

    try:
        status = api.flows.wait(spec.flow, job_id, timeout=spec.limits.wait_seconds)
    except JobTimeoutError:
        return {
            id_field: job_id,
            status_field: flow["pendingStatuses"][0],
            "next": f"Still running. Call this tool again right away with the same {id_field}.",
        }

    result: Result = {id_field: job_id, status_field: status.get(status_field)}
    if status.get("reason"):
        result["reason"] = status["reason"]
    return {**result, **_read(spec, api.flows.results(spec.flow, job_id, max_items=spec.limits.max_items + 1))}
