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
    "list_activity_logs",
    "get_dashboard_summary",
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
    "update_schedule",
    "cancel_schedule",
    "retry_schedule",
    "delete_schedule",
    "create_board",
    "update_board",
    "delete_board",
    "create_webhook",
    "update_webhook",
    "delete_webhook",
}
DESTRUCTIVE_TOOLS = {"delete_pin", "delete_board", "delete_webhook", "delete_schedule"}


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


def test_open_world_hint_marks_only_tools_that_leave_the_workspace() -> None:
    """Directory reviewers reject hints that don't match behaviour (OpenAI, Anthropic)."""
    tools = _tools(enable_write_tools=True)
    # Pinterest's public keyword data is the one read outside the caller's workspace.
    for name in READ_ONLY_TOOLS:
        assert tools[name].annotations.openWorldHint is (name == "list_related_terms"), name
    # Every write publishes to or changes Pinterest, or delivers to an outside URL.
    for name in WRITE_TOOLS:
        assert tools[name].annotations.openWorldHint is True, name


def test_protected_resource_metadata_advertises_scope() -> None:
    from starlette.testclient import TestClient

    from pinbridge_mcp.http import create_app

    app = create_app(Settings(pinbridge_api_key="pb_local", enable_quota=False))
    with TestClient(app) as client:
        body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["scopes_supported"] == ["mcp"]
    assert body["authorization_servers"] == ["https://api.pinbridge.io"]


def test_write_server_registers_every_roadmap_tool_with_annotations() -> None:
    tools = _tools(enable_write_tools=True)
    assert set(tools) == READ_ONLY_TOOLS | WRITE_TOOLS
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, name
        assert annotations.readOnlyHint is False, name
        assert annotations.destructiveHint is (name in DESTRUCTIVE_TOOLS), name
    # Creates mint an idempotency key when none is supplied, so a repeat call publishes
    # again: they must not advertise idempotency.
    for name in ("create_pin", "create_pins_batch", "create_schedule", "upload_asset"):
        assert tools[name].annotations.idempotentHint is False, name
    assert tools["update_pin"].annotations.idempotentHint is True
    for name in ("retry_schedule", "update_webhook", "update_schedule", "update_board"):
        assert tools[name].annotations.idempotentHint is True, name


def test_every_tool_has_a_title_for_the_directory_listing() -> None:
    """Claude's connectors directory flags any tool without annotations.title."""
    tools = _tools(enable_write_tools=True)
    titles = []
    for name, tool in tools.items():
        assert tool.annotations is not None and tool.annotations.title, name
        assert tool.title == tool.annotations.title, name
        titles.append(tool.annotations.title)
    assert len(titles) == len(set(titles))


def test_every_parameter_has_a_schema_description() -> None:
    """Glama's TDQS scores schema-level parameter descriptions; keep coverage at 100%."""
    tools = _tools(enable_write_tools=True)
    missing = [
        f"{name}.{param}"
        for name, tool in tools.items()
        for param, spec in tool.inputSchema.get("properties", {}).items()
        if not spec.get("description")
    ]
    assert missing == []


def test_descriptions_name_a_sibling_and_a_failure_mode() -> None:
    """Each description says when to use another tool and what a failure looks like."""
    tools = _tools(enable_write_tools=True)
    problems: list[str] = []
    for name, tool in tools.items():
        text = tool.description or ""
        if "Use " not in text:
            problems.append(f"{name}: no usage guidance")
        if not any(other != name and other in text for other in tools):
            problems.append(f"{name}: names no sibling tool")
        if not any(word in text for word in ("Fails", "fails", "Never fails", "Never raises")):
            problems.append(f"{name}: says nothing about failure")
    assert problems == []


def test_update_webhook_fields_are_all_optional() -> None:
    tools = _tools(enable_write_tools=True)
    schema = tools["update_webhook"].inputSchema
    assert schema["required"] == ["webhook_id"]
    assert {"url", "secret", "events", "is_enabled"} <= set(schema["properties"])


def test_instructions_state_account_linking_is_dashboard_only() -> None:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    assert "dashboard-only" in (mcp.instructions or "")


def test_create_pin_and_schedule_expose_dry_run() -> None:
    tools = _tools(enable_write_tools=True)
    assert "dry_run" in tools["create_pin"].inputSchema["properties"]
    assert "dry_run" in tools["create_schedule"].inputSchema["properties"]
    assert "idempotency_key" not in tools["create_schedule"].inputSchema["properties"]
    batch_items = tools["create_pins_batch"].inputSchema["properties"]["pins"]["items"]
    assert "$ref" in batch_items or "properties" in batch_items
    assert tools["upload_asset"].inputSchema["properties"]["asset_type"]["enum"] == [
        "image",
        "video",
    ]
    assert {"account_id", "board_id", "status", "error_code", "since", "until"} <= set(
        tools["list_pins"].inputSchema["properties"]
    )


def test_lists_take_search_and_a_closed_set_of_sorts() -> None:
    tools = _tools(enable_write_tools=False)
    pins = tools["list_pins"].inputSchema["properties"]
    schedules = tools["list_schedules"].inputSchema["properties"]
    assert "q" in pins and "q" in schedules
    assert pins["sort"]["default"] == "created_at_desc"
    assert "published_at_desc" in pins["sort"]["enum"]
    assert schedules["sort"]["default"] == "run_at_desc"
    assert "run_at_asc" in schedules["sort"]["enum"]
    assert "run_at_asc" not in pins["sort"]["enum"]


def test_dashboard_summary_tool_is_read_only_with_optional_range() -> None:
    tools = _tools(enable_write_tools=False)
    tool = tools["get_dashboard_summary"]
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    schema = tool.inputSchema
    assert set(schema["properties"]) == {"start", "end", "tz", "account_id"}
    assert schema.get("required", []) == []
    assert schema["properties"]["tz"]["default"] == "UTC"


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
    assert "resolved.idempotency_key" in text
    assert "deferred" in text
    assert "delete_pin" in text
    assert all(argument.description for argument in prompts[0].arguments or [])


def test_instructions_describe_the_workflow_and_error_contract() -> None:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    assert "publish_pin" in (mcp.instructions or "")
    assert "insufficient_scope" in (mcp.instructions or "")
    assert "check_board_access" in (mcp.instructions or "")
    assert "get_dashboard_summary" in (mcp.instructions or "")


def test_list_tools_publish_a_described_page_schema() -> None:
    """Agents read the page shape (and how to page) from the outputSchema."""
    tools = _tools(enable_write_tools=False)
    for name in ("list_pins", "list_schedules"):
        schema = tools[name].outputSchema
        assert schema is not None, name
        properties = schema["properties"]
        assert set(properties) == {"items", "total", "limit", "offset", "has_more"}, name
        assert set(schema["required"]) == set(properties), name
        assert all(spec.get("description") for spec in properties.values()), name
        assert "offset + limit" in properties["has_more"]["description"], name
        assert "limit=1" in properties["total"]["description"], name


def test_list_pins_returns_the_page_as_structured_content(monkeypatch) -> None:
    page = {"items": [{"id": "pin-1"}], "total": 41, "limit": 20, "offset": 20, "has_more": True}

    async def fake_list_pins(self, **kwargs):
        assert kwargs["offset"] == 20
        return page

    monkeypatch.setattr("pinbridge_mcp.service.PinBridgeService.list_pins", fake_list_pins)
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    content, structured = asyncio.run(mcp.call_tool("list_pins", {"offset": 20}))
    assert structured == page
    assert '"total": 41' in content[0].text


def test_instructions_explain_paging_and_counting() -> None:
    instructions = create_mcp_server(Settings(pinbridge_api_key="pb_local")).instructions or ""
    assert "LISTS AND PAGING" in instructions
    assert "offset = offset + limit" in instructions
    assert "limit=1" in instructions
