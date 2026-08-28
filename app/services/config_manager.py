import asyncio
import hashlib
import logging
import os
from pathlib import Path

from app.models.channel import AppConfig, Channel
from app.services.config_loader import ConfigLoadError, load_config

logger = logging.getLogger(__name__)


class ChannelConfig:
    def __init__(self, path: Path | None = None, poll_interval: float = 1.0):
        configured_path = path or Path(os.getenv("IPTV_CONFIG_PATH", "config/config.yaml"))
        self._path = configured_path.resolve()
        self._poll_interval = poll_interval
        self._config: AppConfig | None = None
        self._channels: tuple[Channel, ...] = ()
        self._channel_by_id: dict[str, Channel] = {}
        self._revision = ""
        self._observed_revision = ""
        self._last_error: str | None = None
        self._poll_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        config, revision = load_config(self._path)
        self._apply(config, revision)
        self._poll_task = asyncio.create_task(self._watch_loop())
        logger.info(
            "[Config] Loaded %d enabled channels from %s (revision=%s)",
            len(self._channels),
            self._path,
            revision[:8],
        )

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("[Config] Watcher stopped")

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            raise RuntimeError("configuration has not been loaded")
        return self._config

    @property
    def channels(self) -> tuple[Channel, ...]:
        return self._channels

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def status(self) -> str:
        return "degraded" if self._last_error else "ok"

    def get_channels_by_group(self, group_name: str) -> list[Channel]:
        return [channel for channel in self._channels if channel.group == group_name]

    def get_channel_by_id(self, channel_id: str) -> Channel | None:
        return self._channel_by_id.get(channel_id)

    @property
    def total_count(self) -> int:
        return len(self._channels)

    def _apply(self, config: AppConfig, revision: str) -> None:
        enabled = tuple(
            sorted(
                (channel for channel in config.channels if channel.enabled),
                key=lambda channel: (channel.sort_order, channel.id),
            )
        )
        self._config = config
        self._channels = enabled
        self._channel_by_id = {channel.id: channel for channel in enabled}
        self._revision = revision
        self._observed_revision = revision
        self._last_error = None

    async def reload(self) -> bool:
        try:
            config, revision = load_config(self._path)
        except ConfigLoadError as exc:
            self._last_error = str(exc)
            logger.error("[Config] Reload rejected; keeping previous config: %s", exc)
            return False

        if revision == self._revision:
            self._observed_revision = revision
            self._last_error = None
            return False

        self._apply(config, revision)
        logger.info(
            "[Config] Reloaded %d enabled channels (revision=%s)",
            len(self._channels),
            revision[:8],
        )
        return True

    async def _watch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                try:
                    observed = hashlib.sha256(self._path.read_bytes()).hexdigest()
                except OSError as exc:
                    message = f"cannot read config {self._path}: {exc}"
                    if message != self._last_error:
                        logger.error("[Config] %s; keeping previous config", message)
                    self._last_error = message
                    continue

                if observed == self._observed_revision:
                    continue
                self._observed_revision = observed
                await asyncio.sleep(0.5)
                await self.reload()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Config] Unexpected watcher error")


channel_config = ChannelConfig()
