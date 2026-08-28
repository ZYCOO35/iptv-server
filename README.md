# IPTV Server

一个用 YAML 管理频道的 IPTV 播放列表和 HLS 代理服务。支持多级 HLS 清单、
加密密钥、TS/fMP4 分片、Range 请求和配置热更新。

## 使用 Docker Compose

先下载项目并准备配置文件：

```bash
git clone https://github.com/MrSibe/iptv-server.git
cd iptv-server
cp config/config.example.yaml config/config.yaml
vi config/config.yaml
```

启动服务：

```bash
docker compose up -d
docker compose ps
```

默认监听 `8889` 端口。播放器订阅地址：

```text
http://服务器地址:8889/playlist.m3u8
```

查看日志：

```bash
docker compose logs -f
```

停止服务：

```bash
docker compose down
```

## 使用 docker run

如果不使用 Compose，可以直接运行发布在 GHCR 的镜像：

```bash
mkdir -p config
curl -fsSL \
  https://raw.githubusercontent.com/MrSibe/iptv-server/main/config/config.example.yaml \
  -o config/config.yaml
vi config/config.yaml

docker run -d \
  --name iptv-server \
  --restart unless-stopped \
  -p 8889:8889 \
  -e TZ=Asia/Shanghai \
  -v "$(pwd)/config/config.yaml:/app/config/config.yaml:ro" \
  ghcr.io/mrsibe/iptv-server:latest
```

## 配置频道

`config/config.yaml` 的基本结构如下：

```yaml
version: 1

server:
  public_base_url: null

proxy:
  connect_timeout_seconds: 10
  read_timeout_seconds: 30
  total_timeout_seconds: 120
  forward_request_headers:
    - range
    - user-agent
  headers: {}

channels:
  - id: cctv1
    name: CCTV-1
    url: https://example.com/live/index.m3u8
    mode: proxy
    group: 央视
    logo: ""
    enabled: true
    sort_order: 10
    headers:
      Referer: https://example.com/
```

频道 `id` 只能包含字母、数字、下划线和连字符，并且不能重复。`url` 只接受
HTTP 或 HTTPS 地址。

`mode: proxy` 会通过本服务转发 HLS 请求；`mode: direct` 会把源地址直接交给播放器。
需要 Referer、User-Agent、Cookie 或 Authorization 的频道，可以在该频道的 `headers`
中填写。

保存配置后，服务会自动加载新内容。配置有误时会继续使用上一份有效配置，详情可以从
容器日志和 `/health` 查看。

## 使用环境变量

YAML 字符串支持 `${ENV_NAME}`：

```yaml
url: https://example.com/live/index.m3u8?token=${IPTV_TOKEN}
headers:
  Cookie: session=${IPTV_SESSION}
```

使用 `docker run` 时传入变量：

```bash
docker run -d \
  --name iptv-server \
  --restart unless-stopped \
  -p 8889:8889 \
  -e IPTV_TOKEN="your-token" \
  -e IPTV_SESSION="your-session" \
  -v "$(pwd)/config/config.yaml:/app/config/config.yaml:ro" \
  ghcr.io/mrsibe/iptv-server:latest
```

使用 Compose 时，在 `docker-compose.yml` 的 `environment` 中添加同名变量。
配置引用了不存在的环境变量时，服务会拒绝启动。

## 外部访问地址

服务位于反向代理后面，或者播放器使用的地址与请求 Host 不同时，设置：

```yaml
server:
  public_base_url: https://iptv.example.com
```

## 更新

`latest` 指向最新稳定版本。也可以使用 `1`、`1.0` 或 `1.0.0` 这类固定版本标签。

更新 Compose 部署：

```bash
docker compose pull
docker compose up -d
```

更新 `docker run` 部署：

```bash
docker pull ghcr.io/mrsibe/iptv-server:latest
docker rm -f iptv-server
```

然后重新执行前面的 `docker run` 命令。删除容器不会删除宿主机上的
`config/config.yaml`。

## 接口

- `/playlist.m3u8`：M3U 播放列表
- `/playlist.m3u`：兼容播放列表地址
- `/channels.json`：频道信息和播放地址
- `/health`：配置状态和有效频道数量
- `/docs`：OpenAPI 文档
