import pytest
from stonepi_contracts.agent import Param, Tier, ToolSpec, registry_rev, to_mcp_tools, validate_params

SPEC = ToolSpec(
    "get_events", "eventtrakr", Tier.READ, "Upcoming events.",
    (Param("days", "integer", "How many days ahead", required=False), Param("query", "string", "Filter")),
)


def test_validate_coerces_and_refuses():
    assert validate_params(SPEC, {"query": "jazz", "days": "3"}) == ({"query": "jazz", "days": 3}, "")
    assert validate_params(SPEC, {"days": 3})[1] == "missing param query"
    assert validate_params(SPEC, {"query": "x", "user": "eva"})[1] == "unknown param user"
    assert validate_params(SPEC, {"query": 5})[1] == "param query must be string"
    assert validate_params(SPEC, {"query": "x", "days": True})[1] == "param days must be integer"


def test_param_types_are_flat_scalars():
    with pytest.raises(ValueError):
        Param("x", "object", "nope")


def test_mcp_shape_and_rev():
    tool = to_mcp_tools([SPEC])[0]
    assert tool["inputSchema"]["required"] == ["query"]
    assert tool["inputSchema"]["properties"]["days"]["type"] == "integer"
    other = ToolSpec("get_events", "eventtrakr", Tier.READ, "Upcoming events.", (Param("days", "integer", "d"),))
    assert registry_rev([SPEC]) != registry_rev([other])
