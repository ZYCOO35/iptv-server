import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.middleware.cors import CORSMiddleware

from app.services.config_manager import channel_config
from app.services.proxy_service import ProxyService, close_session, init_session
from app.utils.m3u8_generator import M3U8Generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

m3u8_generator = M3U8Generator()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_session()
    try:
        await channel_config.start()
        logger.info("[Server] IPTV server started")
        yield
    finally:
        await channel_config.stop()
        await close_session()
        logger.info("[Server] IPTV server stopped")


app = FastAPI(
    title="IPTV Server",
    description="YAML-configured IPTV playlist and HLS proxy server",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD"],
    allow_headers=["*"],
)


def _server_base(request: Request) -> str:
    configured = channel_config.config.server.public_base_url
    return configured or str(request.base_url).rstrip("/")


def _play_url(channel_id: str, request: Request) -> str:
    return f"{_server_base(request)}/proxy/{channel_id}/index.m3u8"


@app.get("/", tags=["welcome"])
async def root() -> dict[str, object]:
    return {
        "message": "Welcome to IPTV Server!",
        "endpoints": {
            "health_check": "/health",
            "playlist_m3u8": "/playlist.m3u8",
            "playlist_m3u": "/playlist.m3u",
            "playlist_json": "/channels.json",
            "api_docs": "/docs",
        },
        "version": "1.0.0",
    }


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, object]:
    return {
        "status": channel_config.status,
        "config_loaded": True,
        "config_version": channel_config.config.version,
        "config_revision": channel_config.revision[:12],
        "channel_count": channel_config.total_count,
    }


@app.get("/playlist.m3u8", tags=["playlist"])
@app.get("/playlist.m3u", tags=["playlist"], include_in_schema=False)
async def get_playlist(request: Request) -> PlainTextResponse:
    content = m3u8_generator.generate_m3u8(channel_config.channels, _server_base(request))
    return PlainTextResponse(content=content, media_type="audio/x-mpegurl")


@app.get("/channels.json", tags=["playlist"])
async def get_channels_json(request: Request) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for channel in channel_config.channels:
        play_url = channel.url if channel.mode == "direct" else _play_url(channel.id, request)
        result.append(
            {
                "id": channel.id,
                "name": channel.name,
                "mode": channel.mode,
                "group": channel.group,
                "logo": channel.logo,
                "enabled": channel.enabled,
                "sort_order": channel.sort_order,
                "play_url": play_url,
            }
        )
    return result


def _channel_or_404(channel_id: str):
    channel = channel_config.get_channel_by_id(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    return channel


@app.api_route("/proxy/{channel_id}", methods=["GET", "HEAD"], tags=["proxy"])
@app.api_route(
    "/proxy/{channel_id}/index.m3u8",
    methods=["GET", "HEAD"],
    tags=["proxy"],
)
async def proxy_playlist(channel_id: str, request: Request) -> Response:
    channel = _channel_or_404(channel_id)
    if channel.mode == "direct":
        return RedirectResponse(channel.url, status_code=307)
    return await ProxyService.fetch(
        upstream_url=channel.url,
        server_base=_server_base(request),
        channel=channel,
        proxy_config=channel_config.config.proxy,
        request_headers=request.headers,
        method=request.method,
        force_playlist=True,
    )


@app.api_route(
    "/proxy/{channel_id}/resource",
    methods=["GET", "HEAD"],
    tags=["proxy"],
)
async def proxy_resource(
    channel_id: str,
    request: Request,
    url: str,
    sig: str,
) -> Response:
    channel = _channel_or_404(channel_id)
    if channel.mode != "proxy":
        raise HTTPException(status_code=404, detail="Proxy resource not found")
    upstream_url = ProxyService.verify_target(channel_id, url, sig)
    return await ProxyService.fetch(
        upstream_url=upstream_url,
        server_base=_server_base(request),
        channel=channel,
        proxy_config=channel_config.config.proxy,
        request_headers=request.headers,
        method=request.method,
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)
