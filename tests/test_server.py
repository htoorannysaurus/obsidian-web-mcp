"""Tests for MCP schemas and operational endpoints."""

import pytest
from mcp.server.fastmcp import Context
from mcp.server.lowlevel.server import RequestContext
from starlette.applications import Starlette
from starlette.testclient import TestClient

from obsidian_vault_mcp.health import health_routes
from obsidian_vault_mcp.server import mcp
from obsidian_vault_mcp.tools.info import vault_info


def test_tools_advertise_structured_output():
    tools = mcp._tool_manager.list_tools()
    assert tools
    assert all(tool.output_schema is not None for tool in tools)
    assert all(tool.output_schema.get("type") == "object" for tool in tools)
    assert all(tool.output_schema.get("anyOf") for tool in tools)
    assert all("ToolFailureResult" in tool.output_schema.get("$defs", {}) for tool in tools)
    read_schema = mcp._tool_manager.get_tool("vault_read").output_schema
    assert read_schema["$defs"]["VaultReadResult"]["properties"]["path"]["type"] == "string"


def test_server_advertises_package_version():
    options = mcp._mcp_server.create_initialization_options()
    assert options.server_name == "Notebook"
    assert options.server_version == "1.2.0"
    assert "Call vault_bootstrap before using any other Notebook tool" in options.instructions


def test_vault_info_exposes_version_to_models():
    result = vault_info()
    assert result["connector"] == "Notebook"
    assert result["version"] == "1.2.0"
    assert "revision-protected writes" in result["capabilities"]
    assert "session-enforced agent bootstrap guidance" in result["capabilities"]


def test_tool_schemas_include_usage_hints_and_enums():
    bootstrap_tool = mcp._tool_manager.get_tool("vault_bootstrap")
    read_schema = mcp._tool_manager.get_tool("vault_read").parameters
    insert_schema = mcp._tool_manager.get_tool("vault_insert").parameters
    recent_schema = mcp._tool_manager.get_tool("vault_recent").parameters
    assert "daily-logs" in read_schema["properties"]["path"]["description"]
    assert "REQUIRED FIRST CALL" in bootstrap_tool.description
    assert insert_schema["properties"]["position"]["enum"] == [
        "append",
        "prepend",
        "after_heading",
    ]
    assert recent_schema["properties"]["sort_by"]["enum"] == [
        "modified",
        "frontmatter_date",
    ]


def test_health_endpoint_returns_index_status():
    response = TestClient(Starlette(routes=health_routes)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.2.0"}


@pytest.mark.asyncio
async def test_top_level_failures_are_mcp_errors(vault_dir):
    result = await mcp._tool_manager.call_tool(
        "vault_read",
        {"path": "missing.md"},
        convert_result=True,
    )

    assert result.isError is True
    payload = result.structuredContent
    assert payload["error"]["type"] == "NOT_FOUND"
    assert payload["error"]["recoverable"] is True
    assert payload["error"]["data"]["path"] == "missing.md"
    assert payload["error"]["next_actions"][0]["tool"] == "vault_list"
    assert result.content[0].text.startswith('{"error":')


def session_context() -> Context:
    request_context = RequestContext(
        request_id="test-request",
        meta=None,
        session=object(),
        lifespan_context={"frontmatter_index": None, "bootstrapped": False},
    )
    return Context(request_context=request_context, fastmcp=mcp)


@pytest.mark.asyncio
async def test_bootstrap_is_required_once_per_session(vault_dir):
    ctx = session_context()

    blocked = await mcp._tool_manager.call_tool(
        "vault_info", {}, context=ctx, convert_result=True
    )
    assert blocked.isError is True
    assert blocked.structuredContent["error"]["type"] == "BOOTSTRAP_REQUIRED"
    assert blocked.structuredContent["error"]["next_actions"][0]["tool"] == "vault_bootstrap"

    bootstrap = await mcp._tool_manager.call_tool(
        "vault_bootstrap", {}, context=ctx, convert_result=True
    )
    assert isinstance(bootstrap, tuple)
    assert ctx.request_context.lifespan_context["bootstrapped"] is True

    allowed = await mcp._tool_manager.call_tool(
        "vault_info", {}, context=ctx, convert_result=True
    )
    assert isinstance(allowed, tuple)

    fresh_session = await mcp._tool_manager.call_tool(
        "vault_info", {}, context=session_context(), convert_result=True
    )
    assert fresh_session.isError is True
    assert fresh_session.structuredContent["error"]["type"] == "BOOTSTRAP_REQUIRED"
