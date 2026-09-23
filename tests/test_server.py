"""Tests for tool registration, annotations, resources and prompts."""

from __future__ import annotations

import asyncio

from pinbridge_mcp.config import Settings
from pinbridge_mcp.server import create_mcp_server

READ_ONLY_TOOLS = {
    "server_info",
    "list_pinterest_accounts",
    "list_boards",
    "check_board_access",
    "list_related_terms",
    "list_pins",
    "get_pin",
    "get_pin_analytics",
    "get_account_analytics",
    "validate_pin",
    "list_activity_logs",
    "list_webhooks",
    "get_billing_status",
    "get_rate_meter",
    "list_schedules",
    "get_schedule",
}
WRITE_TOOLS = {
    "upload_asset",
    "create_pin",
    "create_pins_batch",
    "update_pin",
    "delete_pin",
    "retry_pin",
    "create_schedule",
    "cancel_schedule",
    "create_board",
    "delete_board",
    "create_webhook",
    "delete_webhook",
}
DESTRUCTIVE_TOOLS = {"delete_pin", "delete_board", "delete_webhook"}


def _tools(enable_write_tools: bool) -> dict[str, object]:
    mcp = create_mcp_server(
        Settings(pinbridge_api_key="pb_local", enable_write_tools=enable_write_tools)
    )
    tools = asyncio.run(mcp.list_tools())
    return {tool.name: tool for tool in tools}


def test_read_only_server_registers_only_read_tools() -> None:
    tools = _tools(enable_write_tools=False)
    assert set(tools) == READ_ONLY_TOOLS
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.readOnlyHint is True, name
        assert tool.annotations.destructiveHint is False, name


def test_write_server_registers_every_roadmap_tool_with_annotations() -> None:
    tools = _tools(enable_write_tools=True)
    assert set(tools) == READ_ONLY_TOOLS | WRITE_TOOLS
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, name
        assert annotations.readOnlyHint is False, name
        assert annotations.destructiveHint is (name in DESTRUCTIVE_TOOLS), name
    assert tools["create_pin"].annotations.idempotentHint is True
    assert tools["upload_asset"].annotations.idempotentHint is False


def test_create_pin_and_schedule_expose_dry_run() -> None:
    tools = _tools(enable_write_tools=True)
    assert "dry_run" in tools["create_pin"].inputSchema["properties"]
    assert "dry_run" in tools["create_schedule"].inputSchema["properties"]
    assert {"account_id", "board_id", "status", "error_code", "since", "until"} <= set(
        tools["list_pins"].inputSchema["properties"]
    )


def test_resources_and_prompt_are_registered() -> None:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    resources = asyncio.run(mcp.list_resources())
    templates = asyncio.run(mcp.list_resource_templates())
    prompts = asyncio.run(mcp.list_prompts())
    assert [str(r.uri) for r in resources] == ["pinbridge://accounts"]
    assert [t.uriTemplate for t in templates] == ["pinbridge://accounts/{account_id}/boards"]
    assert [p.name for p in prompts] == ["publish_pin"]

    rendered = asyncio.run(
        mcp.get_prompt("publish_pin", {"goal": "Announce the sale", "board_id": "b1"})
    )
    text = rendered.messages[0].content.text
    assert "Announce the sale" in text
    assert "upload_asset" in text
    assert "dry_run=true" in text
    assert "delete_pin" in text


def test_instructions_describe_the_workflow_and_error_contract() -> None:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    assert "publish_pin" in (mcp.instructions or "")
    assert "insufficient_scope" in (mcp.instructions or "")
    assert "check_board_access" in (mcp.instructions or "")
