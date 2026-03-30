from __future__ import annotations

import asyncio

from pinbridge_mcp.auth import bind_current_api_key, reset_current_api_key
from pinbridge_mcp.config import Settings
from pinbridge_mcp.service import PinBridgeService


class _FakeModel:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str = "python") -> dict:
        return dict(self._payload)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return dict(self._payload)


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
                    {"term": term, "related_terms": [f"{term} idea"]}
                    for term in terms
                ],
                "exact_match": exact_match,
            }
        )


class _FakePins:
    async def list(self, *, limit: int, offset: int) -> list[_FakeModel]:
        return [_FakeModel({"id": "pin_1", "limit": limit, "offset": offset})]

    async def get(self, pin_id: str) -> _FakeModel:
        return _FakeModel({"id": pin_id, "title": "Demo pin"})


class _FakeActivityLogs:
    async def list(self, **kwargs) -> _FakeModel:
        return _FakeModel({"items": [{"id": "log_1", **kwargs}], "next_cursor": None})


class _FakeWebhooks:
    async def list(self) -> list[_FakeModel]:
        return [_FakeModel({"id": "webhook_1", "url": "https://example.com/webhook"})]


class _FakeBilling:
    async def status(self) -> _FakeModel:
        return _FakeModel({"plan": "free", "quota_calls_monthly": 1000, "calls_used": 10})


class _FakeRateMeter:
    async def get(self, account_id: str) -> _FakeModel:
        return _FakeModel({"account_id": account_id, "tokens_available": 10})


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.pinterest = _FakePinterest()
        self.pins = _FakePins()
        self.webhooks = _FakeWebhooks()
        self.billing = _FakeBilling()
        self.rate_meter = _FakeRateMeter()

    async def aclose(self) -> None:
        return None

    async def request(self, method: str, path: str, *, params: dict | None = None) -> _FakeResponse:
        if method == "GET" and path == "/v1/activity-logs":
            return _FakeResponse(
                {"items": [{"id": "log_1", **(params or {})}], "next_cursor": None}
            )
        raise AssertionError(f"Unexpected request: {method} {path}")


def test_service_uses_bound_request_api_key() -> None:
    service = PinBridgeService(Settings(), client_factory=_FakeClient)
    token = bind_current_api_key("pb_test_123")

    async def run() -> None:
        accounts = await service.list_pinterest_accounts()
        assert accounts == [{"id": "acct_1", "username": "demo"}]

    try:
        asyncio.run(run())
    finally:
        reset_current_api_key(token)


def test_service_related_terms_normalizes_string_input() -> None:
    service = PinBridgeService(
        Settings(pinbridge_api_key="pb_local_key"),
        client_factory=_FakeClient,
    )

    async def run() -> None:
        response = await service.list_related_terms(
            account_id="acct_1",
            terms="home decor,  kitchen",
            exact_match=True,
        )
        assert response["related_term_count"] == 2
        assert response["exact_match"] is True

    asyncio.run(run())


def test_service_activity_logs_uses_raw_request_for_sdk_compatibility() -> None:
    service = PinBridgeService(
        Settings(pinbridge_api_key="pb_local_key"),
        client_factory=_FakeClient,
    )

    async def run() -> None:
        response = await service.list_activity_logs(
            limit=5,
            category="publishing",
            status="success",
            since="2026-03-30T12:00:00Z",
        )
        assert response["items"][0]["limit"] == 5
        assert response["items"][0]["category"] == "publishing"
        assert response["items"][0]["status"] == "success"
        assert response["items"][0]["since"] == "2026-03-30T12:00:00+00:00"

    asyncio.run(run())
