import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CHANNEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
BLOCKED_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _validate_single_line(value: str, field_name: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} must not contain newlines")
    return value


def _validate_http_url(value: str) -> str:
    _validate_single_line(value, "URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https and include a host")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    return value


def _validate_headers(headers: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        clean_name = _validate_single_line(name.strip(), "header name")
        clean_value = _validate_single_line(value, f"header {clean_name}")
        if not clean_name:
            raise ValueError("header name must not be empty")
        if clean_name.lower() in BLOCKED_HEADERS:
            raise ValueError(f"header {clean_name!r} is not allowed")
        normalized[clean_name] = clean_value
    return normalized

class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    mode: Literal["proxy", "direct"] = "proxy"
    group: str = "Default"
    logo: str = ""
    enabled: bool = True
    sort_order: int = 0
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not CHANNEL_ID_PATTERN.fullmatch(value):
            raise ValueError("must contain only letters, digits, underscores, or hyphens")
        return value

    @field_validator("name", "group", "logo")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validate_single_line(value, "text field")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_headers(value)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_base_url: str | None = None

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_http_url(value).rstrip("/")


class ProxyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_timeout_seconds: float = Field(default=10, gt=0)
    read_timeout_seconds: float = Field(default=30, gt=0)
    total_timeout_seconds: float = Field(default=120, gt=0)
    forward_request_headers: list[str] = Field(default_factory=lambda: ["range", "user-agent"])
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("forward_request_headers")
    @classmethod
    def validate_forward_headers(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for name in value:
            normalized = _validate_single_line(name.strip().lower(), "header name")
            if not normalized:
                raise ValueError("header name must not be empty")
            if normalized in BLOCKED_HEADERS:
                raise ValueError(f"header {name!r} is not allowed")
            if normalized not in result:
                result.append(normalized)
        return result

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_headers(value)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    server: ServerConfig = Field(default_factory=ServerConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    channels: list[Channel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_channel_ids(self) -> "AppConfig":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for channel in self.channels:
            if channel.id in seen:
                duplicates.add(channel.id)
            seen.add(channel.id)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate channel ids: {names}")
        return self
