import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from app.models.channel import Channel, ProxyConfig

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')
RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
}

_client_session: aiohttp.ClientSession | None = None
_signing_key = secrets.token_bytes(32)


def redact_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    query = "<redacted>" if parsed.query else ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


async def init_session() -> aiohttp.ClientSession:
    global _client_session
    if _client_session is None or _client_session.closed:
        _client_session = aiohttp.ClientSession()
    return _client_session


async def close_session() -> None:
    global _client_session
    if _client_session is not None and not _client_session.closed:
        await _client_session.close()
    _client_session = None


class ProxyStream:
    def __init__(self, response: aiohttp.ClientResponse):
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.response.content.iter_chunked(CHUNK_SIZE):
                yield chunk
        finally:
            self.response.close()


class ProxyService:
    @staticmethod
    def sign_target(channel_id: str, target_url: str) -> tuple[str, str]:
        encoded = base64.urlsafe_b64encode(target_url.encode("utf-8")).rstrip(b"=").decode()
        payload = f"{channel_id}\0{encoded}".encode()
        signature = hmac.new(_signing_key, payload, hashlib.sha256).hexdigest()
        return encoded, signature

    @staticmethod
    def verify_target(channel_id: str, encoded_url: str, signature: str) -> str:
        payload = f"{channel_id}\0{encoded_url}".encode()
        expected = hmac.new(_signing_key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="Invalid proxy resource signature")
        try:
            padding = "=" * (-len(encoded_url) % 4)
            target_url = base64.urlsafe_b64decode(encoded_url + padding).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid proxy resource URL") from exc
        parsed = urlsplit(target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Invalid proxy resource URL")
        return target_url

    @classmethod
    def resource_url(
        cls,
        server_base: str,
        channel_id: str,
        target_url: str,
    ) -> str:
        encoded, signature = cls.sign_target(channel_id, target_url)
        return (
            f"{server_base.rstrip('/')}/proxy/{channel_id}/resource"
            f"?url={encoded}&sig={signature}"
        )

    @classmethod
    def transform_manifest(
        cls,
        content: str,
        source_url: str,
        server_base: str,
        channel_id: str,
    ) -> str:
        def rewrite_uri(uri: str) -> str:
            absolute = urljoin(source_url, uri)
            if urlsplit(absolute).scheme not in {"http", "https"}:
                return uri
            return cls.resource_url(server_base, channel_id, absolute)

        transformed: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                transformed.append(line)
            elif stripped.startswith("#"):
                transformed.append(
                    URI_ATTRIBUTE.sub(lambda match: f'URI="{rewrite_uri(match.group(1))}"', line)
                )
            else:
                transformed.append(rewrite_uri(stripped))
        return "\n".join(transformed) + ("\n" if content.endswith(("\n", "\r")) else "")

    @classmethod
    async def fetch(
        cls,
        *,
        upstream_url: str,
        server_base: str,
        channel: Channel,
        proxy_config: ProxyConfig,
        request_headers: Mapping[str, str],
        method: str = "GET",
        force_playlist: bool = False,
    ) -> Response:
        session = await init_session()
        headers = cls._build_request_headers(proxy_config, channel, request_headers)
        timeout = aiohttp.ClientTimeout(
            total=proxy_config.total_timeout_seconds,
            connect=proxy_config.connect_timeout_seconds,
            sock_connect=proxy_config.connect_timeout_seconds,
            sock_read=proxy_config.read_timeout_seconds,
        )

        logger.info("[Proxy] %s %s", method, redact_url(upstream_url))
        try:
            response = await session.request(method, upstream_url, headers=headers, timeout=timeout)
        except TimeoutError as exc:
            logger.warning("[Proxy] Upstream timeout: %s", redact_url(upstream_url))
            raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
        except aiohttp.ClientError as exc:
            logger.warning("[Proxy] Upstream request failed: %s", exc)
            raise HTTPException(status_code=502, detail="Upstream request failed") from exc

        response_headers = cls._response_headers(response)
        if method == "HEAD":
            response.close()
            return Response(status_code=response.status, headers=response_headers)

        is_success = 200 <= response.status < 300
        if is_success and (force_playlist or cls._is_hls_playlist(response, upstream_url)):
            try:
                raw_content = await response.text(encoding="utf-8", errors="replace")
            finally:
                response.close()
            transformed = cls.transform_manifest(
                raw_content,
                source_url=upstream_url,
                server_base=server_base,
                channel_id=channel.id,
            )
            response_headers.pop("content-length", None)
            response_headers.pop("content-range", None)
            response_headers.pop("content-type", None)
            return PlainTextResponse(
                transformed,
                status_code=response.status,
                media_type="application/vnd.apple.mpegurl",
                headers=response_headers,
            )

        if not is_success:
            try:
                body = await response.read()
            finally:
                response.close()
            return Response(content=body, status_code=response.status, headers=response_headers)

        return StreamingResponse(
            ProxyStream(response),
            status_code=response.status,
            headers=response_headers,
            media_type=None if "content-type" in response_headers else "application/octet-stream",
        )

    @staticmethod
    def _build_request_headers(
        proxy_config: ProxyConfig,
        channel: Channel,
        request_headers: Mapping[str, str],
    ) -> dict[str, str]:
        headers: dict[str, str] = {}

        def set_header(name: str, value: str) -> None:
            existing = next((key for key in headers if key.lower() == name.lower()), None)
            if existing is not None:
                del headers[existing]
            headers[name] = value

        for name, value in proxy_config.headers.items():
            set_header(name, value)
        for name in proxy_config.forward_request_headers:
            value = request_headers.get(name)
            if value:
                set_header(name, value)
        for name, value in channel.headers.items():
            set_header(name, value)
        return headers

    @staticmethod
    def _response_headers(response: aiohttp.ClientResponse) -> dict[str, str]:
        has_encoding = "Content-Encoding" in response.headers
        result: dict[str, str] = {}
        for name, value in response.headers.items():
            normalized = name.lower()
            if normalized in RESPONSE_HEADERS:
                if has_encoding and normalized == "content-length":
                    continue
                result[normalized] = value
        return result

    @staticmethod
    def _is_hls_playlist(response: aiohttp.ClientResponse, url: str) -> bool:
        content_type = response.headers.get("Content-Type", "").lower()
        path = urlsplit(url).path.lower()
        return "mpegurl" in content_type or path.endswith((".m3u", ".m3u8"))
