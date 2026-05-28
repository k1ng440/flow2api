# Flow2API

<div align="center">

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.119.0-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)

**A fully-featured OpenAI-compatible API service providing a unified interface for Flow**

</div>

## ✨ Core Features

- 🎨 **Text-to-Image** / **Image-to-Image**
- 🎬 **Text-to-Video** / **Image-to-Video**
- 🎞️ **First/Last Frame Video**
- 🔄 **AT/ST Auto-refresh** - AT refreshes automatically on expiry; ST refreshes via browser when expired (personal mode)
- 📊 **Balance Display** - Real-time query and display of VideoFX Credits
- 🚀 **Load Balancing** - Multi-token round-robin with concurrency control
- 🌐 **Proxy Support** - HTTP/SOCKS5 proxy with single URL, rotating proxy list file, and WARP auto-reconnect
- 📱 **Web Admin UI** - Intuitive token and configuration management
- 🎨 **Continuous image generation conversation**
- 🧩 **Gemini official request body compatible** - Supports `generateContent` / `streamGenerateContent`, `systemInstruction`, `contents.parts.text/inlineData/fileData`
- ✅ **Gemini official format tested and verified** - Confirmed with real tokens that `/models/{model}:generateContent` correctly returns `candidates[].content.parts[].inlineData`

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose (recommended)
- Or Python 3.8+

- Since Flow added extra CAPTCHAs, you can choose browser-based or third-party CAPTCHA solving:
Register at [YesCaptcha](https://yescaptcha.com/i/13Xd8K) to get an API key, and enter it in the system config page under ```YesCaptcha API Key```
- YesCaptcha supports switching `type` in the admin page: `RecaptchaV3TaskProxyless`, `RecaptchaV3TaskProxylessM1`, `RecaptchaV3TaskProxylessM1S7`, `RecaptchaV3TaskProxylessM1S9`; S7/S9 force-submit `minScore` 0.7/0.9.
- The default `docker-compose.yml` is recommended with third-party CAPTCHA solvers (yescaptcha/capmonster/ezcaptcha/capsolver).
For headed browser CAPTCHA inside Docker (browser/personal mode), use `docker-compose.headed.yml` below.

- Auto-update ST browser extension: [Flow2API-Token-Updater](https://github.com/TheSmallHanCat/Flow2API-Token-Updater)

### Method 1: Docker Deployment (Recommended)

#### Standard Mode (no proxy)

```bash
# Clone the repository
git clone https://github.com/TheSmallHanCat/flow2api.git
cd flow2api

# Start the service
docker-compose up -d

# View logs
docker-compose logs -f
```

> Note: Compose mounts `./tmp:/app/tmp` by default. Setting cache timeout to `0` means “never auto-expire/delete”; to retain cached files after container rebuild, keep this `tmp` mount.

#### WARP Mode (with proxy)

```bash
# Start with WARP proxy
docker-compose -f docker-compose.warp.yml up -d

# View logs
docker-compose -f docker-compose.warp.yml logs -f
```

#### Docker Headed Browser Mode (browser / personal)

> For scenarios requiring a virtualized desktop to enable headed browser CAPTCHA solving inside a container.  
> This mode starts `Xvfb + Fluxbox` by default for in-container display, and sets `ALLOW_DOCKER_HEADED_CAPTCHA=true`.  
> Only the application port is exposed; no remote desktop ports are opened.
> The `personal` built-in browser now starts in headed mode by default; to temporarily switch back to headless, set `PERSONAL_BROWSER_HEADLESS=true`.

```bash
# Start headed mode (first run: include --build)
docker compose -f docker-compose.headed.yml up -d --build

# View logs
docker compose -f docker-compose.headed.yml logs -f
```

- API port: `8000`
- After entering the admin panel, set the CAPTCHA method to `browser` or `personal`

### Method 2: Local Deployment

```bash
# Clone the repository
git clone https://github.com/TheSmallHanCat/flow2api.git
cd flow2api

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the service
python main.py
```

### First Access

After the service starts, visit the admin panel at **http://localhost:8000**. Change your password immediately after first login!

- **Username**: `admin`
- **Password**: `admin`

## 🌐 Proxy Configuration

Configure proxies in the admin panel under **Settings → Proxy**.

### Single Proxy URL

Enter one proxy URL directly. Supports HTTP and SOCKS5:

```
http://user:pass@host:port
socks5://user:pass@host:port
host:port:user:pass
```

### Proxy List File (Rotating)

Point **Proxy List File** to a file containing one proxy per line. A random proxy is selected per request. The file is hot-reloaded every 30 seconds — no restart needed.

```
# /etc/flow2api/proxies.txt
# Lines starting with # are ignored

http://user:pass@gate.proxy-cheap.com:31112
socks5://user:pass@gate.proxy-cheap.com:31113
gate.proxy-cheap.com:31114:user:pass

# proxybroker2 format also supported:
<Proxy US 0.18s [HTTP: High] 143.42.66.91:80>
<Proxy US 0.28s [SOCKS5: Anonymous] 34.44.49.215:1080>
```

> When a list file is configured it takes priority over the single proxy URL field.

#### Automatic Proxy Harvesting (`scripts/proxy_harvest.py`)

A self-contained harvester that finds high-anonymity proxies via [proxybroker2](https://github.com/bluet/proxybroker2), checks their reputation against ProxyCheck.io (blocks TOR exits, caches results 7 days), and validates each one by actually fetching Google Flow through it. Only proxies that pass all checks are written to the output file.

**Dependencies** (on the proxy host):
```bash
pip3 install proxybroker2 aiohttp
```

**Run manually:**
```bash
python3 scripts/proxy_harvest.py --limit 60 --output /opt/flow2api/data/proxies.txt
```

**Env vars:**
```
PROXYCHECK_KEY   ProxyCheck.io API key — raises daily limit from 100 to 1000 req/day
```

**Run automatically via systemd** (hourly timer, single-instance locked):
```ini
# /etc/systemd/system/proxybroker-find.service
[Unit]
Description=Proxy harvest + Google Flow validation
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/flow2api/scripts/proxy_harvest.py
TimeoutStartSec=600

# /etc/systemd/system/proxybroker-find.timer
[Timer]
OnBootSec=1min
OnUnitActiveSec=1h
Persistent=true
```

Then set **Proxy List File** to `/opt/flow2api/data/proxies.txt` in the admin panel.

**Important**: Use **sticky session** proxies (same IP for the duration of a request), not purely rotating ones. Both the reCAPTCHA solve and the API submission must reach the same egress IP, or Google will reject the token.

### Capsolver + Proxy (Recommended for API captcha)

When using Capsolver as the captcha method with a proxy configured, Flow2API automatically switches Capsolver to `ReCaptchaV3EnterpriseTask` (proxy-aware) and passes your proxy into the Capsolver task. This ensures Capsolver solves the captcha from the same IP that submits the API request, preventing `UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC` reCAPTCHA rejections caused by IP mismatch.

> **Residential or ISP proxies are strongly recommended.** Datacenter IPs (including WARP) receive lower reCAPTCHA trust scores and are more likely to trigger rate limiting.

### WARP Auto-Reconnect

Enable **WARP Auto-Reconnect** to automatically cycle the Cloudflare WARP connection when a `TOO_MUCH_TRAFFIC` rate limit is hit. Requires `warp-cli` installed on the host.

This is a fallback for WARP-based setups. For sustained high volume, a residential proxy list is more reliable.

## 📈 Monitoring Endpoints

- `GET /health`: Public health check. Returns service status, active token count, soon-to-expire tokens, expired tokens, 429-banned count, and other summary data.
- `GET /metrics`: Prometheus metrics endpoint.
- `GET /api/tokens`: Admin endpoint. Returns token states including `at_expires`, `at_expired`, `at_expiring_within_1h`, `ban_reason`, `consecutive_error_count`, etc.

Prometheus can scrape `/metrics` directly. For Kubernetes deployments, scrape only within the cluster and restrict external access to `/metrics` at the Ingress/Gateway layer.

### Model Test Page

Visit **http://localhost:8000/test** to open the built-in model test page, which supports:

- Browse all available models by category (image generation, text/image-to-video, multi-image video, video upscale, etc.)
- Enter a prompt and test with one click; generation progress streams live
- Image-to-image / image-to-video scenarios support image upload
- Preview generated images or videos directly after completion

## 📋 Supported Models

### Image Generation

| Model Name | Description | Aspect Ratio |
|---------|--------|--------|
| `gemini-3.0-pro-image-landscape` | Image/Text-to-Image | Landscape |
| `gemini-3.0-pro-image-portrait` | Image/Text-to-Image | Portrait |
| `gemini-3.0-pro-image-square` | Image/Text-to-Image | Square |
| `gemini-3.0-pro-image-four-three` | Image/Text-to-Image | Landscape 4:3 |
| `gemini-3.0-pro-image-three-four` | Image/Text-to-Image | Portrait 3:4 |
| `gemini-3.0-pro-image-landscape-2k` | Image/Text-to-Image (2K) | Landscape |
| `gemini-3.0-pro-image-portrait-2k` | Image/Text-to-Image (2K) | Portrait |
| `gemini-3.0-pro-image-square-2k` | Image/Text-to-Image (2K) | Square |
| `gemini-3.0-pro-image-four-three-2k` | Image/Text-to-Image (2K) | Landscape 4:3 |
| `gemini-3.0-pro-image-three-four-2k` | Image/Text-to-Image (2K) | Portrait 3:4 |
| `gemini-3.0-pro-image-landscape-4k` | Image/Text-to-Image (4K) | Landscape |
| `gemini-3.0-pro-image-portrait-4k` | Image/Text-to-Image (4K) | Portrait |
| `gemini-3.0-pro-image-square-4k` | Image/Text-to-Image (4K) | Square |
| `gemini-3.0-pro-image-four-three-4k` | Image/Text-to-Image (4K) | Landscape 4:3 |
| `gemini-3.0-pro-image-three-four-4k` | Image/Text-to-Image (4K) | Portrait 3:4 |
| `imagen-4.0-generate-preview-landscape` | Image/Text-to-Image | Landscape |
| `imagen-4.0-generate-preview-portrait` | Image/Text-to-Image | Portrait |
| `gemini-3.1-flash-image-landscape` | Image/Text-to-Image | Landscape |
| `gemini-3.1-flash-image-portrait` | Image/Text-to-Image | Portrait |
| `gemini-3.1-flash-image-square` | Image/Text-to-Image | Square |
| `gemini-3.1-flash-image-four-three` | Image/Text-to-Image | Landscape 4:3 |
| `gemini-3.1-flash-image-three-four` | Image/Text-to-Image | Portrait 3:4 |
| `gemini-3.1-flash-image-landscape-2k` | Image/Text-to-Image (2K) | Landscape |
| `gemini-3.1-flash-image-portrait-2k` | Image/Text-to-Image (2K) | Portrait |
| `gemini-3.1-flash-image-square-2k` | Image/Text-to-Image (2K) | Square |
| `gemini-3.1-flash-image-four-three-2k` | Image/Text-to-Image (2K) | Landscape 4:3 |
| `gemini-3.1-flash-image-three-four-2k` | Image/Text-to-Image (2K) | Portrait 3:4 |
| `gemini-3.1-flash-image-landscape-4k` | Image/Text-to-Image (4K) | Landscape |
| `gemini-3.1-flash-image-portrait-4k` | Image/Text-to-Image (4K) | Portrait |
| `gemini-3.1-flash-image-square-4k` | Image/Text-to-Image (4K) | Square |
| `gemini-3.1-flash-image-four-three-4k` | Image/Text-to-Image (4K) | Landscape 4:3 |
| `gemini-3.1-flash-image-three-four-4k` | Image/Text-to-Image (4K) | Portrait 3:4 |

### Video Generation

#### Text to Video (T2V)
⚠️ **Does not support image uploads**

| Model Name | Description | Aspect Ratio |
|---------|---------|--------|
| `veo_3_1_t2v_fast_portrait` | Text-to-Video | Portrait |
| `veo_3_1_t2v_fast_landscape` | Text-to-Video | Landscape |
| `veo_3_1_t2v_fast_portrait_ultra` | Text-to-Video | Portrait |
| `veo_3_1_t2v_fast_ultra` | Text-to-Video | Landscape |
| `veo_3_1_t2v_fast_portrait_ultra_relaxed` | Text-to-Video | Portrait |
| `veo_3_1_t2v_fast_ultra_relaxed` | Text-to-Video | Landscape |
| `veo_3_1_t2v_portrait` | Text-to-Video | Portrait |
| `veo_3_1_t2v_landscape` | Text-to-Video | Landscape |
| `veo_3_1_t2v_landscape_4s` | Text-to-Video 4s | Landscape |
| `veo_3_1_t2v_portrait_4s` | Text-to-Video 4s | Portrait |
| `veo_3_1_t2v_landscape_6s` | Text-to-Video 6s | Landscape |
| `veo_3_1_t2v_portrait_6s` | Text-to-Video 6s | Portrait |
| `veo_3_1_t2v_fast_landscape_4s` | Text-to-Video Fast 4s | Landscape |
| `veo_3_1_t2v_fast_portrait_4s` | Text-to-Video Fast 4s | Portrait |
| `veo_3_1_t2v_fast_landscape_6s` | Text-to-Video Fast 6s | Landscape |
| `veo_3_1_t2v_fast_portrait_6s` | Text-to-Video Fast 6s | Portrait |
| `veo_3_1_t2v_lite_portrait` | Text-to-Video Lite | Portrait |
| `veo_3_1_t2v_lite_landscape` | Text-to-Video Lite | Landscape |
| `veo_3_1_t2v_lite_4s_portrait` | Text-to-Video Lite 4s | Portrait |
| `veo_3_1_t2v_lite_4s_landscape` | Text-to-Video Lite 4s | Landscape |
| `veo_3_1_t2v_lite_6s_portrait` | Text-to-Video Lite 6s | Portrait |
| `veo_3_1_t2v_lite_6s_landscape` | Text-to-Video Lite 6s | Landscape |

#### First/Last Frame Model (I2V - Image to Video)
📸 **Supports 1-2 images: 1 as start frame, 2 as start+end frames**

> 💡 **Auto-adaptation**: The system automatically selects the corresponding model_key based on the number of images
> - **Single-frame mode** (1 image): generates video from start frame
> - **Dual-frame mode** (2 images): generates transition video from start+end frames
> - `veo_3_1_i2v_lite_*` supports only **1** start frame image
> - `veo_3_1_interpolation_lite_*` supports only **2** start+end frame images

| Model Name | Description | Aspect Ratio |
|---------|---------|--------|
| `veo_3_1_i2v_s_fast_portrait_fl` | Image-to-Video | Portrait |
| `veo_3_1_i2v_s_fast_fl` | Image-to-Video | Landscape |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl` | Image-to-Video | Portrait |
| `veo_3_1_i2v_s_fast_ultra_fl` | Image-to-Video | Landscape |
| `veo_3_1_i2v_s_fast_portrait_ultra_relaxed` | Image-to-Video | Portrait |
| `veo_3_1_i2v_s_fast_ultra_relaxed` | Image-to-Video | Landscape |
| `veo_3_1_i2v_s_portrait` | Image-to-Video | Portrait |
| `veo_3_1_i2v_s_landscape` | Image-to-Video | Landscape |
| `veo_3_1_i2v_s_landscape_4s` | Image-to-Video 4s | Landscape |
| `veo_3_1_i2v_s_portrait_4s` | Image-to-Video 4s | Portrait |
| `veo_3_1_i2v_s_landscape_6s` | Image-to-Video 6s | Landscape |
| `veo_3_1_i2v_s_portrait_6s` | Image-to-Video 6s | Portrait |
| `veo_3_1_i2v_s_fast_landscape_4s_fl` | Image-to-Video Fast 4s | Landscape |
| `veo_3_1_i2v_s_fast_portrait_4s_fl` | Image-to-Video Fast 4s | Portrait |
| `veo_3_1_i2v_s_fast_landscape_6s_fl` | Image-to-Video Fast 6s | Landscape |
| `veo_3_1_i2v_s_fast_portrait_6s_fl` | Image-to-Video Fast 6s | Portrait |
| `veo_3_1_i2v_lite_portrait` | Image-to-Video Lite (start frame only) | Portrait |
| `veo_3_1_i2v_lite_landscape` | Image-to-Video Lite (start frame only) | Landscape |
| `veo_3_1_i2v_lite_4s_portrait` | Image-to-Video Lite 4s (start frame only) | Portrait |
| `veo_3_1_i2v_lite_4s_landscape` | Image-to-Video Lite 4s (start frame only) | Landscape |
| `veo_3_1_i2v_lite_6s_portrait` | Image-to-Video Lite 6s (start frame only) | Portrait |
| `veo_3_1_i2v_lite_6s_landscape` | Image-to-Video Lite 6s (start frame only) | Landscape |
| `veo_3_1_interpolation_lite_portrait` | Image-to-Video Lite (start+end frame transition) | Portrait |
| `veo_3_1_interpolation_lite_landscape` | Image-to-Video Lite (start+end frame transition) | Landscape |
| `veo_3_1_interpolation_lite_4s_portrait` | Image-to-Video Lite 4s (start+end frame transition) | Portrait |
| `veo_3_1_interpolation_lite_4s_landscape` | Image-to-Video Lite 4s (start+end frame transition) | Landscape |
| `veo_3_1_interpolation_lite_6s_portrait` | Image-to-Video Lite 6s (start+end frame transition) | Portrait |
| `veo_3_1_interpolation_lite_6s_landscape` | Image-to-Video Lite 6s (start+end frame transition) | Landscape |

#### 多图生成 (R2V - Reference Images to Video)
🖼️ **支持多张图片**

> **2026-03-06 更新**
>
> - 已同步上游新版 `R2V` 视频请求体
> - `textInput` 已切换为 `structuredPrompt.parts`
> - 顶层新增 `mediaGenerationContext.batchId`
> - 顶层新增 `useV2ModelConfig: true`
> - 横屏 / 竖屏 `R2V` 模型共用同一套新版请求体
> - 横屏 `R2V` 的上游 `videoModelKey` 已切换为 `*_landscape` 形式
> - 根据当前上游协议，`referenceImages` 当前最多传 **3 张**

| 模型名称 | 说明| 尺寸 |
|---------|---------|--------|
| `veo_3_1_r2v_fast_portrait` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape` | 图生视频 | 横屏 |
| `veo_3_1_r2v_fast_portrait_ultra` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape_ultra` | 图生视频 | 横屏 |
| `veo_3_1_r2v_fast_portrait_ultra_relaxed` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape_ultra_relaxed` | 图生视频 | 横屏 |

#### 视频放大模型 (Upsample)

这些模型不是直接调用上游 upsampler key，而是先用对应的 Veo 3.1 普通模型生成视频，再提交 1080P/4K 放大请求。

| 模型名称 | 说明 | 输出 |
|---------|---------|--------|
| `veo_3_1_t2v_landscape_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_portrait_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_landscape_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_portrait_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_landscape_4s_4k` | 文生视频 4秒放大 | 4K |
| `veo_3_1_t2v_portrait_4s_4k` | 文生视频 4秒放大 | 4K |
| `veo_3_1_t2v_landscape_4s_1080p` | 文生视频 4秒放大 | 1080P |
| `veo_3_1_t2v_portrait_4s_1080p` | 文生视频 4秒放大 | 1080P |
| `veo_3_1_t2v_landscape_6s_4k` | 文生视频 6秒放大 | 4K |
| `veo_3_1_t2v_portrait_6s_4k` | 文生视频 6秒放大 | 4K |
| `veo_3_1_t2v_landscape_6s_1080p` | 文生视频 6秒放大 | 1080P |
| `veo_3_1_t2v_portrait_6s_1080p` | 文生视频 6秒放大 | 1080P |
| `veo_3_1_t2v_fast_portrait_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_portrait_ultra_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_ultra_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_portrait_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_portrait_ultra_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_ultra_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_fast_ultra_fl_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_fast_ultra_fl_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_landscape_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_portrait_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_landscape_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_portrait_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_landscape_4s_4k` | 图生视频 4秒放大 | 4K |
| `veo_3_1_i2v_s_portrait_4s_4k` | 图生视频 4秒放大 | 4K |
| `veo_3_1_i2v_s_landscape_4s_1080p` | 图生视频 4秒放大 | 1080P |
| `veo_3_1_i2v_s_portrait_4s_1080p` | 图生视频 4秒放大 | 1080P |
| `veo_3_1_i2v_s_landscape_6s_4k` | 图生视频 6秒放大 | 4K |
| `veo_3_1_i2v_s_portrait_6s_4k` | 图生视频 6秒放大 | 4K |
| `veo_3_1_i2v_s_landscape_6s_1080p` | 图生视频 6秒放大 | 1080P |
| `veo_3_1_i2v_s_portrait_6s_1080p` | 图生视频 6秒放大 | 1080P |
| `veo_3_1_r2v_fast_portrait_ultra_4k` | 多图视频放大 | 4K |
| `veo_3_1_r2v_fast_landscape_ultra_4k` | 多图视频放大 | 4K |
| `veo_3_1_r2v_fast_portrait_ultra_1080p` | 多图视频放大 | 1080P |
| `veo_3_1_r2v_fast_landscape_ultra_1080p` | 多图视频放大 | 1080P |

## 📡 API 使用示例（需要使用流式）

> 除了下方 `OpenAI-compatible` 示例，服务也支持 Gemini 官方格式：
> - `POST /v1beta/models/{model}:generateContent`
> - `POST /models/{model}:generateContent`
> - `POST /v1beta/models/{model}:streamGenerateContent`
> - `POST /models/{model}:streamGenerateContent`
>
> Gemini 官方格式支持以下认证方式：
> - `Authorization: Bearer <api_key>`
> - `x-goog-api-key: <api_key>`
> - `?key=<api_key>`
>
> Gemini 官方图片请求体已兼容：
> - `systemInstruction`
> - `contents[].parts[].text`
> - `contents[].parts[].inlineData`
> - `contents[].parts[].fileData.fileUri`
> - `generationConfig.responseModalities`
> - `generationConfig.imageConfig.aspectRatio`
> - `generationConfig.imageConfig.imageSize`

### Gemini 官方 generateContent（文生图）

> 已使用真实 Token 实测通过。
> 如需流式返回，可将路径替换为 `:streamGenerateContent?alt=sse`。

```bash
curl -X POST "http://localhost:8000/models/gemini-3.1-flash-image:generateContent" \
  -H "x-goog-api-key: han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "systemInstruction": {
      "parts": [
        {
          "text": "Return an image only."
        }
      ]
    },
    "contents": [
      {
        "role": "user",
        "parts": [
          {
            "text": "一颗放在木桌上的红苹果，棚拍光线，极简背景"
          }
        ]
      }
    ],
    "generationConfig": {
      "responseModalities": ["IMAGE"],
      "imageConfig": {
        "aspectRatio": "1:1",
        "imageSize": "1K"
      }
    }
  }'
```

### 文生图

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image-landscape",
    "messages": [
      {
        "role": "user",
        "content": "一只可爱的猫咪在花园里玩耍"
      }
    ],
    "stream": true
  }'
```

### 图生图

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image-landscape",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "将这张图片变成水彩画风格"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<base64_encoded_image>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

### 文生视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_t2v_fast_landscape",
    "messages": [
      {
        "role": "user",
        "content": "一只小猫在草地上追逐蝴蝶"
      }
    ],
    "stream": true
  }'
```

### 首尾帧生成视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_i2v_s_fast_fl_landscape",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "从第一张图过渡到第二张图"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<首帧base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<尾帧base64>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

### 多图生成视频

> `R2V` 会由服务端自动组装新版视频请求体，调用方仍然使用 OpenAI 兼容输入即可。
> 服务端会将横屏 `R2V` 自动映射到最新的 `*_landscape` 上游模型键。
> 当前最多传 **3 张参考图**。

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_r2v_fast_portrait",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "以三张参考图的人物和场景为基础，生成一段镜头平滑推进的竖屏视频"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图1base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图2base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图3base64>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

---

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

---

## 🙏 致谢

- [PearNoDec](https://github.com/PearNoDec) 提供的YesCaptcha打码方案
- [raomaiping](https://github.com/raomaiping) 提供的无头打码方案
感谢所有贡献者和使用者的支持！

---

## 📞 联系方式

- 提交 Issue：[GitHub Issues](https://github.com/TheSmallHanCat/flow2api/issues)

---

**⭐ 如果这个项目对你有帮助，请给个 Star！**

## 最近更新

- `9f1d712` 同步 personal 打码逻辑，包含清理、浏览器参数和打码方式配置。
- `da2ad06` 合并 PR #133。
- `abd0c00` 修复 PR #133 合并后的集成问题。
- `55431c9` 将 origin/main 同步到 PR #133。
- `4b7a0ad` 新增 Prometheus 服务指标和 Token 健康监控。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TheSmallHanCat/flow2api&type=date&legend=top-left)](https://www.star-history.com/#TheSmallHanCat/flow2api&type=date&legend=top-left)
