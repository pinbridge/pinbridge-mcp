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

    @property
    def normalized_public_base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
