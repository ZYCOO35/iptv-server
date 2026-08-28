import asyncio
from pathlib import Path

import pytest

from app.services.config_loader import ConfigLoadError, load_config
from app.services.config_manager import ChannelConfig


def write_config(path: Path, channel_name: str = "News", extra: str = "") -> None:
    path.write_text(
        f"""version: 1
server:
  public_base_url: null
proxy:
  headers: {{}}
channels:
  - id: news
    name: {channel_name}
    url: https://example.com/live/index.m3u8
    enabled: true
    headers: {{}}
{extra}""",
        encoding="utf-8",
    )


def test_load_config_expands_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("CHANNEL_TOKEN", "secret:#value")
    path.write_text(
        """version: 1
channels:
  - id: news
    name: News
    url: https://example.com/index.m3u8?token=${CHANNEL_TOKEN}
""",
        encoding="utf-8",
    )

    config, revision = load_config(path)

    assert config.channels[0].url.endswith("token=secret:#value")
    assert len(revision) == 64


def test_load_config_rejects_missing_environment(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """version: 1
channels:
  - id: news
    name: News
    url: https://example.com/index.m3u8?token=${MISSING_TOKEN}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError, match="MISSING_TOKEN"):
        load_config(path)


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        (
            """  - id: news
    name: Duplicate
    url: https://example.com/duplicate.m3u8
""",
            "duplicate channel ids",
        ),
        ("", "header 'Host' is not allowed"),
    ],
)
def test_load_config_rejects_duplicate_ids_and_blocked_headers(
    tmp_path: Path,
    fragment: str,
    message: str,
) -> None:
    path = tmp_path / "config.yaml"
    if fragment:
        write_config(path, extra=fragment)
    else:
        path.write_text(
            """version: 1
proxy:
  headers:
    Host: internal.example
channels: []
""",
            encoding="utf-8",
        )

    with pytest.raises(ConfigLoadError, match=message):
        load_config(path)


def test_load_config_rejects_invalid_url_and_id(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """version: 1
channels:
  - id: bad/id
    name: Bad
    url: file:///tmp/stream.m3u8
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError) as error:
        load_config(path)
    assert "letters, digits" in str(error.value)
    assert "http or https" in str(error.value)


async def test_manager_keeps_last_good_config_after_failed_reload(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    manager = ChannelConfig(path, poll_interval=60)
    await manager.start()
    try:
        old_revision = manager.revision
        path.write_text("version: 1\nchannels: [", encoding="utf-8")
        assert await manager.reload() is False
        assert manager.status == "degraded"
        assert manager.channels[0].name == "News"
        assert manager.revision == old_revision

        write_config(path)
        assert await manager.reload() is False
        assert manager.status == "ok"

        path.write_text("version: 1\nchannels: [", encoding="utf-8")
        assert await manager.reload() is False
        write_config(path, channel_name="Updated")
        assert await manager.reload() is True
        assert manager.status == "ok"
        assert manager.channels[0].name == "Updated"
        assert manager.revision != old_revision
    finally:
        await manager.stop()


async def test_manager_start_rejects_invalid_initial_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("channels: []\n", encoding="utf-8")
    manager = ChannelConfig(path)

    with pytest.raises(ConfigLoadError):
        await manager.start()


async def test_manager_watcher_applies_valid_file_change(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    manager = ChannelConfig(path, poll_interval=0.01)
    await manager.start()
    try:
        write_config(path, channel_name="Watched")
        await asyncio.sleep(0.7)
        assert manager.channels[0].name == "Watched"
        assert manager.status == "ok"
    finally:
        await manager.stop()
