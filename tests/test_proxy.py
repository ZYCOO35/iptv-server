import asyncio

import aiohttp
import pytest
from aiohttp import web
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

import app.services.proxy_service as proxy_module
from app.models.channel import Channel, ProxyConfig
from app.services.proxy_service import ProxyService, ProxyStream


@pytest.fixture
async def upstream_server(unused_tcp_port: int):
    async def playlist(_: web.Request) -> web.Response:
        return web.Response(
            text="#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key\"\nsegment.ts?token=one\n",
            content_type="application/vnd.apple.mpegurl",
        )

    async def nested(_: web.Request) -> web.Response:
        return web.Response(text="#EXTM3U\n../segment.ts\n", content_type="audio/mpegurl")

    async def segment(request: web.Request) -> web.Response:
        if request.method == "HEAD":
            return web.Response(headers={"Content-Length": "5", "ETag": "stream-v1"})
        if request.headers.get("Range") == "bytes=1-3":
            return web.Response(
                body=b"234",
                status=206,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Range": "bytes 1-3/5",
                    "ETag": "stream-v1",
                },
            )
        return web.Response(body=b"12345", headers={"Accept-Ranges": "bytes"})

    async def error(_: web.Request) -> web.Response:
        return web.Response(text="missing", status=404)

    async def slow(_: web.Request) -> web.Response:
        await asyncio.sleep(0.2)
        return web.Response(text="late")

    application = web.Application()
    application.router.add_get("/playlist.m3u8", playlist)
    application.router.add_get("/nested/child", nested)
    application.router.add_route("*", "/segment.ts", segment)
    application.router.add_get("/error", error)
    application.router.add_get("/slow", slow)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{unused_tcp_port}"
    finally:
        await runner.cleanup()


def proxy_channel(url: str) -> Channel:
    return Channel(id="demo", name="Demo", url=url, headers={"Referer": "https://player.test"})


async def response_body(response: StreamingResponse) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def test_fetch_transforms_playlist_and_nested_content(upstream_server: str) -> None:
    channel = proxy_channel(f"{upstream_server}/playlist.m3u8")

    response = await ProxyService.fetch(
        upstream_url=channel.url,
        server_base="http://iptv.test",
        channel=channel,
        proxy_config=ProxyConfig(),
        request_headers={"user-agent": "TestPlayer"},
        force_playlist=True,
    )

    assert isinstance(response, PlainTextResponse)
    assert response.status_code == 200
    content = response.body.decode()
    assert content.count("/proxy/demo/resource?") == 2

    encoded, signature = ProxyService.sign_target("demo", f"{upstream_server}/nested/child")
    nested_response = await ProxyService.fetch(
        upstream_url=ProxyService.verify_target("demo", encoded, signature),
        server_base="http://iptv.test",
        channel=channel,
        proxy_config=ProxyConfig(),
        request_headers={},
    )
    assert isinstance(nested_response, PlainTextResponse)
    assert "/proxy/demo/resource?" in nested_response.body.decode()


async def test_stream_preserves_range_status_and_headers(upstream_server: str) -> None:
    channel = proxy_channel(f"{upstream_server}/playlist.m3u8")
    response = await ProxyService.fetch(
        upstream_url=f"{upstream_server}/segment.ts",
        server_base="http://iptv.test",
        channel=channel,
        proxy_config=ProxyConfig(),
        request_headers={"range": "bytes=1-3"},
    )

    assert isinstance(response, StreamingResponse)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 1-3/5"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == "stream-v1"
    assert await response_body(response) == b"234"


async def test_head_and_upstream_error_are_preserved(upstream_server: str) -> None:
    channel = proxy_channel(f"{upstream_server}/playlist.m3u8")
    head = await ProxyService.fetch(
        upstream_url=f"{upstream_server}/segment.ts",
        server_base="http://iptv.test",
        channel=channel,
        proxy_config=ProxyConfig(),
        request_headers={},
        method="HEAD",
    )
    assert head.status_code == 200
    assert head.headers["content-length"] == "5"
    assert head.body == b""

    missing = await ProxyService.fetch(
        upstream_url=f"{upstream_server}/error",
        server_base="http://iptv.test",
        channel=channel,
        proxy_config=ProxyConfig(),
        request_headers={},
    )
    assert missing.status_code == 404
    assert missing.body == b"missing"


async def test_timeout_and_connection_failure_map_to_gateway_errors(
    upstream_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = proxy_channel(f"{upstream_server}/playlist.m3u8")
    short_timeout = ProxyConfig(
        connect_timeout_seconds=0.05,
        read_timeout_seconds=0.05,
        total_timeout_seconds=0.05,
    )
    with pytest.raises(HTTPException) as timeout_error:
        await ProxyService.fetch(
            upstream_url=f"{upstream_server}/slow",
            server_base="http://iptv.test",
            channel=channel,
            proxy_config=short_timeout,
            request_headers={},
        )
    assert timeout_error.value.status_code == 504

    class FailingSession:
        closed = False

        async def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("connection refused")

        async def close(self) -> None:
            self.closed = True

    await proxy_module.close_session()
    monkeypatch.setattr(proxy_module, "_client_session", FailingSession())
    with pytest.raises(HTTPException) as connection_error:
        await ProxyService.fetch(
            upstream_url="http://127.0.0.1:9/unreachable",
            server_base="http://iptv.test",
            channel=channel,
            proxy_config=ProxyConfig(connect_timeout_seconds=0.05),
            request_headers={},
        )
    assert connection_error.value.status_code == 502


def test_channel_headers_override_forwarded_and_global_headers() -> None:
    config = ProxyConfig(
        headers={"User-Agent": "Global", "X-Global": "yes"},
        forward_request_headers=["range", "user-agent"],
    )
    channel = Channel(
        id="demo",
        name="Demo",
        url="https://example.com/index.m3u8",
        headers={"User-Agent": "Channel", "Referer": "https://player.test"},
    )

    headers = ProxyService._build_request_headers(
        config,
        channel,
        {"range": "bytes=0-", "user-agent": "Client"},
    )

    assert headers == {
        "X-Global": "yes",
        "range": "bytes=0-",
        "User-Agent": "Channel",
        "Referer": "https://player.test",
    }


async def test_proxy_stream_closes_upstream_when_consumer_stops() -> None:
    class Content:
        async def iter_chunked(self, _: int):
            yield b"first"
            yield b"second"

    class UpstreamResponse:
        content = Content()
        closed = False

        def close(self) -> None:
            self.closed = True

    upstream = UpstreamResponse()
    iterator = ProxyStream(upstream).__aiter__()
    assert await anext(iterator) == b"first"
    await iterator.aclose()
    assert upstream.closed is True
