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
    # The PinBridge web app, for links people open (connect Pinterest, pricing).
    app_base_url: str = "https://app.pinbridge.io"
    pinbridge_api_key: str | None = None
    verify_incoming_api_keys: bool = True
    auth_cache_ttl_seconds: int = 60
    host: str = "127.0.0.1"
    port: int = 57289
    streamable_http_path: str = "/"
    enable_write_tools: bool = False
    log_level: str = "INFO"
    min_plan: str = "free"

    # Weekly assistant-request quota, counted per tool call through the API (db-backed)
    enable_quota: bool = True

    # Google Connected Console domain check, served at /.well-known/google-connected-apps.
    # Comma-separated tokens from the console's google-connected-apps.json. Empty = 404.
    google_connected_apps_tokens: str = ""

    @property
    def normalized_public_base_url(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def normalized_app_base_url(self) -> str:
        return self.app_base_url.rstrip("/")

    @property
    def google_connected_apps_token_list(self) -> list[str]:
        return [t.strip() for t in self.google_connected_apps_tokens.split(",") if t.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
