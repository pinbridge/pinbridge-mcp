from __future__ import annotations

import asyncio
import base64

import pytest
from pinbridge_sdk.errors import APIError, AuthenticationError

from pinbridge_mcp.auth import bind_current_api_key, reset_current_api_key
from pinbridge_mcp.config import Settings
from pinbridge_mcp.service import PinBridgeService


class _FakeModel:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str = "python") -> dict:
        return dict(self._payload)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"" if status_code == 204 else b"{}"

    def json(self):
        return self._payload if not isinstance(self._payload, dict) else dict(self._payload)


class _FakePinterest:
    async def list_accounts(self) -> list[_FakeModel]:
        return [_FakeModel({"id": "acct_1", "username": "demo"})]

    async def list_boards(self, account_id: str) -> list[_FakeModel]:
        return [_FakeModel({"id": "board_1", "account_id": account_id, "name": "Ideas"})]

    async def list_related_terms(
        self,
        *,
        account_id: str,
        terms: list[str],
        exact_match: bool,
    ) -> _FakeModel:
        return _FakeModel(
            {
                "id": account_id,
                "related_term_count": len(terms),
                "related_terms_list": [
                    {"term": term, "related_terms": [f"{term} idea"]} for term in terms
                ],
                "exact_match": exact_match,
            }
        )


class _FakePins:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.retries: list[tuple[str, dict | None]] = []

    async def get(self, pin_id: str) -> _FakeModel:
        return _FakeModel({"id": pin_id, "title": "Demo pin"})

    async def retry(self, pin_id: str, data: dict | None = None) -> _FakeModel:
        self.retries.append((pin_id, data))
        return _FakeModel({"id": pin_id, "status": "queued"})

    async def create(self, payload: dict) -> _FakeModel:
        self.created.append(payload)
        return _FakeModel({"id": "pin_new", "status": "queued", **payload})


class _FakeAssets:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str | None, str | None]] = []

    async def upload_image(self, data: bytes, *, filename: str, content_type: str | None):
        self.uploads.append(("image", data, filename, content_type))
        return _FakeModel({"id": "asset_1", "asset_type": "image", "public_url": "https://cdn/x"})

    async def upload_video(self, data: bytes, *, filename: str, content_type: str | None):
        self.uploads.append(("video", data, filename, content_type))
        return _FakeModel({"id": "asset_2", "asset_type": "video"})


class _FakeWebhooks:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.updated: list[tuple[str, dict]] = []

    async def list(self) -> list[_FakeModel]:
        return [_FakeModel({"id": "webhook_1", "url": "https://example.com/webhook"})]

    async def create(self, payload: dict) -> _FakeModel:
        return _FakeModel({"id": "webhook_new", **payload})

    async def update(self, webhook_id: str, payload: dict) -> _FakeModel:
        self.updated.append((webhook_id, payload))
        return _FakeModel({"id": webhook_id, **payload})

    async def delete(self, webhook_id: str) -> None:
        self.deleted.append(webhook_id)


class _FakeBilling:
    async def status(self) -> _FakeModel:
        return _FakeModel({"plan": "free", "quota_calls_monthly": 1000, "calls_used": 10})


class _FakeRateMeter:
    async def get(self, account_id: str) -> _FakeModel:
        return _FakeModel({"account_id": account_id, "tokens_available": 10})


class _FakeSchedules:
    def __init__(self) -> None:
        self.retried: list[str] = []
        self.deleted: list[str] = []

    async def create(self, payload: dict) -> _FakeModel:
        return _FakeModel({"id": "sched_new", "status": "scheduled", **payload})

    async def get(self, schedule_id: str) -> _FakeModel:
        return _FakeModel({"id": schedule_id})

    async def cancel(self, schedule_id: str) -> _FakeModel:
        return _FakeModel({"id": schedule_id, "status": "canceled"})

    async def retry(self, schedule_id: str) -> _FakeModel:
        self.retried.append(schedule_id)
        return _FakeModel({"id": schedule_id, "status": "scheduled"})

    async def delete(self, schedule_id: str) -> None:
        self.deleted.append(schedule_id)


class _FakeClient:
    """Records every raw request so tests can assert paths, params and bodies."""

    instances: list[_FakeClient] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs
        self.pinterest = _FakePinterest()
        self.pins = _FakePins()
        self.assets = _FakeAssets()
        self.webhooks = _FakeWebhooks()
        self.billing = _FakeBilling()
        self.rate_meter = _FakeRateMeter()
        self.schedules = _FakeSchedules()
        self.requests: list[dict] = []
        _FakeClient.instances.append(self)

    async def aclose(self) -> None:
        return None

    async def request(self, method: str, path: str, **kwargs) -> _FakeResponse:
        self.requests.append({"method": method, "path": path, **kwargs})
        if path == "/v1/activity-logs":
            return _FakeResponse(
                {"items": [{"id": "log_1", **(kwargs.get("params") or {})}], "next_cursor": None}
            )
        if path == "/v1/pins" and method == "GET":
            return _FakeResponse([{"id": "pin_1", **(kwargs.get("params") or {})}])
        if path == "/v1/schedules" and method == "GET":
            return _FakeResponse([{"id": "sched_1", **(kwargs.get("params") or {})}])
        if path == "/v1/pins/validate":
            return _FakeResponse(
                {"valid": True, "dry_run": True, "checks": [], "resolved": kwargs["json"]}
            )
        if path == "/v1/schedules/validate":
            return _FakeResponse({"valid": False, "dry_run": True, "checks": [{"name": "run_at"}]})
        if path == "/v1/pins/batch":
            return _FakeResponse({"created_count": len(kwargs["json"]["pins"]), "results": []})
        if path == "/v1/schedules/{schedule_id}" and method == "PATCH":
            return _FakeResponse(
                {
                    "id": kwargs["path_params"]["schedule_id"],
                    "status": "scheduled",
                    **kwargs["json"],
                }
            )
        if path == "/v1/pinterest/boards/{board_id}" and method == "PATCH":
            return _FakeResponse({"id": kwargs["path_params"]["board_id"], **kwargs["json"]})
        if path == "/v1/pins/{pin_id}" and method == "PATCH":
            return _FakeResponse({"id": kwargs["path_params"]["pin_id"], **kwargs["json"]})
        if path == "/v1/pins/{pin_id}" and method == "DELETE":
            if kwargs.get("params") and kwargs["path_params"]["pin_id"] != "pin_old_api":
                return _FakeResponse(
                    {"id": "pin_1", "deleted": True, "removed_from_pinterest": True}
                )
            return _FakeResponse(None, status_code=204)
        if path.endswith("/analytics"):
            return _FakeResponse(
                {"totals": {"impression": 5}, "daily": [], **(kwargs.get("params") or {})}
            )
        if path == "/v1/pinterest/boards/{board_id}/access":
            return _FakeResponse(
                {"publishable": True, "status": "ok", **(kwargs.get("params") or {})}
            )
        raise AssertionError(f"Unexpected request: {method} {path}")


def _service() -> PinBridgeService:
    _FakeClient.instances.clear()
    return PinBridgeService(Settings(pinbridge_api_key="pb_local_key"), client_factory=_FakeClient)


def _last_request() -> dict:
    return _FakeClient.instances[-1].requests[-1]


def test_service_uses_bound_request_api_key() -> None:
    service = PinBridgeService(Settings(), client_factory=_FakeClient)
    token = bind_current_api_key("pb_test_123")

    async def run() -> None:
        accounts = await service.list_pinterest_accounts()
        assert accounts == [{"id": "acct_1", "username": "demo"}]
        assert _FakeClient.instances[-1].kwargs["api_key"] == "pb_test_123"

    try:
        asyncio.run(run())
    finally:
        reset_current_api_key(token)


def test_service_related_terms_normalizes_string_input() -> None:
    service = _service()

    async def run() -> None:
        response = await service.list_related_terms(
            account_id="acct_1", terms="home decor,  kitchen", exact_match=True
        )
        assert response["related_term_count"] == 2
        assert response["exact_match"] is True

    asyncio.run(run())


def test_service_activity_logs_uses_raw_request_for_sdk_compatibility() -> None:
    service = _service()

    async def run() -> None:
        response = await service.list_activity_logs(
            limit=5, category="publishing", status="success", since="2026-03-30T12:00:00Z"
        )
        assert response["items"][0]["limit"] == 5
        assert response["items"][0]["since"] == "2026-03-30T12:00:00+00:00"

    asyncio.run(run())


def test_list_pins_and_schedules_send_filters_server_side() -> None:
    service = _service()

    async def run() -> None:
        pins = await service.list_pins(
            limit=5, status="failed", error_code="board_access_denied", since="2026-09-01T00:00:00Z"
        )
        assert pins[0]["status"] == "failed"
        assert pins[0]["error_code"] == "board_access_denied"
        assert pins[0]["since"] == "2026-09-01T00:00:00+00:00"
        assert "board_id" not in pins[0]
        schedules = await service.list_schedules(status="scheduled", account_id="acct_1")
        assert schedules[0]["status"] == "scheduled"
        assert _last_request()["params"]["account_id"] == "acct_1"

    asyncio.run(run())


def test_create_pin_dry_run_validates_instead_of_publishing() -> None:
    service = _service()

    async def run() -> None:
        result = await service.create_pin(
            account_id="acct_1", board_id="b1", title="T", image_url="https://x/y.png", dry_run=True
        )
        assert result["valid"] is True
        assert result["resolved"]["idempotency_key"]
        assert _FakeClient.instances[-1].pins.created == []

        created = await service.create_pin(
            account_id="acct_1", board_id="b1", title="T", image_url="https://x/y.png"
        )
        assert created["status"] == "queued"
        assert "description" not in created  # None fields are not sent

    asyncio.run(run())


def test_create_schedule_dry_run_and_batch() -> None:
    service = _service()

    async def run() -> None:
        result = await service.create_schedule(
            account_id="acct_1",
            board_id="b1",
            title="T",
            run_at="2026-10-01T10:00:00Z",
            image_url="https://x/y.png",
            dry_run=True,
        )
        assert result["valid"] is False
        assert _last_request()["json"]["run_at"] == "2026-10-01T10:00:00+00:00"

        batch = await service.create_pins_batch(
            [
                {
                    "account_id": "acct_1",
                    "board_id": "b1",
                    "title": "A",
                    "image_url": "https://x/a",
                },
                {
                    "account_id": "acct_1",
                    "board_id": "b1",
                    "title": "B",
                    "image_url": "https://x/b",
                    "idempotency_key": "k-b",
                },
            ]
        )
        assert batch["created_count"] == 2
        sent = _last_request()["json"]["pins"]
        assert sent[1]["idempotency_key"] == "k-b"
        assert sent[0]["idempotency_key"]

    asyncio.run(run())


def test_batch_rejects_incomplete_entries() -> None:
    service = _service()
    with pytest.raises(ValueError, match="pins\\[0\\] is missing required field 'board_id'"):
        asyncio.run(service.create_pins_batch([{"account_id": "a", "title": "x"}]))


def test_update_delete_retry_and_analytics_hit_new_endpoints() -> None:
    service = _service()

    async def run() -> None:
        updated = await service.update_pin("pin_1", title="Fixed", description="d")
        assert updated == {"id": "pin_1", "title": "Fixed", "description": "d"}

        with pytest.raises(ValueError):
            await service.update_pin("pin_1")

        removed = await service.delete_pin("pin_1")
        assert removed["removed_from_pinterest"] is True
        assert _last_request()["params"] == {"delete_from_pinterest": "true"}

        record_only = await service.delete_pin("pin_1", delete_from_pinterest=False)
        assert record_only == {
            "id": "pin_1",
            "deleted": True,
            "removed_from_pinterest": False,
            "pinterest_pin_id": None,
            "reason": "record_only",
        }

        old_api = await service.delete_pin("pin_old_api")
        assert old_api["reason"] == "api_version_too_old"
        assert "still live on Pinterest" in old_api["warning"]

        retried = await service.retry_pin("pin_1", board_id="b2")
        assert retried["status"] == "queued"
        assert _FakeClient.instances[-1].pins.retries == [("pin_1", {"board_id": "b2"})]
        await service.retry_pin("pin_1")
        assert _FakeClient.instances[-1].pins.retries[-1] == ("pin_1", None)

        analytics = await service.get_pin_analytics(
            "pin_1", start_date="2026-09-01", metrics="IMPRESSION"
        )
        assert analytics["totals"] == {"impression": 5}
        assert analytics["start_date"] == "2026-09-01"
        account = await service.get_account_analytics("acct_1", end_date="2026-09-20")
        assert account["end_date"] == "2026-09-20"

        access = await service.check_board_access(account_id="acct_1", board_id="b1", fresh=True)
        assert access["publishable"] is True
        assert access["fresh"] == "true"

    asyncio.run(run())


def test_upload_asset_from_base64_and_validation() -> None:
    service = _service()

    async def run() -> None:
        payload = base64.b64encode(b"\x89PNG...").decode()
        asset = await service.upload_asset(
            filename="hero.png", content_base64=payload, content_type="image/png"
        )
        assert asset["id"] == "asset_1"
        kind, data, filename, content_type = _FakeClient.instances[-1].assets.uploads[0]
        assert (kind, data, filename, content_type) == (
            "image",
            b"\x89PNG...",
            "hero.png",
            "image/png",
        )

        video = await service.upload_asset(
            filename="clip.mp4", content_base64=payload, asset_type="video"
        )
        assert video["id"] == "asset_2"

    asyncio.run(run())

    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(service.upload_asset(filename="x.png"))
    with pytest.raises(ValueError, match="valid base64"):
        asyncio.run(service.upload_asset(filename="x.png", content_base64="not base64!!"))
    with pytest.raises(ValueError, match="asset_type"):
        asyncio.run(service.upload_asset(filename="x.gif", content_base64="aGk=", asset_type="gif"))


def test_upload_asset_accepts_wrapped_unpadded_and_data_uri_base64() -> None:
    service = _service()
    raw = base64.b64encode(b"hello world").decode()  # aGVsbG8gd29ybGQ=
    variants = [
        raw[:8] + "\n" + raw[8:],
        raw.rstrip("="),
        f"data:image/png;base64,{raw}",
    ]
    for variant in variants:
        asyncio.run(service.upload_asset(filename="x.png", content_base64=variant))
        assert _FakeClient.instances[-1].assets.uploads[-1][1] == b"hello world"


class _StreamedResponse:
    def __init__(self, status_code=200, headers=None, chunks=(b"bytes-from-url",)):
        self.status_code = status_code
        self.headers = headers or {"content-type": "image/jpeg; charset=binary"}
        self._chunks = chunks

    async def aiter_bytes(self, size):
        for chunk in self._chunks:
            yield chunk


class _StreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return None


def _install_fetcher(monkeypatch, response, seen: list[str]):
    class _Fetcher:
        def __init__(self, *args, **kwargs) -> None:
            assert kwargs.get("follow_redirects") is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        def stream(self, method: str, url: str):
            seen.append(url)
            return _StreamContext(response)

    monkeypatch.setattr("pinbridge_mcp.service.httpx.AsyncClient", _Fetcher)


def test_upload_asset_from_public_source_url(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr("pinbridge_mcp.service._host_is_public", lambda host: True)
    seen: list[str] = []
    _install_fetcher(monkeypatch, _StreamedResponse(), seen)

    asset = asyncio.run(
        service.upload_asset(filename="hero.jpg", source_url="https://cdn.example.com/hero.jpg")
    )
    assert asset["id"] == "asset_1"
    assert seen == ["https://cdn.example.com/hero.jpg"]
    kind, data, filename, content_type = _FakeClient.instances[-1].assets.uploads[0]
    assert (data, content_type) == (b"bytes-from-url", "image/jpeg")


def test_upload_asset_refuses_private_hosts_schemes_and_redirects(monkeypatch) -> None:
    service = _service()
    seen: list[str] = []

    for url in (
        "http://127.0.0.1:8000/x.png",
        "http://169.254.169.254/latest",
        "http://api:8000/x",
    ):
        with pytest.raises(ValueError, match="public host"):
            asyncio.run(service.upload_asset(filename="x.png", source_url=url))
    with pytest.raises(ValueError, match="http\\(s\\)"):
        asyncio.run(service.upload_asset(filename="x.png", source_url="file:///etc/passwd"))

    monkeypatch.setattr("pinbridge_mcp.service._host_is_public", lambda host: True)
    _install_fetcher(
        monkeypatch, _StreamedResponse(status_code=302, headers={"location": "https://z"}), seen
    )
    with pytest.raises(ValueError, match="redirects"):
        asyncio.run(service.upload_asset(filename="x.png", source_url="https://cdn.example.com/a"))


def test_upload_asset_enforces_size_ceiling_while_streaming(monkeypatch) -> None:
    from pinbridge_mcp import service as service_module

    service = _service()
    monkeypatch.setattr("pinbridge_mcp.service._host_is_public", lambda host: True)
    monkeypatch.setattr(service_module, "_MAX_ASSET_BYTES", 10)
    seen: list[str] = []
    _install_fetcher(monkeypatch, _StreamedResponse(headers={"content-length": "11"}), seen)
    with pytest.raises(ValueError, match="200 MB"):
        asyncio.run(service.upload_asset(filename="x.png", source_url="https://cdn.example.com/a"))

    _install_fetcher(
        monkeypatch, _StreamedResponse(headers={}, chunks=(b"123456", b"7890123")), seen
    )
    with pytest.raises(ValueError, match="200 MB"):
        asyncio.run(service.upload_asset(filename="x.png", source_url="https://cdn.example.com/b"))


def test_host_is_public_rejects_private_and_accepts_global(monkeypatch) -> None:
    from pinbridge_mcp.service import _host_is_public

    def fake_getaddrinfo(host, port):
        table = {"private.internal": "10.0.0.5", "public.example": "93.184.216.34"}
        return [(None, None, None, None, (table[host], 0))]

    monkeypatch.setattr("pinbridge_mcp.service.socket.getaddrinfo", fake_getaddrinfo)
    assert _host_is_public("private.internal") is False
    assert _host_is_public("public.example") is True


def test_webhook_create_and_delete() -> None:
    service = _service()

    async def run() -> None:
        created = await service.create_webhook(
            url="https://example.com/hook", secret="0123456789abcdef", events=["pin.published"]
        )
        assert created["id"] == "webhook_new"
        deleted = await service.delete_webhook("webhook_1")
        assert deleted == {"deleted": True, "webhook_id": "webhook_1"}
        assert _FakeClient.instances[-1].webhooks.deleted == ["webhook_1"]

    asyncio.run(run())


def test_webhook_update_sends_only_given_fields() -> None:
    service = _service()

    async def run() -> None:
        updated = await service.update_webhook("webhook_1", is_enabled=False)
        assert updated == {"id": "webhook_1", "is_enabled": False}
        assert _FakeClient.instances[-1].webhooks.updated == [("webhook_1", {"is_enabled": False})]

    asyncio.run(run())


def test_webhook_update_rejects_empty_change() -> None:
    service = _service()
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(service.update_webhook("webhook_1"))


def test_update_schedule_sends_only_given_fields_and_normalizes_run_at() -> None:
    service = _service()

    async def run() -> None:
        updated = await service.update_schedule(
            "sched_1", run_at="2030-01-01T10:00:00+02:00", title="Moved"
        )
        assert updated["id"] == "sched_1"
        sent = _FakeClient.instances[-1].requests[-1]
        assert sent["method"] == "PATCH"
        assert sent["json"] == {"run_at": "2030-01-01T10:00:00+02:00", "title": "Moved"}

    asyncio.run(run())
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(service.update_schedule("sched_1"))
    with pytest.raises(ValueError, match="not both"):
        asyncio.run(service.update_schedule("sched_1", image_url="https://x/a.png", asset_id="a"))
    with pytest.raises(ValueError, match="run_at"):
        asyncio.run(service.update_schedule("sched_1", run_at="tomorrow"))


def test_update_board_requires_a_change() -> None:
    service = _service()

    async def run() -> None:
        updated = await service.update_board("board_1", account_id="acct_1", name="Renamed")
        assert updated == {"id": "board_1", "account_id": "acct_1", "name": "Renamed"}

    asyncio.run(run())
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(service.update_board("board_1", account_id="acct_1"))


def test_schedule_retry_and_delete() -> None:
    service = _service()

    async def run() -> None:
        retried = await service.retry_schedule("sched_1")
        assert retried == {"id": "sched_1", "status": "scheduled"}
        assert _FakeClient.instances[-1].schedules.retried == ["sched_1"]
        deleted = await service.delete_schedule("sched_2")
        assert deleted == {"deleted": True, "schedule_id": "sched_2"}
        assert _FakeClient.instances[-1].schedules.deleted == ["sched_2"]

    asyncio.run(run())


def test_format_error_surfaces_code_and_remediation() -> None:
    error = APIError(
        status_code=422,
        message="Pinterest has no board with this ID visible to the connected account.",
        code=None,
        details={
            "detail": {"error": {"code": "board_not_found"}},
            "error": {
                "code": "board_not_found",
                "message": (
                    "Pinterest has no board with this ID visible to the connected account."
                ),
                "remediation": (
                    "Refresh the board list and use a board ID that belongs to this account."
                ),
            },
        },
    )
    text = PinBridgeService.format_error(error)
    assert text == (
        "PinBridge API error (422) [board_not_found]: Pinterest has no board with this ID "
        "visible to the connected account. Fix: Refresh the board list and use a board ID "
        "that belongs to this account."
    )
    scope = AuthenticationError(
        status_code=403,
        message="This API key does not have the 'write' scope.",
        details={
            "error": {
                "code": "insufficient_scope",
                "message": "This API key does not have the 'write' scope.",
                "remediation": "Ask a workspace admin for a key with the write scope.",
            }
        },
    )
    assert PinBridgeService.format_error(scope) == (
        "PinBridge API error (403) [insufficient_scope]: This API key does not have the "
        "'write' scope. Fix: Ask a workspace admin for a key with the write scope."
    )
    login = AuthenticationError(
        status_code=401, message="Invalid API key", details="Invalid API key"
    )
    assert (
        PinBridgeService.format_error(login) == "PinBridge authentication failed: Invalid API key"
    )

    validation = APIError(
        status_code=422,
        message="Request validation failed for: title.",
        details={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed for: title.",
                "errors": [
                    {"loc": ["body", "title"], "msg": "String should have at most 100 characters"}
                ],
            }
        },
    )
    assert PinBridgeService.format_error(validation) == (
        "PinBridge API error (422) [validation_error]: Request validation failed for: title. "
        "(title: String should have at most 100 characters)"
    )
    limited = APIError(
        status_code=429,
        message="Too many Pinterest read requests",
        details={
            "error": {
                "code": "rate_limited",
                "message": "Too many Pinterest read requests",
                "retry_after_seconds": 42,
                "remediation": "Retry after the Retry-After window.",
            }
        },
    )
    assert "Retry after 42s." in PinBridgeService.format_error(limited)
    plain = APIError(status_code=500, message="Internal server error", details="boom")
    assert (
        PinBridgeService.format_error(plain) == "PinBridge API error (500): Internal server error"
    )
