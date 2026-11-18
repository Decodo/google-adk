from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Mapping, Sequence
from typing import Any

from decodo import BundledSchema, DecodoSchema, FlowSchema, WebScrapingApi
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from . import runs
from .client import INTEGRATION_HEADER, build_api, load_schema, resolve_api_key
from .errors import failure, failure_from, maybe_sent
from .tool_specs import ToolSpec, specs_from_surface

JsonSchema = dict[str, Any]

logger = logging.getLogger(__name__)


def _api_schema(spec: ToolSpec, flow: Mapping[str, Any], schema: FlowSchema) -> JsonSchema:
    if spec.target:
        found = schema.get_target_parameter_schema(spec.target)  # type: ignore[attr-defined]
        source = f"target {spec.target!r}"
    else:
        name = flow["run"] if flow["pattern"] == "call" else flow["start"]
        found = (schema.get_operation(name) or {}).get("requestSchema")
        source = f"operation {name!r}"
    if not found or "properties" not in found:
        raise ValueError(f"the Decodo schema lists no fields for {source}")
    return dict(found)


def build_parameters(spec: ToolSpec, flow: Mapping[str, Any], schema: FlowSchema) -> JsonSchema:
    if flow["pattern"] == "job" and spec.step in ("results", "cancel"):
        id_field = flow["idField"]
        text = spec.descriptions.get(id_field, f"The {id_field} returned when the {spec.flow} was started.")
        return {
            "type": "object",
            "properties": {id_field: {"type": "string", "description": text}},
            "required": [id_field],
        }
    if (flow["pattern"] == "job") != (spec.step == "start"):
        raise ValueError(f"step {spec.step!r} does not fit a {flow['pattern']} flow")

    api = _api_schema(spec, flow, schema)
    api_properties: dict[str, JsonSchema] = api["properties"]
    api_required: list[str] = api.get("required") or []

    uncovered = [name for name in api_required if name not in spec.fields and name not in spec.fixed]
    if uncovered:
        raise ValueError(f"the required API fields {uncovered} are neither shown nor fixed")

    properties: dict[str, JsonSchema] = {}
    for name in spec.fields:
        if name not in api_properties:
            logger.warning("%s: the Decodo schema has no field %r; leaving it out.", spec.name, name)
            continue
        shown = dict(api_properties[name])
        if name in spec.descriptions:
            shown["description"] = spec.descriptions[name]
        if name in spec.defaults:
            shown["default"] = spec.defaults[name]
        if name in spec.maximums:
            shown["maximum"] = min(spec.maximums[name], shown.get("maximum", spec.maximums[name]))
        if name in spec.minimums:
            shown["minimum"] = max(spec.minimums[name], shown.get("minimum", spec.minimums[name]))
        properties[name] = shown

    required = [name for name in properties if name in api_required or name in spec.required]
    return {"type": "object", "properties": properties, "required": required}


class DecodoTool(BaseTool):
    def __init__(self, spec: ToolSpec, parameters: JsonSchema, api: WebScrapingApi, *, starts_work: bool) -> None:
        super().__init__(name=spec.name, description=spec.description)
        self._spec = spec
        self._parameters = parameters
        self._api = api
        self._starts_work = starts_work

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self._parameters,
        )

    def request_params(self, args: dict[str, Any]) -> dict[str, Any]:
        properties: dict[str, JsonSchema] = self._parameters["properties"]
        params = {name: value for name, value in self._spec.defaults.items() if name in properties}
        params.update({name: value for name, value in args.items() if name in properties and value is not None})
        for name, value in params.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = min(value, self._spec.maximums.get(name, value))
                params[name] = max(value, self._spec.minimums.get(name, value))
        return {**params, **self._spec.fixed}

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        try:
            params = self.request_params(args)
            missing = [name for name in self._parameters["required"] if name not in params]
            if missing:
                return failure(f"{self.name} needs {', '.join(missing)}.", "invalid_input")
            return await asyncio.to_thread(runs.run, self._api, self._spec, params)
        except Exception as error:
            result = failure_from(error)
            if result["code"] == "unexpected_error":
                frames = "".join(traceback.format_tb(error.__traceback__))
                logger.error("%s failed unexpectedly with %s\n%s", self.name, type(error).__name__, frames)
            if self._starts_work and maybe_sent(result, partly=self._spec.step is None):
                result["retryable"] = False
                result["error"] += " The request may have been accepted, so do not send it again unchanged."
            return result


def build_tools(specs: Sequence[ToolSpec], schema: DecodoSchema, api: WebScrapingApi) -> list[BaseTool]:
    if not isinstance(schema, FlowSchema):
        return []
    tools: list[BaseTool] = []
    for spec in specs:
        try:
            flow = api.flows.describe(spec.flow)
            parameters = build_parameters(spec, flow, schema)
            tools.append(DecodoTool(spec, parameters, api, starts_work=runs.starts_work(spec, flow)))
        except Exception as error:
            logger.warning("Leaving out the tool %r: %s.", spec.name, error)
    return tools


class DecodoToolset(BaseToolset):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        specs: Sequence[ToolSpec] | None = None,
        schema: DecodoSchema | None = None,
    ) -> None:
        super().__init__()
        self._api_key = resolve_api_key(api_key)
        self._specs = specs
        self._schema = schema
        self._tools: list[BaseTool] | None = None

    def _tools_from(self, schema: DecodoSchema) -> list[BaseTool]:
        try:
            specs = self._specs
            if specs is None:
                get_surface = getattr(schema, "get_surface", None)
                specs = specs_from_surface(get_surface(INTEGRATION_HEADER) if callable(get_surface) else None)
            return build_tools(specs, schema, build_api(self._api_key, schema))
        except Exception as error:
            logger.warning("Could not build tools from the Decodo schema: %s: %s.", type(error).__name__, error)
            return []

    def _build_tools(self) -> list[BaseTool]:
        if self._schema is not None:
            return self._tools_from(self._schema)
        tools = self._tools_from(load_schema())
        if self._specs is not None and not self._specs:
            return tools
        if not tools:
            logger.warning("The published Decodo schema gave no usable tools; using the one bundled with decodo-sdk.")
            tools = self._tools_from(BundledSchema.shared)
        if not tools:
            raise RuntimeError(
                "No Decodo tools could be built. The Decodo schema must describe flows and a "
                f"{INTEGRATION_HEADER} surface, which needs decodo-sdk 2.6.0 or newer."
            )
        return tools

    async def get_tools(self, readonly_context: ReadonlyContext | None = None) -> list[BaseTool]:
        if self._tools is None:
            self._tools = await asyncio.to_thread(self._build_tools)
        return self._tools
