# 甲方设备 MPP 环境说明

> 阅读前说明（更新于 2026-03-31）
>
> - 本文针对某一类甲方板端环境问题，不是当前仓库所有部署场景的统一结论。
> - 当前代码在 `cleaningcar/video_io.py` 中会先尝试 `FFmpeg` 硬解，失败后再尝试 `GStreamer+mpp`，最后才回退到软件解码。
> - 单车视频写出顺序是 `FFmpeg 硬编 -> GStreamer 硬编 -> FFmpeg libx264`。
> - 因此，本文中建议显式关闭 `hw_decode` 的前提是：你已经确认目标板既缺少可用的 `ffmpeg rkmpp`，也缺少 `mppvideodec` 或 Rockchip GStreamer MPP 插件。

本文档用于说明某类甲方设备为什么不能稳定命中当前硬件解码优先链路，以及后续可选处理方案。

## 1. 当前结论

本项目的硬解入口依赖：

- `video.hw_decode=true`
- `ffmpeg` 提供可用的硬件解码器，优先 `*_rkmpp`
- 若 `ffmpeg` 硬解不可用，OpenCV 仍需支持 `GStreamer`
- GStreamer 存在 `mppvideodec`
- 系统存在 `libgstrockchipmpp.so` 和 `librockchip_mpp.so`

当前两套设备的排查结论如下。

### 实验室设备

- 系统：`Ubuntu 24.04.1`
- GStreamer：`1.24.2`
- 已安装包：
  - `gstreamer1.0-rockchip1`
  - `librockchip-mpp1`
  - `rockchip-multimedia-config`
- 已确认存在：
  - `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so`
  - `/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0`
- 项目日志至少可见备用链路可用：
  - `[reader] Using GStreamer+mpp file pipeline for ...`

### 甲方设备

- 系统：`Ubuntu 20.04.6`
- GStreamer：`1.16.3`
- 已安装包：
  - `librockchip-mpp1`
  - `librockchip-vpu0`
- 未安装包：
  - `gstreamer1.0-rockchip1`
  - `rockchip-multimedia-config`
- 已确认存在：
  - `/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0`
- 已确认不存在：
  - `mppvideodec`
  - `libgstrockchipmpp.so`

现场命令结果已经证明：

```bash
gst-inspect-1.0 mppvideodec
```

返回：

```text
No such element or plugin 'mppvideodec'
```

这说明甲方设备只有 Rockchip MPP 运行库，没有 GStreamer 的 Rockchip MPP 插件层；结合同批排查里 `ffmpeg -hide_banner -decoders | grep rkmpp` 输出为空，可以判断当前板端既缺少 `FFmpeg` 优先硬解能力，也缺少第二备选 `GStreamer+mpp` 硬解链路。

## 2. 对项目的直接影响

- 当前甲方设备上，`video.hw_decode=true` 不能稳定命中可用的 `FFmpeg` 硬解
- 当前板端又缺少 `GStreamer+mpp` 备用链路，因此读流最终只能回退到软件解码
- 若现场还要求 per-id 单车视频尽量走硬编，也应同步补齐 `ffmpeg rkmpp`；否则程序会退到 `GStreamer` 硬编，再退到 `FFmpeg libx264`
- 但为了避免在这类已知缺链路环境中反复尝试硬解、增加排障噪声，仍建议在环境修复前显式保持：

```json
"video": {
  "hw_decode": false
}
```

对这类甲方设备，交付建议默认走软件解码部署。

## 3. 为什么不能直接拷实验室的插件

不建议直接把实验室设备的 `libgstrockchipmpp.so` 或对应 deb 包直接复制到甲方设备，原因如下：

- 实验室设备是 `Ubuntu 24.04`
- 甲方设备是 `Ubuntu 20.04`
- 两边 GStreamer 主版本和系统依赖不同
- 直接混装存在 ABI 不匹配、插件注册失败、运行时崩溃的风险

因此，MPP 环境问题应按系统级多媒体环境处理，而不是按 Python 项目文件处理。

## 4. 可选方案

### 方案 1：更换系统并对齐实验室环境

这是推荐方案。

目标：

- 将甲方设备更换为与实验室一致或兼容的系统镜像
- 至少对齐到同时支持 `ffmpeg rkmpp` 与 `gstreamer1.0-rockchip1` 的系统和软件源

建议口径：

- 优先由厂家/BSP 侧提供与实验室一致的板端镜像
- 或至少升级到可安装 `gstreamer1.0-rockchip1` 且能提供 `ffmpeg rkmpp` 的系统版本

环境完成后需要满足：

- `ffmpeg -hide_banner -decoders | grep rkmpp` 有输出
- `gst-inspect-1.0 mppvideodec` 能输出插件详情
- `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so` 存在
- 项目启用 `hw_decode` 后日志出现：
  - `[reader] Using FFmpeg hardware decoder ...`
  - 或 `[reader] Using GStreamer+mpp file pipeline for ...`
  - 或 `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

### 方案 2：保留 Focal，由厂家补齐 FFmpeg + GStreamer 硬件加速链路

若甲方设备必须继续使用 `Ubuntu 20.04 (Focal)`，则需要由厂家或 BSP 供应方处理：

- 针对 `Ubuntu 20.04`
- 针对 `GStreamer 1.16.3`
- 针对当前板端 BSP / `librockchip-mpp1`
- 补齐 `ffmpeg` 的 `rkmpp` 能力
- 编译并交付一套可用的 `mppvideodec` 插件

交付形态建议为：

- 完整系统镜像
- 或可安装的 deb 包

不建议只给单个 `.so` 文件。

验收口径与方案 1 相同：

- `ffmpeg -hide_banner -decoders | grep rkmpp` 成功
- `gst-inspect-1.0 mppvideodec` 成功
- `gst-launch-1.0` 本地 H.264 管线成功
- 项目日志确认实际走到 `FFmpeg` 硬解或 `GStreamer+mpp` 备用链路

## 5. 本轮交付口径

本轮项目先按软件解码部署，不阻塞交付：

- `video.hw_decode=false`
- 先完成业务功能、稳定性和事件链路验收
- `FFmpeg/GStreamer` 硬件加速链路作为甲方环境增强项单独推进

## 6. 环境修复后的最小验收命令

甲方设备完成环境补齐后，按以下顺序复测。

### 6.1 检查 FFmpeg 编解码能力

```bash
ffmpeg -hide_banner -decoders | grep rkmpp
ffmpeg -hide_banner -encoders | grep -E "rkmpp|v4l2m2m|omx"
```

### 6.2 检查插件

```bash
gst-inspect-1.0 mppvideodec
gst-inspect-1.0 | grep -Ei "mpp|rockchip"
```

### 6.3 检查本地 H.264 管线

```bash
gst-launch-1.0 -v \
  filesrc location="/path/to/test.mp4" ! \
  qtdemux ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

### 6.4 检查项目实际日志

启用 `video.hw_decode=true` 后，项目日志必须出现：

- `[reader] Using FFmpeg hardware decoder ...`
- `[reader] Using GStreamer+mpp file pipeline for ...`
- 或 `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

若未出现，则视为仍未真正启用项目硬解链路。
