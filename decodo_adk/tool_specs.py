"""Reads the tools Decodo publishes for this client.

Which tools exist, what they are called, the text a model reads, the fields they
show and the values they fix are not in this repository. Decodo publishes them
with its schema as the ``google-adk-python`` surface, and each tool names the
flow it runs, how its response is read and how much of it a model gets. Field
types, enums and limits come from the API description.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from decodo import check_result_rule

logger = logging.getLogger(__name__)

STEPS = frozenset({"start", "results", "cancel"})


@dataclass(frozen=True)
class Limits:
    """How much one tool call puts into a model's context, and how long it waits for a job.

    The values here are used when the surface publishes none, or one this client cannot read.
    """

    max_result_chars: int = 60_000
    max_text_chars: int = 40_000
    max_items: int = 50
    max_list_values: int = 500
    wait_seconds: int = 45


# The most a published limit can ask for, so a wrong value cannot flood a model's context.
CEILINGS = Limits(
    max_result_chars=200_000, max_text_chars=150_000, max_items=200, max_list_values=2_000, wait_seconds=120
)

PUBLISHED_LIMITS = {
    "maxResultChars": "max_result_chars",
    "maxTextChars": "max_text_chars",
    "maxItems": "max_items",
    "maxListValues": "max_list_values",
    "waitSeconds": "wait_seconds",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    flow: str
    """The flow the tool runs: one call, a job, or a batch of jobs."""
    step: str | None = None
    """Which part of a job flow the tool is: start, results or cancel."""
    result: Mapping[str, Any] | None = None
    """The published rule for reading the response; without one it is returned as it is."""
    limits: Limits = Limits()
    """How much of the result a model gets, and how long a results tool waits for its job."""
    target: str | None = None
    """Scrape target whose parameters are the tool's fields, when the flow's endpoint lists none."""
    fields: tuple[str, ...] = ()
    """API fields shown to the model, in order."""
    required: tuple[str, ...] = ()
    """Shown fields the model must give, on top of those the API requires."""
    fixed: Mapping[str, Any] = field(default_factory=dict)
    """API fields always sent with this value and never shown."""
    defaults: Mapping[str, Any] = field(default_factory=dict)
    """Values used when the model leaves a shown field out; they replace the API default."""
    maximums: Mapping[str, float] = field(default_factory=dict)
    """Caps on shown numeric fields, below the API's own; larger values are lowered to the cap."""
    minimums: Mapping[str, float] = field(default_factory=dict)
    """Floors on shown numeric fields; smaller values are raised to the floor."""
    descriptions: Mapping[str, str] = field(default_factory=dict)
    """Text a model reads for an input, in place of the schema's."""


def _text(entry: Mapping[str, Any], key: str, *, optional: bool = False) -> Any:
    value = entry.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} is not text")
    return value


def _mapping(entry: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = entry.get(key) or {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} is not an object")
    return value


def _result_rule(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    rule = entry.get("result")
    if rule is not None:
        check_result_rule(rule)
    return cast(Mapping[str, Any] | None, rule)


def _limits(entry: Mapping[str, Any]) -> Limits:
    """The published limits, each held under its ceiling. One this client cannot read keeps its built-in value."""
    published = entry.get("limits") or {}
    if not isinstance(published, Mapping):
        logger.warning("Ignoring the limits of the published tool %r: not an object.", entry.get("name"))
        return Limits()
    limits: dict[str, int] = {}
    for key, name in PUBLISHED_LIMITS.items():
        value = published.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            logger.warning("Ignoring the limit %s of the published tool %r: %r.", key, entry.get("name"), value)
            continue
        limits[name] = min(value, getattr(CEILINGS, name))
    return Limits(**limits)


def _numbers(entry: Mapping[str, Any], key: str) -> Mapping[str, float]:
    value = _mapping(entry, key)
    if not all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in value.values()):
        raise TypeError(f"{key} holds a value that is not a number")
    return value


def _names(entry: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = entry.get(key) or ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(name, str) for name in value):
        raise TypeError(f"{key} is not a list of names")
    return tuple(value)


def _spec_from(entry: Any) -> ToolSpec:
    if not isinstance(entry, Mapping):
        raise TypeError("the tool is not an object")
    step = _text(entry, "step", optional=True)
    if step is not None and step not in STEPS:
        raise TypeError(f"step {step!r} is not one this client knows")
    maximums, minimums = _numbers(entry, "maximums"), _numbers(entry, "minimums")
    return ToolSpec(
        name=_text(entry, "name"),
        description=_text(entry, "description"),
        flow=_text(entry, "flow"),
        step=step,
        result=_result_rule(entry),
        limits=_limits(entry),
        target=_text(entry, "target", optional=True),
        fields=_names(entry, "fields"),
        required=_names(entry, "required"),
        fixed=_mapping(entry, "fixed"),
        defaults=_mapping(entry, "defaults"),
        maximums=maximums,
        minimums=minimums,
        descriptions=_mapping(entry, "descriptions"),
    )


def specs_from_surface(surface: Any) -> tuple[ToolSpec, ...]:
    """The tools of a published surface. One this client cannot read is left out."""
    entries = surface.get("tools") if isinstance(surface, Mapping) else None
    specs: list[ToolSpec] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            specs.append(_spec_from(entry))
        except TypeError as error:
            name = entry.get("name") if isinstance(entry, Mapping) else None
            logger.warning("Leaving out the published tool %r: %s.", name, error)
    return tuple(specs)
