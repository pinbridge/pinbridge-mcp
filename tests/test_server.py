"""Tests for tool registration, annotations, resources and prompts."""

from __future__ import annotations

import asyncio
from typing import Any

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
    "delete_asset",
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
# Deletes, edits that overwrite existing values, and the cancel that cannot be undone.
# Only creates and retries are additive.
DESTRUCTIVE_TOOLS = {
    "delete_asset",
    "delete_pin",
    "delete_board",
    "delete_webhook",
    "delete_schedule",
    "update_pin",
    "update_schedule",
    "update_board",
    "update_webhook",
    "cancel_schedule",
}
# Writes that only change PinBridge's own records, never Pinterest or an outside URL.
CLOSED_WORLD_WRITE_TOOLS = {"delete_asset", "cancel_schedule", "delete_schedule", "delete_webhook"}


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
    # Every other write publishes to or changes Pinterest, or delivers to an outside URL.
    for name in WRITE_TOOLS:
        assert tools[name].annotations.openWorldHint is (name not in CLOSED_WORLD_WRITE_TOOLS), name


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
    for name in (
        "retry_schedule",
        "update_webhook",
        "update_schedule",
        "update_board",
        "delete_asset",
    ):
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
    assert "rate_limited" in (mcp.instructions or "")
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


def test_upload_asset_declares_a_chatgpt_file_param() -> None:
    """ChatGPT hands chat attachments and generated images only to declared file params."""
    tools = _tools(enable_write_tools=True)
    upload = tools["upload_asset"]
    assert upload.meta == {"openai/fileParams": ["file"]}
    schema = upload.inputSchema
    file_schema = schema["properties"]["file"]
    assert set(file_schema) <= {"$ref", "description", "title"}
    assert "file" not in schema["required"]
    file_object = schema["$defs"][file_schema["$ref"].rsplit("/", 1)[1]]
    # OpenAI's tool scan rejects a file schema that misses one of the four properties,
    # does not require download_url and file_id, or requires anything else.
    assert set(file_object["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
    assert set(file_object["required"]) == {"download_url", "file_id"}
    assert all(spec == {**spec, "type": "string"} for spec in file_object["properties"].values())
    for name, tool in tools.items():
        if name != "upload_asset":
            assert "openai/fileParams" not in (tool.meta or {}), name


def test_upload_asset_downloads_a_chatgpt_file(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_upload_asset(self, **kwargs):
        calls.append(kwargs)
        return {"id": "asset_1"}

    monkeypatch.setattr("pinbridge_mcp.service.PinBridgeService.upload_asset", fake_upload_asset)
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local", enable_write_tools=True))
    chat_file = {
        "download_url": "https://files.example.com/file_1?sig=abc",
        "file_id": "file_1",
        "mime_type": "image/png",
        "file_name": "folder-system.png",
    }
    asyncio.run(mcp.call_tool("upload_asset", {"filename": "pin.png", "file": chat_file}))
    assert calls[-1]["file_url"] == "https://files.example.com/file_1?sig=abc"
    assert calls[-1]["content_type"] == "image/png"
    assert calls[-1]["content_base64"] is None

    # ChatGPT may leave out mime_type and file_name; an explicit content_type wins.
    minimal = {"download_url": "https://files.example.com/file_2", "file_id": "file_2"}
    asyncio.run(
        mcp.call_tool(
            "upload_asset", {"filename": "a.jpg", "file": minimal, "content_type": "image/jpeg"}
        )
    )
    assert calls[-1]["file_url"] == "https://files.example.com/file_2"
    assert calls[-1]["content_type"] == "image/jpeg"


def test_create_pin_tools_drop_related_terms_from_older_clients(monkeypatch) -> None:
    tools = _tools(enable_write_tools=True)
    assert "related_terms" not in tools["create_pin"].inputSchema["properties"]
    batch_schema = tools["create_pins_batch"].inputSchema
    assert "related_terms" not in batch_schema["$defs"]["PinInput"]["properties"]

    calls: list[dict[str, Any]] = []

    async def fake_create(self, **kwargs):
        calls.append(kwargs)
        return {"id": "pin_1"}

    monkeypatch.setattr("pinbridge_mcp.service.PinBridgeService.create_pin", fake_create)
    monkeypatch.setattr("pinbridge_mcp.service.PinBridgeService.create_pins_batch", fake_create)
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local", enable_write_tools=True))

    # A client holding the older tool schema, such as ChatGPT before a rescan, still sends it.
    pin = {
        "account_id": "acct_1",
        "board_id": "b1",
        "title": "T",
        "image_url": "https://x/y.png",
        "related_terms": ["old"],
    }
    asyncio.run(mcp.call_tool("create_pin", pin))
    assert "related_terms" not in calls[-1]
    asyncio.run(mcp.call_tool("create_pins_batch", {"pins": [pin]}))
    assert "related_terms" not in str(calls[-1])


def test_schedule_tools_take_alt_text_like_create_pin(monkeypatch) -> None:
    tools = _tools(enable_write_tools=True)
    for name in ("create_pin", "create_schedule", "update_schedule"):
        assert "alt_text" in tools[name].inputSchema["properties"], name

    calls: list[dict[str, Any]] = []

    async def fake_create_schedule(self, **kwargs):
        calls.append(kwargs)
        return {"id": "sched_1"}

    async def fake_update_schedule(self, schedule_id, **kwargs):
        calls.append(kwargs)
        return {"id": schedule_id}

    monkeypatch.setattr(
        "pinbridge_mcp.service.PinBridgeService.create_schedule", fake_create_schedule
    )
    monkeypatch.setattr(
        "pinbridge_mcp.service.PinBridgeService.update_schedule", fake_update_schedule
    )
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local", enable_write_tools=True))
    asyncio.run(
        mcp.call_tool(
            "create_schedule",
            {
                "account_id": "acct_1",
                "board_id": "b1",
                "title": "Soup",
                "run_at": "2030-01-01T10:00:00Z",
                "asset_id": "asset_1",
                "alt_text": "A bowl of tomato soup",
            },
        )
    )
    assert calls[-1]["alt_text"] == "A bowl of tomato soup"

    asyncio.run(
        mcp.call_tool("update_schedule", {"schedule_id": "sched_1", "alt_text": "A red bicycle"})
    )
    assert calls[-1]["alt_text"] == "A red bicycle"


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
    assert "has_more" in instructions
    assert "limit=1" in instructions


def _quota_server(monkeypatch, *, exhausted: bool) -> tuple[Any, list[str], list[str]]:
    """A server with the quota on, a bound caller key, and the quota client faked."""
    checked: list[str] = []
    tracked: list[str] = []

    async def fake_check(self, api_key):  # noqa: ANN001, ANN202
        checked.append(api_key)
        if exhausted:
            return False, "Used all 100 assistant requests. Upgrade: https://app.test/pricing"
        return True, None

    def fake_track(self, api_key):  # noqa: ANN001, ANN202
        tracked.append(api_key)

    async def fake_accounts(self):  # noqa: ANN001, ANN202
        return {"items": [], "connect_url": "https://app.test/connect", "next_step": "connect"}

    monkeypatch.setattr("pinbridge_mcp.quota.QuotaClient.check_quota", fake_check)
    monkeypatch.setattr("pinbridge_mcp.quota.QuotaClient.track_background", fake_track)
    monkeypatch.setattr(
        "pinbridge_mcp.service.PinBridgeService.list_pinterest_accounts", fake_accounts
    )
    mcp = create_mcp_server(Settings(enable_quota=True))
    return mcp, checked, tracked


def test_tool_calls_spend_the_weekly_quota(monkeypatch) -> None:
    from pinbridge_mcp.auth import bind_current_api_key, reset_current_api_key

    mcp, checked, tracked = _quota_server(monkeypatch, exhausted=False)
    token = bind_current_api_key("pb_caller")
    try:
        asyncio.run(mcp.call_tool("list_pinterest_accounts", {}))
    finally:
        reset_current_api_key(token)
    assert checked == ["pb_caller"]
    assert tracked == ["pb_caller"]


def test_used_up_quota_returns_the_upgrade_message_as_a_tool_error(monkeypatch) -> None:
    from mcp import types

    from pinbridge_mcp.auth import bind_current_api_key, reset_current_api_key

    mcp, _, tracked = _quota_server(monkeypatch, exhausted=True)
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="list_pinterest_accounts", arguments={}),
    )
    token = bind_current_api_key("pb_caller")
    try:
        result = asyncio.run(handler(request)).root
    finally:
        reset_current_api_key(token)
    assert result.isError is True
    assert "Upgrade: https://app.test/pricing" in result.content[0].text
    assert tracked == []


def test_resource_reads_do_not_spend_the_quota(monkeypatch) -> None:
    from pinbridge_mcp.auth import bind_current_api_key, reset_current_api_key

    mcp, checked, tracked = _quota_server(monkeypatch, exhausted=True)
    token = bind_current_api_key("pb_caller")
    try:
        contents = asyncio.run(mcp.read_resource("pinbridge://accounts"))
    finally:
        reset_current_api_key(token)
    assert "connect_url" in list(contents)[0].content
    assert checked == [] and tracked == []


def test_instructions_cover_plan_limits_and_the_connect_step() -> None:
    instructions = create_mcp_server(Settings(pinbridge_api_key="pb_local")).instructions or ""
    assert "when it resets" in instructions
    assert "connect_url" in instructions
    assert "pinterest_not_connected" in instructions


def test_instructions_stay_within_directory_review_rules() -> None:
    """OpenAI holds server instructions that are long, restate tools, or sell plans."""
    instructions = create_mcp_server(Settings(pinbridge_api_key="pb_local")).instructions or ""
    assert len(instructions) <= 2000
    # The call order has to sit in the first 512 characters.
    for name in ("list_pinterest_accounts", "list_boards", "upload_asset", "create_schedule"):
        assert 0 <= instructions.find(name) < 512, name
    lowered = instructions.lower()
    for banned in ("api key", "bearer", "env var", "upgrade", "pricing", "subscription"):
        assert banned not in lowered, banned
