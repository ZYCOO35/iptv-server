from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app.services.proxy_service import ProxyService, redact_url


def decode_proxy_url(proxy_url: str, channel_id: str = "demo") -> str:
    query = parse_qs(urlsplit(proxy_url).query)
    return ProxyService.verify_target(channel_id, query["url"][0], query["sig"][0])


def test_transform_manifest_rewrites_nested_and_attribute_uris() -> None:
    source = "https://origin.example/live/master/index.m3u8?auth=abc"
    manifest = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,URI="../audio/track.m3u8?token=1"
#EXT-X-KEY:METHOD=AES-128,URI="/keys/live.key"
#EXT-X-MAP:URI="init.mp4"
#EXT-X-STREAM-INF:BANDWIDTH=1000000
variants/high/index.m3u8?quality=high
../segment.ts?part=2
https://cdn.example/chunk.m4s?sig=xyz
"""

    transformed = ProxyService.transform_manifest(
        manifest,
        source_url=source,
        server_base="http://iptv.local:8889",
        channel_id="demo",
    )

    resource_urls: list[str] = []
    for line in transformed.splitlines():
        if 'URI="' in line:
            resource_urls.append(line.split('URI="', 1)[1].split('"', 1)[0])
        elif line and not line.startswith("#"):
            resource_urls.append(line)

    assert [decode_proxy_url(url) for url in resource_urls] == [
        "https://origin.example/live/audio/track.m3u8?token=1",
        "https://origin.example/keys/live.key",
        "https://origin.example/live/master/init.mp4",
        "https://origin.example/live/master/variants/high/index.m3u8?quality=high",
        "https://origin.example/live/segment.ts?part=2",
        "https://cdn.example/chunk.m4s?sig=xyz",
    ]


def test_proxy_signature_is_bound_to_channel_and_target() -> None:
    encoded, signature = ProxyService.sign_target("one", "https://example.com/segment.ts")
    assert ProxyService.verify_target("one", encoded, signature).endswith("segment.ts")

    with pytest.raises(HTTPException) as error:
        ProxyService.verify_target("two", encoded, signature)
    assert error.value.status_code == 403

    with pytest.raises(HTTPException) as error:
        ProxyService.verify_target("one", encoded, "0" * 64)
    assert error.value.status_code == 403


def test_redact_url_removes_query_and_fragment() -> None:
    redacted = redact_url("https://user:pass@example.com/live.m3u8?token=secret#part")
    assert "token=secret" not in redacted
    assert "user" not in redacted
    assert "pass" not in redacted
    assert "#part" not in redacted
    assert redacted.endswith("?<redacted>")
