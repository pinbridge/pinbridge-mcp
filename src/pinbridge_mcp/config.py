"""Configuration for the PinBridge MCP server."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed server settings."""

    model_config = SettingsConfigDict(
        env_prefix="PINBRIDGE_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_base_url: str = "http://127.0.0.1:57289"
    pinbridge_base_url: str = "https://api.pinbridge.io"
    pinbridge_api_key: str | None = None
    verify_incoming_api_keys: bool = True
    auth_cache_ttl_seconds: int = 60
    host: str = "127.0.0.1"
    port: int = 57289
    streamable_http_path: str = "/"
    enable_write_tools: bool = False
    log_level: str = "INFO"
    min_plan: str = "free"

    # Quota — weekly request limits per plan (0 = unlimited)
    enable_quota: bool = True
    quota_free: int = 100
    quota_starter: int = 500
    quota_growth: int = 2000
    quota_pro: int = 10000
    quota_enterprise: int = 0  # unlimited

    @property
    def plan_weekly_limits(self) -> dict[str, int]:
        return {
            "free": self.quota_free,
            "playground": self.quota_free,
            "starter": self.quota_starter,
            "growth": self.quota_growth,
            "pro": self.quota_pro,
            "enterprise": self.quota_enterprise,
        }

    @property
    def normalized_public_base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
