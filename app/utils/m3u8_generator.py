from app.models.channel import Channel


class M3U8Generator:
    def generate_m3u8(self, channels: tuple[Channel, ...], server_base: str) -> str:
        lines = ["#EXTM3U"]
        for ch in channels:
            line = self._format_channel(ch, server_base)
            lines.append(line)
        return "\n".join(lines) + "\n"

    def _format_channel(self, channel: Channel, server_base: str) -> str:
        logo = channel.logo.replace('"', "'")
        group = channel.group.replace('"', "'")
        name = channel.name.replace(",", "，")
        if channel.logo:
            extinf = f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{name}'
        else:
            extinf = f'#EXTINF:-1 group-title="{group}",{name}'

        if channel.mode == "proxy":
            final_url = f"{server_base.rstrip('/')}/proxy/{channel.id}/index.m3u8"
        else:
            final_url = channel.url

        return f"{extinf}\n{final_url}"
