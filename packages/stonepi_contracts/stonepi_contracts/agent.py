"""Agent tool contract (ideation/platform-plumbing.md P4/P5; muse.md §7).

The agent-neutral description of what AgentLink can do. Adapters (Pebble now, Muse and
MCP later) derive their own formats from these specs. Params are **flat scalars only**
(string, integer, number, boolean): that maps onto MCP JSON Schema and the Muse SDK alike.

No tool takes a user: the binding decides whose data a call sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

INVOKE_VERSION = 1
PARAM_TYPES = ("string", "integer", "number", "boolean")


class Tier(str, Enum):
    READ = "read"  # allowed when enabled for the binding
    WRITE = "write"  # low-risk change to the user's own data; always audited
    CONFIRM = "confirm"  # affects others, shared screens, public URLs or paid quotas


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # one of PARAM_TYPES
    description: str
    required: bool = True

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"param {self.name}: type must be one of {PARAM_TYPES}")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    app: str  # the StonePi app whose data it reads or changes
    tier: Tier
    description: str  # written for the model: what it returns and when to use it
    params: tuple[Param, ...] = ()
    # On for a new binding? Personal-data tools start off (muse.md §15); status-type tools on.
    default_enabled: bool = False
    # The user's app grant the tool needs ("" = none beyond the binding).
    requires_app: str = ""


@dataclass(frozen=True)
class InvokeRequest:
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    v: int = INVOKE_VERSION


def ok(payload: Any) -> dict[str, Any]:
    return {"ok": True, "payload": payload}


def fail(error: str, message: str = "") -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message or error}


def validate_params(spec: ToolSpec, params: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """Checked, coerced params for ``spec``, or (None, reason). Unknown params are refused."""
    params = dict(params or {})
    known = {p.name: p for p in spec.params}
    extra = sorted(set(params) - set(known))
    if extra:
        return None, f"unknown param {extra[0]}"
    out: dict[str, Any] = {}
    for p in spec.params:
        if p.name not in params or params[p.name] is None:
            if p.required:
                return None, f"missing param {p.name}"
            continue
        value = params[p.name]
        try:
            if p.type == "string":
                if not isinstance(value, str):
                    raise TypeError
                out[p.name] = value[:500]
            elif p.type == "integer":
                if isinstance(value, bool):
                    raise TypeError
                out[p.name] = int(value)
            elif p.type == "number":
                if isinstance(value, bool):
                    raise TypeError
                out[p.name] = float(value)
            else:
                if not isinstance(value, bool):
                    raise TypeError
                out[p.name] = value
        except (TypeError, ValueError):
            return None, f"param {p.name} must be {p.type}"
    return out, ""


def to_mcp_tools(specs: list[ToolSpec] | tuple[ToolSpec, ...]) -> list[dict[str, Any]]:
    """MCP ``tools/list`` entries (for the later MCP adapter, F4)."""
    tools = []
    for spec in specs:
        props = {p.name: {"type": p.type, "description": p.description} for p in spec.params}
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": [p.name for p in spec.params if p.required],
                    "additionalProperties": False,
                },
            }
        )
    return tools


def registry_rev(specs: list[ToolSpec] | tuple[ToolSpec, ...]) -> str:
    """Changes whenever a tool is added, removed or reshaped (adapters refresh on change)."""
    import hashlib

    text = "|".join(
        f"{s.name}:{s.tier.value}:{','.join(f'{p.name}/{p.type}/{int(p.required)}' for p in s.params)}"
        for s in sorted(specs, key=lambda s: s.name)
    )
    return hashlib.sha256(text.encode()).hexdigest()[:12]
