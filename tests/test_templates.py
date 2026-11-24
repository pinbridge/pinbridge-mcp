"""Pin templates through the MCP server (DEV-718)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pinbridge_sdk.errors import APIError, NotFoundError

from pinbridge_mcp import service as service_module
from pinbridge_mcp.config import Settings
from pinbridge_mcp.server import create_mcp_server
from pinbridge_mcp.service import TEMPLATES_NOT_AVAILABLE, PinBridgeService

TEMPLATE_ID = "7e897057-e533-446f-920c-92ab88edd99d"

TEMPLATE_DETAIL = {
    "id": TEMPLATE_ID,
    "key": "recipe-card",
    "name": "Recipe card",
    "description": "Photo on top, title on a band",
    "niches": ["food"],
    "niche": "food",
    "tags": ["recipe"],
    "source": "workspace",
    "version": 3,
    "visibility": "public",
    "maker": {"name": "Outfit Notes"},
    "review": {"status": "approved", "note": None},
    "copied_from": None,
    "copies_count": 2,
    "pins_count": 14,
    "uses_custom_fonts": False,
    "uses_uploads": False,
    "created_at": "2026-11-01T10:00:00Z",
    "updated_at": "2026-11-10T10:00:00Z",
    "definition": {
        "format": "pin-template",
        "canvas": {"width": 1000, "height": 2000, "background": "#ffffff"},
        "inputs": {
            "title": {"type": "text", "label": "Title", "required": True, "max_length": 60},
            "photo": {"type": "image", "label": "Photo", "required": True},
            "accent": {"type": "color", "label": "Accent", "default": "#D81E2F"},
        },
        "root": {"type": "frame", "children": []},
    },
    "sample_inputs": {"title": "Lemon pancakes", "photo": "niche_food_1.jpg"},
}


class _Response:
    def __init__(self, payload: Any, *, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


def _api_error(status: int, message: str, code: str | None = None, **headers: str) -> APIError:
    envelope = {"code": code or "not_found", "message": message}
    details = {"detail": message, "error": envelope}
    response = httpx.Response(status, headers=headers)
    cls = NotFoundError if status == 404 else APIError
    return cls(status_code=status, message=message, code=None, details=details, response=response)


class _TemplateClient:
    """Answers the template routes; ``script`` lists what POST /v1/pins answers in turn."""

    instances: list[_TemplateClient] = []
    script: list[Any] = []
    feature_on = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.requests: list[dict[str, Any]] = []
        _TemplateClient.instances.append(self)

    async def aclose(self) -> None:
        return None

    async def request(self, method: str, path: str, **kwargs: Any) -> _Response:
        self.requests.append({"method": method, "path": path, **kwargs})
        if path.startswith("/v1/templates") and not _TemplateClient.feature_on:
            raise _api_error(404, "Not Found")
        if path == "/v1/templates":
            return _Response([TEMPLATE_DETAIL], headers={"X-Total-Count": "7"})
        if path == "/v1/templates/{template_id}":
            if kwargs["path_params"]["template_id"] != TEMPLATE_ID:
                raise _api_error(404, "Template not found")
            return _Response(TEMPLATE_DETAIL)
        if path == "/v1/pins/batch":
            pins = kwargs["json"]["pins"]
            return _Response(
                {
                    "created_count": len(pins),
                    "existing_count": 0,
                    "failed_count": 0,
                    "results": [
                        {
                            "index": i,
                            "idempotency_key": p["idempotency_key"],
                            "status": "created",
                            "pin": {"id": f"batch-{i}"},
                            "error": None,
                        }
                        for i, p in enumerate(pins)
                    ],
                    "headroom": {"quota_remaining": 90},
                }
            )
        if path == "/v1/pins" and method == "POST":
            outcome = _TemplateClient.script.pop(0) if _TemplateClient.script else None
            if isinstance(outcome, Exception):
                raise outcome
            headers: dict[str, str] = {}
            if isinstance(outcome, tuple):
                outcome, headers = outcome
            body = outcome or {"created_at": "2999-01-01T00:00:00Z"}
            return _Response(
                {"id": f"pin-{kwargs['json']['title']}", **kwargs["json"], **body},
                headers=headers,
            )
        if path in ("/v1/pins/validate", "/v1/schedules/validate"):
            return _Response({"valid": True, "resolved": kwargs["json"]})
        if path == "/v1/schedules" and method == "POST":
            return _Response({"id": "sched_1", "status": "scheduled", **kwargs["json"]})
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.fixture
def service() -> PinBridgeService:
    _TemplateClient.instances.clear()
    _TemplateClient.script = []
    _TemplateClient.feature_on = True
    return PinBridgeService(Settings(pinbridge_api_key="pb_local"), client_factory=_TemplateClient)


def _requests() -> list[dict[str, Any]]:
    return [r for client in _TemplateClient.instances for r in client.requests]


def test_list_templates_sends_the_filters_and_summarizes(service: PinBridgeService) -> None:
    page = asyncio.run(
        service.list_templates(
            source="gallery", niche=["food", " "], q="recipe", sort="pins", limit=5, offset=10
        )
    )
    sent = _requests()[-1]
    assert sent["params"] == {
        "source": "gallery",
        "niche": ["food"],
        "q": "recipe",
        "sort": "pins",
        "limit": 5,
        "offset": 10,
    }
    assert page["total"] == 7 and page["has_more"] is False
    item = page["items"][0]
    assert item["id"] == TEMPLATE_ID and item["maker"] == {"name": "Outfit Notes"}
    # Bookkeeping and the deprecated niche are left out.
    assert "key" not in item and "niche" not in item and "definition" not in item


def test_get_template_returns_size_and_inputs(service: PinBridgeService) -> None:
    template = asyncio.run(service.get_template(TEMPLATE_ID))
    assert template["size"] == {"width": 1000, "height": 2000}
    assert template["inputs"] == {
        "title": {"type": "text", "label": "Title", "required": True, "max_length": 60},
        "photo": {"type": "image", "label": "Photo", "required": True},
        "accent": {"type": "color", "label": "Accent", "default": "#D81E2F"},
    }
    assert template["sample_inputs"]["title"] == "Lemon pancakes"
    assert "definition" not in template
    assert _requests()[-1]["params"] is None

    full = asyncio.run(service.get_template(TEMPLATE_ID, gallery=True, detail="full"))
    assert full["definition"]["root"] == {"type": "frame", "children": []}
    assert _requests()[-1]["params"] == {"gallery": "true"}


def test_template_routes_say_when_the_workspace_has_no_templates(
    service: PinBridgeService,
) -> None:
    _TemplateClient.feature_on = False
    for call in (service.list_templates(), service.get_template(TEMPLATE_ID)):
        with pytest.raises(ValueError) as caught:
            asyncio.run(call)
        assert str(caught.value) == TEMPLATES_NOT_AVAILABLE
        assert "[templates_not_available]" in PinBridgeService.format_error(caught.value)


def test_unknown_template_stays_not_found(service: PinBridgeService) -> None:
    with pytest.raises(NotFoundError) as caught:
        asyncio.run(service.get_template("00000000-0000-0000-0000-000000000000"))
    text = PinBridgeService.format_error(caught.value)
    assert "(404)" in text and "Template not found" in text


def test_create_pin_and_schedule_send_the_template_with_a_longer_timeout(
    service: PinBridgeService,
) -> None:
    template = {
        "template_id": TEMPLATE_ID,
        "template_inputs": {"title": "Soup", "photo": {"asset_id": "a1", "zoom": 1.5}},
        "template_version": 3,
    }
    pin = {"account_id": "acct_1", "board_id": "b1", "title": "Soup", **template}

    async def run() -> None:
        await service.create_pin(**pin, dry_run=True)
        dry = _requests()[-1]
        assert dry["path"] == "/v1/pins/validate"
        assert {k: dry["json"][k] for k in template} == template
        assert "image_url" not in dry["json"] and "asset_id" not in dry["json"]
        assert _TemplateClient.instances[-1].kwargs["timeout"] == 90.0

        created = await service.create_pin(**pin)
        assert created["template_id"] == TEMPLATE_ID
        assert _TemplateClient.instances[-1].kwargs["timeout"] == 90.0

        schedule = await service.create_schedule(
            **{k: v for k, v in pin.items()}, run_at="2030-01-01T10:00:00Z"
        )
        sent = _requests()[-1]
        assert sent["path"] == "/v1/schedules" and sent["method"] == "POST"
        assert schedule["template_id"] == TEMPLATE_ID
        assert schedule["template_version"] == 3

        # An image pin keeps the SDK's default timeout.
        await service.create_pin(
            account_id="acct_1", board_id="b1", title="Plain", image_url="https://x/y.png"
        )
        assert "timeout" not in _TemplateClient.instances[-1].kwargs

    asyncio.run(run())


def _drawn(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "account_id": "acct_1",
        "board_id": "b1",
        "title": title,
        "template_id": TEMPLATE_ID,
        "template_inputs": {"title": title},
        **extra,
    }


def _plain(title: str) -> dict[str, Any]:
    return {"account_id": "acct_1", "board_id": "b1", "title": title, "image_url": "https://x/a"}


def test_batch_sends_template_entries_one_at_a_time(service: PinBridgeService) -> None:
    _TemplateClient.script = [
        None,
        {"created_at": "2020-01-01T00:00:00Z"},  # an idempotency-key hit
        _api_error(422, "Some template_inputs can't be used.", "template_inputs_invalid"),
    ]
    answer = asyncio.run(
        service.create_pins_batch(
            [
                _plain("P0"),
                _drawn("D1"),
                _plain("P2"),
                _drawn("D3", idempotency_key="k3"),
                _drawn("D4"),
            ]
        )
    )
    batch_calls = [r for r in _requests() if r["path"] == "/v1/pins/batch"]
    pin_calls = [r for r in _requests() if r["path"] == "/v1/pins"]
    assert [p["title"] for p in batch_calls[0]["json"]["pins"]] == ["P0", "P2"]
    assert [p["json"]["title"] for p in pin_calls] == ["D1", "D3", "D4"]
    assert pin_calls[1]["json"]["idempotency_key"] == "k3"

    assert [r["index"] for r in answer["results"]] == [0, 1, 2, 3, 4]
    assert [r["status"] for r in answer["results"]] == [
        "created",
        "created",
        "created",
        "existing",
        "failed",
    ]
    assert answer["results"][2]["pin"] == {"id": "batch-1"}
    assert answer["results"][4]["error"]["code"] == "template_inputs_invalid"
    assert answer["results"][4]["error"]["status_code"] == 422
    assert (answer["created_count"], answer["existing_count"], answer["failed_count"]) == (3, 1, 1)
    assert answer["not_sent_count"] == 0
    assert answer["headroom"] == {"quota_remaining": 90}


def test_batch_reads_replays_by_the_api_clock(service: PinBridgeService) -> None:
    """Pin age is read against the API's Date header, so a skewed client clock is moot."""
    created = {"created_at": "2026-01-01T00:00:00.400Z"}
    _TemplateClient.script = [
        (created, {"date": "Thu, 01 Jan 2026 00:00:00 GMT"}),  # saved during this request
        (created, {"date": "Thu, 01 Jan 2026 00:00:03 GMT"}),  # saved 3 s before it
    ]
    answer = asyncio.run(service.create_pins_batch([_drawn("D0"), _drawn("D1")]))
    assert [r["status"] for r in answer["results"]] == ["created", "existing"]


def test_batch_of_image_pins_still_returns_the_api_answer(service: PinBridgeService) -> None:
    answer = asyncio.run(service.create_pins_batch([_plain("P0"), _plain("P1")]))
    assert answer["created_count"] == 2
    assert "not_sent_count" not in answer
    assert all(r["path"] == "/v1/pins/batch" for r in _requests())


def test_batch_waits_out_render_busy(service: PinBridgeService, monkeypatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(service_module, "_sleep", fake_sleep)
    busy = _api_error(503, "Every renderer is busy.", "render_busy", **{"Retry-After": "3"})
    _TemplateClient.script = [busy, None, busy, busy, busy]
    answer = asyncio.run(service.create_pins_batch([_drawn("D0"), _drawn("D1"), _drawn("D2")]))
    # D0 is drawn after one wait. D1 stays busy after two waits, so neither it nor D2 is
    # sent again: both come back not_sent with the API's Retry-After.
    assert waits == [3, 3, 3]
    assert [r["status"] for r in answer["results"]] == ["created", "not_sent", "not_sent"]
    assert answer["results"][1]["error"]["retry_after_seconds"] == 3
    assert "Wait 3 seconds" in answer["results"][2]["error"]["remediation"]
    keys = [r["json"]["idempotency_key"] for r in _requests() if r["path"] == "/v1/pins"]
    # Every attempt for an entry reuses its key, so a retry never duplicates the pin.
    assert len(keys) == 5 and len(set(keys[:2])) == 1 and len(set(keys[2:])) == 1


def test_batch_does_not_wait_past_its_budget(service: PinBridgeService, monkeypatch) -> None:
    async def no_sleep(seconds: float) -> None:
        raise AssertionError("must not wait")

    monkeypatch.setattr(service_module, "_sleep", no_sleep)
    busy = _api_error(503, "Every renderer is busy.", "render_busy", **{"Retry-After": "50"})
    _TemplateClient.script = [busy]
    answer = asyncio.run(service.create_pins_batch([_drawn("D0"), _drawn("D1")]))
    assert [r["status"] for r in answer["results"]] == ["not_sent", "not_sent"]
    assert answer["results"][0]["error"]["retry_after_seconds"] == 50
    assert len([r for r in _requests() if r["path"] == "/v1/pins"]) == 1


def test_batch_marks_entries_past_the_time_budget_not_sent(
    service: PinBridgeService, monkeypatch
) -> None:
    # Budget starts at 0 and ends at 45. D0 runs from 1 to 31, so D1 (at 31, with a
    # 30-second draw behind it) would end past 45 and is not started; D2 neither.
    clock = iter([0.0, 1.0, 31.0, 31.0, 31.0])
    monkeypatch.setattr(service_module, "_clock", lambda: next(clock))
    answer = asyncio.run(service.create_pins_batch([_drawn("D0"), _drawn("D1"), _drawn("D2")]))
    assert [r["status"] for r in answer["results"]] == ["created", "not_sent", "not_sent"]
    assert answer["not_sent_count"] == 2
    assert answer["results"][1]["error"]["code"] == "not_sent"
    assert len([r for r in _requests() if r["path"] == "/v1/pins"]) == 1


def _tools() -> dict[str, Any]:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local", enable_write_tools=True))
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def test_tools_take_the_template_fields() -> None:
    tools = _tools()
    for name in ("create_pin", "create_schedule"):
        props = tools[name].inputSchema["properties"]
        assert {"template_id", "template_inputs", "template_version"} <= set(props), name
        assert "get_template" in props["template_inputs"]["description"], name
    batch = tools["create_pins_batch"].inputSchema["$defs"]["PinInput"]["properties"]
    assert {"template_id", "template_inputs", "template_version"} <= set(batch)

    listing = tools["list_templates"]
    assert listing.annotations.readOnlyHint is True
    assert listing.annotations.openWorldHint is False
    assert set(listing.outputSchema["properties"]) == {
        "items",
        "total",
        "limit",
        "offset",
        "has_more",
    }
    assert listing.inputSchema["properties"]["sort"]["enum"] == [
        "updated",
        "pins",
        "copies",
        "name",
    ]
    assert tools["get_template"].inputSchema["required"] == ["template_id"]


def test_instructions_and_prompt_point_at_templates() -> None:
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local", enable_write_tools=True))
    instructions = mcp.instructions or ""
    assert "list_templates" in instructions and "template_inputs" in instructions
    rendered = asyncio.run(mcp.get_prompt("publish_pin", {}))
    text = rendered.messages[0].content.text
    assert "get_template" in text and "template_inputs" in text


def test_list_templates_tool_returns_a_page(monkeypatch) -> None:
    async def fake_list(self, **kwargs):
        assert kwargs == {
            "source": None,
            "niche": ["food"],
            "q": None,
            "sort": "updated",
            "limit": 20,
            "offset": 0,
        }
        return {
            "items": [{"id": TEMPLATE_ID}],
            "total": 1,
            "limit": 20,
            "offset": 0,
            "has_more": False,
        }

    monkeypatch.setattr("pinbridge_mcp.service.PinBridgeService.list_templates", fake_list)
    mcp = create_mcp_server(Settings(pinbridge_api_key="pb_local"))
    _, structured = asyncio.run(mcp.call_tool("list_templates", {"niche": ["food"]}))
    assert structured["items"] == [{"id": TEMPLATE_ID}]
