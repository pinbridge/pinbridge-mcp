from __future__ import annotations

import asyncio
import base64

import pytest
from pinbridge_sdk.errors import APIError

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

    async def get(self, pin_id: str) -> _FakeModel:
        return _FakeModel({"id": pin_id, "title": "Demo pin"})

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
    async def list(self) -> list[_FakeModel]:
        return [_FakeModel({"id": "webhook_1", "url": "https://example.com/webhook"})]

    async def create(self, payload: dict) -> _FakeModel:
        return _FakeModel({"id": "webhook_new", **payload})


class _FakeBilling:
    async def status(self) -> _FakeModel:
        return _FakeModel({"plan": "free", "quota_calls_monthly": 1000, "calls_used": 10})


class _FakeRateMeter:
    async def get(self, account_id: str) -> _FakeModel:
        return _FakeModel({"account_id": account_id, "tokens_available": 10})


class _FakeSchedules:
    async def create(self, payload: dict) -> _FakeModel:
        return _FakeModel({"id": "sched_new", "status": "scheduled", **payload})

    async def get(self, schedule_id: str) -> _FakeModel:
        return _FakeModel({"id": schedule_id})

    async def cancel(self, schedule_id: str) -> _FakeModel:
        return _FakeModel({"id": schedule_id, "status": "canceled"})


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
        if path == "/v1/pins/{pin_id}" and method == "PATCH":
            return _FakeResponse({"id": kwargs["path_params"]["pin_id"], **kwargs["json"]})
        if path == "/v1/pins/{pin_id}" and method == "DELETE":
            if kwargs.get("params"):
                return _FakeResponse(
                    {"id": "pin_1", "deleted": True, "removed_from_pinterest": True}
                )
            return _FakeResponse(None, status_code=204)
        if path == "/v1/pins/{pin_id}/retry":
            return _FakeResponse({"id": kwargs["path_params"]["pin_id"], "status": "queued"})
        if path.endswith("/analytics"):
            return _FakeResponse(
                {"totals": {"impression": 5}, "daily": [], **(kwargs.get("params") or {})}
            )
        if path == "/v1/pinterest/boards/{board_id}/access":
            return _FakeResponse(
                {"publishable": True, "status": "ok", **(kwargs.get("params") or {})}
            )
        if path == "/v1/webhooks/{webhook_id}" and method == "DELETE":
            return _FakeResponse(None, status_code=204)
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

        retried = await service.retry_pin("pin_1", board_id="b2")
        assert retried["status"] == "queued"
        assert _last_request()["json"] == {"board_id": "b2"}

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
            filename="clip.mp4", content_base64=payload, media_type="video"
        )
        assert video["id"] == "asset_2"

    asyncio.run(run())

    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(service.upload_asset(filename="x.png"))
    with pytest.raises(ValueError, match="valid base64"):
        asyncio.run(service.upload_asset(filename="x.png", content_base64="not base64!!"))
    with pytest.raises(ValueError, match="media_type"):
        asyncio.run(service.upload_asset(filename="x.gif", content_base64="aGk=", media_type="gif"))


def test_upload_asset_from_source_url(monkeypatch) -> None:
    service = _service()

    class _Fetched:
        status_code = 200
        content = b"bytes-from-url"
        headers = {"content-type": "image/jpeg; charset=binary"}

    class _Fetcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            assert url == "https://cdn.example.com/hero.jpg"
            return _Fetched()

    monkeypatch.setattr("pinbridge_mcp.service.httpx.AsyncClient", _Fetcher)

    asset = asyncio.run(
        service.upload_asset(filename="hero.jpg", source_url="https://cdn.example.com/hero.jpg")
    )
    assert asset["id"] == "asset_1"
    kind, data, filename, content_type = _FakeClient.instances[-1].assets.uploads[0]
    assert (data, content_type) == (b"bytes-from-url", "image/jpeg")


def test_webhook_create_and_delete() -> None:
    service = _service()

    async def run() -> None:
        created = await service.create_webhook(
            url="https://example.com/hook", secret="0123456789abcdef", events=["pin.published"]
        )
        assert created["id"] == "webhook_new"
        deleted = await service.delete_webhook("webhook_1")
        assert deleted == {"deleted": True, "webhook_id": "webhook_1"}
        assert _last_request()["path_params"] == {"webhook_id": "webhook_1"}

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
    plain = APIError(status_code=500, message="Internal server error", details="boom")
    assert (
        PinBridgeService.format_error(plain) == "PinBridge API error (500): Internal server error"
    )
