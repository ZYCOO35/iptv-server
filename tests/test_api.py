from pathlib import Path

import httpx

import app.main as main_module
from app.services.config_manager import ChannelConfig


async def test_playlist_channels_health_and_direct_redirect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """version: 1
server:
  public_base_url: http://iptv.test:8889
channels:
  - id: proxy
    name: Proxy Channel
    url: https://origin.example/index.m3u8?secret=value
    mode: proxy
    enabled: true
    sort_order: 20
    headers:
      Cookie: secret-cookie
  - id: direct
    name: Direct Channel
    url: https://direct.example/live.m3u8
    mode: direct
    enabled: true
    sort_order: 10
  - id: disabled
    name: Disabled
    url: https://example.com/off.m3u8
    enabled: false
""",
        encoding="utf-8",
    )
    manager = ChannelConfig(path, poll_interval=60)
    monkeypatch.setattr(main_module, "channel_config", manager)

    async with main_module.lifespan(main_module.app):
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["channel_count"] == 2

            playlist = await client.get("/playlist.m3u8")
            alias = await client.get("/playlist.m3u")
            assert playlist.status_code == 200
            assert alias.text == playlist.text
            assert "http://iptv.test:8889/proxy/proxy/index.m3u8" in playlist.text
            assert "https://direct.example/live.m3u8" in playlist.text
            assert "Disabled" not in playlist.text

            channels = (await client.get("/channels.json")).json()
            assert [channel["id"] for channel in channels] == ["direct", "proxy"]
            proxy = channels[1]
            assert proxy["play_url"] == "http://iptv.test:8889/proxy/proxy/index.m3u8"
            assert "url" not in proxy
            assert "headers" not in proxy
            assert "secret" not in str(proxy)

            redirect = await client.get("/proxy/direct/index.m3u8", follow_redirects=False)
            assert redirect.status_code == 307
            assert redirect.headers["location"] == "https://direct.example/live.m3u8"

            assert (await client.get("/favicon.ico")).status_code == 204
